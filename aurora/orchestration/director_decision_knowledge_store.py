"""Durable store for promoted Director empirical knowledge."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3

from .director_decision_knowledge import DirectorDecisionKnowledge


class DirectorDecisionKnowledgeStore:
    """Persist promoted empirical knowledge independently from policy state."""

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
                CREATE TABLE IF NOT EXISTS director_decision_knowledge (
                    knowledge_id TEXT PRIMARY KEY,
                    alternative TEXT NOT NULL,
                    chosen_decision TEXT NOT NULL,
                    comparability_basis_json TEXT NOT NULL,
                    observed_direction TEXT NOT NULL,
                    decision_events INTEGER NOT NULL,
                    observations INTEGER NOT NULL,
                    sources_json TEXT NOT NULL,
                    claim TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(
                        alternative,
                        chosen_decision,
                        comparability_basis_json,
                        observed_direction
                    )
                )
                """
            )

    def record(self, knowledge: DirectorDecisionKnowledge) -> None:
        """Insert or refresh a promoted cohort without changing its identity."""
        comparability_basis_json = json.dumps(
            knowledge.comparability_basis,
            sort_keys=True,
        )
        sources_json = json.dumps(knowledge.sources, sort_keys=True)
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO director_decision_knowledge (
                    knowledge_id, alternative, chosen_decision,
                    comparability_basis_json, observed_direction,
                    decision_events, observations, sources_json,
                    claim, state, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (
                    alternative,
                    chosen_decision,
                    comparability_basis_json,
                    observed_direction
                ) DO UPDATE SET
                    decision_events = excluded.decision_events,
                    observations = excluded.observations,
                    sources_json = excluded.sources_json,
                    claim = excluded.claim,
                    state = excluded.state
                """,
                (
                    knowledge.knowledge_id,
                    knowledge.alternative,
                    knowledge.chosen_decision,
                    comparability_basis_json,
                    knowledge.observed_direction,
                    knowledge.decision_events,
                    knowledge.observations,
                    sources_json,
                    knowledge.claim,
                    knowledge.state,
                    knowledge.created_at,
                ),
            )

    def get(self, knowledge_id: str) -> DirectorDecisionKnowledge | None:
        with self._connect() as db:
            row = db.execute(
                """
                SELECT * FROM director_decision_knowledge
                WHERE knowledge_id = ?
                """,
                (knowledge_id,),
            ).fetchone()
        return self._row(row) if row is not None else None

    def list_all(self) -> tuple[DirectorDecisionKnowledge, ...]:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT * FROM director_decision_knowledge
                ORDER BY knowledge_id
                """
            ).fetchall()
        return tuple(self._row(row) for row in rows)

    @staticmethod
    def _row(row: sqlite3.Row) -> DirectorDecisionKnowledge:
        return DirectorDecisionKnowledge(
            knowledge_id=str(row["knowledge_id"]),
            alternative=str(row["alternative"]),
            chosen_decision=str(row["chosen_decision"]),
            comparability_basis=tuple(json.loads(row["comparability_basis_json"])),
            observed_direction=str(row["observed_direction"]),
            decision_events=int(row["decision_events"]),
            observations=int(row["observations"]),
            sources=tuple(json.loads(row["sources_json"])),
            claim=str(row["claim"]),
            state=str(row["state"]),
            created_at=str(row["created_at"]),
        )


__all__ = ["DirectorDecisionKnowledgeStore"]
