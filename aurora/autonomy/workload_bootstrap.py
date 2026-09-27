#!/usr/bin/env python3
"""Privileged network bootstrap and post-drop seccomp handoff for Aurora."""

from __future__ import annotations

import ctypes
import ctypes.util
import errno
import ipaddress
import os
from pathlib import Path
import select
import shutil
import subprocess
import sys
import time


class BootstrapError(RuntimeError):
    """Raised when secure workload bootstrap cannot be completed."""


# Linux namespace flags. These are deliberately kept local to the bootstrap
# helper so the helper can be injected into the bubblewrap root without needing
# the Aurora package itself to be importable there.
CLONE_NEWTIME = 0x00000080
CLONE_NEWNS = 0x00020000
CLONE_NEWCGROUP = 0x02000000
CLONE_NEWUTS = 0x04000000
CLONE_NEWIPC = 0x08000000
CLONE_NEWUSER = 0x10000000
CLONE_NEWPID = 0x20000000
CLONE_NEWNET = 0x40000000
NAMESPACE_CLONE_FLAGS = (
    CLONE_NEWTIME
    | CLONE_NEWNS
    | CLONE_NEWCGROUP
    | CLONE_NEWUTS
    | CLONE_NEWIPC
    | CLONE_NEWUSER
    | CLONE_NEWPID
    | CLONE_NEWNET
)

SYS_clone = 56
SYS_unshare = 272
SYS_setns = 308
SYS_clone3 = 435
AUDIT_ARCH_X86_64 = 0xC000003E

PR_SET_NO_NEW_PRIVS = 38
PR_SET_SECCOMP = 22
SECCOMP_MODE_FILTER = 2
SECCOMP_RET_ALLOW = 0x7FFF0000
SECCOMP_RET_ERRNO = 0x00050000


class SockFilter(ctypes.Structure):
    _fields_ = [
        ("code", ctypes.c_ushort),
        ("jt", ctypes.c_ubyte),
        ("jf", ctypes.c_ubyte),
        ("k", ctypes.c_uint32),
    ]


class SockFprog(ctypes.Structure):
    _fields_ = [
        ("len", ctypes.c_ushort),
        ("filter", ctypes.POINTER(SockFilter)),
    ]


def _stmt(code: int, k: int) -> SockFilter:
    return SockFilter(code, 0, 0, k)


def _jump(code: int, jt: int, jf: int, k: int) -> SockFilter:
    return SockFilter(code, jt, jf, k)


def seccomp_program() -> list[SockFilter]:
    """Allow normal process/thread creation, but deny namespace creation/join."""
    BPF_LD_W_ABS = 0x20
    BPF_JMP_JEQ_K = 0x15
    BPF_ALU_AND_K = 0x54
    BPF_RET_K = 0x06

    # seccomp_data offsets on Linux: arch=4, syscall nr=0, arg0=16.
    program = [
        _stmt(BPF_LD_W_ABS, 4),
        _jump(BPF_JMP_JEQ_K, 1, 0, AUDIT_ARCH_X86_64),
        _stmt(BPF_RET_K, SECCOMP_RET_ERRNO | errno.EPERM),
        _stmt(BPF_LD_W_ABS, 0),
        _jump(BPF_JMP_JEQ_K, 7, 0, SYS_unshare),
        _jump(BPF_JMP_JEQ_K, 6, 0, SYS_setns),
        # clone3() takes a pointer to struct clone_args, so fail closed for it.
        _jump(BPF_JMP_JEQ_K, 5, 0, SYS_clone3),
        # For clone(), inspect flags and only deny namespace-creating variants.
        _jump(BPF_JMP_JEQ_K, 0, 5, SYS_clone),
        _stmt(BPF_LD_W_ABS, 16),
        _stmt(BPF_ALU_AND_K, NAMESPACE_CLONE_FLAGS),
        _jump(BPF_JMP_JEQ_K, 2, 0, 0),
        _stmt(BPF_RET_K, SECCOMP_RET_ERRNO | errno.EPERM),
        _stmt(BPF_RET_K, SECCOMP_RET_ERRNO | errno.EPERM),
        _stmt(BPF_RET_K, SECCOMP_RET_ALLOW),
    ]
    return program


