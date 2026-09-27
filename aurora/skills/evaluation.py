"""Deterministic evaluation protocol for specialist skills."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

from .registry import SkillRegistry


_MARKER = re.compile(r"^(VERIFY|QUALITY):([^=\s]+)=(pass|fail)$", re.IGNORECASE)


@dataclass(frozen=True)
class SkillEvaluation:
    skill_id: str
    skill_version: str
    mission_id: str | None
    task_id: str
    role: str
    score: float
    status: str
    model: str | None
    verification_passed: tuple[str, ...]
    verification_failed: tuple[str, ...]
    verification_missing: tuple[str, ...]
    quality_passed: tuple[str, ...]
    quality_failed: tuple[str, ...]
    quality_missing: tuple[str, ...]
    failure_signature: str | None
    evidence_completeness: float
    evidence_state: str


class SkillEvaluator:
    """Evaluate observable skill execution without pretending missing checks passed."""

    def __init__(self, registry: SkillRegistry) -> None:
        self.registry = registry

    def evaluate(
        self,
        *,
        task: DelegatedTask,
        result: DelegatedTaskResult,
        mission_id: str | None = None,
        default_skill_ids: tuple[str, ...] = (),
        agent_capabilities=frozenset(),
    ) -> tuple[SkillEvaluation, ...]:
        assignment = self.registry.for_agent(
            role=task.role,
            agent_capabilities=agent_capabilities,
            default_skill_ids=default_skill_ids,
            requested_skill_ids=task.skills,
        )
        content = ""
        model = None
        if result.model_result is not None:
            content = result.model_result.response.content
            model = result.model_result.response.model
        elif result.capability_result is not None:
            execution = getattr(result.capability_result, "execution", None)
            if execution is not None:
                stdout = str(getattr(execution, "stdout", ""))
                stderr = str(getattr(execution, "stderr", ""))
                content = "\n".join(value for value in (stdout, stderr) if value)

        markers: dict[str, str] = {}
        for line in content.splitlines():
            match = _MARKER.match(line.strip())
            if match:
                markers[f"{match.group(1).upper()}:{match.group(2)}"] = match.group(3).lower()

        evaluations: list[SkillEvaluation] = []
        for skill in assignment.skills:
            verification = self._partition(
                skill.verification_requirements, "VERIFY", markers
            )
            quality = self._partition(skill.quality_checks, "QUALITY", markers)
            score = self._score(
                result=result,
                has_content=bool(content.strip()),
                verification=verification,
                quality=quality,
            )
            failure_signature = (
                result.error.strip()[:240]
                if result.status.value != "succeeded" and result.error
                else None
            )
            evaluations.append(
                SkillEvaluation(
                    skill_id=skill.skill_id,
                    skill_version=skill.version,
                    mission_id=mission_id,
                    task_id=task.task_id,
                    role=task.role,
                    score=score,
                    status=result.status.value,
                    model=model,
                    verification_passed=verification[0],
                    verification_failed=verification[1],
                    verification_missing=verification[2],
                    quality_passed=quality[0],
                    quality_failed=quality[1],
                    quality_missing=quality[2],
                    failure_signature=failure_signature,
                    evidence_completeness=self._evidence_completeness(verification, quality),
                    evidence_state=self._evidence_state(result, verification, quality),
                )
            )
        return tuple(evaluations)

    @staticmethod
    def _partition(
        checks: Iterable[str],
        prefix: str,
        markers: dict[str, str],
    ) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
        passed: list[str] = []
        failed: list[str] = []
        missing: list[str] = []
        for check in checks:
            state = markers.get(f"{prefix}:{check}")
            if state == "pass":
                passed.append(check)
            elif state == "fail":
                failed.append(check)
            else:
                missing.append(check)
        return tuple(passed), tuple(failed), tuple(missing)

    @staticmethod
    def _evidence_completeness(
        verification: tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]],
        quality: tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]],
    ) -> float:
        total = sum(len(part) for part in (*verification, *quality))
        if total == 0:
            return 1.0
        observed = (
            len(verification[0]) + len(verification[1])
            + len(quality[0]) + len(quality[1])
        )
        return observed / total

    @staticmethod
    def _evidence_state(
        result: DelegatedTaskResult,
        verification: tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]],
        quality: tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]],
    ) -> str:
        if result.status.value != "succeeded":
            return "execution_failed"
        failed = len(verification[1]) + len(quality[1])
        missing = len(verification[2]) + len(quality[2])
        if failed:
            return "evidence_failed"
        if missing:
            return "insufficient_evidence"
        return "evidence_complete"

    @staticmethod
    def _score(
        *,
        result: DelegatedTaskResult,
        has_content: bool,
        verification: tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]],
        quality: tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]],
    ) -> float:
        if result.status.value != "succeeded":
            return 0.0
        total_checks = sum(len(part) for part in (*verification, *quality))
        if total_checks == 0:
            return 1.0 if has_content or result.capability_result is not None else 0.5
        passed = len(verification[0]) + len(quality[0])
        failed = len(verification[1]) + len(quality[1])
        evidence_ratio = passed / total_checks
        missing = total_checks - passed - failed
        penalty = 0.25 * (failed / total_checks) + 0.10 * (missing / total_checks)
        execution_floor = 0.45 if has_content or result.capability_result is not None else 0.25
        return max(0.0, min(1.0, execution_floor + 0.55 * evidence_ratio - penalty))
