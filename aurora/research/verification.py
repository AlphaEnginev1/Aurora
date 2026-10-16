"""Independent research claim verification with fail-closed adjudication."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterable

from .models import ClaimRecord, EvidenceRecord, EvidenceState


class VerificationError(ValueError):
    """Raised when verification input violates research trust boundaries."""


@dataclass(frozen=True)
class VerificationAssessment:
    claim_id: str
    state: EvidenceState
    source_ids: tuple[str, ...]
    supporting_source_ids: tuple[str, ...]
    contradicting_source_ids: tuple[str, ...]
    rationale: str

    @property
    def independently_supported(self) -> bool:
        return self.state is EvidenceState.VERIFIED


@dataclass(frozen=True)
class EvidenceRelation:
    """Explicit comparison supplied by a verifier.

    support means independent support, contradict means incompatible evidence,
    and unclear never contributes to verification.
    """

    source_id: str
    relation: str
    evidence_text: str

    def __post_init__(self) -> None:
        if self.relation not in {"support", "contradict", "unclear"}:
            raise ValueError("relation must be support, contradict, or unclear")
        if not self.source_id.strip() or not self.evidence_text.strip():
            raise ValueError("verification relation requires source and evidence")


class ResearchVerifier:
    """Verify claims using independently identified source observations.

    Extraction confidence is never treated as truth. Verification requires at
    least two distinct supporting source IDs and no supplied contradiction.
    """

    def assess(
        self,
        claim: ClaimRecord,
        relations: Iterable[EvidenceRelation],
    ) -> VerificationAssessment:
        items = tuple(relations)
        if not items:
            return VerificationAssessment(
                claim.claim_id, EvidenceState.INSUFFICIENT, (), (), (),
                "no independent verification evidence supplied",
            )

        supporting = tuple(dict.fromkeys(
            item.source_id for item in items if item.relation == "support"
        ))
        contradicting = tuple(dict.fromkeys(
            item.source_id for item in items if item.relation == "contradict"
        ))

        if claim.source_id in supporting or claim.source_id in contradicting:
            raise VerificationError(
                "independent verification cannot reuse the candidate source"
            )

        all_sources = tuple(dict.fromkeys(item.source_id for item in items))
        if contradicting:
            return VerificationAssessment(
                claim.claim_id, EvidenceState.CONTRADICTED,
                all_sources, supporting, contradicting,
                "at least one independent source contradicts the candidate claim",
            )

        if len(supporting) < 2:
            return VerificationAssessment(
                claim.claim_id, EvidenceState.INSUFFICIENT,
                all_sources, supporting, contradicting,
                "fewer than two independent supporting sources",
            )

        return VerificationAssessment(
            claim.claim_id, EvidenceState.VERIFIED,
            all_sources, supporting, contradicting,
            "at least two independent sources support the claim and none contradict it",
        )

    def build_evidence(
        self,
        claim: ClaimRecord,
        assessment: VerificationAssessment,
    ) -> tuple[EvidenceRecord, ...]:
        if assessment.claim_id != claim.claim_id:
            raise VerificationError("assessment does not belong to claim")

        records = []
        for source_id in assessment.source_ids:
            relation = (
                "contradicts" if source_id in assessment.contradicting_source_ids
                else "supports" if source_id in assessment.supporting_source_ids
                else "unclear"
            )
            state = (
                EvidenceState.CONTRADICTED
                if relation == "contradicts"
                else EvidenceState.VERIFIED
                if assessment.state is EvidenceState.VERIFIED and relation == "supports"
                else EvidenceState.INSUFFICIENT
            )
            evidence_id = "ev-" + hashlib.sha256(
                f"{claim.claim_id}|{source_id}|{relation}".encode("utf-8")
            ).hexdigest()[:32]
            records.append(EvidenceRecord(
                evidence_id=evidence_id,
                source_id=source_id,
                claim=claim.claim,
                state=state,
                verification_notes=assessment.rationale,
                project_id=claim.project_id,
            ))
        return tuple(records)


__all__ = [
    "EvidenceRelation",
    "ResearchVerifier",
    "VerificationAssessment",
    "VerificationError",
]
