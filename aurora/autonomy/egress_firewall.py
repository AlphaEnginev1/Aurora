from dataclasses import dataclass


class FirewallPolicyError(RuntimeError):
    pass


@dataclass(frozen=True)
class EgressFirewallPolicy:
    proxy_host: str = "10.0.2.2"
    proxy_port: int = 3128

    def iptables_rules(self) -> tuple[tuple[str, ...], ...]:
        if not 1 <= self.proxy_port <= 65535:
            raise FirewallPolicyError("invalid proxy port")
        return (
            ("-P", "OUTPUT", "DROP"),
            ("-A", "OUTPUT", "-o", "lo", "-j", "ACCEPT"),
            (
                "-A",
                "OUTPUT",
                "-d",
                self.proxy_host,
                "-p",
                "tcp",
                "--dport",
                str(self.proxy_port),
                "-j",
                "ACCEPT",
            ),
            ("-A", "OUTPUT", "-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED", "-j", "ACCEPT"),
        )

    def assert_complete(self, rules: tuple[tuple[str, ...], ...]) -> None:
        required = set(self.iptables_rules())
        if not required.issubset(set(rules)):
            raise FirewallPolicyError("egress firewall is incomplete")
