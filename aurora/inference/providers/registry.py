"""Canonical runtime registry for Aurora inference providers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from ..discovery import ModelDiscoveryProvider

from ..models import ModelSpec
from . import InferenceProvider


class ProviderRegistryError(RuntimeError):
    """Raised when an inference provider cannot be resolved safely."""


ProviderFactory = Callable[[ModelSpec], InferenceProvider]
DiscoveryFactory = Callable[[], "ModelDiscoveryProvider"]


@dataclass(frozen=True)
class ProviderSpec:
    """Immutable provider registration independent from individual models."""

    provider_id: str
    name: str
    capabilities: tuple[str, ...]
    enabled: bool
    factory: ProviderFactory
    discovery_factory: DiscoveryFactory | None = None


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
            raise ProviderRegistryError(f"provider {provider_id!r} has an empty name")
        if provider_id in self._providers:
            raise ProviderRegistryError(f"duplicate provider registration: {provider_id}")
        if not callable(provider.factory):
            raise ProviderRegistryError(f"provider {provider_id!r} factory is not callable")
        if provider.discovery_factory is not None and not callable(provider.discovery_factory):
            raise ProviderRegistryError(f"provider {provider_id!r} discovery factory is not callable")
        capabilities = tuple(sorted({c.strip() for c in provider.capabilities if c.strip()}))
        self._providers[provider_id] = ProviderSpec(
            provider_id=provider_id,
            name=provider.name.strip(),
            capabilities=capabilities,
            enabled=bool(provider.enabled),
            factory=provider.factory,
            discovery_factory=provider.discovery_factory,
        )

    def resolve(self, provider_id: str) -> ProviderSpec:
        normalized = provider_id.strip()
        if not normalized:
            raise ProviderRegistryError("provider_id is empty")
        try:
            return self._providers[normalized]
        except KeyError as exc:
            raise ProviderRegistryError(f"unknown inference provider: {normalized}") from exc

    def create(self, model: ModelSpec) -> InferenceProvider:
        provider = self.resolve(model.provider)
        if not provider.enabled:
            raise ProviderRegistryError(f"inference provider is disabled: {provider.provider_id}")
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
                f"provider factory returned mismatched provider identity: expected {provider.provider_id!r}"
            )
        return instance

    def create_discovery(self, provider_id: str) -> "ModelDiscoveryProvider":
        provider = self.resolve(provider_id)
        if not provider.enabled:
            raise ProviderRegistryError(f"inference provider is disabled: {provider.provider_id}")
        if provider.discovery_factory is None:
            raise ProviderRegistryError(
                f"provider does not support model discovery: {provider.provider_id}"
            )
        try:
            return provider.discovery_factory()
        except Exception as exc:
            raise ProviderRegistryError(
                f"failed to construct discovery adapter for {provider.provider_id}: {exc}"
            ) from exc

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
                "discovery_supported": provider.discovery_factory is not None,
            }
            for provider in sorted(self._providers.values(), key=lambda item: item.provider_id)
        )


def default_provider_registry(*, nvidia_api_key: str | None = None) -> ProviderRegistry:
    """Build Aurora's NVIDIA-only canonical provider registry."""

    def nvidia_factory(model: ModelSpec) -> InferenceProvider:
        from .nvidia_api import NVIDIAAPIProvider
        return NVIDIAAPIProvider(model, api_key=nvidia_api_key)

    return ProviderRegistry((
        ProviderSpec(
            "nvidia_api",
            "NVIDIA NIM API",
            ("chat", "reasoning"),
            True,
            nvidia_factory,
            discovery_factory=lambda: _nvidia_discovery_factory(api_key=nvidia_api_key),
        ),
    ))


def _nvidia_discovery_factory(*, api_key: str | None) -> "ModelDiscoveryProvider":
    from .nvidia_discovery import NVIDIAModelDiscoveryProvider
    return NVIDIAModelDiscoveryProvider(
        base_url="https://integrate.api.nvidia.com/v1",
        api_key=api_key,
    )


__all__ = [
    "DiscoveryFactory",
    "ProviderFactory",
    "ProviderRegistry",
    "ProviderRegistryError",
    "ProviderSpec",
    "default_provider_registry",
]
