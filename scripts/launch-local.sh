#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
VENV="$ROOT/.venv"
RUNTIME="$ROOT/.aurora-runtime"
mkdir -p "$RUNTIME"
AURORA_LOG="$RUNTIME/aurora.log"
PROXY_LOG="$RUNTIME/egress-proxy.log"
AURORA_PID="$RUNTIME/aurora.pid"
PROXY_PID="$RUNTIME/egress-proxy.pid"

[[ -x "$VENV/bin/python" ]] || { echo "Aurora virtual environment is missing: $VENV" >&2; exit 1; }
for dep in bwrap slirp4netns nft ip; do command -v "$dep" >/dev/null || { echo "$dep is required" >&2; exit 1; }; done
HOST_IP="$(ip -4 route get 1.1.1.1 | awk '/src/ {for (i=1;i<=NF;i++) if ($i=="src") {print $(i+1); exit}}')"
[[ -n "$HOST_IP" ]] || { echo "Unable to determine WSL host IPv4" >&2; exit 1; }

alive() { [[ -f "$1" ]] && kill -0 "$(cat "$1")" 2>/dev/null; }

if ! alive "$PROXY_PID"; then
  rm -f "$PROXY_PID"
  nohup "$VENV/bin/python" -m aurora.autonomy.egress_proxy_daemon --host "$HOST_IP" --port 3128 --network-policy "$ROOT/config/network-policy.json" >>"$PROXY_LOG" 2>&1 &
  echo $! > "$PROXY_PID"
fi

if alive "$AURORA_PID"; then
  exit 0
fi

export AURORA_EGRESS_PROXY_HOST="$HOST_IP"
export AURORA_EGRESS_PROXY_PORT=3128
export AURORA_DASHBOARD_HOST=127.0.0.1
export AURORA_DASHBOARD_PORT=8787
export AURORA_CANONICAL_COMMIT="$($VENV/bin/python -c 'import subprocess; print(subprocess.check_output(["git","rev-parse","HEAD"], text=True).strip())')"

nohup env AURORA_EGRESS_PROXY_HOST="$AURORA_EGRESS_PROXY_HOST" AURORA_EGRESS_PROXY_PORT=3128 AURORA_DASHBOARD_HOST=127.0.0.1 AURORA_DASHBOARD_PORT=8787 AURORA_CANONICAL_COMMIT="$AURORA_CANONICAL_COMMIT" "$VENV/bin/python" -m aurora.orchestration.daemon --dashboard >>"$AURORA_LOG" 2>&1 &
echo $! > "$AURORA_PID"
echo "Aurora dashboard: http://127.0.0.1:8787"