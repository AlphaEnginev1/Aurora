from aurora.autonomy.network_dataplane import NetworkDataplaneError, NetworkDataplanePolicy, Slirp4netnsDataplane

def test_dataplane_command_is_fail_closed_when_binary_missing(monkeypatch):
    monkeypatch.setattr("aurora.autonomy.network_dataplane.shutil.which", lambda _: None)
    dataplane = Slirp4netnsDataplane()
    try:
        dataplane.command(1234)
    except NetworkDataplaneError as exc:
        assert "slirp4netns" in str(exc)
    else:
        raise AssertionError("missing dataplane backend must fail closed")

def test_dataplane_disables_host_loopback(monkeypatch):
    monkeypatch.setattr("aurora.autonomy.network_dataplane.shutil.which", lambda _: "/usr/bin/slirp4netns")
    argv = Slirp4netnsDataplane().command(4321)
    assert "--disable-host-loopback" in argv
    assert "--unshare-net" not in argv
    assert argv[-2:] == ["4321", "tap0"]

def test_custom_mtu_and_policy(monkeypatch):
    monkeypatch.setattr("aurora.autonomy.network_dataplane.shutil.which", lambda _: "/usr/bin/slirp4netns")
    argv = Slirp4netnsDataplane(NetworkDataplanePolicy(mtu=1500)).command(77)
    assert "--mtu=1500" in argv

def test_invalid_pid_rejected():
    try:
        Slirp4netnsDataplane().command(0)
    except NetworkDataplaneError as exc:
        assert "pid" in str(exc)
    else:
        raise AssertionError("invalid pid must be rejected")
