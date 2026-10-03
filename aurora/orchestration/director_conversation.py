"""Provider-neutral durable conversation service for the Aurora Director."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Any, Mapping
from uuid import uuid4

from aurora.inference.models import InferenceRequest, InferenceResponse
from aurora.inference.router import ModelRouter, ModelRoutingError
from .director_provider import DirectorProvider, InferenceDirectorProvider

from .director_bootstrap import DirectorBootstrap, DirectorBootstrapError


class DirectorConversationError(RuntimeError):
    """Raised when an Aurora Director conversation cannot proceed safely."""


@dataclass(frozen=True)
class ConversationSession:
    session_id: str
    created_at: str
    updated_at: str
    state: str
    bootstrap_id: str
    context_manifest_id: str


@dataclass(frozen=True)
class ConversationTurn:
    turn_id: str
    session_id: str
    sequence: int
    role: str
    content: str
    state: str
    created_at: str
    correlation_id: str
    model: str | None = None
    provider: str | None = None
    execution_id: str | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class DirectorConversationResponse:
    turn: ConversationTurn
    response_text: str
    state: str
    correlation_id: str
    context_manifest_id: str
    model: str
    provider: str
    execution_id: str
    presentation_hints: Mapping[str, Any]


class DirectorConversationService:
    """Own durable text conversation state while delegating inference to ModelRouter."""

    SYSTEM_PROMPT = """You are Aurora Director.
