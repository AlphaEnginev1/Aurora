import hashlib
import json

import pytest

from aurora.autonomy.pypi_downloader import (
    ArtifactDownloadError,
    PypiArtifactDownloader,
)


class FakeGateway:
    def __init__(self, responses):
        self.responses = responses
        self.authorized = []

    def authorize(self, url):
        self.authorized.append(url)

    def get(self, url):
        self.authorized.append(url)
        value = self.responses[url]
        if isinstance(value, str):
            return value.encode()
        return value


def test_resolve_prefers_universal_wheel():
    payload = {
        "info": {"name": "demo", "version": "1.0.0"},
        "urls": [
            {
                "filename": "demo-1.0.0.tar.gz",
                "url": "https://files.pythonhosted.org/demo.tar.gz",
                "digests": {"sha256": "0" * 64},
                "yanked": False,
            },
            {
                "filename": "demo-1.0.0-cp312-cp312-manylinux.whl",
                "url": "https://files.pythonhosted.org/demo-platform.whl",
                "digests": {"sha256": "1" * 64},
                "yanked": False,
            },
            {
                "filename": "demo-1.0.0-py3-none-any.whl",
                "url": "https://files.pythonhosted.org/demo-universal.whl",
                "digests": {"sha256": "2" * 64},
                "yanked": False,
            },
        ],
    }
    metadata_url = "https://pypi.org/pypi/demo/1.0.0/json"
    gateway = FakeGateway({metadata_url: json.dumps(payload)})
    artifact = PypiArtifactDownloader(
        gateway,
        cache_dir="/tmp/aurora-test-cache",
    ).resolve("demo", "1.0.0")
    assert artifact.filename.endswith("py3-none-any.whl")
    assert artifact.sha256 == "2" * 64
    assert metadata_url in gateway.authorized


def test_download_verifies_sha256_and_caches(tmp_path):
    data = b"aurora-wheel"
    digest = hashlib.sha256(data).hexdigest()
    url = "https://files.pythonhosted.org/packages/demo.whl"
    gateway = FakeGateway({url: data})
    downloader = PypiArtifactDownloader(gateway, cache_dir=tmp_path / "cache")
    artifact = type(
        "Artifact",
        (),
        {
            "name": "demo",
            "version": "1.0.0",
            "filename": "demo-1.0.0-py3-none-any.whl",
            "url": url,
            "sha256": digest,
        },
    )()
    first = downloader.download(artifact)
    second = downloader.download(artifact)
    assert first == second
    assert first.read_bytes() == data


def test_download_rejects_hash_mismatch(tmp_path):
    url = "https://files.pythonhosted.org/packages/demo.whl"
    gateway = FakeGateway({url: b"tampered"})
    downloader = PypiArtifactDownloader(gateway, cache_dir=tmp_path / "cache")
    artifact = type(
        "Artifact",
        (),
        {
            "name": "demo",
            "version": "1.0.0",
            "filename": "demo-1.0.0-py3-none-any.whl",
            "url": url,
            "sha256": "0" * 64,
        },
    )()
    with pytest.raises(ArtifactDownloadError, match="sha256 mismatch"):
        downloader.download(artifact)


def test_resolve_rejects_untrusted_artifact_host():
    payload = {
        "info": {"name": "demo", "version": "1.0.0"},
        "urls": [
            {
                "filename": "demo-1.0.0-py3-none-any.whl",
                "url": "https://evil.example/demo.whl",
                "digests": {"sha256": "2" * 64},
                "yanked": False,
            }
        ],
    }
    metadata_url = "https://pypi.org/pypi/demo/1.0.0/json"
    gateway = FakeGateway({metadata_url: json.dumps(payload)})
    with pytest.raises(ArtifactDownloadError, match="no compatible wheel"):
        PypiArtifactDownloader(
            gateway,
            cache_dir="/tmp/aurora-test-cache",
        ).resolve("demo", "1.0.0")


def test_resolve_rejects_unsafe_filename(tmp_path):
    metadata_url = "https://pypi.org/pypi/demo/1.0.0/json"
    gateway = FakeGateway({
        metadata_url: json.dumps({
            "info": {"name": "demo", "version": "1.0.0"},
            "urls": [{
                "filename": "../demo-1.0.0-py3-none-any.whl",
                "url": "https://files.pythonhosted.org/demo.whl",
                "digests": {"sha256": "2" * 64},
                "yanked": False,
            }],
        })
    })
    with pytest.raises(ArtifactDownloadError, match="no compatible wheel"):
        PypiArtifactDownloader(gateway, cache_dir=tmp_path).resolve("demo", "1.0.0")


def test_resolve_exact_requires_metadata_attestation(tmp_path):
    filename = "demo-1.0.0-py3-none-any.whl"
    metadata_url = "https://pypi.org/pypi/demo/1.0.0/json"
    gateway = FakeGateway({
        metadata_url: json.dumps({
            "info": {"name": "demo", "version": "1.0.0"},
            "urls": [{
                "filename": filename,
                "url": "https://files.pythonhosted.org/demo.whl",
                "digests": {"sha256": "3" * 64},
                "yanked": False,
            }],
        })
    })
    artifact = PypiArtifactDownloader(
        gateway, cache_dir=tmp_path
    ).resolve_exact("demo", "1.0.0", filename)
    assert artifact.sha256 == "3" * 64
