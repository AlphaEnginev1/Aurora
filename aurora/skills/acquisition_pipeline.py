"""Governed skill acquisition orchestration."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3
import json

from .acquisition import SkillAcquisitionRuntime, SkillCandidate, SkillGap
from .models import SkillSpec
from .registry import SkillRegistry


class SkillAcquisitionPipelineError(RuntimeError):
    """Raised when a governed acquisition stage fails."""


@dataclass(frozen=True)
class SkillAcquisitionEvidence:
    source_ids: tuple[str, ...]
    acquired_tools: tuple[str, ...]
    validation_returncode: int
    validation_stdout: str
    validation_stderr: str
    state: str


@dataclass(frozen=True)
class PreparedSkillCandidate:
    candidate: SkillCandidate
    evidence: SkillAcquisitionEvidence


class SkillAcquisitionPipeline:
    """Durable approval boundary for newly discovered specialist skills."""

    def __init__(self, runtime: SkillAcquisitionRuntime, artifact_path: str | Path | None = None) -> None:
        self.runtime = runtime
        self.artifact_path = Path(artifact_path or runtime.path.parent / "skill-acquisition-artifacts.db")
        self.artifact_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS candidate_artifacts (
                    candidate_id TEXT PRIMARY KEY,
                    skill_json TEXT NOT NULL,
                    source_ids_json TEXT NOT NULL,
                    acquired_tools_json TEXT NOT NULL,
                    validation_returncode INTEGER NOT NULL,
                    validation_stdout TEXT NOT NULL,
                    validation_stderr TEXT NOT NULL,
                    state TEXT NOT NULL
                )"""
            )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.artifact_path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=30000")
        return db

    def prepare_candidate(
        self,
        *,
        gap: SkillGap,
        skill: SkillSpec,
        registry: SkillRegistry,
        source_ids: tuple[str, ...],
        acquired_tools: tuple[str, ...],
        validation_returncode: int,
        validation_stdout: str = "",
        validation_stderr: str = "",
    ) -> PreparedSkillCandidate:
        if not source_ids:
            raise SkillAcquisitionPipelineError("research evidence is required")
        if validation_returncode != 0:
            raise SkillAcquisitionPipelineError("sandbox validation failed")
        candidate = self.runtime.propose_candidate(gap=gap, skill=skill, registry=registry)
        candidate = self.runtime.validate_candidate(candidate, registry)
        evidence = SkillAcquisitionEvidence(
            source_ids, acquired_tools, validation_returncode,
            validation_stdout, validation_stderr, "awaiting_approval"
        )
        self._persist(candidate, evidence)
        return PreparedSkillCandidate(candidate, evidence)

    def approve(self, prepared: PreparedSkillCandidate) -> SkillCandidate:
        approved = self.runtime.approve(prepared.candidate)
        self._set_state(approved.candidate_id, "approved")
        return approved

    def promote(
        self, prepared: PreparedSkillCandidate, registry: SkillRegistry
    ) -> SkillRegistry:
        if prepared.candidate.status != "approved":
            raise SkillAcquisitionPipelineError(
                "candidate must be explicitly approved before promotion"
            )
        promoted = self.runtime.promote(prepared.candidate, registry)
        self._set_state(prepared.candidate.candidate_id, "promoted")
        return promoted

    def _persist(self, candidate: SkillCandidate, evidence: SkillAcquisitionEvidence) -> None:
        skill = candidate.skill
        if skill is None:
            raise SkillAcquisitionPipelineError("candidate has no skill definition")
        payload = json.dumps({
            "skill_id": skill.skill_id, "version": skill.version,
            "name": skill.name, "mission": skill.mission,
            "instructions": skill.instructions,
            "required_capabilities": sorted(skill.required_capabilities),
            "verification_requirements": list(skill.verification_requirements),
            "quality_checks": list(skill.quality_checks),
            "tags": list(skill.tags), "dependencies": list(skill.dependencies),
            "enabled": skill.enabled, "model_preferences": list(skill.model_preferences),
        }, sort_keys=True)
        with self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO candidate_artifacts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (candidate.candidate_id, payload, json.dumps(evidence.source_ids),
                 json.dumps(evidence.acquired_tools), evidence.validation_returncode,
                 evidence.validation_stdout, evidence.validation_stderr, evidence.state),
            )

    def _set_state(self, candidate_id: str, state: str) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE candidate_artifacts SET state=? WHERE candidate_id=?",
                (state, candidate_id),
            )


__all__ = [
    "PreparedSkillCandidate",
    "SkillAcquisitionEvidence",
    "SkillAcquisitionPipeline",
    "SkillAcquisitionPipelineError",
]
