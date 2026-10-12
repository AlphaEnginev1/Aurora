"""Capability-aware model router."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from datetime import datetime, timezone
from threading import RLock
from typing import TYPE_CHECKING, Mapping, Sequence

if TYPE_CHECKING:
    from aurora.context import ContextGate

from .models import InferenceRequest, InferenceResponse, ModelSpec, new_execution_id
from .provider_health import ProviderHealthStore
from .multimodal import MultimodalContentError, message_modalities
from .registry import ModelRegistry, ModelRegistryError
from .providers.registry import ProviderRegistry, ProviderRegistryError, default_provider_registry


logger = logging.getLogger(__name__)


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
    health_store: ProviderHealthStore | None = None
    cost_policy: str = "all"

    def __post_init__(self) -> None:
        self.cost_policy = str(self.cost_policy or "all").strip().lower()
        if self.cost_policy not in {"all", "free_only"}:
            raise ModelRoutingError("cost_policy must be all or free_only")
        if self.provider_registry is None:
            self.provider_registry = default_provider_registry(
                nvidia_api_key=self.nvidia_api_key
            )
        if self.model_registry is None:
            self.model_registry = ModelRegistry.from_specs(self.models)
        else:
            self.models = list(self.model_registry.models)
        self._active_execution_lock = RLock()
        self._active_executions: dict[str, dict[str, object]] = {}

    def active_executions(self) -> tuple[dict[str, object], ...]:
        """Return a secret-free snapshot of model calls executing right now."""
        with self._active_execution_lock:
            records = [
                dict(record)
                for record in self._active_executions.values()
            ]
        return tuple(sorted(records, key=lambda record: str(record.get("started_at", ""))))

    def _begin_active_execution(
        self,
        *,
        execution_id: str,
        spec: ModelSpec,
        request: InferenceRequest,
    ) -> None:
        metadata = request.metadata if isinstance(request.metadata, Mapping) else {}
        record: dict[str, object] = {
            "execution_id": execution_id,
            "request_id": request.request_id,
            "model_id": spec.id,
            "provider": spec.provider,
            "role": metadata.get("role"),
            "task_id": metadata.get("task_id"),
            "mission_id": metadata.get("mission_id"),
            "project_id": metadata.get("project_id"),
            "task_type": metadata.get("task_type"),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "status": "running",
        }
        with self._active_execution_lock:
            self._active_executions[execution_id] = record

    def _end_active_execution(self, execution_id: str) -> None:
        with self._active_execution_lock:
            self._active_executions.pop(execution_id, None)

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
        models = [
            model
            for model in self._registered_models()
            if model.enabled and model.healthy and model.available
            and (self.cost_policy != "free_only" or model.pricing_tier == "free")
        ]
        if self.health_store is None:
            return models
        return [
            model
            for model in models
            if self.health_store.is_eligible(model.provider, model.id)
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
            try:
                modalities = message_modalities(request.messages)
            except MultimodalContentError as exc:
                raise ModelRoutingError(f"Invalid multimodal request: {exc}") from exc
            required_modalities = {item for item in modalities if item != "text"}
            for model in self.available():
                if model.id != request.model:
                    continue
                if required_modalities and not all(
                    modality in model.capabilities or "multimodal" in model.capabilities
                    for modality in required_modalities
                ):
                    raise ModelRoutingError(
                        f"Requested model {request.model} does not support modalities: "
                        + ", ".join(sorted(required_modalities))
                    )
                if capability and capability not in model.capabilities:
                    raise ModelRoutingError(
                        f"Requested model {request.model} does not satisfy capability={capability!r}"
                    )
                if not self._meets_constraints(model, request):
                    raise ModelRoutingError(
                        f"Requested model {request.model} does not satisfy routing constraints"
                    )
                score, factors = self._score_breakdown(
                    model,
                    request=request,
                    preferred_models=preferred,
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
        if required_modalities:
            candidates = [
                m for m in candidates
                if all(
                    modality in m.capabilities or "multimodal" in m.capabilities
                    for modality in required_modalities
                )
            ]
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

    def _availability_score(self, model: ModelSpec) -> float:
        if self.health_store is None:
            return 1.0
        return self.health_store.snapshot(model.provider, model.id).availability_score

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
        tried: set[str] = set()
        routing_request = request
        last_error: Exception | None = None

        while True:
            decision = self.route(
                routing_request,
                capability=capability,
                preferred_models=preferred_models,
            )
            spec = self._model_by_id(decision.selected_model)
            tried.add(spec.id)
            execution_id = new_execution_id()
            attempt_status_code = None
            if self.provider_registry is None:
                raise ModelRoutingError("inference provider registry is not configured")
            try:
                provider = self.provider_registry.create(spec)
            except ProviderRegistryError as exc:
                if self.health_store is not None:
                    self.health_store.record_failure(spec.provider, spec.id, exc)
                last_error = ModelRoutingError(str(exc))
            else:
                import time
                started = time.monotonic()
                self._begin_active_execution(
                    execution_id=execution_id,
                    spec=spec,
                    request=routing_request,
                )
                try:
                    response = provider.generate(
                        routing_request,
                        selected_model=spec.id,
                        execution_id=execution_id,
                    )
                except Exception as exc:
                    last_error = exc
                    # Emit a durable, secret-free attempt record. Never include the
                    # provider response body, headers, request payload, or credentials.
                    attempt_status_code = getattr(exc, "status_code", None)
                    if attempt_status_code is None:
                        attempt_status_code = getattr(exc, "http_status", None)
                    logger.warning(
                        "AURORA_INFERENCE_ATTEMPT %s",
                        json.dumps(
                            {
                                "event": "provider_attempt_failed",
                                "request_id": routing_request.request_id,
                                "execution_id": execution_id,
                                "provider": spec.provider,
                                "model": spec.id,
                                "http_status": attempt_status_code,
                                "error_type": type(exc).__name__,
                            },
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    )
                    if self.health_store is not None:
                        self.health_store.record_failure(
                            spec.provider,
                            spec.id,
                            exc,
                            latency_ms=int((time.monotonic() - started) * 1000),
                        )
                else:
                    if self.health_store is not None:
                        self.health_store.record_success(
                            spec.provider,
                            spec.id,
                            latency_ms=int((time.monotonic() - started) * 1000),
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
                finally:
                    self._end_active_execution(execution_id)

            remaining = [
                model_id
                for model_id, _score in decision.candidates
                if model_id not in tried
            ]
            if not remaining:
                logger.warning(
                    "AURORA_INFERENCE_FAILOVER %s",
                    json.dumps(
                        {
                            "event": "reserve_pool_exhausted",
                            "request_id": routing_request.request_id,
                            "failed_model": spec.id,
                            "http_status": attempt_status_code,
                            "tried_models": sorted(tried),
                            "reserve_models": [],
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                )
                if last_error is not None:
                    raise last_error
                raise ModelRoutingError("inference failed without a provider error")

            metadata = dict(routing_request.metadata or {})
            original_allowed = _allowed_models(routing_request)
            if original_allowed is not None:
                remaining = [model_id for model_id in remaining if model_id in original_allowed]
            if not remaining:
                logger.warning(
                    "AURORA_INFERENCE_FAILOVER %s",
                    json.dumps(
                        {
                            "event": "no_eligible_reserve_after_allowlist",
                            "request_id": routing_request.request_id,
                            "failed_model": spec.id,
                            "http_status": attempt_status_code,
                            "tried_models": sorted(tried),
                            "reserve_models": [],
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                )
                if last_error is not None:
                    raise last_error
                raise ModelRoutingError("no eligible reserve model remains after provider failure")
            logger.warning(
                "AURORA_INFERENCE_FAILOVER %s",
                json.dumps(
                    {
                        "event": "reserve_models_selected",
                        "request_id": routing_request.request_id,
                        "failed_model": spec.id,
                        "http_status": attempt_status_code,
                        "tried_models": sorted(tried),
                        "reserve_models": remaining,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
            metadata["allowed_models"] = remaining
            routing_request = InferenceRequest(
                messages=routing_request.messages,
                model=None,
                temperature=routing_request.temperature,
                max_tokens=routing_request.max_tokens,
                metadata=metadata,
                request_id=routing_request.request_id,
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
