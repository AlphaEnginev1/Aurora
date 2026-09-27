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


def test_network_bwrap_uses_post_setup_seccomp_handoff(tmp_path: Path, monkeypatch):
    executor = SandboxExecutor(SandboxPolicy(network=True))
    monkeypatch.setattr(
        "aurora.autonomy.sandbox.shutil.which",
        lambda name: (
            "/usr/bin/slirp4netns"
            if name == "slirp4netns"
            else "/usr/sbin/ip"
            if name == "ip"
            else "/usr/bin/nft"
            if name == "nft"
            else "/usr/bin/bwrap"
            if name == "bwrap"
            else "/usr/bin/setpriv"
        ),
    )
    argv = executor._bubblewrap_argv(
        "/usr/bin/bwrap",
        ("true",),
        tmp_path,
        network=True,
        bootstrap_fd=17,
        json_status_fd=21,
    )
    assert "--unshare-user" in argv
    assert "--cap-add" in argv
    assert "CAP_NET_ADMIN" in argv
    assert argv.count("--cap-add") == 2
    assert "CAP_SETPCAP" in argv
    assert "--uid" in argv
    assert argv[argv.index("--uid") + 1] == "0"
    assert "--gid" in argv
    assert argv[argv.index("--gid") + 1] == "0"
    assert "--seccomp" not in argv
    assert "--json-status-fd" in argv
    assert "21" in argv
    assert "--file" in argv
    assert "17" in argv
    assert "/aurora-workload-bootstrap.py" in argv
    assert argv[-1] == "true"


def test_workload_bootstrap_handoff_locks_securebits(monkeypatch):
    import aurora.autonomy.workload_bootstrap as bootstrap

    captured = {}

    monkeypatch.setattr(bootstrap.shutil, "which", lambda name: "/usr/bin/setpriv" if name == "setpriv" else None)

    def fake_execvpe(file, argv, env):
        captured["file"] = file
        captured["argv"] = argv
        captured["env"] = env
        raise RuntimeError("captured")

    monkeypatch.setattr(bootstrap.os, "execvpe", fake_execvpe)

    with pytest.raises(RuntimeError, match="captured"):
        bootstrap.handoff_to_workload(["true"])

    assert captured["file"] == "/usr/bin/setpriv"
    argv = captured["argv"]
    assert "--no-new-privs" in argv
    assert "--securebits=+noroot,+noroot_locked,+no_setuid_fixup,+no_setuid_fixup_locked" in argv
    assert "--bounding-set=-all" in argv
    assert "--inh-caps=-all" in argv
    assert "--ambient-caps=-all" in argv
    assert argv[-1] == "true"


def test_network_bwrap_requires_bootstrap_helper(tmp_path: Path):
    executor = SandboxExecutor(SandboxPolicy(network=True))
    with pytest.raises(SandboxError, match="bootstrap helper"):
        executor._bubblewrap_argv(
            "/usr/bin/bwrap",
            ("true",),
            tmp_path,
            network=True,
            json_status_fd=21,
        )


def test_bwrap_status_reader_accepts_child_pid():
    import os

    read_fd, write_fd = os.pipe()
    os.write(write_fd, b'{"child-pid": 4321}\n')
    os.close(write_fd)

    class DummyProcess:
        def poll(self):
            return None

    try:
        assert SandboxExecutor._read_bwrap_child_pid(
            read_fd,
            DummyProcess(),
            timeout_seconds=1,
        ) == 4321
    except Exception:
        os.close(read_fd)
        raise


def test_bwrap_status_reader_accepts_fragmented_json():
    import os
    import threading

    read_fd, write_fd = os.pipe()

    class DummyProcess:
        def poll(self):
            return None

    def writer():
        try:
            os.write(write_fd, b'{ "child-pid": 43')
            os.write(write_fd, b'21 }\\n')
        finally:
            os.close(write_fd)

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        assert SandboxExecutor._read_bwrap_child_pid(
            read_fd,
            DummyProcess(),
            timeout_seconds=1,
        ) == 4321
    finally:
        thread.join(timeout=1)


def test_bwrap_status_reader_fails_on_empty_channel():
    import os

    read_fd, write_fd = os.pipe()
    os.close(write_fd)

    class DummyProcess:
        returncode = 1

        def poll(self):
            return 1

    with pytest.raises(SandboxError, match="closed status channel"):
        SandboxExecutor._read_bwrap_child_pid(
            read_fd,
            DummyProcess(),
            timeout_seconds=1,
        )


def test_seccomp_filter_is_x86_64_and_nonempty():
    from aurora.autonomy.workload_bootstrap import seccomp_program

    program = seccomp_program()
    assert len(program) == 14
