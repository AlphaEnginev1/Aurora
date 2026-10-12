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

# Reuse the verified recovery path: stop orphaned daemons and the proxy,
# then launch-local.sh validates that the new dashboard process stays alive.
dashboard_process_matches() {
  [[ -s "$AURORA_PID" ]] || return 1
  local pid
  local -a argv=()
  pid="$(cat "$AURORA_PID" 2>/dev/null || true)"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  [[ -r "/proc/$pid/cmdline" ]] || return 1

  # /proc/PID/cmdline is NUL-delimited. Compare argv elements instead of
  # flattening them into a string, which adds a trailing space and can make a
  # healthy dashboard fail an exact-string readiness check.
  mapfile -d '' -t argv < "/proc/$pid/cmdline"
  [[ "${#argv[@]}" -eq 4 ]] || return 1
  [[ "${argv[0]}" == "$VENV/bin/python" ]] || return 1
  [[ "${argv[1]}" == "-m" ]] || return 1
  [[ "${argv[2]}" == "aurora.orchestration.daemon" ]] || return 1
  [[ "${argv[3]}" == "--dashboard" ]]
}

if ! bash "$ROOT/scripts/restart-local.sh"; then
  write_status "failed" "$updated" "Aurora restart failed; inspect .aurora-runtime/update.log"
  exit 1
fi

# Do not accept the old server's HTTP response as proof of a successful
# restart. restart-local.sh must finish successfully, then verify its PID.
for _ in $(seq 1 10); do
  if dashboard_process_matches && curl -fsS --max-time 2 http://127.0.0.1:8787/ >/dev/null 2>&1; then
    sleep 1
    if dashboard_process_matches && curl -fsS --max-time 2 http://127.0.0.1:8787/ >/dev/null 2>&1; then
      write_status "ready" "$updated" "Aurora updated and dashboard is ready"
      exit 0
    fi
  fi
  sleep 1
done

write_status "failed" "$updated" "Aurora restart helper finished but dashboard did not remain ready"
exit 1
