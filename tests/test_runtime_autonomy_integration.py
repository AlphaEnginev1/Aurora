from pathlib import Path


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
