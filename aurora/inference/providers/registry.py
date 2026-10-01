"""Canonical runtime registry for Aurora inference providers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..models import ModelSpec
from . import InferenceProvider


class ProviderRegistryError(RuntimeError):
    """Raised when an inference provider cannot be resolved safely."""


ProviderFactory = Callable[[ModelSpec], InferenceProvider]


@dataclass(frozen=True)
class ProviderSpec:
    """Immutable provider registration independent from individual models."""

    provider_id: str
    name: str
    capabilities: tuple[str, ...]
    enabled: bool
    factory: ProviderFactory


class ProviderRegistry:
    """Resolve provider implementations by stable provider identity."""

    def __init__(self, providers: tuple[ProviderSpec, ...] = ()) -> None:
        self._providers: dict[str, ProviderSpec] = {}
        for provider in providers:
            self.register(provider)

    def register(self, provider: ProviderSpec) -> None:
        provider_id = provider.provider_id.strip()
        if not provider_id:
            raise ProviderRegistryError("provider_id is empty")
        if not provider.name.strip():
            raise ProviderRegistryError(
                f"provider {provider_id!r} has an empty name"
            )
        if provider_id in self._providers:
            raise ProviderRegistryError(
                f"duplicate provider registration: {provider_id}"
            )
        if not callable(provider.factory):
            raise ProviderRegistryError(
                f"provider {provider_id!r} factory is not callable"
            )
        capabilities = tuple(
            sorted(
                {
                    capability.strip()
                    for capability in provider.capabilities
                    if capability.strip()
                }
            )
        )
        self._providers[provider_id] = ProviderSpec(
            provider_id=provider_id,
            name=provider.name.strip(),
            capabilities=capabilities,
            enabled=bool(provider.enabled),
            factory=provider.factory,
        )

    def resolve(self, provider_id: str) -> ProviderSpec:
        normalized = provider_id.strip()
        if not normalized:
            raise ProviderRegistryError("provider_id is empty")
        try:
            return self._providers[normalized]
        except KeyError as exc:
            raise ProviderRegistryError(
                f"unknown inference provider: {normalized}"
            ) from exc

    def create(self, model: ModelSpec) -> InferenceProvider:
        provider = self.resolve(model.provider)
        if not provider.enabled:
            raise ProviderRegistryError(
                f"inference provider is disabled: {provider.provider_id}"
            )
        try:
            instance = provider.factory(model)
        except ProviderRegistryError:
            raise
        except Exception as exc:
            raise ProviderRegistryError(
                f"failed to construct provider {provider.provider_id}: {exc}"
            ) from exc
        if getattr(instance, "provider_id", None) != provider.provider_id:
            raise ProviderRegistryError(
                f"provider factory returned mismatched provider identity: "
                f"expected {provider.provider_id!r}"
            )
        return instance

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._providers))

    @property
    def catalog(self) -> tuple[dict[str, object], ...]:
        return tuple(
            {
                "provider_id": provider.provider_id,
                "name": provider.name,
                "enabled": provider.enabled,
                "capabilities": provider.capabilities,
            }
            for provider in sorted(
                self._providers.values(),
                key=lambda item: item.provider_id,
            )
        )


def default_provider_registry(*, nvidia_api_key: str | None = None) -> ProviderRegistry:
    """Build Aurora's current canonical provider registry.

    Provider adapters are imported lazily so registry initialization never
    creates an inference-package import cycle.
    """

    def nvidia_factory(model: ModelSpec) -> InferenceProvider:
        from .nvidia_api import NVIDIAAPIProvider

        return NVIDIAAPIProvider(model, api_key=nvidia_api_key)

    return ProviderRegistry(
        (
            ProviderSpec(
                provider_id="nvidia_api",
                name="NVIDIA NIM API",
                capabilities=("chat", "reasoning"),
                enabled=True,
                factory=nvidia_factory,
            ),
        )
    )


__all__ = [
    "ProviderFactory",
    "ProviderRegistry",
    "ProviderRegistryError",
    "ProviderSpec",
    "default_provider_registry",
]
