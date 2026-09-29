"""Durable storage for Director policy validations and lifecycle activations."""

from __future__ import annotations

from dataclasses import asdict
import json
import sqlite3
from pathlib import Path

from .director_decision_policy_lifecycle import (
    DirectorDecisionPolicyActivation,
    DirectorDecisionPolicyValidation,
)


class DirectorDecisionPolicyLifecycleStore:
    """Persist lifecycle evidence separately from the active policy implementation."""

    def __init__(self, path: str | Path) -> None:
        self.path=Path(path); self.path.parent.mkdir(parents=True, exist_ok=True); self._initialize()

    def _connect(self) -> sqlite3.Connection:
        db=sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory=sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=30000")
        return db

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS director_decision_policy_validations (
                    validation_id TEXT PRIMARY KEY,
                    proposal_id TEXT NOT NULL,
                    holdout_observations INTEGER NOT NULL,
                    baseline_consistent INTEGER NOT NULL,
                    candidate_consistent INTEGER NOT NULL,
                    canary_eligible INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS director_decision_policy_activations (
                    activation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    proposal_id TEXT NOT NULL,
                    validation_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    authorized_by TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)

    def record_validation(self, validation: DirectorDecisionPolicyValidation) -> None:
        if not validation.validation_id:
            raise ValueError("only persisted validations with an id may be recorded")
        with self._connect() as db:
            db.execute(
                """
                INSERT OR REPLACE INTO director_decision_policy_validations (
                    validation_id, proposal_id, holdout_observations,
                    baseline_consistent, candidate_consistent,
                    canary_eligible, reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    validation.validation_id,
                    validation.proposal_id,
                    validation.holdout_observations,
                    int(validation.baseline_consistent),
                    int(validation.candidate_consistent),
                    int(validation.canary_eligible),
                    validation.reason,
                    validation.created_at,
                ),
            )

    def validation(self, validation_id: str) -> DirectorDecisionPolicyValidation | None:
        with self._connect() as db:
            row=db.execute(
                "SELECT * FROM director_decision_policy_validations WHERE validation_id=?",
                (validation_id,),
            ).fetchone()
        if row is None:
            return None
        return DirectorDecisionPolicyValidation(
            validation_id=str(row["validation_id"]),
            proposal_id=str(row["proposal_id"]),
            holdout_observations=int(row["holdout_observations"]),
            baseline_consistent=bool(row["baseline_consistent"]),
            candidate_consistent=bool(row["candidate_consistent"]),
            canary_eligible=bool(row["canary_eligible"]),
            reason=str(row["reason"]),
            created_at=str(row["created_at"]),
        )

    def record_activation(self, activation: DirectorDecisionPolicyActivation) -> None:
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO director_decision_policy_activations (
                    proposal_id, validation_id, state, authorized_by, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    activation.proposal_id,
                    activation.validation_id,
                    activation.state,
                    activation.authorized_by,
                    activation.created_at,
                ),
            )

    def activations_for(self, proposal_id: str) -> tuple[DirectorDecisionPolicyActivation, ...]:
        with self._connect() as db:
            rows=db.execute(
                """
                SELECT proposal_id, validation_id, state, authorized_by, created_at
                FROM director_decision_policy_activations
                WHERE proposal_id=? ORDER BY activation_id
                """,
                (proposal_id,),
            ).fetchall()
        return tuple(
            DirectorDecisionPolicyActivation(
                proposal_id=str(row["proposal_id"]),
                validation_id=str(row["validation_id"]),
                state=str(row["state"]),
                authorized_by=str(row["authorized_by"]),
                created_at=str(row["created_at"]),
            )
            for row in rows
        )


__all__=["DirectorDecisionPolicyLifecycleStore"]
