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
from .provider_health import AvailabilityState, ProviderHealthSnapshot, ProviderHealthStore, classify_failure
from .ensemble import CriticalDecisionResult, EnsembleMemberResult, ModelEnsemble, ModelEnsembleError, ModelEnsembleStore
from .providers.registry import ProviderRegistry, ProviderRegistryError, ProviderSpec

__all__ = ["InferenceRequest", "InferenceResponse", "ModelRouter", "ModelSpec", "ModelRegistry", "ModelRegistryError", "DiscoveredModelCandidate", "ModelCandidateStore", "ModelDiscoveryError", "ModelDiscoveryProvider", "ModelDiscoveryService", "ModelPromotionValidation", "ProviderRegistry", "ProviderRegistryError", "ProviderSpec", "AvailabilityState", "ProviderHealthSnapshot", "ProviderHealthStore", "classify_failure", "CriticalDecisionResult", "EnsembleMemberResult", "ModelEnsemble", "ModelEnsembleError", "ModelEnsembleStore"]
