from pathlib import Path

import pytest

from aurora.research import (
    ClaimRecord,
    EvidenceRecord,
    EvidenceState,
    KnowledgeError,
    ResearchFinding,
    ResearchLearningError,
    ResearchLearningGate,
    ResearchStore,
    SourceRecord,
    TrustedKnowledgeBuilder,
)


def _trusted_artifact(store: ResearchStore):
    source = SourceRecord(
        source_id="src-1",
        url="https://example.test/source",
        retrieved_at="2026-09-28T00:00:00+00:00",
        content_sha256="sha-1",
        status_code=200,
        content_text="Aurora evidence.",
    )
    store.record_source(source)

    claim = ClaimRecord(
        claim_id="claim-1",
        source_id="src-1",
        source_content_sha256="sha-1",
        claim="Aurora evidence.",
        start_offset=0,
        end_offset=16,
        extraction_confidence=1.0,
    )
    evidence = EvidenceRecord(
        evidence_id="ev-1",
        source_id="src-1",
        claim=claim.claim,
        state=EvidenceState.VERIFIED,
        verification_notes="verified independently",
    )
    store.record_evidence(evidence)

    finding = ResearchFinding(
        finding_id="finding-1",
        claim=claim.claim,
        evidence_ids=(evidence.evidence_id,),
        confidence=1.0,
        state=EvidenceState.VERIFIED,
    )
    artifact, _ = TrustedKnowledgeBuilder(store).build(
        finding,
        (evidence,),
    )
    return artifact


def test_research_learning_gate_authorizes_verified_artifact(tmp_path: Path):
    store = ResearchStore(tmp_path / "research.db")
    artifact = _trusted_artifact(store)

    signal = ResearchLearningGate(store).authorize((artifact.artifact_id,))

    assert signal.state is EvidenceState.VERIFIED
    assert signal.artifact_ids == (artifact.artifact_id,)


def test_research_learning_gate_fails_closed_for_unknown_artifact(tmp_path: Path):
    store = ResearchStore(tmp_path / "research.db")

    with pytest.raises(ResearchLearningError, match="unknown knowledge artifact"):
        ResearchLearningGate(store).authorize(("missing-artifact",))


def test_research_learning_gate_rejects_artifact_without_verified_evidence(tmp_path: Path):
    store = ResearchStore(tmp_path / "research.db")
    source = SourceRecord(
        source_id="src-1",
        url="https://example.test/source",
        retrieved_at="2026-09-28T00:00:00+00:00",
        content_sha256="sha-1",
        status_code=200,
        content_text="Aurora evidence.",
    )
    store.record_source(source)

    from aurora.research.knowledge import KnowledgeArtifact

    artifact = KnowledgeArtifact(
        artifact_id="kart-unverified-evidence",
        finding_id="finding-1",
        claim="Aurora evidence.",
        evidence_ids=("ev-missing",),
        source_ids=("src-1",),
        source_content_sha256=(("src-1", "sha-1"),),
    )
    store.record_knowledge_artifact(artifact)

    with pytest.raises(
        ResearchLearningError,
        match="lacks fully verified evidence",
    ):
        ResearchLearningGate(store).authorize((artifact.artifact_id,))
