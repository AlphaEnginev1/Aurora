"""Capability-aware model router."""

from dataclasses import dataclass
from typing import Mapping, Sequence

from .models import InferenceRequest, InferenceResponse, ModelSpec, new_execution_id
from .providers.nvidia_api import NVIDIAAPIProvider


class ModelRoutingError(RuntimeError):
    """Raised when no configured model can satisfy an inference request."""


@dataclass(frozen=True)
class RoutingDecision:
    """Auditable result of one dynamic model-routing decision."""

    selected_model: str
    selected_score: float
    candidates: tuple[tuple[str, float], ...]
    factors: dict[str, float]


@dataclass
class ModelRouter:
    models: list[ModelSpec]
    nvidia_api_key: str | None = None

    def available(self) -> list[ModelSpec]:
        return [m for m in self.models if m.enabled and m.healthy and m.available]

    def route(
        self,
        request: InferenceRequest,
        capability: str | None = None,
        preferred_models: Sequence[str] | None = None,
    ) -> RoutingDecision:
        preferred = preferred_models or ()
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
                score, factors = self._score_breakdown(
                    model, request=request, preferred_models=preferred
                )
                return RoutingDecision(
                    selected_model=model.id,
                    selected_score=score,
                    candidates=((model.id, score),),
                    factors=factors,
                )
            raise ModelRoutingError(
                f"Requested model is not configured or unavailable: {request.model}"
            )

        candidates = self.available()
        if capability:
            candidates = [m for m in candidates if capability in m.capabilities]
        candidates = [m for m in candidates if self._meets_constraints(m, request)]
        if not candidates:
            raise ModelRoutingError(
                f"No enabled, healthy, available model satisfies capability={capability!r}"
            )

        scored = [
            (
                model,
                *self._score_breakdown(
                    model,
                    request=request,
                    preferred_models=preferred,
                ),
            )
            for model in candidates
        ]
        viable = [
            (model, score, factors)
            for model, score, factors in scored
            if score != float("-inf")
        ]
        if not viable:
            raise ModelRoutingError("No enabled model has sufficient context capacity")
        selected, score, factors = max(
            viable, key=lambda item: (item[1], item[0].id)
        )
        return RoutingDecision(
            selected_model=selected.id,
            selected_score=score,
            candidates=tuple(
                sorted(
                    ((model.id, score) for model, score, _ in viable),
                    key=lambda item: (-item[1], item[0]),
                )
            ),
            factors=factors,
        )

    def select(
        self,
        request: InferenceRequest,
        capability: str | None = None,
        preferred_models: Sequence[str] | None = None,
    ) -> ModelSpec:
        decision = self.route(
            request,
            capability=capability,
            preferred_models=preferred_models,
        )
        return next(model for model in self.models if model.id == decision.selected_model)

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

    @classmethod
    def _score_breakdown(
        cls,
        model: ModelSpec,
        *,
        request: InferenceRequest,
        preferred_models: Sequence[str],
    ) -> tuple[float, dict[str, float]]:
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
                return float("-inf"), {}
            context_fit = min(
                1.0, model.context_window / max(context_tokens, 1) / 4.0
            )
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
        factors = {
            "quality": model.quality_score,
            "latency": model.latency_score,
            "cost": model.cost_score,
            "skill_tag_overlap": tag_overlap,
            "work_type_match": work_match,
            "context_fit": context_fit,
            "learned_score": learned_score,
            "preference": preference,
        }
        score = (
            0.20 * factors["quality"]
            + 0.10 * factors["latency"]
            + 0.10 * factors["cost"]
            + 0.20 * factors["skill_tag_overlap"]
            + 0.15 * factors["work_type_match"]
            + 0.10 * factors["context_fit"]
            + 0.15 * factors["learned_score"]
            + 0.10 * factors["preference"]
        )
        return score, factors

    @classmethod
    def _score(
        cls,
        model: ModelSpec,
        *,
        request: InferenceRequest,
        preferred_models: Sequence[str],
    ) -> float:
        return cls._score_breakdown(
            model,
            request=request,
            preferred_models=preferred_models,
        )[0]

    def generate(
        self,
        request: InferenceRequest,
        capability: str | None = None,
        preferred_models: Sequence[str] | None = None,
    ) -> InferenceResponse:
        decision = self.route(
            request,
            capability=capability,
            preferred_models=preferred_models,
        )
        spec = next(model for model in self.models if model.id == decision.selected_model)
        execution_id = new_execution_id()
        if spec.provider == "nvidia_api":
            response = NVIDIAAPIProvider(
                spec, api_key=self.nvidia_api_key
            ).generate(
                request,
                selected_model=spec.id,
                execution_id=execution_id,
            )
            return InferenceResponse(
                model=response.model,
                provider=response.provider,
                content=response.content,
                raw=response.raw,
                request_id=response.request_id,
                execution_id=response.execution_id,
                requested_model=response.requested_model,
                selected_model=response.selected_model,
                provider_model=response.provider_model,
                reported_model=response.reported_model,
                usage=response.usage,
                telemetry=response.telemetry,
                routing_score=decision.selected_score,
                routing_factors=dict(decision.factors),
                routing_candidates=decision.candidates,
            )
        raise ModelRoutingError(f"Unsupported provider: {spec.provider}")


__all__ = ["ModelRouter", "ModelRoutingError", "RoutingDecision"]
