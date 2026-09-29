"""Durable storage for formal Director policy canary evaluations."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .director_decision_policy_canary_evaluation import (
    DirectorDecisionPolicyCanaryEvaluation,
)


class DirectorDecisionPolicyCanaryEvaluationStore:
    """Persist canary evaluations independently from policy activation state."""

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
                CREATE TABLE IF NOT EXISTS director_decision_policy_canary_evaluations (
                    evaluation_id TEXT PRIMARY KEY,
                    proposal_id TEXT NOT NULL,
                    observations INTEGER NOT NULL,
                    agreements INTEGER NOT NULL,
                    disagreements INTEGER NOT NULL,
                    agreement_rate REAL NOT NULL,
                    governance_review_ready INTEGER NOT NULL,
                    rollback_required INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    def record(self, evaluation: DirectorDecisionPolicyCanaryEvaluation) -> None:
        with self._connect() as db:
            db.execute(
                """
                INSERT OR REPLACE INTO director_decision_policy_canary_evaluations (
                    evaluation_id, proposal_id, observations, agreements,
                    disagreements, agreement_rate, governance_review_ready,
                    rollback_required, state, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evaluation.evaluation_id,
                    evaluation.proposal_id,
                    evaluation.observations,
                    evaluation.agreements,
                    evaluation.disagreements,
                    evaluation.agreement_rate,
                    int(evaluation.governance_review_ready),
                    int(evaluation.rollback_required),
                    evaluation.state,
                    evaluation.reason,
                    evaluation.created_at,
                ),
            )

    def get(self, evaluation_id: str) -> DirectorDecisionPolicyCanaryEvaluation | None:
        with self._connect() as db:
            row = db.execute(
                """
                SELECT * FROM director_decision_policy_canary_evaluations
                WHERE evaluation_id=?
                """,
                (evaluation_id,),
            ).fetchone()
        return self._row(row) if row is not None else None

    def latest_for_proposal(
        self,
        proposal_id: str,
    ) -> DirectorDecisionPolicyCanaryEvaluation | None:
        with self._connect() as db:
            row = db.execute(
                """
                SELECT * FROM director_decision_policy_canary_evaluations
                WHERE proposal_id=?
                ORDER BY created_at DESC, evaluation_id DESC
                LIMIT 1
                """,
                (proposal_id,),
            ).fetchone()
        return self._row(row) if row is not None else None

    @staticmethod
    def _row(row: sqlite3.Row) -> DirectorDecisionPolicyCanaryEvaluation:
        return DirectorDecisionPolicyCanaryEvaluation(
            evaluation_id=str(row["evaluation_id"]),
            proposal_id=str(row["proposal_id"]),
            observations=int(row["observations"]),
            agreements=int(row["agreements"]),
            disagreements=int(row["disagreements"]),
            agreement_rate=float(row["agreement_rate"]),
            governance_review_ready=bool(row["governance_review_ready"]),
            rollback_required=bool(row["rollback_required"]),
            state=str(row["state"]),
            reason=str(row["reason"]),
            created_at=str(row["created_at"]),
        )


__all__ = ["DirectorDecisionPolicyCanaryEvaluationStore"]
