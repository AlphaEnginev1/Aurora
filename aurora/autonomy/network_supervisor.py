"""Supervisor-owned network bootstrap for Aurora sandboxes."""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress


class NetworkSupervisorError(RuntimeError):
    """Raised when controlled network bootstrap cannot be represented safely."""


@dataclass(frozen=True)
class NetworkSupervisorPolicy:
    """Route-fence policy applied before workload privileges are dropped."""

    proxy_host: str = "10.0.2.2"
    proxy_port: int = 3128
    interface: str = "tap0"
    bootstrap_timeout_checks: int = 500

    def validate(self) -> None:
        try:
            ipaddress.ip_address(self.proxy_host)
        except ValueError as exc:
            raise NetworkSupervisorError("proxy_host must be an IP address") from exc
        if self.proxy_host != "10.0.2.2":
            raise NetworkSupervisorError("rootless slirp proxy gateway must be 10.0.2.2")
        if not 1 <= self.proxy_port <= 65535:
            raise NetworkSupervisorError("invalid proxy port")
        if not self.interface.isidentifier():
            raise NetworkSupervisorError("invalid network interface")
        if self.bootstrap_timeout_checks < 1:
            raise NetworkSupervisorError("bootstrap timeout must be positive")

    def bootstrap_script(self) -> tuple[tuple[str, ...], ...]:
        """Commands for the privileged bootstrap phase before setpriv."""
        self.validate()
        wait = (
            "i=0; "
            f"while ! ip link show {self.interface} >/dev/null 2>&1; do "
            f"i=$((i+1)); [ $i -lt {self.bootstrap_timeout_checks} ] || "
            "exit 1; sleep 0.01; done"
        )
        return (
            ("/bin/sh", "-c", wait),
            ("ip", "-4", "route", "del", "default"),
            ("ip", "-4", "route", "del", "10.0.2.0/24", "dev", self.interface),
            ("ip", "-4", "route", "replace", f"{self.proxy_host}/32", "dev", self.interface),
            ("ip", "-6", "route", "del", "default"),
        )

    def assert_complete(self, commands: tuple[tuple[str, ...], ...]) -> None:
        self.validate()
        required = set(self.bootstrap_script())
        if not required.issubset(set(commands)):
            raise NetworkSupervisorError("network supervisor bootstrap is incomplete")
