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


def test_conversation_run_mission_preserves_accepted_receipt_after_status_runtime_error(
    tmp_path: Path, monkeypatch
):
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

    submission = SimpleNamespace(
        mission_id="mission-accepted-status-error",
        plan=SimpleNamespace(objective="Inspect accepted receipt", tasks=()),
    )
    snapshot = SimpleNamespace(
        submission=submission,
        mission_id=submission.mission_id,
        status=SimpleNamespace(value="running"),
        tasks=(),
        events=(),
        wait_timed_out=False,
        wait_timeout_seconds=None,
    )
    calls = []

    monkeypatch.setattr(
        runtime,
        "submit_mission",
        lambda **kwargs: calls.append(("submit", kwargs)) or submission,
    )

    def fail_wait(actual_submission, *, timeout_seconds):
        calls.append(("wait", actual_submission, timeout_seconds))
        raise RuntimeError("controlled status store interruption")

    monkeypatch.setattr(runtime, "wait_until_terminal", fail_wait)
    monkeypatch.setattr(
        runtime,
        "durable_mission_snapshot",
        lambda mission_id: snapshot if mission_id == submission.mission_id else None,
    )
    result = runtime.conversation_service.mission_runner(
        objective="Inspect accepted receipt"
    )

    assert result is snapshot
    assert result.submission.mission_id == submission.mission_id
    assert result.status.value == "running"
    assert calls[0][0] == "submit"
    assert [item[0] for item in calls].count("submit") == 1
    assert [item[0] for item in calls].count("wait") == 1


def test_conversation_run_mission_returns_conservative_receipt_when_status_reads_fail(
    tmp_path: Path, monkeypatch
):
    from aurora.orchestration.factory import build_director_runtime
    from aurora.orchestration.task_engine import TaskStatus

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

    submission = SimpleNamespace(
        mission_id="mission-accepted-status-unavailable",
        plan=SimpleNamespace(objective="Preserve accepted order", tasks=()),
    )
    submitted = []

    def submit(**kwargs):
        submitted.append(kwargs)
        return submission

    def fail_wait(_submission, *, timeout_seconds):
        raise RuntimeError("controlled wait failure")

    def fail_snapshot(_mission_id):
        raise RuntimeError("controlled snapshot read failure")

    monkeypatch.setattr(runtime, "submit_mission", submit)
    monkeypatch.setattr(runtime, "wait_until_terminal", fail_wait)
    monkeypatch.setattr(runtime, "durable_mission_snapshot", fail_snapshot)
    monkeypatch.setenv("AURORA_CONVERSATION_MISSION_TIMEOUT_SECONDS", "45")

    receipt = runtime.conversation_service.mission_runner(
        objective="Preserve accepted order"
    )

    assert receipt.submission.mission_id == submission.mission_id
    assert receipt.status is TaskStatus.RUNNING
    assert receipt.wait_timed_out is True
    assert receipt.wait_timeout_seconds == 45.0
    assert receipt.tasks == ()
    assert submitted == [{"objective": "Preserve accepted order"}]


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

    def wait_until_terminal(actual_submission, *, timeout_seconds):
        calls.append(("wait", actual_submission, timeout_seconds))
        return terminal_result

    monkeypatch.setattr(runtime, "submit_mission", submit_mission)
    monkeypatch.setattr(runtime, "wait_until_terminal", wait_until_terminal)
    monkeypatch.setenv("AURORA_CONVERSATION_MISSION_TIMEOUT_SECONDS", "45")

    assert runtime.conversation_service is not None
    result = runtime.conversation_service.mission_runner(objective="Run a governed read-only test")

    assert result is terminal_result
    assert calls == [
        ("submit", {"objective": "Run a governed read-only test"}),
        ("wait", submission, 45.0),
    ]
