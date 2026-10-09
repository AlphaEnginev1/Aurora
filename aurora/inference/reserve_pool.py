"""Dynamic reserve-pool selection over Aurora's existing routing and health layers.

The reserve pool is intentionally not a second router or failover controller. It
materializes a fresh, deduplicated set of eligible model candidates from the
existing ModelRouter registry, ProviderHealthStore, routing constraints, and
learned scores supplied by the caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from .models import InferenceRequest, ModelSpec
from .router import ModelRouter
from .multimodal import MultimodalContentError, message_modalities


class DynamicReservePoolError(RuntimeError):
    """Raised when reserve-pool construction receives invalid input."""


@dataclass(frozen=True)
class ReserveCandidate:
    """One dynamically eligible reserve model and its routing evidence."""

    model: ModelSpec
    score: float
    factors: dict[str, float]

    @property
    def key(self) -> tuple[str, str]:
        return (self.model.provider, self.model.id)


class DynamicReservePool:
    """Build a fresh reserve set from Aurora's existing routing fabric.

    The pool is ephemeral by design: durable discovery/approval remains owned by
    ModelCandidateStore and operational state remains owned by ProviderHealthStore.
    No second router, health database, or failover state machine is introduced.
    """

    def __init__(self, router: ModelRouter, *, max_candidates: int = 32, max_per_provider: int | None = None) -> None:
        if max_candidates < 1:
            raise ValueError("max_candidates must be at least 1")
        if max_per_provider is not None and max_per_provider < 1:
            raise ValueError("max_per_provider must be at least 1")
        self.router = router
        self.max_candidates = int(max_candidates)
        self.max_per_provider = int(max_per_provider) if max_per_provider is not None else None

    def build(
        self,
        request: InferenceRequest,
        *,
        models: Iterable[ModelSpec] | None = None,
        capability: str | None = None,
        exclude: Iterable[tuple[str, str] | str] = (),
        preferred_models: Sequence[str] = (),
        learned_scores: Mapping[str, float] | None = None,
    ) -> tuple[ReserveCandidate, ...]:
        """Return the best currently eligible reserve candidates."""
        try:
            modalities = message_modalities(request.messages)
        except MultimodalContentError as exc:
            raise DynamicReservePoolError(f"invalid multimodal request: {exc}") from exc

        required_modalities = {item for item in modalities if item != "text"}
        allowed_models = _allowed_models(request)
        excluded = _normalize_exclusions(exclude)
        source = list(models) if models is not None else self.router.available()

        unique: dict[tuple[str, str], ModelSpec] = {}
        for model in source:
            key = (model.provider, model.id)
            if key in unique or key in excluded or model.id in excluded:
                continue
            if not model.enabled or not model.healthy or not model.available:
                continue
            if self.router.cost_policy == "free_only":
                if model.pricing_tier != "free":
                    continue
                if model.provider != "nvidia_api":
                    from .free_policy import is_free_model
                    if not is_free_model(model.provider, model.id):
                        continue
            if allowed_models is not None and model.id not in allowed_models:
                continue
            if capability and capability not in model.capabilities:
                continue
            if required_modalities and not all(
                modality in model.capabilities or "multimodal" in model.capabilities
                for modality in required_modalities
            ):
                continue
            if not self.router._meets_constraints(model, request):
                continue
            if self.router.health_store is not None and not self.router.health_store.is_eligible(model.provider, model.id):
                continue
            unique[key] = model

        scored: list[ReserveCandidate] = []
        metadata = dict(request.metadata or {})
        if learned_scores is not None:
            metadata["learned_model_scores"] = dict(learned_scores)
        scoring_request = InferenceRequest(
            messages=request.messages,
            model=request.model,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            metadata=metadata,
            request_id=request.request_id,
        )
        for model in unique.values():
            score, factors = self.router._score_breakdown(
                model,
                request=scoring_request,
                preferred_models=preferred_models,
            )
            if score == float("-inf"):
                continue
            factors = dict(factors)
            if self.router.health_store is not None:
                factors["availability"] = self.router.health_store.snapshot(model.provider, model.id).availability_score
            scored.append(ReserveCandidate(model=model, score=score, factors=factors))

        scored.sort(key=lambda item: (
            -item.score,
            -item.factors.get("availability", 1.0),
            -item.model.quality_score,
            -item.model.latency_score,
            item.model.provider,
            item.model.id,
        ))

        result: list[ReserveCandidate] = []
        provider_counts: dict[str, int] = {}
        for candidate in scored:
            provider = candidate.model.provider
            if self.max_per_provider is not None and provider_counts.get(provider, 0) >= self.max_per_provider:
                continue
            result.append(candidate)
            provider_counts[provider] = provider_counts.get(provider, 0) + 1
            if len(result) >= self.max_candidates:
                break
        return tuple(result)


def _normalize_exclusions(values: Iterable[tuple[str, str] | str]) -> set[tuple[str, str] | str]:
    result: set[tuple[str, str] | str] = set()
    for value in values:
        if isinstance(value, tuple):
            if len(value) != 2:
                raise DynamicReservePoolError("reserve exclusion tuples must contain provider and model")
            provider, model = (str(item).strip() for item in value)
            if provider and model:
                result.add((provider, model))
            continue
        normalized = str(value).strip()
        if normalized:
            result.add(normalized)
    return result


def _allowed_models(request: InferenceRequest) -> set[str] | None:
    raw = (request.metadata or {}).get("allowed_models")
    if raw is None:
        return None
    if isinstance(raw, str):
        return {raw.strip()} if raw.strip() else set()
    if not isinstance(raw, (list, tuple, set, frozenset)):
        return set()
    return {str(item).strip() for item in raw if str(item).strip()}


__all__ = ["DynamicReservePool", "DynamicReservePoolError", "ReserveCandidate"]
