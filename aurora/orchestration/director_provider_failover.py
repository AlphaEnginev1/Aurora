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
    ) -> None:
        if not getattr(active_provider, "provider_id", "").strip():
            raise DirectorProviderFailoverError("active Director provider must expose provider_id")
        if not candidates:
            raise DirectorProviderFailoverError("at least one Director provider candidate is required")
        self._active_provider = active_provider
        self._candidates = candidates
        self._store = store
        self._activate = activate
        self._event_sink = event_sink
        self._context_provider = context_provider
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
    ) -> DirectorModelResponse:
        with self._lock:
            active = self._active_provider
            try:
                return active.generate(
                    messages=messages,
                    model=model,
                    role=role,
                    tools=tools,
                )
            except Exception as exc:
                if not _is_failoverable(exc):
                    raise
                correlation_id = self._correlation_id(active)
                self._record_and_emit(
                    correlation_id=correlation_id,
                    phase="failure_detected",
                    role=role,
                    from_provider=getattr(active, "provider_id", "unknown"),
                    from_model=model,
                    to_provider=None,
                    to_model=None,
                    error=exc,
                )

                candidates = tuple(
                    candidate
                    for candidate in self._candidates
                    if candidate.provider_id != getattr(active, "provider_id", "")
                )
                last_error: Exception = exc
                for candidate in candidates:
                    transition_id = f"director-provider-failover:{correlation_id}:{candidate.provider_id}"
                    self._store.record(
                        transition_id=transition_id,
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
                        self._activate(candidate, replacement)
                        self._active_provider = replacement
                        self._store.record(
                            transition_id=transition_id,
                            correlation_id=correlation_id,
                            phase="succeeded",
                            role=role,
                            from_provider=getattr(active, "provider_id", "unknown"),
                            from_model=model,
                            to_provider=candidate.provider_id,
                            to_model=candidate.model,
                        )
                        self._emit(
                            {
                                "event_type": "director_provider_failover_succeeded",
                                "correlation_id": correlation_id,
                                "role": role,
                                "from_provider": getattr(active, "provider_id", "unknown"),
                                "from_model": model,
                                **candidate.descriptor(),
                                "response_provider": response.provider,
                                "response_model": response.model,
                            }
                        )
                        return response
                    except Exception as replacement_error:
                        last_error = replacement_error
                        self._store.record(
                            transition_id=transition_id,
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
                    from_model=model,
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
                        "from_model": model,
                        "error_type": type(last_error).__name__,
                        "error": str(last_error)[:1000],
                    }
                )
                raise DirectorProviderFailoverError(
                    f"Director provider failover exhausted after {getattr(active, 'provider_id', 'unknown')} failed"
                ) from last_error

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


def _is_failoverable(exc: Exception) -> bool:
    return bool(getattr(exc, "failoverable", True))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "DirectorProviderCandidate",
    "DirectorProviderFailoverController",
    "DirectorProviderFailoverError",
    "DirectorProviderFailoverStore",
]
