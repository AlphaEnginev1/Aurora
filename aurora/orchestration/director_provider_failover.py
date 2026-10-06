"""Durable Director provider failover and replacement boundary.

The controller keeps the Director provider contract vendor-neutral. It only
switches providers after a provider execution failure and never changes Aurora
mission ownership or task state.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import sqlite3
from threading import RLock
from typing import Any, Callable, Mapping

from .director_provider import DirectorModelResponse, DirectorProvider, DirectorToolSpec
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

    def generate(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str | None,
        role: str,
        tools: tuple[DirectorToolSpec, ...] = (),
    ) -> DirectorModelResponse:
        request = InferenceRequest(
            messages=messages,
            model=self._model if self._model is not None else model,
            temperature=0.0,
            max_tokens=3000,
            metadata={"role": role, "director_tools": [tool.name for tool in tools]},
        )
        response = self._router.generate(request, capability="reasoning")
        return DirectorModelResponse(
            content=response.content,
            model=response.model,
            provider=response.provider,
            request_id=response.request_id,
            execution_id=response.execution_id,
            tool_calls=(),
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
        self._failed_candidates: set[tuple[str, str]] = set()

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
    ) -> DirectorModelResponse:
        with self._lock:
            active = self._active_provider
            try:
                response = active.generate(
                    messages=messages,
                    model=model,
                    role=role,
                    tools=tools,
                )
                if self._health_store is not None:
                    self._health_store.record_success(
                        str(getattr(active, "provider_id", "") or ""),
                        str(model or getattr(active, "model", "") or getattr(active, "provider_id", "") or "unknown"),
                    )
                return response
            except Exception as exc:
                self._record_health(active, model, exc)
                if not _is_failoverable(exc):
                    raise
                correlation_id = self._correlation_id(active)
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
                    and self._candidate_key(candidate) not in self._failed_candidates
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
                            candidate.provider_id == active_provider_id
                            and active_model_id
                            and candidate.model == active_model_id
                        )
                        and self._candidate_key(candidate) not in self._failed_candidates
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
                        from_model=model,
                        to_provider=candidate.provider_id,
                        to_model=candidate.model,
                    )
                    self._emit(
                        {
                            "event_type": "director_provider_failover_attempted",
                            "correlation_id": correlation_id,
                            "role": role,
                            "from_provider": getattr(active, "provider_id", "unknown"),
                            "from_model": model,
                            **candidate.descriptor(),
                        }
                    )
                    try:
                        replacement = candidate.factory()
                        response = replacement.generate(
                            messages=messages,
                            model=candidate.model,
                            role=role,
                            tools=tools,
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
                            except Exception:
                                pass
                        self._failed_candidates.add(self._candidate_key(candidate))
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

    def _record_health(
        self,
        provider: DirectorProvider,
        model: str | None,
        error: Exception,
    ) -> None:
        if self._health_store is None:
            return
        provider_id = str(getattr(provider, "provider_id", "") or "").strip()
        model_id = str(model or getattr(provider, "model", "") or provider_id).strip()
        if not provider_id or not model_id:
            return
        try:
            self._health_store.record_failure(provider_id, model_id, error)
        except Exception:
            pass

    @staticmethod
    def _candidate_key(candidate: DirectorProviderCandidate) -> tuple[str, str]:
        return (
            candidate.provider_id,
            str(candidate.model or "").strip(),
        )

    def _correlation_id(self, provider: DirectorProvider) -> str:
        context = self._context_provider() if self._context_provider is not None else None
        mission_id = str((context or {}).get("mission_id") or "global")
        role = str((context or {}).get("role") or "director")
        safe = "_".join(
            item for item in (mission_id.strip(), role.strip(), getattr(provider, "provider_id", "unknown"))
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
        except Exception:
            # Failover evidence is durably stored above; telemetry must not
            # prevent the replacement provider from serving the Director.
            pass

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
    requested = str(requested_model or getattr(active, "model", "") or "").strip()
    if requested:
        return requested
    context = getattr(error, "context", None)
    selected = str(getattr(context, "selected_model", "") or "").strip()
    if selected:
        return selected
    return str(getattr(active, "model", "") or "").strip()


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