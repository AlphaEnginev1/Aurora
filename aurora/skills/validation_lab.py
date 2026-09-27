"""Validation lab for side-by-side specialist skill candidate testing."""
from __future__ import annotations

from dataclasses import dataclass
import re

from aurora.inference.models import InferenceRequest
from aurora.inference.router import ModelRouter, ModelRoutingError
from .models import SkillSpec

_MARKER = re.compile(r"^(VERIFY|QUALITY):([^=\\s]+)=(pass|fail)$", re.IGNORECASE)


class SkillValidationError(RuntimeError):
    """Raised when a candidate cannot be validated safely."""


@dataclass(frozen=True)
class SkillValidationCase:
    case_id: str
    objective: str
    expected_evidence: tuple[str, ...]


@dataclass(frozen=True)
class SkillValidationResult:
    case_id: str
    score: float
    model: str
    evidence_complete: bool
    passed: tuple[str, ...]
    failed: tuple[str, ...]
    missing: tuple[str, ...]


@dataclass(frozen=True)
class SkillValidationReport:
    baseline_score: float
    candidate_score: float
    improvement: float
    cases: tuple[SkillValidationResult, ...]
    model: str
    state: str


class SkillValidationLab:
    """Run deterministic, evidence-gated A/B validation before approval.

    The lab never mutates the registry. It asks the routed model to execute
    representative procedural cases and scores only observable evidence markers.
    """

    def __init__(self, router: ModelRouter, *, minimum_improvement: float = 0.03) -> None:
        if minimum_improvement < 0:
            raise ValueError("minimum_improvement must be non-negative")
        self.router = router
        self.minimum_improvement = minimum_improvement

    def compare(
        self,
        *,
        baseline: SkillSpec,
        candidate: SkillSpec,
        cases: tuple[SkillValidationCase, ...],
    ) -> SkillValidationReport:
        if baseline.skill_id != candidate.skill_id:
            raise SkillValidationError("baseline and candidate skill ids differ")
        if not cases:
            raise SkillValidationError("at least one validation case is required")
        baseline_results = tuple(self._run(baseline, case) for case in cases)
        candidate_results = tuple(self._run(candidate, case) for case in cases)
        baseline_score = sum(item.score for item in baseline_results) / len(baseline_results)
        candidate_score = sum(item.score for item in candidate_results) / len(candidate_results)
        improvement = candidate_score - baseline_score
        state = "improved" if improvement >= self.minimum_improvement else "not_improved"
        if state != "improved":
            raise SkillValidationError(
                f"candidate did not demonstrate required improvement: "
                f"baseline={baseline_score:.3f}, candidate={candidate_score:.3f}, "
                f"delta={improvement:.3f}, required={self.minimum_improvement:.3f}"
            )
        return SkillValidationReport(
            baseline_score=baseline_score,
            candidate_score=candidate_score,
            improvement=improvement,
            cases=candidate_results,
            model=candidate_results[0].model,
            state=state,
        )

    def _run(self, skill: SkillSpec, case: SkillValidationCase) -> SkillValidationResult:
        prompt = (
            "Execute this governed specialist procedure against the validation case.\n"
            "Do not invent evidence. Emit exactly one machine-readable line for every "
            "required check.\n\n"
            f"Skill {skill.skill_id} v{skill.version}\n"
            f"Procedure:\n{skill.instructions}\n"
            f"Case objective: {case.objective}\n"
            f"Expected evidence: {', '.join(case.expected_evidence)}\n"
            f"Verification checks: {', '.join(skill.verification_requirements)}\n"
            f"Quality checks: {', '.join(skill.quality_checks)}"
        )
        request = InferenceRequest(
            messages=[
                {"role": "system", "content": "You are Aurora's controlled skill validation executor."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=1400,
            temperature=0.0,
            metadata={
                "task_type": "skill_validation",
                "skill_tags": skill.tags,
                "context_tokens": max(256, len(prompt) // 4),
            },
        )
        try:
            response = self.router.generate(request, preferred_models=skill.model_preferences)
        except ModelRoutingError as exc:
            raise SkillValidationError(str(exc)) from exc

        markers: dict[str, str] = {}
        for line in response.content.splitlines():
            match = _MARKER.match(line.strip())
            if match:
                markers[f"{match.group(1).upper()}:{match.group(2)}"] = match.group(3).lower()

        expected = tuple(
            [f"VERIFY:{name}" for name in skill.verification_requirements]
            + [f"QUALITY:{name}" for name in skill.quality_checks]
        )
        passed = tuple(name for name in expected if markers.get(name) == "pass")
        failed = tuple(name for name in expected if markers.get(name) == "fail")
        missing = tuple(name for name in expected if name not in markers)
        total = len(expected)
        score = 1.0 if total == 0 else max(
            0.0,
            min(1.0, len(passed) / total - 0.25 * len(failed) / total - 0.10 * len(missing) / total),
        )
        return SkillValidationResult(
            case_id=case.case_id,
            score=score,
            model=response.model,
            evidence_complete=not failed and not missing,
            passed=passed,
            failed=failed,
            missing=missing,
        )
