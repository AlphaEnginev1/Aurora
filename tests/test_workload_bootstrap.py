from aurora.autonomy.workload_bootstrap import (
    AUDIT_ARCH_X86_64,
    BootstrapError,
    NAMESPACE_CLONE_FLAGS,
    SYS_clone,
    SYS_clone3,
    SYS_setns,
    SYS_unshare,
    _delete_route_if_present,
    seccomp_program,
)


def test_namespace_clone_mask_covers_all_namespace_types():
    expected = (
        0x00000080
        | 0x00020000
        | 0x02000000
        | 0x04000000
        | 0x08000000
        | 0x10000000
        | 0x20000000
        | 0x40000000
    )
    assert NAMESPACE_CLONE_FLAGS == expected


def test_seccomp_program_targets_namespace_syscalls():
    program = seccomp_program()
    constants = [instruction.k for instruction in program]
    assert AUDIT_ARCH_X86_64 in constants
    assert SYS_unshare in constants
    assert SYS_setns in constants
    assert SYS_clone3 in constants
    assert SYS_clone in constants


def test_seccomp_program_is_longer_than_old_all_clone_block():
    assert len(seccomp_program()) >= 14


def test_route_delete_accepts_race_when_route_disappears(monkeypatch):
    import aurora.autonomy.workload_bootstrap as bootstrap

    monkeypatch.setattr(
        bootstrap,
        "_route_present",
        lambda args: True if not getattr(bootstrap, "_seen", False) else False,
    )
    bootstrap._seen = False
    original = bootstrap._route_present

    def route_present(args):
        if not bootstrap._seen:
            bootstrap._seen = True
            return True
        return False

    monkeypatch.setattr(bootstrap, "_route_present", route_present)

    class Result:
        returncode = 2
        stdout = ""
        stderr = "RTNETLINK answers: No such process"

    monkeypatch.setattr(bootstrap.subprocess, "run", lambda *args, **kwargs: Result())

    _delete_route_if_present(
        ["ip", "-4", "route", "show", "default"],
        ["ip", "-4", "route", "del", "default"],
        "the IPv4 default route",
    )


def test_route_delete_fails_when_route_remains(monkeypatch):
    import aurora.autonomy.workload_bootstrap as bootstrap

    monkeypatch.setattr(bootstrap, "_route_present", lambda args: True)

    class Result:
        returncode = 2
        stdout = ""
        stderr = "RTNETLINK answers: Operation not permitted"

    monkeypatch.setattr(bootstrap.subprocess, "run", lambda *args, **kwargs: Result())

    try:
        _delete_route_if_present(
            ["ip", "-4", "route", "show", "default"],
            ["ip", "-4", "route", "del", "default"],
            "the IPv4 default route",
        )
    except BootstrapError as exc:
        assert "Operation not permitted" in str(exc)
    else:
        raise AssertionError("persistent route-delete failure must fail closed")


def test_require_cap_net_admin_reads_effective_capability(monkeypatch):
    import aurora.autonomy.workload_bootstrap as bootstrap

    monkeypatch.setattr(
        bootstrap.Path,
        "read_text",
        lambda *args, **kwargs: "CapEff:\t0000000000001000\n",
    )
    bootstrap._require_cap_net_admin()


def test_require_cap_net_admin_fails_closed_without_capability(monkeypatch):
    import aurora.autonomy.workload_bootstrap as bootstrap

    monkeypatch.setattr(
        bootstrap.Path,
        "read_text",
        lambda *args, **kwargs: "CapEff:\t0000000000000000\n",
    )
    try:
        bootstrap._require_cap_net_admin()
    except bootstrap.BootstrapError as exc:
        assert "CAP_NET_ADMIN" in str(exc)
    else:
        raise AssertionError("missing CAP_NET_ADMIN must fail closed")
