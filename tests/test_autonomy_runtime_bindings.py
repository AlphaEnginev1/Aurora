from types import SimpleNamespace

from aurora.autonomy.runtime_bindings import build_autonomy_runtime_bindings


class FakeRouter:
    def generate(self, request, capability=None):
        prompt = request.messages[-1]["content"]
        if "Choose at most one safe next action" in prompt:
            value = '{"done": true}'
        elif "State one testable hypothesis" in prompt:
            value = "A testable hypothesis"
        elif "Evaluate whether the hypothesis" in prompt:
            value = '{"verdict":"supported","evidence":["src-1","src-2"]}'
        elif "Classify whether the claim is novel" in prompt:
            value = "novel"
        else:
            value = "continue"
        return SimpleNamespace(content=value)


class FakeWorkbench:
    def __init__(self):
        self.calls = []

    def execute(self, request):
        self.calls.append(request)
        return SimpleNamespace(
            status="succeeded",
            error=None,
            output=[{"capability_id": "workbench:computer", "available": True}],
        )


class FakeDirector:
    def __init__(self):
        self.calls = []

    def run_mission(self, *, objective, context=None, model=None):
        self.calls.append({"objective": objective, "context": context})
        return SimpleNamespace(
            objective=objective,
            tasks=(SimpleNamespace(status="succeeded"),),
        )


class FakeResearch:
    def discover(self, request):
        return ("https://research.example/a", "https://research.example/b")

    def retrieve(self, request):
        return (
            SimpleNamespace(url="https://research.example/a", source_id="src-1", content_text="Evidence A"),
            SimpleNamespace(url="https://research.example/b", source_id="src-2", content_text="Evidence B"),
        )


def build():
    director = FakeDirector()
    workbench = FakeWorkbench()
    learning = object()
    controllers = build_autonomy_runtime_bindings(
        director_service=director,
        router=FakeRouter(),
        workbench=workbench,
        research_service=FakeResearch(),
        skill_learning=learning,
    )
    return controllers, director, workbench, learning


def test_runtime_bindings_expose_all_four_remaining_controllers():
    controllers, _, _, learning = build()
    assert {
        "computer_use",
        "software_engineer",
        "discovery_loop",
        "recursive_self_improvement",
        "learning_store",
    } <= set(controllers)
    assert controllers["learning_store"] is learning


def test_computer_use_uses_canonical_workbench_and_observes_before_deciding():
    controllers, _, workbench, _ = build()
    steps = controllers["computer_use"].run("inspect the available workstation")
    assert steps == ()
    assert workbench.calls
    assert workbench.calls[0].capability == "workbench:census"


def test_software_engineer_delegates_each_phase_to_existing_director_service():
    controllers, director, _, _ = build()
    result = controllers["software_engineer"].run("inspect repository")
    assert result.status == "succeeded"
    assert len(director.calls) == 7
    assert all("Autonomous engineering controller phase" in item["objective"] for item in director.calls)


def test_discovery_loop_does_not_promote_two_sources_to_trusted_knowledge():
    controllers, _, _, _ = build()
    result = controllers["discovery_loop"].run("test a research hypothesis")
    # Two sources are insufficient: a candidate source plus two independent
    # source observations are required, and a model verdict is not an artifact.
    assert result.status == "insufficient"
    assert result.knowledge == ()


def test_recursive_self_improvement_fails_closed_without_explicit_ci_evidence():
    controllers, _, _, _ = build()
    result = controllers["recursive_self_improvement"].run("improve Aurora")
    assert result.status == "insufficient"
    assert result.change_id is None
