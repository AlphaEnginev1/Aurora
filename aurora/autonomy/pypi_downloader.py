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
    """Resolve and download a pinned PyPI artifact through the InternetGateway."""

    _NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    _VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+!_-]{0,127}$")
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

    def resolve(self, name: str, version: str | None = None) -> PypiArtifact:
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

        resolved_version = version or payload.get("info", {}).get("version")
        if not isinstance(resolved_version, str) or not resolved_version:
            raise ArtifactDownloadError(f"PyPI metadata did not provide a version for {name}")

        files = payload.get("urls")
        if not isinstance(files, list):
            raise ArtifactDownloadError(f"PyPI metadata has no release files for {name}")

        candidates: list[tuple[int, dict]] = []
        for item in files:
            if not isinstance(item, dict):
                continue
            filename = item.get("filename")
            url = item.get("url")
            digests = item.get("digests") or {}
            sha256 = digests.get("sha256")
            if not isinstance(filename, str) or not filename.endswith(".whl"):
                continue
            if not isinstance(url, str) or not isinstance(sha256, str):
                continue
            parsed = urlparse(url)
            if parsed.scheme != "https" or parsed.hostname != self._ALLOWED_HOST:
                continue
            if item.get("yanked"):
                continue
            # Prefer universal wheels first, then other wheels supported by the
            # current Python runtime. A non-universal wheel is still accepted
            # when PyPI marks it as the release artifact; pip performs the
            # final compatibility check during offline installation.
            priority = 0
            if filename.endswith("-py3-none-any.whl"):
                priority = 3
            elif "-none-any.whl" in filename:
                priority = 2
            else:
                priority = 1
            candidates.append((priority, item))

        if not candidates:
            raise ArtifactDownloadError(
                f"no compatible wheel artifact found for {name}=={resolved_version}"
            )
        candidates.sort(key=lambda pair: pair[0], reverse=True)
        item = candidates[0][1]
        return PypiArtifact(
            name=name,
            version=resolved_version,
            filename=item["filename"],
            url=item["url"],
            sha256=item["digests"]["sha256"],
        )

    def download(self, artifact: PypiArtifact) -> Path:
        if artifact.sha256 and len(artifact.sha256) != 64:
            raise ArtifactDownloadError("PyPI artifact sha256 must be 64 hex characters")
        try:
            expected = bytes.fromhex(artifact.sha256)
        except ValueError as exc:
            raise ArtifactDownloadError("PyPI artifact sha256 is not hexadecimal") from exc
        if len(expected) != 32:
            raise ArtifactDownloadError("PyPI artifact sha256 must be 32 bytes")

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        target = self.cache_dir / artifact.sha256.lower() / artifact.filename
        if target.is_file():
            if self._sha256(target) == artifact.sha256.lower():
                return target
            target.unlink(missing_ok=True)

        parsed = urlparse(artifact.url)
        if parsed.scheme != "https" or parsed.hostname != self._ALLOWED_HOST:
            raise ArtifactDownloadError("PyPI artifact URL is outside the trusted host")
        try:
            self.gateway.authorize(artifact.url)
            data = self.gateway.get(artifact.url)
        except InternetPolicyError as exc:
            raise ArtifactDownloadError("PyPI artifact download blocked by Internet Gateway") from exc

        if len(data) > self.max_artifact_bytes:
            raise ArtifactDownloadError("PyPI artifact exceeds the configured size limit")
        actual = hashlib.sha256(data).hexdigest()
        if actual != artifact.sha256.lower():
            raise ArtifactDownloadError(
                f"PyPI artifact sha256 mismatch: expected {artifact.sha256}, got {actual}"
            )

        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".aurora-artifact-", dir=target.parent)
        try:
            with open(fd, "wb", closefd=True) as handle:
                handle.write(data)
                handle.flush()
            Path(tmp_name).replace(target)
        finally:
            Path(tmp_name).unlink(missing_ok=True)
        return target

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
