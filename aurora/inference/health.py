"""Non-invasive inference provider health checks."""

from dataclasses import dataclass
from .models import ModelSpec

@dataclass(frozen=True)
class ProviderHealth:
    model: str
    provider: str
    configured: bool
    reason: str

def check_model(spec: ModelSpec, api_key_present: bool = False) -> ProviderHealth:
    if not spec.enabled:
        return ProviderHealth(spec.id, spec.provider, False, "model_disabled")
    if not spec.endpoint:
        return ProviderHealth(spec.id, spec.provider, False, "endpoint_missing")
    if spec.provider == "nvidia_api" and not api_key_present:
        return ProviderHealth(spec.id, spec.provider, False, "NVIDIA_API_KEY_missing")
    return ProviderHealth(spec.id, spec.provider, True, "configured")
