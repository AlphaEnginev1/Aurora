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

PROXY_STARTED_PID=""
if ! alive "$PROXY_PID" "aurora.autonomy.egress_proxy_daemon"; then
  rm -f "$PROXY_PID"
  printf '\n=== Aurora egress proxy start %s ===\n' "$(date -Is)" >>"$PROXY_LOG"
  nohup "$VENV/bin/python" -m aurora.autonomy.egress_proxy_daemon --host "$HOST_IP" --port 3128 --network-policy "$ROOT/config/network-policy.json" >>"$PROXY_LOG" 2>&1 &
  PROXY_STARTED_PID="$!"
  echo "$PROXY_STARTED_PID" > "$PROXY_PID"
else
  PROXY_STARTED_PID="$(cat "$PROXY_PID")"
fi

PROXY_READY=0
for _ in $(seq 1 30); do
  if ! process_matches "$PROXY_STARTED_PID" "aurora.autonomy.egress_proxy_daemon"; then
    break
  fi
  if python3 - "$HOST_IP" <<'PY' >/dev/null 2>&1
import socket, sys
with socket.create_connection((sys.argv[1], 3128), timeout=1):
    pass
PY
  then
    sleep 0.5
    if process_matches "$PROXY_STARTED_PID" "aurora.autonomy.egress_proxy_daemon" && python3 - "$HOST_IP" <<'PY' >/dev/null 2>&1
import socket, sys
with socket.create_connection((sys.argv[1], 3128), timeout=1):
    pass
PY
    then
      PROXY_READY=1
      break
    fi
  fi
  sleep 1
done

if [[ "$PROXY_READY" -ne 1 ]]; then
  echo "Aurora egress proxy process did not stay alive and accept connections." >&2
  if process_matches "$PROXY_STARTED_PID" "aurora.autonomy.egress_proxy_daemon"; then
    kill -TERM "$PROXY_STARTED_PID" 2>/dev/null || true
  fi
  rm -f "$PROXY_PID"
  tail -n 80 "$PROXY_LOG" >&2 || true
  exit 1
fi

if alive "$AURORA_PID" "aurora.orchestration.daemon --dashboard"; then
  EXISTING_DAEMON_PID="$(cat "$AURORA_PID")"
  if curl -fsS --max-time 2 http://127.0.0.1:8787/ >/dev/null 2>&1 && process_matches "$EXISTING_DAEMON_PID" "aurora.orchestration.daemon --dashboard"; then
    echo "Aurora dashboard is already running: http://127.0.0.1:8787"
    exit 0
  fi
  echo "Aurora daemon exists but its dashboard is not healthy. Run scripts/restart-local.sh." >&2
  exit 1
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

printf '\n=== Aurora dashboard start %s commit=%s ===\n' "$(date -Is)" "$AURORA_CANONICAL_COMMIT" >>"$AURORA_LOG"
nohup env AURORA_EGRESS_PROXY_HOST="$AURORA_EGRESS_PROXY_HOST" AURORA_EGRESS_PROXY_PORT=3128 HTTP_PROXY="$HTTP_PROXY" HTTPS_PROXY="$HTTPS_PROXY" NO_PROXY="$NO_PROXY" AURORA_DASHBOARD_HOST=127.0.0.1 AURORA_DASHBOARD_PORT=8787 AURORA_CANONICAL_COMMIT="$AURORA_CANONICAL_COMMIT" OPENROUTER_API_KEY="${OPENROUTER_API_KEY:-}" AURORA_DIRECTOR_PROVIDER="${AURORA_DIRECTOR_PROVIDER:-router}" OPENROUTER_MODEL_ID="${OPENROUTER_MODEL_ID:-openrouter/free}" "$VENV/bin/python" -m aurora.orchestration.daemon --dashboard >>"$AURORA_LOG" 2>&1 &
DAEMON_STARTED_PID="$!"
echo "$DAEMON_STARTED_PID" > "$AURORA_PID"

DASHBOARD_READY=0
for _ in $(seq 1 30); do
  if ! process_matches "$DAEMON_STARTED_PID" "aurora.orchestration.daemon --dashboard"; then
    break
  fi
  if curl -fsS --max-time 1 http://127.0.0.1:8787/ >/dev/null 2>&1; then
    # Make sure the HTTP listener belongs to a process that survived startup.
    sleep 1
    if process_matches "$DAEMON_STARTED_PID" "aurora.orchestration.daemon --dashboard" && curl -fsS --max-time 2 http://127.0.0.1:8787/ >/dev/null 2>&1; then
      DASHBOARD_READY=1
      break
    fi
  fi
  sleep 1
done

if [[ "$DASHBOARD_READY" -ne 1 ]]; then
  echo "Aurora dashboard did not become ready from its new daemon process." >&2
  if process_matches "$DAEMON_STARTED_PID" "aurora.orchestration.daemon --dashboard"; then
    kill -TERM "$DAEMON_STARTED_PID" 2>/dev/null || true
  fi
  rm -f "$AURORA_PID"
  tail -n 120 "$AURORA_LOG" >&2 || true
  exit 1
fi

if command -v cmd.exe >/dev/null 2>&1; then
  cmd.exe /C start "" "http://127.0.0.1:8787" >/dev/null 2>&1 || true
fi

echo "Aurora dashboard: http://127.0.0.1:8787"