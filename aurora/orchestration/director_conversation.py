"""Provider-neutral durable conversation service for the Aurora Director."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import inspect
import json
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from threading import RLock
import unicodedata
from typing import Any, Callable, Mapping
from uuid import uuid4

from aurora.inference.models import InferenceRequest, InferenceResponse
from aurora.inference.router import ModelRouter, ModelRoutingError
from .director_provider import (
    DirectorModelResponse,
    DirectorProvider,
    DirectorToolSpec,
    InferenceDirectorProvider,
)
from .task_engine import DelegatedTask
from aurora.workbench import UniversalWorkbench, WorkbenchRequest

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
Answer the human clearly and naturally.
Use authoritative Aurora context supplied with the request.
Never claim that an action, task, tool call, research result, or state change occurred unless authoritative evidence establishes it.
When the human explicitly asks Aurora to plan, delegate, execute, verify, or run a mission, use the governed run_mission tool; do not substitute a direct Workbench lookup for a requested mission.
Use the direct workbench tool only for one isolated evidence operation when the human has not requested a mission.
Every Workbench capability must use its fully qualified registered name beginning with 'workbench:'; never omit that prefix.
When the human is only asking a question or having a conversation, answer directly without creating work.
Keep responses useful and concise unless the human asks for depth."""

    SESSION_STATES = frozenset({"active", "closed"})
    TURN_STATES = frozenset({"processing", "completed", "failed"})

    _RUN_MISSION_TOOL = DirectorToolSpec(
        name="run_mission",
        description=(
            "Execute a real Aurora mission through the durable DirectorRuntime. "
            "Use objective for a model-planned mission, or name/title plus a declarative "
            "tasks array for an explicit task graph. Explicit tasks are read-only Workbench "
            "operations and are still validated by TaskEngine and policy."
        ),
        parameters={
            "type": "object",
            "properties": {
                "objective": {"type": "string"},
                "name": {"type": "string"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "tasks": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 16,
                    "items": {
                        "type": "object",
                        "required": ["id", "type", "operation"],
                        "properties": {
                            "id": {"type": "string"},
                            "task_id": {"type": "string"},
                            "title": {"type": "string"},
                            "type": {"type": "string"},
                            "tool": {"type": "string"},
                            "operation": {"type": "string"},
                            "name": {"type": "string"},
                            "arguments": {"type": ["array", "object"]},
                            "args": {"type": "array", "items": {"type": "string"}},
                            "dependencies": {"type": "array", "items": {"type": "string"}},
                            "depends_on": {"type": "array", "items": {"type": "string"}},
                        },
                        "additionalProperties": False,
                    },
                },
            },
            "anyOf": [
                {"required": ["objective"]},
                {"required": ["name"]},
                {"required": ["title"]},
                {"required": ["description"]},
            ],
            "additionalProperties": False,
        },
    )

    _EXPLICIT_READ_ONLY_WORKBENCH_OPERATIONS = frozenset({
        "census",
        "preflight",
        "file:list",
    })

    _WORKBENCH_TOOL = DirectorToolSpec(
        name="workbench",
        description="Execute one isolated evidence operation through Universal Workbench. capability must be the exact fully qualified registered name prefixed with workbench: (for example workbench:file:read). Never use bare aliases such as file:read. Do not use this tool when the human requests a mission; use run_mission.",
        parameters={
            "type": "object",
            "required": ["capability"],
            "properties": {
                "capability": {"type": "string"},
                "arguments": {"type": "array", "items": {"type": "string"}},
                "options": {"type": "object"},
            },
            "additionalProperties": False,
        },
    )

    def __init__(
        self,
        *,
        router: ModelRouter,
        bootstrap: DirectorBootstrap,
        db_path: str | Path,
        max_history_turns: int = 20,
        provider: DirectorProvider | None = None,
        mission_runner: Callable[..., Any] | None = None,
        workbench: UniversalWorkbench | None = None,
    ) -> None:
        if max_history_turns < 1:
            raise DirectorConversationError("max_history_turns must be at least 1")
        self.router = router
        self.bootstrap = bootstrap
        self.db_path = Path(db_path)
        self.max_history_turns = max_history_turns
        self.provider: DirectorProvider = provider or InferenceDirectorProvider(router)
        self.mission_runner = mission_runner
        self.workbench = workbench
        self._session_locks_guard = RLock()
        self._session_locks: dict[str, RLock] = {}
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

    def _session_lock_for(self, session_id: str) -> RLock:
        # One session must have a total order for concurrent HTTP turns. The lock
        # also makes duplicate request IDs wait for the original response to be
        # persisted before attempting to replay it.
        with self._session_locks_guard:
            lock = self._session_locks.get(session_id)
            if lock is None:
                lock = RLock()
                self._session_locks[session_id] = lock
            return lock

    def _get_turn_by_id(
        self,
        session_id: str,
        turn_id: str,
    ) -> ConversationTurn | None:
        with self._connect() as db:
            row = db.execute(
                """
                SELECT turn_id, session_id, sequence, role, content, state,
                       created_at, correlation_id, model, provider,
                       execution_id, error_code
                FROM conversation_turns
                WHERE session_id = ? AND turn_id = ?
                LIMIT 1
                """,
                (session_id, turn_id),
            ).fetchone()
        return ConversationTurn(**dict(row)) if row is not None else None

    @staticmethod
    def _response_from_saved_turn(
        session: ConversationSession,
        turn: ConversationTurn,
    ) -> DirectorConversationResponse:
        """Replay a completed response without invoking inference or mission tools."""
        return DirectorConversationResponse(
            turn=turn,
            response_text=turn.content,
            state="completed",
            correlation_id=turn.correlation_id,
            context_manifest_id=session.context_manifest_id,
            model=turn.model or "unknown",
            provider=turn.provider or "unknown",
            execution_id=turn.execution_id or "",
            presentation_hints={
                "avatar_state": "SPEAKING",
                "text_available": True,
                "voice_requested": False,
                "idempotent_replay": True,
            },
        )

    @staticmethod
    def _in_progress_response(
        session: ConversationSession,
        user_turn: ConversationTurn,
    ) -> DirectorConversationResponse:
        message = (
            "Esta solicitud ya tiene un turno duradero registrado, pero su respuesta "
            "final no está guardada. Aurora no la vuelve a ejecutar automáticamente "
            "para evitar duplicar órdenes. Comprueba la misión y los eventos de esta "
            "correlación antes de volver a emitirla. "
            f"correlation_id={user_turn.correlation_id}"
        )
        pending_turn = ConversationTurn(
            turn_id=f"{user_turn.turn_id}-in-progress",
            session_id=user_turn.session_id,
            sequence=user_turn.sequence + 1,
            role="assistant",
            content=message,
            state="processing",
            created_at=_now(),
            correlation_id=user_turn.correlation_id,
        )
        return DirectorConversationResponse(
            turn=pending_turn,
            response_text=message,
            state="in_progress",
            correlation_id=user_turn.correlation_id,
            context_manifest_id=session.context_manifest_id,
            model="unknown",
            provider="unknown",
            execution_id="",
            presentation_hints={
                "avatar_state": "THINKING",
                "text_available": True,
                "voice_requested": False,
                "idempotent_replay": True,
            },
        )

    def send_message(
        self,
        *,
        session_id: str,
        user_input: str,
        model: str | None = None,
        request_id: str | None = None,
    ) -> DirectorConversationResponse:
        _require_identifier(session_id, "session_id")
        with self._session_lock_for(session_id):
            return self._send_message_locked(
                session_id=session_id,
                user_input=user_input,
                model=model,
                request_id=request_id,
            )

    def _send_message_locked(
        self,
        *,
        session_id: str,
        user_input: str,
        model: str | None = None,
        request_id: str | None = None,
    ) -> DirectorConversationResponse:
        _require_identifier(session_id, "session_id")
        prompt = user_input.strip()
        if not prompt:
            raise DirectorConversationError("user_input is empty")
        if request_id is not None:
            if not isinstance(request_id, str):
                raise DirectorConversationError("request_id must be a string or null")
            request_id = request_id.strip()
            if not request_id or len(request_id) > 256 or "\x00" in request_id:
                raise DirectorConversationError("request_id must contain 1 to 256 non-null characters")
            digest = hashlib.sha256(
                f"{session_id}\x00{request_id}".encode("utf-8")
            ).hexdigest()[:32]
            turn_id = f"turn-request-{digest}"
            correlation_id = f"corr-request-{digest}"
        else:
            turn_id = f"turn-{uuid4().hex}"
            correlation_id = f"corr-{uuid4().hex}"

        session = self.get_session(session_id)
        if request_id is not None:
            existing_user_turn = self._get_turn_by_id(session_id, turn_id)
            if existing_user_turn is not None:
                if existing_user_turn.role != "user" or existing_user_turn.content != prompt:
                    raise DirectorConversationError(
                        "request_id is already bound to different conversation input"
                    )
                existing_response = self._get_turn_by_id(
                    session_id, f"{turn_id}-response"
                )
                if existing_response is not None and existing_response.state == "completed":
                    return self._response_from_saved_turn(session, existing_response)
                existing_error = self._get_turn_by_id(
                    session_id, f"{turn_id}-error"
                )
                if existing_error is not None:
                    raise DirectorConversationError(
                        "this request_id already failed and will not be re-executed; "
                        "use a new request_id only to intentionally submit a new turn"
                    )
                return self._in_progress_response(session, existing_user_turn)

        if session.state != "active":
            raise DirectorConversationError(
                f"conversation session is not active: {session_id}"
            )

        bootstrap_state = self._initialize_bootstrap()
        policy = bootstrap_state.director_model_policy
        self._validate_requested_model(model, policy)

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
                    tools=self._conversation_tools(user_input=prompt),
                )
                if getattr(response, "tool_calls", ()) or (
                    self.mission_runner is not None
                    and self._requests_mission_execution(prompt)
                ):
                    response = self._execute_conversation_tools(
                        response=response,
                        model=model,
                        provider_messages=provider_messages,
                        user_input=prompt,
                        conversation_turn_id=turn_id,
                        correlation_id=correlation_id,
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


    _CANONICAL_WORKBENCH_CAPABILITIES = frozenset({
        "workbench:census",
        "workbench:preflight",
        "workbench:capability:resolve",
        "workbench:command",
        "workbench:research:discover",
        "workbench:research:retrieve",
        "workbench:research:retrieve_extract",
        "workbench:file:list",
        "workbench:file:read",
        "workbench:file:write",
        "workbench:artifact:intake",
        "workbench:artifact:inspect",
        "workbench:document:extract",
        "workbench:github:get",
        "workbench:github:branch",
        "workbench:github:file:write",
        "workbench:github:pr",
        "workbench:github:merge",
        "workbench:github:workflow:read",
        "workbench:project:inspect",
        "workbench:project:register",
        "workbench:project:register_github",
        "workbench:connector:census",
        "workbench:connector:request",
        "workbench:browser",
        "workbench:mcp:list_tools",
        "workbench:mcp:call",
        "workbench:computer",
    })

    @staticmethod
    def _requests_mission_execution(user_input: str | None) -> bool:
        text = str(user_input or "").casefold()
        normalized = "".join(
            character
            for character in unicodedata.normalize("NFKD", text)
            if not unicodedata.combining(character)
        )
        mission_markers = (
            "mision",
            "mission",
            "multiagente",
            "multi-agent",
            "run_mission",
            "tarea delegada",
            "tareas delegadas",
        )
        action_markers = (
            "ejecut",
            "planifica",
            "planear",
            "delega",
            "delegate",
            "execute",
            "run",
            "launch",
            "start",
            "realiza",
            "haz ",
        )
        return (
            any(marker in normalized for marker in mission_markers)
            and any(marker in normalized for marker in action_markers)
        )

    @classmethod
    def _canonical_workbench_capability(cls, requested: str) -> str:
        capability = requested.strip()
        if capability in cls._CANONICAL_WORKBENCH_CAPABILITIES:
            return capability
        candidate = f"workbench:{capability}"
        if candidate in cls._CANONICAL_WORKBENCH_CAPABILITIES:
            return candidate
        return capability

    def _conversation_tools(
        self, *, user_input: str | None = None
    ) -> tuple[DirectorToolSpec, ...]:
        tools: list[DirectorToolSpec] = []
        mission_requested = (
            self.mission_runner is not None
            and self._requests_mission_execution(user_input)
        )
        if self.mission_runner is not None:
            tools.append(self._RUN_MISSION_TOOL)
        if self.workbench is not None and not mission_requested:
            tools.append(self._WORKBENCH_TOOL)
        return tuple(tools)

    def _invoke_mission_runner(
        self,
        *,
        objective: str,
        mission_id: str | None = None,
        correlation_id: str | None = None,
        **kwargs: Any,
    ) -> Any:
        """Invoke the durable mission boundary with per-turn identity when supported.

        Compatibility with third-party/test callbacks is preserved, while the
        canonical factory callback receives both idempotency and correlation IDs.
        """
        if self.mission_runner is None:
            raise DirectorConversationError("run_mission is unavailable")
        call_kwargs: dict[str, Any] = {"objective": objective, **kwargs}
        try:
            parameters = inspect.signature(self.mission_runner).parameters
        except (TypeError, ValueError):
            parameters = {}
        accepts_extra = any(
            item.kind is inspect.Parameter.VAR_KEYWORD
            for item in parameters.values()
        )
        if mission_id and (accepts_extra or "mission_id" in parameters):
            call_kwargs["mission_id"] = mission_id
        if correlation_id and (accepts_extra or "correlation_id" in parameters):
            call_kwargs["correlation_id"] = correlation_id
        return self.mission_runner(**call_kwargs)

    def _execute_conversation_tools(
        self,
        *,
        response: Any,
        model: str | None,
        provider_messages: list[dict[str, Any]],
        user_input: str | None = None,
        conversation_turn_id: str | None = None,
        correlation_id: str | None = None,
    ) -> Any:
        calls = tuple(getattr(response, "tool_calls", ()) or ())
        results: list[dict[str, Any]] = []
        media_parts: list[dict[str, Any]] = []

        mission_requested = (
            self.mission_runner is not None
            and self._requests_mission_execution(user_input)
        )
        if mission_requested:
            # A model may ignore the offered tool list and select the direct
            # Workbench anyway. Never let that silently replace a mission.
            mission_calls = tuple(
                call for call in calls if call.name == self._RUN_MISSION_TOOL.name
            )
            if mission_calls:
                calls = mission_calls
            else:
                calls = ()
                objective = str(user_input or "").strip()
                try:
                    mission = self._invoke_mission_runner(
                        objective=objective,
                        mission_id=(
                            f"mission-conversation-{conversation_turn_id}"
                            if conversation_turn_id else None
                        ),
                        correlation_id=correlation_id,
                    )
                    evidence = self._mission_evidence(mission)
                    evidence["routing_fallback"] = (
                        "explicit_mission_request_without_run_mission_tool_call"
                    )
                    results.append(evidence)
                except Exception as exc:
                    results.append({
                        "status": "failed",
                        "objective": objective,
                        "error": str(exc)[:4000],
                        "routing_fallback": (
                            "explicit_mission_request_without_run_mission_tool_call"
                        ),
                    })

        if len(calls) > 4:
            raise DirectorConversationError("Director requested too many conversation tools")
        for call in calls:
            arguments = call.arguments if isinstance(call.arguments, dict) else {}
            if call.name == self._RUN_MISSION_TOOL.name:
                if self.mission_runner is None:
                    raise DirectorConversationError("run_mission is unavailable")
                objective, explicit_tasks = self._normalize_mission_tool_arguments(arguments)
                try:
                    call_key = hashlib.sha256(
                        str(call.call_id).encode("utf-8")
                    ).hexdigest()[:16]
                    # Distinct tool calls in one user turn are distinct commands;
                    # retries/replays of the same call keep the same durable ID.
                    mission_id = (
                        f"mission-conversation-{conversation_turn_id}-{call_key}"
                        if conversation_turn_id else None
                    )
                    if explicit_tasks is None:
                        mission = self._invoke_mission_runner(
                            objective=objective,
                            mission_id=mission_id,
                            correlation_id=correlation_id,
                        )
                    else:
                        # All missions emitted by one conversation turn share a
                        # durable idempotency key. Duplicate tool calls for the
                        # same objective return the original receipt instead of
                        # enqueueing a second task graph.
                        mission = self._invoke_mission_runner(
                            objective=objective,
                            mission_id=mission_id,
                            correlation_id=correlation_id,
                            tasks=explicit_tasks,
                            planning_model=getattr(response, "model", None),
                            planning_provider=getattr(response, "provider", None),
                            planning_execution_id=getattr(response, "execution_id", None),
                        )
                    results.append(self._mission_evidence(mission))
                except Exception as exc:
                    results.append({
                        "status": "failed",
                        "objective": objective,
                        "error": str(exc)[:4000],
                    })
                continue
            if call.name == self._WORKBENCH_TOOL.name:
                if self.workbench is None:
                    raise DirectorConversationError("workbench is unavailable")
                capability = arguments.get("capability")
                call_args = arguments.get("arguments", [])
                options = arguments.get("options", {})
                if not isinstance(capability, str) or not capability.strip():
                    raise DirectorConversationError("workbench requires a non-empty capability")
                if not isinstance(call_args, list) or not all(isinstance(item, str) for item in call_args):
                    raise DirectorConversationError("workbench arguments must be a string array")
                if not isinstance(options, dict):
                    raise DirectorConversationError("workbench options must be an object")
                requested_capability = capability.strip()
                capability = self._canonical_workbench_capability(requested_capability)
                try:
                    direct_workbench_mission_id = (
                        f"conversation:{conversation_turn_id}:{call.call_id}"
                        if conversation_turn_id
                        else f"conversation:adhoc-{uuid4().hex}:{call.call_id}"
                    )
                    result = self.workbench.execute(
                        WorkbenchRequest(
                            capability=capability,
                            actor="director",
                            mission_id=direct_workbench_mission_id,
                            reason="Director conversation direct Workbench evidence",
                            arguments=tuple(call_args),
                            options=dict(options),
                            correlation_id=correlation_id,
                            idempotency_key=(
                                f"direct-workbench:{direct_workbench_mission_id}"
                            ),
                        )
                    )
                    results.append(self._workbench_evidence(result))
                    self._append_media_parts(media_parts, result.output)
                except Exception as exc:
                    failure = {
                        "status": "failed",
                        "capability": capability,
                        "error": str(exc)[:4000],
                    }
                    if requested_capability != capability:
                        failure["requested_capability"] = requested_capability
                    results.append(failure)
                continue
            raise DirectorConversationError(
                f"Director requested unregistered conversation tool: {call.name}"
            )
        evidence = json.dumps(results, ensure_ascii=False, sort_keys=True)
        evidence_content: list[dict[str, Any]] = [{
            "modality": "text",
            "text": (
                "The following governed Aurora execution evidence is authoritative. "
                "Report only what it establishes. Do not claim success when execution failed.\n"
                + "EXECUTION_EVIDENCE:\n" + evidence
            ),
        }]
        evidence_content.extend(media_parts)
        follow_up = [
            *provider_messages,
            {"role": "user", "content": evidence_content},
        ]
        final_response = self.provider.generate(
            messages=follow_up,
            model=model,
            role="director_conversation_result",
            tools=(),
        )
        # Keep the exact execution receipt in the persisted assistant turn. The
        # summarizing model may explain the result, but cannot omit the authoritative
        # mission ID, task outcomes, errors, planning provenance, or event evidence.
        if results:
            evidence_suffix = json.dumps(results, ensure_ascii=False, sort_keys=True)
            summary = str(getattr(final_response, "content", "") or "").rstrip()
            content = (
                (summary + "\n\n" if summary else "")
                + "EVIDENCIA_AUTORITATIVA_DE_EJECUCION (JSON):\n"
                + evidence_suffix
            )
            return DirectorModelResponse(
                content=content,
                model=str(getattr(final_response, "model", getattr(response, "model", "unknown"))),
                provider=str(getattr(final_response, "provider", getattr(response, "provider", "unknown"))),
                request_id=str(getattr(final_response, "request_id", getattr(response, "request_id", "unknown"))),
                execution_id=str(getattr(final_response, "execution_id", getattr(response, "execution_id", "unknown"))),
                tool_calls=(),
            )
        return final_response

    @classmethod
    def _normalize_mission_tool_arguments(
        cls, arguments: dict[str, Any]
    ) -> tuple[str, tuple[DelegatedTask, ...] | None]:
        objective = (
            arguments.get("objective")
            or arguments.get("description")
            or arguments.get("name")
            or arguments.get("title")
        )
        if not isinstance(objective, str) or not objective.strip():
            raise DirectorConversationError(
                "run_mission requires objective, description, name, or title"
            )
        raw_tasks = arguments.get("tasks")
        if raw_tasks is None:
            return objective.strip(), None
        if not isinstance(raw_tasks, list) or not raw_tasks or len(raw_tasks) > 16:
            raise DirectorConversationError("explicit mission tasks must be a list of 1 to 16 tasks")

        tasks: list[DelegatedTask] = []
        for item in raw_tasks:
            if not isinstance(item, dict):
                raise DirectorConversationError("each explicit mission task must be an object")
            task_id = item.get("id", item.get("task_id"))
            task_type = item.get("type", item.get("tool"))
            if item.get("type") is not None and item.get("tool") is not None and item["type"] != item["tool"]:
                raise DirectorConversationError("explicit task type and tool disagree")
            operation = item.get("operation", item.get("name"))
            if item.get("operation") is not None and item.get("name") is not None and item["operation"] != item["name"]:
                raise DirectorConversationError("explicit task operation and name disagree")
            if not isinstance(task_id, str) or not task_id.strip():
                raise DirectorConversationError("explicit task id is required")
            if task_type != "workbench":
                raise DirectorConversationError("explicit tasks must use the governed Workbench")
            if not isinstance(operation, str) or operation not in cls._EXPLICIT_READ_ONLY_WORKBENCH_OPERATIONS:
                raise DirectorConversationError(
                    "explicit mission only allows read-only Workbench operations: census, preflight, file:list"
                )
            raw_arguments = item.get("arguments", item.get("args", []))
            if isinstance(raw_arguments, dict):
                if raw_arguments:
                    raise DirectorConversationError("named Workbench task arguments are not supported")
                task_args: tuple[str, ...] = ()
            elif isinstance(raw_arguments, list) and all(isinstance(value, str) for value in raw_arguments):
                task_args = tuple(raw_arguments)
            else:
                raise DirectorConversationError("WorkBench task arguments must be an empty object or string array")
            if operation in {"census", "preflight"} and task_args:
                raise DirectorConversationError(f"workbench:{operation} takes no arguments")
            if operation == "file:list" and task_args != (".",):
                raise DirectorConversationError("read-only workbench:file:list must be scoped to ['.']")
            dependencies = item.get("dependencies", item.get("depends_on", []))
            if item.get("dependencies") is not None and item.get("depends_on") is not None and item["dependencies"] != item["depends_on"]:
                raise DirectorConversationError("explicit task dependencies disagree")
            if not isinstance(dependencies, list) or not all(isinstance(dep, str) and dep.strip() for dep in dependencies):
                raise DirectorConversationError("explicit task dependencies must be a string array")
            task_title = item.get("title")
            if task_title is not None and (not isinstance(task_title, str) or not task_title.strip()):
                raise DirectorConversationError("explicit task title must be a non-empty string")
            tasks.append(DelegatedTask(
                task_id=task_id.strip(),
                role="operator",
                objective=(task_title.strip() if isinstance(task_title, str) else f"Read-only Workbench operation {operation}"),
                capability=f"workbench:{operation}",
                args=task_args,
                depends_on=tuple(dependencies),
            ))
        return objective.strip(), tuple(tasks)

    @staticmethod
    def _workbench_evidence(result: Any) -> dict[str, Any]:
        return {
            "status": result.status,
            "capability": result.capability,
            "operation": result.operation,
            "output": result.output,
            "artifact_id": result.artifact_id,
            "provenance": result.provenance,
            "receipt_id": result.receipt_id,
            "correlation_id": result.correlation_id,
            "error": result.error,
        }

    @staticmethod
    def _append_media_parts(target: list[dict[str, Any]], output: Any) -> None:
        if not isinstance(output, list):
            return
        for item in output:
            if not isinstance(item, dict):
                continue
            mime_type = item.get("mime_type") or item.get("mimeType")
            data = item.get("data")
            if not isinstance(mime_type, str) or not isinstance(data, str):
                continue
            modality = (
                "image" if mime_type.startswith("image/")
                else "audio" if mime_type.startswith("audio/")
                else "video" if mime_type.startswith("video/")
                else None
            )
            if modality:
                target.append({
                    "modality": modality,
                    "mime_type": mime_type,
                    "data": data,
                })

    @staticmethod
    def _mission_evidence(mission: Any) -> dict[str, Any]:
        # Durable results wrap the submitted plan and persistent ID inside
        # .submission. Retain compatibility with direct MissionResult callers.
        submission = getattr(mission, "submission", None)
        plan = getattr(submission, "plan", None) or getattr(mission, "plan", None)
        mission_id = getattr(mission, "mission_id", None) or getattr(submission, "mission_id", None)
        objective = getattr(mission, "objective", None) or getattr(plan, "objective", None) or ""
        tasks: list[dict[str, Any]] = []
        for item in tuple(getattr(mission, "tasks", ()) or ()):
            task = getattr(item, "task", None)
            task_status = getattr(item, "status", "unknown")
            value = getattr(task_status, "value", task_status)
            capability_result = getattr(item, "capability_result", None)
            execution = getattr(capability_result, "execution", capability_result)
            output = getattr(execution, "output", None)
            model_result = getattr(item, "model_result", None)
            durable_result = getattr(item, "durable_result", None)
            if output is None and model_result is not None:
                model_response = getattr(model_result, "response", None)
                output = getattr(model_response, "content", None)

            # DurableRuntime reconstructs completed tasks from SQLite; typed
            # live provider objects are not available on that path. Preserve the
            # persisted result instead of returning a successful task with result=null.
            if output is None and isinstance(durable_result, Mapping):
                result_type = str(durable_result.get("type", ""))
                if result_type == "model":
                    output = durable_result.get("content")
                elif result_type == "research":
                    output = {
                        "request_id": durable_result.get("request_id"),
                        "source_ids": durable_result.get("source_ids", []),
                        "claim_ids": durable_result.get("claim_ids", []),
                        "trust_state": durable_result.get("trust_state"),
                        "knowledge_artifact_ids": durable_result.get("knowledge_artifact_ids", []),
                    }
                elif "output" in durable_result:
                    output = durable_result.get("output")
                elif "stdout" in durable_result or "stderr" in durable_result:
                    output = {
                        "returncode": durable_result.get("returncode"),
                        "stdout": durable_result.get("stdout", ""),
                        "stderr": durable_result.get("stderr", ""),
                        "truncated": durable_result.get("truncated", False),
                    }

            artifact_id = getattr(execution, "artifact_id", None)
            receipt_id = getattr(execution, "receipt_id", None)
            provenance = getattr(execution, "provenance", None)
            correlation_id = getattr(execution, "correlation_id", None)
            if isinstance(durable_result, Mapping):
                artifact_id = artifact_id or durable_result.get("artifact_id")
                receipt_id = receipt_id or durable_result.get("receipt_id")
                provenance = provenance or durable_result.get("provenance")
                correlation_id = correlation_id or durable_result.get("correlation_id")
            tasks.append({
                "task_id": getattr(task, "task_id", None),
                "role": getattr(task, "role", None),
                "status": str(value),
                "error": getattr(item, "error", None),
                "capability": getattr(task, "capability", None),
                "depends_on": list(getattr(task, "depends_on", ()) or ()),
                "result": DirectorConversationService._bound_evidence_value(output),
                "artifact_id": artifact_id,
                "receipt_id": receipt_id,
                "provenance": provenance,
                "correlation_id": correlation_id,
            })

        wait_timed_out = bool(getattr(mission, "wait_timed_out", False))
        wait_timeout_seconds = getattr(mission, "wait_timeout_seconds", None)
        failed = any(item["status"] == "failed" for item in tasks)
        blocked = any(item["status"] == "blocked" for item in tasks)
        mission_status = getattr(mission, "status", None)
        mission_status = getattr(mission_status, "value", mission_status)
        if wait_timed_out:
            # The request's wait timer expired, not the accepted mission. Until
            # DirectorRuntime finalizes the durable order, the conversation must
            # not override this receipt with one task's intermediate failed state.
            status = "running"
        elif failed:
            status = "failed"
        elif blocked:
            status = "blocked"
        elif mission_status:
            # Report the durable terminal decision, including a final review block.
            status = str(mission_status)
        elif tasks and all(item["status"] == "succeeded" for item in tasks):
            status = "succeeded"
        else:
            status = "no_tasks" if not tasks else "failed"

        events = tuple(getattr(mission, "events", ()) or ())
        durable_events = [
            {
                "event_id": getattr(event, "event_id", None),
                "event_type": getattr(event, "event_type", None),
                "task_id": getattr(event, "task_id", None),
                "payload": DirectorConversationService._bound_evidence_value(
                    getattr(event, "payload", None), max_chars=1200
                ),
            }
            for event in events
        ]
        return {
            "mission_id": mission_id,
            "accepted": bool(mission_id and submission is not None),
            "status": status,
            "wait_timed_out": wait_timed_out,
            "wait_timeout_seconds": wait_timeout_seconds,
            "objective": objective,
            "model": getattr(plan, "model", None),
            "provider": getattr(plan, "provider", None),
            "planning_execution_id": getattr(plan, "execution_id", None),
            "task_count": len(tasks),
            "tasks": tasks,
            "persistence_evidence": {
                "mission_id_present": bool(mission_id),
                "task_ids_returned_by_runtime": [item["task_id"] for item in tasks],
                "durable_event_count": len(durable_events),
                "durable_events": durable_events,
            },
        }

    @staticmethod
    def _bound_evidence_value(value: Any, *, max_chars: int = 4000) -> Any:
        if value is None:
            return None
        try:
            serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
            decoded = json.loads(serialized)
        except (TypeError, ValueError):
            serialized = str(value)
            decoded = serialized
        if len(serialized) <= max_chars:
            return decoded
        return {"truncated": True, "preview": serialized[:max_chars]}

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
