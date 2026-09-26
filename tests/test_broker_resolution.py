from aurora.autonomy.broker import CapabilityBroker
from aurora.autonomy.models import CapabilityRequest
from aurora.autonomy.policy import Policy
from aurora.autonomy.registry import ToolSpec

def test_broker_resolves_authorized_tool():
    broker = CapabilityBroker(Policy(frozenset({"tool:rg"}), frozenset({"ubuntu-apt"})), {"rg": ToolSpec("rg", "system", "ubuntu-apt", "rg")})
    result = broker.authorize(CapabilityRequest("tool:rg", "agent-1", "search source"))
    assert result.status == "authorized"
    assert result.source == "ubuntu-apt"
    assert result.verification == "required"

def test_broker_denies_unknown_tool():
    broker = CapabilityBroker(Policy(frozenset({"tool:rg"}), frozenset({"ubuntu-apt"})), {})
    assert broker.authorize(CapabilityRequest("tool:rg", "agent-1")).status == "unresolved"

def test_broker_blocks_untrusted_source():
    broker = CapabilityBroker(Policy(frozenset({"tool:rg"}), frozenset({"pypi"})), {"rg": ToolSpec("rg", "system", "ubuntu-apt", "rg")})
    assert broker.authorize(CapabilityRequest("tool:rg", "agent-1")).status == "blocked_source"
