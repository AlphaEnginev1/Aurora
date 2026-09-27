"""Autonomous capability resolution and fallback discovery.

The capability boundary remains authoritative: discovery may propose a ToolSpec,
but policy, acquisition, verification, and sandbox execution are still enforced
by the existing CapabilityBroker/AcquisitionManager path.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
from typing import Callable, Protocol, Sequence
from urllib.parse import urlencode

from .audit import append_event
from .broker import CapabilityBroker
from .models import CapabilityRequest, CapabilityResult
from .registry import ToolSpec
from .internet import InternetGateway, InternetPolicyError


class CapabilityDiscoveryProvider(Protocol):
    """Discover candidate tool specifications for an unmet capability."""

    def discover(
        self,
        request: CapabilityRequest,
    ) -> Sequence[ToolSpec]:
        ...


class HttpCapabilityDiscoveryProvider:
    """Discover governed ToolSpec candidates from an allow-listed JSON endpoint.

    Expected response:
    {"results": [{"name": "...", "kind": "runtime|system",
                  "source": "...", "executable": "...", ...}]}
    """

    def __init__(
        self,
        endpoint: str,
        gateway: InternetGateway,
        *,
        limit: int = 10,
    ) -> None:
        if not endpoint.strip():
            raise CapabilityResolutionError("capability discovery endpoint is empty")
        if limit < 1:
            raise CapabilityResolutionError("capability discovery limit must be positive")
        self.endpoint = endpoint
        self.gateway = gateway
        self.limit = limit

    def discover(self, request: CapabilityRequest) -> Sequence[ToolSpec]:
        query = urlencode({"capability": request.name, "limit": self.limit})
        separator = "&" if "?" in self.endpoint else "?"
        url = f"{self.endpoint}{separator}{query}"
        try:
            raw = self.gateway.get(url)
        except InternetPolicyError as exc:
            raise CapabilityResolutionError(
                f"capability discovery network access blocked: {exc}"
            ) from exc
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CapabilityResolutionError(
                "capability discovery returned invalid JSON"
            ) from exc
        rows = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise CapabilityResolutionError("capability discovery results must be a list")

        candidates: list[ToolSpec] = []
        for row in rows[: self.limit]:
            if not isinstance(row, dict):
                continue
            required = ("name", "kind", "source", "executable")
            if not all(isinstance(row.get(key), str) and row[key] for key in required):
                continue
            optional = {
                key: row[key]
                for key in ("version", "artifact_sha256")
                if row.get(key) is None or isinstance(row.get(key), str)
            }
            candidates.append(
                ToolSpec(
                    row["name"],
                    row["kind"],
                    row["source"],
                    row["executable"],
                    optional.get("version"),
                    optional.get("artifact_sha256"),
                )
            )
        return tuple(candidates)


@dataclass(frozen=True)
class CapabilityResolution:
    """Auditable result of autonomous capability resolution."""

    request_name: str
    result: CapabilityResult
    discovered: tuple[str, ...] = ()
    attempts: int = 0


class CapabilityResolutionError(RuntimeError):
    """Raised when autonomous capability resolution cannot continue safely."""


class CapabilityResolutionRuntime:
    """Resolve capabilities through registry first, then governed discovery.

    Discovery never executes a candidate directly. A candidate is inserted into
    the in-memory broker registry only after capability/source policy checks,
    then goes through the normal broker authorization and acquisition path.
    An optional registrar can persist the approved ToolSpec in a durable registry.
    """

    def __init__(
        self,
        broker: CapabilityBroker,
        *,
        discovery: CapabilityDiscoveryProvider | None = None,
        registrar: Callable[[ToolSpec], None] | None = None,
        audit_path: str | Path | None = None,
    ) -> None:
        self.broker = broker
        self.discovery = discovery
        self.registrar = registrar
        self.audit_path = Path(audit_path) if audit_path else None

    def ensure(self, request: CapabilityRequest) -> CapabilityResolution:
        initial = self.broker.ensure(request)
        if initial.status in {"installed", "already_available"}:
            return CapabilityResolution(request.name, initial, attempts=1)

        if self.discovery is None:
            self._audit(request, initial, discovered=(), attempts=1)
            return CapabilityResolution(request.name, initial, attempts=1)

        try:
            candidates = tuple(self.discovery.discover(request))
        except Exception as exc:
            self._audit(request, initial, discovered=(), attempts=1, error=str(exc))
            return CapabilityResolution(request.name, CapabilityResult(
                request.name,
                "discovery_failed",
                verification="discovery",
            ), attempts=1)

        discovered: list[str] = []
        attempts = 1
        last = initial
        requested_name = request.name.split(":", 1)[1] if ":" in request.name else request.name

        for candidate in candidates:
            attempts += 1
            if not isinstance(candidate, ToolSpec):
                continue
            if candidate.name != requested_name:
                continue
            if not self.broker.policy.allows_capability(request.name):
                last = CapabilityResult(request.name, "denied", verification="policy")
                break
            if not self.broker.policy.allows_source(candidate.source):
                last = CapabilityResult(
                    request.name,
                    "blocked_source",
                    source=candidate.source,
                    verification="policy",
                )
                continue
            if candidate.kind not in {"system", "runtime"}:
                continue

            # Registration is deliberately after policy checks and before
            # acquisition so the normal broker remains the execution boundary.
            self.broker.registry[candidate.name] = candidate
            if self.registrar is not None:
                try:
                    self.registrar(candidate)
                except Exception as exc:
                    last = CapabilityResult(
                        request.name,
                        "registration_failed",
                        source=candidate.source,
                        verification="registry",
                    )
                    self._audit(
                        request,
                        last,
                        discovered=tuple(discovered),
                        attempts=attempts,
                        error=str(exc),
                    )
                    continue

            discovered.append(candidate.name)
            last = self.broker.ensure(request)
            if last.status in {"installed", "already_available"}:
                self._audit(
                    request,
                    last,
                    discovered=tuple(discovered),
                    attempts=attempts,
                )
                return CapabilityResolution(
                    request.name,
                    last,
                    discovered=tuple(discovered),
                    attempts=attempts,
                )

        self._audit(
            request,
            last,
            discovered=tuple(discovered),
            attempts=attempts,
        )
        return CapabilityResolution(
            request.name,
            last,
            discovered=tuple(discovered),
            attempts=attempts,
        )

    def prepare(
        self,
        names: Sequence[str],
        *,
        actor: str,
        reason: str,
    ) -> tuple[CapabilityResult, ...]:
        prepared: list[CapabilityResult] = []
        for name in dict.fromkeys(names):
            resolution = self.ensure(
                CapabilityRequest(name=name, actor=actor, reason=reason)
            )
            result = resolution.result
            if result.status not in {"installed", "already_available"}:
                raise CapabilityResolutionError(
                    f"capability {name} could not be resolved: {result.status}"
                )
            if (
                self.broker.policy.require_verification
                and result.verification != "verified"
            ):
                raise CapabilityResolutionError(
                    f"capability {name} could not be verified"
                )
            prepared.append(result)
        return tuple(prepared)

    def _audit(
        self,
        request: CapabilityRequest,
        result: CapabilityResult,
        *,
        discovered: Sequence[str],
        attempts: int,
        error: str | None = None,
    ) -> None:
        if self.audit_path is None:
            return
        append_event(
            self.audit_path,
            {
                "event": "capability_resolution",
                "capability": request.name,
                "actor": request.actor,
                "reason": request.reason,
                "status": result.status,
                "source": result.source,
                "verification": result.verification,
                "discovered": list(discovered),
                "attempts": attempts,
                "error": error,
            },
        )


__all__ = [
    "CapabilityDiscoveryProvider",
    "HttpCapabilityDiscoveryProvider",
    "CapabilityResolution",
    "CapabilityResolutionError",
    "CapabilityResolutionRuntime",
]