You are the conversational Director of an autonomous Aurora system.
Answer the human clearly and truthfully.
Use authoritative Aurora context supplied with the request.
Never claim that an action, task, tool call, research result, or state change occurred unless authoritative evidence in context establishes it.
When the human asks for work that should be executed, explain the next governed step rather than inventing execution.
Keep responses useful and concise unless the human asks for depth."""

    SESSION_STATES = frozenset({"active", "closed"})
    TURN_STATES = frozenset({"processing", "completed", "failed"})

    def __init__(
        self,
        *,
        router: ModelRouter,
        bootstrap: DirectorBootstrap,
        db_path: str | Path,
        max_history_turns: int = 20,
        provider: DirectorProvider | None = None,
    ) -> None:
        if max_history_turns < 1:
            raise DirectorConversationError("max_history_turns must be at least 1")
        self.router = router
        self.bootstrap = bootstrap
        self.db_path = Path(db_path)
        self.max_history_turns = max_history_turns
        self.provider: DirectorProvider = provider or InferenceDirectorProvider(router)
        self.router.configure_context_gate(bootstrap.context_gate, enforce=True)
        self._initialize_schema()

    def create_session(self, *, session_id: str | None = None) -> ConversationSession:
        state = self._initialize_bootstrap()
        session = session_id or f"conv-{uuid4().hex}"
        _require_identifier(session, "session_id")
        now = _now()
        with self._connect() as db:
            try:
                db.execute(
                    """
                    INSERT INTO conversation_sessions (
                        session_id, created_at, updated_at, state,
                        bootstrap_id, context_manifest_id
                    ) VALUES (?, ?, ?, 'active', ?, ?)
                    """,
                    (session, now, now, state.boot_id, state.context.manifest_id),
                )
            except sqlite3.IntegrityError as exc:
                raise DirectorConversationError(
                    f"conversation session already exists: {session}"
                ) from exc
        return ConversationSession(
            session_id=session,
            created_at=now,
            updated_at=now,
            state="active",
            bootstrap_id=state.boot_id,
            context_manifest_id=state.context.manifest_id,
        )

    def get_session(self, session_id: str) -> ConversationSession:
        _require_identifier(session_id, "session_id")
        with self._connect() as db:
            row = db.execute(
                """
                SELECT session_id, created_at, updated_at, state,
                       bootstrap_id, context_manifest_id
                FROM conversation_sessions
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
        if row is None:
            raise DirectorConversationError(f"unknown conversation session: {session_id}")
        return ConversationSession(**dict(row))

    def list_turns(self, session_id: str, *, limit: int | None = None) -> tuple[ConversationTurn, ...]:
        _require_identifier(session_id, "session_id")
        effective_limit = self.max_history_turns if limit is None else limit
        if effective_limit < 1:
            raise DirectorConversationError("limit must be at least 1")
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT turn_id, session_id, sequence, role, content, state,
                       created_at, correlation_id, model, provider,
                       execution_id, error_code
                FROM conversation_turns
                WHERE session_id = ?
                ORDER BY sequence DESC
                LIMIT ?
                """,
                (session_id, effective_limit),
            ).fetchall()
        return tuple(
            ConversationTurn(**dict(row)) for row in reversed(rows)
        )

    def send_message(
        self,
        *,
        session_id: str,
        user_input: str,
        model: str | None = None,
    ) -> DirectorConversationResponse:
        _require_identifier(session_id, "session_id")
        prompt = user_input.strip()
        if not prompt:
            raise DirectorConversationError("user_input is empty")

        session = self.get_session(session_id)
        if session.state != "active":
            raise DirectorConversationError(
                f"conversation session is not active: {session_id}"
            )

        bootstrap_state = self._initialize_bootstrap()
        policy = bootstrap_state.director_model_policy
        self._validate_requested_model(model, policy)

        correlation_id = f"corr-{uuid4().hex}"
        turn_id = f"turn-{uuid4().hex}"
        next_sequence = self._next_sequence(session_id)

        self._insert_turn(
            turn_id=turn_id,
            session_id=session_id,
            sequence=next_sequence,
            role="user",
            content=prompt,
            state="completed",
            created_at=_now(),
            correlation_id=correlation_id,
        )

        history = list(self.list_turns(session_id, limit=self.max_history_turns))
        messages = [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            *[
                {"role": item.role, "content": item.content}
                for item in history
                if item.state == "completed" and item.role in {"user", "assistant"}
            ],
        ]

        request = InferenceRequest(
            messages=messages,
            model=model,
            metadata={
                "role": "director_conversation",
                "conversation_session_id": session_id,
                "conversation_turn_id": turn_id,
                "correlation_id": correlation_id,
                "task_type": "conversation",
                "allowed_models": self._preferred_models(policy),
            },
        )

        try:
            if isinstance(self.provider, InferenceDirectorProvider):
                response = self.router.generate(
                    request,
                    capability=str(policy["default_capability"]),
                    preferred_models=self._preferred_models(policy),
                )
            else:
                provider_messages = [
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    *bootstrap_state.messages,
                    *[
                        {"role": item.role, "content": item.content}
                        for item in history
                        if item.state == "completed" and item.role in {"user", "assistant"}
                    ],
                ]
                response = self.provider.generate(
                    messages=provider_messages,
                    model=model,
                    role="director_conversation",
                )
        except Exception as exc:
            self._insert_turn(
                turn_id=f"{turn_id}-error",
                session_id=session_id,
                sequence=next_sequence + 1,
                role="assistant",
                content="",
                state="failed",
                created_at=_now(),
                correlation_id=correlation_id,
                error_code=_error_code(exc),
            )
            self._touch_session(
                session_id,
                context_manifest_id=str(
                    self._context_manifest_id_from_error(exc)
                    or session.context_manifest_id
                ),
                bootstrap_id=bootstrap_state.boot_id,
            )
            if isinstance(exc, ModelRoutingError):
                raise DirectorConversationError(
                    f"Director conversation inference rejected: {exc}"
                ) from exc
            if isinstance(exc, DirectorBootstrapError):
                raise DirectorConversationError(str(exc)) from exc
            raise DirectorConversationError(
                f"Director conversation inference failed: {exc}"
            ) from exc

        assistant_turn = ConversationTurn(
            turn_id=f"{turn_id}-response",
            session_id=session_id,
            sequence=next_sequence + 1,
            role="assistant",
            content=response.content,
            state="completed",
            created_at=_now(),
            correlation_id=correlation_id,
            model=getattr(response, "selected_model", None) or response.model,
            provider=response.provider,
            execution_id=response.execution_id,
        )
        self._insert_turn(
            turn_id=assistant_turn.turn_id,
            session_id=assistant_turn.session_id,
            sequence=assistant_turn.sequence,
            role=assistant_turn.role,
            content=assistant_turn.content,
            state=assistant_turn.state,
            created_at=assistant_turn.created_at,
            correlation_id=assistant_turn.correlation_id,
            model=assistant_turn.model,
            provider=assistant_turn.provider,
            execution_id=assistant_turn.execution_id,
        )
        context_manifest_id = self._verified_manifest_id(response, session.context_manifest_id)
        self._touch_session(
            session_id,
            context_manifest_id=context_manifest_id,
            bootstrap_id=bootstrap_state.boot_id,
        )

        return DirectorConversationResponse(
            turn=assistant_turn,
            response_text=response.content,
            state="completed",
            correlation_id=correlation_id,
            context_manifest_id=context_manifest_id,
            model=getattr(response, "selected_model", None) or response.model,
            provider=response.provider,
            execution_id=response.execution_id,
            presentation_hints={
                "avatar_state": "SPEAKING",
                "text_available": True,
                "voice_requested": False,
            },
        )

    def set_provider(self, provider: DirectorProvider) -> None:
        if not getattr(provider, "provider_id", ""):
            raise DirectorConversationError("Director provider must expose provider_id")
        self.provider = provider

    def close_session(self, session_id: str) -> ConversationSession:
        session = self.get_session(session_id)
        if session.state == "closed":
            return session
        now = _now()
        with self._connect() as db:
            db.execute(
                """
                UPDATE conversation_sessions
                SET state = 'closed', updated_at = ?
                WHERE session_id = ?
                """,
                (now, session_id),
            )
        return ConversationSession(
            session_id=session.session_id,
            created_at=session.created_at,
            updated_at=now,
            state="closed",
            bootstrap_id=session.bootstrap_id,
            context_manifest_id=session.context_manifest_id,
        )

    def _initialize_bootstrap(self):
        try:
            return self.bootstrap.initialize()
        except Exception as exc:
            if isinstance(exc, DirectorBootstrapError):
                raise
            raise DirectorConversationError(
                f"Director bootstrap failed: {exc}"
            ) from exc

    def _initialize_schema(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_sessions (
                    session_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (state IN ('active', 'closed')),
                    bootstrap_id TEXT NOT NULL,
                    context_manifest_id TEXT NOT NULL
                )
                """
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_turns (
                    turn_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES conversation_sessions(session_id),
                    sequence INTEGER NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (state IN ('processing', 'completed', 'failed')),
                    created_at TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    model TEXT,
                    provider TEXT,
                    execution_id TEXT,
                    error_code TEXT,
                    UNIQUE(session_id, sequence)
                )
                """
            )
            db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_conversation_turns_session_sequence
                ON conversation_turns(session_id, sequence)
                """
            )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        return db

    def _next_sequence(self, session_id: str) -> int:
        with self._connect() as db:
            row = db.execute(
                """
                SELECT COALESCE(MAX(sequence), 0) AS sequence
                FROM conversation_turns
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
        return int(row["sequence"]) + 1

    def _insert_turn(
        self,
        *,
        turn_id: str,
        session_id: str,
        sequence: int,
        role: str,
        content: str,
        state: str,
        created_at: str,
        correlation_id: str,
        model: str | None = None,
        provider: str | None = None,
        execution_id: str | None = None,
        error_code: str | None = None,
    ) -> None:
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO conversation_turns (
                    turn_id, session_id, sequence, role, content, state,
                    created_at, correlation_id, model, provider,
                    execution_id, error_code
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    turn_id,
                    session_id,
                    sequence,
                    role,
                    content,
                    state,
                    created_at,
                    correlation_id,
                    model,
                    provider,
                    execution_id,
                    error_code,
                ),
            )

    def _touch_session(
        self,
        session_id: str,
        *,
        context_manifest_id: str,
        bootstrap_id: str,
    ) -> None:
        with self._connect() as db:
            db.execute(
                """
                UPDATE conversation_sessions
                SET updated_at = ?, bootstrap_id = ?, context_manifest_id = ?
                WHERE session_id = ?
                """,
                (_now(), bootstrap_id, context_manifest_id, session_id),
            )

    def _preferred_models(self, policy: Mapping[str, Any]) -> tuple[str, ...]:
        models = policy.get("models", ())
        configured = tuple(
            item["id"]
            for item in models
            if isinstance(item, Mapping)
            and isinstance(item.get("id"), str)
            and bool(item.get("enabled", True))
        )
        if configured:
            return configured
        return tuple(model.id for model in self.router.available())

    def _validate_requested_model(self, model: str | None, policy: Mapping[str, Any]) -> None:
        if model is None:
            return
        if getattr(self.provider, "provider_id", "") == "gemini":
            if str(model).strip():
                return
        configured = {
            item["id"]
            for item in policy.get("models", ())
            if isinstance(item, Mapping)
            and isinstance(item.get("id"), str)
            and bool(item.get("enabled", True))
        }
        if model not in configured:
            raise DirectorConversationError(
                f"requested Director model is not enabled by policy: {model}"
            )

    @staticmethod
    def _verified_manifest_id(
        response: InferenceResponse,
        fallback: str,
    ) -> str:
        return fallback

    @staticmethod
    def _context_manifest_id_from_error(exc: Exception) -> str | None:
        text = str(exc)
        marker = "ctx-"
        index = text.find(marker)
        if index < 0:
            return None
        candidate = text[index : index + 36]
        return candidate if len(candidate) == 36 else None


def _require_identifier(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise DirectorConversationError(f"{name} is empty")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _error_code(exc: Exception) -> str:
    return f"{type(exc).__name__.lower()}"


__all__ = [
    "ConversationSession",
    "ConversationTurn",
    "DirectorConversationError",
    "DirectorConversationResponse",
    "DirectorConversationService",
]
