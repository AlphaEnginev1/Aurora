"""Dashboard-facing provider settings with runtime reconfiguration."""
from __future__ import annotations
import json, urllib.error, urllib.request
from typing import Any, Mapping
from aurora.inference.models import ModelSpec
from aurora.inference.free_policy import is_free_model
from aurora.inference.providers.registry import default_provider_registry
from aurora.inference.discovery import ModelDiscoveryError
from aurora.operator_settings import OperatorSettingsError, OperatorSettingsStore
from aurora.orchestration.director_provider import InferenceDirectorProvider
from aurora.orchestration.gemini_provider import GeminiDirectorProvider

class DashboardProviderSettingsError(RuntimeError):
    """Raised when provider settings cannot be applied or tested."""

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
            router.nvidia_api_key = nvidia_key or None
            router.provider_registry = default_provider_registry(
                nvidia_api_key=nvidia_key or None,
            )

            selected_model = str(settings["director"].get("model", "")).strip()
            if (
                nvidia_key
                and str(settings["director"].get("provider", "")).strip().lower() == "nvidia_api"
                and selected_model
            ):
                existing = next(
                    (item for item in router.model_registry.models if item.id == selected_model),
                    None,
                )
                if existing is None and hasattr(self.runtime, "model_discovery"):
                    try:
                        self.runtime.model_discovery.refresh_all(
                            provider_ids=("nvidia_api",),
                            min_interval_seconds=0,
                            force=True,
                        )
                        self.runtime.model_discovery.activate_free_discovered(
                            provider_id="nvidia_api"
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
                            "free_only policy requires a currently discovered or explicitly seeded free NVIDIA model"
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

            director = settings["director"]
            provider_id = str(director.get("provider", "")).strip().lower()
            if provider_id in {"router", "nvidia_api"}:
                provider = InferenceDirectorProvider(router)
            elif provider_id in {"openai", "anthropic"}:
                if getattr(router, "cost_policy", "all") == "free_only":
                    raise DashboardProviderSettingsError(f"{provider_id} is disabled by Aurora free_only policy")
                key = self.store.credential(provider_id)
                if not key:
                    raise DashboardProviderSettingsError(
                        f"{provider_id} API key is required before selecting it as Director"
                    )
                from aurora.orchestration.director_provider_failover import BoundInferenceDirectorProvider
                provider = BoundInferenceDirectorProvider(
                    router, provider_id=provider_id, model=str(director["model"])
                )
            elif provider_id == "gemini":
                api_key = self.store.credential("gemini")
                if not api_key:
                    raise DashboardProviderSettingsError("Gemini API key is required before selecting Gemini as Director")
                provider = GeminiDirectorProvider(api_key=api_key, model=str(director["model"]), endpoint=str(director["endpoint"]))
            else:
                raise DashboardProviderSettingsError(f"unsupported Director provider: {provider_id}")
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
        settings = self.store.load()
        if provider_id == "gemini":
            key = self.store.credential("gemini")
            if not key:
                raise DashboardProviderSettingsError("Gemini API key is not configured")
            model = str(settings["director"]["model"]).strip()
            endpoint = str(settings["director"]["endpoint"]).rstrip("/")
            request = urllib.request.Request(f"{endpoint}/models/{model}", headers={"x-goog-api-key": key, "Accept": "application/json"}, method="GET")
            with _urlopen(request) as response:
                json.loads(response.read().decode("utf-8"))
            return {"provider":"gemini","ok":True,"model":model}
        if provider_id in {"nvidia_api"}:
            key = self.store.credential(provider_id)
            if not key:
                raise DashboardProviderSettingsError(f"{provider_id} API key is not configured")
            discovery = getattr(self.runtime, "model_discovery", None)
            if discovery is None:
                raise DashboardProviderSettingsError("model discovery is not available in the current runtime")
            try:
                candidates = tuple(discovery.discover_and_stage(provider_id))
            except ModelDiscoveryError as exc:
                raise DashboardProviderSettingsError(str(exc)) from exc
            models = [
                candidate.model.id
                for candidate in candidates
                if getattr(candidate, "model", None) is not None
                and getattr(candidate.model, "pricing_tier", "unknown") == "free"
            ]
            return {
                "provider": provider_id,
                "ok": True,
                "models": models[:100],
                "staged_count": len(models),
                "stage_state": "discovered",
            }
        raise DashboardProviderSettingsError(
            "provider test supports gemini or nvidia_api"
        )

def _urlopen(request: urllib.request.Request):
    try:
        return urllib.request.urlopen(request, timeout=20)
    except urllib.error.HTTPError as exc:
        raise DashboardProviderSettingsError(f"{exc.code} provider response") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise DashboardProviderSettingsError(f"provider connection failed: {exc}") from None

__all__ = ["DashboardProviderSettingsError","ProviderSettingsService"]