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
if str(free_policy.get("providers", {}).get("nvidia_api", {}).get("dynamic_catalog", "")).strip().lower() == "free_endpoint":
    network_policy = json.loads((root / "config/network-policy.json").read_text(encoding="utf-8"))
    if "build.nvidia.com" not in network_policy.get("allowed_hosts", []):
        raise SystemExit("dynamic NVIDIA free catalog requires build.nvidia.com in allowed_hosts")
models = policy.get("models", [])
enabled_models = [
    model for model in models
    if isinstance(model, dict) and model.get("enabled") is True
]
if not enabled_models:
    raise SystemExit("inference policy has no enabled model")
free_provider_models = {}
for provider_id, provider in free_policy.get("providers", {}).items():
    rows = provider.get("models", {}) if isinstance(provider, dict) else {}
    free_provider_models[provider_id] = {str(model_id): record for model_id, record in rows.items() if isinstance(record, dict) and str(record.get("tier", "")).strip().lower() == "free"}
for model in enabled_models:
    model_id = str(model.get("id", "")).strip()
    provider_id = str(model.get("provider", "")).strip()
    if str(model.get("pricing_tier", "")).strip().lower() != "free":
        raise SystemExit("enabled model is not explicitly marked free: " + model_id)
    if model_id not in free_provider_models.get(provider_id, {}):
        raise SystemExit("enabled model is absent from free-model allowlist: " + provider_id + "/" + model_id)
if not os.getenv("NVIDIA_API_KEY", "").strip():
    raise SystemExit("NVIDIA_API_KEY is required for the configured inference fabric")

director = os.getenv("AURORA_DIRECTOR_PROVIDER", "router").strip().lower()
if director == "nvidia_api":
    nvidia_model = os.getenv("NVIDIA_MODEL_ID", "").strip()
    if nvidia_model and nvidia_model not in free_provider_models.get("nvidia_api", {}):
        if nvidia_model not in {str(item.get("id", "")) for item in enabled_models if str(item.get("provider", "")) == "nvidia_api"}:
            raise SystemExit("configured NVIDIA Director model is absent from free-model catalog: " + nvidia_model)
elif director not in {"router", "inference_fabric", ""}:
    # Legacy provider values cannot select another vendor; the runtime falls back
    # to the NVIDIA-only router.
    director = "router"

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
