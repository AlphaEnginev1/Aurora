"""Trusted knowledge artifacts and durable citation graph primitives."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum

from .models import EvidenceRecord, EvidenceState, ResearchFinding
from .store import ResearchStore


class KnowledgeError(ValueError):
    """Raised when a trusted knowledge artifact violates trust boundaries."""


class CitationRelation(StrEnum):
    DERIVED_FROM = "derived_from"
    SUPPORTED_BY = "supported_by"
    CITES = "cites"


@dataclass(frozen=True)
class KnowledgeArtifact:
    """A durable knowledge statement whose supporting evidence is fully verified."""

    artifact_id: str
    finding_id: str
    claim: str
    evidence_ids: tuple[str, ...]
    source_ids: tuple[str, ...]
    source_content_sha256: tuple[tuple[str, str], ...]
    state: EvidenceState = EvidenceState.VERIFIED
    created_at: str = ""
    project_id: str | None = None

    def __post_init__(self) -> None:
        if self.state is not EvidenceState.VERIFIED:
            raise KnowledgeError("trusted knowledge artifacts must be VERIFIED")
        if not self.claim.strip():
            raise KnowledgeError("trusted knowledge artifact requires a claim")
        if not self.evidence_ids or not self.source_ids:
            raise KnowledgeError("trusted knowledge artifact requires evidence and sources")
        if len(self.source_ids) != len(self.source_content_sha256):
            raise KnowledgeError("source provenance must cover every source")
        if self.project_id is not None and (
            not self.project_id.strip() or "\x00" in self.project_id
        ):
            raise KnowledgeError("project_id must be a non-empty identifier")
        if not self.created_at:
            object.__setattr__(self, "created_at", datetime.now(timezone.utc).isoformat())


@dataclass(frozen=True)
class CitationEdge:
    """A deterministic directed edge in the durable knowledge citation graph."""

    edge_id: str
    from_id: str
    to_id: str
    relation: CitationRelation
    artifact_id: str

    @classmethod
    def deterministic(
        cls,
        artifact_id: str,
        from_id: str,
        to_id: str,
        relation: CitationRelation,
    ) -> "CitationEdge":
        edge_id = "cedge-" + hashlib.sha256(
            f"{artifact_id}|{from_id}|{to_id}|{relation.value}".encode("utf-8")
        ).hexdigest()[:32]
        return cls(edge_id, from_id, to_id, relation, artifact_id)


class TrustedKnowledgeBuilder:
    """Build trusted artifacts only from fully verified findings/evidence."""

    def __init__(self, store: ResearchStore) -> None:
        self.store = store

    def build(
        self,
        finding: ResearchFinding,
        evidence: tuple[EvidenceRecord, ...],
    ) -> tuple[KnowledgeArtifact, tuple[CitationEdge, ...]]:
        if finding.state is not EvidenceState.VERIFIED:
            raise KnowledgeError("only VERIFIED findings can become trusted knowledge")
        if not finding.verified(evidence):
            raise KnowledgeError("all finding evidence must be VERIFIED")

        by_id = {item.evidence_id: item for item in evidence}
        selected = tuple(by_id[evidence_id] for evidence_id in finding.evidence_ids)

        if not all(item.state is EvidenceState.VERIFIED for item in selected):
            raise KnowledgeError("trusted knowledge requires verified evidence")
        if any(item.claim != finding.claim for item in selected):
            raise KnowledgeError("trusted knowledge evidence claim must match the finding")

        source_ids = tuple(dict.fromkeys(item.source_id for item in selected))
        provenance: list[tuple[str, str]] = []
        for source_id in source_ids:
            source = self.store.source(source_id)
            if source is None:
                raise KnowledgeError(f"missing source provenance: {source_id}")
            provenance.append((source_id, source.content_sha256))

        artifact_id = "kart-" + hashlib.sha256(
            f"{finding.finding_id}|{finding.claim}|{'|'.join(finding.evidence_ids)}".encode("utf-8")
        ).hexdigest()[:32]
        artifact = KnowledgeArtifact(
            artifact_id=artifact_id,
            finding_id=finding.finding_id,
            claim=finding.claim,
            evidence_ids=finding.evidence_ids,
            source_ids=source_ids,
            source_content_sha256=tuple(provenance),
            project_id=finding.project_id,
        )

        edges = [
            CitationEdge.deterministic(
                artifact.artifact_id,
                artifact.artifact_id,
                finding.finding_id,
                CitationRelation.DERIVED_FROM,
            )
        ]
        for evidence_record in selected:
            edges.append(
                CitationEdge.deterministic(
                    artifact.artifact_id,
                    artifact.artifact_id,
                    evidence_record.evidence_id,
                    CitationRelation.SUPPORTED_BY,
                )
            )
            edges.append(
                CitationEdge.deterministic(
                    artifact.artifact_id,
                    evidence_record.evidence_id,
                    evidence_record.source_id,
                    CitationRelation.CITES,
                )
            )

        self.store.record_knowledge_artifact(artifact)
        for edge in edges:
            self.store.record_citation_edge(edge)
        return artifact, tuple(edges)


__all__ = [
    "CitationEdge",
    "CitationRelation",
    "KnowledgeArtifact",
    "KnowledgeError",
    "TrustedKnowledgeBuilder",
]
