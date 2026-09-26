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


def test_network_bwrap_uses_nftables_and_seccomp_lockdown(tmp_path: Path, monkeypatch):
    executor = SandboxExecutor(SandboxPolicy(network=True))
    monkeypatch.setattr(
        "aurora.autonomy.sandbox.shutil.which",
        lambda name: "/usr/bin/nft" if name == "nft" else "/usr/bin/slirp4netns",
    )
    argv = executor._bubblewrap_argv(
        "/usr/bin/bwrap",
        ("true",),
        tmp_path,
        network=True,
        seccomp_fd=17,
    )
    assert "--unshare-user" in argv
    assert "--disable-userns" not in argv
    assert "--cap-add" in argv
    assert "CAP_NET_ADMIN" in argv
    assert "--seccomp" in argv
    assert "17" in argv
    assert "--block-fd" not in argv
    assert "/bin/sh" in argv
    bootstrap = argv[argv.index("/bin/sh") + 2]
    assert "nft add table inet aurora_egress" in bootstrap
    assert "nft add chain inet aurora_egress output" in bootstrap
    assert "policy drop" in bootstrap
    assert "10.0.2.2" in bootstrap
    assert "setpriv --no-new-privs --bounding-set=-all" in bootstrap


def test_seccomp_filter_is_x86_64_and_nonempty():
    import struct

    with SandboxExecutor._seccomp_filter_fd() as f:
        payload = f.read()
    assert struct.unpack("<H", payload[:2])[0] == 9
    assert len(payload) == 8 + (9 * 8)
