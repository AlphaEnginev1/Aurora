"""Dashboard provider settings for the NVIDIA-only Aurora runtime."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Mapping

from aurora.inference.models import ModelSpec
from aurora.inference.free_policy import is_free_model
from aurora.inference.providers.registry import default_provider_registry
from aurora.inference.discovery import ModelDiscoveryError
from aurora.operator_settings import OperatorSettingsError, OperatorSettingsStore
from aurora.orchestration.director_provider import InferenceDirectorProvider


class DashboardProviderSettingsError(RuntimeError):
    """Raised when NVIDIA provider settings cannot be applied or tested."""


class ProviderSettingsService:
    def __init__(self, store: OperatorSettingsStore, runtime) -> None:
        self.store = store
        self.runtime = runtime

    def public(self) -> dict[str, Any]:
        provider = getattr(self.runtime.director.planner, "provider", None)
        data = self.store.public()
        data["runtime"] = {"director_provider": getattr(provider, "provider_id", "unknown")}
        return data

    def save_and_apply(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        try:
            settings = self.store.update_from_form(payload)
            router = self.runtime.director.planner.router
            nvidia_key = self.store.credential("nvidia_api")
            openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip()
            router.nvidia_api_key = nvidia_key or None
            # Settings saves must not detach inference from Aurora's canonical
            # deny-by-default InternetGateway. The catalog/discovery path is gateway-
            # bound at runtime; replacing this registry without the gateway makes chat
            # use the legacy direct urllib path instead (regression of PR #176).
            network_gateway = getattr(
                getattr(self.runtime, "workbench", None),
                "gateway",
                None,
            )
            router.provider_registry = default_provider_registry(
                nvidia_api_key=nvidia_key or None,
                network_gateway=network_gateway,
                openrouter_api_key=openrouter_key or None,
                include_openrouter=bool(openrouter_key),
            )
            # Refresh must use the new key and the same gateway too; update this
            # reference before selected-model handling can trigger a catalog refresh.
            discovery = getattr(self.runtime, "model_discovery", None)
            if discovery is not None:
                discovery.provider_registry = router.provider_registry

            selected_model = str(settings["director"].get("model", "")).strip()
            provider_id = str(settings["director"].get("provider", "")).strip().lower()
            if provider_id not in {"router", "nvidia_api"}:
                raise DashboardProviderSettingsError(
                    "Aurora supports only the NVIDIA inference fabric"
                )

            if nvidia_key and selected_model and provider_id == "nvidia_api":
                existing = next(
                    (item for item in router.model_registry.models if item.id == selected_model),
                    None,
                )
                if existing is None and hasattr(self.runtime, "model_discovery"):
                    try:
                        refresh_service = getattr(self.runtime, "autonomous_discovery_refresh", None)
                        if refresh_service is not None:
                            refresh_service.refresh_if_due(force=True)
                        else:
                            self.runtime.model_discovery.refresh_all(
                                provider_ids=("nvidia_api",),
                                min_interval_seconds=0,
                                force=True,
                            )
                            self.runtime.model_discovery.activate_eligible_discovered(
                                provider_id="nvidia_api",
                                cost_policy=getattr(router, "cost_policy", "free_only"),
                                health_store=getattr(router, "health_store", None),
                            )
                        existing = next(
                            (item for item in router.model_registry.models if item.id == selected_model),
                            None,
                        )
                    except Exception:
                        existing = None
                if existing is None:
                    if not is_free_model("nvidia_api", selected_model):
                        raise DashboardProviderSettingsError(
                            "selected model is not a currently discovered or explicitly seeded free NVIDIA model"
                        )
                    existing = ModelSpec(
                        id=selected_model,
                        provider="nvidia_api",
                        endpoint=str(settings["providers"]["nvidia_api"]["base_url"]).rstrip("/"),
                        capabilities=("chat", "reasoning", "tool_use"),
                        context_window=131072,
                        enabled=True,
                        pricing_tier="free",
                    )
                    router.model_registry = type(router.model_registry).from_specs(
                        [*router.model_registry.models, existing],
                        provider_registry=router.provider_registry,
                    )
                    router.models = list(router.model_registry.models)
                elif not existing.enabled or existing.endpoint != str(settings["providers"]["nvidia_api"]["base_url"]).rstrip("/"):
                    updated = ModelSpec(
                        id=existing.id,
                        provider=existing.provider,
                        endpoint=str(settings["providers"]["nvidia_api"]["base_url"]).rstrip("/"),
                        capabilities=existing.capabilities or ("chat", "reasoning", "tool_use"),
                        context_window=existing.context_window or 131072,
                        enabled=True,
                        specialties=existing.specialties,
                        quality_score=existing.quality_score,
                        latency_score=existing.latency_score,
                        cost_score=existing.cost_score,
                        healthy=existing.healthy,
                        available=existing.available,
                        pricing_tier=existing.pricing_tier or "free",
                    )
                    router.model_registry.replace(updated)
                    router.models = list(router.model_registry.models)

            # Keep live discovery on the same credential, registry, and gateway
            # as inference after Settings has been saved. ModelDiscoveryService holds
            # registry references from startup, so replacing the router registry alone
            # leaves the catalog path stale.
            discovery = getattr(self.runtime, "model_discovery", None)
            if discovery is not None:
                discovery.provider_registry = router.provider_registry
                discovery.model_registry = router.model_registry

            provider = InferenceDirectorProvider(router)
            active = self.runtime.director.planner.provider
            if hasattr(active, "set_active_provider"):
                active.set_active_provider(provider)
                provider_for_runtime = active
            else:
                self.runtime.director.planner.provider = provider
                provider_for_runtime = provider
            conversation = getattr(self.runtime, "conversation_service", None)
            if conversation is not None:
                conversation.set_provider(provider_for_runtime)
            return self.public()
        except (OperatorSettingsError, KeyError, TypeError, ValueError) as exc:
            raise DashboardProviderSettingsError(str(exc)) from exc

    def test_provider(self, provider_id: str) -> dict[str, Any]:
        provider_id = str(provider_id).strip().lower()
        if provider_id != "nvidia_api":
            raise DashboardProviderSettingsError(
                "Aurora provider test supports only nvidia_api"
            )
        settings = self.store.load()
        key = self.store.credential("nvidia_api")
        if not key:
            raise DashboardProviderSettingsError("NVIDIA API key is not configured")
        discovery = getattr(self.runtime, "model_discovery", None)
        if discovery is None:
            raise DashboardProviderSettingsError(
                "model discovery is not available in the current runtime"
            )
        try:
            candidates = tuple(discovery.discover_and_stage("nvidia_api"))
        except ModelDiscoveryError as exc:
            raise DashboardProviderSettingsError(str(exc)) from exc
        models = [
            candidate.model.id
            for candidate in candidates
            if getattr(candidate, "model", None) is not None
            and getattr(candidate.model, "pricing_tier", "unknown") == "free"
        ]
        return {
            "provider": "nvidia_api",
            "ok": True,
            "models": models,
            "staged_count": len(models),
            "stage_state": "discovered",
        }


def _urlopen(request: urllib.request.Request):
    try:
        return urllib.request.urlopen(request, timeout=20)
    except urllib.error.HTTPError as exc:
        raise DashboardProviderSettingsError(f"{exc.code} provider response") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise DashboardProviderSettingsError(f"provider connection failed: {exc}") from None


__all__ = ["DashboardProviderSettingsError", "ProviderSettingsService"]
