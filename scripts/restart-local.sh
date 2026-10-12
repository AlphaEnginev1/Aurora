#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PYTHON="$ROOT/.venv/bin/python"
RUNTIME="$ROOT/.aurora-runtime"
[[ -x "$PYTHON" ]] || { echo "Aurora virtual environment is missing: $PYTHON" >&2; exit 1; }
mkdir -p "$RUNTIME"

# Find only processes launched with this repository's venv and exact Aurora module.
# This also catches orphaned instances missing from stale PID files.
find_module_pids() {
  "$PYTHON" - "$PYTHON" "$1" "$2" <<'PY'
import pathlib
import sys

executable, module, required_arg = sys.argv[1:4]
for entry in pathlib.Path("/proc").iterdir():
    if not entry.name.isdigit():
        continue
    try:
        args = [item.decode(errors="replace") for item in (entry / "cmdline").read_bytes().split(b"\0") if item]
    except (OSError, PermissionError):
        continue
    if len(args) < 3 or args[0] != executable or args[1:3] != ["-m", module]:
        continue
    if required_arg and required_arg not in args[3:]:
        continue
    print(entry.name)
PY
}

stop_module() {
  local module="$1"
  local required_arg="$2"
  local pid
  local remaining

  mapfile -t pids < <(find_module_pids "$module" "$required_arg")
  for pid in "${pids[@]}"; do
    [[ "$pid" =~ ^[0-9]+$ ]] || continue
    echo "Deteniendo instancia Aurora: $module (PID $pid)"
    kill -TERM "$pid" 2>/dev/null || true
  done

  for _ in $(seq 1 120); do
    remaining="$(find_module_pids "$module" "$required_arg")"
    if [[ -z "$remaining" ]]; then
      return 0
    fi
    sleep 0.1
  done

  remaining="$(find_module_pids "$module" "$required_arg")"
  if [[ -n "$remaining" ]]; then
    echo "Estas instancias no terminaron; no se iniciará otro proceso: $module (PID/s $remaining)" >&2
    return 1
  fi
}

stop_module "aurora.orchestration.daemon" "--dashboard"
stop_module "aurora.autonomy.egress_proxy_daemon" ""

rm -f -- "$RUNTIME/aurora.pid" "$RUNTIME/egress-proxy.pid"
exec bash "$ROOT/scripts/launch-local.sh"
