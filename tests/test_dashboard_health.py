from aurora.dashboard.health import DashboardHealthService
from aurora.observability import ObservabilityEvent, ProductionObservability


def test_dashboard_health_exposes_truthful_component_states_and_agents(tmp_path):
    from tests.test_dashboard_gateway import _gateway

    gateway = _gateway(tmp_path)
    observer = ProductionObservability(tmp_path / "observability.jsonl")
    gateway.observability = observer

    result = DashboardHealthService(gateway).health()

    assert result["overall"] in {"HEALTHY", "UNKNOWN", "DEGRADED", "CRITICAL"}
    assert {item["name"] for item in result["components"]} >= {
        "Director", "Task Worker", "Agents", "Model Layer", "Observability",
        "Event Bus", "Egress Proxy",
    }
    assert result["agents"][0]["role"] == "researcher"
    assert result["agents"][0]["status"] == "HEALTHY"


def test_dashboard_health_records_task_failure_in_incident_history(tmp_path):
    from tests.test_dashboard_gateway import _gateway

    gateway = _gateway(tmp_path)
    gateway.task_store.append_event(
        mission_id="mission-1",
        task_id="task-1",
        event_type="retry_scheduled",
        payload={"error": "provider timeout", "api_key": "must-not-be-shown"},
        project_id="project-a",
        agent_role="researcher",
        correlation_id="corr-1",
    )

    result = DashboardHealthService(gateway).health()

    assert result["summary"]["active_incidents"] >= 1
    incident = result["incidents"][0]
    assert incident["task_id"] == "task-1"
    assert incident["agent_role"] == "researcher"
    assert incident["error"] == "provider timeout"
    assert incident["evidence"]["api_key"] == "[redacted]"


def test_dashboard_health_reads_observability_failures(tmp_path):
    from tests.test_dashboard_gateway import _gateway

    gateway = _gateway(tmp_path)
    observer = ProductionObservability(tmp_path / "observability.jsonl")
    gateway.observability = observer
    observer.record(
        ObservabilityEvent(
            event_type="execution_failed",
            correlation_id="corr-2",
            mission_id="mission-2",
            task_id="task-9",
            role="engineer",
            error_code="MODEL_TIMEOUT",
            status="failed",
        )
    )

    result = DashboardHealthService(gateway).health()

    assert any(
        item["source"] == "observability" and item["task_id"] == "task-9"
        for item in result["incidents"]
    )
