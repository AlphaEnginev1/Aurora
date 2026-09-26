from pathlib import Path
import pytest
from aurora.autonomy.sandbox import SandboxError, SandboxExecutor, SandboxPolicy

def test_network_is_fail_closed(tmp_path: Path):
    executor = SandboxExecutor(SandboxPolicy(network=True))
    with pytest.raises(SandboxError, match="network broker"):
        executor.execute(("true",), workspace=tmp_path)

def test_missing_bwrap_is_fail_closed(tmp_path: Path, monkeypatch):
    executor = SandboxExecutor()
    monkeypatch.setattr("aurora.autonomy.sandbox.shutil.which", lambda _: None)
    with pytest.raises(SandboxError, match="bubblewrap"):
        executor.execute(("true",), workspace=tmp_path)

def test_environment_allowlist_excludes_secrets():
    executor = SandboxExecutor(SandboxPolicy(allowed_env=frozenset({"PATH"})))
    env = executor._environment({"PATH": "/x", "NVIDIA_API_KEY": "secret"})
    assert env["PATH"] == "/x"
    assert "NVIDIA_API_KEY" not in env

def test_command_rejects_null_bytes(tmp_path: Path):
    executor = SandboxExecutor()
    with pytest.raises(SandboxError, match="invalid"):
        executor.execute(("printf", "bad" + bytes([0]).decode() + "value"), workspace=tmp_path)
