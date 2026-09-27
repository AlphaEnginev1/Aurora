import pytest

from aurora.autonomy.acquisition import AcquisitionError, AcquisitionManager
from aurora.autonomy.pypi_downloader import PypiArtifact
from aurora.autonomy.sandbox import SandboxResult
from aurora.autonomy.internet import InternetGateway, InternetPolicy
from aurora.autonomy.provenance import PackageProvenance
from aurora.autonomy.registry import ToolSpec


def test_plan_rejects_untrusted_source():
    manager = AcquisitionManager(allowed_sources=frozenset({"pypi"}))
    with pytest.raises(AcquisitionError):
        manager.plan(ToolSpec("rg", "system", "ubuntu-apt", "rg"))


def test_acquisition_uses_existing_executable(tmp_path):
    manager = AcquisitionManager(
        allowed_sources=frozenset({"ubuntu-apt"}),
        audit_path=tmp_path / "audit.jsonl",
    )
    result = manager.acquire(ToolSpec("python", "system", "ubuntu-apt", "python3"))
    assert result.status in {"already_available", "installed"}
    assert result.path
    assert (tmp_path / "audit.jsonl").exists()


def test_pypi_requires_isolated_target():
    manager = AcquisitionManager(allowed_sources=frozenset({"pypi"}))
    with pytest.raises(AcquisitionError) as exc:
        manager.acquire(ToolSpec("example", "runtime", "pypi", "example"))
    assert "isolated install_root" in str(exc.value)


def test_gateway_policy_is_required_for_networked_acquisition(tmp_path):
    gateway = InternetGateway(
        InternetPolicy(frozenset({"https"}), frozenset({"pypi.org"}))
    )
    manager = AcquisitionManager(
        allowed_sources=frozenset({"pypi"}),
        install_root=tmp_path / "site",
        internet_gateway=gateway,
    )
    assert manager.plan(
        ToolSpec("example", "runtime", "pypi", "example")
    )["action"] == "install"


def test_gateway_blocks_unapproved_acquisition_source(tmp_path):
    gateway = InternetGateway(
        InternetPolicy(frozenset({"https"}), frozenset({"example.com"}))
    )
    manager = AcquisitionManager(
        allowed_sources=frozenset({"pypi"}),
        install_root=tmp_path / "site",
        internet_gateway=gateway,
    )
    with pytest.raises(AcquisitionError, match="Internet Gateway"):
        manager.plan(ToolSpec("example", "runtime", "pypi", "example"))


def test_pypi_installer_uses_verified_cached_artifact_and_offline_sandbox(tmp_path, monkeypatch):
    gateway = InternetGateway(
        InternetPolicy(
            frozenset({"https"}),
            frozenset({"pypi.org", "files.pythonhosted.org"}),
        )
    )
    manager = AcquisitionManager(
        allowed_sources=frozenset({"pypi"}),
        install_root=tmp_path / "site",
        internet_gateway=gateway,
        transaction_dir=tmp_path / "transactions",
    )

    artifact = PypiArtifact(
        name="example",
        version="1.0.0",
        filename="example-1.0.0-py3-none-any.whl",
        url="https://files.pythonhosted.org/packages/example.whl",
        sha256="a" * 64,
    )
    artifact_path = tmp_path / "artifact.whl"
    artifact_path.write_bytes(b"wheel-bytes")
    monkeypatch.setattr(
        "aurora.autonomy.acquisition.PypiArtifactDownloader.resolve",
        lambda self, name, version=None: artifact,
    )
    monkeypatch.setattr(
        "aurora.autonomy.acquisition.PypiArtifactDownloader.download",
        lambda self, resolved: artifact_path,
    )
    monkeypatch.setattr(
        "shutil.which",
        lambda name: "/usr/bin/python3" if name == "python3" else None,
    )

    class FakeSandbox:
        calls = []

        def execute(self, command, *, workspace):
            self.calls.append(tuple(command))
            if list(command) == ["python3", "-m", "venv", "/workspace/managed-site"]:
                return SandboxResult(tuple(command), 0, "", "")
            if list(command[:4]) != [
                "/workspace/managed-site/bin/python",
                "-m",
                "pip",
                "install",
            ]:
                raise AssertionError(f"unexpected sandbox command: {command!r}")
            site = workspace / "managed-site" / "lib" / "python3.12" / "site-packages"
            dist = site / "example-1.0.0.dist-info"
            dist.mkdir(parents=True)
            (site / "example.py").write_bytes(b"example")
            (dist / "METADATA").write_text(
                "Name: example\nVersion: 1.0.0\n", encoding="utf-8"
            )
            (dist / "RECORD").write_text(
                "example.py,,\nexample-1.0.0.dist-info/METADATA,,\nexample-1.0.0.dist-info/RECORD,,\n",
                encoding="utf-8",
            )
            return SandboxResult(tuple(command), 0, "", "")

    manager.sandbox = FakeSandbox()
    monkeypatch.setattr(
        manager,
        "_verify_all_installed_distributions",
        lambda *args, **kwargs: 1,
    )

    result = manager.acquire(
        ToolSpec("example", "runtime", "pypi", "example", version="1.0.0")
    )
    assert result.status == "installed"
    assert result.version == "1.0.0"
    assert result.verification == "verified"
    assert result.artifact_sha256 == "a" * 64
    assert len(manager.sandbox.calls) == 2
    assert manager.sandbox.calls[0] == ("python3", "-m", "venv", "/workspace/managed-site")
    assert manager.sandbox.calls[1][0:4] == (
        "/workspace/managed-site/bin/python",
        "-m",
        "pip",
        "install",
    )
