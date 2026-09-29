"""Typed, auditable contract for an activated Director decision policy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib


class DirectorDecisionPolicyContractError(ValueError):
    """Raised when an active policy contract violates its invariants."""


@dataclass(frozen=True)
class DirectorDecisionPolicyContract:
    """Immutable operational envelope around a governed active policy.

    The contract makes the policy's decision relationship, applicability basis,
    evidence provenance, and governance authority explicit. It remains advisory:
    the contract never forces the Director to choose the candidate decision.
    """

    contract_id: str
    version: int
    proposal_id: str
    validation_id: str
    knowledge_id: str
    baseline_decision: str
    candidate_decision: str
    observed_direction: str
    comparability_basis: tuple[str, ...]
    directive: str
    proposed_change: str
    evidence_summary: str
    authorized_by: str
    state: str = "active"
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.contract_id.strip():
            raise DirectorDecisionPolicyContractError("policy contract requires an id")
        if self.version < 1:
            raise DirectorDecisionPolicyContractError("policy contract version must be positive")
        for field_name, value in (
            ("proposal_id", self.proposal_id),
            ("validation_id", self.validation_id),
            ("knowledge_id", self.knowledge_id),
            ("baseline_decision", self.baseline_decision),
            ("candidate_decision", self.candidate_decision),
            ("authorized_by", self.authorized_by),
            ("proposed_change", self.proposed_change),
            ("evidence_summary", self.evidence_summary),
        ):
            if not value.strip():
                raise DirectorDecisionPolicyContractError(
                    f"{field_name} cannot be empty"
                )
        if self.baseline_decision not in {"complete", "continue", "blocked"}:
            raise DirectorDecisionPolicyContractError("invalid baseline decision")
        if self.candidate_decision not in {"complete", "continue", "blocked"}:
            raise DirectorDecisionPolicyContractError("invalid candidate decision")
        if self.observed_direction not in {"better", "worse", "same"}:
            raise DirectorDecisionPolicyContractError("invalid observed direction")
        if self.directive not in {"consider_candidate", "avoid_candidate", "no_change"}:
            raise DirectorDecisionPolicyContractError("invalid policy directive")
        if not self.comparability_basis:
            raise DirectorDecisionPolicyContractError(
                "policy contract requires a comparability basis"
            )
        normalized_basis = tuple(sorted({item.strip() for item in self.comparability_basis if item.strip()}))
        if normalized_basis != self.comparability_basis:
            raise DirectorDecisionPolicyContractError(
                "comparability basis must be non-empty, unique, and normalized"
            )
        if self.state != "active":
            raise DirectorDecisionPolicyContractError(
                "only active policies may create an operational contract"
            )
        if not self.created_at:
            object.__setattr__(
                self,
                "created_at",
                datetime.now(timezone.utc).isoformat(),
            )

    @classmethod
    def from_proposal(
        cls,
        *,
        proposal_id: str,
        validation_id: str,
        knowledge_id: str,
        alternative: str,
        chosen_decision: str,
        observed_direction: str,
        comparability_basis: tuple[str, ...],
        proposed_change: str,
        evidence_summary: str,
        authorized_by: str,
        version: int = 1,
        created_at: str | None = None,
    ) -> "DirectorDecisionPolicyContract":
        directive = {
            "better": "consider_candidate",
            "worse": "avoid_candidate",
            "same": "no_change",
        }[observed_direction]
        normalized_basis = tuple(sorted({item.strip() for item in comparability_basis if item.strip()}))
        contract_id = "dpc-" + hashlib.sha256(
            (
                f"{proposal_id}|{validation_id}|{version}|"
                f"{alternative}|{chosen_decision}|{observed_direction}|"
                f"{'|'.join(normalized_basis)}"
            ).encode("utf-8")
        ).hexdigest()[:32]
        return cls(
            contract_id=contract_id,
            version=version,
            proposal_id=proposal_id,
            validation_id=validation_id,
            knowledge_id=knowledge_id,
            baseline_decision=chosen_decision,
            candidate_decision=alternative,
            observed_direction=observed_direction,
            comparability_basis=normalized_basis,
            directive=directive,
            proposed_change=proposed_change.strip(),
            evidence_summary=evidence_summary.strip(),
            authorized_by=authorized_by.strip(),
            created_at=created_at or "",
        )

    def applicability(self, current_comparability_basis: tuple[str, ...]) -> bool:
        """Return whether the current context satisfies the exact governed basis."""
        current = tuple(sorted({item.strip() for item in current_comparability_basis if item.strip()}))
        return current == self.comparability_basis

    def advisory_record(self) -> dict[str, object]:
        """Serialize the contract fields consumed by the Director runtime."""
        return {
            "contract_id": self.contract_id,
            "version": self.version,
            "proposal_id": self.proposal_id,
            "validation_id": self.validation_id,
            "knowledge_id": self.knowledge_id,
            "baseline_decision": self.baseline_decision,
            "candidate_decision": self.candidate_decision,
            "observed_direction": self.observed_direction,
            "comparability_basis": list(self.comparability_basis),
            "directive": self.directive,
            "proposed_change": self.proposed_change,
            "evidence_summary": self.evidence_summary,
            "authorized_by": self.authorized_by,
            "state": self.state,
            "created_at": self.created_at,
            "advisory_only": True,
            "must_re_evaluate_current_evidence": True,
        }


__all__ = [
    "DirectorDecisionPolicyContract",
    "DirectorDecisionPolicyContractError",
]
