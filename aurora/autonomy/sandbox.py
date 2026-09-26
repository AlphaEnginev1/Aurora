"""Fail-closed sandbox for executing acquired Aurora tools."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import resource
import shlex
import shutil
import signal
import subprocess
from typing import Mapping, Sequence

from .egress_firewall import EgressFirewallPolicy
from .network_dataplane import NetworkDataplaneError, Slirp4netnsDataplane


class SandboxError(RuntimeError):
    """Raised when sandbox execution cannot be safely established."""


@dataclass(frozen=True)
class SandboxPolicy:
    backend: str = "bubblewrap"
    network: bool = False
    egress_proxy_host: str = "10.0.2.2"
    egress_proxy_port: int = 3128
    timeout_seconds: int = 120
    cpu_seconds: int = 60
    memory_bytes: int = 1_073_741_824
    max_processes: int = 64
    max_open_files: int = 256
    max_output_bytes: int = 1_048_576
    allowed_env: frozenset[str] = frozenset({"PATH", "LANG", "LC_ALL", "TZ"})
    readonly_system: bool = True


@dataclass(frozen=True)
class SandboxResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


class _EgressEnforcer:
    """Attach the isolated rootless dataplane before sandbox bootstrap."""

    def __init__(self, policy: SandboxPolicy) -> None:
        self.dataplane = Slirp4netnsDataplane()
        self.policy = policy

    def _require(self, name: str) -> str:
        binary = shutil.which(name)
        if not binary:
            raise SandboxError(f"{name} is required for controlled sandbox networking")
        return binary

    def prepare(self, sandbox_pid: int) -> subprocess.Popen[str]:
        self._require("slirp4netns")
        try:
            slirp = self.dataplane.attach(sandbox_pid)
            return slirp.process
        except (NetworkDataplaneError, OSError, subprocess.SubprocessError) as exc:
            raise SandboxError(f"controlled network setup failed: {exc}") from exc


class SandboxExecutor:
    """Execute a command inside an isolated, bounded Aurora workspace."""

    def __init__(self, policy: SandboxPolicy | None = None) -> None:
        self.policy = policy or SandboxPolicy()

    def execute(
        self,
        command: Sequence[str],
        *,
        workspace: str | Path,
        environment: Mapping[str, str] | None = None,
    ) -> SandboxResult:
        if not command or any("\x00" in item for item in command):
            raise SandboxError("invalid sandbox command")
        if os.name != "posix":
            raise SandboxError("production sandbox currently requires POSIX/Linux")
        root = Path(workspace).resolve()
        root.mkdir(parents=True, exist_ok=True)
        if not root.is_dir():
            raise SandboxError("workspace is not a directory")
        if self.policy.backend != "bubblewrap":
            raise SandboxError(f"unsupported sandbox backend: {self.policy.backend}")
        bwrap = shutil.which("bwrap")
        if not bwrap:
            raise SandboxError("bubblewrap is required for the production sandbox")

        network = self._network_mode()
        env = self._environment(environment or {}, network=network)
        slirp_process: subprocess.Popen[str] | None = None

        try:
            argv = self._bubblewrap_argv(bwrap, tuple(command), root, network=network)
            completed, timed_out, slirp_process = self._run(argv, env, root, network=network)
            return SandboxResult(
                tuple(command), completed.returncode, self._cap(completed.stdout), self._cap(completed.stderr), timed_out
            )
        finally:
            if slirp_process is not None:
                self._stop_process(slirp_process)

    def _network_mode(self) -> bool:
        if not self.policy.network:
            return False
        if self.policy.egress_proxy_port < 1 or self.policy.egress_proxy_port > 65535:
            raise SandboxError("invalid egress proxy port")
        return True

    def _bubblewrap_argv(self, bwrap: str, command: tuple[str, ...], root: Path, *, network: bool) -> list[str]:
        argv = [
            bwrap, "--die-with-parent", "--new-session", "--unshare-pid", "--unshare-uts",
            "--unshare-ipc", "--unshare-cgroup-try", "--unshare-net",
        ]
        if network:
            argv += ["--unshare-user", "--disable-userns", "--cap-add", "CAP_NET_ADMIN"]
        argv += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/run"]
        if self.policy.readonly_system:
            for path in ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc"):
                if Path(path).exists():
                    argv += ["--ro-bind", path, path]
        argv += ["--bind", str(root), "/workspace", "--chdir", "/workspace", "--"]
        if network:
            if self.policy.egress_proxy_host != "10.0.2.2":
                raise SandboxError("controlled network requires slirp gateway 10.0.2.2 as proxy host")
            nft = shutil.which("nft")
            if nft is None:
                raise SandboxError("nft is required for controlled sandbox networking")
            firewall = EgressFirewallPolicy(
                proxy_host=self.policy.egress_proxy_host,
                proxy_port=self.policy.egress_proxy_port,
            )
            commands = [
                [nft, *rule] for rule in firewall.nft_commands()
            ]
            bootstrap_lines = ["set -eu"]
            for command_tokens in commands:
                bootstrap_lines.append(" ".join(shlex.quote(x) for x in command_tokens))
            wrapped = " ".join(shlex.quote(item) for item in command)
            bootstrap_lines.append(
                "exec setpriv --no-new-privs --bounding-set=-all --inh-caps=-all --ambient-caps=-all -- " + wrapped
            )
            argv += ["/bin/sh", "-c", "; ".join(bootstrap_lines)]
        else:
            argv += list(command)
        return argv

    def _environment(self, supplied: Mapping[str, str], *, network: bool = False) -> dict[str, str]:
        result: dict[str, str] = {}
        for key in self.policy.allowed_env:
            if key in supplied:
                result[key] = str(supplied[key])
            elif key in os.environ:
                result[key] = os.environ[key]
        result.setdefault("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
        result["HOME"] = "/workspace"
        if network:
            proxy = f"http://{self.policy.egress_proxy_host}:{self.policy.egress_proxy_port}"
            result["HTTP_PROXY"] = proxy
            result["HTTPS_PROXY"] = proxy
            result["NO_PROXY"] = ""
        else:
            for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
                result.pop(key, None)
        return result

    def _limits(self) -> None:
        resource.setrlimit(resource.RLIMIT_CPU, (self.policy.cpu_seconds, self.policy.cpu_seconds))
        resource.setrlimit(resource.RLIMIT_AS, (self.policy.memory_bytes, self.policy.memory_bytes))
        resource.setrlimit(resource.RLIMIT_NOFILE, (self.policy.max_open_files, self.policy.max_open_files))
        if hasattr(resource, "RLIMIT_NPROC"):
            resource.setrlimit(resource.RLIMIT_NPROC, (self.policy.max_processes, self.policy.max_processes))

    def _run(self, argv: list[str], env: dict[str, str], cwd: Path, *, network: bool) -> tuple[subprocess.CompletedProcess[str], bool, subprocess.Popen[str] | None]:
        slirp_process: subprocess.Popen[str] | None = None
        process: subprocess.Popen[str] | None = None
        try:
            process = subprocess.Popen(
                argv, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                start_new_session=True, preexec_fn=self._limits, pass_fds=(),
            )
            if network:
                slirp_process = _EgressEnforcer(self.policy).prepare(process.pid)
            try:
                stdout, stderr = process.communicate(timeout=self.policy.timeout_seconds)
                return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr), False, slirp_process
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                stdout, stderr = process.communicate()
                return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr), True, slirp_process
        except (OSError, SandboxError) as exc:
            if process is not None and process.poll() is None:
                process.kill()
                process.wait()
            if slirp_process is not None:
                self._stop_process(slirp_process)
            if isinstance(exc, SandboxError):
                raise
            raise SandboxError(f"sandbox execution failed: {exc}") from exc

    @staticmethod
    def _stop_process(process: subprocess.Popen[str]) -> None:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    def _cap(self, value: str) -> str:
        encoded = value.encode("utf-8", errors="replace")
        if len(encoded) <= self.policy.max_output_bytes:
            return value
        return encoded[: self.policy.max_output_bytes].decode("utf-8", errors="ignore")
