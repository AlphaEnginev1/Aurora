from aurora.autonomy.workload_bootstrap import (
    AUDIT_ARCH_X86_64,
    NAMESPACE_CLONE_FLAGS,
    SYS_clone,
    SYS_clone3,
    SYS_setns,
    SYS_unshare,
    seccomp_program,
)


def test_namespace_clone_mask_covers_all_namespace_types():
    expected = (
        0x00000080  # CLONE_NEWTIME
        | 0x00020000  # CLONE_NEWNS
        | 0x02000000  # CLONE_NEWCGROUP
        | 0x04000000  # CLONE_NEWUTS
        | 0x08000000  # CLONE_NEWIPC
        | 0x10000000  # CLONE_NEWUSER
        | 0x20000000  # CLONE_NEWPID
        | 0x40000000  # CLONE_NEWNET
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
    # The longer program checks clone flags instead of denying ordinary
    # fork/thread creation unconditionally.
    assert len(seccomp_program()) >= 14
