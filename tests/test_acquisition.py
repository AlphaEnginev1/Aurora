from aurora.autonomy.acquisition import AcquisitionError, AcquisitionManager
from aurora.autonomy.registry import ToolSpec

def test_plan_rejects_untrusted_source():
    manager = AcquisitionManager(allowed_sources=frozenset({"pypi"}))
    try:
        manager.plan(ToolSpec("rg", "system", "ubuntu-apt", "rg"))
    except AcquisitionError:
        pass
    else:
        raise AssertionError("expected AcquisitionError")

def test_acquisition_uses_existing_executable(tmp_path):
    manager = AcquisitionManager(allowed_sources=frozenset({"ubuntu-apt"}), audit_path=tmp_path / "audit.jsonl")
    result = manager.acquire(ToolSpec("python", "system", "ubuntu-apt", "python3"))
    assert result.status in {"already_available", "installed"}
    assert result.path
    assert (tmp_path / "audit.jsonl").exists()

def test_pypi_requires_isolated_target():
    manager = AcquisitionManager(allowed_sources=frozenset({"pypi"}))
    try:
        manager.acquire(ToolSpec("example", "runtime", "pypi", "example"))
    except AcquisitionError as exc:
        assert "isolated install_root" in str(exc)
    else:
        raise AssertionError("expected AcquisitionError")
