"""Common provider execution boundary."""

from __future__ import annotations

from typing import Protocol

from ..models import InferenceRequest, InferenceResponse


class InferenceProvider(Protocol):
    """Common adapter contract implemented by every model provider."""

    provider_id: str

    def generate(
        self,
        request: InferenceRequest,
        *,
        selected_model: str,
        execution_id: str,
    ) -> InferenceResponse:
        """Execute one request and preserve request/execution/model identity."""
        ...


from .registry import (
    DiscoveryFactory,
    ProviderFactory,
    ProviderRegistry,
    ProviderRegistryError,
    ProviderSpec,
    default_provider_registry,
)

__all__ = [
    "InferenceProvider",
    "DiscoveryFactory",
    "ProviderFactory",
    "ProviderRegistry",
    "ProviderRegistryError",
    "ProviderSpec",
    "default_provider_registry",
]
