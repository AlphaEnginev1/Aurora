#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VENV="$ROOT/.venv"
RUNTIME="$ROOT/.aurora-runtime"
STATUS="$RUNTIME/update-status"
LOCK="$RUNTIME/update.lock"
AURORA_PID="$RUNTIME/aurora.pid"
UPDATE_LOG="$RUNTIME/update.log"

mkdir -p "$RUNTIME"

exec 9>"$LOCK"
flock -n 9 || exit 0

write_status() {
  local state="$1"
  local commit="${2:-}"
  local message="${3:-}"
  {
    printf 'state=%s\n' "$state"
    printf 'commit=%s\n' "$commit"
    printf 'message=%s\n' "$message"
  } > "${STATUS}.tmp"
  mv "${STATUS}.tmp" "$STATUS"
}

write_status "starting" "$(git rev-parse HEAD 2>/dev/null || true)" "Preparing safe update"
exec >>"$UPDATE_LOG" 2>&1

if [[ "$(git branch --show-current)" != "main" ]]; then
  write_status "failed" "$(git rev-parse HEAD)" "Local repository is not on main"
  exit 1
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
  write_status "blocked" "$(git rev-parse HEAD)" "Local working tree has uncommitted changes"
  exit 2
fi

if [[ ! -x "$VENV/bin/python" ]]; then
  write_status "failed" "$(git rev-parse HEAD)" "Aurora virtual environment is missing"
  exit 1
fi

before="$(git rev-parse HEAD)"
write_status "checking" "$before" "Checking origin/main"

if ! git fetch --prune origin main; then
  write_status "failed" "$before" "Unable to fetch origin/main"
  exit 1
fi

target="$(git rev-parse origin/main)"
if [[ "$before" == "$target" ]]; then
  write_status "ready" "$before" "Aurora is already up to date"
  exit 0
fi

if ! git merge --ff-only origin/main; then
  write_status "blocked" "$before" "Update was not a fast-forward"
  exit 2
fi

updated="$(git rev-parse HEAD)"
write_status "installing" "$updated" "Updating Python environment"

if ! "$VENV/bin/python" -m pip install -e .; then
  write_status "failed" "$updated" "Python environment update failed"
  exit 1
fi

write_status "restarting" "$updated" "Restarting Aurora"

if [[ -f "$AURORA_PID" ]]; then
  pid="$(cat "$AURORA_PID" 2>/dev/null || true)"
  if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 20); do
      if ! kill -0 "$pid" 2>/dev/null; then
        break
      fi
      sleep 0.25
    done
  fi
fi
rm -f "$AURORA_PID"

nohup bash "$ROOT/scripts/launch-local.sh" >>"$UPDATE_LOG" 2>&1 < /dev/null &

for _ in $(seq 1 40); do
  if curl -fsS --max-time 1 http://127.0.0.1:8787/ >/dev/null 2>&1; then
    write_status "ready" "$updated" "Aurora updated and dashboard is ready"
    exit 0
  fi
  sleep 0.5
done

write_status "failed" "$updated" "Aurora restarted but dashboard did not become ready"
exit 1
