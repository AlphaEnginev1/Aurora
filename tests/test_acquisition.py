import pytest

from aurora.autonomy.acquisition import AcquisitionError, AcquisitionManager
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


def test_gateway_policy_is_required_for_networked_acquisition():
    gateway = InternetGateway(
        InternetPolicy(frozenset({"https"}), frozenset({"pypi.org"}))
    )
    manager = AcquisitionManager(
        allowed_sources=frozenset({"pypi"}),
        internet_gateway=gateway,
    )
    assert manager.plan(
        ToolSpec("example", "runtime", "pypi", "example")
    )["action"] == "install"


def test_gateway_blocks_unapproved_acquisition_source():
    gateway = InternetGateway(
        InternetPolicy(frozenset({"https"}), frozenset({"example.com"}))
    )
    manager = AcquisitionManager(
        allowed_sources=frozenset({"pypi"}),
        internet_gateway=gateway,
    )
    with pytest.raises(AcquisitionError, match="Internet Gateway"):
        manager.plan(ToolSpec("example", "runtime", "pypi", "example"))


def test_pypi_installer_is_pinned_to_gateway_approved_index(tmp_path, monkeypatch):
    gateway = InternetGateway(
        InternetPolicy(frozenset({"https"}), frozenset({"pypi.org"}))
    )
    manager = AcquisitionManager(
        allowed_sources=frozenset({"pypi"}),
        install_root=tmp_path / "site",
        internet_gateway=gateway,
    )

    captured = {}

    class Result:
        returncode = 0
        stderr = ""

    monkeypatch.setattr(
        manager,
        "_run",
        lambda args, **kwargs: captured.update(
            args=args, env=kwargs["env"]
        ) or Result(),
    )
    monkeypatch.setattr(
        "shutil.which",
        lambda name: "/usr/bin/python3" if name == "python3" else None,
    )
    monkeypatch.setattr(
        "aurora.autonomy.acquisition.load_pip_report",
        lambda path: PackageProvenance(
            name="example",
            version="1.0.0",
            source="pypi",
            artifact_sha256="abc123",
            provenance_url="https://files.pythonhosted.org/example.whl",
        ),
    )
    monkeypatch.setattr(manager, "_verify_installed_distribution", lambda name, version: 1)

    result = manager.acquire(ToolSpec("example", "runtime", "pypi", "example"))
    assert result.status == "installed"
    assert result.version == "1.0.0"
    assert result.verification == "verified"
    assert "--index-url" in captured["args"]
    assert "https://pypi.org/simple/" in captured["args"]
    assert "--report" in captured["args"]
    assert captured["env"]["PIP_NO_INPUT"] == "1"

