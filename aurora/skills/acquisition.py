"""Governed runtime acquisition and promotion of missing specialist skills."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import sqlite3
from pathlib import Path
from uuid import uuid4

from .models import SkillSpec
from .registry import SkillRegistry, SkillRegistryError


class SkillAcquisitionError(RuntimeError):
    """Raised when a skill cannot be safely acquired or promoted."""


@dataclass(frozen=True)
class SkillGap:
    skill_id: str
    mission_id: str
    reason: str
    required_capabilities: tuple[str, ...]
    status: str = "pending"


@dataclass(frozen=True)
class SkillCandidate:
    candidate_id: str
    skill_id: str
    base_version: str | None
    candidate_version: str
    mission_id: str
    reason: str
    status: str
    skill: SkillSpec | None = None


class SkillAcquisitionRuntime:
    """Durable gate between a discovered skill gap and trusted registry promotion."""

    def __init__(self, path: str | Path) -> None:
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
                """CREATE TABLE IF NOT EXISTS skill_gaps (
                    gap_id TEXT PRIMARY KEY, skill_id TEXT NOT NULL, mission_id TEXT NOT NULL,
                    reason TEXT NOT NULL, required_capabilities_json TEXT NOT NULL,
                    status TEXT NOT NULL, created_at TEXT NOT NULL)"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS skill_candidates (
                    candidate_id TEXT PRIMARY KEY, skill_id TEXT NOT NULL, base_version TEXT,
                    candidate_version TEXT NOT NULL, mission_id TEXT NOT NULL, reason TEXT NOT NULL,
                    status TEXT NOT NULL, created_at TEXT NOT NULL)"""
            )

    def detect_gap(self, *, mission_id: str, skill_id: str, reason: str,
                   required_capabilities: tuple[str, ...] = ()) -> SkillGap:
        if not mission_id.strip() or not skill_id.strip():
            raise SkillAcquisitionError("mission_id and skill_id are required")
        gap = SkillGap(skill_id, mission_id, reason.strip() or "skill is unavailable",
                       tuple(dict.fromkeys(required_capabilities)))
        with self._connect() as db:
            existing = db.execute(
                "SELECT gap_id FROM skill_gaps WHERE mission_id=? AND skill_id=? AND status='pending'",
                (mission_id, skill_id)).fetchone()
            if existing is None:
                db.execute("INSERT INTO skill_gaps VALUES (?, ?, ?, ?, ?, 'pending', ?)",
                           (f"gap-{uuid4().hex}", gap.skill_id, gap.mission_id, gap.reason,
                            json.dumps(gap.required_capabilities), self._now()))
        return gap

    def propose_candidate(self, *, gap: SkillGap, skill: SkillSpec,
                          registry: SkillRegistry) -> SkillCandidate:
        if skill.skill_id != gap.skill_id:
            raise SkillAcquisitionError("candidate skill does not match detected gap")
        if not skill.enabled:
            raise SkillAcquisitionError("candidate skill must be enabled")
        self._validate_candidate_dependencies(skill, registry)
        base = registry.skills.get(skill.skill_id)
        if base and skill.version == base.version:
            raise SkillAcquisitionError("candidate must advance the active skill version")
        candidate = SkillCandidate(
            candidate_id=f"skill-candidate-{uuid4().hex}",
            skill_id=skill.skill_id,
            base_version=base.version if base else None,
            candidate_version=skill.version,
            mission_id=gap.mission_id,
            reason=gap.reason,
            status="pending",
            skill=skill,
        )
        with self._connect() as db:
            db.execute(
                """INSERT INTO skill_candidates (
                    candidate_id, skill_id, base_version, candidate_version,
                    mission_id, reason, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)""",
                (
                    candidate.candidate_id,
                    candidate.skill_id,
                    candidate.base_version,
                    candidate.candidate_version,
                    candidate.mission_id,
                    candidate.reason,
                    self._now(),
                ),
            )
        return candidate

    def validate_candidate(self, candidate: SkillCandidate,
                           registry: SkillRegistry) -> SkillCandidate:
        if candidate.skill is None:
            raise SkillAcquisitionError("candidate has no skill definition")
        try:
            trial = SkillRegistry({**registry.skills, candidate.skill.skill_id: candidate.skill})
            trial.resolve(candidate.skill.skill_id)
        except (SkillRegistryError, ValueError) as exc:
            raise SkillAcquisitionError(f"candidate validation failed: {exc}") from exc
        return self._set_status(candidate, "validated")

    def approve(self, candidate: SkillCandidate) -> SkillCandidate:
        if candidate.status != "validated":
            raise SkillAcquisitionError("candidate must be validated before governance decision")
        return self._set_status(candidate, "approved")

    def reject(self, candidate: SkillCandidate) -> SkillCandidate:
        if candidate.status != "validated":
            raise SkillAcquisitionError("candidate must be validated before governance decision")
        return self._set_status(candidate, "rejected")

    def promote(self, candidate: SkillCandidate, registry: SkillRegistry) -> SkillRegistry:
        if candidate.status != "approved" or candidate.skill is None:
            raise SkillAcquisitionError("only an approved validated candidate may be promoted")
        existing = registry.skills.get(candidate.skill_id)
        if existing and existing.version != candidate.base_version:
            raise SkillAcquisitionError("active skill changed since candidate creation")
        return SkillRegistry({**registry.skills, candidate.skill_id: candidate.skill})

    def _validate_candidate_dependencies(self, skill: SkillSpec, registry: SkillRegistry) -> None:
        for dependency in skill.dependencies:
            if dependency != skill.skill_id and dependency not in registry.skills:
                raise SkillAcquisitionError(f"candidate dependency is unavailable: {dependency}")

    def _set_status(self, candidate: SkillCandidate, status: str) -> SkillCandidate:
        with self._connect() as db:
            updated = db.execute(
                "UPDATE skill_candidates SET status=? WHERE candidate_id=? AND status IN ('pending','validated')",
                (status, candidate.candidate_id)).rowcount
            if not updated:
                raise SkillAcquisitionError(f"candidate cannot transition to {status}: {candidate.candidate_id}")
        return SkillCandidate(candidate.candidate_id, candidate.skill_id, candidate.base_version,
                              candidate.candidate_version, candidate.mission_id, candidate.reason,
                              status, candidate.skill)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()


__all__ = ["SkillAcquisitionError", "SkillAcquisitionRuntime", "SkillCandidate", "SkillGap"]
