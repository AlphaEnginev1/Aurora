"""Deterministic delegated task engine.

The Director creates structured tasks; this engine validates dependencies and
dispatches each task through the existing orchestration and capability boundaries.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import hashlib
from enum import StrEnum
from pathlib import Path
from typing import Sequence

from aurora.failure_tracker import report_failure

from aurora.research.models import EvidenceState, ResearchRequest
from aurora.research.service import ResearchService
from aurora.research.learning import ResearchLearningGate


from aurora.autonomy.capability_runtime import CapabilityExecutionError
from aurora.autonomy.models import CapabilityRequest
from .engine import OrchestrationEngine, TaskResult
from aurora.organization import (
    OrganizationConfigError,
    OrganizationRegistry,
)
from aurora.skills import SkillRegistry, SkillRegistryError


class TaskEngineError(RuntimeError):
    """Raised when a delegated task graph is invalid or cannot execute safely."""


@dataclass(frozen=True)
class ResearchTaskResult:
    """Durable result summary for a governed research task."""

    request_id: str
    source_ids: tuple[str, ...]
    claim_ids: tuple[str, ...]
    trust_state: EvidenceState = EvidenceState.INSUFFICIENT
    knowledge_artifact_ids: tuple[str, ...] = ()

    @property
    def learning_authorized(self) -> bool:
        """Only verified research backed by trusted knowledge may teach agents."""
        return (
            self.trust_state is EvidenceState.VERIFIED
            and bool(self.knowledge_artifact_ids)
        )


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class DelegatedTask:
    task_id: str
    role: str
    objective: str
    capability: str | None = None
    args: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    project_id: str | None = None


@dataclass(frozen=True)
class DelegatedTaskResult:
    task: DelegatedTask
    status: TaskStatus
    model_result: TaskResult | None = None
    capability_result: object | None = None
    error: str | None = None
    skill_evaluations: tuple[object, ...] = ()
    trusted_research_artifact_ids: tuple[str, ...] = ()
    research_trust_state: EvidenceState = EvidenceState.INSUFFICIENT
    # Bounded JSON payload rehydrated from DurableTaskStore for completed missions.
    # Typed live execution objects are intentionally not reconstructed from storage.
    durable_result: dict | None = None


def research_learning_is_authorized(result: DelegatedTaskResult) -> bool:
    """Return whether this task's result may influence durable skill learning.

    Raw research discovery/retrieval is observational, not trusted knowledge.
    Only the dedicated research result carrying verified knowledge artifacts
    may cross the research learning boundary.
    """
    task = getattr(result, "task", None)
    capability = str(getattr(task, "capability", "") or "").strip().lower()
    capability_result = getattr(result, "capability_result", None)

    if capability.startswith("workbench:research:"):
        # These Workbench operations return discovery/retrieval/extraction
        # material, not a trust-gated ResearchTaskResult.
        return False
    if capability == "research":
        return (
            isinstance(capability_result, ResearchTaskResult)
            and capability_result.learning_authorized
        )
    if isinstance(capability_result, ResearchTaskResult):
        return capability_result.learning_authorized
    trusted_artifact_ids = tuple(
        getattr(result, "trusted_research_artifact_ids", ()) or ()
    )
    research_trust_state = getattr(
        result,
        "research_trust_state",
        EvidenceState.INSUFFICIENT,
    )
    if (
        trusted_artifact_ids
        and research_trust_state is not EvidenceState.VERIFIED
    ):
        return False
    return True


@dataclass
class TaskEngine:
    """Execute delegated tasks through OrchestrationEngine."""

    orchestration: OrchestrationEngine
    workspace_root: Path
    organization: OrganizationRegistry | None = None
    skill_registry: SkillRegistry | None = None
    skill_learning: object | None = None
    model_performance: object | None = None
    research_service: ResearchService | None = None
    research_learning_gate: ResearchLearningGate | None = None
    _results: dict[str, DelegatedTaskResult] = field(default_factory=dict, init=False)

    def validate(self, tasks: Sequence[DelegatedTask]) -> tuple[DelegatedTask, ...]:
        normalized = tuple(tasks)
        if not normalized:
            raise TaskEngineError("task plan is empty")
        ids = [task.task_id for task in normalized]
        if any(not task_id or any(ch.isspace() for ch in task_id) for task_id in ids):
            raise TaskEngineError("task ids must be non-empty and contain no whitespace")
        if len(set(ids)) != len(ids):
            raise TaskEngineError("task ids must be unique")
        known = set(ids)
        for task in normalized:
            self._validate_task_fields(task)
            if self.organization is not None:
                try:
                    if task.role == "auto":
                        if self.skill_registry is not None:
                            for skill_id in task.skills:
                                self.skill_registry.resolve(skill_id)
                    else:
                        agent = self.organization.validate_task(
                            role=task.role,
                            capability=task.capability,
                        )
                        if self.skill_registry is not None:
                            self.skill_registry.for_agent(
                                role=agent.role,
                                agent_capabilities=agent.capabilities,
                                default_skill_ids=agent.skill_ids,
                                requested_skill_ids=task.skills,
                            )
                except (OrganizationConfigError, SkillRegistryError) as exc:
                    raise TaskEngineError(str(exc)) from exc
            elif task.skills or task.role == "auto":
                raise TaskEngineError(
                    "skill assignments and automatic agent selection require an organization registry"
                )
            if any(dep not in known for dep in task.depends_on):
                missing = sorted(set(task.depends_on) - known)
                raise TaskEngineError(
                    f"task {task.task_id} depends on unknown task(s): {', '.join(missing)}"
                )
            if task.task_id in task.depends_on:
                raise TaskEngineError(f"task {task.task_id} cannot depend on itself")
        self._assert_acyclic(normalized)
        return normalized

    @staticmethod
    def _validate_task_fields(task: DelegatedTask) -> None:
        if not task.task_id or any(ch.isspace() for ch in task.task_id):
            raise TaskEngineError(
                f"task {task.task_id!r} has an invalid task id"
            )
        if not task.role.strip() or not task.objective.strip():
            raise TaskEngineError(f"task {task.task_id} has an empty role or objective")
        if any("\x00" in arg for arg in task.args):
            raise TaskEngineError(f"task {task.task_id} contains a null-byte argument")

    def execute(self, tasks: Sequence[DelegatedTask]) -> tuple[DelegatedTaskResult, ...]:
        plan = self.validate(tasks)
        self._results.clear()
        remaining = {task.task_id: task for task in plan}
        ordered: list[DelegatedTaskResult] = []

        while remaining:
            progressed = False
            ready: list[tuple[str, DelegatedTask]] = []
            blocked: list[tuple[str, DelegatedTask]] = []

            for task_id, task in tuple(remaining.items()):
                if any(dep not in self._results for dep in task.depends_on):
                    continue
                dependencies = [self._results[dep] for dep in task.depends_on]
                if any(result.status is not TaskStatus.SUCCEEDED for result in dependencies):
                    blocked.append((task_id, task))
                else:
                    ready.append((task_id, task))

            for task_id, task in blocked:
                result = DelegatedTaskResult(
                    task,
                    TaskStatus.BLOCKED,
                    error="dependency did not succeed",
                )
                self._results[task_id] = result
                ordered.append(result)
                del remaining[task_id]
                progressed = True

            if ready:
                # Independent ready tasks execute concurrently; dependency barriers
                # are preserved between rounds of the DAG.
                with ThreadPoolExecutor(
                    max_workers=len(ready),
                    thread_name_prefix="aurora-task",
                ) as pool:
                    futures = {
                        task_id: pool.submit(self.execute_one, task)
                        for task_id, task in ready
                    }
                    for task_id, task in ready:
                        result = futures[task_id].result()
                        self._results[task_id] = result
                        ordered.append(result)
                        del remaining[task_id]
                progressed = True

            if not progressed:
                raise TaskEngineError("task graph made no progress; refusing deadlock")
        return tuple(ordered)

    def execute_team(self, team, tasks: Sequence[DelegatedTask], *, allow_missing_skills: bool = False) -> tuple[DelegatedTaskResult, ...]:
        """Execute a validated task plan through a formed specialist team."""
        if not team.members:
            raise TaskEngineError("cannot execute an empty agent team")
        if team.missing_skills and not allow_missing_skills:
            raise TaskEngineError("agent team is incomplete; missing skills: " + ", ".join(team.missing_skills))
        member_roles = {member.role for member in team.members}
        normalized = []
        for task in tasks:
            if task.role == "auto":
                candidates = [
                    DelegatedTask(task.task_id, role, task.objective, task.capability, task.args, task.depends_on, task.skills, task.project_id)
                    for role in member_roles
                ]
                selected = None
                for candidate in candidates:
                    try:
                        self.validate((candidate,))
                        selected = candidate
                        break
                    except Exception:
                        continue
                if selected is None:
                    raise TaskEngineError("no viable team member for task " + task.task_id)
                normalized.append(selected)
            else:
                if task.role not in member_roles:
                    raise TaskEngineError(
                        f"task {task.task_id} assigns role {task.role!r} outside formed team"
                    )
                normalized.append(task)
        return self.execute(tuple(normalized))

    def execute_one(
        self,
        task: DelegatedTask,
        *,
        execution_scope: str | None = None,
        mission_id: str | None = None,
        correlation_id: str | None = None,
    ) -> DelegatedTaskResult:
        """Execute one already-dependency-resolved task for a durable worker."""
        self._validate_task_fields(task)
        task = self._resolve_agent_task(task)
        artifact_ids, _ = self._research_args(task.args)
        trusted_artifacts: tuple[str, ...] = ()
        research_trust_state = EvidenceState.INSUFFICIENT
        if artifact_ids:
            if self.research_learning_gate is None:
                raise TaskEngineError(
                    "trusted research artifacts require a configured research learning gate"
                )
            signal = self.research_learning_gate.authorize(artifact_ids)
            trusted_artifacts = signal.artifact_ids
            research_trust_state = signal.state
        try:
            workspace = self._workspace_for_task(task, execution_scope)
            result = self._execute_task(
                task,
                workspace=workspace,
                trusted_artifact_ids=trusted_artifacts,
                execution_scope=execution_scope,
                mission_id=mission_id,
                correlation_id=correlation_id,
            )
        except (CapabilityExecutionError, RuntimeError, ValueError) as exc:
            result = DelegatedTaskResult(
                task,
                TaskStatus.FAILED,
                error=str(exc),
                trusted_research_artifact_ids=trusted_artifacts,
                research_trust_state=research_trust_state,
            )
            return self._learn_from_result(result)
        capability_result = None if isinstance(result, TaskResult) else result
        if capability_result is not None and not isinstance(capability_result, ResearchTaskResult):
            execution = getattr(capability_result, "execution", capability_result)
            reported_status = getattr(execution, "status", None)
            status_value = getattr(reported_status, "value", reported_status)
            failure_reasons: list[str] = []
            if reported_status is not None and str(status_value).strip().lower() != TaskStatus.SUCCEEDED.value:
                failure_reasons.append(
                    f"capability execution reported non-success status: {status_value}"
                )

            # Brokered CapabilityExecutionResult exposes returncode/timed_out,
            # not a Workbench-style status. A missing status must never turn a
            # non-zero exit or timed-out process into a successful task.
            if bool(getattr(execution, "timed_out", False)):
                failure_reasons.append("capability execution timed out")
            if hasattr(execution, "returncode"):
                returncode = getattr(execution, "returncode", None)
                if (
                    not isinstance(returncode, int)
                    or isinstance(returncode, bool)
                    or returncode != 0
                ):
                    failure_reasons.append(
                        f"capability execution returned non-zero or invalid returncode: {returncode!r}"
                    )
            elif reported_status is None:
                failure_reasons.append(
                    "capability execution reported neither a status nor a returncode"
                )

            if failure_reasons:
                error = getattr(execution, "error", None) or "; ".join(failure_reasons)
                failed_result = DelegatedTaskResult(
                    task,
                    TaskStatus.FAILED,
                    capability_result=capability_result,
                    error=str(error),
                    trusted_research_artifact_ids=trusted_artifacts,
                    research_trust_state=research_trust_state,
                )
                return self._learn_from_result(failed_result)
        result = DelegatedTaskResult(
            task,
            TaskStatus.SUCCEEDED,
            model_result=result if isinstance(result, TaskResult) else None,
            capability_result=capability_result,
            trusted_research_artifact_ids=trusted_artifacts,
            research_trust_state=research_trust_state,
        )
        return self._learn_from_result(result)

    def _learn_from_result(self, result: DelegatedTaskResult) -> DelegatedTaskResult:
        """Evaluate and persist specialist learning immediately after execution."""
        if not research_learning_is_authorized(result):
            return result
        if (
            self.skill_learning is None
            or self.skill_registry is None
            or self.organization is None
        ):
            return result

        if (
            isinstance(result.capability_result, ResearchTaskResult)
            and not result.capability_result.learning_authorized
        ):
            # Retrieval/extraction is observational. Until independent verification
            # produces trusted knowledge, research must not influence skill history,
            # model performance, or skill evolution.
            return result
        if result.trusted_research_artifact_ids and result.research_trust_state is not EvidenceState.VERIFIED:
            return result

        try:
            from aurora.skills.evaluation import SkillEvaluator
            from aurora.skills.learning import SkillEvolutionEngine

            agent = self.organization.resolve_agent(result.task.role)
            evaluator = SkillEvaluator(self.skill_registry)
            evaluations = evaluator.evaluate(
                task=result.task,
                result=result,
                default_skill_ids=agent.skill_ids,
                agent_capabilities=agent.capabilities,
                task_type=self._classify_task(result.task.objective, self._skill_tags(result.task)),
                context_bucket=self._context_bucket(result.task.objective),
            )
            evolution = SkillEvolutionEngine(self.skill_learning)
            for evaluation in evaluations:
                self.skill_learning.record(evaluation)
                if self.model_performance is not None:
                    response = (
                        result.model_result.response
                        if result.model_result is not None
                        else None
                    )
                    self.model_performance.record(
                        model=evaluation.model,
                        skill_id=evaluation.skill_id,
                        skill_version=evaluation.skill_version,
                        task_type=evaluation.task_type,
                        context_bucket=evaluation.context_bucket,
                        score=evaluation.score,
                        evidence_completeness=evaluation.evidence_completeness,
                        evidence_state=evaluation.evidence_state,
                        created_at=self._learning_timestamp(),
                        routing_score=response.routing_score if response else None,
                        routing_factors=response.routing_factors if response else None,
                        routing_candidates=response.routing_candidates if response else (),
                    )
                evolution.observe(
                    evaluation,
                    base_version=evaluation.skill_version,
                )
            return DelegatedTaskResult(
                task=result.task,
                status=result.status,
                model_result=result.model_result,
                capability_result=result.capability_result,
                error=result.error,
                skill_evaluations=evaluations,
                trusted_research_artifact_ids=result.trusted_research_artifact_ids,
                research_trust_state=result.research_trust_state,
                durable_result=result.durable_result,
            )
        except Exception as exc:
            # Learning must never turn a completed task into a false execution
            # failure. The durable execution result remains authoritative.
            report_failure("task_learning", exc)
            return result

    def _skill_tags(self, task: DelegatedTask) -> tuple[str, ...]:
        if self.skill_registry is None or self.organization is None:
            return ()
        agent = self.organization.resolve_agent(task.role)
        assignment = self.skill_registry.for_agent(
            role=agent.role,
            agent_capabilities=agent.capabilities,
            default_skill_ids=agent.skill_ids,
            requested_skill_ids=task.skills,
        )
        return assignment.tags

    @staticmethod
    def _context_bucket(objective: str, extra_context: str = "") -> str:
        size = len(objective) + len(extra_context)
        if size <= 120:
            return "small"
        if size <= 600:
            return "medium"
        return "large"

    @staticmethod
    def _learning_timestamp() -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()
    def _resolve_agent_task(self, task: DelegatedTask) -> DelegatedTask:
        """Resolve the Director's automatic-agent sentinel to a specialist role."""
        if task.role != "auto":
            return task
        if self.organization is None:
            raise TaskEngineError("automatic agent selection requires an organization registry")
        from aurora.organization.agent_selection import AgentSelector

        selector = AgentSelector(
            self.organization,
            self.skill_registry,
            self.skill_learning,
        )
        try:
            role = selector.select(
                objective=task.objective,
                required_skill_ids=task.skills,
                capability=task.capability,
            )
        except Exception as exc:
            raise TaskEngineError(str(exc)) from exc
        return DelegatedTask(
            task_id=task.task_id,
            role=role,
            objective=task.objective,
            capability=task.capability,
            args=task.args,
            depends_on=task.depends_on,
            skills=task.skills,
            project_id=task.project_id,
        )

    def skill_assignment_for(self, task: DelegatedTask):
        if self.skill_registry is None or self.organization is None:
            return None
        agent = self.organization.resolve_agent(task.role)
        return self.skill_registry.for_agent(
            role=agent.role,
            agent_capabilities=agent.capabilities,
            default_skill_ids=agent.skill_ids,
            requested_skill_ids=task.skills,
        )

    @staticmethod
    def _classify_task(objective: str, tags: Sequence[str]) -> str:
        text = objective.lower()
        tagset = {tag.lower() for tag in tags}
        rules = (
            ("security", {"security", "audit", "threat", "vulnerability"}),
            ("research", {"research", "literature", "paper", "evidence", "study"}),
            ("data", {"data", "dataset", "pipeline", "analysis", "statistics"}),
            ("verification", {"verify", "validation", "test", "reproduce", "check"}),
            ("operations", {"runtime", "recovery", "incident", "health", "monitor"}),
            ("discovery", {"discover", "novel", "hypothesis", "explore", "prior art"}),
            ("synthesis", {"synthesize", "summary", "decision", "brief", "integrate"}),
            ("engineering", {"code", "implement", "debug", "refactor", "software"}),
        )
        for name, keywords in rules:
            if name in tagset or any(keyword in text for keyword in keywords):
                return name
        return "general"

    @staticmethod
    def _research_args(args: Sequence[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Separate trusted-artifact references from source URLs without ambiguity."""
        artifacts: list[str] = []
        sources: list[str] = []
        for raw in args:
            value = raw.strip()
            if value.startswith("artifact:"):
                artifact_id = value.removeprefix("artifact:").strip()
                if artifact_id:
                    artifacts.append(artifact_id)
                continue
            if value.startswith("url:"):
                value = value.removeprefix("url:").strip()
            if value:
                sources.append(value)
        return tuple(dict.fromkeys(artifacts)), tuple(dict.fromkeys(sources))

    def _execute_task(
        self,
        task: DelegatedTask,
        *,
        workspace: Path | None = None,
        trusted_artifact_ids: tuple[str, ...] = (),
        execution_scope: str | None = None,
        mission_id: str | None = None,
        correlation_id: str | None = None,
    ):
        mission_identity = mission_id or (
            execution_scope.split(":", 1)[0]
            if execution_scope and ":" in execution_scope
            else execution_scope or task.task_id
        )
        skill_assignment = None
        if self.skill_registry is not None and self.organization is not None:
            agent = self.organization.resolve_agent(task.role)
            skill_assignment = self.skill_registry.for_agent(
                role=agent.role,
                agent_capabilities=agent.capabilities,
                default_skill_ids=agent.skill_ids,
                requested_skill_ids=task.skills,
            )
        skill_preferences = skill_assignment.model_preferences if skill_assignment else ()
        skill_tags = skill_assignment.tags if skill_assignment else ()
        skill_ids = skill_assignment.skill_ids if skill_assignment else ()
        skill_versions = (
            {skill.skill_id: skill.version for skill in skill_assignment.skills}
            if skill_assignment is not None
            else {}
        )
        task_type = self._classify_task(task.objective, skill_tags)
        context_bucket = self._context_bucket(task.objective)
        learned_model_scores = {}
        if self.model_performance is not None and skill_ids:
            learned_model_scores = self.model_performance.trusted_scores_for(
                skill_ids=skill_ids,
                skill_versions=skill_versions,
                task_type=task_type,
                context_bucket=context_bucket,
            )
        if self.model_performance is None and self.skill_learning is not None and skill_ids:
            fallback_scores = self.skill_learning.model_scores_for(skill_ids)
            for model, score in fallback_scores.items():
                learned_model_scores.setdefault(model, score)
        organization_preferences = (
            self.organization.model_preferences_for(task.role)
            if self.organization is not None
            else ()
        )
        model_preferences = tuple(dict.fromkeys((*skill_preferences, *organization_preferences)))
        artifact_ids, source_args = self._research_args(task.args)
        if task.capability == "research":
            if self.research_service is None:
                raise TaskEngineError("research task requires a configured research service")
            request_id = (
                f"research-{mission_identity}-{task.task_id}"
                if mission_id is not None
                else f"research-{task.task_id}"
            )
            request = ResearchRequest(
                request_id=request_id,
                objective=task.objective,
                required_sources=tuple(source_args),
                max_sources=max(1, len(source_args)) if source_args else 10,
            )
            if source_args:
                sources, claims = self.research_service.retrieve_and_extract(request)
            else:
                urls = self.research_service.discover(request)
                discovered = ResearchRequest(
                    request_id=request.request_id,
                    objective=request.objective,
                    required_sources=urls,
                    constraints=request.constraints,
                    max_sources=request.max_sources,
                )
                sources, claims = self.research_service.retrieve_and_extract(discovered)
            trust_state = EvidenceState.INSUFFICIENT
            trusted_artifacts: tuple[str, ...] = ()
            if artifact_ids:
                if self.research_learning_gate is None:
                    raise TaskEngineError("trusted research artifacts require a configured research learning gate")
                signal = self.research_learning_gate.authorize(tuple(artifact_ids))
                trust_state = signal.state
                trusted_artifacts = signal.artifact_ids
            return ResearchTaskResult(
                request_id=request.request_id,
                source_ids=tuple(source.source_id for source in sources),
                claim_ids=tuple(claim.claim_id for claim in claims),
                trust_state=trust_state,
                knowledge_artifact_ids=trusted_artifacts,
            )

        if task.capability:
            execution_workspace = workspace or self.workspace_root
            constraints = {
                "mission_id": mission_identity,
                "work_id": mission_identity,
                    # Stable across the worker's attempt number; a retry must
                    # resume the same Workbench action rather than repeat an
                    # external side effect with a new identity.
                    "idempotency_key": f"task:{mission_identity}:{task.task_id}",
                }
            if task.project_id:
                constraints["project_id"] = task.project_id
            if correlation_id:
                constraints["correlation_id"] = correlation_id
            request = CapabilityRequest(
                name=task.capability,
                actor=task.role,
                reason=task.objective,
                constraints=constraints,
            )
            return self.orchestration.run_capability_task(
                task_id=task.task_id,
                role=task.role,
                request=request,
                workspace=execution_workspace,
                args=task.args,
            )
        skill_context = (
            skill_assignment.render_system_context()
            if skill_assignment is not None
            else ""
        )
        if trusted_artifact_ids:
            if self.research_learning_gate is None:
                raise TaskEngineError("trusted research artifacts require a configured research learning gate")
            research_context = self.research_learning_gate.context_for(trusted_artifact_ids)
            research_block = "\n".join(
                f"- artifact_id={artifact_id}: {claim}"
                for artifact_id, claim in research_context
            )
            skill_context = (
                skill_context + "\n\n"
                if skill_context else ""
            ) + (
                "Verified research knowledge authorized for this task. Treat these claims "
                "as governed evidence and do not extend them beyond their stated content:\n"
                + research_block
            )
        required_capabilities = (
            tuple(sorted({
                capability
                for skill in skill_assignment.skills
                for capability in skill.required_capabilities
            }))
            if skill_assignment is not None
            else ()
        )
        prepare_capabilities = getattr(
            self.orchestration, "prepare_capabilities", None
        )
        artifacts = (
            prepare_capabilities(
                names=required_capabilities,
                actor=task.role,
                reason=task.objective,
            )
            if prepare_capabilities is not None
            else ()
        )
        if artifacts:
            manifest = "\n".join(
                f"- {item.name}: version={item.version or 'unknown'}, "
                f"source={item.source or 'unknown'}, path={item.path or 'unknown'}, "
                f"artifact_id={item.artifact_id or 'unknown'}, verification={item.verification}"
                for item in artifacts
            )
            skill_context = (
                skill_context + "\n\n"
                if skill_context else ""
            ) + (
                "Governed capability artifacts prepared for this task. "
                "Use only through Aurora's capability boundary; do not bypass it.\n"
                + manifest
            )
        return self.orchestration.run_task(
            task_id=task.task_id,
            role=task.role,
            objective=task.objective,
            mission_id=mission_identity,
            model_preferences=model_preferences,
            skill_ids=skill_ids,
            skill_tags=skill_tags,
            task_type=task_type,
            learned_model_scores=learned_model_scores,
            context=(
                ([{"role": "system", "content": skill_context}] if skill_context else [])
                + ([{"role": "system", "content": f"Authoritative Aurora project_id: {task.project_id}. Do not use another project's context."}] if task.project_id else [])
                or None
            ),
            project_id=task.project_id,
            correlation_id=correlation_id,
        )

    def _workspace_for_task(self, task: DelegatedTask, execution_scope: str | None) -> Path:
        """Return an isolated deterministic workspace for one execution attempt."""
        if execution_scope is None:
            return self.workspace_root
        if not execution_scope.strip():
            raise TaskEngineError("execution_scope cannot be empty")
        digest = hashlib.sha256(execution_scope.encode("utf-8")).hexdigest()
        workspace = self.workspace_root / "tasks" / digest
        workspace.mkdir(parents=True, exist_ok=True)
        return workspace

    @staticmethod
    def _assert_acyclic(tasks: Sequence[DelegatedTask]) -> None:
        graph = {task.task_id: set(task.depends_on) for task in tasks}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(task_id: str) -> None:
            if task_id in visiting:
                raise TaskEngineError("task graph contains a dependency cycle")
            if task_id in visited:
                return
            visiting.add(task_id)
            for dep in graph[task_id]:
                visit(dep)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in graph:
            visit(task_id)


__all__ = [
    "DelegatedTask",
    "DelegatedTaskResult",
    "TaskEngine",
    "TaskEngineError",
    "TaskStatus",
]