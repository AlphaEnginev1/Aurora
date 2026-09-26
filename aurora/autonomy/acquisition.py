"""Policy-controlled, non-interactive capability acquisition."""

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
from typing import Sequence

from .audit import append_event
from .registry import ToolSpec
from .verification import sha256_file

class AcquisitionError(RuntimeError):
    """Raised when a capability cannot be safely acquired."""

@dataclass(frozen=True)
class AcquisitionResult:
    tool: str
    status: str
    source: str
    executable: str
    path: str | None = None
    sha256: str | None = None

class AcquisitionManager:
    """Acquire approved system/runtime tools without arbitrary shell execution."""
    def __init__(self, *, allowed_sources: frozenset[str], audit_path: str | Path | None = None,
                 install_root: str | Path | None = None, timeout: int = 300) -> None:
        self.allowed_sources = allowed_sources
        self.audit_path = Path(audit_path) if audit_path else None
        self.install_root = Path(install_root) if install_root else None
        self.timeout = timeout

    def plan(self, tool_spec: ToolSpec) -> dict[str, str]:
        if tool_spec.source not in self.allowed_sources:
            raise AcquisitionError(f"source is not allowed: {tool_spec.source}")
        if tool_spec.kind not in {"system", "runtime"}:
            raise AcquisitionError(f"unsupported acquisition kind: {tool_spec.kind}")
        return {"tool": tool_spec.name, "source": tool_spec.source, "action": "install"}

    def acquire(self, tool_spec: ToolSpec) -> AcquisitionResult:
        self.plan(tool_spec)
        existing = shutil.which(tool_spec.executable)
        if existing:
            result = AcquisitionResult(tool_spec.name, "already_available", tool_spec.source, tool_spec.executable, existing, sha256_file(existing) if os.path.isfile(existing) else None)
            self._audit(result)
            return result
        if tool_spec.source == "ubuntu-apt":
            result = self._apt(tool_spec)
        elif tool_spec.source == "pypi":
            result = self._pip(tool_spec)
        else:
            raise AcquisitionError(f"no safe installer implemented for source: {tool_spec.source}")
        self._audit(result)
        return result

    def _run(self, args: Sequence[str]) -> subprocess.CompletedProcess[str]:
        if not args or any("\x00" in x for x in args):
            raise AcquisitionError("invalid installer arguments")
        try:
            return subprocess.run(list(args), check=False, capture_output=True, text=True, timeout=self.timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AcquisitionError(f"installer execution failed: {exc}") from exc

    def _apt(self, spec: ToolSpec) -> AcquisitionResult:
        if os.name == "nt":
            raise AcquisitionError("ubuntu-apt acquisition requires Linux/WSL")
        result = self._run(["sudo", "apt-get", "update"])
        if result.returncode != 0:
            raise AcquisitionError(f"apt update failed: {result.stderr.strip()}")
        result = self._run(["sudo", "apt-get", "install", "-y", spec.name])
        if result.returncode != 0:
            raise AcquisitionError(f"apt install failed: {result.stderr.strip()}")
        path = shutil.which(spec.executable)
        if not path:
            raise AcquisitionError(f"installed package did not provide executable: {spec.executable}")
        return AcquisitionResult(spec.name, "installed", spec.source, spec.executable, path, sha256_file(path) if os.path.isfile(path) else None)

    def _pip(self, spec: ToolSpec) -> AcquisitionResult:
        if not self.install_root:
            raise AcquisitionError("pypi acquisition requires an isolated install_root")
        self.install_root.mkdir(parents=True, exist_ok=True)
        python = shutil.which("python3") or shutil.which("python")
        if not python:
            raise AcquisitionError("python runtime not found")
        result = self._run([python, "-m", "pip", "install", "--target", str(self.install_root), spec.name])
        if result.returncode != 0:
            raise AcquisitionError(f"pip install failed: {result.stderr.strip()}")
        return AcquisitionResult(spec.name, "installed", spec.source, spec.executable, str(self.install_root), None)

    def _audit(self, result: AcquisitionResult) -> None:
        if self.audit_path:
            append_event(self.audit_path, {"event":"capability_acquisition", "tool":result.tool, "status":result.status, "source":result.source, "executable":result.executable, "path":result.path, "sha256":result.sha256})
