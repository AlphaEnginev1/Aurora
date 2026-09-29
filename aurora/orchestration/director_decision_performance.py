"""Durable aggregate learning for Director decision outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import sqlite3
from typing import Iterable


@dataclass(frozen=True)
class DirectorDecisionPerformance:
    decision: str
    evaluations: int
    average_score: float
    successful_evaluations: int
    success_rate: float


class DirectorDecisionPerformanceStore:
    """SQLite ledger aggregating observed outcomes of Director decisions."""

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
                CREATE TABLE IF NOT EXISTS director_decision_performance (
                    evaluation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    decision_event_id INTEGER NOT NULL UNIQUE,
                    round INTEGER NOT NULL,
                    decision TEXT NOT NULL,
                    score REAL NOT NULL,
                    outcome TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_director_decision_perf_decision "
                "ON director_decision_performance(decision)"
            )

    def record(
        self,
        *,
        decision_event_id: int,
        round: int,
        decision: str,
        score: float,
        outcome: str,
        evidence: Iterable[str],
        created_at: str,
    ) -> None:
        with self._connect() as db:
            db.execute(
                """
                INSERT OR IGNORE INTO director_decision_performance (
                    decision_event_id, round, decision, score, outcome,
                    evidence_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(decision_event_id),
                    int(round),
                    decision,
                    max(0.0, min(1.0, float(score))),
                    outcome,
                    json.dumps(list(evidence), sort_keys=True),
                    created_at,
                ),
            )

    def summary(
        self,
        *,
        minimum_evaluations: int = 1,
    ) -> tuple[DirectorDecisionPerformance, ...]:
        if minimum_evaluations < 1:
            raise ValueError("minimum_evaluations must be at least 1")
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT decision,
                       COUNT(*) AS evaluations,
                       AVG(score) AS average_score,
                       SUM(CASE WHEN score >= 1.0 THEN 1 ELSE 0 END)
                           AS successful_evaluations
                FROM director_decision_performance
                GROUP BY decision
                HAVING COUNT(*) >= ?
                ORDER BY decision
                """,
                (minimum_evaluations,),
            ).fetchall()
        return tuple(
            DirectorDecisionPerformance(
                decision=str(row["decision"]),
                evaluations=int(row["evaluations"]),
                average_score=float(row["average_score"]),
                successful_evaluations=int(row["successful_evaluations"]),
                success_rate=(
                    float(row["successful_evaluations"]) / int(row["evaluations"])
                ),
            )
            for row in rows
        )


__all__ = [
    "DirectorDecisionPerformance",
    "DirectorDecisionPerformanceStore",
]
