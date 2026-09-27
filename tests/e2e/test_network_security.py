"""Linux E2E security battery for Aurora controlled sandbox egress."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import shutil
import subprocess
import threading

import pytest

from aurora.autonomy.acquisition import AcquisitionManager
from aurora.autonomy.egress_proxy import EgressProxy, EgressProxyPolicy
from aurora.autonomy.internet import InternetGateway, InternetPolicy
from aurora.autonomy.registry import ToolSpec
from aurora.autonomy.sandbox import SandboxExecutor, SandboxPolicy

REQUIRED = ("bwrap", "slirp4netns", "nft", "ip", "setpriv", "curl")


@pytest.fixture(scope="module", autouse=True)
def require_linux_network_stack():
    if os.name != "posix":
        pytest.skip("Linux E2E battery")
    missing = [name for name in REQUIRED if shutil.which(name) is None]
    if missing:
        pytest.fail("E2E prerequisites missing: " + ", ".join(missing))


def host_ipv4() -> str:
    result = subprocess.run(
        ["ip", "-4", "route", "get", "1.1.1.1"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=5,
    )
    if result.returncode != 0:
        raise RuntimeError(f"cannot discover host IPv4: {result.stderr.strip()}")
    fields = result.stdout.split()
    try:
        host = fields[fields.index("src") + 1]
    except (ValueError, IndexError) as exc:
        raise RuntimeError(f"host IPv4 missing from route lookup: {result.stdout!r}") from exc
    if ":" in host:
        raise RuntimeError(f"host route lookup returned non-IPv4 address: {host}")
    return host


class ProxyHarness:
    def __init__(self, allowed_hosts=frozenset({"github.com"})):
        self.host = host_ipv4()
        self.proxy = EgressProxy(EgressProxyPolicy(frozenset(allowed_hosts)))
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        self.server = None
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        asyncio.set_event_loop(self.loop)

        async def start():
            self.server = await self.proxy.serve(self.host, 3128)
            self.ready.set()
            while self.server is not None:
                await asyncio.sleep(0.05)

        self.loop.run_until_complete(start())

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
    harness = ProxyHarness().start()
    yield harness
    harness.stop()

@pytest.fixture
def pypi_proxy():
    harness = ProxyHarness(
        frozenset({"pypi.org", "files.pythonhosted.org"})
    ).start()
    yield harness
    harness.stop()


def run_sandbox(tmp_path: Path, *args: str, proxy_host: str | None = None):
    return SandboxExecutor(
        SandboxPolicy(
            network=True,
            egress_proxy_host=proxy_host or host_ipv4(),
            egress_proxy_port=3128,
            timeout_seconds=20,
        )
    ).execute(args, workspace=tmp_path)


@pytest.fixture(scope="module")
def controlled_network_enforcement_available(tmp_path_factory):
    probe = run_sandbox(tmp_path_factory.mktemp("network-probe"), "true")
    if probe.returncode == 0:
        return True
    pytest.fail(
        "controlled network enforcement bootstrap failed: "
        f"rc={probe.returncode} stdout={probe.stdout!r} stderr={probe.stderr!r} "
        f"args={probe.args!r}"
    )


def test_allowed_https_reaches_approved_host(controlled_network_enforcement_available, proxy, tmp_path: Path):
    result = run_sandbox(
        tmp_path, "curl", "-fsS", "--max-time", "10", "-o", "/dev/null",
        "https://github.com/", proxy_host=proxy.host,
    )
    assert result.returncode == 0, f"rc={result.returncode} stderr={result.stderr!r}"


def test_disallowed_host_is_rejected_by_proxy(controlled_network_enforcement_available, proxy, tmp_path: Path):
    result = run_sandbox(
        tmp_path, "curl", "-fsS", "--max-time", "5", "-o", "/dev/null",
        "https://example.com/", proxy_host=proxy.host,
    )
    assert result.returncode != 0


def test_direct_ipv4_socket_is_blocked(controlled_network_enforcement_available, tmp_path: Path):
    result = run_sandbox(tmp_path, "curl", "--noproxy", "*", "--connect-timeout", "3", "--max-time", "5", "https://1.1.1.1/")
    assert result.returncode != 0


def test_direct_ipv6_socket_is_blocked(controlled_network_enforcement_available, tmp_path: Path):
    result = run_sandbox(tmp_path, "curl", "-6", "--noproxy", "*", "--connect-timeout", "3", "--max-time", "5", "https://[2606:4700:4700::1111]/")
    assert result.returncode != 0


def test_proxy_down_does_not_fall_back_to_direct_network(controlled_network_enforcement_available, tmp_path: Path):
    result = run_sandbox(tmp_path, "curl", "-fsS", "--max-time", "5", "-o", "/dev/null", "https://github.com/")
    assert result.returncode != 0


def test_dataplane_has_no_default_route(controlled_network_enforcement_available, tmp_path: Path):
    result = run_sandbox(tmp_path, "ip", "-4", "route")
    assert result.returncode == 0, result.stderr
    assert not any(line.startswith("default") for line in result.stdout.splitlines())
    assert any("10.0.2.2" in line and "tap0" in line for line in result.stdout.splitlines())


def test_workload_cannot_restore_unrestricted_route(controlled_network_enforcement_available, tmp_path: Path):
    result = run_sandbox(tmp_path, "ip", "route", "add", "default", "via", "10.0.2.2")
    assert result.returncode != 0


def test_workload_cannot_create_or_join_namespaces(controlled_network_enforcement_available, tmp_path: Path):
    result = run_sandbox(tmp_path, "unshare", "--user", "true")
    assert result.returncode != 0


def test_proxy_gateway_is_reachable(controlled_network_enforcement_available, proxy, tmp_path: Path):
    result = run_sandbox(
        tmp_path, "curl", "-v", "--connect-timeout", "3", "--max-time", "5",
        f"http://{proxy.host}:3128/", proxy_host=proxy.host,
    )
    assert result.returncode == 0
    assert "403" in result.stderr


def test_ipv6_has_no_default_route(controlled_network_enforcement_available, tmp_path: Path):
    result = run_sandbox(tmp_path, "ip", "-6", "route")
    assert result.returncode == 0, result.stderr
    assert not any(line.startswith("default") for line in result.stdout.splitlines())



def test_real_pypi_acquisition_downloads_verifies_and_executes(
    controlled_network_enforcement_available, tmp_path: Path
):
    gateway = InternetGateway(
        InternetPolicy(
            frozenset({"https"}),
            frozenset({"pypi.org", "files.pythonhosted.org"}),
        )
    )
    install_root = tmp_path / "managed-site"
    manager = AcquisitionManager(
        allowed_sources=frozenset({"pypi"}),
        install_root=install_root,
        internet_gateway=gateway,
        transaction_dir=tmp_path / "transactions",
        artifact_cache_dir=tmp_path / "artifact-cache",
    )

    result = manager.acquire(
        ToolSpec(
            "packaging",
            "runtime",
            "pypi",
            "packaging",
            version="25.0",
        )
    )

    assert result.status == "installed"
    assert result.version == "25.0"
    assert result.verification == "verified"
    assert result.artifact_sha256 and len(result.artifact_sha256) == 64
    assert result.provenance_url.startswith("https://files.pythonhosted.org/")
    assert any(install_root.rglob("packaging-25.0.dist-info/RECORD"))

    execution = SandboxExecutor(
        SandboxPolicy(network=False, timeout_seconds=20)
    ).execute(
        [
            "python3",
            "-c",
            (
                "import packaging; print(packaging.__version__)"
            ),
        ],
        workspace=tmp_path,
    )
    assert execution.returncode == 0, execution.stderr
    assert execution.stdout.strip() == "25.0"


def test_real_pypi_dependency_closure(
    controlled_network_enforcement_available,
    pypi_proxy,
    tmp_path: Path,
):
    gateway = InternetGateway(
        InternetPolicy(
            frozenset({"https"}),
            frozenset({"pypi.org", "files.pythonhosted.org"}),
        )
    )
    network_sandbox = SandboxExecutor(
        SandboxPolicy(
            network=True,
            egress_proxy_host=pypi_proxy.host,
            egress_proxy_port=3128,
            timeout_seconds=30,
        )
    )
    install_root = tmp_path / "managed-site"
    manager = AcquisitionManager(
        allowed_sources=frozenset({"pypi"}),
        install_root=install_root,
        internet_gateway=gateway,
        transaction_dir=tmp_path / "transactions",
        artifact_cache_dir=tmp_path / "artifact-cache",
        sandbox=SandboxExecutor(SandboxPolicy(network=False, timeout_seconds=20)),
        network_sandbox=network_sandbox,
    )

    result = manager.acquire(
        ToolSpec(
            "requests",
            "runtime",
            "pypi",
            "requests",
            version="2.31.0",
        )
    )

    assert result.status == "installed"
    assert result.version == "2.31.0"
    assert result.verification == "verified"
    assert result.artifact_sha256 and len(result.artifact_sha256) == 64

    expected = {
        "requests",
        "certifi",
        "charset_normalizer",
        "idna",
        "urllib3",
    }
    installed_names = set()
    for metadata_path in install_root.rglob("*.dist-info/METADATA"):
        for line in metadata_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("Name: "):
                installed_names.add(line.split(": ", 1)[1].strip().lower().replace("-", "_"))
                break
    assert expected.issubset(installed_names)

    execution = SandboxExecutor(
        SandboxPolicy(network=False, timeout_seconds=20)
    ).execute(
        [
            "/workspace/managed-site/bin/python",
            "-c",
            "import requests; print(requests.__version__)",
        ],
        workspace=tmp_path,
    )
    assert execution.returncode == 0, execution.stderr
    assert execution.stdout.strip() == "2.31.0"

    managed_python = install_root / "bin" / "python"
    assert managed_python.is_file()
    normalizer = install_root / "bin" / "normalizer"
    assert normalizer.is_file()
    script_execution = SandboxExecutor(
        SandboxPolicy(network=False, timeout_seconds=20)
    ).execute(
        [str(Path("/workspace/managed-site/bin/normalizer")), "--help"],
        workspace=tmp_path,
    )
    assert script_execution.returncode == 0, script_execution.stderr
