"""Aurora inference fabric public API."""

from .models import InferenceRequest, InferenceResponse, ModelSpec
from .registry import ModelRegistry, ModelRegistryError
from .discovery import (
    DiscoveredModelCandidate,
    ModelCandidateStore,
    ModelDiscoveryError,
    ModelDiscoveryProvider,
    ModelDiscoveryService,
    ModelPromotionValidation,
)
from .router import ModelRouter
from .reserve_pool import DynamicReservePool, DynamicReservePoolError, ReserveCandidate
from .autonomous_discovery import AutonomousDiscoveryRefreshReport, AutonomousDiscoveryRefreshService, DiscoveryProviderRefreshReport
from .provider_health import AvailabilityState, ProviderAvailabilitySnapshot, ProviderHealthSnapshot, ProviderHealthStore, classify_failure
from .ensemble import CriticalDecisionResult, EnsembleMemberResult, ModelEnsemble, ModelEnsembleError, ModelEnsembleStore
from .providers.catalog import ProviderCatalog, ProviderCatalogError
from .providers.registry import ProviderRegistry, ProviderRegistryError, ProviderSpec

__all__ = ["InferenceRequest", "InferenceResponse", "ModelRouter", "DynamicReservePool", "DynamicReservePoolError", "ReserveCandidate", "ModelSpec", "ModelRegistry", "ModelRegistryError", "DiscoveredModelCandidate", "ModelCandidateStore", "ModelDiscoveryError", "ModelDiscoveryProvider", "ModelDiscoveryService", "ModelPromotionValidation", "AutonomousDiscoveryRefreshReport", "AutonomousDiscoveryRefreshService", "DiscoveryProviderRefreshReport", "ProviderCatalog", "ProviderCatalogError", "ProviderRegistry", "ProviderRegistryError", "ProviderSpec", "AvailabilityState", "ProviderAvailabilitySnapshot", "ProviderHealthSnapshot", "ProviderHealthStore", "classify_failure", "CriticalDecisionResult", "EnsembleMemberResult", "ModelEnsemble", "ModelEnsembleError", "ModelEnsembleStore"]
