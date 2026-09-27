"""Autonomous specialist discovery, construction, acquisition and governance."""
from __future__ import annotations
from dataclasses import dataclass
from aurora.autonomy.acquisition import AcquisitionManager, AcquisitionResult
from aurora.autonomy.registry import ToolSpec
from aurora.autonomy.sandbox import SandboxExecutor
from aurora.research.models import ResearchRequest, SourceRecord
from aurora.research.service import ResearchService
from .acquisition import SkillAcquisitionRuntime, SkillGap
from .acquisition_pipeline import PreparedSkillCandidate, SkillAcquisitionPipeline
from .discovery import SkillConstructionEvaluation, SkillDiscoveryService
from .models import SkillSpec
from .registry import SkillRegistry

class AutonomousSpecialistAcquisitionError(RuntimeError):
    """Raised when specialist acquisition cannot complete safely."""

@dataclass(frozen=True)
class SpecialistAcquisitionPlan:
    """Legacy explicit plan retained only for compatibility with older callers."""
    skill: SkillSpec
    research_urls: tuple[str, ...]
    tools: tuple[ToolSpec, ...]
    validation_command: tuple[str, ...]

class AutonomousSpecialistAcquisition:
    """Turn a durable skill gap into an evidence-backed approval candidate."""

    def __init__(self, *, research: ResearchService, acquisition: AcquisitionManager,
                 pipeline: SkillAcquisitionPipeline, sandbox: SandboxExecutor,
                 discovery: SkillDiscoveryService | None = None) -> None:
        self.research=research; self.acquisition=acquisition; self.pipeline=pipeline
        self.sandbox=sandbox; self.discovery=discovery

    def discover_and_prepare(self, *, gap: SkillGap, registry: SkillRegistry) -> PreparedSkillCandidate:
        if self.discovery is None:
            raise AutonomousSpecialistAcquisitionError("autonomous skill discovery is not configured")
        try:
            result=self.discovery.discover(gap=gap, registry=registry)
        except Exception as exc:
            raise AutonomousSpecialistAcquisitionError(f"skill discovery/construction failed: {exc}") from exc
        return self._prepare(
            gap=gap, skill=result.skill, sources=result.sources, tools=result.tools,
            validation_command=result.validation_command, registry=registry, construction_evaluation=result.evaluation,
        )

    def prepare(self, *, gap: SkillGap, plan: SpecialistAcquisitionPlan,
                registry: SkillRegistry) -> PreparedSkillCandidate:
        if gap.skill_id != plan.skill.skill_id:
            raise AutonomousSpecialistAcquisitionError("acquisition plan skill does not match the durable gap")
        try:
            sources=self.research.retrieve(ResearchRequest(
                request_id=f"acq-{gap.mission_id}-{gap.skill_id}",
                objective=f"Research procedure for specialist skill {gap.skill_id}",
                required_sources=plan.research_urls, constraints=gap.required_capabilities,
                max_sources=len(plan.research_urls),
            ))
        except Exception as exc:
            raise AutonomousSpecialistAcquisitionError(f"governed research failed: {exc}") from exc
        return self._prepare(
            gap=gap, skill=plan.skill, sources=sources, tools=plan.tools,
            validation_command=plan.validation_command, registry=registry,
        )

    def _prepare(self, *, gap: SkillGap, skill: SkillSpec, sources: tuple[SourceRecord, ...],
                 tools: tuple[ToolSpec, ...], validation_command: tuple[str, ...],
                 registry: SkillRegistry,
                 construction_evaluation: SkillConstructionEvaluation | None = None) -> PreparedSkillCandidate:
        if not sources:
            raise AutonomousSpecialistAcquisitionError("research returned no sources")
        acquired: list[AcquisitionResult]=[]
        for tool in tools:
            try: acquired.append(self.acquisition.acquire(tool))
            except Exception as exc:
                raise AutonomousSpecialistAcquisitionError(f"tool acquisition failed: {tool.name}") from exc
        if not validation_command:
            raise AutonomousSpecialistAcquisitionError("sandbox validation command is required")
        import tempfile
        with tempfile.TemporaryDirectory(prefix="aurora-specialist-") as workspace:
            result=self.sandbox.execute(validation_command, workspace=workspace)
        if result.returncode != 0 or result.timed_out:
            raise AutonomousSpecialistAcquisitionError("specialist sandbox validation failed")
        return self.pipeline.prepare_candidate(
            gap=gap, skill=skill, registry=registry,
            source_ids=tuple(source.source_id for source in sources),
            acquired_tools=tuple(item.tool for item in acquired),
            validation_returncode=result.returncode,
            validation_stdout=result.stdout, validation_stderr=result.stderr,
            construction_score=construction_evaluation.score if construction_evaluation else 0.0,
            construction_evidence_state=construction_evaluation.evidence_state if construction_evaluation else "unknown",
        )

__all__=["AutonomousSpecialistAcquisition","AutonomousSpecialistAcquisitionError","SpecialistAcquisitionPlan"]
