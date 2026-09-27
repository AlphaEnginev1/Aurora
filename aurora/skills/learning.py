"""Durable specialist-skill learning and controlled evolution proposals."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from .evaluation import SkillEvaluation


class SkillLearningError(RuntimeError):
    """Raised when skill learning state cannot be persisted safely."""


@dataclass(frozen=True)
class SkillPerformance:
    skill_id: str
    evaluations: int
    average_score: float
    failure_count: int
    model_scores: dict[str, float]


@dataclass(frozen=True)
class SkillEvolutionProposal:
    proposal_id: str
    skill_id: str
    base_version: str
    candidate_version: str
    reason: str
    evidence_count: int
    suggested_change: str
    status: str = "pending"


@dataclass(frozen=True)
class SkillEvidenceSnapshot:
    skill_id: str
    skill_version: str
    task_id: str
    role: str
    score: float
    status: str
    model: str | None
    failure_signature: str | None
    evidence_completeness: float
    evidence_state: str
    verification_passed: tuple[str, ...] = ()
    verification_failed: tuple[str, ...] = ()
    verification_missing: tuple[str, ...] = ()
    quality_passed: tuple[str, ...] = ()
    quality_failed: tuple[str, ...] = ()
    quality_missing: tuple[str, ...] = ()


class SkillLearningStore:
    """SQLite-backed evidence ledger for skill performance and model specialization."""

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
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS skill_evaluations (
                    evaluation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    skill_id TEXT NOT NULL,
                    skill_version TEXT NOT NULL,
                    mission_id TEXT,
                    task_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    score REAL NOT NULL,
                    status TEXT NOT NULL,
                    model TEXT,
                    verification_passed_json TEXT NOT NULL,
                    verification_failed_json TEXT NOT NULL,
                    verification_missing_json TEXT NOT NULL,
                    quality_passed_json TEXT NOT NULL,
                    quality_failed_json TEXT NOT NULL,
                    quality_missing_json TEXT NOT NULL,
                    failure_signature TEXT,
                    evidence_completeness REAL NOT NULL DEFAULT 0.0,
                    evidence_state TEXT NOT NULL DEFAULT 'unknown',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS skill_evolution_proposals (
                    proposal_id TEXT PRIMARY KEY,
                    skill_id TEXT NOT NULL,
                    base_version TEXT NOT NULL,
                    candidate_version TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    evidence_count INTEGER NOT NULL,
                    suggested_change TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_skill_eval_skill
                    ON skill_evaluations(skill_id, created_at);
                """
            )

    def record(self, evaluation: SkillEvaluation) -> None:
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO skill_evaluations (
                    skill_id, skill_version, mission_id, task_id, role, score, status, model,
                    verification_passed_json, verification_failed_json, verification_missing_json,
                    quality_passed_json, quality_failed_json, quality_missing_json,
                    failure_signature, evidence_completeness, evidence_state, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evaluation.skill_id,
                    evaluation.skill_version,
                    evaluation.mission_id,
                    evaluation.task_id,
                    evaluation.role,
                    evaluation.score,
                    evaluation.status,
                    evaluation.model,
                    json.dumps(evaluation.verification_passed),
                    json.dumps(evaluation.verification_failed),
                    json.dumps(evaluation.verification_missing),
                    json.dumps(evaluation.quality_passed),
                    json.dumps(evaluation.quality_failed),
                    json.dumps(evaluation.quality_missing),
                    evaluation.failure_signature,
                    evaluation.evidence_completeness,
                    evaluation.evidence_state,
                    self._now(),
                ),
            )

    def evidence_for(
        self,
        skill_id: str,
        *,
        skill_version: str | None = None,
        limit: int = 20,
    ) -> tuple[SkillEvidenceSnapshot, ...]:
        """Return recent immutable execution evidence for governed learning."""
        if limit < 1:
            raise ValueError("limit must be at least 1")
        query = (
            "SELECT skill_id, skill_version, task_id, role, score, status, model, "
            "failure_signature, evidence_completeness, evidence_state, "
            "verification_passed_json, verification_failed_json, verification_missing_json, "
            "quality_passed_json, quality_failed_json, quality_missing_json "
            "FROM skill_evaluations WHERE skill_id = ?"
        )
        params: list[object] = [skill_id]
        if skill_version is not None:
            query += " AND skill_version = ?"
            params.append(skill_version)
        query += " ORDER BY evaluation_id DESC LIMIT ?"
        params.append(limit)
        with self._connect() as db:
            rows = db.execute(query, tuple(params)).fetchall()
        return tuple(
            SkillEvidenceSnapshot(
                skill_id=row["skill_id"],
                skill_version=row["skill_version"],
                task_id=row["task_id"],
                role=row["role"],
                score=float(row["score"]),
                status=row["status"],
                model=row["model"],
                failure_signature=row["failure_signature"],
                evidence_completeness=float(row["evidence_completeness"]),
                evidence_state=row["evidence_state"],
                verification_passed=tuple(json.loads(row["verification_passed_json"])),
                verification_failed=tuple(json.loads(row["verification_failed_json"])),
                verification_missing=tuple(json.loads(row["verification_missing_json"])),
                quality_passed=tuple(json.loads(row["quality_passed_json"])),
                quality_failed=tuple(json.loads(row["quality_failed_json"])),
                quality_missing=tuple(json.loads(row["quality_missing_json"])),
            )
            for row in rows
        )

    def performance(self, skill_id: str) -> SkillPerformance:
        with self._connect() as db:
            rows = db.execute(
                "SELECT score, status, model FROM skill_evaluations WHERE skill_id = ?",
                (skill_id,),
            ).fetchall()
        if not rows:
            return SkillPerformance(skill_id, 0, 0.0, 0, {})
        scores = [float(row["score"]) for row in rows]
        model_buckets: dict[str, list[float]] = {}
        for row in rows:
            if row["model"]:
                model_buckets.setdefault(row["model"], []).append(float(row["score"]))
        return SkillPerformance(
            skill_id=skill_id,
            evaluations=len(rows),
            average_score=sum(scores) / len(scores),
            failure_count=sum(row["status"] != "succeeded" for row in rows),
            model_scores={
                model: sum(values) / len(values)
                for model, values in model_buckets.items()
            },
        )

    def performance_for_version(self, skill_id: str, skill_version: str) -> SkillPerformance:
        """Return evidence-backed performance for one immutable skill version."""
        with self._connect() as db:
            rows = db.execute(
                "SELECT score, status, model FROM skill_evaluations "
                "WHERE skill_id = ? AND skill_version = ?",
                (skill_id, skill_version),
            ).fetchall()
        if not rows:
            return SkillPerformance(skill_id, 0, 0.0, 0, {})
        scores = [float(row["score"]) for row in rows]
        buckets: dict[str, list[float]] = {}
        for row in rows:
            if row["model"]:
                buckets.setdefault(str(row["model"]), []).append(float(row["score"]))
        return SkillPerformance(
            skill_id=skill_id,
            evaluations=len(rows),
            average_score=sum(scores) / len(scores),
            failure_count=sum(row["status"] != "succeeded" for row in rows),
            model_scores={k: sum(v) / len(v) for k, v in buckets.items()},
        )

    def agent_scores_for(
        self,
        skill_ids: tuple[str, ...],
        *,
        minimum_evaluations: int = 2,
        minimum_evidence_completeness: float = 0.75,
    ) -> dict[str, float]:
        """Return trusted historical specialist scores by agent role."""
        if not skill_ids:
            return {}
        if minimum_evaluations < 1:
            raise ValueError("minimum_evaluations must be at least 1")
        if not 0.0 <= minimum_evidence_completeness <= 1.0:
            raise ValueError("minimum_evidence_completeness must be between 0 and 1")
        placeholders = ",".join("?" for _ in skill_ids)
        with self._connect() as db:
            rows = db.execute(
                f"""
                SELECT role, AVG(score) AS average_score, COUNT(*) AS evaluations,
                       AVG(evidence_completeness) AS evidence_completeness
                FROM skill_evaluations
                WHERE skill_id IN ({placeholders}) AND role IS NOT NULL
                GROUP BY role
                HAVING COUNT(*) >= ? AND AVG(evidence_completeness) >= ?
                """,
                (*skill_ids, minimum_evaluations, minimum_evidence_completeness),
            ).fetchall()
        return {
            str(row["role"]): float(row["average_score"])
            for row in rows
        }

    def model_scores_for(
        self,
        skill_ids: tuple[str, ...],
        *,
        minimum_evaluations: int = 2,
        minimum_evidence_completeness: float = 0.75,
    ) -> dict[str, float]:
        if not skill_ids:
            return {}
        if minimum_evaluations < 1:
            raise ValueError("minimum_evaluations must be at least 1")
        if not 0.0 <= minimum_evidence_completeness <= 1.0:
            raise ValueError("minimum_evidence_completeness must be between 0 and 1")
        placeholders = ",".join("?" for _ in skill_ids)
        with self._connect() as db:
            rows = db.execute(
                f"""
                SELECT model, AVG(score) AS average_score, COUNT(*) AS evaluations,
                       AVG(evidence_completeness) AS evidence_completeness
                FROM skill_evaluations
                WHERE skill_id IN ({placeholders}) AND model IS NOT NULL
                GROUP BY model
                HAVING COUNT(*) >= ? AND AVG(evidence_completeness) >= ?
                """,
                (*skill_ids, minimum_evaluations, minimum_evidence_completeness),
            ).fetchall()
        return {
            str(row["model"]): float(row["average_score"])
            for row in rows
        }

    def propose_evolution(
        self,
        *,
        skill_id: str,
        base_version: str,
        suggested_change: str,
        threshold: float = 0.72,
        minimum_evaluations: int = 3,
    ) -> SkillEvolutionProposal | None:
        performance = self.performance(skill_id)
        if performance.evaluations < minimum_evaluations or performance.average_score >= threshold:
            return None
        candidate_version = self._patch_version(base_version)
        reason = (
            f"Observed average skill score {performance.average_score:.3f} across "
            f"{performance.evaluations} evaluations with {performance.failure_count} failures."
        )
        proposal = SkillEvolutionProposal(
            proposal_id=f"skill-evo-{uuid4().hex}",
            skill_id=skill_id,
            base_version=base_version,
            candidate_version=candidate_version,
            reason=reason,
            evidence_count=performance.evaluations,
            suggested_change=suggested_change,
        )
        with self._connect() as db:
            existing = db.execute(
                """
                SELECT 1
                FROM skill_evolution_proposals
                WHERE skill_id = ? AND base_version = ? AND status = 'pending'
                LIMIT 1
                """,
                (proposal.skill_id, proposal.base_version),
            ).fetchone()
            if existing is not None:
                return None
            db.execute(
                """
                INSERT INTO skill_evolution_proposals (
                    proposal_id, skill_id, base_version, candidate_version,
                    reason, evidence_count, suggested_change, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    proposal.proposal_id,
                    proposal.skill_id,
                    proposal.base_version,
                    proposal.candidate_version,
                    proposal.reason,
                    proposal.evidence_count,
                    proposal.suggested_change,
                    self._now(),
                ),
            )
        return proposal

    def resolve_proposal(self, proposal_id: str, *, decision: str) -> SkillEvolutionProposal:
        """Record an explicit governance decision without mutating the active skill."""
        normalized = decision.strip().lower()
        if normalized not in {"approved", "rejected"}:
            raise SkillLearningError("proposal decision must be 'approved' or 'rejected'")
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM skill_evolution_proposals WHERE proposal_id = ?",
                (proposal_id,),
            ).fetchone()
            if row is None:
                raise SkillLearningError(f"unknown skill evolution proposal: {proposal_id}")
            if row["status"] != "pending":
                raise SkillLearningError(
                    f"skill evolution proposal is already resolved: {proposal_id}"
                )
            db.execute(
                "UPDATE skill_evolution_proposals SET status = ? WHERE proposal_id = ?",
                (normalized, proposal_id),
            )
            row = dict(row)
        return SkillEvolutionProposal(
            proposal_id=row["proposal_id"],
            skill_id=row["skill_id"],
            base_version=row["base_version"],
            candidate_version=row["candidate_version"],
            reason=row["reason"],
            evidence_count=int(row["evidence_count"]),
            suggested_change=row["suggested_change"],
            status=normalized,
        )

    def pending_proposals(self, skill_id: str | None = None) -> tuple[SkillEvolutionProposal, ...]:
        query = "SELECT * FROM skill_evolution_proposals WHERE status = 'pending'"
        params: tuple = ()
        if skill_id is not None:
            query += " AND skill_id = ?"
            params = (skill_id,)
        query += " ORDER BY created_at"
        with self._connect() as db:
            rows = db.execute(query, params).fetchall()
        return tuple(
            SkillEvolutionProposal(
                proposal_id=row["proposal_id"],
                skill_id=row["skill_id"],
                base_version=row["base_version"],
                candidate_version=row["candidate_version"],
                reason=row["reason"],
                evidence_count=int(row["evidence_count"]),
                suggested_change=row["suggested_change"],
                status=row["status"],
            )
            for row in rows
        )

    @staticmethod
    def _patch_version(version: str) -> str:
        parts = version.split(".")
        if len(parts) == 3 and all(part.isdigit() for part in parts):
            return f"{parts[0]}.{parts[1]}.{int(parts[2]) + 1}"
        return version + ".next"

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()


class SkillEvolutionEngine:
    """Generate controlled evolution proposals from observed specialist failures."""

    def __init__(self, store: SkillLearningStore) -> None:
        self.store = store

    def observe(self, evaluation: SkillEvaluation, *, base_version: str) -> SkillEvolutionProposal | None:
        missing = list(evaluation.verification_missing) + list(evaluation.quality_missing)
        failed = list(evaluation.verification_failed) + list(evaluation.quality_failed)
        suggestions: list[str] = []
        if failed:
            suggestions.append("Strengthen the procedure around failed checks: " + ", ".join(failed))
        if missing:
            suggestions.append("Make required evidence explicit and machine-observable: " + ", ".join(missing))
        if evaluation.failure_signature:
            suggestions.append("Address recurring execution failure: " + evaluation.failure_signature)
        if not suggestions:
            suggestions.append("Refine the procedure using observed performance data.")
        return self.store.propose_evolution(
            skill_id=evaluation.skill_id,
            base_version=base_version,
            suggested_change=" ".join(suggestions),
        )
