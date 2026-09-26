"""Rootless isolated network dataplane primitives for Aurora sandboxes.

This module deliberately does not grant sandbox processes network access by itself.
It provides the OS/network-namespace transport that a policy-enforced proxy can use.
Direct sandbox networking remains fail-closed until the proxy and firewall are ready.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import select
import shutil
import subprocess
import time


class NetworkDataplaneError(RuntimeError):
    """Raised when the isolated network dataplane cannot be established safely."""


@dataclass(frozen=True)
class NetworkDataplanePolicy:
    backend: str = "slirp4netns"
    disable_host_loopback: bool = True
    disable_dns: bool = True
    mtu: int = 65520
    ready_timeout_seconds: float = 10.0
    fail_closed_if_backend_missing: bool = True


@dataclass
class NetworkDataplaneHandle:
    """Live slirp process plus the parent-held exit-fd lease."""

    pid: int
    process: subprocess.Popen[str]
    exit_fd: int | None = None


class Slirp4netnsDataplane:
    """Attach rootless user-mode networking to an isolated network namespace.

    The caller must supply the PID of a process that is actually inside the
    target network namespace. Bubblewrap's launcher PID is intentionally not
    sufficient when --unshare-net is used.
    """

    def __init__(self, policy: NetworkDataplanePolicy | None = None) -> None:
        self.policy = policy or NetworkDataplanePolicy()

    def command(
        self,
        sandbox_pid: int,
        *,
        ready_fd: int | None = None,
        exit_fd: int | None = None,
    ) -> list[str]:
        if sandbox_pid <= 0:
            raise NetworkDataplaneError("sandbox pid must be positive")
        if self.policy.backend != "slirp4netns":
            raise NetworkDataplaneError(
                "unsupported network dataplane: " + self.policy.backend
            )
        binary = shutil.which(self.policy.backend)
        if not binary:
            raise NetworkDataplaneError(
                "slirp4netns is required for the isolated network dataplane"
            )
        if self.policy.mtu < 576 or self.policy.mtu > 65521:
            raise NetworkDataplaneError("invalid slirp4netns MTU")
        if self.policy.ready_timeout_seconds <= 0:
            raise NetworkDataplaneError("ready timeout must be positive")
        args = [binary]
        if self.policy.disable_host_loopback:
            args.append("--disable-host-loopback")
        if self.policy.disable_dns:
            args.append("--disable-dns")
        if ready_fd is not None:
            if ready_fd < 0:
                raise NetworkDataplaneError("ready fd must be non-negative")
            args += ["--ready-fd", str(ready_fd)]
        if exit_fd is not None:
            if exit_fd < 0:
                raise NetworkDataplaneError("exit fd must be non-negative")
            args += ["--exit-fd", str(exit_fd)]
        args += [
            "--configure",
            "--mtu=" + str(self.policy.mtu),
            str(sandbox_pid),
            "tap0",
        ]
        return args

    def attach(self, sandbox_pid: int) -> NetworkDataplaneHandle:
        """Start slirp and wait for its readiness signal before returning.

        A pipe-backed --exit-fd is also held by the parent. Closing that FD
        terminates slirp even if the supervising Python process exits abruptly.
        """
        ready_r, ready_w = os.pipe()
        exit_r, exit_w = os.pipe()
        process: subprocess.Popen[str] | None = None
        try:
            argv = self.command(
                sandbox_pid,
                ready_fd=ready_w,
                exit_fd=exit_r,
            )
            process = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                pass_fds=(ready_w, exit_r),
            )
            os.close(ready_w)
            ready_w = -1
            os.close(exit_r)
            exit_r = -1
            self._wait_ready(process, ready_r)
            ready_r = -1
            return NetworkDataplaneHandle(
                pid=sandbox_pid,
                process=process,
                exit_fd=exit_w,
            )
        except (OSError, NetworkDataplaneError, subprocess.SubprocessError):
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            try:
                os.close(ready_r)
            except OSError:
                pass
            if ready_w >= 0:
                try:
                    os.close(ready_w)
                except OSError:
                    pass
            if exit_r >= 0:
                try:
                    os.close(exit_r)
                except OSError:
                    pass
            try:
                os.close(exit_w)
            except OSError:
                pass
            raise

    def _wait_ready(self, process: subprocess.Popen[str], ready_fd: int) -> None:
        deadline = time.monotonic() + self.policy.ready_timeout_seconds
        try:
            while True:
                if process.poll() is not None:
                    stderr = process.stderr.read().strip() if process.stderr else ""
                    detail = f"slirp4netns exited before readiness (rc={process.returncode})"
                    if stderr:
                        detail += f": {stderr}"
                    raise NetworkDataplaneError(detail)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise NetworkDataplaneError(
                        "slirp4netns readiness timeout"
                    )
                readable, _, _ = select.select(
                    [ready_fd], [], [], min(0.25, remaining)
                )
                if not readable:
                    continue
                payload = os.read(ready_fd, 1)
                if payload == b"1":
                    return
                if payload == b"":
                    raise NetworkDataplaneError(
                        "slirp4netns closed readiness channel without becoming ready"
                    )
                raise NetworkDataplaneError(
                    "slirp4netns returned an invalid readiness signal"
                )
        finally:
            try:
                os.close(ready_fd)
            except OSError:
                pass

    @staticmethod
    def stop(handle: NetworkDataplaneHandle) -> None:
        if handle.exit_fd is not None:
            try:
                os.close(handle.exit_fd)
            except OSError:
                pass
            handle.exit_fd = None
        if handle.process.poll() is None:
            handle.process.terminate()
            try:
                handle.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                handle.process.kill()
                handle.process.wait(timeout=5)
