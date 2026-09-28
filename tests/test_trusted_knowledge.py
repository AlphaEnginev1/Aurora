from pathlib import Path

import pytest

from aurora.research import (
    CitationRelation,
    EvidenceRecord,
    EvidenceState,
    KnowledgeError,
    ResearchFinding,
    ResearchStore,
    TrustedKnowledgeBuilder,
)


def _store_with_sources(tmp_path: Path) -> ResearchStore:
    from aurora.research.models import SourceRecord

    store = ResearchStore(tmp_path / "research.db")
    for source_id, digest in (("src-a", "sha-a"), ("src-b", "sha-b")):
        store.record_source(SourceRecord(
            source_id=source_id,
            url=f"https://example.test/{source_id}",
            retrieved_at="2026-09-28T00:00:00+00:00",
            content_sha256=digest,
            status_code=200,
            content_text="supporting source",
        ))
    return store


def _verified_evidence() -> tuple[EvidenceRecord, ...]:
    return (
        EvidenceRecord(
            evidence_id="ev-a",
            source_id="src-a",
            claim="Aurora can verify knowledge",
            state=EvidenceState.VERIFIED,
            verification_notes="independent support",
        ),
        EvidenceRecord(
            evidence_id="ev-b",
            source_id="src-b",
            claim="Aurora can verify knowledge",
            state=EvidenceState.VERIFIED,
            verification_notes="independent support",
        ),
    )


def test_builds_trusted_artifact_with_provenance_and_citation_edges(tmp_path):
    store = _store_with_sources(tmp_path)
    evidence = _verified_evidence()
    finding = ResearchFinding(
        finding_id="finding-1",
        claim="Aurora can verify knowledge",
        evidence_ids=("ev-a", "ev-b"),
        confidence=1.0,
        state=EvidenceState.VERIFIED,
    )

    artifact, edges = TrustedKnowledgeBuilder(store).build(finding, evidence)

    assert artifact.state is EvidenceState.VERIFIED
    assert artifact.source_content_sha256 == (("src-a", "sha-a"), ("src-b", "sha-b"))
    assert {edge.relation for edge in edges} == {
        CitationRelation.DERIVED_FROM,
        CitationRelation.SUPPORTED_BY,
        CitationRelation.CITES,
    }
    assert len(edges) == 5
    assert store.knowledge_artifact(artifact.artifact_id) == artifact
    assert store.citation_edges(artifact.artifact_id) == edges


def test_unverified_finding_cannot_become_trusted(tmp_path):
    store = _store_with_sources(tmp_path)
    finding = ResearchFinding(
        finding_id="finding-1",
        claim="unverified",
        evidence_ids=("ev-a", "ev-b"),
        state=EvidenceState.PENDING,
    )

    with pytest.raises(KnowledgeError):
        TrustedKnowledgeBuilder(store).build(finding, _verified_evidence())


def test_contradicted_evidence_cannot_become_trusted(tmp_path):
    store = _store_with_sources(tmp_path)
    evidence = (
        _verified_evidence()[0],
        EvidenceRecord(
            evidence_id="ev-b",
            source_id="src-b",
            claim="Aurora can verify knowledge",
            state=EvidenceState.CONTRADICTED,
            verification_notes="contradiction",
        ),
    )
    finding = ResearchFinding(
        finding_id="finding-1",
        claim="Aurora can verify knowledge",
        evidence_ids=("ev-a", "ev-b"),
        state=EvidenceState.VERIFIED,
    )

    with pytest.raises(KnowledgeError):
        TrustedKnowledgeBuilder(store).build(finding, evidence)


def test_missing_source_provenance_fails_closed(tmp_path):
    store = _store_with_sources(tmp_path)
    evidence = (
        _verified_evidence()[0],
        EvidenceRecord(
            evidence_id="ev-b",
            source_id="src-missing",
            claim="Aurora can verify knowledge",
            state=EvidenceState.VERIFIED,
        ),
    )
    finding = ResearchFinding(
        finding_id="finding-1",
        claim="Aurora can verify knowledge",
        evidence_ids=("ev-a", "ev-b"),
        state=EvidenceState.VERIFIED,
    )

    with pytest.raises(KnowledgeError):
        TrustedKnowledgeBuilder(store).build(finding, evidence)


def test_citation_edges_are_deterministic(tmp_path):
    store = _store_with_sources(tmp_path)
    finding = ResearchFinding(
        finding_id="finding-1",
        claim="Aurora can verify knowledge",
        evidence_ids=("ev-a", "ev-b"),
        state=EvidenceState.VERIFIED,
    )
    first = TrustedKnowledgeBuilder(store).build(finding, _verified_evidence())[1]
    second = TrustedKnowledgeBuilder(store).build(finding, _verified_evidence())[1]

    assert first == second
