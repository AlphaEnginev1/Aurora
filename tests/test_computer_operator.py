from __future__ import annotations

import pytest

from aurora.autonomy.computer_operator import (
    ComputerOperator,
    ComputerOperatorError,
    ComputerPolicy,
    CredentialRef,
)


class FakeSession:
    def __init__(self):
        self.calls = []
        self.closed = False

    def navigate(self, url):
        self.calls.append(("navigate", url))

    def click(self, selector):
        self.calls.append(("click", selector))

    def fill(self, selector, value):
        self.calls.append(("fill", selector, value))

    def extract(self, selector):
        self.calls.append(("extract", selector))
        return "visible text"

    def screenshot(self, path):
        self.calls.append(("screenshot", path))

    def close(self):
        self.closed = True


class FakeCredentials:
    def resolve(self, ref):
        return {"username": "agent@example.test", "password": "SECRET"}


def test_computer_operator_enforces_host_and_hides_credentials():
    session = FakeSession()
    operator = ComputerOperator(
        ComputerPolicy(allowed_hosts=frozenset({"example.test"})),
        lambda: session,
        credential_provider=FakeCredentials(),
    )
    output = operator.run(
        [
            {"action": "navigate", "url": "https://example.test/login"},
            {"action": "fill", "selector": "username", "value": "agent@example.test"},
            {"action": "fill", "selector": "password", "credential_id": "login"},
            {"action": "extract", "selector": "body"},
        ],
        actor="researcher",
        mission_id="mission-1",
        credential_refs=(CredentialRef("login", "site login", "example.test"),),
    )
    assert "SECRET" not in output
    assert session.calls[2] == ("fill", "password", "SECRET")
    assert session.closed is True


def test_computer_operator_rejects_unauthorized_host():
    operator = ComputerOperator(
        ComputerPolicy(allowed_hosts=frozenset({"example.test"})),
        FakeSession,
    )
    with pytest.raises(ComputerOperatorError, match="not authorized"):
        operator.run(
            [{"action": "navigate", "url": "https://evil.example/login"}],
            actor="researcher",
            mission_id="mission-1",
        )


def test_computer_operator_rejects_http_when_https_required():
    operator = ComputerOperator(
        ComputerPolicy(allowed_hosts=frozenset({"example.test"})),
        FakeSession,
    )
    with pytest.raises(ComputerOperatorError, match="requires HTTPS"):
        operator.run(
            [{"action": "navigate", "url": "http://example.test/login"}],
            actor="researcher",
            mission_id="mission-1",
        )


def test_computer_operator_bounds_action_count():
    operator = ComputerOperator(
        ComputerPolicy(allowed_hosts=frozenset({"example.test"}), max_steps=1),
        FakeSession,
    )
    with pytest.raises(ComputerOperatorError, match="max_steps"):
        operator.run(
            [
                {"action": "navigate", "url": "https://example.test"},
                {"action": "extract", "selector": "body"},
            ],
            actor="researcher",
            mission_id="mission-1",
        )
