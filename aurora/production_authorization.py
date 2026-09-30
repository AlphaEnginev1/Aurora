"""Explicit, fail-closed authorization boundary for Aurora live execution."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping


REQUIRED_PREREQUISITES: Mapping[str, str] = MappingProxyType(
    {
        "LIVE-RESEARCH-DISCOVERY": "VERIFIED",
        "OS-NETWORK-ENFORCEMENT": "VERIFIED",
        "LIVE-NVIDIA": "VERIFIED",
        "LIVE-GEMINI": "VERIFIED",
        "PRODUCTION-OBSERVABILITY": "VERIFIED",
        "RECOVERY-DRILLS": "VERIFIED",
    }
)

ALLOWED_SCOPE = frozenset(
    {
        "director_live_execution",
        "live_external_research",
        "live_external_inference",
        "live_capability_acquisition",
    }
)


class LiveAuthorizationError(RuntimeError):
    """Raised when live execution authorization cannot be established."""


_SENSITIVE_KEY_RE = re.compile(
    r"(?:^|[_\-])(?:authorization(?:_(?:header|value))?(?!_id)|password|passwd|"
    r"secret|credential|api[_\-]?key|access[_\-]?token|refresh[_\-]?token|"
    r"private[_\-]?key|client[_\-]?secret|token)(?:$|[_\-])",
    re.IGNORECASE,
)

_CREDENTIAL_VALUE_PATTERNS = (
    re.compile(r"\bBearer\s+\S+", re.IGNORECASE),
    re.compile(r"\bBasic\s+[A-Za-z0-9+/=]+", re.IGNORECASE),
)


def _contains_credential_material(value: Any, *, key: str | None = None) -> bool:
    if key is not None and _SENSITIVE_KEY_RE.search(key):
        return True
    if isinstance(value, Mapping):
        return any(
            _contains_credential_material(child, key=str(child_key))
            for child_key, child in value.items()
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_contains_credential_material(child) for child in value)
    if isinstance(value, str):
        return any(pattern.search(value) for pattern in _CREDENTIAL_VALUE_PATTERNS)
    return False


def _parse_time(value: str, field_name: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise LiveAuthorizationError(
            f"invalid {field_name} timestamp"
        ) from exc


@dataclass(frozen=True, slots=True)
class AuthorizationRecord:
    authorization_id: str
    approved_by: str
    issued_at: str
    expires_at: str
    scope: frozenset[str]
    prerequisites: Mapping[str, str]
    live_enabled: bool
    revoked: bool
    kill_switch: bool
    canonical_commit: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "scope", frozenset(self.scope))
        object.__setattr__(
            self,
            "prerequisites",
            MappingProxyType(dict(self.prerequisites)),
        )
        object.__setattr__(
            self,
            "metadata",
            MappingProxyType(dict(self.metadata)),
        )


@dataclass(frozen=True, slots=True)
class LiveAuthorizationDecision:
    authorized: bool
    authorization_id: str
    approved_by: str
    operation: str
    scope: frozenset[str]
    canonical_commit: str
    issued_at: str
    expires_at: str


class ControlledLiveAuthorization:
    """Validate an explicitly supplied human authorization record."""

    def __init__(
        self,
        *,
        expected_prerequisites: Mapping[str, str] | None = None,
        expected_canonical_commit: str | None = None,
        clock_skew_seconds: int = 60,
    ) -> None:
        if clock_skew_seconds < 0:
            raise ValueError("clock_skew_seconds cannot be negative")
        self.expected_prerequisites = MappingProxyType(
            dict(expected_prerequisites or REQUIRED_PREREQUISITES)
        )
        self.expected_canonical_commit = expected_canonical_commit
        self.clock_skew = timedelta(seconds=clock_skew_seconds)

    @classmethod
    def from_file(cls, path: str | Path) -> AuthorizationRecord:
        file_path = Path(path)
        if not file_path.is_file():
            raise LiveAuthorizationError(
                f"authorization record is required: {file_path}"
            )
        try:
            raw = json.loads(file_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LiveAuthorizationError(
                "authorization record cannot be read"
            ) from exc
        if not isinstance(raw, dict):
            raise LiveAuthorizationError("authorization record must be an object")
        if _contains_credential_material(raw):
            raise LiveAuthorizationError(
                "credential material is not permitted in authorization record"
            )
        required = {
            "authorization_id",
            "approved_by",
            "issued_at",
            "expires_at",
            "scope",
            "prerequisites",
            "live_enabled",
            "revoked",
            "kill_switch",
            "canonical_commit",
        }
        missing = sorted(required - raw.keys())
        if missing:
            raise LiveAuthorizationError(
                f"authorization record missing fields: {', '.join(missing)}"
            )
        try:
            scope = frozenset(str(item) for item in raw["scope"])
            prerequisites = dict(raw["prerequisites"])
            metadata = dict(raw.get("metadata", {}))
        except (TypeError, ValueError) as exc:
            raise LiveAuthorizationError(
                "authorization record contains invalid structured fields"
            ) from exc
        return AuthorizationRecord(
            authorization_id=str(raw["authorization_id"]),
            approved_by=str(raw["approved_by"]),
            issued_at=str(raw["issued_at"]),
            expires_at=str(raw["expires_at"]),
            scope=scope,
            prerequisites=prerequisites,
            live_enabled=bool(raw["live_enabled"]),
            revoked=bool(raw["revoked"]),
            kill_switch=bool(raw["kill_switch"]),
            canonical_commit=str(raw["canonical_commit"]),
            metadata=metadata,
        )

    def require(
        self,
        record: AuthorizationRecord | None,
        *,
        operation: str,
        canonical_commit: str,
        now: datetime | None = None,
    ) -> LiveAuthorizationDecision:
        if record is None:
            raise LiveAuthorizationError("authorization record is required")
        if not isinstance(record, AuthorizationRecord):
            raise LiveAuthorizationError("invalid authorization record type")
        payload = {
            "authorization_id": record.authorization_id,
            "approved_by": record.approved_by,
            "issued_at": record.issued_at,
            "expires_at": record.expires_at,
            "scope": record.scope,
            "prerequisites": record.prerequisites,
            "live_enabled": record.live_enabled,
            "revoked": record.revoked,
            "kill_switch": record.kill_switch,
            "canonical_commit": record.canonical_commit,
            "metadata": record.metadata,
        }
        if _contains_credential_material(payload):
            raise LiveAuthorizationError(
                "credential material is not permitted in authorization record"
            )
        if not record.live_enabled:
            raise LiveAuthorizationError("live execution is not enabled")
        if not record.approved_by.strip():
            raise LiveAuthorizationError("approved_by is required")
        if not record.authorization_id.strip():
            raise LiveAuthorizationError("authorization_id is required")
        if record.revoked:
            raise LiveAuthorizationError("authorization is revoked")
        if record.kill_switch:
            raise LiveAuthorizationError("kill switch is active")
        if operation not in ALLOWED_SCOPE:
            raise LiveAuthorizationError(f"unknown scope operation: {operation}")
        if not record.scope:
            raise LiveAuthorizationError("scope is empty")
        unknown_scope = sorted(record.scope - ALLOWED_SCOPE)
        if unknown_scope:
            raise LiveAuthorizationError(
                f"unknown scope operation: {unknown_scope[0]}"
            )
        if operation not in record.scope:
            raise LiveAuthorizationError(
                f"operation not authorized by scope: {operation}"
            )

        expected = self.expected_prerequisites
        for name, required_state in expected.items():
            actual = record.prerequisites.get(name)
            if actual != required_state:
                raise LiveAuthorizationError(
                    f"prerequisite {name} is not VERIFIED"
                )

        if not canonical_commit.strip():
            raise LiveAuthorizationError("canonical commit is required")
        if not record.canonical_commit.strip():
            raise LiveAuthorizationError("authorization canonical commit is required")
        if record.canonical_commit != canonical_commit:
            raise LiveAuthorizationError("authorization canonical commit mismatch")
        if (
            self.expected_canonical_commit is not None
            and canonical_commit != self.expected_canonical_commit
        ):
            raise LiveAuthorizationError(
                "runtime canonical commit does not match verified commit"
            )

        issued = _parse_time(record.issued_at, "issued_at")
        expires = _parse_time(record.expires_at, "expires_at")
        current = now or datetime.now(timezone.utc)
        if issued.tzinfo is None or expires.tzinfo is None:
            raise LiveAuthorizationError("authorization timestamps must include timezone")
        if expires <= issued:
            raise LiveAuthorizationError("authorization expiry must be after issue time")
        if issued - current > self.clock_skew:
            raise LiveAuthorizationError("authorization is issued in the future")
        if current - expires > self.clock_skew:
            raise LiveAuthorizationError("authorization is expired")

        return LiveAuthorizationDecision(
            authorized=True,
            authorization_id=record.authorization_id,
            approved_by=record.approved_by,
            operation=operation,
            scope=record.scope,
            canonical_commit=record.canonical_commit,
            issued_at=record.issued_at,
            expires_at=record.expires_at,
        )


__all__ = [
    "ALLOWED_SCOPE",
    "AuthorizationRecord",
    "ControlledLiveAuthorization",
    "LiveAuthorizationDecision",
    "LiveAuthorizationError",
    "REQUIRED_PREREQUISITES",
]
