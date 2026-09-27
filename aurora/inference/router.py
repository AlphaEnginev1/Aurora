"""Capability-aware model router."""

from dataclasses import dataclass
from typing import Mapping, Sequence
from .models import InferenceRequest, InferenceResponse, ModelSpec
from .providers.nvidia_api import NVIDIAAPIProvider


class ModelRoutingError(RuntimeError):
    """Raised when no configured model can satisfy an inference request."""


@dataclass
class ModelRouter:
    models: list[ModelSpec]
    nvidia_api_key: str | None = None

    def available(self) -> list[ModelSpec]:
        return [m for m in self.models if m.enabled and m.healthy and m.available]

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
                if not self._meets_constraints(model, request):
                    raise ModelRoutingError(
                        f"Requested model {request.model} does not satisfy routing constraints"
                    )
                return model
            raise ModelRoutingError(f"Requested model is not configured or unavailable: {request.model}")

        candidates = self.available()
        if capability:
            candidates = [m for m in candidates if capability in m.capabilities]
        candidates = [m for m in candidates if self._meets_constraints(m, request)]
        if not candidates:
            raise ModelRoutingError(f"No enabled, healthy, available model satisfies capability={capability!r}")

        scored = [
            (
                model,
                self._score(
                    model,
                    request=request,
                    preferred_models=preferred_models or (),
                ),
            )
            for model in candidates
        ]
        viable = [(model, score) for model, score in scored if score != float("-inf")]
        if not viable:
            raise ModelRoutingError("No enabled model has sufficient context capacity")
        # Explicit id tie-break makes routing deterministic even if configured
        # model ordering changes between deployments.
        return max(viable, key=lambda item: (item[1], item[0].id))[0]

    @staticmethod
    def _meets_constraints(model: ModelSpec, request: InferenceRequest) -> bool:
        metadata = request.metadata or {}
        constraints = (
            ("min_quality_score", model.quality_score),
            ("min_latency_score", model.latency_score),
            ("min_cost_score", model.cost_score),
        )
        for key, actual in constraints:
            value = metadata.get(key)
            if value is None:
                continue
            try:
                threshold = float(value)
            except (TypeError, ValueError):
                return False
            if not 0.0 <= threshold <= 1.0 or actual < threshold:
                return False
        return True

    @staticmethod
    def _score(
        model: ModelSpec,
        *,
        request: InferenceRequest,
        preferred_models: Sequence[str],
    ) -> float:
        metadata = request.metadata or {}
        skill_tags = {
            str(tag).strip().lower()
            for tag in metadata.get("skill_tags", ())
            if str(tag).strip()
        }
        work_type = str(metadata.get("task_type", "")).strip().lower()
        specialties = {tag.lower() for tag in model.specialties}
        tag_overlap = (
            len(skill_tags & specialties) / max(len(skill_tags), 1)
            if skill_tags else 0.0
        )
        work_match = 1.0 if work_type and work_type in specialties else 0.0
        context_tokens = metadata.get("context_tokens")
        context_fit = 1.0
        if isinstance(context_tokens, int) and model.context_window:
            if context_tokens > model.context_window:
                return float("-inf")
            context_fit = min(1.0, model.context_window / max(context_tokens, 1) / 4.0)
        learned = metadata.get("learned_model_scores", {})
        learned_score = 0.5
        if isinstance(learned, Mapping) and model.id in learned:
            try:
                learned_score = max(0.0, min(1.0, float(learned[model.id])))
            except (TypeError, ValueError):
                pass
        preference = 0.0
        if model.id in preferred_models:
            index = preferred_models.index(model.id)
            preference = 1.0 / (index + 1)
        return (
            0.20 * model.quality_score
            + 0.10 * model.latency_score
            + 0.10 * model.cost_score
            + 0.20 * tag_overlap
            + 0.15 * work_match
            + 0.10 * context_fit
            + 0.15 * learned_score
            + 0.10 * preference
        )

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
