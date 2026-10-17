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
    project_id: str | None = None


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
    execution_status: str | None = None


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
        # Optional durable status reader used to refresh an accepted mission when
        # the browser resends the same stable request_id after a wait timeout.
        self.mission_status_reader: Callable[[str], Any] | None = None
        self._session_locks_guard = RLock()
        self._session_locks: dict[str, RLock] = {}
        self.router.configure_context_gate(bootstrap.context_gate, enforce=True)
        self._initialize_schema()

    def create_session(
        self, *, session_id: str | None = None, project_id: str | None = None
    ) -> ConversationSession:
        state = self._initialize_bootstrap()
        project_id = self._validate_project_id(project_id)
        session = session_id or f"conv-{uuid4().hex}"
        _require_identifier(session, "session_id")
        now = _now()
        with self._connect() as db:
            try:
                db.execute(
                    """
                    INSERT INTO conversation_sessions (
                        session_id, created_at, updated_at, state,
                        bootstrap_id, context_manifest_id, project_id
                    ) VALUES (?, ?, ?, 'active', ?, ?, ?)
                    """,
                    (session, now, now, state.boot_id, state.context.manifest_id, project_id),
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
            project_id=project_id,
        )

    def get_session(self, session_id: str) -> ConversationSession:
        _require_identifier(session_id, "session_id")
        with self._connect() as db:
            row = db.execute(
                """
                SELECT session_id, created_at, updated_at, state,
                       bootstrap_id, context_manifest_id, project_id
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
            execution_status=DirectorConversationService._execution_status_from_content(
                turn.content
            ),
        )

    _EXECUTION_EVIDENCE_MARKER = "EVIDENCIA_AUTORITATIVA_DE_EJECUCION (JSON):"

    @classmethod
    def _execution_evidence_payload(
        cls, content: str
    ) -> tuple[int, list[dict[str, Any]]] | None:
        marker_index = content.rfind(cls._EXECUTION_EVIDENCE_MARKER)
        if marker_index < 0:
            return None
        encoded = content[marker_index + len(cls._EXECUTION_EVIDENCE_MARKER):].strip()
        try:
            payload = json.loads(encoded)
        except (TypeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, list):
            return None
        return marker_index, [
            dict(item) for item in payload if isinstance(item, Mapping)
        ]

    @classmethod
    def _execution_status_from_content(cls, content: str) -> str | None:
        parsed = cls._execution_evidence_payload(content)
        if parsed is None:
            return None
        _marker_index, results = parsed
        missions = [
            item for item in results
            if item.get("accepted") is True
            and isinstance(item.get("mission_id"), str)
        ]
        if not missions:
            return None
        statuses = [
            "running" if item.get("wait_timed_out") is True
            else str(item.get("status") or "unknown").strip().lower()
            for item in missions
        ]
        if "running" in statuses:
            return "running"
        if "failed" in statuses:
            return "failed"
        if "blocked" in statuses:
            return "blocked"
        if all(status in {"succeeded", "complete", "completed"} for status in statuses):
            return "succeeded"
        return statuses[0] if len(set(statuses)) == 1 else "unknown"

    def _refresh_saved_mission_response(
        self,
        session: ConversationSession,
        turn: ConversationTurn,
    ) -> DirectorConversationResponse:
        """Refresh a cached running mission receipt without re-running its turn."""
        saved = self._response_from_saved_turn(session, turn)
        if saved.execution_status != "running" or self.mission_status_reader is None:
            return saved
        parsed = self._execution_evidence_payload(turn.content)
        if parsed is None:
            return saved
        _marker_index, evidence = parsed
        changed = False
        for index, item in enumerate(evidence):
            if (
                item.get("accepted") is not True
                or item.get("status") != "running"
                or not isinstance(item.get("mission_id"), str)
                or not item["mission_id"].strip()
            ):
                continue
            try:
                current = self.mission_status_reader(item["mission_id"])
            except Exception:
                # An unavailable status read is not a reason to replay execution.
                continue
            if current is None:
                continue
            refreshed = self._mission_evidence(current)
            if refreshed != item:
                evidence[index] = refreshed
                changed = True
        if not changed:
            return saved

        # Prefer deterministic status text over an old model summary when the
        # same request_id is replayed. The structured receipt remains authoritative.
        content = (
            "Estado actualizado de la orden durable de Aurora.\n\n"
            + self._EXECUTION_EVIDENCE_MARKER
            + "\n"
            + json.dumps(evidence, ensure_ascii=False, sort_keys=True)
        )
        self._update_saved_turn_content(
            turn.session_id, turn.turn_id, content
        )
        updated = self._get_turn_by_id(turn.session_id, turn.turn_id)
        return (
            self._response_from_saved_turn(session, updated)
            if updated is not None else saved
        )

    def _update_saved_turn_content(
        self,
        session_id: str,
        turn_id: str,
        content: str,
    ) -> None:
        with self._connect() as db:
            db.execute(
                """
                UPDATE conversation_turns
                SET content = ?
                WHERE session_id = ? AND turn_id = ? AND state = 'completed'
                """,
                (content, session_id, turn_id),
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
            execution_status="running",
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
        if (
            request_id is None
            and self.mission_runner is not None
            and self._requests_mission_execution(prompt)
        ):
            raise DirectorConversationError(
                "request_id is required for durable mission execution so retries "
                "can replay the accepted order without creating a duplicate"
            )
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
                    return self._refresh_saved_mission_response(
                        session, existing_response
                    )
                existing_error = self._get_turn_by_id(
                    session_id, f"{turn_id}-error"
                )
                if existing_error is not None:
                    raise DirectorConversationError(
                        "this request_id already failed and will not be re-executed; "
                        "use a new request_id only to intentionally submit a new turn"
                    )
                # Recover an interrupted turn. The mission runner receives a stable
                # mission ID derived from this durable turn, so a mission already
                # accepted before a crash is replayed rather than enqueued twice.
                recovering_turn = True
            else:
                recovering_turn = False
        else:
            recovering_turn = False

        if session.state != "active":
            raise DirectorConversationError(
                f"conversation session is not active: {session_id}"
            )

        bootstrap_state = self._initialize_bootstrap()
        policy = bootstrap_state.director_model_policy
        self._validate_requested_model(model, policy)

        next_sequence = self._next_sequence(session_id)
        if not recovering_turn:
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
                **({"project_id": session.project_id} if session.project_id else {}),
                "task_type": "conversation",
                "allowed_models": self._preferred_models(policy),
            },
        )

        try:
            provider_messages = [
                {"role": "system", "content": self.SYSTEM_PROMPT},
                *bootstrap_state.messages,
                *[
                    {"role": item.role, "content": item.content}
                    for item in history
                    if item.state == "completed" and item.role in {"user", "assistant"}
                ],
            ]
            tools = self._conversation_tools(user_input=prompt)
            response = self._generate_provider(
                messages=provider_messages,
                model=model,
                role="director_conversation",
                tools=tools,
                metadata=request.metadata,
                capability=str(policy["default_capability"]),
                preferred_models=self._preferred_models(policy),
            )
            if (
                request_id is None
                and self.mission_runner is not None
                and any(
                    getattr(call, "name", None) == self._RUN_MISSION_TOOL.name
                    for call in (getattr(response, "tool_calls", ()) or ())
                )
            ):
                raise DirectorConversationError(
                    "request_id is required before executing a provider-requested mission"
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
                    metadata=request.metadata,
                    capability=str(policy["default_capability"]),
                    preferred_models=self._preferred_models(policy),
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
            execution_status=self._execution_status_from_content(response.content),
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
        """Distinguish execution requests from information questions and single reads."""
        text = str(user_input or "").casefold()
        normalized = "".join(
            character
            for character in unicodedata.normalize("NFKD", text)
            if not unicodedata.combining(character)
        ).strip().lstrip("¿¡- ").strip()
        if not normalized:
            return False

        # Informational questions should not create missions merely because they
        # mention a tool, mission, or action word.
        informational_prefixes = (
            "que es ", "que hace ", "que significa ", "por que ", "porque ",
            "como funciona ", "explica ", "describe ", "what is ", "what does ",
            "how does ", "why does ", "explain ", "describe ", "how do i ",
            "how can i ", "how to ", "como puedo ", "como se ",
        )
        if normalized.startswith(informational_prefixes):
            return False

        # Broad verbs cover project work and multi-step tasks even when the user
        # does not use the word "mission/misión". Plain read/list/open requests are
        # intentionally omitted so a single Workbench evidence read stays direct.
        execution_markers = (
            "implement", "corrig", "arregl", "repar", "modific", "actualiz",
            "integra", "complet", "termina", "continua", "sigue", "ejecut",
            "realiza", "lanza", "inicia", "constru", "desarroll", "audita",
            "audit", "verifica", "comprueba", "prueba", "testea", "investiga",
            "investigate", "review", "revisa", "check", "verify", "fix", "repair",
            "build", "refactor", "inspect", "analyz", "analyse", "analiz",
            "compara", "run tests", "run the tests", "deploy", "create", "write",
            "delegate", "execute", "launch", "continue with", "finish", "complete",
            "resolve", "examine", "inspecciona", "mision", "mission", "multiagente",
            "multi-agent", "run_mission", "tarea delegada", "tareas delegadas",
        )
        tokens = normalized.split()
        return any(
            marker in normalized if " " in marker
            else any(token.startswith(marker) for token in tokens)
            for marker in execution_markers
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

    def _generate_provider(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str | None,
        role: str,
        tools: tuple[DirectorToolSpec, ...],
        metadata: Mapping[str, Any] | None = None,
        capability: str = "reasoning",
        preferred_models: tuple[str, ...] = (),
    ) -> Any:
        """Call any registered Director adapter with supported routing context."""
        kwargs: dict[str, Any] = {
            "messages": messages,
            "model": model,
            "role": role,
            "tools": tools,
        }
        try:
            parameters = inspect.signature(self.provider.generate).parameters
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
        return self.provider.generate(**kwargs)

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
        # Only pass optional context fields to callbacks that explicitly support
        # them. This preserves older/test adapters while the canonical runtime
        # receives project_id and correlation identity.
        if not accepts_extra and parameters:
            call_kwargs = {
                key: value
                for key, value in call_kwargs.items()
                if key in parameters
            }
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
        metadata: Mapping[str, Any] | None = None,
        capability: str = "reasoning",
        preferred_models: tuple[str, ...] = (),
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
                mission = None
                try:
                    mission = self._invoke_mission_runner(
                        objective=objective,
                        mission_id=(
                            f"mission-conversation-{conversation_turn_id}-0"
                            if conversation_turn_id else None
                        ),
                        correlation_id=correlation_id,
                        project_id=(metadata or {}).get("project_id"),
                    )
                    evidence = self._mission_evidence(mission)
                    evidence["routing_fallback"] = (
                        "explicit_mission_request_without_run_mission_tool_call"
                    )
                    results.append(evidence)
                except Exception as exc:
                    evidence = self._degraded_mission_evidence(
                        mission,
                        objective=objective,
                        project_id=(metadata or {}).get("project_id"),
                        error=exc,
                    )
                    evidence["routing_fallback"] = (
                        "explicit_mission_request_without_run_mission_tool_call"
                    )
                    results.append(evidence)

        if len(calls) > 4:
            raise DirectorConversationError("Director requested too many conversation tools")
        for call_index, call in enumerate(calls):
            arguments = call.arguments if isinstance(call.arguments, dict) else {}
            if call.name == self._RUN_MISSION_TOOL.name:
                mission = None
                objective_hint = (
                    arguments.get("objective")
                    or arguments.get("description")
                    or arguments.get("name")
                    or arguments.get("title")
                )
                objective = (
                    objective_hint.strip()
                    if isinstance(objective_hint, str) and objective_hint.strip()
                    else "Invalid run_mission request"
                )
                if self.mission_runner is None:
                    results.append({
                        "tool": "run_mission",
                        "call_index": call_index,
                        "accepted": False,
                        "status": "failed",
                        "objective": objective,
                        "error": "run_mission is unavailable",
                    })
                    continue
                try:
                    objective, explicit_tasks = self._normalize_mission_tool_arguments(arguments)
                    # A durable turn slot is stable even if a provider retry
                    # regenerates different tool-call IDs or arguments. Different
                    # mission calls in one turn keep distinct slots; changed content
                    # at an already-used slot fails closed at DurableDirectorService.
                    mission_id = (
                        f"mission-conversation-{conversation_turn_id}-{call_index}"
                        if conversation_turn_id else None
                    )
                    if explicit_tasks is None:
                        mission = self._invoke_mission_runner(
                            objective=objective,
                            mission_id=mission_id,
                            correlation_id=correlation_id,
                            project_id=(metadata or {}).get("project_id"),
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
                            project_id=(metadata or {}).get("project_id"),
                        )
                    results.append(self._mission_evidence(mission))
                except Exception as exc:
                    evidence = self._degraded_mission_evidence(
                        mission,
                        objective=objective,
                        project_id=(metadata or {}).get("project_id"),
                        error=exc,
                    )
                    evidence.setdefault("accepted", False)
                    evidence["tool"] = "run_mission"
                    evidence["call_index"] = call_index
                    results.append(evidence)
                continue
            if call.name == self._WORKBENCH_TOOL.name:
                if self.workbench is None:
                    results.append({
                        "tool": "workbench",
                        "call_index": call_index,
                        "accepted": False,
                        "status": "failed",
                        "error": "workbench is unavailable",
                    })
                    continue
                capability = arguments.get("capability")
                call_args = arguments.get("arguments", [])
                options = arguments.get("options", {})
                validation_error = None
                if not isinstance(capability, str) or not capability.strip():
                    validation_error = "workbench requires a non-empty capability"
                elif not isinstance(call_args, list) or not all(isinstance(item, str) for item in call_args):
                    validation_error = "workbench arguments must be a string array"
                elif not isinstance(options, dict):
                    validation_error = "workbench options must be an object"
                if validation_error is not None:
                    results.append({
                        "tool": "workbench",
                        "call_index": call_index,
                        "accepted": False,
                        "status": "failed",
                        "error": validation_error,
                    })
                    continue
                requested_capability = capability.strip()
                capability = self._canonical_workbench_capability(requested_capability)
                try:
                    # Provider call IDs may change when an interrupted turn is
                    # regenerated. Use the deterministic turn slot so a retry reaches
                    # the same durable Workbench idempotency record and cannot repeat a
                    # completed or ambiguously-started side effect under a new key.
                    direct_workbench_mission_id = (
                        f"conversation:{conversation_turn_id}:{call_index}"
                        if conversation_turn_id
                        else f"conversation:adhoc-{uuid4().hex}:{call_index}"
                    )
                    workbench_options = dict(options)
                    workbench_options.pop("correlation_id", None)
                    workbench_options["work_id"] = direct_workbench_mission_id
                    scoped_project_id = (metadata or {}).get("project_id")
                    if isinstance(scoped_project_id, str) and scoped_project_id.strip():
                        # A project-scoped conversation cannot be redirected to a
                        # different project by model-generated tool options.
                        workbench_options["project_id"] = scoped_project_id
                    result = self.workbench.execute(
                        WorkbenchRequest(
                            capability=capability,
                            actor="director",
                            mission_id=direct_workbench_mission_id,
                            reason="Director conversation direct Workbench evidence",
                            arguments=tuple(call_args),
                            options=workbench_options,
                            correlation_id=correlation_id,
                            idempotency_key=(
                                f"direct-workbench:{direct_workbench_mission_id}"
                                if getattr(self.workbench, "session_store", None) is not None
                                else None
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
            # A malformed/unregistered later tool call must not discard durable
            # results from earlier calls in this same provider response.
            results.append({
                "tool": str(call.name or "unknown"),
                "call_index": call_index,
                "accepted": False,
                "status": "failed",
                "error": f"Director requested unregistered conversation tool: {call.name}",
            })
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
        try:
            final_response = self._generate_provider(
                messages=follow_up,
                model=model,
                role="director_conversation_result",
                tools=(),
                metadata=metadata,
                capability=capability,
                preferred_models=preferred_models,
            )
        except Exception as exc:
            # Governed execution has already produced durable receipts. A second
            # provider outage must not turn an accepted order into a failed
            # conversation turn or hide the mission ID from the caller.
            # Preserve the initial provider metadata only as provenance; the JSON
            # execution evidence appended below remains the source of truth.
            final_response = DirectorModelResponse(
                content=(
                    "La ejecución ya produjo recibos de Aurora, pero no se pudo "
                    "generar el resumen conversacional. Consulta la evidencia "
                    "autoritativa adjunta para conocer el estado real de la orden."
                ),
                model="aurora/durable-receipt-fallback",
                provider="aurora_runtime",
                request_id=f"receipt-fallback-request-{conversation_turn_id or uuid4().hex}",
                execution_id=f"receipt-fallback-execution-{conversation_turn_id or uuid4().hex}",
                tool_calls=(),
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
        # Workbench outputs are adapter-owned and are not guaranteed to be
        # JSON-native (for example Path/bytes/custom result objects). A successful
        # operation must still produce a persistable execution receipt instead
        # of failing the whole conversation while encoding a later tool result.
        output = DirectorConversationService._bound_evidence_value(
            result.output, max_chars=4000
        )
        return {
            "status": DirectorConversationService._bound_evidence_value(
                result.status, max_chars=200
            ),
            "capability": DirectorConversationService._bound_evidence_value(
                result.capability, max_chars=200
            ),
            "operation": DirectorConversationService._bound_evidence_value(
                result.operation, max_chars=200
            ),
            "output": output,
            "output_truncated": bool(
                isinstance(output, Mapping) and output.get("truncated") is True
            ),
            "artifact_id": DirectorConversationService._bound_evidence_value(
                result.artifact_id, max_chars=500
            ),
            "provenance": DirectorConversationService._bound_evidence_value(
                result.provenance, max_chars=1000
            ),
            "receipt_id": DirectorConversationService._bound_evidence_value(
                result.receipt_id, max_chars=500
            ),
            "correlation_id": DirectorConversationService._bound_evidence_value(
                result.correlation_id, max_chars=500
            ),
            "error": DirectorConversationService._bound_evidence_value(
                result.error, max_chars=1000
            ),
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
    def _degraded_mission_evidence(
        mission: Any,
        *,
        objective: str,
        project_id: str | None,
        error: BaseException,
    ) -> dict[str, Any]:
        """Preserve accepted mission identity if receipt projection itself fails."""
        submission = getattr(mission, "submission", None) if mission is not None else None
        mission_id = (
            getattr(mission, "mission_id", None) if mission is not None else None
        ) or getattr(submission, "mission_id", None)
        if not (
            isinstance(mission_id, str)
            and mission_id.strip()
            and submission is not None
        ):
            return {
                "status": "failed",
                "objective": objective,
                "error": str(error)[:4000],
            }

        raw_status = getattr(mission, "status", "running")
        raw_status = getattr(raw_status, "value", raw_status)
        status = str(raw_status or "running").strip().lower()
        if status not in {"succeeded", "failed", "blocked", "running"}:
            status = "running"

        resolved_project_id = project_id
        try:
            for event in tuple(getattr(mission, "events", ()) or ()):
                if getattr(event, "event_type", None) != "mission_submitted":
                    continue
                payload = getattr(event, "payload", None)
                if isinstance(payload, Mapping):
                    candidate = payload.get("project_id") or getattr(
                        event, "project_id", None
                    )
                    if isinstance(candidate, str) and candidate.strip():
                        resolved_project_id = candidate.strip()
                        break
        except Exception:
            # Projection is already degraded. Preserve the caller's canonical
            # project binding and the accepted mission ID rather than fail again.
            pass

        return {
            "mission_id": mission_id,
            "project_id": resolved_project_id,
            "accepted": True,
            "status": status,
            "wait_timed_out": False,
            "objective": objective,
            "task_count": 0,
            "tasks": [],
            "persistence_evidence": {
                "mission_id_present": True,
                "task_ids_returned_by_runtime": [],
                "durable_event_count": 0,
                "durable_events": [],
                "projection_error": str(error)[:1000],
            },
            "receipt_degraded": True,
        }

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
                "project_id": getattr(task, "project_id", None),
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
        elif mission_status:
            # The Director's durable terminal decision governs the mission as a
            # whole. Failed/blocked tasks from earlier phases remain in the
            # evidence, but must not contradict a later successful recovery phase.
            status = str(mission_status)
        elif failed:
            status = "failed"
        elif blocked:
            status = "blocked"
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
            "project_id": next((
                (
                    event.payload.get("project_id")
                    if event.payload.get("project_id") is not None
                    else getattr(event, "project_id", None)
                )
                for event in events
                if getattr(event, "event_type", None) == "mission_submitted"
                and isinstance(getattr(event, "payload", None), Mapping)
            ), None),
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
            project_id=session.project_id,
        )

    def _validate_project_id(self, project_id: str | None) -> str | None:
        if project_id is None:
            return None
        if not isinstance(project_id, str) or not project_id.strip() or "\x00" in project_id:
            raise DirectorConversationError("project_id must be a non-empty identifier")
        gate = getattr(self.bootstrap, "context_gate", None)
        plane = getattr(gate, "plane", None)
        registry = getattr(plane, "project_registry", None)
        if registry is None:
            raise DirectorConversationError("project-scoped conversation requires the canonical project registry")
        try:
            registry.resolve(project_id.strip())
        except Exception as exc:
            raise DirectorConversationError(f"unknown project_id: {project_id}") from exc
        return project_id.strip()

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
                    context_manifest_id TEXT NOT NULL,
                    project_id TEXT
                )
                """
            )
            session_columns = {row["name"] for row in db.execute("PRAGMA table_info(conversation_sessions)").fetchall()}
            if "project_id" not in session_columns:
                db.execute("ALTER TABLE conversation_sessions ADD COLUMN project_id TEXT")
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
