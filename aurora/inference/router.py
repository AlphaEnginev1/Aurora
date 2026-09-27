"""Capability-aware model router."""

from dataclasses import dataclass
from typing import Sequence
from .models import InferenceRequest, InferenceResponse, ModelSpec
from .providers.nvidia_api import NVIDIAAPIProvider

class ModelRoutingError(RuntimeError):
    """Raised when no configured model can satisfy an inference request."""

@dataclass
class ModelRouter:
    models: list[ModelSpec]
    nvidia_api_key: str | None = None

    def available(self) -> list[ModelSpec]:
        return [m for m in self.models if m.enabled]

    def select(
        self,
        request: InferenceRequest,
        capability: str | None = None,
        preferred_models: Sequence[str] | None = None,
    ) -> ModelSpec:
        if request.model:
            for model in self.available():
                if model.id != request.model:
                    continue
                if capability and capability not in model.capabilities:
                    raise ModelRoutingError(
                        f"Requested model {request.model} does not satisfy capability={capability!r}"
                    )
                return model
            raise ModelRoutingError(f"Requested model is not configured: {request.model}")
        candidates = self.available()
        if capability:
            candidates = [m for m in candidates if capability in m.capabilities]
        if not candidates:
            raise ModelRoutingError(f"No enabled model satisfies capability={capability!r}")

        if preferred_models:
            preferred = {model_id: index for index, model_id in enumerate(preferred_models)}
            ranked = [
                model
                for model in candidates
                if model.id in preferred
            ]
            if ranked:
                return min(ranked, key=lambda model: preferred[model.id])
        return candidates[0]

    def generate(
        self,
        request: InferenceRequest,
        capability: str | None = None,
        preferred_models: Sequence[str] | None = None,
    ) -> InferenceResponse:
        spec = self.select(
            request,
            capability,
            preferred_models=preferred_models,
        )
        if spec.provider == "nvidia_api":
            return NVIDIAAPIProvider(spec, api_key=self.nvidia_api_key).generate(request)
        raise ModelRoutingError(f"Unsupported provider: {spec.provider}")
