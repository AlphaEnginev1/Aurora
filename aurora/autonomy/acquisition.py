"""Policy-controlled, non-interactive capability acquisition."""

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Sequence

from .audit import append_event
from .internet import InternetGateway, InternetPolicyError
from .provenance import (
    PackageProvenance,
    ProvenanceError,
    load_pip_report,
    sha256_file,
    verify_record,
)
from .registry import ToolSpec


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
    version: str | None = None
    artifact_sha256: str | None = None
    provenance_url: str | None = None
    verification: str = "unknown"


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
                verification="observed",
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

    def _run(
        self, args: Sequence[str], *, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
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
        version = self._apt_version(spec.name)
        return AcquisitionResult(
            spec.name,
            "installed",
            spec.source,
            spec.executable,
            path,
            sha256_file(path) if os.path.isfile(path) else None,
            version=version,
            verification="verified" if version else "observed",
        )

    def _apt_version(self, package: str) -> str | None:
        result = self._run(
            ["dpkg-query", "-W", "-f=${Status}\t${Version}", package]
        )
        if result.returncode != 0:
            return None
        parts = result.stdout.strip().split("\t", 1)
        if len(parts) != 2 or parts[0] != "install ok installed":
            return None
        return parts[1] or None

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
        report_file = tempfile.NamedTemporaryFile(
            prefix="aurora-pip-", suffix=".json", delete=False
        )
        report_path = Path(report_file.name)
        report_file.close()
        try:
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
                    "--report",
                    str(report_path),
                    "--target",
                    str(self.install_root),
                    spec.name,
                ],
                env=env,
            )
            if result.returncode != 0:
                raise AcquisitionError(f"pip install failed: {result.stderr.strip()}")
            try:
                provenance = load_pip_report(report_path)
                self._validate_expected(spec, provenance)
                record_count = self._verify_installed_distribution(
                    provenance.name, provenance.version
                )
            except (ProvenanceError, OSError, ValueError) as exc:
                raise AcquisitionError(
                    f"package provenance verification failed: {exc}"
                ) from exc
            return AcquisitionResult(
                spec.name,
                "installed",
                spec.source,
                spec.executable,
                str(self.install_root),
                None,
                version=provenance.version,
                artifact_sha256=provenance.artifact_sha256,
                provenance_url=provenance.provenance_url,
                verification="verified" if record_count > 0 else "observed",
            )
        finally:
            report_path.unlink(missing_ok=True)

    def _validate_expected(self, spec: ToolSpec, provenance: PackageProvenance) -> None:
        if provenance.name.lower().replace("-", "_") != spec.name.lower().replace("-", "_"):
            raise ProvenanceError(
                f"resolved package name mismatch: expected {spec.name}, got {provenance.name}"
            )
        if spec.version and provenance.version != spec.version:
            raise ProvenanceError(
                f"version mismatch: expected {spec.version}, got {provenance.version}"
            )
        if spec.artifact_sha256:
            if not provenance.artifact_sha256:
                raise ProvenanceError("expected artifact sha256 was not supplied by pip")
            if provenance.artifact_sha256.lower() != spec.artifact_sha256.lower():
                raise ProvenanceError(
                    f"artifact sha256 mismatch: expected {spec.artifact_sha256}, got {provenance.artifact_sha256}"
                )

    def _verify_installed_distribution(self, name: str, version: str) -> int:
        assert self.install_root is not None
        normalized = name.lower().replace("-", "_")
        for metadata_path in self.install_root.glob("*.dist-info/METADATA"):
            metadata = metadata_path.read_text(encoding="utf-8", errors="strict")
            fields = {}
            for line in metadata.splitlines():
                if ": " in line:
                    key, value = line.split(": ", 1)
                    fields[key.lower()] = value.strip()
            if (
                fields.get("name", "").lower().replace("-", "_") == normalized
                and fields.get("version") == version
            ):
                record_path = metadata_path.parent / "RECORD"
                return verify_record(self.install_root, record_path)
        raise ProvenanceError(
            f"installed distribution metadata not found: {name}=={version}"
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
                    "version": result.version,
                    "artifact_sha256": result.artifact_sha256,
                    "provenance_url": result.provenance_url,
                    "verification": result.verification,
                    "network_gateway": self.internet_gateway is not None,
                },
            )
