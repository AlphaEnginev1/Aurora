import pytest

from aurora.inference.models import InferenceResponse, ModelSpec
from aurora.inference.router import ModelRouter
from aurora.orchestration.director import DirectorPlanError, DirectorPlanner
from aurora.organization import AgentSpec, DepartmentSpec, OrganizationRegistry
from aurora.projects import ProjectRegistry, ProjectSpec, ProjectStatus


class FakeRouter(ModelRouter):
    def __init__(self, response: str):
        super().__init__([ModelSpec("fake/model", "fake", "https://example.invalid")])
        self.response = response
        self.requests = []

    def generate(self, request, capability=None):
        self.requests.append((request, capability))
        return InferenceResponse(
            model="fake/model",
            provider="fake",
            content=self.response,
        )


def test_director_planner_parses_structured_plan():
    router = FakeRouter(
        '{"tasks":[{"task_id":"collect","role":"researcher","objective":"Collect sources",'
        '"capability":"tool:curl","args":["--help"],"depends_on":[]},'
        '{"task_id":"review","role":"reviewer","objective":"Review evidence",'
        '"capability":null,"args":[],"depends_on":["collect"]}]}'
    )
    plan = DirectorPlanner(router).plan(objective="Research the target.")

    assert plan.objective == "Research the target."
    assert [task.task_id for task in plan.tasks] == ["collect", "review"]
    assert plan.tasks[0].capability == "tool:curl"
    assert plan.tasks[1].depends_on == ("collect",)
    assert router.requests[0][1] == "reasoning"
    assert router.requests[0][0].metadata["role"] == "director_planner"


def test_director_planner_accepts_json_code_fence():
    fence = chr(96) * 3
    payload = '{"tasks":[{"task_id":"one","role":"agent","objective":"Do one",'
    payload += '"capability":null,"args":[],"depends_on":[]}]}'
    router = FakeRouter(fence + "json\n" + payload + "\n" + fence)
    plan = DirectorPlanner(router).plan(objective="Do work.")
    assert plan.tasks[0].task_id == "one"


@pytest.mark.parametrize(
    "response",
    [
        "not json",
        "{}",
        '{"tasks":[]}',
        '{"tasks":[1]}',
        '{"tasks":[{"task_id":"a","role":"agent","objective":"x","args":[1]}]}',
    ],
)
def test_director_planner_rejects_invalid_plans(response):
    with pytest.raises(DirectorPlanError):
        DirectorPlanner(FakeRouter(response)).plan(objective="Do work.")


def test_director_planner_rejects_empty_objective():
    with pytest.raises(DirectorPlanError, match="empty"):
        DirectorPlanner(FakeRouter('{"tasks":[]}')).plan(objective="  ")


def test_director_planner_parses_review_decision():
    router = FakeRouter(
        '{"decision":"continue","rationale":"Need verification",'
        '"tasks":[{"task_id":"verify","role":"validator","objective":"Verify evidence",'
        '"capability":null,"args":[],"depends_on":[]}]}'
    )
    review = DirectorPlanner(router).review(
        objective="Research the target.",
        context=[{"role": "user", "content": "Current state: incomplete"}],
    )
    assert review.decision == "continue"
    assert review.rationale == "Need verification"
    assert review.tasks[0].task_id == "verify"
    assert router.requests[0][0].metadata["role"] == "director_reviewer"


@pytest.mark.parametrize(
    "response",
    [
        '{"decision":"complete","rationale":"done","tasks":[{"task_id":"x","role":"a","objective":"x"}]}',
        '{"decision":"continue","rationale":"more","tasks":[]}',
        '{"decision":"blocked","rationale":"blocked","tasks":[{"task_id":"x","role":"a","objective":"x"}]}',
        '{"decision":"unknown","rationale":"bad","tasks":[]}',
    ],
)
def test_director_planner_rejects_invalid_reviews(response):
    with pytest.raises(DirectorPlanError):
        DirectorPlanner(FakeRouter(response)).review(objective="Do work.")



def test_director_normalizes_role_casing_to_registered_role():
    registry = OrganizationRegistry(
        agents={
            "researcher": AgentSpec(
                agent_id="researcher",
                role="researcher",
                department="research",
                mission="research",
                capabilities=frozenset(),
                model_preferences=(),
            )
        },
        departments={
            "research": DepartmentSpec(
                department_id="research",
                mission="research",
                agent_ids=("researcher",),
                model_preferences=(),
                allowed_capabilities=frozenset(),
            )
        },
    )
    router = FakeRouter(
        '{"tasks":[{"task_id":"x","role":"Researcher","objective":"x",'
        '"capability":null,"args":[],"depends_on":[]}]}'
    )

    plan = DirectorPlanner(router, organization=registry).plan(objective="Do work.")

    assert plan.tasks[0].role == "researcher"

def test_director_rejects_role_outside_organization():
    registry = OrganizationRegistry(
        agents={
            "researcher": AgentSpec(
                agent_id="researcher",
                role="researcher",
                department="research",
                mission="research",
                capabilities=frozenset(),
                model_preferences=(),
            )
        },
        departments={
            "research": DepartmentSpec(
                department_id="research",
                mission="research",
                agent_ids=("researcher",),
                model_preferences=(),
                allowed_capabilities=frozenset(),
            )
        },
    )
    router = FakeRouter(
        '{"tasks":[{"task_id":"x","role":"unknown","objective":"x",'
        '"capability":null,"args":[],"depends_on":[]}]}'
    )
    with pytest.raises(DirectorPlanError, match="unknown Aurora agent role"):
        DirectorPlanner(router, organization=registry).plan(objective="Do work.")

def test_director_planner_receives_registered_project_catalog():
    projects = ProjectRegistry(
        {
            "project-a": ProjectSpec(
                project_id="project-a",
                name="Project A",
                objective="Build a governed research workflow.",
                status=ProjectStatus.ACTIVE,
                current_state="research phase",
            )
        }
    )
    router = FakeRouter(
        '{"tasks":[{"task_id":"collect","role":"researcher","objective":"Collect sources",'
        '"capability":null,"args":[],"depends_on":[]}]}'
    )

    DirectorPlanner(router, project_registry=projects).plan(
        objective="Continue Project A."
    )

    system = router.requests[0][0].messages[0]["content"]
    assert "Registered Aurora project catalog:" in system
    assert "project-a: Project A" in system
    assert "status=active" in system
    assert "state=research phase" in system



def test_director_planner_prompt_prefers_governed_workbench_for_repository_reads():
    router = FakeRouter(
        '{"tasks":[{"task_id":"read","role":"researcher",'
        '"objective":"Inspect README using governed Workbench reads",'
        '"capability":null,"args":[],"depends_on":[]}]}' 
    )
    DirectorPlanner(router).plan(objective="Inspect the repository read-only.")
    system = router.requests[0][0].messages[0]["content"]

    assert "fully qualified registered capability beginning with 'workbench:'" in system
    assert "workbench:file:read" in system
    assert "Never emit bare aliases such as file:read" in system
    assert "capability such as tool:rg or python.package:pip" not in system
