from aurora.autonomy.acquisition import AcquisitionManager
from aurora.autonomy.broker import CapabilityBroker
from aurora.autonomy.models import CapabilityRequest
from aurora.autonomy.policy import Policy
from aurora.autonomy.registry import ToolSpec

def test_broker_ensure_uses_existing_tool(tmp_path):
    manager = AcquisitionManager(
        allowed_sources=frozenset({"ubuntu-apt"}),
        audit_path=tmp_path / "audit.jsonl",
    )
    broker = CapabilityBroker(
        Policy(frozenset({"tool:python"}), frozenset({"ubuntu-apt"})),
        {"python": ToolSpec("python", "system", "ubuntu-apt", "python3")},
        manager,
    )
    result = broker.ensure(CapabilityRequest("tool:python", "agent-1"))
    assert result.status == "already_available"
    assert result.path
    assert result.sha256
    assert result.verification == "verified"

def test_broker_ensure_rejects_unresolved_capability(tmp_path):
    manager = AcquisitionManager(
        allowed_sources=frozenset({"ubuntu-apt"}),
        audit_path=tmp_path / "audit.jsonl",
    )
    broker = CapabilityBroker(
        Policy(frozenset({"tool:missing"}), frozenset({"ubuntu-apt"})),
        {"python": ToolSpec("python", "system", "ubuntu-apt", "python3")},
        manager,
    )
    result = broker.ensure(CapabilityRequest("tool:missing", "agent-1"))
    assert result.status == "unresolved"

def test_broker_ensure_blocks_untrusted_source(tmp_path):
    manager = AcquisitionManager(
        allowed_sources=frozenset({"pypi"}),
        audit_path=tmp_path / "audit.jsonl",
    )
    broker = CapabilityBroker(
        Policy(frozenset({"tool:python"}), frozenset({"pypi"})),
        {"python": ToolSpec("python", "system", "ubuntu-apt", "python3")},
        manager,
    )
    result = broker.ensure(CapabilityRequest("tool:python", "agent-1"))
    assert result.status == "blocked_source"
