"""Governed runtime for controlled specialist-skill evolution."""
from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3
from pathlib import Path
from uuid import uuid4

from .learning import SkillEvolutionProposal, SkillLearningStore
from .models import SkillSpec
from .registry import SkillRegistry, SkillRegistryError
from .validation_lab import SkillValidationCase, SkillValidationLab, SkillValidationReport


class SkillEvolutionRuntimeError(RuntimeError):
    """Raised when an evolution candidate cannot safely transition."""


@dataclass(frozen=True)
class PreparedSkillEvolution:
    candidate_id: str
    proposal: SkillEvolutionProposal
    skill: SkillSpec
    status: str = "validated"
    validation: SkillValidationReport | None = None


class SkillEvolutionRuntime:
    """Construct, validate, approve and promote versioned skill evolutions.

    Evolution is deliberately conservative: the active registry is never
    mutated while preparing or approving a candidate. Promotion re-checks the
    active base version to prevent stale candidates from overwriting newer work.
    """

    def __init__(self, learning: SkillLearningStore, path: str | Path, validation_lab: SkillValidationLab | None = None) -> None:
        self.learning = learning
        self.validation_lab = validation_lab
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=30000")
        return db

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS skill_evolution_candidates (
                    candidate_id TEXT PRIMARY KEY,
                    proposal_id TEXT NOT NULL UNIQUE,
                    skill_id TEXT NOT NULL,
                    base_version TEXT NOT NULL,
                    candidate_version TEXT NOT NULL,
                    skill_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )"""
            )

    def prepare(
        self,
        *,
        proposal: SkillEvolutionProposal,
        registry: SkillRegistry,
        improved_instructions: str | None = None,
    ) -> PreparedSkillEvolution:
        if proposal.status != "pending":
            raise SkillEvolutionRuntimeError("proposal must be pending")
        active = registry.skills.get(proposal.skill_id)
        if active is None:
            raise SkillEvolutionRuntimeError(
                f"active skill is unavailable: {proposal.skill_id}"
            )
        if active.version != proposal.base_version:
            raise SkillEvolutionRuntimeError(
                "active skill version changed since proposal creation"
            )
        instructions = (improved_instructions or "").strip()
        if not instructions:
            instructions = (
                active.instructions.rstrip()
                + "\n\nEvolution guidance (derived from observed evidence):\n"
                + proposal.suggested_change.strip()
            )
        skill = SkillSpec(
            skill_id=active.skill_id,
            version=proposal.candidate_version,
            name=active.name,
            mission=active.mission,
            instructions=instructions,
            required_capabilities=active.required_capabilities,
            verification_requirements=active.verification_requirements,
            quality_checks=active.quality_checks,
            tags=active.tags,
            dependencies=active.dependencies,
            enabled=active.enabled,
            model_preferences=active.model_preferences,
        )
        try:
            trial = SkillRegistry({**registry.skills, skill.skill_id: skill})
            trial.resolve(skill.skill_id)
        except (SkillRegistryError, ValueError) as exc:
            raise SkillEvolutionRuntimeError(f"candidate validation failed: {exc}") from exc

        validation = None
        if self.validation_lab is not None:
            evidence = self.learning.evidence_for(active.skill_id, skill_version=active.version, limit=8)
            cases = tuple(
                SkillValidationCase(
                    case_id=f"evidence-{item.task_id}-{index}",
                    objective=(
                        "Reproduce the specialist outcome represented by the historical task. "
                        + (f"Observed failure: {item.failure_signature}" if item.failure_signature else
                           "Preserve the successful behavior while maintaining all evidence checks.")
                    ),
                    expected_evidence=tuple(active.verification_requirements) + tuple(active.quality_checks),
                )
                for index, item in enumerate(evidence)
            )
            if not cases:
                raise SkillEvolutionRuntimeError("skill validation requires durable historical evidence")
            try:
                validation = self.validation_lab.compare(
                    baseline=active,
                    candidate=skill,
                    cases=cases,
                )
            except Exception as exc:
                raise SkillEvolutionRuntimeError(f"skill validation lab rejected candidate: {exc}") from exc

        candidate_id = f"skill-evolution-{uuid4().hex}"
        with self._connect() as db:
            existing = db.execute(
                "SELECT candidate_id, status FROM skill_evolution_candidates WHERE proposal_id=?",
                (proposal.proposal_id,),
            ).fetchone()
            if existing is not None:
                raise SkillEvolutionRuntimeError(
                    f"evolution candidate already exists: {existing['candidate_id']}"
                )
            db.execute(
                """INSERT INTO skill_evolution_candidates
                   (candidate_id, proposal_id, skill_id, base_version,
                    candidate_version, skill_json, status)
                   VALUES (?, ?, ?, ?, ?, ?, 'validated')""",
                (
                    candidate_id,
                    proposal.proposal_id,
                    skill.skill_id,
                    proposal.base_version,
                    skill.version,
                    json.dumps({
                        "skill_id": skill.skill_id,
                        "version": skill.version,
                        "name": skill.name,
                        "mission": skill.mission,
                        "instructions": skill.instructions,
                        "required_capabilities": sorted(skill.required_capabilities),
                        "verification_requirements": list(skill.verification_requirements),
                        "quality_checks": list(skill.quality_checks),
                        "tags": list(skill.tags),
                        "dependencies": list(skill.dependencies),
                        "enabled": skill.enabled,
                        "model_preferences": list(skill.model_preferences),
                    }, sort_keys=True),
                ),
            )
        return PreparedSkillEvolution(candidate_id, proposal, skill, "validated", validation)

    def approve(self, prepared: PreparedSkillEvolution) -> PreparedSkillEvolution:
        if prepared.status != "validated":
            raise SkillEvolutionRuntimeError("candidate must be validated before approval")
        with self._connect() as db:
            changed = db.execute(
                "UPDATE skill_evolution_candidates SET status='approved' "
                "WHERE candidate_id=? AND status='validated'",
                (prepared.candidate_id,),
            ).rowcount
        if not changed:
            raise SkillEvolutionRuntimeError("candidate is no longer awaiting approval")
        return PreparedSkillEvolution(
            prepared.candidate_id, prepared.proposal, prepared.skill, "approved", prepared.validation
        )

    def promote(
        self,
        prepared: PreparedSkillEvolution,
        registry: SkillRegistry,
    ) -> SkillRegistry:
        if prepared.status != "approved":
            raise SkillEvolutionRuntimeError(
                "candidate must be explicitly approved before promotion"
            )
        active = registry.skills.get(prepared.skill.skill_id)
        if active is None or active.version != prepared.proposal.base_version:
            raise SkillEvolutionRuntimeError(
                "active skill changed since evolution candidate creation"
            )
        promoted = SkillRegistry({**registry.skills, prepared.skill.skill_id: prepared.skill})
        with self._connect() as db:
            changed = db.execute(
                "UPDATE skill_evolution_candidates SET status='promoted' "
                "WHERE candidate_id=? AND status='approved'",
                (prepared.candidate_id,),
            ).rowcount
        if not changed:
            raise SkillEvolutionRuntimeError("candidate promotion transition failed")
        self.learning.resolve_proposal(
            prepared.proposal.proposal_id, decision="approved"
        )
        return promoted
