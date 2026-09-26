"""Fail-closed sandbox for executing acquired Aurora tools."""

from __future__ import annotations

from dataclasses import dataclass
import errno
import os
from pathlib import Path
import resource
import shlex
import shutil
import signal
import struct
import subprocess
import tempfile
from typing import Mapping, Sequence

from .egress_firewall import EgressFirewallPolicy, FirewallPolicyError
from .network_supervisor import NetworkSupervisorError, NetworkSupervisorPolicy
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
            completed, timed_out, slirp_process = self._run(
                bwrap, tuple(command), env, root, network=network
            )
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

    def _bubblewrap_argv(
        self,
        bwrap: str,
        command: tuple[str, ...],
        root: Path,
        *,
        network: bool,
        seccomp_fd: int | None = None,
    ) -> list[str]:
        argv = [
            bwrap, "--die-with-parent", "--new-session", "--unshare-pid", "--unshare-uts",
            "--unshare-ipc", "--unshare-cgroup-try", "--unshare-net",
        ]
        if network:
            # --disable-userns is intentionally not used here: in this runtime it
            # creates a nested-userns arrangement that interferes with network
            # namespace bootstrap. Seccomp blocks namespace-changing syscalls
            # after sandbox setup and setpriv removes network-admin capabilities.
            argv += ["--unshare-user", "--cap-add", "CAP_NET_ADMIN"]
        if seccomp_fd is not None:
            argv += ["--seccomp", str(seccomp_fd)]
        argv += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/run"]
        if self.policy.readonly_system:
            for path in ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc"):
                if Path(path).exists():
                    argv += ["--ro-bind", path, path]
        argv += ["--bind", str(root), "/workspace", "--chdir", "/workspace", "--"]
        if network:
            if self.policy.egress_proxy_host != "10.0.2.2":
                raise SandboxError("controlled network requires slirp gateway 10.0.2.2 as proxy host")
            supervisor = NetworkSupervisorPolicy(
                proxy_host=self.policy.egress_proxy_host,
                proxy_port=self.policy.egress_proxy_port,
            )
            try:
                supervisor.validate()
            except NetworkSupervisorError as exc:
                raise SandboxError(str(exc)) from exc
            if shutil.which("ip") is None:
                raise SandboxError("ip is required for controlled sandbox networking")
            nft = shutil.which("nft")
            if nft is None:
                raise SandboxError("nft is required for controlled sandbox networking")
            # slirp4netns creates tap0 asynchronously after the parent attaches it
            # to this network namespace. The workload bootstrap must therefore
            # wait for tap0 before touching routes/firewall; otherwise the shell
            # can fail closed before the parent has a chance to attach slirp.
            bootstrap_lines = [
                "set -eu",
                "i=0; while ! ip link show tap0 >/dev/null 2>&1; do i=$((i+1)); [ $i -lt 200 ] || { echo 'slirp tap0 did not appear' >&2; exit 1; }; sleep 0.05; done",
            ]
            route_commands = supervisor.bootstrap_script()
            for command_tokens in route_commands:
                bootstrap_lines.append(" ".join(shlex.quote(x) for x in command_tokens))
            firewall = EgressFirewallPolicy(
                proxy_host=self.policy.egress_proxy_host,
                proxy_port=self.policy.egress_proxy_port,
            )
            try:
                firewall_commands = firewall.nft_commands()
                firewall.assert_complete(firewall_commands)
            except FirewallPolicyError as exc:
                raise SandboxError(str(exc)) from exc
            for command_tokens in firewall_commands:
                bootstrap_lines.append(" ".join(shlex.quote(x) for x in (nft, *command_tokens)))
            wrapped = " ".join(shlex.quote(item) for item in command)
            bootstrap_lines.append(
                "exec setpriv --no-new-privs --bounding-set=-all --inh-caps=-all --ambient-caps=-all -- " + wrapped
            )
            argv += ["/bin/sh", "-c", "; ".join(bootstrap_lines)]
        else:
            argv += list(command)
        return argv

    @staticmethod
    def _seccomp_filter_fd() -> tempfile._TemporaryFileWrapper:
        """Return a compiled classic-BPF seccomp program for namespace syscalls."""
        if os.uname().machine != "x86_64":
            raise SandboxError("controlled network seccomp policy currently requires x86_64")

        BPF_LD = 0x00
        BPF_JMP = 0x05
        BPF_JEQ = 0x10
        BPF_K = 0x00
        BPF_RET = 0x06
        SECCOMP_RET_ALLOW = 0x7FFF0000
        SECCOMP_RET_ERRNO = 0x00050000
        EPERM = errno.EPERM

        __NR_clone = 56
        __NR_unshare = 272
        __NR_setns = 308
        __NR_clone3 = 435

        def stmt(code: int, k: int) -> bytes:
            return struct.pack("<HBBI", code, 0, 0, k)

        def jump(code: int, jt: int, jf: int, k: int) -> bytes:
            return struct.pack("<HBBI", code, jt, jf, k)

        program = b"".join(
            [
                stmt(BPF_LD | BPF_K | 0x20, 4),
                jump(BPF_JMP | BPF_JEQ | BPF_K, 0, 5, 0xC000003E),
                stmt(BPF_LD | BPF_K | 0x20, 0),
                jump(BPF_JMP | BPF_JEQ | BPF_K, 3, 0, __NR_clone),
                jump(BPF_JMP | BPF_JEQ | BPF_K, 2, 0, __NR_unshare),
                jump(BPF_JMP | BPF_JEQ | BPF_K, 1, 0, __NR_setns),
                jump(BPF_JMP | BPF_JEQ | BPF_K, 0, 1, __NR_clone3),
                stmt(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | EPERM),
                stmt(BPF_RET | BPF_K, SECCOMP_RET_ALLOW),
            ]
        )
        f = tempfile.TemporaryFile()
        f.write(program)
        f.flush()
        f.seek(0)
        return f

    def _run(
        self,
        bwrap: str,
        command: tuple[str, ...],
        env: dict[str, str],
        cwd: Path,
        *,
        network: bool,
    ) -> tuple[subprocess.CompletedProcess[str], bool, subprocess.Popen[str] | None]:
        slirp_process: subprocess.Popen[str] | None = None
        process: subprocess.Popen[str] | None = None
        seccomp_file = self._seccomp_filter_fd() if network else None
        try:
            argv = self._bubblewrap_argv(
                bwrap, command, cwd, network=network,
                seccomp_fd=seccomp_file.fileno() if seccomp_file is not None else None,
            )
            pass_fds = (seccomp_file.fileno(),) if seccomp_file is not None else ()
            process = subprocess.Popen(
                argv, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                start_new_session=True, preexec_fn=self._limits, pass_fds=pass_fds,
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
        finally:
            if seccomp_file is not None:
                seccomp_file.close()

    @staticmethod
    def _stop_process(process: subprocess.Popen[str]) -> None:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

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

    def _cap(self, value: str) -> str:
        encoded = value.encode("utf-8", errors="replace")
        if len(encoded) <= self.policy.max_output_bytes:
            return value
        return encoded[: self.policy.max_output_bytes].decode("utf-8", errors="ignore")
