"""Durable Director provider failover and replacement boundary.

The controller keeps the Director provider contract vendor-neutral. It only
switches providers after a provider execution failure and never changes Aurora
mission ownership or task state.
"""
from __future__ import annotations

from dataclasses import dataclass
import inspect
from datetime import datetime, timezone
import sqlite3
from threading import RLock
from typing import Any, Callable, Mapping

from aurora.failure_tracker import report_failure

from .director_provider import (
    DirectorModelResponse,
    DirectorProvider,
    DirectorToolSpec,
    InferenceDirectorProvider,
)
from aurora.inference.models import InferenceRequest
from aurora.inference.router import ModelRouter
from aurora.inference.provider_health import ProviderHealthStore, classify_failure


class DirectorProviderFailoverError(RuntimeError):
    """Raised when Director provider failover exhausts all eligible candidates."""


@dataclass(frozen=True)
class DirectorProviderCandidate:
    """A concrete, eligible provider that can become the active Director."""

    provider_id: str
    model: str | None
    endpoint: str | None
    factory: Callable[[], DirectorProvider]

    def descriptor(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "model": self.model,
            "endpoint": self.endpoint,
        }


def _router_generate_compatible(
    router: ModelRouter,
    request: InferenceRequest,
    *,
    capability: str,
    preferred_models: tuple[str, ...],
) -> Any:
    """Forward optional routing preferences only when supported by this router."""
    kwargs: dict[str, Any] = {"capability": capability}
    try:
        parameters = inspect.signature(router.generate).parameters
    except (TypeError, ValueError):
        parameters = {}
    accepts_extra = any(
        item.kind is inspect.Parameter.VAR_KEYWORD
        for item in parameters.values()
    )
    if "preferred_models" in parameters or accepts_extra:
        kwargs["preferred_models"] = preferred_models or None
    return router.generate(request, **kwargs)


class BoundInferenceDirectorProvider:
    """Expose a stable failover identity over Aurora's inference fabric."""

    def __init__(
        self,
        router: ModelRouter,
        *,
        provider_id: str,
        model: str | None = None,
    ) -> None:
        if not provider_id.strip():
            raise ValueError("provider_id is empty")
        self._router = router
        self._provider_id = provider_id
        self._model = model

    @property
    def provider_id(self) -> str:
        return self._provider_id

    @property
    def model(self) -> str | None:
        """Expose the pinned model for health accounting and safe retry exclusion."""
        return self._model

    def generate(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str | None,
        role: str,
        tools: tuple[DirectorToolSpec, ...] = (),
        metadata: Mapping[str, Any] | None = None,
        capability: str = "reasoning",
        preferred_models: tuple[str, ...] = (),
    ) -> DirectorModelResponse:
        prompt_messages = list(messages)
        if tools:
            instruction = InferenceDirectorProvider._tool_instruction(tools)
            if prompt_messages and prompt_messages[0].get("role") == "system":
                prompt_messages[0] = {
                    **prompt_messages[0],
                    "content": str(prompt_messages[0].get("content", "")) + "\n\n" + instruction,
                }
            else:
                prompt_messages.insert(0, {"role": "system", "content": instruction})
        request = InferenceRequest(
            messages=prompt_messages,
            model=self._model if self._model is not None else model,
            temperature=0.0,
            max_tokens=3000,
            metadata={
                **dict(metadata or {}),
                "role": role,
                "director_tools": [tool.name for tool in tools],
            },
        )
        response = _router_generate_compatible(
            self._router,
            request,
            capability=capability,
            preferred_models=preferred_models,
        )
        tool_calls = InferenceDirectorProvider._parse_tool_calls(response.content, tools)
        return DirectorModelResponse(
            content=response.content if not tool_calls else "",
            model=response.model,
            provider=response.provider,
            request_id=response.request_id,
            execution_id=response.execution_id,
            tool_calls=tool_calls,
        )


