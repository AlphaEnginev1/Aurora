#!/usr/bin/env bash
set -euo pipefail
command -v python3 >/dev/null || { echo "python3 missing"; exit 1; }
command -v git >/dev/null || { echo "git missing"; exit 1; }
python3 -m pytest -q
