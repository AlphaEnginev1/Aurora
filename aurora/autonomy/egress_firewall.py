from dataclasses import dataclass


class FirewallPolicyError(RuntimeError):
    """Raised when the controlled egress firewall policy is invalid."""


@dataclass(frozen=True)
class EgressFirewallPolicy:
    """Native nftables policy applied before workload privileges are dropped."""

    table: str = "aurora_egress"
    chain: str = "output"
    proxy_host: str = "10.0.2.2"
    proxy_port: int = 3128

    def nft_commands(self) -> tuple[tuple[str, ...], ...]:
        if not self.table or not self.chain:
            raise FirewallPolicyError("invalid nftables table or chain")
        if self.proxy_host != "10.0.2.2":
            raise FirewallPolicyError("proxy gateway must be 10.0.2.2")
        if not 1 <= self.proxy_port <= 65535:
            raise FirewallPolicyError("invalid proxy port")
        return (
            ("add", "table", "inet", self.table),
            (
                "add", "chain", "inet", self.table, self.chain, "{",
                "type", "filter", "hook", "output", "priority", "0", ";",
                "policy", "drop", ";", "}",
            ),
            ("add", "rule", "inet", self.table, self.chain, "oifname", "lo", "accept"),
            (
                "add", "rule", "inet", self.table, self.chain,
                "ip", "daddr", self.proxy_host,
                "tcp", "dport", str(self.proxy_port), "accept",
            ),
            (
                "add", "rule", "inet", self.table, self.chain,
                "ct", "state", "established,related", "accept",
            ),
        )

    def assert_complete(self, commands: tuple[tuple[str, ...], ...]) -> None:
        required = set(self.nft_commands())
        if not required.issubset(set(commands)):
            raise FirewallPolicyError("egress firewall is incomplete")
