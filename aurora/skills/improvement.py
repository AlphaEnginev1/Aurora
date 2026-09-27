"""Evidence-driven generation of specialist skill improvements."""
from __future__ import annotations

from dataclasses import dataclass

from aurora.inference.models import InferenceRequest
from aurora.inference.router import ModelRouter, ModelRoutingError
from .learning import SkillEvolutionProposal, SkillLearningStore
from .models import SkillSpec


class SkillImprovementGenerationError(RuntimeError):
    """Raised when an evidence-driven improvement cannot be generated safely."""


@dataclass(frozen=True)
class SkillImprovement:
    instructions: str
    model: str
    evidence_summary: str


class SkillImprovementGenerator:
    """Use the dynamic model router to turn durable evidence into a candidate procedure.

    Generation never mutates a SkillSpec. The returned procedure remains a candidate
    until the existing validation, explicit approval and promotion gates accept it.
    """

    def __init__(self, router: ModelRouter, learning: SkillLearningStore) -> None:
        self.router = router
        self.learning = learning

    def generate(
        self,
        *,
        active: SkillSpec,
        proposal: SkillEvolutionProposal,
    ) -> SkillImprovement:
        performance = self.learning.performance(active.skill_id)
        if performance.evaluations < 1:
            raise SkillImprovementGenerationError("no durable evidence is available")

        prompt = (
            "You are Aurora's governed skill-improvement specialist.\n"
            "Improve the operating procedure below using only the supplied evidence.\n"
            "Do not remove required verification or quality checks.\n"
            "Do not claim capabilities that are not listed.\n"
            "Return exactly two sections: RATIONALE: and INSTRUCTIONS:.\n"
            "INSTRUCTIONS must contain only the replacement operating procedure.\n\n"
            f"Skill: {active.skill_id} v{active.version}\n"
            f"Mission: {active.mission}\n"
            f"Current procedure:\n{active.instructions}\n\n"
            f"Observed evidence: {proposal.reason}\n"
            f"Suggested change: {proposal.suggested_change}\n"
            f"Average score: {performance.average_score:.3f}\n"
            f"Failures: {performance.failure_count}\n"
            f"Evaluations: {performance.evaluations}\n"
            f"Required verification: {', '.join(active.verification_requirements)}\n"
            f"Required quality checks: {', '.join(active.quality_checks)}"
        )
        request = InferenceRequest(
            messages=[
                {"role": "system", "content": "Produce conservative, testable specialist procedures."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=1800,
            temperature=0.2,
            metadata={
                "task_type": "skill_improvement",
                "skill_tags": active.tags,
                "context_tokens": max(256, len(prompt) // 4),
            },
        )
        try:
            response = self.router.generate(
                request,
                preferred_models=active.model_preferences,
            )
        except ModelRoutingError as exc:
            raise SkillImprovementGenerationError(str(exc)) from exc

        marker = "INSTRUCTIONS:"
        if marker not in response.content:
            raise SkillImprovementGenerationError("model output omitted INSTRUCTIONS section")
        instructions = response.content.split(marker, 1)[1].strip()
        if not instructions or len(instructions) > 16000:
            raise SkillImprovementGenerationError("generated instructions are empty or oversized")
        if "VERIFY:" in instructions or "QUALITY:" in instructions:
            raise SkillImprovementGenerationError("generated procedure contains execution evidence markers")
        return SkillImprovement(
            instructions=instructions,
            model=response.model,
            evidence_summary=proposal.reason,
        )
