"""Canonical runtime registry for Aurora inference providers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from ..discovery import ModelDiscoveryProvider

from ..models import ModelSpec
from . import InferenceProvider
from .catalog import ProviderCatalog, ProviderCatalogError


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


def default_provider_registry(
    *,
    nvidia_api_key: str | None = None,
    openai_api_key: str | None = None,
    network_gateway=None,
    anthropic_api_key: str | None = None,
    include_external: bool = False,
    openrouter_api_key: str | None = None,
    include_openrouter: bool = False,
    catalog: ProviderCatalog | None = None,
) -> ProviderRegistry:
    """Build the active registry from the validated provider catalog.

    The catalog decides provider metadata and activation gates. Python retains
    an explicit allow-list of adapter implementations; catalog data can never
    trigger dynamic imports or arbitrary code execution.
    """
    try:
        provider_catalog = catalog or ProviderCatalog()
    except ProviderCatalogError as exc:
        raise ProviderRegistryError(f"provider catalog is invalid: {exc}") from exc

    providers: list[ProviderSpec] = []
    for provider_id in provider_catalog.provider_ids:
        metadata = provider_catalog.resolve(provider_id)
        mode = metadata["registry_mode"]
        if mode == "default":
            should_register = True
        elif mode == "include_external":
            should_register = bool(include_external)
        elif mode == "include_openrouter":
            should_register = bool(include_openrouter)
        elif mode == "catalog_only":
            should_register = False
        else:
            raise ProviderRegistryError(f"unsupported registry mode in catalog: {provider_id}")

        if not should_register:
            continue
        if not metadata["adapter_available"] or not metadata["adapter"]:
            raise ProviderRegistryError(
                f"provider {provider_id} is enabled for runtime but has no implemented adapter"
            )

        adapter = metadata["adapter"]
        discovery_factory = None
        discovery = metadata["discovery"]
        if discovery["enabled"]:
            if provider_id != "nvidia_api" or discovery["adapter"] != "nvidia":
                raise ProviderRegistryError(
                    f"unsupported discovery adapter declared for provider: {provider_id}"
                )
            endpoint = metadata["base_url"]
            discovery_factory = (
                lambda api_key=nvidia_api_key, gateway=network_gateway, base_url=endpoint:
                    _nvidia_discovery_factory(
                        api_key=api_key,
                        gateway=gateway,
                        base_url=base_url,
                    )
            )

        if adapter == "nvidia_api":
            if provider_id != "nvidia_api":
                raise ProviderRegistryError("nvidia_api adapter can only serve nvidia_api")

            def factory(model: ModelSpec, api_key=nvidia_api_key, gateway=network_gateway) -> InferenceProvider:
                from .nvidia_api import NVIDIAAPIProvider
                return NVIDIAAPIProvider(model, api_key=api_key, gateway=gateway)

        elif adapter == "openai_api":
            provider_id_override = metadata.get("provider_id_override")
            if provider_id_override is not None and provider_id_override != provider_id:
                raise ProviderRegistryError(
                    f"provider ID override does not match catalog entry: {provider_id}"
                )
            if provider_id_override == "openrouter":
                provider_api_key = openrouter_api_key
            else:
                provider_api_key = openai_api_key

            def factory(
                model: ModelSpec,
                api_key=provider_api_key,
                override=provider_id_override,
            ) -> InferenceProvider:
                from .openai_api import OpenAIAPIProvider
                if override is None:
                    return OpenAIAPIProvider(model, api_key=api_key)
                return OpenAIAPIProvider(model, api_key=api_key, provider_id=override)

        elif adapter == "anthropic_api":
            if provider_id != "anthropic":
                raise ProviderRegistryError("anthropic_api adapter can only serve anthropic")

            def factory(model: ModelSpec, api_key=anthropic_api_key) -> InferenceProvider:
                from .anthropic_api import AnthropicAPIProvider
                return AnthropicAPIProvider(model, api_key=api_key)

        else:
            raise ProviderRegistryError(
                f"provider {provider_id} declares an unapproved adapter: {adapter}"
            )

        providers.append(
            ProviderSpec(
                provider_id=provider_id,
                name=metadata["name"],
                capabilities=tuple(metadata["capabilities"]),
                enabled=True,
                factory=factory,
                discovery_factory=discovery_factory,
            )
        )

    return ProviderRegistry(tuple(providers))


def _nvidia_discovery_factory(
    *,
    api_key: str | None,
    gateway=None,
    base_url: str = "https://integrate.api.nvidia.com/v1",
) -> "ModelDiscoveryProvider":
    from .nvidia_discovery import NVIDIAModelDiscoveryProvider
    return NVIDIAModelDiscoveryProvider(
        base_url=base_url,
        api_key=api_key,
        gateway=gateway,
    )

__all__ = [
    "DiscoveryFactory",
    "ProviderFactory",
    "ProviderRegistry",
    "ProviderRegistryError",
    "ProviderSpec",
    "default_provider_registry",
]
