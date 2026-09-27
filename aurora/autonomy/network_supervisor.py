"""Supervisor-owned network bootstrap for Aurora sandboxes."""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress


class NetworkSupervisorError(RuntimeError):
    """Raised when controlled network bootstrap cannot be represented safely."""


@dataclass(frozen=True)
class NetworkSupervisorPolicy:
    """Route-fence policy applied before workload privileges are dropped."""

    # This is the host address where the egress proxy listens. The slirp
    # gateway remains 10.0.2.2 and is used as the next hop to that host.
    proxy_host: str = ""
    proxy_port: int = 3128
    interface: str = "tap0"
    bootstrap_timeout_checks: int = 500

    def validate(self) -> None:
        try:
            address = ipaddress.ip_address(self.proxy_host)
        except ValueError as exc:
            raise NetworkSupervisorError("proxy_host must be an IP address") from exc
        if not isinstance(address, ipaddress.IPv4Address):
            raise NetworkSupervisorError("proxy_host must be an IPv4 address")
        if address.is_loopback or address.is_unspecified or address.is_multicast:
            raise NetworkSupervisorError("proxy_host must be a routable host IPv4 address")
        if address.is_global and not address.is_private:
            raise NetworkSupervisorError("proxy_host must not be a public Internet address")
        if address == ipaddress.IPv4Address("10.0.2.2"):
            raise NetworkSupervisorError("proxy_host must not be the slirp gateway")
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
        # Route deletion is conditional so an already-clean namespace is a
        # valid idempotent starting state. A real delete failure still aborts.
        clear_v4_default = (
            "/bin/sh", "-c",
            "if ip -4 route show default | grep -q .; then "
            "ip -4 route del default; fi",
        )
        clear_slirp_subnet = (
            "/bin/sh", "-c",
            f"if ip -4 route show 10.0.2.0/24 dev {self.interface} | grep -q .; then "
            f"ip -4 route del 10.0.2.0/24 dev {self.interface}; fi",
        )
        clear_v6_default = (
            "/bin/sh", "-c",
            "if ip -6 route show default | grep -q .; then "
            "ip -6 route del default; fi",
        )
        return (
            ("/bin/sh", "-c", wait),
            clear_v4_default,
            clear_slirp_subnet,
            ("ip", "-4", "route", "replace", "10.0.2.2/32", "dev", self.interface),
            (
                "ip", "-4", "route", "replace", f"{self.proxy_host}/32",
                "via", "10.0.2.2", "dev", self.interface, "onlink",
            ),
            clear_v6_default,
        )

    def assert_complete(self, commands: tuple[tuple[str, ...], ...]) -> None:
        self.validate()
        required = set(self.bootstrap_script())
        if not required.issubset(set(commands)):
            raise NetworkSupervisorError("network supervisor bootstrap is incomplete")
