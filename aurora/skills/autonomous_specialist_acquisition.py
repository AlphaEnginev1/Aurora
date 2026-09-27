"""Autonomous specialist acquisition service.

This service executes the governed acquisition stages after the Director has
identified a durable skill gap. It never promotes a skill implicitly.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from aurora.autonomy.acquisition import AcquisitionManager, AcquisitionResult
from aurora.autonomy.registry import ToolSpec
from aurora.autonomy.sandbox import SandboxExecutor
from aurora.research.models import ResearchRequest
from aurora.research.service import ResearchService

from .acquisition import SkillAcquisitionRuntime, SkillGap
from .acquisition_pipeline import PreparedSkillCandidate, SkillAcquisitionPipeline
from .models import SkillSpec
from .registry import SkillRegistry


class AutonomousSpecialistAcquisitionError(RuntimeError):
    """Raised when specialist acquisition cannot complete safely."""


@dataclass(frozen=True)
class SpecialistAcquisitionPlan:
    skill: SkillSpec
    research_urls: tuple[str, ...]
    tools: tuple[ToolSpec, ...]
    validation_command: tuple[str, ...]


class AutonomousSpecialistAcquisition:
    """Turn a missing skill into an evidence-backed approval candidate."""

    def __init__(
        self,
        *,
        research: ResearchService,
        acquisition: AcquisitionManager,
        pipeline: SkillAcquisitionPipeline,
        sandbox: SandboxExecutor,
    ) -> None:
        self.research = research
        self.acquisition = acquisition
        self.pipeline = pipeline
        self.sandbox = sandbox

    def prepare(
        self,
        *,
        gap: SkillGap,
        plan: SpecialistAcquisitionPlan,
        registry: SkillRegistry,
    ) -> PreparedSkillCandidate:
        if gap.skill_id != plan.skill.skill_id:
            raise AutonomousSpecialistAcquisitionError(
                "acquisition plan skill does not match the durable gap"
            )
        urls = tuple(plan.research_urls)
        if not urls:
            raise AutonomousSpecialistAcquisitionError(
                "autonomous acquisition requires at least one research source"
            )
        try:
            sources = self.research.retrieve(
                ResearchRequest(
                    request_id=f"acq-{gap.mission_id}-{gap.skill_id}",
                    objective=f"Research procedure for specialist skill {gap.skill_id}",
                    required_sources=urls,
                    constraints=gap.required_capabilities,
                    max_sources=len(urls),
                )
            )
        except Exception as exc:
            raise AutonomousSpecialistAcquisitionError(
                f"governed research failed: {exc}"
            ) from exc
        if not sources:
            raise AutonomousSpecialistAcquisitionError("research returned no sources")

        acquired: list[AcquisitionResult] = []
        for tool in plan.tools:
            try:
                acquired.append(self.acquisition.acquire(tool))
            except Exception as exc:
                raise AutonomousSpecialistAcquisitionError(
                    f"tool acquisition failed: {tool.name}"
                ) from exc

        if not plan.validation_command:
            raise AutonomousSpecialistAcquisitionError(
                "sandbox validation command is required"
            )
        # Validate the specialist procedure in a fresh isolated workspace.
        import tempfile
        with tempfile.TemporaryDirectory(prefix="aurora-specialist-") as workspace:
            result = self.sandbox.execute(
                plan.validation_command,
                workspace=workspace,
            )
        if result.returncode != 0 or result.timed_out:
            raise AutonomousSpecialistAcquisitionError(
                "specialist sandbox validation failed"
            )

        return self.pipeline.prepare_candidate(
            gap=gap,
            skill=plan.skill,
            registry=registry,
            source_ids=tuple(source.source_id for source in sources),
            acquired_tools=tuple(item.tool for item in acquired),
            validation_returncode=result.returncode,
            validation_stdout=result.stdout,
            validation_stderr=result.stderr,
        )


__all__ = [
    "AutonomousSpecialistAcquisition",
    "AutonomousSpecialistAcquisitionError",
    "SpecialistAcquisitionPlan",
]
