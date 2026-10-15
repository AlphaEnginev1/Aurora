"""Joint acceptance of Aurora's four remaining autonomous controllers.

Builds the actual canonical DirectorRuntime factory, then replaces external
execution seams with deterministic receipts so this test performs no live model
calls, external research calls, workstation mutations, or GitHub writes.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4


def _build_runtime(tmp_path: Path, monkeypatch):
    from aurora.orchestration.factory import build_director_runtime

    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("AURORA_DIRECTOR_PROVIDER", "router")
    monkeypatch.setenv("AURORA_MODEL_DISCOVERY_REFRESH_ENABLED", "0")
    monkeypatch.setenv("AURORA_WORKBENCH_ROOTS", str(tmp_path))
    return build_director_runtime(
        inference_policy_path="config/inference-policy.json",
        autonomy_policy_path="config/autonomy-policy.json",
        registry_path="config/tool-registry.json",
        network_policy_path="config/network-policy.json",
        workspace_root=tmp_path,
        egress_proxy_host="127.0.0.1",
        egress_proxy_port=3128,
        organization_path="config/organization.json",
        skills_path="config/skills.json",
        projects_path="config/projects.json",
    )


def test_joint_autonomy_controller_acceptance_uses_canonical_runtime_services(
    tmp_path: Path, monkeypatch
):
    """Exercise all four controllers together through the production factory."""
    runtime = _build_runtime(tmp_path, monkeypatch)
    controllers = runtime.autonomy_controllers

    # Prove there is one controller graph, using the same service objects the
    # production factory exposed; no parallel Workbench/Router/Learning store.
    assert runtime.autonomous_computer_use is controllers["computer_use"]
    assert runtime.autonomous_software_engineer is controllers["software_engineer"]
    assert runtime.autonomous_discovery_loop is controllers["discovery_loop"]
    assert runtime.recursive_self_improvement is controllers["recursive_self_improvement"]
    assert controllers["learning_store"] is runtime.skill_learning

    from aurora.research.learning import ResearchLearningGate
    from aurora.research.models import SourceRecord

    workbench = runtime.workbench
    router = runtime.director.planner.router
    director_planner = runtime.director.planner
    task_engine = runtime.director.task_engine
    research_service = workbench.research
    assert task_engine.orchestration._workbench is workbench
    assert task_engine.skill_learning is controllers["learning_store"]
    assert research_service is not None

    workbench_calls = []
    director_calls = []
    research_calls = []

    def execute_workbench(request):
        workbench_calls.append(request)
        if request.capability == "workbench:census":
            output = [{"capability_id": "workbench:computer", "available": True}]
        elif request.capability == "workbench:computer":
            output = {"completed": True, "status": "succeeded"}
        else:
            raise AssertionError(f"unexpected Workbench capability: {request.capability}")
        return SimpleNamespace(status="succeeded", error=None, output=output)

    def generate(request, *, capability="reasoning", preferred_models=None):
        prompt = request.messages[-1]["content"]
        if "Choose at most one safe next action" in prompt:
            content = '{"action":{"objective":"controlled acceptance","action":"observe"}}'
        elif "Choose one candidate claim by its exact claim_id" in prompt:
            payload = json.loads(prompt.split("\n", 1)[1])
            content = json.dumps({"claim_id": payload["candidates"][0]["claim_id"]})
        elif "Classify whether this independent source provides support" in prompt:
            content = json.dumps({
                "relation": "support",
                "evidence_text": "Aurora stores durable research source artifacts.",
            })
        elif "Classify whether the claim is novel" in prompt:
            content = "novel"
        else:
            raise AssertionError("unexpected router prompt in joint autonomy acceptance")
        return SimpleNamespace(content=content)

    def plan(*, objective, context=None, model=None):
        director_calls.append(objective)
        return SimpleNamespace(
            objective=objective,
            tasks=(SimpleNamespace(task_id="controlled-task", status="succeeded"),),
        )

    def execute_tasks(tasks):
        return (SimpleNamespace(status="succeeded"),)

    def discover(request):
        research_calls.append(("discover", request.objective))
        return (
            "https://research.example/a",
            "https://research.example/b",
            "https://research.example/c",
        )

    def retrieve(request):
        research_calls.append(("retrieve", request.objective, request.required_sources))
        sources = []
        for url in request.required_sources:
            body = (
                "Aurora stores durable research source artifacts. "
                "Each source preserves a stable provenance record."
            ).encode("utf-8")
            source = SourceRecord(
                source_id=f"controlled-source-{uuid4().hex}",
                url=url,
                retrieved_at=datetime.now(timezone.utc).isoformat(),
                content_sha256=hashlib.sha256(body).hexdigest(),
                status_code=200,
                content_type="text/plain",
                content_text=body.decode("utf-8"),
                final_url=url,
            )
            research_service.store.record_source(source)
            sources.append(source)
        return tuple(sources)


    # Stub only the external side-effect seams on the canonical service objects.
    # No live provider, real workstation operation, internet search, or code
    # publication is performed by this acceptance test.
    monkeypatch.setattr(workbench, "execute", execute_workbench)
    monkeypatch.setattr(router, "generate", generate)
    monkeypatch.setattr(director_planner, "plan", plan)
    monkeypatch.setattr(task_engine, "execute", execute_tasks)
    monkeypatch.setattr(research_service, "discover", discover)
    monkeypatch.setattr(research_service, "retrieve", retrieve)

    computer_result = controllers["computer_use"].run("inspect controlled workstation")
    assert len(computer_result) == 1
    assert [call.capability for call in workbench_calls] == [
        "workbench:census",
        "workbench:computer",
        "workbench:census",
    ]

    engineering_result = controllers["software_engineer"].run(
        "complete controlled software engineering acceptance"
    )
    assert engineering_result.status == "succeeded"
    assert [iteration.result["phase"] for iteration in engineering_result.iterations] == [
        "inspect", "research", "plan", "edit", "test", "verify", "publish"
    ]
    assert len(director_calls) == 7

    discovery_result = controllers["discovery_loop"].run(
        "test one controlled research hypothesis"
    )
    assert discovery_result.status == "discovered"
    assert len(discovery_result.knowledge) == 1
    artifact_id = discovery_result.knowledge[0]
    artifact = research_service.store.knowledge_artifact(artifact_id)
    assert artifact is not None
    assert artifact_id.startswith("kart-")
    assert artifact.state.value == "verified"
    assert len(artifact.evidence_ids) >= 2
    signal = ResearchLearningGate(research_service.store).authorize((artifact_id,))
    assert signal.artifact_ids == (artifact_id,)
    assert len(research_service.store.citation_edges(artifact_id)) >= 5
    assert [call[0] for call in research_calls] == ["discover", "retrieve", "retrieve"]

    # RSI may not claim success merely because Director phases completed.
    # The first four phases run, then absence of a real GitHub feature-branch
    # commit receipt blocks all later phase missions.
    rsi_result = controllers["recursive_self_improvement"].run(
        "improve Aurora under controlled acceptance"
    )
    assert rsi_result.status == "blocked"
    assert rsi_result.change_id is None
    assert len(rsi_result.cycles) == 7
    assert len(director_calls) == 11




def test_production_runtime_wires_task_engine_to_canonical_research_stack(
    tmp_path: Path, monkeypatch
):
    """The runtime, TaskEngine and Workbench share one canonical research stack."""
    from aurora.orchestration.task_engine import DelegatedTask, TaskStatus

    runtime = _build_runtime(tmp_path, monkeypatch)
    workbench = runtime.workbench
    task_engine = runtime.director.task_engine
    research_service = workbench.research

    assert research_service is not None
    assert task_engine.research_service is research_service
    assert research_service.gateway is workbench.gateway
    assert task_engine.research_learning_gate is not None
    assert task_engine.research_learning_gate.store is research_service.store

    calls = []

    def discover(request):
        calls.append(("discover", request.objective))
        return ("https://research.example/runtime-wiring",)

    def retrieve_and_extract(request):
        calls.append(("retrieve_and_extract", request.objective, request.required_sources))
        return (
            (SimpleNamespace(source_id="controlled-source"),),
            (SimpleNamespace(claim_id="controlled-claim"),),
        )

    # Exercise the actual TaskEngine research dispatch without external network
    # calls; only the provider-facing discovery/retrieval boundary is controlled.
    monkeypatch.setattr(research_service, "discover", discover)
    monkeypatch.setattr(research_service, "retrieve_and_extract", retrieve_and_extract)

    result = task_engine.execute_one(
        DelegatedTask(
            task_id="runtime-research-wiring",
            role="researcher",
            objective="Verify the canonical research dependency wiring",
            capability="research",
        )
    )

    assert result.status is TaskStatus.SUCCEEDED
    assert result.capability_result.source_ids == ("controlled-source",)
    assert result.capability_result.claim_ids == ("controlled-claim",)
    assert [item[0] for item in calls] == ["discover", "retrieve_and_extract"]
    assert calls[1][2] == ("https://research.example/runtime-wiring",)
