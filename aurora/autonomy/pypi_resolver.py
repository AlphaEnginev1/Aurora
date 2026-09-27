"""Full PyPI dependency-closure resolution under Aurora's network sandbox."""

from __future__ import annotations

from dataclasses import dataclass
from email.parser import Parser
from pathlib import Path
import tempfile
import zipfile

from .pypi_downloader import (
    ArtifactDownloadError,
    PypiArtifact,
    PypiArtifactDownloader,
)
from .sandbox import SandboxError, SandboxExecutor


class PypiResolutionError(RuntimeError):
    """Raised when a PyPI dependency closure cannot be safely materialized."""


@dataclass(frozen=True)
class ResolvedWheel:
    artifact: PypiArtifact
    cached_path: Path


@dataclass(frozen=True)
class PypiResolution:
    root: ResolvedWheel
    wheels: tuple[ResolvedWheel, ...]


class PypiDependencyResolver:
    """Use pip's resolver only for planning, then verify every wheel against PyPI."""

    def __init__(
        self,
        downloader: PypiArtifactDownloader,
        network_sandbox: SandboxExecutor,
        *,
        max_artifacts: int = 128,
        max_total_bytes: int = 500 * 1024 * 1024,
    ) -> None:
        self.downloader = downloader
        self.network_sandbox = network_sandbox
        self.max_artifacts = max_artifacts
        self.max_total_bytes = max_total_bytes

        if not network_sandbox.policy.network:
            raise PypiResolutionError(
                "dependency resolution requires a network-enabled controlled sandbox"
            )

    def resolve_and_download(
        self,
        name: str,
        version: str | None = None,
        *,
        workspace: str | Path | None = None,
    ) -> PypiResolution:
        if workspace is None:
            with tempfile.TemporaryDirectory(prefix="aurora-pypi-resolve-") as temp:
                return self._resolve_in_workspace(
                    name,
                    version,
                    Path(temp),
                )

        root = Path(workspace).resolve()
        root.mkdir(parents=True, exist_ok=True)
        return self._resolve_in_workspace(name, version, root)

    def _resolve_in_workspace(
        self,
        name: str,
        version: str | None,
        root: Path,
    ) -> PypiResolution:
        downloads = root / "downloads"
        downloads.mkdir(parents=True, exist_ok=True)

        requested = name if version is None else f"{name}=={version}"
        result = self.network_sandbox.execute(
            [
                "python3",
                "-m",
                "pip",
                "download",
                "--disable-pip-version-check",
                "--no-input",
                "--no-cache-dir",
                "--only-binary=:all:",
                "--progress-bar",
                "off",
                "--index-url",
                "https://pypi.org/simple/",
                "--dest",
                "/workspace/downloads",
                requested,
            ],
            workspace=root,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise PypiResolutionError(
                f"controlled PyPI dependency resolution failed: "
                f"{detail or 'unknown resolver error'}"
            )

        wheel_files = sorted(downloads.glob("*.whl"))
        if not wheel_files:
            raise PypiResolutionError("pip resolver produced no wheel artifacts")
        if len(wheel_files) > self.max_artifacts:
            raise PypiResolutionError(
                f"dependency closure exceeds artifact limit ({self.max_artifacts})"
            )

        total_bytes = sum(path.stat().st_size for path in wheel_files)
        if total_bytes > self.max_total_bytes:
            raise PypiResolutionError(
                f"dependency closure exceeds byte limit ({self.max_total_bytes})"
            )

        resolved: list[ResolvedWheel] = []
        seen: dict[str, str] = {}
        for wheel_path in wheel_files:
            package_name, package_version = self._wheel_identity(wheel_path)
            normalized = package_name.lower().replace("-", "_")
            prior = seen.get(normalized)
            if prior is not None and prior != package_version:
                raise PypiResolutionError(
                    f"resolver produced multiple versions of {package_name}: "
                    f"{prior} and {package_version}"
                )
            seen[normalized] = package_version

            try:
                artifact = self.downloader.resolve_exact(
                    package_name,
                    package_version,
                    wheel_path.name,
                )
                cached = self.downloader.cache_file(artifact, wheel_path)
            except ArtifactDownloadError as exc:
                raise PypiResolutionError(
                    f"PyPI verification failed for {wheel_path.name}: {exc}"
                ) from exc
            resolved.append(ResolvedWheel(artifact=artifact, cached_path=cached))

        root_normalized = name.lower().replace("-", "_")
        candidates = [
            item
            for item in resolved
            if item.artifact.name.lower().replace("-", "_") == root_normalized
            and (version is None or item.artifact.version == version)
        ]
        if len(candidates) != 1:
            raise PypiResolutionError(
                f"root package was not uniquely resolved: {name}"
                + (f"=={version}" if version else "")
            )

        return PypiResolution(
            root=candidates[0],
            wheels=tuple(sorted(resolved, key=lambda item: item.artifact.filename)),
        )

    @staticmethod
    def _wheel_identity(path: Path) -> tuple[str, str]:
        try:
            with zipfile.ZipFile(path) as archive:
                metadata_names = [
                    item
                    for item in archive.namelist()
                    if item.endswith(".dist-info/METADATA")
                ]
                if len(metadata_names) != 1:
                    raise PypiResolutionError(
                        f"wheel has invalid METADATA layout: {path.name}"
                    )
                raw = archive.read(metadata_names[0])
        except (OSError, zipfile.BadZipFile, KeyError) as exc:
            raise PypiResolutionError(f"cannot inspect wheel: {path.name}") from exc

        metadata = Parser().parsestr(raw.decode("utf-8", errors="strict"))
        package_name = metadata.get("Name")
        package_version = metadata.get("Version")
        if not package_name or not package_version:
            raise PypiResolutionError(
                f"wheel metadata missing Name/Version: {path.name}"
            )
        return package_name, package_version
