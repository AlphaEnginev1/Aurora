"""Contracts for Aurora's Universal Work / Cowork Workbench."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class WorkbenchCapability:
    capability_id: str
    kind: str
    available: bool
    authorized: bool
    healthy: bool
    details: str = ""


@dataclass(frozen=True)
class WorkbenchRequest:
    capability: str
    actor: str
    mission_id: str
    reason: str = ""
    arguments: tuple[str, ...] = ()
    options: dict[str, Any] = field(default_factory=dict)
    correlation_id: str | None = None
    idempotency_key: str | None = None


@dataclass(frozen=True)
class WorkbenchResult:
    capability: str
    operation: str
    status: str
    output: Any = None
    artifact_id: str | None = None
    provenance: str | None = None
    error: str | None = None
    receipt_id: str | None = None
    correlation_id: str | None = None


__all__ = ["WorkbenchCapability", "WorkbenchRequest", "WorkbenchResult"]
