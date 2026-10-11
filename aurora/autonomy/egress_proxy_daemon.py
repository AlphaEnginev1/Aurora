"""Small CLI for Aurora's policy-enforcing local HTTPS CONNECT proxy."""
from __future__ import annotations
import argparse, asyncio, json
from pathlib import Path
from .egress_proxy import EgressProxy, EgressProxyPolicy

def main() -> int:
    parser = argparse.ArgumentParser(description="Run Aurora's controlled egress proxy")
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=3128)
    parser.add_argument("--network-policy", default="config/network-policy.json")
    args = parser.parse_args()
    policy = json.loads(Path(args.network_policy).read_text(encoding="utf-8"))
    hosts = frozenset(str(x).strip().lower() for x in policy.get("allowed_hosts", []) if str(x).strip())
    if not hosts:
        raise SystemExit("network policy has no allowed_hosts")
    ports = frozenset(int(value) for value in policy.get("allowed_ports", [443]))
    if not ports or any(port < 1 or port > 65535 for port in ports):
        raise SystemExit("network policy has invalid allowed_ports")
    asyncio.run(_serve(EgressProxy(EgressProxyPolicy(hosts, allowed_ports=ports)), args.host, args.port))
    return 0

async def _serve(proxy: EgressProxy, host: str, port: int) -> None:
    server = await proxy.serve(host, port)
    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    raise SystemExit(main())