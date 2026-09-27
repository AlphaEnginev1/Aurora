"""Controlled PyPI artifact resolution and download for Aurora acquisition."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import tempfile
from urllib.parse import quote, urlparse

from .internet import InternetGateway, InternetPolicyError


class ArtifactDownloadError(RuntimeError):
    """Raised when a PyPI artifact cannot be resolved or verified."""


@dataclass(frozen=True)
class PypiArtifact:
    name: str
    version: str
    filename: str
    url: str
    sha256: str


class PypiArtifactDownloader:
    """Resolve and download pinned PyPI artifacts through the InternetGateway."""

    _NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    _VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+!_-]{0,127}$")
    _SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
    _ALLOWED_HOST = "files.pythonhosted.org"

    def __init__(
        self,
        gateway: InternetGateway,
        *,
        cache_dir: str | Path,
        max_artifact_bytes: int = 100 * 1024 * 1024,
    ) -> None:
        self.gateway = gateway
        self.cache_dir = Path(cache_dir).resolve()
        self.max_artifact_bytes = max_artifact_bytes

    def _release_payload(self, name: str, version: str | None = None) -> dict:
        self._validate_name(name)
        if version is not None:
            self._validate_version(version)
            endpoint = f"https://pypi.org/pypi/{quote(name)}/{quote(version)}/json"
        else:
            endpoint = f"https://pypi.org/pypi/{quote(name)}/json"
        try:
            payload = json.loads(self.gateway.get(endpoint).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, InternetPolicyError) as exc:
            raise ArtifactDownloadError(f"PyPI metadata request failed for {name}") from exc
        if not isinstance(payload, dict):
            raise ArtifactDownloadError("PyPI metadata response is not an object")
        return payload

    def resolve(self, name: str, version: str | None = None) -> PypiArtifact:
        payload = self._release_payload(name, version)
        resolved_version = version or payload.get("info", {}).get("version")
        if not isinstance(resolved_version, str) or not resolved_version:
            raise ArtifactDownloadError(f"PyPI metadata did not provide a version for {name}")

        files = payload.get("urls")
        if not isinstance(files, list):
            raise ArtifactDownloadError(f"PyPI metadata has no release files for {name}")

        candidates: list[tuple[int, PypiArtifact]] = []
        for item in files:
            if not isinstance(item, dict):
                continue
            filename = item.get("filename")
            if not isinstance(filename, str) or not filename.endswith(".whl"):
                continue
            if not self._safe_filename(filename) or item.get("yanked"):
                continue
            try:
                artifact = self._artifact_from_item(name, resolved_version, item)
            except ArtifactDownloadError:
                continue
            priority = (
                3
                if filename.endswith("-py3-none-any.whl")
                else 2
                if "-none-any.whl" in filename
                else 1
            )
            candidates.append((priority, artifact))

        if not candidates:
            raise ArtifactDownloadError(
                f"no compatible wheel artifact found for {name}=={resolved_version}"
            )
        candidates.sort(key=lambda pair: pair[0], reverse=True)
        return candidates[0][1]

    def resolve_exact(
        self,
        name: str,
        version: str,
        filename: str,
    ) -> PypiArtifact:
        self._validate_name(name)
        self._validate_version(version)
        if not self._safe_filename(filename):
            raise ArtifactDownloadError("unsafe PyPI artifact filename")
        payload = self._release_payload(name, version)
        files = payload.get("urls")
        if not isinstance(files, list):
            raise ArtifactDownloadError(f"PyPI metadata has no release files for {name}")
        for item in files:
            if not isinstance(item, dict) or item.get("filename") != filename:
                continue
            if item.get("yanked"):
                raise ArtifactDownloadError(f"PyPI artifact is yanked: {filename}")
            return self._artifact_from_item(name, version, item)
        raise ArtifactDownloadError(
            f"PyPI release does not contain the requested artifact: {filename}"
        )

    def download(self, artifact: PypiArtifact) -> Path:
        self._validate_artifact(artifact)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        target = self.cache_dir / artifact.sha256.lower() / artifact.filename
        if target.is_file():
            if self._sha256(target) == artifact.sha256.lower():
                return target
            target.unlink(missing_ok=True)

        try:
            data = self.gateway.get(artifact.url)
        except InternetPolicyError as exc:
            raise ArtifactDownloadError(
                "PyPI artifact download blocked by Internet Gateway"
            ) from exc

        if len(data) > self.max_artifact_bytes:
            raise ArtifactDownloadError("PyPI artifact exceeds the configured size limit")
        actual = hashlib.sha256(data).hexdigest()
        if actual != artifact.sha256.lower():
            raise ArtifactDownloadError(
                f"PyPI artifact sha256 mismatch: expected {artifact.sha256}, got {actual}"
            )

        target.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_copy_bytes(target, data)
        return target

    def cache_file(self, artifact: PypiArtifact, source: str | Path) -> Path:
        """Verify a previously downloaded artifact and cache it by its SHA-256."""
        self._validate_artifact(artifact)
        source_path = Path(source)
        if not source_path.is_file():
            raise ArtifactDownloadError(
                f"artifact source is not a regular file: {source_path}"
            )
        try:
            size = source_path.stat().st_size
        except OSError as exc:
            raise ArtifactDownloadError(f"cannot stat artifact: {source_path}") from exc
        if size > self.max_artifact_bytes:
            raise ArtifactDownloadError("PyPI artifact exceeds the configured size limit")
        actual = self._sha256(source_path)
        if actual != artifact.sha256.lower():
            raise ArtifactDownloadError(
                f"PyPI artifact sha256 mismatch: expected {artifact.sha256}, got {actual}"
            )

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        target = self.cache_dir / artifact.sha256.lower() / artifact.filename
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file() and self._sha256(target) == artifact.sha256.lower():
            return target

        fd, tmp_name = tempfile.mkstemp(
            prefix=".aurora-artifact-", dir=target.parent
        )
        try:
            with open(fd, "wb", closefd=True) as handle, source_path.open("rb") as source_handle:
                for chunk in iter(lambda: source_handle.read(1024 * 1024), b""):
                    handle.write(chunk)
                handle.flush()
            Path(tmp_name).replace(target)
        finally:
            Path(tmp_name).unlink(missing_ok=True)
        return target

    def _artifact_from_item(
        self,
        name: str,
        version: str,
        item: dict,
    ) -> PypiArtifact:
        filename = item.get("filename")
        url = item.get("url")
        digests = item.get("digests") or {}
        sha256 = digests.get("sha256")
        if not isinstance(filename, str) or not self._safe_filename(filename):
            raise ArtifactDownloadError("PyPI artifact filename is invalid")
        if not isinstance(url, str):
            raise ArtifactDownloadError("PyPI artifact URL is missing")
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != self._ALLOWED_HOST:
            raise ArtifactDownloadError("PyPI artifact URL is outside the trusted host")
        if parsed.username or parsed.password:
            raise ArtifactDownloadError("embedded URL credentials are not allowed")
        if not isinstance(sha256, str) or not self._SHA256_RE.fullmatch(sha256):
            raise ArtifactDownloadError("PyPI artifact sha256 is invalid")
        return PypiArtifact(
            name=name,
            version=version,
            filename=filename,
            url=url,
            sha256=sha256,
        )

    def _validate_artifact(self, artifact: PypiArtifact) -> None:
        if not isinstance(artifact.sha256, str) or not self._SHA256_RE.fullmatch(artifact.sha256):
            raise ArtifactDownloadError(
                "PyPI artifact sha256 must be 64 hexadecimal characters"
            )
        if not self._safe_filename(artifact.filename):
            raise ArtifactDownloadError("unsafe PyPI artifact filename")
        parsed = urlparse(artifact.url)
        if parsed.scheme != "https" or parsed.hostname != self._ALLOWED_HOST:
            raise ArtifactDownloadError("PyPI artifact URL is outside the trusted host")
        if parsed.username or parsed.password:
            raise ArtifactDownloadError("embedded URL credentials are not allowed")

    @staticmethod
    def _safe_filename(filename: str) -> bool:
        return (
            bool(filename)
            and Path(filename).name == filename
            and "/" not in filename
            and "\\" not in filename
            and filename not in {".", ".."}
        )

    @staticmethod
    def _atomic_copy_bytes(target: Path, data: bytes) -> None:
        fd, tmp_name = tempfile.mkstemp(
            prefix=".aurora-artifact-", dir=target.parent
        )
        try:
            with open(fd, "wb", closefd=True) as handle:
                handle.write(data)
                handle.flush()
            Path(tmp_name).replace(target)
        finally:
            Path(tmp_name).unlink(missing_ok=True)

    @classmethod
    def _validate_name(cls, name: str) -> None:
        if not isinstance(name, str) or not cls._NAME_RE.fullmatch(name):
            raise ArtifactDownloadError("invalid PyPI package name")

    @classmethod
    def _validate_version(cls, version: str) -> None:
        if not isinstance(version, str) or not cls._VERSION_RE.fullmatch(version):
            raise ArtifactDownloadError("invalid PyPI package version")

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
