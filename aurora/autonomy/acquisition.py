"""Policy-controlled, non-interactive capability acquisition."""

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
from typing import Sequence

from .audit import append_event
from .internet import InternetGateway, InternetPolicyError
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
    """Acquire approved tools through fixed installers and the Internet Gateway."""

    _SOURCE_URLS = {
        "pypi": "https://pypi.org/simple/",
        "ubuntu-apt": (
            "https://archive.ubuntu.com/ubuntu/",
            "https://security.ubuntu.com/ubuntu/",
        ),
    }

    def __init__(
        self,
        *,
        allowed_sources: frozenset[str],
        audit_path: str | Path | None = None,
        install_root: str | Path | None = None,
        timeout: int = 300,
        internet_gateway: InternetGateway | None = None,
    ) -> None:
        self.allowed_sources = allowed_sources
        self.audit_path = Path(audit_path) if audit_path else None
        self.install_root = Path(install_root) if install_root else None
        self.timeout = timeout
        self.internet_gateway = internet_gateway

    def plan(self, tool_spec: ToolSpec) -> dict[str, str]:
        if tool_spec.source not in self.allowed_sources:
            raise AcquisitionError(f"source is not allowed: {tool_spec.source}")
        if tool_spec.kind not in {"system", "runtime"}:
            raise AcquisitionError(f"unsupported acquisition kind: {tool_spec.kind}")
        self._authorize_source_network(tool_spec.source)
        return {"tool": tool_spec.name, "source": tool_spec.source, "action": "install"}

    def acquire(self, tool_spec: ToolSpec) -> AcquisitionResult:
        self.plan(tool_spec)
        existing = shutil.which(tool_spec.executable)
        if existing:
            result = AcquisitionResult(
                tool_spec.name,
                "already_available",
                tool_spec.source,
                tool_spec.executable,
                existing,
                sha256_file(existing) if os.path.isfile(existing) else None,
            )
            self._audit(result)
            return result

        if tool_spec.source == "ubuntu-apt":
            result = self._apt(tool_spec)
        elif tool_spec.source == "pypi":
            result = self._pip(tool_spec)
        else:
            raise AcquisitionError(
                f"no safe installer implemented for source: {tool_spec.source}"
            )
        self._audit(result)
        return result

    def _authorize_source_network(self, source: str) -> None:
        """Require every networked installer source to pass the gateway policy."""
        if self.internet_gateway is None:
            return
        urls = self._SOURCE_URLS.get(source)
        if not urls:
            raise AcquisitionError(f"no network policy mapping for source: {source}")
        try:
            for url in urls if isinstance(urls, tuple) else (urls,):
                self.internet_gateway.authorize(url)
        except InternetPolicyError as exc:
            raise AcquisitionError(
                f"source network access blocked by Internet Gateway: {source}"
            ) from exc

    def _run(self, args: Sequence[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        if not args or any("\x00" in x for x in args):
            raise AcquisitionError("invalid installer arguments")
        try:
            return subprocess.run(
                list(args),
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=env,
            )
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
            raise AcquisitionError(
                f"installed package did not provide executable: {spec.executable}"
            )
        return AcquisitionResult(
            spec.name,
            "installed",
            spec.source,
            spec.executable,
            path,
            sha256_file(path) if os.path.isfile(path) else None,
        )

    def _pip(self, spec: ToolSpec) -> AcquisitionResult:
        if not self.install_root:
            raise AcquisitionError("pypi acquisition requires an isolated install_root")
        self.install_root.mkdir(parents=True, exist_ok=True)
        python = shutil.which("python3") or shutil.which("python")
        if not python:
            raise AcquisitionError("python runtime not found")

        env = os.environ.copy()
        env["PIP_INDEX_URL"] = "https://pypi.org/simple/"
        env["PIP_NO_INPUT"] = "1"
        result = self._run(
            [
                python,
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-input",
                "--index-url",
                env["PIP_INDEX_URL"],
                "--target",
                str(self.install_root),
                spec.name,
            ],
            env=env,
        )
        if result.returncode != 0:
            raise AcquisitionError(f"pip install failed: {result.stderr.strip()}")
        return AcquisitionResult(
            spec.name, "installed", spec.source, spec.executable, str(self.install_root), None
        )

    def _audit(self, result: AcquisitionResult) -> None:
        if self.audit_path:
            append_event(
                self.audit_path,
                {
                    "event": "capability_acquisition",
                    "tool": result.tool,
                    "status": result.status,
                    "source": result.source,
                    "executable": result.executable,
                    "path": result.path,
                    "sha256": result.sha256,
                    "network_gateway": self.internet_gateway is not None,
                },
            )
