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


def test_software_engineer_fails_closed_without_a_verified_change_receipt():
    controllers, director, _, _ = build()
    result = controllers["software_engineer"].run("inspect repository")
    assert result.status == "blocked"
    assert len(director.calls) == 4
    assert [item["objective"].split("phase '", 1)[1].split("'", 1)[0]
            for item in director.calls] == ["inspect", "research", "plan", "edit"]
    assert all("Autonomous engineering controller phase" in item["objective"] for item in director.calls)


def test_software_engineer_success_requires_matching_commit_ci_and_open_pr():
    import json

    from aurora.autonomy.runtime_bindings import build_autonomy_runtime_bindings

    commit_sha = "d" * 40
    branch_name = "fix/controlled-engineering-change"

    class EvidenceDirector:
        def __init__(self):
            self.calls = []

        def run_mission(self, *, objective, context=None, model=None):
            self.calls.append(objective)
            phase = objective.split("phase '", 1)[1].split("'", 1)[0]
            capability = None
            output = {}
            args = ()
            provenance = None
            if phase == "edit":
                capability = "workbench:github:file:write"
                args = (json.dumps({
                    "owner": "AlphaEnginev1",
                    "repo": "Aurora",
                    "branch": branch_name,
                    "path": "aurora/controlled.py",
                    "content": "# controlled change",
                    "message": "controlled change",
                }),)
                output = {"commit": {"sha": commit_sha}}
                provenance = "github-api"
            elif phase == "verify":
                capability = "workbench:github:workflow:read"
                output = {"workflow_runs": [{
                    "id": 7654322,
                    "name": "CI",
                    "run_number": 18,
                    "head_sha": commit_sha,
                    "status": "completed",
                    "conclusion": "success",
                }]}
                provenance = "github-api"
            elif phase == "publish":
                capability = "workbench:github:pr"
                args = (json.dumps({
                    "owner": "AlphaEnginev1",
                    "repo": "Aurora",
                    "title": "Controlled fix",
                    "head": branch_name,
                    "base": "main",
                    "body": "Controlled evidence-backed change",
                }),)
                output = {
                    "number": 77,
                    "html_url": "https://github.com/AlphaEnginev1/Aurora/pull/77",
                    "state": "open",
                    "head": {"ref": branch_name, "sha": commit_sha},
                    "base": {"ref": "main", "sha": "e" * 40},
                }
                provenance = "github-api"

            task = SimpleNamespace(capability=capability, args=args)
            execution = SimpleNamespace(
                status="succeeded",
                provenance=provenance,
                output=output,
            )
            task_result = SimpleNamespace(
                status="succeeded",
                task=task,
                capability_result=SimpleNamespace(execution=execution) if capability else None,
            )
            return SimpleNamespace(objective=objective, tasks=(task_result,))

    director = EvidenceDirector()
    controllers = build_autonomy_runtime_bindings(
        director_service=director,
        router=FakeRouter(),
        workbench=FakeWorkbench(),
        research_service=FakeResearch(),
        skill_learning=object(),
    )
    result = controllers["software_engineer"].run("repair Aurora with evidence")

    assert result.status == "succeeded"
    assert len(director.calls) == 7
    assert result.final["change_id"] == commit_sha
    assert result.final["ci_head_sha"] == commit_sha
    assert result.final["pull_request_state"] == "open"
    assert result.final["pull_request_id"] == 77


def test_discovery_loop_does_not_promote_two_sources_to_trusted_knowledge():
    controllers, _, _, _ = build()
    result = controllers["discovery_loop"].run("test a research hypothesis")
    # Two sources are insufficient: a candidate source plus two independent
    # source observations are required, and a model verdict is not an artifact.
    assert result.status == "insufficient"
    assert result.knowledge == ()


def test_recursive_self_improvement_stops_when_modify_has_no_github_commit_receipt():
    controllers, director, _, _ = build()
    result = controllers["recursive_self_improvement"].run("improve Aurora")
    assert result.status == "blocked"
    assert result.change_id is None
    # Inspect/diagnose/research/modify run, then later phases do not launch
    # generic Director missions after the missing commit receipt is detected.
    assert len(director.calls) == 4
    assert len(result.cycles) == 7


