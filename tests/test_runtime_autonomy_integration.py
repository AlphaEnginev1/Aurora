from pathlib import Path


def _build_runtime(tmp_path: Path, monkeypatch):
    from aurora.orchestration.factory import build_director_runtime

    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
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


def test_runtime_factory_exposes_autonomous_mission_manager(tmp_path: Path, monkeypatch):
    runtime = _build_runtime(tmp_path, monkeypatch)

    assert runtime.autonomous_mission_manager.runtime is runtime
    assert runtime.workbench is not None


def test_runtime_factory_wires_all_autonomy_controllers_to_canonical_runtime(
    tmp_path: Path, monkeypatch
):
    """Prove the production factory composes the controllers, not only test doubles."""
    from aurora.autonomy.computer_use import AutonomousComputerUse
    from aurora.autonomy.discovery_loop import DiscoveryLoop
    from aurora.autonomy.rsi import RecursiveSelfImprovement
    from aurora.autonomy.software_engineer import AutonomousSoftwareEngineer

    runtime = _build_runtime(tmp_path, monkeypatch)
    controllers = runtime.autonomy_controllers

    assert {
        "computer_use",
        "software_engineer",
        "discovery_loop",
        "recursive_self_improvement",
        "learning_store",
    } <= set(controllers)
    assert isinstance(controllers["computer_use"], AutonomousComputerUse)
    assert isinstance(controllers["software_engineer"], AutonomousSoftwareEngineer)
    assert isinstance(controllers["discovery_loop"], DiscoveryLoop)
    assert isinstance(controllers["recursive_self_improvement"], RecursiveSelfImprovement)

    # The public runtime aliases must be the exact controller instances held by
    # the shared controller registry; no duplicate/parallel controller graph.
    assert runtime.autonomous_computer_use is controllers["computer_use"]
    assert runtime.autonomous_software_engineer is controllers["software_engineer"]
    assert runtime.autonomous_discovery_loop is controllers["discovery_loop"]
    assert runtime.recursive_self_improvement is controllers["recursive_self_improvement"]

    # The bindings must retain the canonical live runtime services.
    assert controllers["learning_store"] is runtime.skill_learning
    assert runtime.autonomous_mission_manager.runtime is runtime
    assert runtime.workbench is not None
