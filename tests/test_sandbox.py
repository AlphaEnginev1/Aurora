from pathlib import Path

import pytest

from aurora.autonomy.sandbox import SandboxError, SandboxExecutor, SandboxPolicy


def test_network_is_fail_closed_when_not_enabled(tmp_path: Path):
    executor = SandboxExecutor(SandboxPolicy(network=False))
    env = executor._environment({}, network=False)
    assert "HTTP_PROXY" not in env
    assert "HTTPS_PROXY" not in env


def test_network_policy_injects_only_controlled_proxy():
    executor = SandboxExecutor(
        SandboxPolicy(network=True, egress_proxy_host="10.0.2.2", egress_proxy_port=3128)
    )
    env = executor._environment(
        {"HTTP_PROXY": "http://evil.invalid:9999", "NVIDIA_API_KEY": "secret"},
        network=True,
    )
    assert env["HTTP_PROXY"] == "http://10.0.2.2:3128"
    assert env["HTTPS_PROXY"] == "http://10.0.2.2:3128"
    assert env["NO_PROXY"] == ""
    assert "NVIDIA_API_KEY" not in env


def test_missing_bwrap_is_fail_closed(tmp_path: Path, monkeypatch):
    executor = SandboxExecutor()
    monkeypatch.setattr("aurora.autonomy.sandbox.shutil.which", lambda _: None)
    with pytest.raises(SandboxError, match="bubblewrap"):
        executor.execute(("true",), workspace=tmp_path)


def test_command_rejects_null_bytes(tmp_path: Path):
    executor = SandboxExecutor()
    with pytest.raises(SandboxError, match="invalid"):
        executor.execute(("printf", "bad" + bytes([0]).decode() + "value"), workspace=tmp_path)


def test_network_bwrap_requires_user_namespace_and_cap_drop(tmp_path: Path):
    executor = SandboxExecutor(SandboxPolicy(network=True))
    argv = executor._bubblewrap_argv(
        "/usr/bin/bwrap",
        ("true",),
        tmp_path,
        network=True,
        gate_fd=7,
    )
    assert "--unshare-user" in argv
    assert "--disable-userns" in argv
    assert "--cap-drop" not in argv
    assert "setpriv" in argv
    assert "--bounding-set=-all" in argv
    assert "--no-new-privs" in argv
    assert "--block-fd" in argv


def test_egress_enforcer_requires_firewall_prerequisites(monkeypatch):
    from aurora.autonomy.sandbox import _EgressEnforcer

    enforcer = _EgressEnforcer(SandboxPolicy(network=True))

    def fake_which(name: str):
        if name == "iptables":
            return None
        return "/usr/bin/" + name

    monkeypatch.setattr("aurora.autonomy.sandbox.shutil.which", fake_which)
    with pytest.raises(SandboxError, match="iptables"):
        enforcer.prepare(1234)
