from aurora.autonomy.broker import CapabilityBroker
from aurora.autonomy.models import CapabilityRequest
from aurora.autonomy.policy import Policy

def test_broker_denies_unapproved_capability():
    b = CapabilityBroker(Policy(frozenset(), frozenset()))
    result = b.authorize(CapabilityRequest("tool:unknown", "test"))
    assert result.status == "denied"
