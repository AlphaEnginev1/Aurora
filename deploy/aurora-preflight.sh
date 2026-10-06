#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "\${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="\${ROOT}/.venv/bin/python"

fail() {
  echo "AURORA PREFLIGHT FAILED: $*" >&2
  exit 1
}

command -v "\${PYTHON}" >/dev/null 2>&1 || fail "missing \${PYTHON}"
command -v git >/dev/null 2>&1 || fail "git is required"

cd "\${ROOT}"
"\${PYTHON}" - <<'PY' || exit 1
import json
import os
import socket
from pathlib import Path

root = Path.cwd()

required = {
    "AURORA_EGRESS_PROXY_HOST": os.getenv("AURORA_EGRESS_PROXY_HOST", "").strip(),
    "AURORA_EGRESS_PROXY_PORT": os.getenv("AURORA_EGRESS_PROXY_PORT", "").strip(),
    "AURORA_CANONICAL_COMMIT": os.getenv("AURORA_CANONICAL_COMMIT", "").strip(),
}
missing = [name for name, value in required.items() if not value]
if missing:
    raise SystemExit("missing required environment: " + ", ".join(missing))

try:
    proxy_port = int(required["AURORA_EGRESS_PROXY_PORT"])
except ValueError:
    raise SystemExit("AURORA_EGRESS_PROXY_PORT must be an integer")
if not 1 <= proxy_port <= 65535:
    raise SystemExit("AURORA_EGRESS_PROXY_PORT must be between 1 and 65535")

for relative in (
    "config/inference-policy.json",
    "config/autonomy-policy.json",
    "config/tool-registry.json",
    "config/network-policy.json",
    "config/organization.json",
    "config/skills.json",
    "aurora/dashboard/ui/index.html",
    "aurora/dashboard/ui/app.js",
    "aurora/dashboard/ui/styles.css",
    "aurora/dashboard/ui/aurora-central-avatar.jpg",
):
    path = root / relative
    if not path.is_file():
        raise SystemExit("required deployment file missing: " + relative)

policy = json.loads((root / "config/inference-policy.json").read_text(encoding="utf-8"))
free_policy_path = root / os.getenv("AURORA_FREE_MODEL_POLICY", policy.get("free_model_policy", "config/free-model-policy.json"))
free_policy = json.loads(free_policy_path.read_text(encoding="utf-8"))
if str(policy.get("cost_policy", "")).strip().lower() != "free_only":
    raise SystemExit("inference policy must enforce cost_policy=free_only")
if str(free_policy.get("mode", "")).strip().lower() != "free_only":
    raise SystemExit("free-model policy must enforce mode=free_only")
models = policy.get("models", [])
enabled_models = [
    model for model in models
    if isinstance(model, dict) and model.get("enabled") is True
]
if not enabled_models:
    raise SystemExit("inference policy has no enabled model")
for model in enabled_models:
    if str(model.get("pricing_tier", "")).strip().lower() != "free":
        raise SystemExit("enabled model is not explicitly marked free: " + str(model.get("id", "")))
if not os.getenv("NVIDIA_API_KEY", "").strip():
    raise SystemExit("NVIDIA_API_KEY is required for the configured inference fabric")

director = os.getenv("AURORA_DIRECTOR_PROVIDER", "router").strip().lower()
if director == "gemini":
    if not os.getenv("GEMINI_API_KEY", "").strip():
        raise SystemExit("GEMINI_API_KEY is required when AURORA_DIRECTOR_PROVIDER=gemini")
    if not os.getenv("GEMINI_MODEL_ID", "").strip():
        raise SystemExit("GEMINI_MODEL_ID is required when AURORA_DIRECTOR_PROVIDER=gemini")
elif director not in {"router", "inference_fabric", ""}:
    raise SystemExit("unsupported AURORA_DIRECTOR_PROVIDER: " + director)

dashboard_host = os.getenv("AURORA_DASHBOARD_HOST", "127.0.0.1").strip().lower()
if dashboard_host not in {"127.0.0.1", "::1", "localhost"} and not os.getenv("AURORA_DASHBOARD_API_TOKEN", "").strip():
    raise SystemExit("non-loopback dashboard host requires AURORA_DASHBOARD_API_TOKEN")

runtime_root = Path(os.getenv("AURORA_WORKSPACE_ROOT", ".aurora-runtime"))
runtime_root.mkdir(parents=True, exist_ok=True)
probe = runtime_root / ".write-probe"
probe.write_text("ok", encoding="utf-8")
probe.unlink()

try:
    with socket.create_connection(
        (required["AURORA_EGRESS_PROXY_HOST"], proxy_port),
        timeout=3,
    ):
        pass
except OSError as exc:
    raise SystemExit(f"egress proxy is unreachable: {exc}")

print("AURORA PREFLIGHT OK")
PY

CURRENT="$(git -C "\${ROOT}" rev-parse HEAD)"
EXPECTED="\${AURORA_CANONICAL_COMMIT}"
[ "\${CURRENT}" = "\${EXPECTED}" ] || fail "deployed commit \${CURRENT} does not match AURORA_CANONICAL_COMMIT=\${EXPECTED}"

echo "Aurora production preflight passed for \${CURRENT}"
