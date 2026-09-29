"""Durable ledger for Director policy evolution proposals."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .director_decision_policy_evolution import DirectorDecisionPolicyProposal


class DirectorDecisionPolicyProposalStore:
    """Persist proposals separately from the active Director policy."""

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
                CREATE TABLE IF NOT EXISTS director_decision_policy_proposals (
                    proposal_id TEXT PRIMARY KEY,
                    knowledge_id TEXT NOT NULL,
                    alternative TEXT NOT NULL,
                    chosen_decision TEXT NOT NULL,
                    observed_direction TEXT NOT NULL,
                    proposed_change TEXT NOT NULL,
                    evidence_summary TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)

    def record(self, proposal: DirectorDecisionPolicyProposal) -> None:
        if proposal.state != "proposed":
            raise ValueError("only proposed policy changes may be recorded")
        with self._connect() as db:
            db.execute(
                """
                INSERT OR IGNORE INTO director_decision_policy_proposals (
                    proposal_id, knowledge_id, alternative, chosen_decision,
                    observed_direction, proposed_change, evidence_summary,
                    state, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proposal.proposal_id,
                    proposal.knowledge_id,
                    proposal.alternative,
                    proposal.chosen_decision,
                    proposal.observed_direction,
                    proposal.proposed_change,
                    proposal.evidence_summary,
                    proposal.state,
                    proposal.created_at,
                ),
            )

    def get(self, proposal_id: str) -> DirectorDecisionPolicyProposal | None:
        with self._connect() as db:
            row=db.execute(
                "SELECT * FROM director_decision_policy_proposals WHERE proposal_id=?",
                (proposal_id,),
            ).fetchone()
        return self._row(row) if row is not None else None

    def list_all(self) -> tuple[DirectorDecisionPolicyProposal, ...]:
        with self._connect() as db:
            rows=db.execute(
                "SELECT * FROM director_decision_policy_proposals ORDER BY proposal_id"
            ).fetchall()
        return tuple(self._row(row) for row in rows)

    @staticmethod
    def _row(row: sqlite3.Row) -> DirectorDecisionPolicyProposal:
        return DirectorDecisionPolicyProposal(
            proposal_id=str(row["proposal_id"]),
            knowledge_id=str(row["knowledge_id"]),
            alternative=str(row["alternative"]),
            chosen_decision=str(row["chosen_decision"]),
            observed_direction=str(row["observed_direction"]),
            proposed_change=str(row["proposed_change"]),
            evidence_summary=str(row["evidence_summary"]),
            state=str(row["state"]),
            created_at=str(row["created_at"]),
        )


__all__=["DirectorDecisionPolicyProposalStore"]
