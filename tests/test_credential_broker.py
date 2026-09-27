from __future__ import annotations

import pytest

from aurora.autonomy.credential_broker import (
    CredentialBroker,
    CredentialBrokerError,
    CredentialGrant,
)


class Backend:
    def resolve(self, credential_id):
        return {"password": "SECRET"}


class Policy:
    def __init__(self, allowed=True):
        self.allowed = allowed
        self.calls = []

    def authorize(self, **kwargs):
        self.calls.append(kwargs)
        return self.allowed


def test_credential_broker_requires_policy_and_returns_secret_only_at_boundary():
    policy = Policy()
    broker = CredentialBroker(Backend(), policy)
    grant = CredentialGrant("site-login", "publish", "youtube", "mission-1", "publisher")

    values = broker.resolve(grant)

    assert values["password"] == "SECRET"
    assert policy.calls == [{
        "credential_id": "site-login",
        "purpose": "publish",
        "scope": "youtube",
        "mission_id": "mission-1",
        "actor": "publisher",
    }]


def test_credential_broker_denies_without_backend_access():
    policy = Policy(allowed=False)
    broker = CredentialBroker(Backend(), policy)
    with pytest.raises(CredentialBrokerError, match="denied"):
        broker.resolve(CredentialGrant("site-login", "publish", "youtube", "m", "a"))
    assert policy.calls
