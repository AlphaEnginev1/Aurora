"""Canonical runtime registry for Aurora inference models."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .models import ModelSpec
from .providers.registry import ProviderRegistry, ProviderRegistryError


class ModelRegistryError(RuntimeError):
    """Raised when an inference model cannot be registered or resolved safely."""


class ModelRegistry:
    """Authoritative in-memory catalog of configured inference models."""

    _models: dict[str, ModelSpec]

    def __init__(
        self,
        models: Iterable[ModelSpec] = (),
        *,
        provider_registry: ProviderRegistry | None = None,
    ) -> None:
        self._models = {}
        self._provider_registry = provider_registry
        self.register_many(models)

    @classmethod
    def from_specs(
        cls,
        models: Iterable[ModelSpec],
        *,
        provider_registry: ProviderRegistry | None = None,
    ) -> "ModelRegistry":
        return cls(models, provider_registry=provider_registry)

    @classmethod
    def from_file(
        cls,
        path: str | Path,
        *,
        provider_registry: ProviderRegistry | None = None,
    ) -> "ModelRegistry":
        from .config import load_model_specs

        try:
            specs = load_model_specs(path)
        except Exception as exc:
            if isinstance(exc, ModelRegistryError):
                raise
            raise ModelRegistryError(
                f"unable to load model registry from {Path(path)}"
            ) from exc
        return cls(specs, provider_registry=provider_registry)

    @property
    def models(self) -> tuple[ModelSpec, ...]:
        return tuple(
            self._models[model_id]
            for model_id in sorted(self._models)
        )

    @property
    def all(self) -> tuple[ModelSpec, ...]:
        """Alias for the immutable registered model snapshot."""
        return self.models

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._models))

    def register(self, model: ModelSpec) -> None:
        self._validate_model(model)
        normalized_id = model.id.strip()
        if normalized_id in self._models:
            raise ModelRegistryError(
                f"duplicate model registration: {normalized_id}"
            )
        if self._provider_registry is not None:
            try:
                provider = self._provider_registry.resolve(model.provider)
            except ProviderRegistryError as exc:
                raise ModelRegistryError(str(exc)) from exc
            if not provider.enabled:
                raise ModelRegistryError(
                    f"model {normalized_id!r} references disabled provider: {model.provider}"
                )
        self._models[normalized_id] = model

    def register_many(self, models: Iterable[ModelSpec]) -> None:
        pending = tuple(models)
        for model in pending:
            self.register(model)

    def replace(self, model: ModelSpec) -> None:
        self._validate_model(model)
        normalized_id = model.id.strip()
        if normalized_id not in self._models:
            raise ModelRegistryError(
                f"cannot replace unregistered model: {normalized_id}"
            )
        if self._provider_registry is not None:
            try:
                provider = self._provider_registry.resolve(model.provider)
            except ProviderRegistryError as exc:
                raise ModelRegistryError(str(exc)) from exc
            if not provider.enabled:
                raise ModelRegistryError(
                    f"model {normalized_id!r} references disabled provider: {model.provider}"
                )
        self._models[normalized_id] = model

    def resolve(self, model_id: str) -> ModelSpec:
        normalized_id = model_id.strip()
        if not normalized_id:
            raise ModelRegistryError("model_id is empty")
        try:
            return self._models[normalized_id]
        except KeyError as exc:
            raise ModelRegistryError(
                f"unknown inference model: {normalized_id}"
            ) from exc

    @property
    def catalog(self) -> tuple[dict[str, object], ...]:
        return tuple(
            {
                "model_id": model.id,
                "provider": model.provider,
                "endpoint": model.endpoint,
                "enabled": model.enabled,
                "healthy": model.healthy,
                "available": model.available,
                "capabilities": model.capabilities,
                "specialties": model.specialties,
                "context_window": model.context_window,
                "quality_score": model.quality_score,
                "latency_score": model.latency_score,
                "cost_score": model.cost_score,
                "pricing_tier": model.pricing_tier,
            }
            for model in self.models
        )

    @staticmethod
    def _validate_model(model: ModelSpec) -> None:
        if not isinstance(model, ModelSpec):
            raise ModelRegistryError("model registration requires a ModelSpec")
        if not model.id.strip():
            raise ModelRegistryError("model id is empty")
        if not model.provider.strip():
            raise ModelRegistryError(
                f"model {model.id!r} has an empty provider"
            )
        if not model.endpoint.strip():
            raise ModelRegistryError(
                f"model {model.id!r} has an empty endpoint"
            )
        if model.context_window is not None and model.context_window < 1:
            raise ModelRegistryError(
                f"model {model.id!r} has an invalid context window"
            )
        for field_name, values in (
            ("capabilities", model.capabilities),
            ("specialties", model.specialties),
        ):
            if any(not isinstance(value, str) or not value.strip() for value in values):
                raise ModelRegistryError(
                    f"model {model.id!r} has invalid {field_name}"
                )
        for field_name, value in (
            ("quality_score", model.quality_score),
            ("latency_score", model.latency_score),
            ("cost_score", model.cost_score),
        ):
            if not 0.0 <= float(value) <= 1.0:
                raise ModelRegistryError(
                    f"model {model.id!r} has invalid {field_name}"
                )


__all__ = ["ModelRegistry", "ModelRegistryError"]
