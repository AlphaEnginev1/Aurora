#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
sudo apt-get update
sudo apt-get install -y bubblewrap slirp4netns nftables iproute2 util-linux curl ca-certificates
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .
echo "Aurora workstation setup complete."