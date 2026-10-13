"""Dashboard provider settings for the NVIDIA-only Aurora runtime."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Mapping

from aurora.inference.models import ModelSpec
from aurora.inference.free_policy import is_free_model
from aurora.inference.providers.catalog import ProviderCatalog
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
        self.catalog = ProviderCatalog()

    def public(self) -> dict[str, Any]:
        provider = getattr(self.runtime.director.planner, "provider", None)
        data = self.store.public()
        data["provider_catalog"] = list(self.catalog.providers)
        data["runtime"] = {"director_provider": getattr(provider, "provider_id", "unknown")}
        return data

    def save_and_apply(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        try:
            settings = self.store.update_from_form(payload)
            router = self.runtime.director.planner.router
            configured_providers = settings.get("providers", {})

            def configured_key(provider_id: str) -> str:
                config = configured_providers.get(provider_id, {})
                if isinstance(config, Mapping) and config.get("enabled", True) is False:
                    return ""
                try:
                    metadata = self.catalog.resolve(provider_id)
                except ValueError:
                    return ""
                stored = self.store.credential(provider_id)
                env_name = metadata.get("credential_env")
                environment = os.getenv(env_name, "").strip() if isinstance(env_name, str) else ""
                return stored or environment

            nvidia_key = configured_key("nvidia_api") or os.getenv("NVIDIA_API_KEY", "").strip()
            openai_key = configured_key("openai")
            anthropic_key = configured_key("anthropic")
            openrouter_key = configured_key("openrouter")
            router.nvidia_api_key = nvidia_key or None
            router.provider_registry = default_provider_registry(
                nvidia_api_key=nvidia_key or None,
                openai_api_key=openai_key or None,
                anthropic_api_key=anthropic_key or None,
                openrouter_api_key=openrouter_key or None,
                include_external=bool(openai_key or anthropic_key),
                include_openrouter=bool(openrouter_key),
                catalog=self.catalog,
            )

            # The model registry validates providers when models are registered.
            # Rebind it to the freshly built provider registry and drop models
            # whose credentials/provider are no longer active.
            current_models = [
                model for model in router.model_registry.models
                if model.provider in router.provider_registry.ids
            ]
            router.model_registry = type(router.model_registry).from_specs(
                current_models,
                provider_registry=router.provider_registry,
            )
            _rebind_runtime_discovery(self.runtime, router)

            if openrouter_key:
                from aurora.orchestration.factory import _register_openrouter_free_model

                openrouter_config = configured_providers.get("openrouter", {})
                endpoint = (
                    str(openrouter_config.get("base_url", "")).strip()
                    if isinstance(openrouter_config, Mapping)
                    else ""
                ) or str(self.catalog.resolve("openrouter")["base_url"])
                _register_openrouter_free_model(
                    router.model_registry,
                    openrouter_key,
                    endpoint=endpoint,
                )
                existing_openrouter = next(
                    (model for model in router.model_registry.models if model.id == "openrouter/free"),
                    None,
                )
                if existing_openrouter is not None and existing_openrouter.endpoint != endpoint.rstrip("/"):
                    router.model_registry.replace(ModelSpec(
                        id=existing_openrouter.id,
                        provider=existing_openrouter.provider,
                        endpoint=endpoint.rstrip("/"),
                        capabilities=existing_openrouter.capabilities,
                        context_window=existing_openrouter.context_window,
                        enabled=existing_openrouter.enabled,
                        specialties=existing_openrouter.specialties,
                        quality_score=existing_openrouter.quality_score,
                        latency_score=existing_openrouter.latency_score,
                        cost_score=existing_openrouter.cost_score,
                        healthy=existing_openrouter.healthy,
                        available=existing_openrouter.available,
                        pricing_tier=existing_openrouter.pricing_tier,
                    ))

            router.models = list(router.model_registry.models)
            selected_model = str(settings["director"].get("model", "")).strip()
            provider_id = str(settings["director"].get("provider", "")).strip().lower()
            if provider_id not in {"router", "nvidia_api"}:
                raise DashboardProviderSettingsError(
                    "Aurora supports only the NVIDIA inference fabric"
                )

            if nvidia_key and selected_model and selected_model != "router":
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
                    _rebind_runtime_discovery(self.runtime, router)
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


def _rebind_runtime_discovery(runtime, router) -> None:
    """Keep all long-lived discovery services on the router's current registries.

    Settings updates rebuild ModelRegistry and ProviderRegistry so newly entered
    credentials are captured by provider factories. Discovery and autonomous
    refresh retain references to their original objects unless they are rebound.
    Without this, a key saved in Settings can be ignored by NVIDIA model discovery
    until a complete process restart.
    """
    discovery = getattr(runtime, "model_discovery", None)
    if discovery is not None:
        discovery.provider_registry = router.provider_registry
        discovery.model_registry = router.model_registry

    refresh = getattr(runtime, "autonomous_discovery_refresh", None)
    if refresh is not None:
        if discovery is not None:
            refresh.discovery = discovery
        refresh.router = router
        if hasattr(router, "health_store"):
            refresh.health_store = router.health_store


def _urlopen(request: urllib.request.Request):
    try:
        return urllib.request.urlopen(request, timeout=20)
    except urllib.error.HTTPError as exc:
        raise DashboardProviderSettingsError(f"{exc.code} provider response") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise DashboardProviderSettingsError(f"provider connection failed: {exc}") from None


__all__ = ["DashboardProviderSettingsError", "ProviderSettingsService"]