class DirectorProviderFailoverStore:
    """Persist provider failure/failover evidence in Aurora's runtime database."""

    def __init__(self, db_path: str) -> None:
        self.db_path = str(db_path)
        self._lock = RLock()
        with self._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS director_provider_failovers (
                    transition_id TEXT PRIMARY KEY,
                    correlation_id TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    role TEXT NOT NULL,
                    from_provider TEXT NOT NULL,
                    from_model TEXT,
                    to_provider TEXT,
                    to_model TEXT,
                    error_type TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                )
                """
            )
            db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_director_provider_failover_created
                ON director_provider_failovers(created_at)
                """
            )

    def record(
        self,
        *,
        transition_id: str,
        correlation_id: str,
        phase: str,
        role: str,
        from_provider: str,
        from_model: str | None,
        to_provider: str | None,
        to_model: str | None,
        error: Exception | None = None,
    ) -> None:
        created_at = _now()
        completed_at = created_at if phase in {"succeeded", "failed", "exhausted"} else None
        error_type = type(error).__name__ if error is not None else None
        error_message = str(error)[:2000] if error is not None else None
        with self._lock, self._connect() as db:
            db.execute(
                """
                INSERT OR REPLACE INTO director_provider_failovers (
                    transition_id, correlation_id, phase, role,
                    from_provider, from_model, to_provider, to_model,
                    error_type, error_message, created_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    transition_id,
                    correlation_id,
                    phase,
                    role,
                    from_provider,
                    from_model,
                    to_provider,
                    to_model,
                    error_type,
                    error_message,
                    created_at,
                    completed_at,
                ),
            )

    def list_recent(self, *, limit: int = 50) -> tuple[dict[str, Any], ...]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        with self._lock, self._connect() as db:
            rows = db.execute(
                """
                SELECT transition_id, correlation_id, phase, role,
                       from_provider, from_model, to_provider, to_model,
                       error_type, error_message, created_at, completed_at
                FROM director_provider_failovers
                ORDER BY created_at DESC, transition_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        return db


def _generate_compatible_provider(
    provider: DirectorProvider,
    *,
    messages: list[dict[str, Any]],
    model: str | None,
    role: str,
    tools: tuple[DirectorToolSpec, ...],
    metadata: Mapping[str, Any] | None,
    capability: str,
    preferred_models: tuple[str, ...],
) -> DirectorModelResponse:
    """Pass optional routing context only to adapters that support it."""
    kwargs: dict[str, Any] = {
        "messages": messages,
        "model": model,
        "role": role,
        "tools": tools,
    }
    try:
        parameters = inspect.signature(provider.generate).parameters
    except (TypeError, ValueError):
        parameters = {}
    accepts_extra = any(
        item.kind is inspect.Parameter.VAR_KEYWORD
        for item in parameters.values()
    )
    optional = {
        "metadata": metadata,
        "capability": capability,
        "preferred_models": preferred_models,
    }
    for name, value in optional.items():
        if name in parameters or accepts_extra:
            kwargs[name] = value
    return provider.generate(**kwargs)


class DirectorProviderFailoverController:
    """Execute Director calls with deterministic provider replacement.

    A provider failure may replace the active provider once per call. The
    first successful alternative becomes active for subsequent Director calls.
    Activation is persisted by the injected callback only after a successful
    response, so a failed candidate can never become authoritative.
    """

    def __init__(
        self,
        *,
        active_provider: DirectorProvider,
        candidates: tuple[DirectorProviderCandidate, ...],
        store: DirectorProviderFailoverStore,
        activate: Callable[[DirectorProviderCandidate, DirectorProvider], None],
        event_sink: Callable[[Mapping[str, Any]], None] | None = None,
        context_provider: Callable[[], Mapping[str, Any] | None] | None = None,
        candidate_resolver: Callable[
            [list[dict[str, Any]], str | None, str],
            tuple[DirectorProviderCandidate, ...],
        ] | None = None,
        health_store: ProviderHealthStore | None = None,
    ) -> None:
        if not getattr(active_provider, "provider_id", "").strip():
            raise DirectorProviderFailoverError("active Director provider must expose provider_id")
        self._active_provider = active_provider
        self._candidates = candidates
        self._store = store
        self._activate = activate
        self._event_sink = event_sink
        self._context_provider = context_provider
        self._candidate_resolver = candidate_resolver
        self._health_store = health_store
        self._lock = RLock()

    @property
    def provider_id(self) -> str:
        return str(getattr(self._active_provider, "provider_id", "") or "")

    @property
    def active_provider(self) -> DirectorProvider:
        return self._active_provider

    def set_active_provider(self, provider: DirectorProvider) -> None:
        """Replace the active provider after an explicit human/operator switch."""
        if not getattr(provider, "provider_id", "").strip():
            raise DirectorProviderFailoverError("Director provider must expose provider_id")
        with self._lock:
            self._active_provider = provider

    def set_event_sink(
        self,
        event_sink: Callable[[Mapping[str, Any]], None] | None,
    ) -> None:
        self._event_sink = event_sink

    def set_context_provider(
        self,
        context_provider: Callable[[], Mapping[str, Any] | None] | None,
    ) -> None:
        self._context_provider = context_provider

    def generate(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str | None,
        role: str,
        tools: tuple[DirectorToolSpec, ...] = (),
        metadata: Mapping[str, Any] | None = None,
        capability: str = "reasoning",
        preferred_models: tuple[str, ...] = (),
    ) -> DirectorModelResponse:
        with self._lock:
            active = self._active_provider
            # A reserve rejected on this request must not be attempted twice in
            # the same fallback chain, but it may recover after its health
            # cooldown for a later request. Durable health state owns cooldown.
            failed_candidates_this_call: set[tuple[str, str]] = set()
            try:
                response = _generate_compatible_provider(
                    active,
                    messages=messages,
                    model=model,
                    role=role,
                    tools=tools,
                    metadata=metadata,
                    capability=capability,
                    preferred_models=preferred_models,
                )
                if (
                    self._health_store is not None
                    and not self._router_owns_health(active)
                ):
                    # Standalone providers may not write ModelRouter health receipts;
                    # use the concrete response identity rather than the facade ID.
                    actual_provider = str(
                        getattr(response, "provider", "")
                        or getattr(active, "provider_id", "")
                        or ""
                    ).strip()
                    actual_model = str(
                        getattr(response, "model", "")
                        or getattr(active, "model", "")
                        or model
                        or actual_provider
                    ).strip()
                    if actual_provider and actual_model:
                        self._health_store.record_success(actual_provider, actual_model)
                return response
            except Exception as exc:
                self._record_health(active, model, exc)
                if not _is_failoverable(exc):
                    raise
                correlation_id = self._correlation_id(
                    active,
                    metadata=metadata,
                    role=role,
                )
                active_provider_id = str(getattr(active, "provider_id", "") or "")
                active_model_id = _failed_model_id(active, model, exc)
                self._record_and_emit(
                    correlation_id=correlation_id,
                    phase="failure_detected",
                    role=role,
                    from_provider=active_provider_id or "unknown",
                    from_model=active_model_id or model,
                    to_provider=None,
                    to_model=None,
                    error=exc,
                )

                candidates: list[DirectorProviderCandidate] = [
                    candidate
                    for candidate in self._candidates
                    if not (
                        active_model_id and candidate.model == active_model_id
                    )
                    and self._candidate_key(candidate) not in failed_candidates_this_call
                ]
                if self._candidate_resolver is not None:
                    try:
                        dynamic_candidates = self._candidate_resolver(messages, model, role)
                    except Exception as discovery_error:
                        last_error = discovery_error
                        self._record_and_emit(
                            correlation_id=correlation_id,
                            phase="candidate_discovery_failed",
                            role=role,
                            from_provider=getattr(active, "provider_id", "unknown"),
                            from_model=model,
                            to_provider=None,
                            to_model=None,
                            error=discovery_error,
                        )
                        dynamic_candidates = ()
                    else:
                        dynamic_candidates = tuple(dynamic_candidates or ())
                    seen = {
                        self._candidate_key(candidate)
                        for candidate in candidates
                    }
                    candidates.extend(
                        candidate
                        for candidate in dynamic_candidates
                        if not (
                            active_model_id and candidate.model == active_model_id
                        )
                        and self._candidate_key(candidate) not in failed_candidates_this_call
                        and self._candidate_key(candidate) not in seen
                    )
                last_error: Exception = exc
                for candidate in candidates:
                    attempt_id = f"director-provider-failover:{correlation_id}:{candidate.provider_id}"
                    self._store.record(
                        transition_id=f"{attempt_id}:attempted",
                        correlation_id=correlation_id,
                        phase="attempted",
                        role=role,
                        from_provider=getattr(active, "provider_id", "unknown"),
                        from_model=active_model_id or model,
                        to_provider=candidate.provider_id,
                        to_model=candidate.model,
                    )
                    self._emit(
                        {
                            "event_type": "director_provider_failover_attempted",
                            "correlation_id": correlation_id,
                            "role": role,
                            "from_provider": getattr(active, "provider_id", "unknown"),
                            "from_model": active_model_id or model,
                            **candidate.descriptor(),
                        }
                    )
                    try:
                        replacement = candidate.factory()
                        response = _generate_compatible_provider(
                            replacement,
                            messages=messages,
                            model=candidate.model,
                            role=role,
                            tools=tools,
                            metadata=metadata,
                            capability=capability,
                            preferred_models=preferred_models,
                        )
                        if self._health_store is not None:
                            self._health_store.record_success(
                                candidate.provider_id,
                                candidate.model or candidate.provider_id,
                            )
                        self._activate(candidate, replacement)
                        self._active_provider = replacement
                        self._store.record(
                            transition_id=f"{attempt_id}:succeeded",
                            correlation_id=correlation_id,
                            phase="succeeded",
                            role=role,
                            from_provider=getattr(active, "provider_id", "unknown"),
                            from_model=active_model_id or model,
                            to_provider=candidate.provider_id,
                            to_model=candidate.model,
                        )
                        self._emit(
                            {
                                "event_type": "director_provider_failover_succeeded",
                                "correlation_id": correlation_id,
                                "role": role,
                                "from_provider": getattr(active, "provider_id", "unknown"),
                                "from_model": active_model_id or model,
                                **candidate.descriptor(),
                                "response_provider": response.provider,
                                "response_model": response.model,
                            }
                        )
                        return response
                    except Exception as replacement_error:
                        last_error = replacement_error
                        # Health is tracked at provider+model granularity. Never suppress
                        # the whole NVIDIA provider because one model failed; continue through
                        # the remaining dynamically discovered reserve models.
                        if self._health_store is not None:
                            try:
                                self._health_store.record_failure(
                                    candidate.provider_id,
                                    candidate.model or candidate.provider_id,
                                    replacement_error,
                                )
                            except Exception as health_exc:
                                report_failure("provider_health_store", health_exc)
                        failed_candidates_this_call.add(self._candidate_key(candidate))
                        self._store.record(
                            transition_id=f"{attempt_id}:failed",
                            correlation_id=correlation_id,
                            phase="failed",
                            role=role,
                            from_provider=getattr(active, "provider_id", "unknown"),
                            from_model=model,
                            to_provider=candidate.provider_id,
                            to_model=candidate.model,
                            error=replacement_error,
                        )
                        self._emit(
                            {
                                "event_type": "director_provider_failover_failed",
                                "correlation_id": correlation_id,
                                "role": role,
                                "from_provider": getattr(active, "provider_id", "unknown"),
                                "from_model": model,
                                **candidate.descriptor(),
                                "error_type": type(replacement_error).__name__,
                                "error": str(replacement_error)[:1000],
                            }
                        )

                self._store.record(
                    transition_id=f"director-provider-failover:{correlation_id}:exhausted",
                    correlation_id=correlation_id,
                    phase="exhausted",
                    role=role,
                    from_provider=getattr(active, "provider_id", "unknown"),
                    from_model=active_model_id or model,
                    to_provider=None,
                    to_model=None,
                    error=last_error,
                )
                self._emit(
                    {
                        "event_type": "director_provider_failover_exhausted",
                        "correlation_id": correlation_id,
                        "role": role,
                        "from_provider": getattr(active, "provider_id", "unknown"),
                        "from_model": active_model_id or model,
                        "error_type": type(last_error).__name__,
                        "error": str(last_error)[:1000],
                    }
                )
                raise DirectorProviderFailoverError(
                    f"Director provider failover exhausted after {getattr(active, 'provider_id', 'unknown')} failed"
                ) from last_error

    def _router_owns_health(self, provider: DirectorProvider) -> bool:
        """Avoid double-counting when the active adapter wraps this health-aware router."""
        router = getattr(provider, "_router", None)
        return (
            router is not None
            and self._health_store is not None
            and getattr(router, "health_store", None) is self._health_store
        )

    def _record_health(
        self,
        provider: DirectorProvider,
        model: str | None,
        error: Exception,
    ) -> None:
        if self._health_store is None or self._router_owns_health(provider):
            return
        # Errors from standalone router adapters identify the real
        # executor in ProviderErrorContext; the facade's provider_id is not the
        # provider that just failed. Attribute failures to that concrete receipt.
        context = getattr(error, "context", None)
        provider_id = str(
            getattr(context, "provider", "")
            or getattr(provider, "provider_id", "")
            or ""
        ).strip()
        model_id = str(
            getattr(context, "selected_model", "")
            or getattr(context, "provider_model", "")
            or getattr(provider, "model", "")
            or model
            or provider_id
        ).strip()
        if not provider_id or not model_id:
            return
        try:
            self._health_store.record_failure(provider_id, model_id, error)
        except Exception as health_exc:
            report_failure("provider_health_store", health_exc)

    @staticmethod
    def _candidate_key(candidate: DirectorProviderCandidate) -> tuple[str, str]:
        return (
            candidate.provider_id,
            str(candidate.model or "").strip(),
        )

    def _correlation_id(
        self,
        provider: DirectorProvider,
        *,
        metadata: Mapping[str, Any] | None = None,
        role: str | None = None,
    ) -> str:
        context = self._context_provider() if self._context_provider is not None else None
        context = context if isinstance(context, Mapping) else {}
        metadata = metadata if isinstance(metadata, Mapping) else {}
        # The durable mission correlation is authoritative when a mission is
        # already being planned. For conversation turns before mission creation,
        # preserve the incoming request/turn correlation rather than minting a
        # disconnected failover ID.
        correlation_id = str(
            context.get("correlation_id")
            or metadata.get("correlation_id")
            or ""
        ).strip()
        if correlation_id:
            return correlation_id
        mission_id = str(
            context.get("mission_id")
            or metadata.get("mission_id")
            or metadata.get("session_id")
            or metadata.get("turn_id")
            or "global"
        )
        resolved_role = str(context.get("role") or role or metadata.get("role") or "director")
        safe = "_".join(
            item for item in (mission_id.strip(), resolved_role.strip(), getattr(provider, "provider_id", "unknown"))
            if item
        )
        return f"{safe}:{_now().replace(':', '').replace('+00:00', 'Z')}"

    def _emit(self, payload: Mapping[str, Any]) -> None:
        if self._event_sink is None:
            return
        event = dict(payload)
        context = self._context_provider() if self._context_provider is not None else None
        if context:
            event.update({str(key): value for key, value in context.items() if key not in event})
        try:
            self._event_sink(event)
        except Exception as sink_exc:
            # Failover evidence is durably stored above; telemetry must not
            # prevent the replacement provider from serving the Director.
            report_failure("failover_event_sink", sink_exc)

    def _record_and_emit(
        self,
        *,
        correlation_id: str,
        phase: str,
        role: str,
        from_provider: str,
        from_model: str | None,
        to_provider: str | None,
        to_model: str | None,
        error: Exception,
    ) -> None:
        self._store.record(
            transition_id=f"director-provider-failover:{correlation_id}:{phase}",
            correlation_id=correlation_id,
            phase=phase,
            role=role,
            from_provider=from_provider,
            from_model=from_model,
            to_provider=to_provider,
            to_model=to_model,
            error=error,
        )
        self._emit(
            {
                "event_type": "director_provider_failure_detected",
                "correlation_id": correlation_id,
                "role": role,
                "from_provider": from_provider,
                "from_model": from_model,
                "error_type": type(error).__name__,
                "error": str(error)[:1000],
            }
        )


def _failed_model_id(
    active: DirectorProvider,
    requested_model: str | None,
    error: Exception,
) -> str:
    """Resolve the concrete model that failed when routing selected it dynamically."""
    # A provider's concrete execution receipt wins over a generic router alias
    # supplied by the caller. Bound providers then supply their pinned model;
    # only an unbound router falls back to the requested alias.
    context = getattr(error, "context", None)
    selected = str(getattr(context, "selected_model", "") or "").strip()
    if selected:
        return selected
    bound_model = str(getattr(active, "model", "") or "").strip()
    if bound_model:
        return bound_model
    return str(requested_model or "").strip()


def _is_failoverable(exc: Exception) -> bool:
    return bool(getattr(exc, "failoverable", True))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "BoundInferenceDirectorProvider",
    "DirectorProviderCandidate",
    "DirectorProviderFailoverController",
    "DirectorProviderFailoverError",
    "DirectorProviderFailoverStore",
]