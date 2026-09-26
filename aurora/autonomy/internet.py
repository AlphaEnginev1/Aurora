"""Policy-controlled HTTPS Internet access for Aurora acquisition and research."""
from dataclasses import dataclass
import json
from pathlib import Path
from urllib.parse import urlparse
import urllib.error
import urllib.request

class InternetPolicyError(RuntimeError):
    """Raised when a network request violates Aurora policy."""

@dataclass(frozen=True)
class InternetPolicy:
    protocols: frozenset[str]
    allowed_hosts: frozenset[str]

    @classmethod
    def from_file(cls, path: str | Path) -> "InternetPolicy":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            frozenset(data.get("protocols", [])),
            frozenset(data.get("allowed_hosts", [])),
        )

class InternetGateway:
    """Small deny-by-default HTTPS gateway for controlled outbound access."""

    def __init__(self, policy: InternetPolicy, timeout: float = 30.0):
        self.policy = policy
        self.timeout = timeout

    def authorize(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in self.policy.protocols:
            raise InternetPolicyError(f"protocol is not allowed: {parsed.scheme}")
        if not parsed.hostname or parsed.hostname not in self.policy.allowed_hosts:
            raise InternetPolicyError(f"host is not allowed: {parsed.hostname}")
        if parsed.username or parsed.password:
            raise InternetPolicyError("embedded URL credentials are not allowed")

    def get(self, url: str) -> bytes:
        self.authorize(url)
        request = urllib.request.Request(
            url,
            method="GET",
            headers={"User-Agent": "Aurora/0.1"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
            final_url = response.geturl()
            self.authorize(final_url)
            return response.read()
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            raise InternetPolicyError(f"network request failed: {exc}") from exc
