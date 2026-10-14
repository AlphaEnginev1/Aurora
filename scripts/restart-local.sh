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
  "$PYTHON" - "$ROOT" "$1" "$2" <<'PY'
import pathlib
import sys

root, module, required_arg = sys.argv[1:4]
root_path = pathlib.Path(root).resolve()
for entry in pathlib.Path("/proc").iterdir():
    if not entry.name.isdigit():
        continue
    try:
        args = [item.decode(errors="replace") for item in (entry / "cmdline").read_bytes().split(b"\0") if item]
        cwd = (entry / "cwd").resolve(strict=True)
    except (OSError, PermissionError, RuntimeError):
        continue
    # Scope cleanup to this checkout, but don't depend on which Python binary
    # or virtual-environment symlink originally launched Aurora.
    if cwd != root_path:
        continue
    try:
        module_index = args.index("-m")
    except ValueError:
        continue
    if module_index + 1 >= len(args) or args[module_index + 1] != module:
        continue
    if required_arg and required_arg not in args[module_index + 2:]:
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

# Do not start a duplicate daemon if an unrecognized process still owns 8787.
# Report the listener but never terminate a process outside this checkout.
if command -v ss >/dev/null 2>&1; then
  dashboard_listeners="$(ss -H -ltn 'sport = :8787' 2>/dev/null || true)"
  if [[ -n "$dashboard_listeners" ]]; then
    echo "STOP: el puerto 8787 sigue ocupado tras detener las instancias de Aurora de este repositorio." >&2
    echo "No se terminará automáticamente un proceso ajeno. Propietario del puerto:" >&2
    ss -ltnp 'sport = :8787' >&2 || true
    exit 1
  fi
fi

rm -f -- "$RUNTIME/aurora.pid" "$RUNTIME/egress-proxy.pid"
exec bash "$ROOT/scripts/launch-local.sh"
