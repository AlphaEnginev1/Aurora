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

@dataclass(frozen=True)
class ResearchRequest:
    request_id: str
    objective: str
    required_sources: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    max_sources: int = 10

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

@dataclass(frozen=True)
class EvidenceRecord:
    evidence_id: str
    source_id: str
    claim: str
    state: EvidenceState = EvidenceState.PENDING
    verification_notes: str = ""
    verified_at: str | None = None

    def verify(self, notes: str = "") -> "EvidenceRecord":
        return EvidenceRecord(self.evidence_id,self.source_id,self.claim,EvidenceState.VERIFIED,notes,datetime.now(timezone.utc).isoformat())

    def reject(self, notes: str) -> "EvidenceRecord":
        return EvidenceRecord(self.evidence_id,self.source_id,self.claim,EvidenceState.REJECTED,notes,datetime.now(timezone.utc).isoformat())

@dataclass(frozen=True)
class ResearchFinding:
    finding_id: str
    claim: str
    evidence_ids: tuple[str, ...] = ()
    confidence: float = 0.0
    state: EvidenceState = EvidenceState.PENDING

    def verified(self, evidence: tuple[EvidenceRecord, ...]) -> bool:
        if not self.evidence_ids:
            return False
        by_id = {item.evidence_id: item for item in evidence}
        return all(by_id.get(evidence_id) is not None and by_id[evidence_id].state is EvidenceState.VERIFIED for evidence_id in self.evidence_ids)

@dataclass(frozen=True)
class ResearchReport:
    request_id: str
    findings: tuple[ResearchFinding, ...]
    source_ids: tuple[str, ...]
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def trusted_findings(self) -> tuple[ResearchFinding, ...]:
        return tuple(finding for finding in self.findings if finding.state is EvidenceState.VERIFIED)
