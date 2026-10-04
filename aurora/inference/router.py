"""Capability-aware model router."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Mapping, Sequence

if TYPE_CHECKING:
    from aurora.context import ContextGate

from .models import InferenceRequest, InferenceResponse, ModelSpec, new_execution_id
from .multimodal import MultimodalContentError, message_modalities
from .registry import ModelRegistry, ModelRegistryError
from .providers.registry import ProviderRegistry, ProviderRegistryError, default_provider_registry


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
    provider_registry: ProviderRegistry | None = None
    model_registry: ModelRegistry | None = None
    context_gate: ContextGate | None = None
    enforce_context: bool = False

    def __post_init__(self) -> None:
        if self.provider_registry is None:
            self.provider_registry = default_provider_registry(
                nvidia_api_key=self.nvidia_api_key
            )
        if self.model_registry is None:
            self.model_registry = ModelRegistry.from_specs(self.models)
        else:
            self.models = list(self.model_registry.models)

    def _registered_models(self) -> list[ModelSpec]:
        if self.model_registry is not None:
            return list(self.model_registry.models)
        return list(self.models)

    def configure_context_gate(
        self,
        context_gate: ContextGate,
        *,
        enforce: bool = True,
    ) -> None:
        """Attach Aurora's universal context gate to the inference boundary."""
        self.context_gate = context_gate
        self.enforce_context = enforce

    def available(self) -> list[ModelSpec]:
        return [
            model
            for model in self._registered_models()
            if model.enabled and model.healthy and model.available
        ]

    def route(
        self,
        request: InferenceRequest,
        capability: str | None = None,
        preferred_models: Sequence[str] | None = None,
    ) -> RoutingDecision:
        preferred = preferred_models or ()
        if request.model:
            allowed_models = _allowed_models(request)
            if allowed_models is not None and request.model not in allowed_models:
                raise ModelRoutingError(
                    f"Requested model {request.model} is outside the request allowlist"
                )
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
        try:
            modalities = message_modalities(request.messages)
        except MultimodalContentError as exc:
            raise ModelRoutingError(f"Invalid multimodal request: {exc}") from exc
        required_modalities = {item for item in modalities if item != "text"}
        allowed_models = _allowed_models(request)
        if allowed_models is not None:
            candidates = [m for m in candidates if m.id in allowed_models]
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
        return self._model_by_id(decision.selected_model)

    def _model_by_id(self, model_id: str) -> ModelSpec:
        try:
            if self.model_registry is not None:
                return self.model_registry.resolve(model_id)
        except ModelRegistryError as exc:
            raise ModelRoutingError(str(exc)) from exc
        for model in self.models:
            if model.id == model_id:
                return model
        raise ModelRoutingError(f"unknown inference model: {model_id}")

    def _enforce_authoritative_context(self, request: InferenceRequest) -> InferenceRequest:
        if not self.enforce_context:
            return request
        if self.context_gate is None:
            raise ModelRoutingError(
                "universal Aurora ContextGate is required but not configured"
            )

        from aurora.context import AuroraContextError, ContextScope

        metadata = dict(request.metadata or {})
        role = str(metadata.get("role", "")).strip()
        project_id = str(metadata.get("project_id", "")).strip() or None
        task_id = str(metadata.get("task_id", "")).strip() or None

        if role.startswith("director"):
            scope = ContextScope.DIRECTOR
            agent_role = None
        elif project_id is not None:
            scope = ContextScope.PROJECT
            agent_role = role or None
        else:
            scope = ContextScope.GLOBAL
            agent_role = role or None

        try:
            manifest = self.context_gate.prepare(
                scope=scope,
                project_id=project_id,
                agent_role=agent_role,
                task_id=task_id,
                require_mailbox=True,
                require_knowledge=True,
            )
        except AuroraContextError as exc:
            raise ModelRoutingError(f"Aurora ContextGate rejected inference: {exc}") from exc

        if "runtime_state" in manifest.unknown_fields:
            raise ModelRoutingError(
                "Aurora ContextGate rejected inference: runtime_state is unknown"
            )

        metadata["aurora_context_verified"] = True
        metadata["aurora_context_manifest_id"] = manifest.manifest_id
        metadata["aurora_context_scope"] = manifest.scope.value
        metadata["aurora_context_unknown_fields"] = list(manifest.unknown_fields)
        return InferenceRequest(
            messages=[*manifest.messages, *request.messages],
            model=request.model,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            metadata=metadata,
            request_id=request.request_id,
        )

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
        request = self._enforce_authoritative_context(request)
        decision = self.route(
            request,
            capability=capability,
            preferred_models=preferred_models,
        )
        spec = self._model_by_id(decision.selected_model)
        execution_id = new_execution_id()
        if self.provider_registry is None:
            raise ModelRoutingError("inference provider registry is not configured")
        try:
            provider = self.provider_registry.create(spec)
        except ProviderRegistryError as exc:
            raise ModelRoutingError(str(exc)) from exc
        response = provider.generate(
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


def _allowed_models(request: InferenceRequest) -> set[str] | None:
    raw = (request.metadata or {}).get("allowed_models")
    if raw is None:
        return None
    if isinstance(raw, str):
        values = {raw.strip()} if raw.strip() else set()
    else:
        try:
            values = {str(value).strip() for value in raw if str(value).strip()}
        except TypeError as exc:
            raise ModelRoutingError("allowed_models must be an iterable of model ids") from exc
    if not values:
        raise ModelRoutingError("allowed_models cannot be empty")
    return values


__all__ = ["ModelRouter", "ModelRoutingError", "RoutingDecision"]
