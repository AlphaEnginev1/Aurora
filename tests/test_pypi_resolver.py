import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from aurora.autonomy.pypi_downloader import PypiArtifactDownloader
from aurora.autonomy.pypi_resolver import PypiDependencyResolver, PypiResolutionError
from aurora.autonomy.sandbox import SandboxPolicy, SandboxResult


class FakeGateway:
    def __init__(self, responses):
        self.responses = responses
        self.authorized = []

    def authorize(self, url):
        self.authorized.append(url)

    def get(self, url):
        self.authorized.append(url)
        value = self.responses[url]
        return value.encode() if isinstance(value, str) else value


class FakeNetworkSandbox:
    policy = SandboxPolicy(network=True)

    def __init__(self, wheel_factory):
        self.wheel_factory = wheel_factory

    def execute(self, command, *, workspace):
        assert "--index-url" in command
        assert "https://pypi.org/simple/" in command
        downloads = Path(workspace) / "downloads"
        downloads.mkdir(parents=True, exist_ok=True)
        self.wheel_factory(downloads)
        return SandboxResult(tuple(command), 0, "", "")


def _wheel(directory: Path, name: str, version: str, *, body: bytes = b"aurora") -> tuple[str, bytes]:
    filename = f"{name}-{version}-py3-none-any.whl"
    metadata = (
        f"Metadata-Version: 2.4\nName: {name}\nVersion: {version}\n\n"
    ).encode()
    target = directory / filename
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr(
            f"{name}-{version}.dist-info/METADATA",
            metadata,
        )
        archive.writestr(f"{name}.py", body)
    return filename, target.read_bytes()


def test_resolver_verifies_and_caches_every_selected_wheel(tmp_path):
    wheels = {}

    def make(downloads):
        filename, data = _wheel(downloads, "demo", "1.0.0")
        wheels[filename] = data

    filename = "demo-1.0.0-py3-none-any.whl"
    metadata_url = "https://pypi.org/pypi/demo/1.0.0/json"
    artifact_url = f"https://files.pythonhosted.org/packages/{filename}"
    digest_holder = tmp_path / "digest"
    digest_holder.write_bytes(b"placeholder")

    # The fake sandbox must create the exact bytes advertised by the PyPI
    # metadata. Build once up front so the JSON can contain its real hash.
    prebuilt = tmp_path / filename
    _, data = _wheel(tmp_path, "demo", "1.0.0")
    prebuilt_digest = hashlib.sha256(data).hexdigest()
    payload = {
        "info": {"name": "demo", "version": "1.0.0"},
        "urls": [{
            "filename": filename,
            "url": artifact_url,
            "digests": {"sha256": prebuilt_digest},
            "yanked": False,
        }],
    }

    def create(downloads):
        _wheel(downloads, "demo", "1.0.0")

    gateway = FakeGateway({metadata_url: json.dumps(payload)})
    downloader = PypiArtifactDownloader(gateway, cache_dir=tmp_path / "cache")
    resolver = PypiDependencyResolver(
        downloader,
        FakeNetworkSandbox(create),
    )
    result = resolver.resolve_and_download(
        "demo",
        "1.0.0",
        workspace=tmp_path / "resolve",
    )

    assert result.root.artifact.filename == filename
    assert len(result.wheels) == 1
    assert result.root.cached_path.is_file()
    assert result.root.cached_path.read_bytes() == data
    assert metadata_url in gateway.authorized


def test_resolver_rejects_a_wheel_not_attested_by_pypi(tmp_path):
    filename = "evil-1.0.0-py3-none-any.whl"
    metadata_url = "https://pypi.org/pypi/evil/1.0.0/json"

    def create(downloads):
        _wheel(downloads, "evil", "1.0.0")

    gateway = FakeGateway({
        metadata_url: json.dumps({
            "info": {"name": "evil", "version": "1.0.0"},
            "urls": [],
        })
    })
    resolver = PypiDependencyResolver(
        PypiArtifactDownloader(gateway, cache_dir=tmp_path / "cache"),
        FakeNetworkSandbox(create),
    )
    with pytest.raises(PypiResolutionError, match="PyPI verification failed"):
        resolver.resolve_and_download("evil", "1.0.0", workspace=tmp_path / "resolve")


def test_resolver_requires_network_sandbox(tmp_path):
    gateway = FakeGateway({})
    downloader = PypiArtifactDownloader(gateway, cache_dir=tmp_path / "cache")
    with pytest.raises(PypiResolutionError, match="network-enabled"):
        PypiDependencyResolver(
            downloader,
            type(
                "OfflineSandbox",
                (),
                {"policy": SandboxPolicy(network=False)},
            )(),
        )
