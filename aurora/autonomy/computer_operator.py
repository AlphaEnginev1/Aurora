"""Governed browser/computer operation for autonomous missions.

The Director supplies intent; the operator enforces URL/action policy and keeps
credentials behind an execution-boundary provider. Agents never receive raw
secrets and never invoke browser automation outside this boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from importlib import import_module
from typing import Mapping, Protocol, Sequence
from urllib.parse import urlparse


class ComputerOperatorError(RuntimeError):
    """Raised when a computer operation cannot be executed safely."""


class ComputerAction(str, Enum):
    NAVIGATE = "navigate"
    CLICK = "click"
    FILL = "fill"
    EXTRACT = "extract"
    SCREENSHOT = "screenshot"


@dataclass(frozen=True)
class ComputerPolicy:
    allowed_hosts: frozenset[str]
    allowed_actions: frozenset[ComputerAction] = frozenset(ComputerAction)
    require_https: bool = True
    max_steps: int = 40

    def __post_init__(self) -> None:
        if self.max_steps < 1:
            raise ComputerOperatorError("max_steps must be positive")
        if any(not host.strip() for host in self.allowed_hosts):
            raise ComputerOperatorError("allowed_hosts cannot contain empty hosts")

    def authorize_url(self, url: str) -> None:
        parsed = urlparse(url)
        if self.require_https and parsed.scheme != "https":
            raise ComputerOperatorError("computer operator requires HTTPS")
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ComputerOperatorError("computer operator requires an absolute web URL")
        if parsed.hostname.lower() not in {host.lower() for host in self.allowed_hosts}:
            raise ComputerOperatorError(
                f"computer navigation is not authorized for host {parsed.hostname}"
            )

    def authorize(self, action: ComputerAction) -> None:
        if action not in self.allowed_actions:
            raise ComputerOperatorError(f"computer action is not authorized: {action.value}")


@dataclass(frozen=True)
class CredentialRef:
    """Opaque reference to a scoped credential; it contains no secret material."""

    credential_id: str
    purpose: str
    scope: str

    def __post_init__(self) -> None:
        if not self.credential_id.strip() or not self.purpose.strip() or not self.scope.strip():
            raise ComputerOperatorError("credential references require id, purpose and scope")


class CredentialProvider(Protocol):
    def resolve(self, ref: CredentialRef) -> Mapping[str, str]:
        """Resolve credentials only at the execution boundary."""


class ComputerSession(Protocol):
    def navigate(self, url: str) -> None: ...
    def click(self, selector: str) -> None: ...
    def fill(self, selector: str, value: str) -> None: ...
    def extract(self, selector: str) -> str: ...
    def screenshot(self, path: str) -> None: ...
    def close(self) -> None: ...


class ComputerOperator:
    """Policy-enforced computer operator with bounded action count."""

    def __init__(
        self,
        policy: ComputerPolicy,
        session_factory,
        *,
        credential_provider: CredentialProvider | None = None,
        audit=None,
    ) -> None:
        self.policy = policy
        self.session_factory = session_factory
        self.credential_provider = credential_provider
        self.audit = audit

    def run(
        self,
        actions: Sequence[Mapping[str, object]],
        *,
        actor: str,
        mission_id: str,
        credential_refs: Sequence[CredentialRef] = (),
    ) -> tuple[str, ...]:
        if not actor.strip() or not mission_id.strip():
            raise ComputerOperatorError("actor and mission_id are required")
        if len(actions) > self.policy.max_steps:
            raise ComputerOperatorError("computer action plan exceeds policy max_steps")

        credentials: dict[str, Mapping[str, str]] = {}
        for ref in credential_refs:
            if self.credential_provider is None:
                raise ComputerOperatorError("credential requested without a provider")
            credentials[ref.credential_id] = self.credential_provider.resolve(ref)

        session = self.session_factory()
        outputs: list[str] = []
        try:
            for action in actions:
                kind = ComputerAction(str(action.get("action", "")))
                self.policy.authorize(kind)
                if kind is ComputerAction.NAVIGATE:
                    url = str(action.get("url", ""))
                    self.policy.authorize_url(url)
                    session.navigate(url)
                    outputs.append("")
                elif kind is ComputerAction.CLICK:
                    session.click(str(action["selector"]))
                    outputs.append("")
                elif kind is ComputerAction.FILL:
                    value = str(action.get("value", ""))
                    credential_id = action.get("credential_id")
                    if credential_id is not None:
                        ref_id = str(credential_id)
                        if ref_id not in credentials:
                            raise ComputerOperatorError("unknown credential reference")
                        field = str(action["selector"])
                        # The provider's returned value is passed directly to the
                        # browser and is never returned to the agent/output.
                        secret_value = credentials[ref_id].get(field)
                        if secret_value is None:
                            secret_value = credentials[ref_id].get("value")
                        if secret_value is None:
                            raise ComputerOperatorError(
                                f"credential {ref_id} has no value for requested field"
                            )
                        session.fill(field, secret_value)
                    else:
                        session.fill(str(action["selector"]), value)
                    outputs.append("")
                elif kind is ComputerAction.EXTRACT:
                    outputs.append(session.extract(str(action["selector"])))
                elif kind is ComputerAction.SCREENSHOT:
                    path = str(action["path"])
                    session.screenshot(path)
                    outputs.append(path)
                self._audit_event(
                    actor=actor,
                    mission_id=mission_id,
                    action=kind.value,
                    credential_used=bool(action.get("credential_id")),
                )
        finally:
            session.close()
        return tuple(outputs)

    def _audit_event(self, **payload: object) -> None:
        if self.audit is not None:
            self.audit(payload)


class PlaywrightSession:
    """Optional Playwright-backed session; imported only when actually used."""

    def __init__(self, *, headless: bool = True) -> None:
        try:
            sync_api = import_module("playwright.sync_api")
        except ImportError as exc:
            raise ComputerOperatorError(
                "Playwright is not installed; acquire/install it before using the browser operator"
            ) from exc
        self._playwright = sync_api.sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=headless)
        self._page = self._browser.new_page()

    def navigate(self, url: str) -> None:
        self._page.goto(url, wait_until="domcontentloaded")

    def click(self, selector: str) -> None:
        self._page.locator(selector).click()

    def fill(self, selector: str, value: str) -> None:
        self._page.locator(selector).fill(value)

    def extract(self, selector: str) -> str:
        return self._page.locator(selector).inner_text()

    def screenshot(self, path: str) -> None:
        self._page.screenshot(path=path, full_page=True)

    def close(self) -> None:
        self._browser.close()
        self._playwright.stop()


__all__ = [
    "ComputerAction",
    "ComputerOperator",
    "ComputerOperatorError",
    "ComputerPolicy",
    "ComputerSession",
    "CredentialProvider",
    "CredentialRef",
    "PlaywrightSession",
]
