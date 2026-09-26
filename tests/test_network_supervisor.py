from aurora.autonomy.network_supervisor import NetworkSupervisorError, NetworkSupervisorPolicy


def test_supervisor_exposes_only_proxy_host_route():
    commands = NetworkSupervisorPolicy().bootstrap_script()
    assert any(cmd[:3] == ("/bin/sh", "-c", "i=0; while") for cmd in commands)
    assert ("ip", "-4", "route", "del", "default") in commands
    assert ("ip", "-4", "route", "del", "10.0.2.0/24", "dev", "tap0") in commands
    assert ("ip", "-4", "route", "replace", "10.0.2.2/32", "dev", "tap0") in commands
    assert ("ip", "-6", "route", "del", "default") in commands


def test_supervisor_rejects_non_slirp_proxy_gateway():
    try:
        NetworkSupervisorPolicy(proxy_host="10.0.0.1").bootstrap_script()
    except NetworkSupervisorError:
        pass
    else:
        raise AssertionError("unsafe proxy gateway was accepted")
