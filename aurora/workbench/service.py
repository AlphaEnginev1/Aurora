"""Universal Work / Cowork execution boundary."""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import replace
import json
import os
import shutil
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from aurora.autonomy.capability_resolution import CapabilityResolutionRuntime
from aurora.autonomy.models import CapabilityRequest
from aurora.autonomy.policy import Policy
from aurora.workbench.artifacts import ArtifactError, ArtifactInspector
from aurora.workbench.local_computer import LocalComputerError, LocalComputerGateway
from aurora.workbench.mcp import MCPBridgeError, MCPClientBridge, MCPRegistry
from aurora.workbench.project_intake import ProjectIntakeError, ProjectIntakeService
from aurora.workbench.connectors import ConnectorClient, ConnectorError, ConnectorRegistry
from aurora.workbench.session import WorkSessionStore
from aurora.autonomy.computer_operator import (
    ComputerAction,
    ComputerOperator,
    ComputerOperatorError,
    ComputerPolicy,
    PlaywrightSession,
)
from aurora.workbench.github import GitHubConnector, GitHubConnectorError
from aurora.workbench.models import WorkbenchCapability, WorkbenchRequest, WorkbenchResult
from aurora.research.models import ResearchRequest
from aurora.research.service import ResearchService


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
        policy: Policy | None = None,
        session_store: WorkSessionStore | None = None,
        audit_path: str | Path | None = None,
        resolution: CapabilityResolutionRuntime | None = None,
        research: ResearchService | None = None,
        configured_roots: Iterable[str | Path] = (),
    ) -> None:
        self.repository_root = Path(repository_root).resolve()
        self.runtime_root = Path(runtime_root).resolve()
        self.gateway = gateway
        self.policy = policy
        self.session_store = session_store
        self.resolution = resolution
        self.research = research
        self.artifacts = ArtifactInspector()
        registry_path = self.repository_root / "config" / "mcp-registry.json"
        if not registry_path.is_file():
            registry_path = Path(__file__).resolve().parents[2] / "config" / "mcp-registry.json"
        self.mcp_registry = MCPRegistry(str(registry_path))
        self.mcp = MCPClientBridge(self.mcp_registry)
        self.local_computer = LocalComputerGateway()
        self.audit_path = Path(audit_path) if audit_path else None
        roots = [self.repository_root, self.runtime_root]
        environment_roots = os.getenv("AURORA_WORKBENCH_ROOTS", "").strip()
        if environment_roots:
            configured_roots = (*configured_roots, *(item for item in environment_roots.split(os.pathsep) if item.strip()))
        roots.extend(Path(item).resolve() for item in configured_roots)
        self.roots = tuple(dict.fromkeys(roots))
        self.github = GitHubConnector(gateway)
        connector_path = self.repository_root / "config" / "connectors.json"
        if not connector_path.is_file():
            connector_path = Path(__file__).resolve().parents[2] / "config" / "connectors.json"
        self.connectors = ConnectorClient(ConnectorRegistry(str(connector_path)), gateway)
        self.project_intake = ProjectIntakeService(
            projects_path=self.repository_root / "config" / "projects.json",
            graph_path=self.runtime_root / "project-source-graph.json",
            github=self.github,
        )
        self._browser = None

    def census(self) -> tuple[WorkbenchCapability, ...]:
        browser_available = self._playwright_available()
        computer_available = self._local_computer_available()
        connector_rows = self.connectors.census()
        connector_ready = any(
            item["enabled"]
            and (not item.get("credential_required", True) or item["token_configured"])
            for item in connector_rows
        )
        github_available = self.github.available()
        github_write = self.github.write_enabled and github_available
        github_merge = self.github.merge_enabled and github_available
        mcp_available = any(item.enabled for item in self.mcp_registry.list())
        return (
            WorkbenchCapability(
                "workbench:research",
                "research",
                self.research is not None,
                self._policy_allows("workbench:research:discover"),
                self.research is not None and self._policy_allows("workbench:research:discover"),
                "Evidence-first research discovery/retrieval through InternetGateway",
            ),
            WorkbenchCapability(
                "workbench:capability:resolve",
                "capability",
                self.resolution is not None,
                self._policy_allows("workbench:capability:resolve"),
                self.resolution is not None and self._policy_allows("workbench:capability:resolve"),
                "Governed registry-first discovery, acquisition and verification fallback",
            ),
            WorkbenchCapability(
                "workbench:file", "file", True,
                self._policy_allows("workbench:file:list"),
                self._policy_allows("workbench:file:list"),
                "scoped local filesystem",
            ),
            WorkbenchCapability(
                "workbench:artifact", "artifact", True,
                self._policy_allows("workbench:artifact:intake"),
                self._policy_allows("workbench:artifact:intake"),
                "hashable artifact intake",
            ),
            WorkbenchCapability(
                "workbench:document", "document", True,
                self._policy_allows("workbench:document:extract"),
                self._policy_allows("workbench:document:extract"),
                "bounded extraction from supported work formats",
            ),
            WorkbenchCapability(
                "workbench:github",
                "connector",
                github_available,
                self._policy_allows("workbench:github:get"),
                github_available and self._policy_allows("workbench:github:get"),
                f"GitHub read={github_available}; write={github_write}; merge={github_merge}",
            ),
            WorkbenchCapability(
                "workbench:browser",
                "browser",
                browser_available,
                self._policy_allows("workbench:browser"),
                browser_available and self._policy_allows("workbench:browser"),
                "Playwright browser automation",
            ),
            WorkbenchCapability(
                "workbench:mcp",
                "integration",
                mcp_available,
                self._policy_allows("workbench:mcp:list_tools"),
                mcp_available and self._policy_allows("workbench:mcp:list_tools"),
                "MCP registry has enabled servers; remote tool health is not actively probed",
            ),
            WorkbenchCapability(
                "workbench:connector",
                "integration",
                bool(connector_rows),
                self._policy_allows("workbench:connector:census"),
                connector_ready and self._policy_allows("workbench:connector:census"),
                "Registered external connectors; remote target health is not actively probed",
            ),
            WorkbenchCapability(
                "workbench:computer",
                "desktop",
                computer_available,
                self._policy_allows("workbench:computer"),
                computer_available and self._policy_allows("workbench:computer"),
                "Local workstation companion when installed and authenticated",
            ),
        )


    def preflight(self) -> dict[str, Any]:
        capabilities = self.census()
        rows = [
            {
                "capability_id": item.capability_id,
                "kind": item.kind,
                "available": item.available,
                "authorized": item.authorized,
                "healthy": item.healthy,
                "details": item.details,
            }
            for item in capabilities
        ]
        blocked = [item["capability_id"] for item in rows if item["available"] and not item["authorized"]]
        unavailable = [item["capability_id"] for item in rows if not item["available"]]
        degraded = [
            item["capability_id"]
            for item in rows
            if item["available"] and item["authorized"] and not item["healthy"]
        ]
        required = ("workbench:file", "workbench:artifact", "workbench:document")
        critical_failures = [
            item["capability_id"]
            for item in rows
            if item["capability_id"] in required and not item["healthy"]
        ]
        return {
            "ready": not critical_failures and not degraded,
            "critical_ready": not critical_failures,
            "capabilities": rows,
            "blocked": blocked,
            "unavailable": unavailable,
            "degraded": degraded,
            "critical_failures": critical_failures,
            "required_capabilities": list(required),
        }

    def _policy_allows(self, capability: str) -> bool:
        return self.policy is None or self.policy.allows_capability(capability)

    def execute(self, request: WorkbenchRequest) -> WorkbenchResult:
        if not request.actor.strip() or not request.mission_id.strip():
            raise WorkbenchError("actor and mission_id are required")
        capability = request.capability.strip()
        if not capability.startswith("workbench:"):
            raise WorkbenchError("UniversalWorkbench only executes workbench:* capabilities")
        if self.policy is not None and not self.policy.allows_capability(capability):
            raise WorkbenchError(f"Workbench capability is denied by policy: {capability}")
        work_id = str(request.options.get("work_id") or request.mission_id).strip()
        correlation_id = request.correlation_id or f"corr-{uuid.uuid4().hex}"
        receipt_id = f"receipt-{uuid.uuid4().hex}"
        try:
            if self.session_store is not None:
                self.session_store.create_or_get(
                    work_id=work_id,
                    mission_id=request.mission_id,
                    actor=request.actor,
                    environment=str(request.options.get("environment", "local")),
                    workspace_root=str(self.repository_root),
                )
                self.session_store.record_capability(work_id, capability)
            if capability == "workbench:research:discover":
                result = self._research_discover(request)
            elif capability == "workbench:research:retrieve":
                result = self._research_retrieve(request, extract=False)
            elif capability == "workbench:research:retrieve_extract":
                result = self._research_retrieve(request, extract=True)
            elif capability == "workbench:capability:resolve":
                result = self._capability_resolve(request)
            elif capability == "workbench:census":
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
            elif capability == "workbench:preflight":
                result = WorkbenchResult(
                    capability,
                    "preflight",
                    "succeeded",
                    output=self.preflight(),
                    provenance="aurora-workbench:preflight",
                )
            elif capability == "workbench:file:list":
                result = self._file_list(request)
            elif capability == "workbench:file:read":
                result = self._file_read(request)
            elif capability == "workbench:file:write":
                result = self._file_write(request)
            elif capability == "workbench:artifact:intake":
                result = self._artifact_intake(request)
            elif capability == "workbench:artifact:inspect":
                result = self._artifact_inspect(request)
            elif capability == "workbench:document:extract":
                result = self._document_extract(request)
            elif capability == "workbench:mcp:list_tools":
                result = self._mcp_list_tools(request)
            elif capability == "workbench:mcp:call":
                result = self._mcp_call(request)
            elif capability == "workbench:computer":
                result = self._computer_action(request)
            elif capability == "workbench:github:branch":
                result = self._github_branch(request)
            elif capability == "workbench:github:file:write":
                result = self._github_file_write(request)
            elif capability == "workbench:github:pr":
                result = self._github_pr(request)
            elif capability == "workbench:github:merge":
                result = self._github_merge(request)
            elif capability == "workbench:github:workflow:read":
                result = self._github_workflow_runs(request)
            elif capability == "workbench:project:inspect":
                result = self._project_inspect(request)
            elif capability == "workbench:project:register":
                result = self._project_register(request)
            elif capability == "workbench:project:register_github":
                result = self._project_register_github(request)
            elif capability == "workbench:connector:census":
                result = self._connector_census(request)
            elif capability == "workbench:connector:request":
                result = self._connector_request(request)
            elif capability == "workbench:github:get":
                result = self._github_get(request)
            elif capability == "workbench:browser":
                result = self._browser_run(request)
            else:
                raise WorkbenchError(f"unsupported Workbench capability: {capability}")
        except (WorkbenchError, GitHubConnectorError, ComputerOperatorError, ArtifactError, MCPBridgeError, LocalComputerError, ProjectIntakeError, ConnectorError) as exc:
            result = WorkbenchResult(capability, capability, "failed", error=str(exc))
        result = replace(
            result,
            receipt_id=receipt_id,
            correlation_id=correlation_id,
        )
        if self.session_store is not None:
            try:
                self.session_store.record_receipt(
                    work_id,
                    receipt_id,
                    correlation_id=correlation_id,
                    payload={
                        "capability": capability,
                        "operation": result.operation,
                        "status": result.status,
                        "artifact_id": result.artifact_id,
                        "provenance": result.provenance,
                        "error": result.error,
                    },
                )
                if result.artifact_id:
                    self.session_store.record_artifact(work_id, result.artifact_id)
                self.session_store.update_tool_health(
                    work_id,
                    capability,
                    "healthy" if result.status == "succeeded" else "failed",
                )
            except Exception as exc:
                raise WorkbenchError(f"failed to persist Workbench receipt: {exc}") from exc
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
                    "path": str(item.relative_to(self.repository_root)) if self.repository_root == item or self.repository_root in item.parents else str(item),
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

    def _mcp_list_tools(self, request: WorkbenchRequest) -> WorkbenchResult:
        if not request.arguments:
            raise WorkbenchError("workbench:mcp:list_tools requires server_id")
        server_id = request.arguments[0].strip()
        tools = self.mcp.list_tools(server_id)
        return WorkbenchResult(request.capability, "mcp_list_tools", "succeeded", output=tools, provenance="mcp:"+server_id)

    def _mcp_call(self, request: WorkbenchRequest) -> WorkbenchResult:
        if len(request.arguments) < 2:
            raise WorkbenchError("workbench:mcp:call requires server_id and tool name")
        server_id, name = request.arguments[0].strip(), request.arguments[1].strip()
        raw = request.arguments[2] if len(request.arguments) >= 3 else "{}"
        try:
            arguments = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise WorkbenchError("MCP tool arguments must be valid JSON") from exc
        result = self.mcp.call_tool(server_id, name, arguments)
        status = "failed" if result.get("is_error") else "succeeded"
        return WorkbenchResult(request.capability, "mcp_call", status, output=result, provenance="mcp:"+server_id)

    def _computer_action(self, request: WorkbenchRequest) -> WorkbenchResult:
        if not request.arguments:
            raise WorkbenchError("workbench:computer requires a JSON action list")
        try:
            actions = json.loads(request.arguments[0])
        except json.JSONDecodeError as exc:
            raise WorkbenchError("computer action plan must be valid JSON") from exc
        if not isinstance(actions, list):
            raise WorkbenchError("computer action plan must be a JSON array")
        output = self.local_computer.execute(actions, mission_id=request.mission_id, actor=request.actor)
        return WorkbenchResult(request.capability, "computer", "succeeded", output=output, provenance="local-computer-gateway")

    def _local_computer_available(self) -> bool:
        try:
            return self.local_computer.available()
        except LocalComputerError:
            return False
    def _artifact_inspect(self, request: WorkbenchRequest) -> WorkbenchResult:
        if not request.arguments:
            raise WorkbenchError("workbench:artifact:inspect requires a source path")
        path = self._safe_path(request.arguments[0], must_exist=True)
        descriptor = self.artifacts.inspect(path)
        return WorkbenchResult(
            request.capability,
            "inspect",
            "succeeded",
            output={
                "path": descriptor.path,
                "filename": descriptor.filename,
                "media_type": descriptor.media_type,
                "size": descriptor.size,
                "sha256": descriptor.sha256,
                "extension": descriptor.extension,
            },
            artifact_id=descriptor.sha256[:24],
            provenance=str(path),
        )

    def _document_extract(self, request: WorkbenchRequest) -> WorkbenchResult:
        if not request.arguments:
            raise WorkbenchError("workbench:document:extract requires a source path")
        path = self._safe_path(request.arguments[0], must_exist=True)
        max_chars = int(request.options.get("max_chars", 2_000_000))
        content = self.artifacts.extract_text(path, max_chars=max_chars)
        descriptor = self.artifacts.inspect(path)
        return WorkbenchResult(
            request.capability,
            "extract",
            "succeeded",
            output={
                "filename": descriptor.filename,
                "media_type": descriptor.media_type,
                "sha256": descriptor.sha256,
                "text": content,
            },
            artifact_id=descriptor.sha256[:24],
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

    @staticmethod
    def _github_json_arg(request: WorkbenchRequest, required: tuple[str, ...] = ()) -> dict[str, Any]:
        if not request.arguments:
            raise WorkbenchError("GitHub operation requires a JSON argument")
        try:
            value = json.loads(request.arguments[0])
        except json.JSONDecodeError as exc:
            raise WorkbenchError("GitHub operation argument must be valid JSON") from exc
        if not isinstance(value, dict):
            raise WorkbenchError("GitHub operation argument must be an object")
        missing = [key for key in required if key not in value]
        if missing:
            raise WorkbenchError("GitHub operation is missing required fields: " + ", ".join(missing))
        return value

    def _github_branch(self, request: WorkbenchRequest) -> WorkbenchResult:
        value = self._github_json_arg(request, ("owner", "repo", "branch", "sha"))
        result = self.github.create_branch(
            str(value["owner"]), str(value["repo"]), str(value["branch"]), str(value["sha"])
        )
        return WorkbenchResult(request.capability, "github_branch", "succeeded", output=result, provenance="github-api")

    def _github_file_write(self, request: WorkbenchRequest) -> WorkbenchResult:
        value = self._github_json_arg(request, ("owner", "repo", "path", "content", "message", "branch"))
        result = self.github.upsert_file(
            str(value["owner"]), str(value["repo"]), str(value["path"]),
            str(value["content"]), str(value["message"]), str(value["branch"]),
            str(value["sha"]) if value.get("sha") else None,
        )
        return WorkbenchResult(request.capability, "github_file_write", "succeeded", output=result, provenance="github-api")

    def _github_pr(self, request: WorkbenchRequest) -> WorkbenchResult:
        value = self._github_json_arg(request, ("owner", "repo", "title", "head", "base"))
        result = self.github.create_pull_request(
            str(value["owner"]), str(value["repo"]), str(value["title"]),
            str(value["head"]), str(value["base"]), str(value.get("body") or ""),
            bool(value.get("draft", False)),
        )
        return WorkbenchResult(request.capability, "github_pr", "succeeded", output=result, provenance="github-api")

    def _github_merge(self, request: WorkbenchRequest) -> WorkbenchResult:
        value = self._github_json_arg(request, ("owner", "repo", "number"))
        result = self.github.merge_pull_request(
            str(value["owner"]), str(value["repo"]), int(value["number"]),
            method=str(value.get("method") or "merge"),
            expected_head_sha=str(value["expected_head_sha"]) if value.get("expected_head_sha") else None,
        )
        status = "succeeded" if result.get("merged") else "failed"
        return WorkbenchResult(request.capability, "github_merge", status, output=result, provenance="github-api")

    def _github_workflow_runs(self, request: WorkbenchRequest) -> WorkbenchResult:
        value = self._github_json_arg(request, ("owner", "repo"))
        result = self.github.workflow_runs(
            str(value["owner"]), str(value["repo"]),
            branch=str(value["branch"]) if value.get("branch") else None,
            status=str(value["status"]) if value.get("status") else None,
        )
        return WorkbenchResult(request.capability, "github_workflow_runs", "succeeded", output=result, provenance="github-api")



    def _research_request(self, request: WorkbenchRequest) -> ResearchRequest:
        if not request.arguments:
            raise WorkbenchError(
                "research capability requires a JSON request object"
            )
        try:
            value = json.loads(request.arguments[0])
        except json.JSONDecodeError as exc:
            raise WorkbenchError("research request must be valid JSON") from exc
        if not isinstance(value, dict):
            raise WorkbenchError("research request must be an object")
        request_id = str(value.get("request_id") or f"research-{request.mission_id}").strip()
        objective = str(value.get("objective") or request.reason).strip()
        required_sources = value.get("required_sources", [])
        constraints = value.get("constraints", [])
        max_sources = value.get("max_sources", 10)
        if not isinstance(required_sources, list) or not all(isinstance(item, str) for item in required_sources):
            raise WorkbenchError("required_sources must be a string array")
        if not isinstance(constraints, list) or not all(isinstance(item, str) for item in constraints):
            raise WorkbenchError("constraints must be a string array")
        if not isinstance(max_sources, int) or isinstance(max_sources, bool) or max_sources < 1:
            raise WorkbenchError("max_sources must be a positive integer")
        return ResearchRequest(
            request_id=request_id,
            objective=objective,
            required_sources=tuple(required_sources),
            constraints=tuple(constraints),
            max_sources=max_sources,
        )

    def _research_discover(self, request: WorkbenchRequest) -> WorkbenchResult:
        if self.research is None:
            raise WorkbenchError("research service is not configured")
        research_request = self._research_request(request)
        urls = self.research.discover(research_request)
        return WorkbenchResult(
            request.capability,
            "research_discover",
            "succeeded",
            output={"request_id": research_request.request_id, "objective": research_request.objective, "urls": list(urls)},
            provenance="aurora-research:discovery",
        )

    def _research_retrieve(self, request: WorkbenchRequest, *, extract: bool) -> WorkbenchResult:
        if self.research is None:
            raise WorkbenchError("research service is not configured")
        research_request = self._research_request(request)
        if extract:
            sources, claims = self.research.retrieve_and_extract(research_request)
            output = {
                "request_id": research_request.request_id,
                "sources": [
                    {
                        "source_id": item.source_id,
                        "url": item.url,
                        "final_url": item.final_url,
                        "status_code": item.status_code,
                        "content_type": item.content_type,
                        "content_sha256": item.content_sha256,
                        "content_text": item.content_text,
                    }
                    for item in sources
                ],
                "claims": [
                    {
                        "claim_id": item.claim_id,
                        "source_id": item.source_id,
                        "claim": item.claim,
                        "state": item.state.value,
                        "extraction_confidence": item.extraction_confidence,
                    }
                    for item in claims
                ],
            }
            operation = "research_retrieve_extract"
        else:
            sources = self.research.retrieve(research_request)
            output = {
                "request_id": research_request.request_id,
                "sources": [
                    {
                        "source_id": item.source_id,
                        "url": item.url,
                        "final_url": item.final_url,
                        "status_code": item.status_code,
                        "content_type": item.content_type,
                        "content_sha256": item.content_sha256,
                        "content_text": item.content_text,
                    }
                    for item in sources
                ],
            }
            operation = "research_retrieve"
        return WorkbenchResult(
            request.capability,
            operation,
            "succeeded",
            output=output,
            provenance="aurora-research:retrieval",
        )

    def _capability_resolve(self, request: WorkbenchRequest) -> WorkbenchResult:
        if self.resolution is None:
            raise WorkbenchError("capability resolution is not configured")
        if not request.arguments:
            raise WorkbenchError(
                "workbench:capability:resolve requires a JSON request object"
            )
        try:
            value = json.loads(request.arguments[0])
        except json.JSONDecodeError as exc:
            raise WorkbenchError(
                "capability resolution request must be valid JSON"
            ) from exc
        if not isinstance(value, dict):
            raise WorkbenchError("capability resolution request must be an object")
        name = str(value.get("name") or "").strip()
        if not name:
            raise WorkbenchError("capability resolution request requires name")
        constraints = value.get("constraints", {})
        if not isinstance(constraints, dict):
            raise WorkbenchError("capability resolution constraints must be an object")
        resolution = self.resolution.ensure(
            CapabilityRequest(
                name=name,
                actor=request.actor,
                reason=request.reason,
                constraints=dict(constraints),
            )
        )
        capability = resolution.result
        status = (
            "succeeded"
            if capability.status in {"installed", "already_available"}
            else "failed"
        )
        return WorkbenchResult(
            request.capability,
            "capability_resolution",
            status,
            output={
                "name": capability.name,
                "status": capability.status,
                "version": capability.version,
                "source": capability.source,
                "verification": capability.verification,
                "path": capability.path,
                "sha256": capability.sha256,
                "artifact_id": capability.artifact_id,
                "discovered": list(resolution.discovered),
                "attempts": resolution.attempts,
            },
            artifact_id=capability.artifact_id,
            provenance="aurora-capability-resolution",
            error=None if status == "succeeded" else (
                f"capability resolution ended with status={capability.status}"
            ),
        )

    def _connector_census(self, request: WorkbenchRequest) -> WorkbenchResult:
        return WorkbenchResult(
            request.capability,
            "connector_census",
            "succeeded",
            output=self.connectors.census(),
            provenance="aurora-connector-registry",
        )

    def _connector_request(self, request: WorkbenchRequest) -> WorkbenchResult:
        if len(request.arguments) < 1:
            raise WorkbenchError("workbench:connector:request requires JSON arguments")
        try:
            value = json.loads(request.arguments[0])
        except json.JSONDecodeError as exc:
            raise WorkbenchError("connector request must be valid JSON") from exc
        if not isinstance(value, dict):
            raise WorkbenchError("connector request must be an object")
        for field in ("connector_id",):
            if field not in value:
                raise WorkbenchError(f"connector request missing field: {field}")
        result = self.connectors.request(
            str(value["connector_id"]),
            path=str(value.get("path") or "/"),
            method=str(value.get("method") or "GET"),
            payload=value.get("payload") if isinstance(value.get("payload"), dict) else None,
            capability=str(value.get("capability") or "read"),
        )
        return WorkbenchResult(
            request.capability,
            "connector_request",
            "succeeded",
            output=result,
            provenance=f"connector:{value['connector_id']}",
        )

    def _project_inspect(self, request: WorkbenchRequest) -> WorkbenchResult:
        if not request.arguments:
            raise WorkbenchError("workbench:project:inspect requires a local path")
        report = self.project_intake.inspect_local(
            request.arguments[0],
            project_id=str(request.options.get("project_id") or "unregistered"),
        )
        return WorkbenchResult(request.capability, "project_inspect", "succeeded", output=report.__dict__, provenance=report.path)

    def _project_register(self, request: WorkbenchRequest) -> WorkbenchResult:
        value = self._github_json_arg(request)
        project = self.project_intake.register(
            project_id=str(value["project_id"]),
            name=str(value["name"]),
            objective=str(value["objective"]),
            workspace=str(value["workspace"]) if value.get("workspace") else None,
            repository=str(value["repository"]) if value.get("repository") else None,
            status=str(value.get("status") or "active"),
        )
        return WorkbenchResult(
            request.capability,
            "project_register",
            "succeeded",
            output=project.__dict__,
            provenance=str(self.project_intake.projects_path),
        )

    def _project_register_github(self, request: WorkbenchRequest) -> WorkbenchResult:
        value = self._github_json_arg(request)
        project = self.project_intake.register_github(
            project_id=str(value["project_id"]),
            name=str(value["name"]) if value.get("name") else None,
            objective=str(value["objective"]),
            owner=str(value["owner"]),
            repo=str(value["repo"]),
        )
        return WorkbenchResult(
            request.capability,
            "project_register_github",
            "succeeded",
            output=project.__dict__,
            provenance=str(self.project_intake.projects_path),
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
                "receipt_id": result.receipt_id,
                "correlation_id": result.correlation_id,
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
