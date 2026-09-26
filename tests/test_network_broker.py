import pytest

from aurora.autonomy.network_broker import NetworkBroker, NetworkBrokerDecision


class Gateway:
    def __init__(self):
        self.calls = []

    def authorize(self, url):
        self.calls.append(("authorize", url))
        if "blocked.example" in url:
            raise RuntimeError("host is not allowed")

    def get(self, url):
        self.calls.append(("get", url))
        return b"ok"


def test_broker_denies_gateway_rejection():
    gateway = Gateway()
    broker = NetworkBroker(gateway)
    decision = broker.authorize("https://blocked.example/")
    assert decision == NetworkBrokerDecision(False, "host is not allowed")


def test_broker_allows_authorized_gateway_request():
    gateway = Gateway()
    broker = NetworkBroker(gateway)
    decision = broker.authorize("https://github.com/")
    assert decision.allowed is True
    assert broker.get("https://github.com/") == b"ok"
    assert gateway.calls[-1] == ("get", "https://github.com/")


def test_broker_never_bypasses_gateway():
    gateway = Gateway()
    broker = NetworkBroker(gateway)
    with pytest.raises(PermissionError):
        broker.get("https://blocked.example/")
    assert gateway.calls == [("authorize", "https://blocked.example/")]
