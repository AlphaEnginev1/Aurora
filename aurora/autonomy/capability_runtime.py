"""Controlled execution of brokered capabilities.

Agents never supply the executable itself. They request a registered capability;
the broker authorizes/acquires it, and this runtime invokes only the registered
executable inside Aurora's sandbox.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from .broker import CapabilityBroker
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

    def __init__(self, broker: CapabilityBroker, sandbox: SandboxExecutor) -> None:
        self.broker = broker
        self.sandbox = sandbox

    def execute(
        self,
        request: CapabilityRequest,
        *,
        workspace: str | Path,
        args: Sequence[str] = (),
        environment: Mapping[str, str] | None = None,
    ) -> CapabilityExecutionResult:
        capability = self.broker.ensure(request)
        if capability.status not in {"installed", "already_available"}:
            raise CapabilityExecutionError(
                f"capability {request.name} is not executable: {capability.status}"
            )
        if self.broker.policy.require_verification and capability.verification != "verified":
            raise CapabilityExecutionError(
                f"capability {request.name} is not verified: {capability.verification}"
            )

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
            raise CapabilityExecutionError(
                f"capability {request.name} sandbox execution failed: {exc}"
            ) from exc

        return CapabilityExecutionResult(
            capability=capability,
            command=command,
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            timed_out=result.timed_out,
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
