"""Durable immutable history for activated Director policy contracts."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .director_decision_policy_contract import DirectorDecisionPolicyContract


class DirectorDecisionPolicyContractStoreError(ValueError):
    """Raised when contract history cannot preserve immutability."""


class DirectorDecisionPolicyContractStore:
    """Persist the exact typed contract consumed by the Director runtime."""

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
                CREATE TABLE IF NOT EXISTS director_decision_policy_contracts (
                    contract_id TEXT PRIMARY KEY,
                    version INTEGER NOT NULL,
                    proposal_id TEXT NOT NULL,
                    validation_id TEXT NOT NULL,
                    knowledge_id TEXT NOT NULL,
                    baseline_decision TEXT NOT NULL,
                    candidate_decision TEXT NOT NULL,
                    observed_direction TEXT NOT NULL,
                    comparability_basis_json TEXT NOT NULL,
                    directive TEXT NOT NULL,
                    proposed_change TEXT NOT NULL,
                    evidence_summary TEXT NOT NULL,
                    authorized_by TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(proposal_id, validation_id, version)
                )
                """
            )

    def record(self, contract: DirectorDecisionPolicyContract) -> None:
        if contract.state != "active":
            raise DirectorDecisionPolicyContractStoreError(
                "only active policy contracts may be recorded"
            )
        payload = (
            contract.contract_id,
            contract.version,
            contract.proposal_id,
            contract.validation_id,
            contract.knowledge_id,
            contract.baseline_decision,
            contract.candidate_decision,
            contract.observed_direction,
            json.dumps(contract.comparability_basis, sort_keys=True),
            contract.directive,
            contract.proposed_change,
            contract.evidence_summary,
            contract.authorized_by,
            contract.state,
            contract.created_at,
        )
        with self._connect() as db:
            existing = db.execute(
                "SELECT * FROM director_decision_policy_contracts WHERE contract_id=?",
                (contract.contract_id,),
            ).fetchone()
            if existing is not None:
                if self._row(existing) != contract:
                    raise DirectorDecisionPolicyContractStoreError(
                        "contract id already exists with different immutable content"
                    )
                return
            try:
                db.execute(
                    """
                    INSERT INTO director_decision_policy_contracts (
                        contract_id, version, proposal_id, validation_id,
                        knowledge_id, baseline_decision, candidate_decision,
                        observed_direction, comparability_basis_json, directive,
                        proposed_change, evidence_summary, authorized_by,
                        state, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    payload,
                )
            except sqlite3.IntegrityError as exc:
                raise DirectorDecisionPolicyContractStoreError(
                    "policy contract version already exists for proposal and validation"
                ) from exc

    def get(self, contract_id: str) -> DirectorDecisionPolicyContract | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM director_decision_policy_contracts WHERE contract_id=?",
                (contract_id,),
            ).fetchone()
        return self._row(row) if row is not None else None

    def list_all(self) -> tuple[DirectorDecisionPolicyContract, ...]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM director_decision_policy_contracts ORDER BY created_at, contract_id"
            ).fetchall()
        return tuple(self._row(row) for row in rows)

    @staticmethod
    def _row(row: sqlite3.Row) -> DirectorDecisionPolicyContract:
        return DirectorDecisionPolicyContract(
            contract_id=str(row["contract_id"]),
            version=int(row["version"]),
            proposal_id=str(row["proposal_id"]),
            validation_id=str(row["validation_id"]),
            knowledge_id=str(row["knowledge_id"]),
            baseline_decision=str(row["baseline_decision"]),
            candidate_decision=str(row["candidate_decision"]),
            observed_direction=str(row["observed_direction"]),
            comparability_basis=tuple(json.loads(row["comparability_basis_json"])),
            directive=str(row["directive"]),
            proposed_change=str(row["proposed_change"]),
            evidence_summary=str(row["evidence_summary"]),
            authorized_by=str(row["authorized_by"]),
            state=str(row["state"]),
            created_at=str(row["created_at"]),
        )


__all__ = [
    "DirectorDecisionPolicyContractStore",
    "DirectorDecisionPolicyContractStoreError",
]
