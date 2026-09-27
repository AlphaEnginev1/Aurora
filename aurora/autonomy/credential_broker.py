"""Governed credential and identity broker.

Agents receive opaque references. Only an execution boundary can resolve a
reference, and every resolution is scoped to a mission, actor and purpose.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol


class CredentialBrokerError(RuntimeError):
    """Raised when credential policy or resolution fails."""


@dataclass(frozen=True)
class CredentialGrant:
    credential_id: str
    purpose: str
    scope: str
    mission_id: str
    actor: str


class SecretBackend(Protocol):
    def resolve(self, credential_id: str) -> Mapping[str, str]:
        ...


class CredentialPolicy(Protocol):
    def authorize(self, *, credential_id: str, purpose: str, scope: str, mission_id: str, actor: str) -> bool:
        ...


class CredentialBroker:
    """Resolve credentials only after explicit policy authorization."""

    def __init__(self, backend: SecretBackend, policy: CredentialPolicy) -> None:
        self.backend = backend
        self.policy = policy

    def resolve(self, grant: CredentialGrant) -> Mapping[str, str]:
        if not self.policy.authorize(
            credential_id=grant.credential_id,
            purpose=grant.purpose,
            scope=grant.scope,
            mission_id=grant.mission_id,
            actor=grant.actor,
        ):
            raise CredentialBrokerError(
                f"credential access denied: {grant.credential_id}"
            )
        values = dict(self.backend.resolve(grant.credential_id))
        if not values:
            raise CredentialBrokerError(
                f"credential backend returned no values: {grant.credential_id}"
            )
        return values


__all__ = ["CredentialBroker", "CredentialBrokerError", "CredentialGrant", "SecretBackend", "CredentialPolicy"]
