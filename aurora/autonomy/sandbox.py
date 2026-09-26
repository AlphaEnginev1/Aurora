"""Fail-closed sandbox for executing acquired Aurora tools."""

from __future__ import annotations
from dataclasses import dataclass, field
import os
from pathlib import Path
import resource
import shutil
import signal
import subprocess
from typing import Mapping, Sequence

class SandboxError(RuntimeError):
    """Raised when sandbox execution cannot be safely established."""

@dataclass(frozen=True)
class SandboxPolicy:
    backend: str = "bubblewrap"
    network: bool = False
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

class SandboxExecutor:
    """Execute a command inside an isolated, bounded Aurora workspace."""
    def __init__(self, policy: SandboxPolicy | None = None) -> None:
        self.policy = policy or SandboxPolicy()

    def execute(self, command: Sequence[str], *, workspace: str | Path, environment: Mapping[str, str] | None = None) -> SandboxResult:
        if not command or any("\x00" in item for item in command):
            raise SandboxError("invalid sandbox command")
        if os.name != "posix":
            raise SandboxError("production sandbox currently requires POSIX/Linux")
        if self.policy.network:
            raise SandboxError("direct sandbox network access is disabled; use the network broker")
        root = Path(workspace).resolve()
        root.mkdir(parents=True, exist_ok=True)
        if not root.is_dir():
            raise SandboxError("workspace is not a directory")
        if self.policy.backend != "bubblewrap":
            raise SandboxError(f"unsupported sandbox backend: {self.policy.backend}")
        bwrap = shutil.which("bwrap")
        if not bwrap:
            raise SandboxError("bubblewrap is required for the production sandbox")
        argv = self._bubblewrap_argv(bwrap, tuple(command), root)
        env = self._environment(environment or {})
        completed, timed_out = self._run(argv, env, root)
        return SandboxResult(tuple(command), completed.returncode, self._cap(completed.stdout), self._cap(completed.stderr), timed_out)

    def _bubblewrap_argv(self, bwrap: str, command: tuple[str, ...], root: Path) -> list[str]:
        argv = [bwrap, "--die-with-parent", "--new-session", "--unshare-pid", "--unshare-uts", "--unshare-ipc", "--unshare-cgroup-try", "--unshare-net", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/run"]
        if self.policy.readonly_system:
            for path in ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc"):
                if Path(path).exists():
                    argv += ["--ro-bind", path, path]
        argv += ["--bind", str(root), "/workspace", "--chdir", "/workspace", "--", *command]
        return argv

    def _environment(self, supplied: Mapping[str, str]) -> dict[str, str]:
        result: dict[str, str] = {}
        for key in self.policy.allowed_env:
            if key in supplied:
                result[key] = str(supplied[key])
            elif key in os.environ:
                result[key] = os.environ[key]
        result.setdefault("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
        result["HOME"] = "/workspace"
        return result

    def _limits(self) -> None:
        resource.setrlimit(resource.RLIMIT_CPU, (self.policy.cpu_seconds, self.policy.cpu_seconds))
        resource.setrlimit(resource.RLIMIT_AS, (self.policy.memory_bytes, self.policy.memory_bytes))
        resource.setrlimit(resource.RLIMIT_NOFILE, (self.policy.max_open_files, self.policy.max_open_files))
        if hasattr(resource, "RLIMIT_NPROC"):
            resource.setrlimit(resource.RLIMIT_NPROC, (self.policy.max_processes, self.policy.max_processes))

    def _run(self, argv: list[str], env: dict[str, str], cwd: Path) -> tuple[subprocess.CompletedProcess[str], bool]:
        try:
            process = subprocess.Popen(argv, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True, preexec_fn=self._limits)
            try:
                stdout, stderr = process.communicate(timeout=self.policy.timeout_seconds)
                return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr), False
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                stdout, stderr = process.communicate()
                return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr), True
        except OSError as exc:
            raise SandboxError(f"sandbox execution failed: {exc}") from exc

    def _cap(self, value: str) -> str:
        encoded = value.encode("utf-8", errors="replace")
        if len(encoded) <= self.policy.max_output_bytes:
            return value
        return encoded[: self.policy.max_output_bytes].decode("utf-8", errors="ignore")
