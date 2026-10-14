"""Dashboard provider settings for the NVIDIA-only Aurora runtime."""

from __future__ import annotations

import json
from dataclasses import replace as dataclass_replace
import os
import urllib.error
import urllib.request
from typing import Any, Mapping
from urllib.parse import urlparse

from aurora.inference.errors import ProviderResponseError
from aurora.inference.models import InferenceRequest, ModelSpec, new_execution_id
from aurora.inference.free_policy import is_free_model
from aurora.inference.providers.catalog import ProviderCatalog
from aurora.inference.providers.openrouter_quota import OpenRouterQuotaError, read_openrouter_free_quota
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

            nvidia_key = configured_key("nvidia_api")
            openai_key = configured_key("openai")
            anthropic_key = configured_key("anthropic")
            openrouter_key = configured_key("openrouter")
            provider_endpoints: dict[str, str] = {}
            for configured_provider_id in self.catalog.provider_ids:
                configured = configured_providers.get(configured_provider_id, {})
                saved_endpoint = (
                    str(configured.get("base_url", "")).strip()
                    if isinstance(configured, Mapping)
                    else ""
                )
                catalog_endpoint = str(
                    self.catalog.resolve(configured_provider_id).get("base_url", "")
                ).strip()
                endpoint = saved_endpoint or catalog_endpoint
                if endpoint:
                    provider_endpoints[configured_provider_id] = endpoint
            router.nvidia_api_key = nvidia_key or None
            network_gateway = getattr(
                getattr(self.runtime, "workbench", None), "gateway", None
            )
            router.provider_registry = default_provider_registry(
                nvidia_api_key=nvidia_key or None,
                openai_api_key=openai_key or None,
                anthropic_api_key=anthropic_key or None,
                network_gateway=network_gateway,
                openrouter_api_key=openrouter_key or None,
                include_external=bool(openai_key or anthropic_key),
                include_openrouter=bool(openrouter_key),
                catalog=self.catalog,
                provider_endpoints=provider_endpoints,
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
        try:
            metadata = self.catalog.resolve(provider_id)
        except ValueError as exc:
            raise DashboardProviderSettingsError("provider is not present in the validated catalog") from exc

        settings = self.store.load()
        provider_configs = settings.get("providers", {})
        config = provider_configs.get(provider_id, {}) if isinstance(provider_configs, Mapping) else {}
        if isinstance(config, Mapping) and config.get("enabled", True) is False:
            raise DashboardProviderSettingsError(f"{provider_id} is disabled in Settings")

        key = self.store.credential(provider_id)
        env_name = metadata.get("credential_env")
        if not key and isinstance(env_name, str) and env_name:
            key = os.getenv(env_name, "").strip()
        if metadata.get("auth_required") is True and not key:
            raise DashboardProviderSettingsError(f"{provider_id} API key is not configured")

        # The NVIDIA check uses Aurora's existing governed discovery path.
        if provider_id == "nvidia_api":
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
                "connection_test": "authenticated_model_discovery",
                "inference_verified": False,
                "models": models,
                "staged_count": len(models),
                "stage_state": "discovered",
            }

        if not metadata.get("adapter_available") or metadata.get("registry_mode") == "catalog_only":
            raise DashboardProviderSettingsError(
                f"{metadata.get('name', provider_id)} credentials can be stored, "
                "but its runtime adapter is not implemented yet"
            )
        if metadata.get("adapter") not in {"openai_api", "anthropic_api"}:
            raise DashboardProviderSettingsError(
                f"{provider_id} does not have a supported connection-test adapter"
            )

        endpoint = (
            str(config.get("base_url", "")).strip()
            if isinstance(config, Mapping)
            else ""
        ) or str(metadata.get("base_url", "")).strip()
        parsed = urlparse(endpoint)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise DashboardProviderSettingsError(
                f"{provider_id} endpoint must be a valid HTTPS base URL without embedded credentials or query parameters"
            )

        if provider_id == "anthropic":
            url = endpoint.rstrip("/") + "/models?limit=1"
            headers = {
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "Accept": "application/json",
            }
        else:
            suffix = "/models?limit=10" if provider_id == "openrouter" else "/models"
            url = endpoint.rstrip("/") + suffix
            headers = {
                "Authorization": f"Bearer {key}",
                "Accept": "application/json",
            }

        gateway = getattr(getattr(self.runtime, "workbench", None), "gateway", None)
        try:
            if gateway is not None:
                response = gateway.request_with_metadata(url, method="GET", headers=headers)
                status_code = int(getattr(response, "status_code", 200))
                body = response.body
            else:
                request = urllib.request.Request(url, method="GET", headers=headers)
                with _urlopen(request) as response:
                    status_code = int(getattr(response, "status", 200))
                    body = response.read()
        except DashboardProviderSettingsError as exc:
            # _urlopen already converts HTTP and transport errors into safe diagnostics.
            # Preserve those messages; they contain no response body or credential.
            raise DashboardProviderSettingsError(
                f"{provider_id} connection test failed: {exc}"
            ) from None
        except Exception as exc:
            cause = getattr(exc, "__cause__", None)
            status = getattr(cause, "code", None)
            if status is None:
                status = getattr(exc, "code", None)
            if isinstance(status, int):
                raise DashboardProviderSettingsError(
                    f"{provider_id} connection test failed: HTTP {status}"
                ) from None
            raise DashboardProviderSettingsError(
                f"{provider_id} connection test failed: network or gateway policy error"
            ) from None

        if len(body) > 2 * 1024 * 1024:
            raise DashboardProviderSettingsError(
                f"{provider_id} model-list response exceeded the 2 MiB safety limit"
            )
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise DashboardProviderSettingsError(
                f"{provider_id} returned a non-JSON model-list response"
            ) from None
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise DashboardProviderSettingsError(
                f"{provider_id} response did not match the supported model-list contract"
            )

        model_ids = [
            str(item["id"])
            for item in payload["data"]
            if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip()
        ]
        return {
            "provider": provider_id,
            "ok": True,
            "connection_test": "authenticated_model_list",
            "http_status": status_code,
            "model_count": len(model_ids),
            "models": model_ids[:10],
            "inference_verified": False,
        }


    def test_inference(self, provider_id: str) -> dict[str, Any]:
        """Run one minimal real completion using an explicitly free provider route.

        This is intentionally narrower than the connection test. It never submits
        a paid OpenAI/Anthropic model, and it does not claim a full Director mission
        has been verified.
        """
        provider_id = str(provider_id).strip().lower()
        if provider_id not in {"nvidia_api", "openrouter"}:
            raise DashboardProviderSettingsError(
                "Live inference checks are currently enabled only for NVIDIA Free Endpoint "
                "models and OpenRouter's official free router. Other providers can be "
                "connection-tested, but must not be invoked without a verified free-model policy."
            )

        try:
            metadata = self.catalog.resolve(provider_id)
        except ValueError:
            raise DashboardProviderSettingsError("provider is not present in the validated catalog") from None

        settings = self.store.load()
        configs = settings.get("providers", {})
        config = configs.get(provider_id, {}) if isinstance(configs, Mapping) else {}
        if isinstance(config, Mapping) and config.get("enabled", True) is False:
            raise DashboardProviderSettingsError(f"{provider_id} is disabled in Settings")

        key = self.store.credential(provider_id)
        env_name = metadata.get("credential_env")
        if not key and isinstance(env_name, str) and env_name:
            key = os.getenv(env_name, "").strip()
        if metadata.get("auth_required") is True and not key:
            raise DashboardProviderSettingsError(f"{provider_id} API key is not configured")

        router = self.runtime.director.planner.router
        registry = getattr(router, "provider_registry", None)
        if registry is None or provider_id not in registry.ids:
            raise DashboardProviderSettingsError(
                f"{provider_id} is not active in the runtime registry; save its credential and reload Settings"
            )

        openrouter_quota: dict[str, int | float | bool] | None = None
        if provider_id == "openrouter":
            network_gateway = getattr(
                getattr(self.runtime, "workbench", None), "gateway", None
            )
            if network_gateway is None:
                raise DashboardProviderSettingsError(
                    "OpenRouter quota preflight requires Aurora's controlled InternetGateway; no inference request was sent"
                )
            try:
                openrouter_quota = read_openrouter_free_quota(
                    key, gateway=network_gateway
                )
            except OpenRouterQuotaError as exc:
                raise DashboardProviderSettingsError(str(exc)) from None

        discovered_models: list[str] = []
        if provider_id == "nvidia_api":
            discovery = getattr(self.runtime, "model_discovery", None)
            if discovery is None:
                raise DashboardProviderSettingsError("NVIDIA model discovery is not available in the current runtime")
            try:
                candidates = tuple(discovery.discover_and_stage("nvidia_api"))
            except Exception as exc:
                raise DashboardProviderSettingsError(
                    f"NVIDIA free-model discovery failed ({type(exc).__name__}); check endpoint, network policy and API-key access"
                ) from None
            eligible = [
                candidate for candidate in candidates
                if getattr(candidate, "model", None) is not None
                and getattr(candidate.model, "provider", None) == "nvidia_api"
                and str(getattr(candidate.model, "pricing_tier", "")).lower() == "free"
                and str(getattr(candidate.model, "id", "")).strip()
            ]
            discovered_models = [candidate.model.id for candidate in eligible]
            if not eligible:
                raise DashboardProviderSettingsError(
                    "NVIDIA discovery returned no eligible free models; no inference request was sent"
                )
            model = dataclass_replace(eligible[0].model, enabled=True, pricing_tier="free")
        else:
            endpoint = (
                str(config.get("base_url", "")).strip()
                if isinstance(config, Mapping)
                else ""
            ) or str(metadata.get("base_url", "")).strip()
            if not endpoint:
                raise DashboardProviderSettingsError("OpenRouter endpoint is not configured")
            model = ModelSpec(
                id="openrouter/free",
                provider="openrouter",
                endpoint=endpoint.rstrip("/"),
                capabilities=("chat", "reasoning"),
                context_window=200000,
                enabled=True,
                pricing_tier="free",
            )

        request = InferenceRequest(
            messages=[{
                "role": "user",
                "content": "Reply with exactly: AURORA SETTINGS LIVE INFERENCE OK",
            }],
            model=model.id,
            temperature=0,
            max_tokens=32,
        )
        execution_id = new_execution_id()
        try:
            adapter = registry.create(model)
            response = adapter.generate(
                request,
                selected_model=model.id,
                execution_id=execution_id,
            )
        except Exception as exc:
            status_code = getattr(exc, "status_code", None)
            if isinstance(status_code, int):
                raise DashboardProviderSettingsError(
                    f"{provider_id} live inference failed: {_http_status_message(status_code)}"
                ) from None
            if type(exc).__name__ in {"ProviderTimeoutError", "TimeoutError"}:
                raise DashboardProviderSettingsError(
                    f"{provider_id} live inference timed out; verify network/egress connectivity"
                ) from None
            if type(exc).__name__ == "ProviderTransportError":
                raise DashboardProviderSettingsError(
                    f"{provider_id} live inference transport failed; verify DNS, TLS, proxy and network policy"
                ) from None
            if provider_id == "openrouter" and isinstance(exc, ProviderResponseError):
                # ProviderResponseError messages are adapter-authored diagnostics,
                # not upstream response bodies. Preserve this safe parser diagnosis
                # so a malformed/unexpected response can be distinguished from quota
                # or connectivity failures without leaking response payloads.
                raise DashboardProviderSettingsError(
                    f"{provider_id} live inference returned an unexpected response: {exc}; "
                    "credential value and provider response body were omitted"
                ) from None
            raise DashboardProviderSettingsError(
                f"{provider_id} live inference failed ({type(exc).__name__}); credential value and provider response body were omitted"
            ) from None

        content = getattr(response, "content", None)
        if not isinstance(content, str) or not content.strip():
            raise DashboardProviderSettingsError(
                f"{provider_id} returned an empty response; live inference was not verified"
            )
        telemetry = getattr(response, "telemetry", None)
        return {
            "provider": provider_id,
            "ok": True,
            "inference_verified": True,
            "model": model.id,
            "response_non_empty": True,
            "latency_ms": getattr(telemetry, "duration_ms", None),
            "discovered_free_models": discovered_models[:10],
            "openrouter_quota_preflight": openrouter_quota,
            "request_id": request.request_id,
            "execution_id": execution_id,
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

    # Dashboard snapshots must observe the exact registries used by the router;
    # otherwise a newly saved provider can work at runtime but remain invisible
    # in the model/provider views until restart.
    gateway = getattr(runtime, "dashboard_gateway", None)
    if gateway is not None:
        gateway.provider_registry = router.provider_registry
        gateway.model_registry = router.model_registry
        gateway.model_router = router


def _http_status_message(status_code: int) -> str:
    """Describe common HTTP failures without exposing provider response bodies."""
    if status_code == 401:
        return "authentication rejected (HTTP 401); verify this provider's API key"
    if status_code == 403:
        return "access denied (HTTP 403); verify account permissions and endpoint access"
    if status_code == 404:
        return "endpoint or model-list route not found (HTTP 404); verify the base URL"
    if status_code == 408:
        return "request timed out (HTTP 408)"
    if status_code == 429:
        return "rate limit or quota reached (HTTP 429); retry after the provider cooldown"
    if 500 <= status_code <= 599:
        return f"provider service unavailable (HTTP {status_code}); retry later"
    return f"provider returned HTTP {status_code}"


def _urlopen(request: urllib.request.Request):
    try:
        return urllib.request.urlopen(request, timeout=20)
    except urllib.error.HTTPError as exc:
        # Never forward response bodies or headers: they can contain request metadata.
        raise DashboardProviderSettingsError(_http_status_message(exc.code)) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise DashboardProviderSettingsError(
            "provider connection failed (DNS, TLS, proxy, or network error)"
        ) from None


__all__ = ["DashboardProviderSettingsError", "ProviderSettingsService"]
