#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PYTHON="$ROOT/.venv/bin/python"
PID_FILE="$ROOT/.aurora-runtime/aurora.pid"
EXPECTED="$PYTHON -m aurora.orchestration.daemon --dashboard"

if [[ -f "$PID_FILE" ]]; then
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ ! "$pid" =~ ^[0-9]+$ ]]; then
    echo "Aurora PID file is invalid; refusing to stop any process." >&2
    exit 1
  fi
  if kill -0 "$pid" 2>/dev/null; then
    if [[ ! -r "/proc/$pid/cmdline" ]]; then
      echo "Cannot verify the process in Aurora's PID file; refusing to stop it." >&2
      exit 1
    fi
    actual="$(tr '\0' ' ' < "/proc/$pid/cmdline")"
    if [[ "$actual" != "$EXPECTED"* ]]; then
      echo "PID $pid does not match Aurora's dashboard daemon; refusing to stop it." >&2
      exit 1
    fi
    echo "Deteniendo de forma controlada Aurora (PID $pid)..."
    kill -TERM "$pid"
    stopped=0
    for _ in $(seq 1 120); do
      if ! kill -0 "$pid" 2>/dev/null || [[ ! -s "/proc/$pid/cmdline" ]]; then
        stopped=1
        break
      fi
      sleep 0.1
    done
    if [[ "$stopped" -ne 1 ]]; then
      echo "Aurora no terminó dentro del plazo; no se iniciará otra instancia." >&2
      exit 1
    fi
  fi
  rm -f -- "$PID_FILE"
fi

exec bash "$ROOT/scripts/launch-local.sh"
