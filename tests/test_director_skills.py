from aurora.inference.models import InferenceResponse, ModelSpec
from aurora.inference.router import ModelRouter
from aurora.organization import AgentSpec, DepartmentSpec, OrganizationRegistry
from aurora.orchestration.director import DirectorPlanner
from aurora.skills import SkillRegistry
from aurora.skills.models import SkillSpec


class FakeRouter(ModelRouter):
    def __init__(self, content: str):
        super().__init__([
            ModelSpec("model/a", "fake", "https://example.invalid", capabilities=("reasoning",))
        ])
        self.content = content

    def generate(self, request, capability=None, preferred_models=None):
        return InferenceResponse(model="model/a", provider="fake", content=self.content)


def make_org() -> OrganizationRegistry:
    return OrganizationRegistry(
        agents={
            "researcher": AgentSpec(
                agent_id="researcher",
                role="researcher",
                department="research",
                mission="research",
                capabilities=frozenset({"tool:curl"}),
                model_preferences=(),
                skill_ids=("research-skill",),
            )
        },
        departments={
            "research": DepartmentSpec(
                department_id="research",
                mission="research",
                agent_ids=("researcher",),
                model_preferences=(),
                allowed_capabilities=frozenset({"tool:curl"}),
            )
        },
    )


def make_skills() -> SkillRegistry:
    return SkillRegistry({
        "research-skill": SkillSpec(
            "research-skill",
            "1.0.0",
            "Research",
            "research",
            "Use primary evidence and state uncertainty.",
            frozenset({"tool:curl"}),
            ("source_check",),
            ("citation_check",),
        )
    })


def test_director_parses_and_validates_task_skills():
    content='{"tasks":[{"task_id":"t","role":"researcher","objective":"research",'
    content+=' "capability":null,"args":[],"depends_on":[],"skills":["research-skill"]}]}'
    planner=DirectorPlanner(FakeRouter(content), make_org(), make_skills())
    plan=planner.plan(objective="research")
    assert plan.tasks[0].skills == ("research-skill",)


def test_director_rejects_unknown_skill():
    content='{"tasks":[{"task_id":"t","role":"researcher","objective":"research",'
    content+=' "capability":null,"args":[],"depends_on":[],"skills":["missing"]}]}'
    planner=DirectorPlanner(FakeRouter(content), make_org(), make_skills())
    try:
        planner.plan(objective="research")
    except ValueError as exc:
        assert "unknown Aurora skill" in str(exc)
    else:
        raise AssertionError("unknown skill was accepted")
