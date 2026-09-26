"""Linux E2E security battery for Aurora's controlled sandbox egress.

These tests exercise the real bubblewrap + slirp4netns + namespace firewall path.
They intentionally use a host-side Aurora EgressProxy on the slirp gateway.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import os
import shutil
import threading

import pytest

from aurora.autonomy.egress_proxy import EgressProxy, EgressProxyPolicy
from aurora.autonomy.sandbox import SandboxExecutor, SandboxPolicy


REQUIRED = ("bwrap", "slirp4netns", "ip", "setpriv", "curl")


@pytest.fixture(scope="module", autouse=True)
def require_linux_network_stack():
    if os.name != "posix":
        pytest.skip("Linux E2E battery")
    missing = [name for name in REQUIRED if shutil.which(name) is None]
    if missing:
        pytest.fail("E2E prerequisites missing: " + ", ".join(missing))


class ProxyHarness:
    def __init__(self, allowed_hosts: frozenset[str]):
        self.proxy = EgressProxy(EgressProxyPolicy(allowed_hosts))
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        self.server = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        asyncio.set_event_loop(self.loop)

        async def start():
            self.server = await self.proxy.serve("0.0.0.0", 3128)
            self.ready.set()
            while self.server is not None:
                await asyncio.sleep(0.05)

        try:
            self.loop.run_until_complete(start())
        finally:
            self.loop.close()

    def start(self):
        self.thread.start()
        if not self.ready.wait(10):
            raise RuntimeError("egress proxy did not start")
        return self

    def stop(self):
        def close():
            if self.server is not None:
                self.server.close()
                self.server = None

        self.loop.call_soon_threadsafe(close)
        self.thread.join(timeout=10)


@pytest.fixture
def proxy():
    harness = ProxyHarness(frozenset({"github.com"})).start()
    yield harness
    harness.stop()


def run_sandbox(tmp_path: Path, *args: str, port: int = 3128):
    executor = SandboxExecutor(
        SandboxPolicy(
            network=True,
            egress_proxy_host="10.0.2.2",
            egress_proxy_port=port,
            timeout_seconds=20,
        )
    )
    return executor.execute(args, workspace=tmp_path)


def test_allowed_https_reaches_approved_host(proxy, tmp_path: Path):
    result = run_sandbox(
        tmp_path,
        "curl",
        "-v",
        "--connect-timeout",
        "3",
        "--max-time",
        "10",
        "-o",
        "/dev/null",
        "https://github.com/",
    )
    assert result.returncode == 0, result.stderr


def test_disallowed_host_is_rejected_by_proxy(proxy, tmp_path: Path):
    result = run_sandbox(
        tmp_path,
        "curl",
        "-fsS",
        "--max-time",
        "5",
        "-o",
        "/dev/null",
        "https://example.com/",
    )
    assert result.returncode != 0


def test_direct_ipv4_socket_is_blocked(tmp_path: Path):
    result = run_sandbox(
        tmp_path,
        "curl",
        "--noproxy",
        "*",
        "--connect-timeout",
        "3",
        "--max-time",
        "5",
        "https://1.1.1.1/",
    )
    assert result.returncode != 0


def test_direct_ipv6_socket_is_blocked(tmp_path: Path):
    result = run_sandbox(
        tmp_path,
        "curl",
        "-6",
        "--noproxy",
        "*",
        "--connect-timeout",
        "3",
        "--max-time",
        "5",
        "https://[2606:4700:4700::1111]/",
    )
    assert result.returncode != 0


def test_proxy_down_does_not_fall_back_to_direct_network(tmp_path: Path):
    result = run_sandbox(
        tmp_path,
        "curl",
        "-fsS",
        "--max-time",
        "5",
        "-o",
        "/dev/null",
        "https://github.com/",
    )
    assert result.returncode != 0


def test_workload_cannot_modify_namespace_firewall(proxy, tmp_path: Path):
    result = run_sandbox(
        tmp_path,
        "iptables",
        "-S",
        "OUTPUT",
    )
    assert result.returncode != 0


def test_workload_cannot_restore_default_route(proxy, tmp_path: Path):
    result = run_sandbox(
        tmp_path,
        "ip",
        "route",
        "add",
        "default",
        "via",
        "10.0.2.2",
    )
    assert result.returncode != 0


def test_proxy_gateway_is_reachable(proxy, tmp_path: Path):
    result = run_sandbox(
        tmp_path,
        "curl",
        "-v",
        "--connect-timeout",
        "3",
        "--max-time",
        "5",
        "http://10.0.2.2:3128/",
    )
    assert result.returncode != 0
    assert "HTTP/1.1 403" in result.stderr or "HTTP/1.0 403" in result.stderr


def test_route_fence_exposes_only_proxy_gateway(tmp_path: Path):
    result = run_sandbox(tmp_path, "ip", "route")
    assert result.returncode == 0, result.stderr
    assert "10.0.2.2/32" in result.stdout
    assert "default" not in result.stdout