def install_seccomp() -> None:
    if os.uname().machine != "x86_64":
        raise BootstrapError("controlled network seccomp policy currently requires x86_64")

    libc_path = ctypes.util.find_library("c")
    if not libc_path:
        raise BootstrapError("libc is required for seccomp")
    libc = ctypes.CDLL(libc_path, use_errno=True)
    libc.prctl.argtypes = [
        ctypes.c_int,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
    ]
    libc.prctl.restype = ctypes.c_int

    if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        raise BootstrapError(
            f"PR_SET_NO_NEW_PRIVS failed: {os.strerror(ctypes.get_errno())}"
        )

    program = (SockFilter * len(seccomp_program()))(*seccomp_program())
    fprog = SockFprog(len(program), ctypes.cast(program, ctypes.POINTER(SockFilter)))
    if libc.prctl(
        PR_SET_SECCOMP,
        SECCOMP_MODE_FILTER,
        ctypes.addressof(fprog),
        0,
        0,
    ) != 0:
        raise BootstrapError(
            f"PR_SET_SECCOMP failed: {os.strerror(ctypes.get_errno())}"
        )


def _require_cap_net_admin() -> None:
    """Require CAP_NET_ADMIN before route and firewall mutation."""
    try:
        status = Path("/proc/self/status").read_text(encoding="ascii")
    except (OSError, UnicodeError) as exc:
        raise BootstrapError(f"cannot inspect effective capabilities: {exc}") from exc
    effective = None
    for line in status.splitlines():
        if line.startswith("CapEff:"):
            _, value = line.split(None, 1)
            try:
                effective = int(value, 16)
            except ValueError as exc:
                raise BootstrapError("invalid CapEff value") from exc
            break
    if effective is None:
        raise BootstrapError("CapEff is unavailable")
    if not (effective & (1 << 12)):
        raise BootstrapError("network bootstrap requires effective CAP_NET_ADMIN")