def test_rsi_success_requires_feature_branch_commit_and_ci_for_the_exact_sha():
    import json

    from aurora.autonomy.runtime_bindings import build_autonomy_runtime_bindings

    commit_sha = "a" * 40
    branch_name = "fix/controlled-rsi-change"

    class EvidenceDirector:
        def __init__(self):
            self.calls = []

        def run_mission(self, *, objective, context=None, model=None):
            self.calls.append(objective)
            phase = objective.split("phase '", 1)[1].split("'", 1)[0]
            capability = None
            output = {}
            args = ()
            provenance = None
            if phase == "modify":
                capability = "workbench:github:file:write"
                args = (json.dumps({
                    "owner": "AlphaEnginev1",
                    "repo": "Aurora",
                    "branch": branch_name,
                    "path": "aurora/controlled.py",
                    "content": "# controlled change",
                    "message": "controlled change",
                }),)
                output = {"commit": {"sha": commit_sha}}
                provenance = "github-api"
            elif phase == "ci":
                capability = "workbench:github:workflow:read"
                output = {"workflow_runs": [{
                    "id": 7654321,
                    "name": "CI",
                    "run_number": 17,
                    "head_sha": commit_sha,
                    "status": "completed",
                    "conclusion": "success",
                }]}
                provenance = "github-api"

            task = SimpleNamespace(capability=capability, args=args)
            execution = SimpleNamespace(
                status="succeeded",
                provenance=provenance,
                output=output,
            )
            task_result = SimpleNamespace(
                status="succeeded",
                task=task,
                capability_result=SimpleNamespace(execution=execution) if capability else None,
            )
            return SimpleNamespace(objective=objective, tasks=(task_result,))

    director = EvidenceDirector()
    controllers = build_autonomy_runtime_bindings(
        director_service=director,
        router=FakeRouter(),
        workbench=FakeWorkbench(),
        research_service=FakeResearch(),
        skill_learning=object(),
    )

    result = controllers["recursive_self_improvement"].run("improve Aurora under evidence")

    assert result.status == "succeeded"
    assert result.change_id == commit_sha
    assert len(director.calls) == 7


def test_rsi_rejects_ci_success_for_a_different_commit():
    from aurora.autonomy.runtime_bindings import _mission_summary

    commit_sha = "a" * 40
    wrong_sha = "b" * 40

    class Director:
        def run_mission(self, *, objective, context=None, model=None):
            task = SimpleNamespace(
                capability="workbench:github:workflow:read",
                args=(),
            )
            execution = SimpleNamespace(
                status="succeeded",
                provenance="github-api",
                output={"workflow_runs": [{
                    "id": 7,
                    "name": "CI",
                    "run_number": 1,
                    "head_sha": wrong_sha,
                    "status": "completed",
                    "conclusion": "success",
                }]},
            )
            return SimpleNamespace(
                objective=objective,
                tasks=(SimpleNamespace(
                    status="succeeded",
                    task=task,
                    capability_result=SimpleNamespace(execution=execution),
                ),),
            )

    result = _mission_summary(
        Director(),
        "ci",
        "verify controlled change",
        {"change_id": commit_sha},
    )
    assert result["ci_status"] == "insufficient"
    assert result["ci_head_sha"] == commit_sha
    assert "ci_run_id" not in result


def test_rsi_rejects_writes_to_the_default_branch(monkeypatch):
    import json

    from aurora.autonomy.runtime_bindings import _mission_summary

    commit_sha = "c" * 40

    class Director:
        def run_mission(self, *, objective, context=None, model=None):
            task = SimpleNamespace(
                capability="workbench:github:file:write",
                args=(json.dumps({
                    "owner": "AlphaEnginev1",
                    "repo": "Aurora",
                    "branch": "main",
                    "path": "aurora/controlled.py",
                    "content": "# controlled change",
                    "message": "controlled change",
                }),),
            )
            execution = SimpleNamespace(
                status="succeeded",
                provenance="github-api",
                output={"commit": {"sha": commit_sha}},
            )
            return SimpleNamespace(
                objective=objective,
                tasks=(SimpleNamespace(
                    status="succeeded",
                    task=task,
                    capability_result=SimpleNamespace(execution=execution),
                ),),
            )

    result = _mission_summary(Director(), "modify", "controlled change", {})
    assert "change_id" not in result
    assert result["change_receipt_rejected"]
