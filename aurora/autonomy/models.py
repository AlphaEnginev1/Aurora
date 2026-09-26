from dataclasses import dataclass, field
from typing import Any

@dataclass(frozen=True)
class CapabilityRequest:
    name: str
    actor: str
    reason: str = ""
    constraints: dict[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class CapabilityResult:
    name: str
    status: str
    version: str | None = None
    source: str | None = None
    verification: str = "unknown"
