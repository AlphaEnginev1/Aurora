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
from .transaction import AcquisitionTransaction, TransactionError, operation_key
from .snapshot import FilesystemSnapshot, SnapshotError
from .pypi_downloader import ArtifactDownloadError, PypiArtifactDownloader
from .pypi_resolver import PypiDependencyResolver, PypiResolutionError
from .sandbox import SandboxError, SandboxExecutor, SandboxPolicy


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
        transaction_dir: str | Path | None = None,
        artifact_cache_dir: str | Path | None = None,
        sandbox: SandboxExecutor | None = None,
        network_sandbox: SandboxExecutor | None = None,
    ) -> None:
        self.allowed_sources = allowed_sources
        self.audit_path = Path(audit_path) if audit_path else None
        self.install_root = Path(install_root) if install_root else None
        self.timeout = timeout
        self.internet_gateway = internet_gateway
        self.transaction_dir = Path(transaction_dir) if transaction_dir else (self.audit_path.parent / "transactions" if self.audit_path else Path(".aurora-transactions"))
        self.artifact_cache_dir = Path(artifact_cache_dir) if artifact_cache_dir else self.transaction_dir / "artifacts"
        self.sandbox = sandbox or SandboxExecutor(SandboxPolicy(network=False))
        self.network_sandbox = network_sandbox

    def plan(self, tool_spec: ToolSpec) -> dict[str, str]:
        if tool_spec.source not in self.allowed_sources:
            raise AcquisitionError(f"source is not allowed: {tool_spec.source}")
        if tool_spec.kind not in {"system", "runtime"}:
            raise AcquisitionError(f"unsupported acquisition kind: {tool_spec.kind}")
        if tool_spec.source == "pypi" and self.install_root is None:
            raise AcquisitionError("pypi acquisition requires an isolated install_root")
        self._authorize_source_network(tool_spec.source)
        return {"tool": tool_spec.name, "source": tool_spec.source, "action": "install"}

    def acquire(self, tool_spec: ToolSpec) -> AcquisitionResult:
        tx = self._transaction(tool_spec)
        with tx.lock():
            return self._acquire_locked(tool_spec)

    def _acquire_locked(self, tool_spec: ToolSpec) -> AcquisitionResult:
        self.plan(tool_spec)
        tx = self._transaction(tool_spec)
        try:
            previous = tx.begin()
        except TransactionError as exc:
            raise AcquisitionError(str(exc)) from exc

        if previous.state == "committed":
            existing = self._existing_result(tool_spec)
            if existing:
                self._audit(existing)
                return existing
            raise AcquisitionError(
                f"transaction {tx.transaction_id} is committed but its postcondition is absent"
            )

        existing = self._existing_result(tool_spec)
        if existing:
            tx.commit("postcondition already satisfied")
            self._audit(existing)
            return existing

        try:
            if tool_spec.source == "ubuntu-apt":
                result = self._apt(tool_spec)
            elif tool_spec.source == "pypi":
                result = self._pip(tool_spec, transaction_id=tx.transaction_id)
            else:
                raise AcquisitionError(
                    f"no safe installer implemented for source: {tool_spec.source}"
                )
            tx.commit("installer completed and postcondition verified")
        except AcquisitionError as exc:
            tx.require_recovery(str(exc))
            raise
        self._audit(result)
        return result

    def recover(self, tool_spec: ToolSpec) -> AcquisitionResult | None:
        """Recover an interrupted operation when its verified postcondition exists."""
        self.plan(tool_spec)
        tx = self._transaction(tool_spec)
        latest = tx.latest
        if not latest or latest.state not in {"running", "recovery_required"}:
            return None
        existing = self._existing_result(tool_spec)
        if not existing:
            raise AcquisitionError(
                f"transaction {tx.transaction_id} has no verified postcondition; manual cleanup/recovery is required"
            )
        tx.commit("recovery verified existing postcondition")
        self._audit(existing)
        return existing

    def _transaction(self, spec: ToolSpec) -> AcquisitionTransaction:
        key = operation_key(
            tool=spec.name,
            source=spec.source,
            version=spec.version,
            artifact_sha256=spec.artifact_sha256,
            target=str(self.install_root) if self.install_root else None,
        )
        return AcquisitionTransaction(
            journal_dir=self.transaction_dir,
            operation_key=key,
            tool=spec.name,
            source=spec.source,
            target=str(self.install_root) if self.install_root else None,
        )

    def _existing_result(self, spec: ToolSpec) -> AcquisitionResult | None:
        existing = shutil.which(spec.executable)
        if existing and spec.source == "ubuntu-apt":
            version = self._apt_version(spec.name)
            if spec.version and version != spec.version:
                return None
            return AcquisitionResult(
                spec.name, "already_available", spec.source, spec.executable,
                existing, sha256_file(existing) if os.path.isfile(existing) else None,
                version=version, verification="verified" if os.path.isfile(existing) else "observed",
            )
        if spec.source == "pypi" and self.install_root:
            try:
                provenance = self._existing_pip_provenance(spec)
            except (ProvenanceError, OSError):
                provenance = None
            if provenance:
                return AcquisitionResult(
                    spec.name, "already_available", spec.source, spec.executable,
                    str(self.install_root), None, version=provenance.version,
                    artifact_sha256=provenance.artifact_sha256,
                    provenance_url=provenance.provenance_url,
                    verification="verified",
                )
        if existing and not spec.version:
            return AcquisitionResult(
                spec.name, "already_available", spec.source, spec.executable,
                existing, sha256_file(existing) if os.path.isfile(existing) else None,
                verification="observed",
            )
        return None

    def _existing_pip_provenance(self, spec: ToolSpec) -> PackageProvenance | None:
        assert self.install_root is not None
        normalized = spec.name.lower().replace("-", "_")
        for metadata_path in self.install_root.rglob("*.dist-info/METADATA"):
            fields = {}
            metadata = metadata_path.read_text(encoding="utf-8", errors="strict")
            for line in metadata.splitlines():
                if ": " in line:
                    key, value = line.split(": ", 1)
                    fields[key.lower()] = value.strip()
            name = fields.get("name", "").lower().replace("-", "_")
            version = fields.get("version")
            if name != normalized or not version:
                continue
            if spec.version and version != spec.version:
                continue
            record_path = metadata_path.parent / "RECORD"
            verify_record(self.install_root, record_path)
            return PackageProvenance(
                name=fields["name"], version=version, source="pypi",
                artifact_sha256=spec.artifact_sha256,
                install_root=self.install_root,
                record_verified=True,
            )
        return None

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
            verification="verified" if os.path.isfile(path) else "observed",
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

    def _pip(self, spec: ToolSpec, *, transaction_id: str | None = None) -> AcquisitionResult:
        if not self.install_root:
            raise AcquisitionError("pypi acquisition requires an isolated install_root")
        self.install_root.parent.mkdir(parents=True, exist_ok=True)
        if self.internet_gateway is None:
            raise AcquisitionError(
                "pypi acquisition requires an Internet Gateway for artifact download"
            )
        python = "python3"

        downloader = PypiArtifactDownloader(
            self.internet_gateway,
            cache_dir=self.artifact_cache_dir,
        )

        # v2 path: one pre-verified wheel downloaded directly through the gateway.
        # v3 path: pip resolves the full dependency closure inside a networked
        # sandbox; every selected wheel is then verified against PyPI metadata and
        # cached before an offline sandbox performs the actual installation.
        if self.network_sandbox is None:
            try:
                artifact = downloader.resolve(spec.name, spec.version)
                if spec.artifact_sha256 and artifact.sha256.lower() != spec.artifact_sha256.lower():
                    raise AcquisitionError(
                        f"artifact sha256 mismatch: expected {spec.artifact_sha256}, got {artifact.sha256}"
                    )
                artifact_path = downloader.download(artifact)
                resolved_wheels = ((artifact, artifact_path),)
                root_artifact = artifact
            except ArtifactDownloadError as exc:
                raise AcquisitionError(f"PyPI artifact acquisition failed: {exc}") from exc
        else:
            try:
                resolver = PypiDependencyResolver(downloader, self.network_sandbox)
                resolution = resolver.resolve_and_download(spec.name, spec.version)
            except (PypiResolutionError, SandboxError) as exc:
                raise AcquisitionError(f"PyPI dependency acquisition failed: {exc}") from exc
            root_artifact = resolution.root.artifact
            if (
                spec.artifact_sha256
                and root_artifact.sha256.lower() != spec.artifact_sha256.lower()
            ):
                raise AcquisitionError(
                    f"artifact sha256 mismatch: expected {spec.artifact_sha256}, got {root_artifact.sha256}"
                )
            resolved_wheels = tuple(
                (item.artifact, item.cached_path) for item in resolution.wheels
            )

        snapshot = FilesystemSnapshot(
            target=self.install_root,
            snapshot_root=self.transaction_dir / "snapshots",
        )
        try:
            snapshot_state = snapshot.create(transaction_id=transaction_id)
            staged_root = snapshot.stage_directory(snapshot_state)
        except SnapshotError as exc:
            raise AcquisitionError(f"filesystem staging preparation failed: {exc}") from exc

        workspace = (
            self.transaction_dir / "sandbox-work" /
            (transaction_id or root_artifact.sha256[:24])
        )
        if workspace.exists():
            shutil.rmtree(workspace)
        wheels = workspace / "wheels"
        # The managed runtime is mounted at this stable logical path when tools execute.
        installed = workspace / "managed-site"
        workspace.mkdir(parents=True, exist_ok=False)
        wheels.mkdir()
        installed.mkdir()
        for artifact, cached_path in resolved_wheels:
            shutil.copy2(cached_path, wheels / artifact.filename)

        committed = False
        try:
            venv_result = self.sandbox.execute(
                [python, "-m", "venv", "/workspace/managed-site"],
                workspace=workspace,
            )
            if venv_result.returncode != 0:
                snapshot.rollback(snapshot_state)
                detail = venv_result.stderr.strip() or venv_result.stdout.strip()
                raise AcquisitionError(
                    f"sandboxed Python environment creation failed: "
                    f"{detail or 'unknown venv error'}"
                )

            sandbox_result = self.sandbox.execute(
                [
                    "/workspace/managed-site/bin/python",
                    "-m",
                    "pip",
                    "install",
                    "--disable-pip-version-check",
                    "--no-input",
                    "--no-index",
                    "--only-binary=:all:",
                    "--find-links",
                    "/workspace/wheels",
                    "--report",
                    "/workspace/pip-report.json",
                    spec.name if spec.version is None else f"{spec.name}=={spec.version}",
                ],
                workspace=workspace,
            )
            if sandbox_result.returncode != 0:
                snapshot.rollback(snapshot_state)
                detail = sandbox_result.stderr.strip() or sandbox_result.stdout.strip()
                raise AcquisitionError(
                    f"sandboxed pip install failed: {detail or 'unknown installer error'}"
                )

            try:
                record_count = self._verify_all_installed_distributions(
                    installed,
                    expected_root=(root_artifact.name, root_artifact.version),
                )
                if record_count <= 0:
                    raise ProvenanceError("installed distribution RECORD set was not verified")
            except (ProvenanceError, OSError, ValueError) as exc:
                snapshot.rollback(snapshot_state)
                raise AcquisitionError(
                    f"package provenance verification failed: {exc}"
                ) from exc

            try:
                shutil.copytree(installed, staged_root, dirs_exist_ok=True)
                snapshot.commit(snapshot_state, staged_root)
                committed = True
            except (OSError, SnapshotError) as exc:
                raise AcquisitionError(f"filesystem commit failed: {exc}") from exc

            return AcquisitionResult(
                spec.name,
                "installed",
                spec.source,
                spec.executable,
                str(self.install_root),
                None,
                version=root_artifact.version,
                artifact_sha256=root_artifact.sha256,
                provenance_url=root_artifact.url,
                verification="verified",
            )
        except AcquisitionError:
            raise
        finally:
            if not committed and staged_root.exists():
                shutil.rmtree(staged_root, ignore_errors=True)
            shutil.rmtree(workspace, ignore_errors=True)
            if committed:
                try:
                    snapshot.discard(snapshot_state)
                except OSError:
                    pass

    def _verify_all_installed_distributions(
        self,
        install_root: Path,
        *,
        expected_root: tuple[str, str],
    ) -> int:
        metadata_paths = sorted(install_root.rglob("*.dist-info/METADATA"))
        if not metadata_paths:
            raise ProvenanceError("no installed distribution metadata found")

        expected_name = expected_root[0].lower().replace("-", "_")
        expected_version = expected_root[1]
        root_found = False
        checked = 0
        for metadata_path in metadata_paths:
            metadata = metadata_path.read_text(encoding="utf-8", errors="strict")
            fields: dict[str, str] = {}
            for line in metadata.splitlines():
                if ": " in line:
                    key, value = line.split(": ", 1)
                    fields[key.lower()] = value.strip()
            name = fields.get("name")
            version = fields.get("version")
            if not name or not version:
                raise ProvenanceError(
                    f"installed distribution metadata is incomplete: {metadata_path}"
                )
            if name.lower().replace("-", "_") == expected_name and version == expected_version:
                root_found = True
            checked += verify_record(install_root, metadata_path.parent / "RECORD")

        if not root_found:
            raise ProvenanceError(
                f"root distribution was not installed: {expected_root[0]}=={expected_root[1]}"
            )
        return checked

    def _verify_installed_distribution_at(
        self, install_root: Path, name: str, version: str
    ) -> int:
        normalized = name.lower().replace("-", "_")
        for metadata_path in install_root.rglob("*.dist-info/METADATA"):
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
                return verify_record(install_root, metadata_path.parent / "RECORD")
        raise ProvenanceError(
            f"installed distribution metadata not found: {name}=={version}"
        )

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
        for metadata_path in self.install_root.rglob("*.dist-info/METADATA"):
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

    def _existing_pip_provenance_at(
        self, spec: ToolSpec, install_root: Path
    ) -> PackageProvenance | None:
        normalized = spec.name.lower().replace("-", "_")
        for metadata_path in install_root.rglob("*.dist-info/METADATA"):
            fields = {}
            metadata = metadata_path.read_text(encoding="utf-8", errors="strict")
            for line in metadata.splitlines():
                if ": " in line:
                    key, value = line.split(": ", 1)
                    fields[key.lower()] = value.strip()
            name = fields.get("name", "").lower().replace("-", "_")
            version = fields.get("version")
            if name != normalized or not version:
                continue
            if spec.version and version != spec.version:
                continue
            verify_record(install_root, metadata_path.parent / "RECORD")
            return PackageProvenance(
                name=fields["name"], version=version, source="pypi",
                artifact_sha256=spec.artifact_sha256,
                install_root=install_root,
                record_verified=True,
            )
        return None


