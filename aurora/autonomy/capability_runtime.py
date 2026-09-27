"""Controlled execution of brokered capabilities.

Agents never supply the executable itself. They request a registered capability;
the broker authorizes/acquires it, and this runtime invokes only the registered
executable inside Aurora's sandbox.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
from pathlib import Path
from typing import Mapping, Sequence

from .audit import append_event
from .broker import CapabilityBroker
from .capability_resolution import CapabilityResolutionRuntime
from .models import CapabilityRequest, CapabilityResult
from .sandbox import SandboxError, SandboxExecutor


class CapabilityExecutionError(RuntimeError):
    """Raised when a brokered capability cannot be safely executed."""


@dataclass(frozen=True)
class CapabilityExecutionResult:
    capability: CapabilityResult
    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


class CapabilityRuntime:
    """Turn a brokered capability into a sandboxed process execution."""

    def __init__(
        self,
        broker: CapabilityBroker,
        sandbox: SandboxExecutor,
        *,
        audit_path: str | Path | None = None,
        resolution: CapabilityResolutionRuntime | None = None,
    ) -> None:
        self.broker = broker
        self.sandbox = sandbox
        self.audit_path = Path(audit_path) if audit_path else None
        self.resolution = resolution

    def execute(
        self,
        request: CapabilityRequest,
        *,
        workspace: str | Path,
        args: Sequence[str] = (),
        environment: Mapping[str, str] | None = None,
    ) -> CapabilityExecutionResult:
        capability = self._ensure(request)
        if capability.status not in {"installed", "already_available"}:
            self._audit(request, capability, status="blocked")
            raise CapabilityExecutionError(
                f"capability {request.name} is not executable: {capability.status}"
            )
        if self.broker.policy.require_verification and capability.verification != "verified":
            self._audit(request, capability, status="unverified")
            raise CapabilityExecutionError(
                f"capability {request.name} is not verified: {capability.verification}"
            )

        capability = replace(capability, artifact_id=self._artifact_id(request.name, capability))
        spec = self.broker.resolve(request)
        if spec is None:
            raise CapabilityExecutionError(
                f"capability {request.name} resolved without a registry specification"
            )

        root = Path(workspace).resolve()
        executable = self._runtime_executable(spec.source, spec.executable, capability.path, root)

        extra = tuple(args)
        if any("\x00" in item for item in extra):
            raise CapabilityExecutionError("capability arguments contain a null byte")
        command = (executable, *extra)

        try:
            result = self.sandbox.execute(
                command,
                workspace=root,
                environment=environment,
            )
        except SandboxError as exc:
            self._audit(request, capability, status="sandbox_failed")
            raise CapabilityExecutionError(
                f"capability {request.name} sandbox execution failed: {exc}"
            ) from exc

        self._audit(
            request,
            capability,
            status="succeeded" if result.returncode == 0 and not result.timed_out else "failed",
            returncode=result.returncode,
            timed_out=result.timed_out,
            argument_count=len(extra),
        )
        return CapabilityExecutionResult(
            capability=capability,
            command=command,
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            timed_out=result.timed_out,
        )

    @staticmethod
    def _artifact_id(name: str, result) -> str:
        material = "|".join(
            str(value or "") for value in
            (name, result.source, result.version, result.path, result.sha256)
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]

    def prepare_capabilities(
        self,
        names: Sequence[str],
        *,
        actor: str,
        reason: str,
    ) -> tuple[CapabilityResult, ...]:
        """Acquire/verify required tools and return their immutable runtime manifest."""
        prepared = []
        for name in dict.fromkeys(names):
            result = self._ensure(
                CapabilityRequest(name=name, actor=actor, reason=reason)
            )
            if result.status not in {"installed", "already_available"}:
                raise CapabilityExecutionError(
                    f"required capability {name} is unavailable: {result.status}"
                )
            if self.broker.policy.require_verification and result.verification != "verified":
                raise CapabilityExecutionError(
                    f"required capability {name} is not verified"
                )
            result = replace(
                result,
                artifact_id=self._artifact_id(name, result),
            )
            prepared.append(result)
        return tuple(prepared)

    def _ensure(self, request: CapabilityRequest) -> CapabilityResult:
        if self.resolution is None:
            return self.broker.ensure(request)
        return self.resolution.ensure(request).result

    def _audit(
        self,
        request: CapabilityRequest,
        capability: CapabilityResult,
        *,
        status: str,
        returncode: int | None = None,
        timed_out: bool = False,
        argument_count: int | None = None,
    ) -> None:
        if not self.audit_path:
            return
        append_event(
            self.audit_path,
            {
                "event": "capability_execution",
                "capability": request.name,
                "actor": request.actor,
                "reason": request.reason,
                "status": status,
                "acquisition_status": capability.status,
                "verification": capability.verification,
                "source": capability.source,
                "version": capability.version,
                "returncode": returncode,
                "timed_out": timed_out,
                "argument_count": argument_count,
            },
        )

    @staticmethod
    def _runtime_executable(
        source: str,
        registered_executable: str,
        acquired_path: str | None,
        workspace: Path,
    ) -> str:
        if not acquired_path:
            raise CapabilityExecutionError("acquired capability has no executable path")

        if source == "pypi":
            install_root = Path(acquired_path).resolve()
            if install_root.parent != workspace:
                raise CapabilityExecutionError(
                    "PyPI capability must be installed directly under the sandbox workspace"
                )
            candidate = install_root / "bin" / registered_executable
            if not candidate.is_file():
                raise CapabilityExecutionError(
                    f"registered PyPI executable is missing: {registered_executable}"
                )
            return f"/workspace/{install_root.name}/bin/{registered_executable}"

        candidate = Path(acquired_path).resolve()
        if not candidate.is_file():
            raise CapabilityExecutionError(
                f"registered executable path is missing: {candidate}"
            )
        return str(candidate)


__all__ = [
    "CapabilityExecutionError",
    "CapabilityExecutionResult",
    "CapabilityRuntime",
]
