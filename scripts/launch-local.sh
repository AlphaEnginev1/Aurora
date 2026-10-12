#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
source "$ROOT/scripts/local-env.sh"
aurora_load_local_env || exit 1
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

process_matches() {
  local pid="$1"
  local expected="$2"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  [[ -r "/proc/$pid/cmdline" ]] || return 1
  tr '\0' ' ' <"/proc/$pid/cmdline" | grep -Fq -- "$expected"
}

alive() {
  local pid_file="$1"
  local expected="$2"
  [[ -f "$pid_file" ]] || return 1
  local pid
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  process_matches "$pid" "$expected"
}

if ! alive "$PROXY_PID" "aurora.autonomy.egress_proxy_daemon"; then
  rm -f "$PROXY_PID"
  nohup "$VENV/bin/python" -m aurora.autonomy.egress_proxy_daemon --host "$HOST_IP" --port 3128 --network-policy "$ROOT/config/network-policy.json" >>"$PROXY_LOG" 2>&1 &
  echo $! > "$PROXY_PID"
fi

for _ in $(seq 1 30); do
  if python3 - "$HOST_IP" <<'PY' >/dev/null 2>&1
import socket, sys
with socket.create_connection((sys.argv[1], 3128), timeout=1):
    pass
PY
  then
    break
  fi
  sleep 1
done

if ! python3 - "$HOST_IP" <<'PY' >/dev/null 2>&1
import socket, sys
with socket.create_connection((sys.argv[1], 3128), timeout=2):
    pass
PY
then
  echo "Aurora egress proxy did not become ready." >&2
  tail -n 80 "$PROXY_LOG" >&2 || true
  exit 1
fi

if alive "$AURORA_PID" "aurora.orchestration.daemon"; then
  exit 0
fi
rm -f "$AURORA_PID"

export AURORA_EGRESS_PROXY_HOST="$HOST_IP"
export AURORA_EGRESS_PROXY_PORT=3128
export HTTP_PROXY="http://$HOST_IP:3128"
export HTTPS_PROXY="http://$HOST_IP:3128"
export NO_PROXY="127.0.0.1,localhost,::1"
export AURORA_DASHBOARD_HOST=127.0.0.1
export AURORA_DASHBOARD_PORT=8787
export AURORA_CANONICAL_COMMIT="$($VENV/bin/python -c 'import subprocess; print(subprocess.check_output(["git","rev-parse","HEAD"], text=True).strip())')"

nohup env AURORA_EGRESS_PROXY_HOST="$AURORA_EGRESS_PROXY_HOST" AURORA_EGRESS_PROXY_PORT=3128 HTTP_PROXY="$HTTP_PROXY" HTTPS_PROXY="$HTTPS_PROXY" NO_PROXY="$NO_PROXY" AURORA_DASHBOARD_HOST=127.0.0.1 AURORA_DASHBOARD_PORT=8787 AURORA_CANONICAL_COMMIT="$AURORA_CANONICAL_COMMIT" OPENROUTER_API_KEY="${OPENROUTER_API_KEY:-}" AURORA_DIRECTOR_PROVIDER="${AURORA_DIRECTOR_PROVIDER:-router}" OPENROUTER_MODEL_ID="${OPENROUTER_MODEL_ID:-openrouter/free}" "$VENV/bin/python" -m aurora.orchestration.daemon --dashboard >>"$AURORA_LOG" 2>&1 &
echo $! > "$AURORA_PID"

for _ in $(seq 1 30); do
  if curl -fsS --max-time 1 http://127.0.0.1:8787/ >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if command -v cmd.exe >/dev/null 2>&1; then
  cmd.exe /C start "" "http://127.0.0.1:8787" >/dev/null 2>&1 || true
fi

if ! curl -fsS --max-time 2 http://127.0.0.1:8787/ >/dev/null 2>&1; then
  echo "Aurora dashboard did not become ready." >&2
  tail -n 120 "$AURORA_LOG" >&2 || true
  exit 1
fi

echo "Aurora dashboard: http://127.0.0.1:8787"