#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT_WIN="$(wslpath -w "$ROOT/scripts/install-desktop-launcher.ps1")"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$SCRIPT_WIN"
