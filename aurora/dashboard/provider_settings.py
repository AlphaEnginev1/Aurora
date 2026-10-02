"""Dashboard-facing provider settings with runtime reconfiguration."""
from __future__ import annotations
import json, urllib.error, urllib.request
from typing import Any, Mapping
from aurora.inference.providers.registry import default_provider_registry
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
        provider = getattr(self.runtime.planner, "provider", None)
        data = self.store.public()
        data["runtime"] = {"director_provider": getattr(provider, "provider_id", "unknown")}
        return data

    def save_and_apply(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        try:
            settings = self.store.update_from_form(payload)
            router = self.runtime.planner.router
            nvidia_key = self.store.credential("nvidia_api")
            router.nvidia_api_key = nvidia_key or None
            router.provider_registry = default_provider_registry(nvidia_api_key=nvidia_key or None)
            director = settings["director"]
            provider_id = str(director.get("provider", "")).strip().lower()
            if provider_id in {"router", "nvidia_api"}:
                provider = InferenceDirectorProvider(router)
            elif provider_id == "gemini":
                api_key = self.store.credential("gemini")
                if not api_key:
                    raise DashboardProviderSettingsError("Gemini API key is required before selecting Gemini as Director")
                provider = GeminiDirectorProvider(api_key=api_key, model=str(director["model"]), endpoint=str(director["endpoint"]))
            else:
                raise DashboardProviderSettingsError(f"unsupported Director provider: {provider_id}")
            self.runtime.planner.provider = provider
            conversation = getattr(self.runtime, "conversation_service", None)
            if conversation is not None:
                conversation.set_provider(provider)
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
        if provider_id == "nvidia_api":
            key = self.store.credential("nvidia_api")
            if not key:
                raise DashboardProviderSettingsError("NVIDIA API key is not configured")
            endpoint = str(settings["providers"]["nvidia_api"]["base_url"]).rstrip("/")
            request = urllib.request.Request(f"{endpoint}/models", headers={"Authorization": f"Bearer {key}", "Accept": "application/json"}, method="GET")
            with _urlopen(request) as response:
                payload = json.loads(response.read().decode("utf-8"))
            rows = payload.get("data", []) if isinstance(payload, dict) else []
            return {"provider":"nvidia_api","ok":True,"models":[x.get("id") for x in rows if isinstance(x, Mapping) and isinstance(x.get("id"),str)][:100]}
        raise DashboardProviderSettingsError("provider test supports gemini or nvidia_api")

def _urlopen(request: urllib.request.Request):
    try:
        return urllib.request.urlopen(request, timeout=20)
    except urllib.error.HTTPError as exc:
        raise DashboardProviderSettingsError(f"{exc.code} provider response") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise DashboardProviderSettingsError(f"provider connection failed: {exc}") from None

__all__ = ["DashboardProviderSettingsError","ProviderSettingsService"]