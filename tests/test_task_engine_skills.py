from pathlib import Path

from aurora.inference.models import InferenceResponse, ModelSpec
from aurora.inference.router import ModelRouter
from aurora.organization import AgentSpec, DepartmentSpec, OrganizationRegistry
from aurora.orchestration.engine import OrchestrationEngine
from aurora.orchestration.task_engine import DelegatedTask, TaskEngine
from aurora.skills import SkillRegistry
from aurora.skills.models import SkillSpec


class CaptureRouter(ModelRouter):
    def __init__(self):
        super().__init__([
            ModelSpec("model/a", "fake", "https://example.invalid", capabilities=("reasoning",))
        ])
        self.request = None
        self.preferences = None

    def generate(self, request, capability=None, preferred_models=None):
        self.request = request
        self.preferences = tuple(preferred_models or ())
        return InferenceResponse(model="model/a", provider="fake", content="ok")


def test_task_engine_executes_with_default_and_task_skills(tmp_path: Path):
    skill_registry=SkillRegistry({
        "default-skill": SkillSpec(
            "default-skill","1.0.0","Default","m","Follow the standard procedure.",
            frozenset({"tool:curl"}),("verify_default",),("quality_default",)
        ),
        "task-skill": SkillSpec(
            "task-skill","1.0.0","Task","m","Apply the task-specific method.",
            frozenset({"tool:curl"}),("verify_task",),("quality_task",)
        ),
    })
    organization=OrganizationRegistry(
        agents={"researcher": AgentSpec(
            "researcher","researcher","research","m",
            frozenset({"tool:curl"}),("model/a",),("default-skill",)
        )},
        departments={"research": DepartmentSpec(
            "research","m",("researcher",),("model/a",),frozenset({"tool:curl"})
        )},
    )
    router=CaptureRouter()
    engine=TaskEngine(
        OrchestrationEngine(router),
        tmp_path/"workspace",
        organization=organization,
        skill_registry=skill_registry,
    )
    result=engine.execute([DelegatedTask("t","researcher","do work",skills=("task-skill",))])
    assert result[0].status.value=="succeeded"
    assert "Aurora specialist skill profile." in router.request.messages[0]["content"]
    assert "Follow the standard procedure." in router.request.messages[0]["content"]
    assert "Apply the task-specific method." in router.request.messages[0]["content"]
    assert router.preferences == ("model/a",)


def test_task_engine_rejects_skill_not_supported_by_agent(tmp_path: Path):
    skill_registry=SkillRegistry({
        "needs-curl": SkillSpec(
            "needs-curl","1.0.0","Network research","m","i",
            frozenset({"tool:curl"}),(),()
        )
    })
    organization=OrganizationRegistry(
        agents={"engineer": AgentSpec(
            "engineer","engineer","engineering","m",
            frozenset({"tool:git"}),(),()
        )},
        departments={"engineering": DepartmentSpec(
            "engineering","m",("engineer",),(),frozenset({"tool:git"})
        )},
    )
    engine=TaskEngine(
        OrchestrationEngine(CaptureRouter()),
        tmp_path/"workspace",
        organization=organization,
        skill_registry=skill_registry,
    )
    from aurora.orchestration.task_engine import TaskEngineError
    try:
        engine.validate([DelegatedTask("t","engineer","do",skills=("needs-curl",))])
    except TaskEngineError as exc:
        assert "unauthorized capabilities" in str(exc)
    else:
        raise AssertionError("unsupported skill was accepted")
 
 
def test_task_engine_passes_skill_tags_and_learned_scores_to_router(tmp_path: Path):
    from aurora.skills.learning import SkillLearningStore
    from aurora.skills.evaluation import SkillEvaluation

    skill_registry=SkillRegistry({
        "research": SkillSpec(
            "research","1.0.0","Research","m","i",
            frozenset(),(),(),("research",)
        )
    })
    organization=OrganizationRegistry(
        agents={"researcher": AgentSpec(
            "researcher","researcher","research","m",
            frozenset(),(),("research",)
        )},
        departments={"research": DepartmentSpec(
            "research","m",("researcher",),(),frozenset()
        )},
    )
    learning=SkillLearningStore(tmp_path/"skills.db")
    for idx, score in enumerate((0.9, 0.9)):
        learning.record(SkillEvaluation(
            "research","1.0.0",None,f"t-a-{idx}","researcher",score,"succeeded","model/a",
            (),(),(),(),(),(),None,1.0,"evidence_complete"
        ))
    router=CaptureRouter()
    engine=TaskEngine(
        OrchestrationEngine(router),
        tmp_path/"workspace",
        organization=organization,
        skill_registry=skill_registry,
        skill_learning=learning,
    )
    result=engine.execute([DelegatedTask("t","researcher","research paper")])
    assert result[0].status.value=="succeeded"
    assert router.request.metadata["skill_tags"] == ["research"]
    assert router.request.metadata["task_type"] == "research"
    assert router.request.metadata["learned_model_scores"] == {"model/a": 0.9}


def test_task_engine_automatically_selects_specialist_agent(tmp_path: Path):
    skill_registry = SkillRegistry({
        "research": SkillSpec(
            "research", "1.0.0", "Research", "m", "i",
            frozenset(), (), (), ("research",)
        )
    })
    organization = OrganizationRegistry(
        agents={"researcher": AgentSpec(
            "researcher", "researcher", "research", "research papers and evidence",
            frozenset(), (), ("research",)
        )},
        departments={"research": DepartmentSpec(
            "research", "research", ("researcher",), (), frozenset()
        )},
    )
    engine = TaskEngine(
        OrchestrationEngine(CaptureRouter()),
        tmp_path / "workspace",
        organization=organization,
        skill_registry=skill_registry,
    )
    result = engine.execute([
        DelegatedTask("t", "auto", "research papers and evidence", skills=("research",))
    ])
    assert result[0].status.value == "succeeded"
    assert result[0].task.role == "researcher"
