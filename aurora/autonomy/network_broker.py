"""Controlled outbound network broker policy types for Aurora."""
from dataclasses import dataclass
import json
from pathlib import Path

class NetworkBrokerError(RuntimeError):
    pass

@dataclass(frozen=True)
class NetworkBrokerPolicy:
    protocols: frozenset[str]
    allowed_hosts: frozenset[str]
    max_response_bytes: int = 10485760
    timeout_seconds: float = 30.0

    @classmethod
    def from_file(cls, path: str | Path) -> "NetworkBrokerPolicy":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            protocols=frozenset(data.get("protocols", [])),
            allowed_hosts=frozenset(data.get("allowed_hosts", [])),
            max_response_bytes=int(data.get("max_response_bytes", 10485760)),
            timeout_seconds=float(data.get("timeout_seconds", 30.0)),
        )
