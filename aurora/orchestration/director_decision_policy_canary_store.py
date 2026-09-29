"""Durable storage for Director policy shadow-canary observations."""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3
from pathlib import Path

from .director_decision_policy_canary import DirectorDecisionPolicyShadowObservation


@dataclass(frozen=True)
class DirectorDecisionPolicyShadowSummary:
    proposal_id: str
    observations: int
    agreements: int
    disagreements: int
    outcomes_observed: int
    candidate_execution_attempts: int
    baseline_outcomes: tuple[str, ...]
    state: str


@dataclass(frozen=True)
class DirectorDecisionPolicyCanaryReadiness:
    proposal_id: str
    observations: int
    agreements: int
    disagreements: int
    agreement_rate: float
    governance_review_ready: bool
    reason: str


class DirectorDecisionPolicyShadowCanaryStore:
    """Persist shadow observations separately from active policy state."""

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
                CREATE TABLE IF NOT EXISTS director_decision_policy_shadow_observations (
                    observation_id TEXT PRIMARY KEY,
                    proposal_id TEXT NOT NULL,
                    validation_id TEXT NOT NULL,
                    decision_event_id INTEGER NOT NULL,
                    baseline_decision TEXT NOT NULL,
                    candidate_decision TEXT NOT NULL,
                    actual_outcome TEXT NOT NULL,
                    decision_agreement INTEGER NOT NULL,
                    baseline_executed INTEGER NOT NULL,
                    candidate_executed INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(proposal_id, decision_event_id)
                )
                """
            )
            db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_director_policy_shadow_proposal
                ON director_decision_policy_shadow_observations(proposal_id)
                """
            )

    def record(self, observation: DirectorDecisionPolicyShadowObservation) -> None:
        if observation.state != "shadow_observed":
            raise ValueError("only shadow-observed policy decisions may be recorded")
        if not observation.baseline_executed:
            raise ValueError("baseline execution must be true for shadow observations")
        if observation.candidate_executed:
            raise ValueError("candidate execution is forbidden in shadow mode")
        with self._connect() as db:
            db.execute(
                """
                INSERT OR IGNORE INTO director_decision_policy_shadow_observations (
                    observation_id, proposal_id, validation_id, decision_event_id,
                    baseline_decision, candidate_decision, actual_outcome,
                    decision_agreement, baseline_executed, candidate_executed,
                    state, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    observation.observation_id,
                    observation.proposal_id,
                    observation.validation_id,
                    observation.decision_event_id,
                    observation.baseline_decision,
                    observation.candidate_decision,
                    observation.actual_outcome,
                    int(observation.decision_agreement),
                    int(observation.baseline_executed),
                    int(observation.candidate_executed),
                    observation.state,
                    observation.created_at,
                ),
            )

    def list_for_proposal(
        self,
        proposal_id: str,
    ) -> tuple[DirectorDecisionPolicyShadowObservation, ...]:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT * FROM director_decision_policy_shadow_observations
                WHERE proposal_id=?
                ORDER BY decision_event_id, observation_id
                """,
                (proposal_id,),
            ).fetchall()
        return tuple(self._row(row) for row in rows)

    def summarize_for_proposal(
        self,
        proposal_id: str,
    ) -> DirectorDecisionPolicyShadowSummary:
        observations = self.list_for_proposal(proposal_id)
        agreements = sum(item.decision_agreement for item in observations)
        disagreements = len(observations) - agreements
        outcomes = tuple(dict.fromkeys(item.actual_outcome for item in observations))
        candidate_execution_attempts = sum(
            1 for item in observations if item.candidate_executed
        )
        integrity_state = "shadow_integrity_intact"
        if candidate_execution_attempts:
            integrity_state = "shadow_integrity_violation"
        return DirectorDecisionPolicyShadowSummary(
            proposal_id=proposal_id,
            observations=len(observations),
            agreements=agreements,
            disagreements=disagreements,
            outcomes_observed=len(observations),
            candidate_execution_attempts=candidate_execution_attempts,
            baseline_outcomes=outcomes,
            state=integrity_state,
        )

    def readiness(
        self,
        proposal_id: str,
        *,
        min_observations: int = 5,
    ) -> DirectorDecisionPolicyCanaryReadiness:
        if min_observations < 1:
            raise ValueError("min_observations must be at least 1")
        summary = self.summarize_for_proposal(proposal_id)
        agreement_rate = (
            summary.agreements / summary.observations
            if summary.observations
            else 0.0
        )
        if summary.state == "shadow_integrity_violation":
            return DirectorDecisionPolicyCanaryReadiness(
                proposal_id=proposal_id,
                observations=summary.observations,
                agreements=summary.agreements,
                disagreements=summary.disagreements,
                agreement_rate=agreement_rate,
                governance_review_ready=False,
                reason="shadow_integrity_violation",
            )
        if summary.observations < min_observations:
            return DirectorDecisionPolicyCanaryReadiness(
                proposal_id=proposal_id,
                observations=summary.observations,
                agreements=summary.agreements,
                disagreements=summary.disagreements,
                agreement_rate=agreement_rate,
                governance_review_ready=False,
                reason="insufficient_shadow_observations",
            )
        return DirectorDecisionPolicyCanaryReadiness(
            proposal_id=proposal_id,
            observations=summary.observations,
            agreements=summary.agreements,
            disagreements=summary.disagreements,
            agreement_rate=agreement_rate,
            governance_review_ready=True,
            reason="sufficient_shadow_observations_for_governance_review",
        )

    @staticmethod
    def _row(row: sqlite3.Row) -> DirectorDecisionPolicyShadowObservation:
        return DirectorDecisionPolicyShadowObservation(
            observation_id=str(row["observation_id"]),
            proposal_id=str(row["proposal_id"]),
            validation_id=str(row["validation_id"]),
            decision_event_id=int(row["decision_event_id"]),
            baseline_decision=str(row["baseline_decision"]),
            candidate_decision=str(row["candidate_decision"]),
            actual_outcome=str(row["actual_outcome"]),
            decision_agreement=bool(row["decision_agreement"]),
            baseline_executed=bool(row["baseline_executed"]),
            candidate_executed=bool(row["candidate_executed"]),
            state=str(row["state"]),
            created_at=str(row["created_at"]),
        )


__all__ = [
    "DirectorDecisionPolicyCanaryReadiness",
    "DirectorDecisionPolicyShadowCanaryStore",
    "DirectorDecisionPolicyShadowSummary",
]
