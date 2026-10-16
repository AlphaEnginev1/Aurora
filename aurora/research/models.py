"""Domain models for evidence-first research.

Research is deliberately separated into source observations, evidence records,
findings and reports so unverified claims cannot be promoted implicitly.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum

class EvidenceState(StrEnum):
    PENDING = "pending"
    VERIFIED = "verified"
    INSUFFICIENT = "insufficient"
    REJECTED = "rejected"
    CONTRADICTED = "contradicted"

@dataclass(frozen=True)
class ResearchRequest:
    request_id: str
    objective: str
    required_sources: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    max_sources: int = 10
    project_id: str | None = None

@dataclass(frozen=True)
class RedirectHop:
    source_url: str
    target_url: str
    status_code: int

@dataclass(frozen=True)
class SourceRecord:
    source_id: str
    url: str
    retrieved_at: str
    content_sha256: str
    status_code: int
    content_type: str | None = None
    title: str | None = None
    content_text: str = ""
    final_url: str | None = None
    redirect_chain: tuple[RedirectHop, ...] = ()
    project_id: str | None = None

@dataclass(frozen=True)
class ClaimRecord:
    """A candidate claim extracted from a specific immutable source snapshot."""

    claim_id: str
    source_id: str
    source_content_sha256: str
    claim: str
    start_offset: int
    end_offset: int
    extraction_confidence: float
    state: EvidenceState = EvidenceState.PENDING
    project_id: str | None = None

    def __post_init__(self) -> None:
        if self.start_offset < 0 or self.end_offset <= self.start_offset:
            raise ValueError("claim source span must be non-empty")
        if not 0.0 <= self.extraction_confidence <= 1.0:
            raise ValueError("extraction_confidence must be between 0 and 1")
        if not self.claim.strip():
            raise ValueError("claim text must be non-empty")


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_id: str
    source_id: str
    claim: str
    state: EvidenceState = EvidenceState.PENDING
    verification_notes: str = ""
    verified_at: str | None = None
    project_id: str | None = None

    def verify(self, notes: str = "") -> "EvidenceRecord":
        return EvidenceRecord(
            self.evidence_id, self.source_id, self.claim, EvidenceState.VERIFIED,
            notes, datetime.now(timezone.utc).isoformat(), self.project_id,
        )

    def reject(self, notes: str) -> "EvidenceRecord":
        return EvidenceRecord(
            self.evidence_id, self.source_id, self.claim, EvidenceState.REJECTED,
            notes, datetime.now(timezone.utc).isoformat(), self.project_id,
        )

@dataclass(frozen=True)
class ResearchFinding:
    finding_id: str
    claim: str
    evidence_ids: tuple[str, ...] = ()
    confidence: float = 0.0
    state: EvidenceState = EvidenceState.PENDING
    project_id: str | None = None

    def verified(self, evidence: tuple[EvidenceRecord, ...]) -> bool:
        if not self.evidence_ids:
            return False
        by_id = {item.evidence_id: item for item in evidence}
        for evidence_id in self.evidence_ids:
            item = by_id.get(evidence_id)
            if item is None or item.state is not EvidenceState.VERIFIED:
                return False
            if item.project_id != self.project_id:
                return False
        return True

@dataclass(frozen=True)
class ResearchReport:
    request_id: str
    findings: tuple[ResearchFinding, ...]
    source_ids: tuple[str, ...]
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    project_id: str | None = None

    @property
    def trusted_findings(self) -> tuple[ResearchFinding, ...]:
        return tuple(finding for finding in self.findings if finding.state is EvidenceState.VERIFIED)
