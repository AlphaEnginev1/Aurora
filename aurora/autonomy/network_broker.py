from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse
from .internet import InternetGateway

@dataclass(frozen=True)
class NetworkBrokerDecision:
    allowed: bool
    reason: str

class NetworkBroker:
    """Policy gate for network operations performed by trusted Aurora services.

    Sandbox processes remain network-isolated; this broker never grants them a
    host network namespace. Trusted services use InternetGateway after this gate.
    """
    def __init__(self, gateway: InternetGateway):
        self.gateway = gateway

    def authorize(self, url: str) -> NetworkBrokerDecision:
        try:
            self.gateway.authorize(url)
        except Exception as exc:
            return NetworkBrokerDecision(False, str(exc))
        parsed = urlparse(url)
        return NetworkBrokerDecision(True, f"allowed:{parsed.hostname}")

    def get(self, url: str) -> bytes:
        decision = self.authorize(url)
        if not decision.allowed:
            raise PermissionError(decision.reason)
        return self.gateway.get(url)
