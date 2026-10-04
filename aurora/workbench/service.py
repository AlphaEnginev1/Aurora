"""Universal Work / Cowork execution boundary."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from aurora.autonomy.computer_operator import (
    ComputerAction,
    ComputerOperator,
    ComputerOperatorError,
    ComputerPolicy,
    PlaywrightSession,
)
from aurora.workbench.github import GitHubConnector, GitHubConnectorError
from aurora.workbench.models import WorkbenchCapability, WorkbenchRequest, WorkbenchResult


class WorkbenchError(RuntimeError):
    """Raised when a Workbench operation cannot be executed safely."""


class UniversalWorkbench:
    """One governed capability plane shared by Director and every agent."""

    def __init__(
        self,
        *,
        repository_root: str | Path,
        runtime_root: str | Path,
        gateway,
        audit_path: str | Path | None = None,
        configured_roots: Iterable[str | Path] = (),
    ) -> None:
        self.repository_root = Path(repository_root).resolve()
        self.runtime_root = Path(runtime_root).resolve()
        self.gateway = gateway
        self.audit_path = Path(audit_path) if audit_path else None
        roots = [self.repository_root, self.runtime_root]
        environment_roots = os.getenv("AURORA_WORKBENCH_ROOTS", "").strip()
        if environment_roots:
            configured_roots = (*configured_roots, *(item for item in environment_roots.split(os.pathsep) if item.strip()))
        roots.extend(Path(item).resolve() for item in configured_roots)
        self.roots = tuple(dict.fromkeys(roots))
        self.github = GitHubConnector(gateway)
        self._browser = None

    def census(self) -> tuple[WorkbenchCapability, ...]:
        browser_available = self._playwright_available()
        return (
            WorkbenchCapability("workbench:file", "file", True, True, True, "scoped local filesystem"),
            WorkbenchCapability("workbench:artifact", "artifact", True, True, True, "hashable artifact intake"),
            WorkbenchCapability(
                "workbench:github",
                "connector",
                True,
                self.github.available(),
                self.github.available(),
                "GitHub API read connector; credential is environment-only",
            ),
            WorkbenchCapability(
                "workbench:browser",
                "browser",
                browser_available,
                browser_available,
                browser_available,
                "Playwright browser automation",
            ),
        )

    def execute(self, request: WorkbenchRequest) -> WorkbenchResult:
        if not request.actor.strip() or not request.mission_id.strip():
            raise WorkbenchError("actor and mission_id are required")
        capability = request.capability.strip()
        try:
            if capability == "workbench:census":
                result = WorkbenchResult(
                    capability,
                    "census",
                    "succeeded",
                    output=[
                        {
                            "capability_id": item.capability_id,
                            "kind": item.kind,
                            "available": item.available,
                            "authorized": item.authorized,
                            "healthy": item.healthy,
                            "details": item.details,
                        }
                        for item in self.census()
                    ],
                    provenance="aurora-workbench:census",
                )
            elif capability == "workbench:file:list":
                result = self._file_list(request)
            elif capability == "workbench:file:read":
                result = self._file_read(request)
            elif capability == "workbench:file:write":
                result = self._file_write(request)
            elif capability == "workbench:artifact:intake":
                result = self._artifact_intake(request)
            elif capability == "workbench:github:get":
                result = self._github_get(request)
            elif capability == "workbench:browser":
                result = self._browser_run(request)
            else:
                raise WorkbenchError(f"unsupported Workbench capability: {capability}")
        except (WorkbenchError, GitHubConnectorError, ComputerOperatorError) as exc:
            result = WorkbenchResult(capability, capability, "failed", error=str(exc))
        self._audit(request, result)
        if result.status == "failed":
            raise WorkbenchError(result.error or "Workbench operation failed")
        return result

    def _safe_path(self, raw: str, *, must_exist: bool = False) -> Path:
        value = raw.strip()
        if not value:
            raise WorkbenchError("path is required")
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = self.repository_root / candidate
        candidate = candidate.resolve()
        if not any(candidate == root or root in candidate.parents for root in self.roots):
            raise WorkbenchError("path is outside configured Workbench roots")
        if must_exist and not candidate.exists():
            raise WorkbenchError(f"path does not exist: {candidate}")
        return candidate

    def _file_list(self, request: WorkbenchRequest) -> WorkbenchResult:
        root = self._safe_path(request.arguments[0] if request.arguments else ".")
        if not root.is_dir():
            raise WorkbenchError(f"path is not a directory: {root}")
        entries = []
        for item in sorted(root.iterdir(), key=lambda p: p.name.lower()):
            entries.append(
                {
                    "name": item.name,
                    "kind": "directory" if item.is_dir() else "file",
                    "size": item.stat().st_size if item.is_file() else None,
                    "path": str(item.relative_to(self.repository_root)),
                }
            )
        return WorkbenchResult(request.capability, "list", "succeeded", output=entries, provenance=str(root))

    def _file_read(self, request: WorkbenchRequest) -> WorkbenchResult:
        if not request.arguments:
            raise WorkbenchError("workbench:file:read requires a path")
        path = self._safe_path(request.arguments[0], must_exist=True)
        if not path.is_file():
            raise WorkbenchError(f"path is not a file: {path}")
        max_bytes = int(request.options.get("max_bytes", 1_048_576))
        if path.stat().st_size > max_bytes:
            raise WorkbenchError(f"file exceeds max_bytes={max_bytes}")
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        try:
            output: Any = data.decode("utf-8")
        except UnicodeDecodeError:
            output = {"base64": __import__("base64").b64encode(data).decode("ascii")}
        return WorkbenchResult(
            request.capability,
            "read",
            "succeeded",
            output=output,
            artifact_id=digest[:24],
            provenance=str(path),
        )

    def _file_write(self, request: WorkbenchRequest) -> WorkbenchResult:
        if len(request.arguments) < 2:
            raise WorkbenchError("workbench:file:write requires path and content")
        path = self._safe_path(request.arguments[0])
        content = request.arguments[1]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return WorkbenchResult(
            request.capability,
            "write",
            "succeeded",
            output={"path": str(path), "bytes": path.stat().st_size},
            artifact_id=digest[:24],
            provenance=str(path),
        )

    def _artifact_intake(self, request: WorkbenchRequest) -> WorkbenchResult:
        if not request.arguments:
            raise WorkbenchError("workbench:artifact:intake requires a source path")
        source = self._safe_path(request.arguments[0], must_exist=True)
        if not source.is_file():
            raise WorkbenchError(f"artifact source is not a file: {source}")
        target_root = self.runtime_root / "artifacts"
        target_root.mkdir(parents=True, exist_ok=True)
        digest = self._sha256(source)
        target = target_root / f"{digest[:24]}-{source.name}"
        if not target.exists():
            shutil.copy2(source, target)
        return WorkbenchResult(
            request.capability,
            "intake",
            "succeeded",
            output={
                "artifact_id": digest[:24],
                "path": str(target),
                "filename": source.name,
                "size": source.stat().st_size,
                "sha256": digest,
            },
            artifact_id=digest[:24],
            provenance=str(source),
        )

    def _github_get(self, request: WorkbenchRequest) -> WorkbenchResult:
        if not request.arguments:
            raise WorkbenchError("workbench:github:get requires an API path")
        target = request.arguments[0].strip()
        if target.startswith("repo:"):
            parts = target.removeprefix("repo:").split("/", 2)
            if len(parts) < 2:
                raise WorkbenchError("repo:owner/name requires an owner and repository")
            suffix = "/" + parts[2] if len(parts) == 3 else ""
            target = self.github.repo_path(parts[0], parts[1], suffix)
        data = self.github.get(target)
        return WorkbenchResult(
            request.capability,
            "github_get",
            "succeeded",
            output=data,
            provenance="github-api",
        )

    def _browser_run(self, request: WorkbenchRequest) -> WorkbenchResult:
        if not request.arguments:
            raise WorkbenchError("workbench:browser requires a JSON action plan")
        try:
            actions = json.loads(request.arguments[0])
        except json.JSONDecodeError as exc:
            raise WorkbenchError(f"browser action plan is not valid JSON: {exc}") from exc
        if not isinstance(actions, list):
            raise WorkbenchError("browser action plan must be a JSON array")
        hosts = {
            urlparse(str(action.get("url", ""))).hostname
            for action in actions
            if isinstance(action, dict) and action.get("action") == "navigate"
        }
        hosts = {host for host in hosts if host}
        unauthorized = sorted(host for host in hosts if host.lower() not in {item.lower() for item in self.gateway.policy.allowed_hosts})
        if unauthorized:
            raise WorkbenchError("browser hosts are blocked by network policy: " + ", ".join(unauthorized))
        operator = ComputerOperator(
            ComputerPolicy(
                allowed_hosts=frozenset(hosts),
                require_https=True,
                max_steps=int(request.options.get("max_steps", 40)),
            ),
            lambda: PlaywrightSession(
                headless=bool(request.options.get("headless", True))
            ),
            audit=lambda event: self._audit_raw(request, "browser_action", event),
        )
        output = operator.run(
            actions,
            actor=request.actor,
            mission_id=request.mission_id,
        )
        return WorkbenchResult(
            request.capability,
            "browser",
            "succeeded",
            output=list(output),
            provenance="playwright",
        )

    def _playwright_available(self) -> bool:
        try:
            import playwright.sync_api  # type: ignore
        except ImportError:
            return False
        return True

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _audit(self, request: WorkbenchRequest, result: WorkbenchResult) -> None:
        self._audit_raw(
            request,
            "workbench",
            {
                "capability": request.capability,
                "status": result.status,
                "operation": result.operation,
                "artifact_id": result.artifact_id,
                "provenance": result.provenance,
                "error": result.error,
            },
        )

    def _audit_raw(self, request: WorkbenchRequest, event: str, payload: Any) -> None:
        if self.audit_path is None:
            return
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "event": event,
            "actor": request.actor,
            "mission_id": request.mission_id,
            "reason": request.reason,
            "capability": request.capability,
            "payload": payload,
        }
        with self.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + os.linesep)


__all__ = ["UniversalWorkbench", "WorkbenchError"]
