"""HTTP-neutral command boundary for the Aurora Director conversation service."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Callable, Mapping

from aurora.orchestration.director_conversation import (
    DirectorConversationError,
    DirectorConversationService,
)


class DashboardConversationAPIError(RuntimeError):
    """Raised when the dashboard conversation command cannot be completed."""


Authorizer = Callable[[str, Mapping[str, Any]], None]


class DashboardConversationAPI:
    """Small governed command facade over DirectorConversationService."""

    def __init__(
        self,
        service: DirectorConversationService,
        *,
        authorizer: Authorizer | None = None,
    ) -> None:
        self.service = service
        self.authorizer = authorizer

    def create_session(
        self,
        *,
        session_id: str | None = None,
        principal: str = "dashboard",
    ) -> dict[str, Any]:
        self._authorize(principal, {"operation": "conversation.create_session"})
        try:
            return asdict(self.service.create_session(session_id=session_id))
        except DirectorConversationError as exc:
            raise DashboardConversationAPIError(str(exc)) from exc

    def send_turn(
        self,
        *,
        session_id: str,
        user_input: str,
        model: str | None = None,
        principal: str = "dashboard",
    ) -> dict[str, Any]:
        self._authorize(
            principal,
            {
                "operation": "conversation.send_turn",
                "session_id": session_id,
            },
        )
        try:
            response = self.service.send_message(
                session_id=session_id,
                user_input=user_input,
                model=model,
            )
        except DirectorConversationError as exc:
            raise DashboardConversationAPIError(str(exc)) from exc
        return {
            "session": asdict(self.service.get_session(session_id)),
            "turn": asdict(response.turn),
            "response_text": response.response_text,
            "state": response.state,
            "correlation_id": response.correlation_id,
            "context_manifest_id": response.context_manifest_id,
            "model": response.model,
            "provider": response.provider,
            "execution_id": response.execution_id,
            "presentation_hints": dict(response.presentation_hints),
        }

    def get_turns(
        self,
        *,
        session_id: str,
        limit: int = 20,
        principal: str = "dashboard",
    ) -> dict[str, Any]:
        self._authorize(
            principal,
            {
                "operation": "conversation.get_turns",
                "session_id": session_id,
            },
        )
        try:
            session = self.service.get_session(session_id)
            turns = self.service.list_turns(session_id, limit=limit)
        except DirectorConversationError as exc:
            raise DashboardConversationAPIError(str(exc)) from exc
        return {
            "session": asdict(session),
            "turns": [asdict(item) for item in turns],
        }

    def _authorize(self, principal: str, request: Mapping[str, Any]) -> None:
        if not principal.strip():
            raise DashboardConversationAPIError("principal is empty")
        if self.authorizer is not None:
            try:
                self.authorizer(principal, request)
            except DashboardConversationAPIError:
                raise
            except Exception as exc:
                raise DashboardConversationAPIError(
                    f"conversation command authorization failed: {exc}"
                ) from exc


__all__ = ["DashboardConversationAPI", "DashboardConversationAPIError", "Authorizer"]
