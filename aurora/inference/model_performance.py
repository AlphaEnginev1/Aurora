"""Durable, scoped model-performance learning for dynamic routing.

Model performance is intentionally scoped by skill/version, task type, and a
coarse context bucket. Only evidence-complete evaluations are trusted for
routing; incomplete evidence remains durable but cannot teach the router.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
import sqlite3


@dataclass(frozen=True)
class ModelPerformance:
    model: str
    skill_id: str
    skill_version: str | None
    task_type: str
    context_bucket: str
    evaluations: int
    average_score: float


class ModelPerformanceStore:
    """SQLite ledger for model outcomes used by adaptive routing."""

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
                """
                CREATE TABLE IF NOT EXISTS model_performance (
                    performance_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    model TEXT NOT NULL,
                    skill_id TEXT NOT NULL,
                    skill_version TEXT,
                    task_type TEXT NOT NULL,
                    context_bucket TEXT NOT NULL,
                    score REAL NOT NULL,
                    evidence_completeness REAL NOT NULL,
                    evidence_state TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_model_perf_scope "
                "ON model_performance(skill_id, skill_version, task_type, context_bucket, model)"
            )

    def record(
        self,
        *,
        model: str | None,
        skill_id: str,
        skill_version: str | None,
        task_type: str,
        context_bucket: str,
        score: float,
        evidence_completeness: float,
        evidence_state: str,
        created_at: str,
    ) -> None:
        if not model:
            return
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO model_performance (
                    model, skill_id, skill_version, task_type, context_bucket,
                    score, evidence_completeness, evidence_state, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    model, skill_id, skill_version, task_type, context_bucket,
                    max(0.0, min(1.0, float(score))),
                    max(0.0, min(1.0, float(evidence_completeness))),
                    evidence_state,
                    created_at,
                ),
            )

    def trusted_scores_for(
        self,
        *,
        skill_ids: tuple[str, ...],
        skill_versions: Mapping[str, str | None] | None = None,
        task_type: str,
        context_bucket: str,
        minimum_evaluations: int = 2,
        minimum_evidence_completeness: float = 0.75,
    ) -> dict[str, float]:
        if not skill_ids:
            return {}
        if minimum_evaluations < 1:
            raise ValueError("minimum_evaluations must be at least 1")
        if not 0.0 <= minimum_evidence_completeness <= 1.0:
            raise ValueError("minimum_evidence_completeness must be between 0 and 1")
        scope = skill_versions or {skill_id: None for skill_id in skill_ids}
        predicates: list[str] = []
        params: list[object] = []
        for skill_id in skill_ids:
            version = scope.get(skill_id)
            if version is None:
                predicates.append("(skill_id = ? AND skill_version IS NULL)")
                params.append(skill_id)
            else:
                predicates.append("(skill_id = ? AND skill_version = ?)")
                params.extend((skill_id, version))
        with self._connect() as db:
            rows = db.execute(
                f"""
                SELECT model, AVG(score) AS average_score, COUNT(*) AS evaluations,
                       AVG(evidence_completeness) AS evidence_completeness
                FROM model_performance
                WHERE ({" OR ".join(predicates)})
                  AND task_type = ?
                  AND context_bucket = ?
                  AND evidence_state = 'evidence_complete'
                GROUP BY model
                HAVING COUNT(*) >= ? AND AVG(evidence_completeness) >= ?
                """,
                (*params, task_type, context_bucket,
                 minimum_evaluations, minimum_evidence_completeness),
            ).fetchall()
        return {str(row["model"]): float(row["average_score"]) for row in rows}
