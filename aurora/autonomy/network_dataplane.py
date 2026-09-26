"""Rootless isolated network dataplane primitives for Aurora sandboxes.

This module deliberately does not grant sandbox processes network access by itself.
It provides the OS/network-namespace transport that a policy-enforced proxy can use.
Direct sandbox networking remains fail-closed until the proxy and firewall are ready.
"""
from __future__ import annotations

from dataclasses import dataclass
import shutil
import subprocess


class NetworkDataplaneError(RuntimeError):
    """Raised when the isolated network dataplane cannot be established safely."""


@dataclass(frozen=True)
class NetworkDataplanePolicy:
    backend: str = "slirp4netns"
    disable_host_loopback: bool = True
    disable_dns: bool = True
    mtu: int = 65520
    fail_closed_if_backend_missing: bool = True


@dataclass(frozen=True)
class NetworkDataplaneHandle:
    pid: int
    process: subprocess.Popen[str]


class Slirp4netnsDataplane:
    """Attach rootless user-mode networking to an isolated network namespace.

    The caller must keep the sandbox process in its own network namespace.
    This class never shares the host network.
    """

    def __init__(self, policy: NetworkDataplanePolicy | None = None) -> None:
        self.policy = policy or NetworkDataplanePolicy()

    def command(self, sandbox_pid: int) -> list[str]:
        if sandbox_pid <= 0:
            raise NetworkDataplaneError("sandbox pid must be positive")
        if self.policy.backend != "slirp4netns":
            raise NetworkDataplaneError("unsupported network dataplane: " + self.policy.backend)
        binary = shutil.which(self.policy.backend)
        if not binary:
            raise NetworkDataplaneError("slirp4netns is required for the isolated network dataplane")
        args = [binary]
        if self.policy.disable_host_loopback:
            args.append("--disable-host-loopback")
        if self.policy.disable_dns:
            args.append("--disable-dns")
        args += ["--configure", "--mtu=" + str(self.policy.mtu), str(sandbox_pid), "tap0"]
        return args

    def attach(self, sandbox_pid: int) -> NetworkDataplaneHandle:
        argv = self.command(sandbox_pid)
        try:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except OSError as exc:
            raise NetworkDataplaneError("failed to start isolated network dataplane: " + str(exc)) from exc
        return NetworkDataplaneHandle(sandbox_pid, process)

    @staticmethod
    def stop(handle: NetworkDataplaneHandle) -> None:
        handle.process.terminate()
        try:
            handle.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            handle.process.kill()
            handle.process.wait(timeout=5)