def _run_checked(args: list[str]) -> None:
    try:
        result = subprocess.run(
            args,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BootstrapError(f"network bootstrap command failed to execute: {args[0]}: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise BootstrapError(
            f"network bootstrap command failed ({result.returncode}): {' '.join(args)}"
            + (f": {detail}" if detail else "")
        )


def _wait_tap0(interface: str, timeout_seconds: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            result = subprocess.run(
                ["ip", "link", "show", interface],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
            )
        except (OSError, subprocess.TimeoutExpired):
            result = None
        if result is not None and result.returncode == 0:
            return
        time.sleep(0.05)
    raise BootstrapError(f"{interface} did not appear after slirp4netns readiness")


def _route_present(show_args: list[str]) -> bool:
    try:
        result = subprocess.run(
            show_args,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BootstrapError(
            f"route inspection failed: {' '.join(show_args)}: {exc}"
        ) from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise BootstrapError(
            f"route inspection failed ({result.returncode}): {' '.join(show_args)}"
            + (f": {detail}" if detail else "")
        )
    return bool(result.stdout.strip())


def _delete_route_if_present(
    show_args: list[str],
    delete_args: list[str],
    description: str,
    *,
    attempts: int = 4,
) -> None:
    for _ in range(attempts):
        if not _route_present(show_args):
            return
        try:
            result = subprocess.run(
                delete_args,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BootstrapError(
                f"network bootstrap command failed to execute: {delete_args[0]}: {exc}"
            ) from exc
        if result.returncode == 0:
            continue
        # A concurrent route update can make the delete lose a race. Accept
        # that only when an immediate re-check proves the route is gone.
        if not _route_present(show_args):
            return
        detail = result.stderr.strip() or result.stdout.strip()
        raise BootstrapError(
            f"network bootstrap command failed ({result.returncode}): "
            f"{' '.join(delete_args)}"
            + (f": {detail}" if detail else "")
        )
    if _route_present(show_args):
        raise BootstrapError(
            f"route fence could not remove {description} after {attempts} attempts"
        )


def configure_routes(proxy_host: str, interface: str) -> None:
    try:
        ipaddress.ip_address(proxy_host)
    except ValueError as exc:
        raise BootstrapError("proxy_host must be an IP address") from exc
    if proxy_host != "10.0.2.2":
        raise BootstrapError("controlled proxy gateway must be 10.0.2.2")
    if not interface.isidentifier():
        raise BootstrapError("invalid network interface")

    _delete_route_if_present(
        ["ip", "-4", "route", "show", "default"],
        ["ip", "-4", "route", "del", "default"],
        "the IPv4 default route",
    )
    _delete_route_if_present(
        ["ip", "-4", "route", "show", "10.0.2.0/24", "dev", interface],
        ["ip", "-4", "route", "del", "10.0.2.0/24", "dev", interface],
        "the slirp IPv4 subnet route",
    )

    _run_checked(["ip", "-4", "route", "replace", f"{proxy_host}/32", "dev", interface])

    _delete_route_if_present(
        ["ip", "-6", "route", "show", "default"],
        ["ip", "-6", "route", "del", "default"],
        "the IPv6 default route",
    )


def configure_firewall(proxy_host: str, proxy_port: int) -> None:
    if proxy_host != "10.0.2.2":
        raise BootstrapError("egress firewall proxy gateway must be 10.0.2.2")
    if not 1 <= proxy_port <= 65535:
        raise BootstrapError("invalid egress proxy port")

    nft = shutil.which("nft")
    if not nft:
        raise BootstrapError("nft is required for controlled sandbox networking")

    # Keep this firewall stateless and portable across restricted Linux kernels:
    # OUTPUT is policy-drop and the proxy destination is the only non-loopback
    # destination explicitly allowed. Return packets do not traverse OUTPUT.
    commands = [
        ["add", "table", "inet", "aurora_egress"],
        [
            "add", "chain", "inet", "aurora_egress", "output", "{",
            "type", "filter", "hook", "output", "priority", "0", ";",
            "policy", "drop", ";", "}",
        ],
        [
            "add", "rule", "inet", "aurora_egress", "output",
            "oifname", "lo", "accept",
        ],
        [
            "add", "rule", "inet", "aurora_egress", "output",
            "ip", "daddr", proxy_host, "tcp", "dport", str(proxy_port), "accept",
        ],
    ]
    for command in commands:
        _run_checked([nft, *command])


def handoff_to_workload(workload: list[str]) -> None:
    if not workload or any("\x00" in arg for arg in workload):
        raise BootstrapError("invalid workload command")

    setpriv = shutil.which("setpriv")
    if not setpriv:
        raise BootstrapError("setpriv is required for controlled sandbox networking")

    env = os.environ.copy()
    env.pop("PYTHONPATH", None)

    argv = [
        setpriv,
        "--no-new-privs",
        "--securebits=+noroot,+noroot_locked,+no_setuid_fixup,+no_setuid_fixup_locked",
        "--bounding-set=-all",
        "--inh-caps=-all",
        "--ambient-caps=-all",
        "--",
        sys.executable,
        sys.argv[0],
        "--stage2",
        "--",
        *workload,
    ]
    try:
        os.execvpe(setpriv, argv, env)
    except OSError as exc:
        raise BootstrapError(f"failed to drop workload capabilities: {exc}") from exc


def main() -> int:
    if "--stage2" in sys.argv:
        marker = sys.argv.index("--stage2")
        if marker + 2 >= len(sys.argv) or sys.argv[marker + 1] != "--":
            raise BootstrapError("invalid stage2 invocation")
        workload = sys.argv[marker + 2 :]
        if not workload:
            raise BootstrapError("stage2 workload is empty")
        install_seccomp()
        os.execvp(workload[0], workload)
        raise AssertionError("execvp returned unexpectedly")

    try:
        separator = sys.argv.index("--")
    except ValueError as exc:
        raise BootstrapError("bootstrap invocation is missing -- separator") from exc

    workload = sys.argv[separator + 1 :]
    option_args = sys.argv[1:separator]
    proxy_host = "10.0.2.2"
    proxy_port = 3128
    interface = "tap0"

    i = 0
    while i < len(option_args):
        option = option_args[i]
        if option == "--proxy-host" and i + 1 < len(option_args):
            proxy_host = option_args[i + 1]
            i += 2
            continue
        if option == "--proxy-port" and i + 1 < len(option_args):
            try:
                proxy_port = int(option_args[i + 1])
            except ValueError as exc:
                raise BootstrapError("proxy-port must be an integer") from exc
            i += 2
            continue
        if option == "--interface" and i + 1 < len(option_args):
            interface = option_args[i + 1]
            i += 2
            continue
        raise BootstrapError(f"unknown bootstrap option: {option}")

    if not workload:
        raise BootstrapError("bootstrap workload is empty")

    _wait_tap0(interface)
    _require_cap_net_admin()
    configure_routes(proxy_host, interface)
    configure_firewall(proxy_host, proxy_port)
    handoff_to_workload(workload)
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BootstrapError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
