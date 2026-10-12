from pathlib import Path
from types import SimpleNamespace


def test_runtime_factory_exposes_autonomous_mission_manager(tmp_path: Path, monkeypatch):
    from aurora.orchestration.factory import build_director_runtime

    monkeypatch.setenv("AURORA_DIRECTOR_PROVIDER", "router")
    monkeypatch.setenv("AURORA_MODEL_DISCOVERY_REFRESH_ENABLED", "0")
    monkeypatch.setenv("AURORA_WORKBENCH_ROOTS", str(tmp_path))
    runtime = build_director_runtime(
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
    assert runtime.autonomous_mission_manager.runtime is runtime
    assert runtime.workbench is not None


def test_conversation_run_mission_uses_durable_runtime_and_returns_terminal_result(tmp_path: Path, monkeypatch):
    from aurora.orchestration.factory import build_director_runtime

    monkeypatch.setenv("AURORA_DIRECTOR_PROVIDER", "router")
    monkeypatch.setenv("AURORA_MODEL_DISCOVERY_REFRESH_ENABLED", "0")
    monkeypatch.setenv("AURORA_WORKBENCH_ROOTS", str(tmp_path))
    runtime = build_director_runtime(
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

    submission = SimpleNamespace(mission_id="mission-durable-test")
    terminal_result = SimpleNamespace(mission_id="mission-durable-test", status="succeeded")
    calls = []

    def submit_mission(**kwargs):
        calls.append(("submit", kwargs))
        return submission

    def run_until_terminal(actual_submission):
        calls.append(("terminal", actual_submission))
        return terminal_result

    monkeypatch.setattr(runtime, "submit_mission", submit_mission)
    monkeypatch.setattr(runtime, "run_until_terminal", run_until_terminal)

    assert runtime.conversation_service is not None
    result = runtime.conversation_service.mission_runner(objective="Run a governed read-only test")

    assert result is terminal_result
    assert calls == [
        ("submit", {"objective": "Run a governed read-only test"}),
        ("terminal", submission),
    ]
