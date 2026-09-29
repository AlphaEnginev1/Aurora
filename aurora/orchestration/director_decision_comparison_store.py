"""Durable ledger for governed Director comparative evidence."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from typing import Iterable


@dataclass(frozen=True)
class DirectorDecisionComparisonSummary:
    alternative: str
    chosen_decision: str | None
    observations: int
    better_observations: int
    worse_observations: int
    same_observations: int
    undetermined_observations: int
    consistent_direction: str
    sources: tuple[str, ...]


@dataclass(frozen=True)
class StoredDirectorDecisionComparison:
    comparison_id: int
    decision_event_id: int
    chosen_decision: str
    alternative: str
    source: str
    alternative_execution_id: str
    chosen_execution_id: str
    chosen_outcome: str
    alternative_outcome: str
    comparability_basis: tuple[str, ...]
    observed_direction: str
    causal_attribution: str
    state: str
    reason: str
    evidence: tuple[str, ...]
    created_at: str


class DirectorDecisionComparisonStore:
    """Persist comparative observations without folding them into performance aggregates."""

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
            db.execute("""
                CREATE TABLE IF NOT EXISTS director_decision_comparisons (
                    comparison_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    decision_event_id INTEGER NOT NULL,
                    chosen_decision TEXT NOT NULL,
                    alternative TEXT NOT NULL,
                    source TEXT NOT NULL,
                    alternative_execution_id TEXT NOT NULL,
                    chosen_execution_id TEXT NOT NULL,
                    chosen_outcome TEXT NOT NULL,
                    alternative_outcome TEXT NOT NULL,
                    comparability_basis_json TEXT NOT NULL,
                    observed_direction TEXT NOT NULL,
                    causal_attribution TEXT NOT NULL,
                    state TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(decision_event_id, alternative, alternative_execution_id, chosen_execution_id)
                )
            """)
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_director_comparison_decision "
                "ON director_decision_comparisons(decision_event_id)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_director_comparison_alternative "
                "ON director_decision_comparisons(alternative)"
            )

    def record(
        self,
        *,
        decision_event_id: int,
        chosen_decision: str,
        alternative: str,
        source: str,
        alternative_execution_id: str,
        chosen_execution_id: str,
        chosen_outcome: str,
        alternative_outcome: str,
        comparability_basis: Iterable[str],
        observed_direction: str,
        causal_attribution: str,
        state: str,
        reason: str,
        evidence: Iterable[str],
        created_at: str,
    ) -> None:
        if source not in {"independent_execution", "historical_comparable"}:
            raise ValueError("invalid comparison source")
        if causal_attribution != "unsupported":
            raise ValueError("causal attribution is not supported")
        if observed_direction not in {"better", "worse", "same", "undetermined"}:
            raise ValueError("invalid observed direction")
        basis = tuple(dict.fromkeys(item for item in comparability_basis if item))
        if not basis:
            raise ValueError("comparability basis is required")
        with self._connect() as db:
            db.execute(
                """
                INSERT OR IGNORE INTO director_decision_comparisons (
                    decision_event_id, chosen_decision, alternative, source,
                    alternative_execution_id, chosen_execution_id,
                    chosen_outcome, alternative_outcome,
                    comparability_basis_json, observed_direction,
                    causal_attribution, state, reason, evidence_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(decision_event_id), chosen_decision, alternative, source,
                    alternative_execution_id, chosen_execution_id,
                    chosen_outcome, alternative_outcome,
                    json.dumps(basis, sort_keys=True),
                    observed_direction, causal_attribution, state, reason,
                    json.dumps(list(dict.fromkeys(evidence)), sort_keys=True),
                    created_at,
                ),
            )

    def summarize_for_decision(
        self, decision_event_id: int
    ) -> tuple[DirectorDecisionComparisonSummary, ...]:
        """Summarize repeated observations without asserting causality."""
        rows = self.list_for_decision(decision_event_id)
        grouped: dict[tuple[str, str], list[StoredDirectorDecisionComparison]] = {}
        for row in rows:
            grouped.setdefault((row.alternative, row.chosen_decision), []).append(row)

        summaries: list[DirectorDecisionComparisonSummary] = []
        for (alternative, chosen_decision), items in grouped.items():
            directions = [item.observed_direction for item in items]
            better = directions.count("better")
            worse = directions.count("worse")
            same = directions.count("same")
            undetermined = directions.count("undetermined")
            observed = {d for d in directions if d in {"better", "worse", "same"}}
            if len(observed) == 1 and undetermined == 0:
                consistent = next(iter(observed))
            elif len(observed) == 0:
                consistent = "undetermined"
            else:
                consistent = "mixed"
            summaries.append(
                DirectorDecisionComparisonSummary(
                    alternative=alternative,
                    chosen_decision=chosen_decision,
                    observations=len(items),
                    better_observations=better,
                    worse_observations=worse,
                    same_observations=same,
                    undetermined_observations=undetermined,
                    consistent_direction=consistent,
                    sources=tuple(sorted({item.source for item in items})),
                )
            )
        return tuple(summaries)

    def list_for_decision(
        self, decision_event_id: int
    ) -> tuple[StoredDirectorDecisionComparison, ...]:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT * FROM director_decision_comparisons
                WHERE decision_event_id = ?
                ORDER BY comparison_id
                """,
                (int(decision_event_id),),
            ).fetchall()
        return tuple(self._row(row) for row in rows)

    @staticmethod
    def _row(row: sqlite3.Row) -> StoredDirectorDecisionComparison:
        return StoredDirectorDecisionComparison(
            comparison_id=int(row["comparison_id"]),
            decision_event_id=int(row["decision_event_id"]),
            chosen_decision=str(row["chosen_decision"]),
            alternative=str(row["alternative"]),
            source=str(row["source"]),
            alternative_execution_id=str(row["alternative_execution_id"]),
            chosen_execution_id=str(row["chosen_execution_id"]),
            chosen_outcome=str(row["chosen_outcome"]),
            alternative_outcome=str(row["alternative_outcome"]),
            comparability_basis=tuple(json.loads(row["comparability_basis_json"])),
            observed_direction=str(row["observed_direction"]),
            causal_attribution=str(row["causal_attribution"]),
            state=str(row["state"]),
            reason=str(row["reason"]),
            evidence=tuple(json.loads(row["evidence_json"])),
            created_at=str(row["created_at"]),
        )


__all__ = ["DirectorDecisionComparisonStore", "DirectorDecisionComparisonSummary", "StoredDirectorDecisionComparison"]
