from pathlib import Path

import pytest

from aurora.research.models import ClaimRecord, EvidenceState, SourceRecord
from aurora.research.service import ResearchService
from aurora.research.verification import (
    EvidenceRelation,
    ResearchVerifier,
    VerificationError,
)


def claim() -> ClaimRecord:
    return ClaimRecord(
        claim_id="clm-test",
        source_id="candidate",
        source_content_sha256="hash",
        claim="Aurora uses governed research retrieval.",
        start_offset=0,
        end_offset=39,
        extraction_confidence=0.9,
    )


def test_two_independent_sources_verify_claim():
    assessment = ResearchVerifier().assess(
        claim(),
        (
            EvidenceRelation("source-a", "support", "independent support"),
            EvidenceRelation("source-b", "support", "independent support"),
        ),
    )
    assert assessment.state is EvidenceState.VERIFIED
    assert assessment.independently_supported
    assert assessment.supporting_source_ids == ("source-a", "source-b")


def test_single_support_is_insufficient():
    assessment = ResearchVerifier().assess(
        claim(),
        (EvidenceRelation("source-a", "support", "one source"),),
    )
    assert assessment.state is EvidenceState.INSUFFICIENT
    assert not assessment.independently_supported


def test_contradiction_wins_over_support():
    assessment = ResearchVerifier().assess(
        claim(),
        (
            EvidenceRelation("source-a", "support", "support"),
            EvidenceRelation("source-b", "support", "support"),
            EvidenceRelation("source-c", "contradict", "contradiction"),
        ),
    )
    assert assessment.state is EvidenceState.CONTRADICTED
    assert assessment.contradicting_source_ids == ("source-c",)


def test_candidate_source_cannot_verify_itself():
    with pytest.raises(VerificationError, match="cannot reuse"):
        ResearchVerifier().assess(
            claim(),
            (
                EvidenceRelation("candidate", "support", "same source"),
                EvidenceRelation("source-b", "support", "other source"),
            ),
        )


def test_unclear_sources_do_not_promote_claim():
    assessment = ResearchVerifier().assess(
        claim(),
        (
            EvidenceRelation("source-a", "unclear", "unclear"),
            EvidenceRelation("source-b", "support", "one support"),
        ),
    )
    assert assessment.state is EvidenceState.INSUFFICIENT


def test_evidence_ids_are_deterministic():
    verifier = ResearchVerifier()
    assessment = verifier.assess(
        claim(),
        (
            EvidenceRelation("source-a", "support", "support"),
            EvidenceRelation("source-b", "support", "support"),
        ),
    )
    first = verifier.build_evidence(claim(), assessment)
    second = verifier.build_evidence(claim(), assessment)
    assert first == second
    assert all(item.state is EvidenceState.VERIFIED for item in first)


def test_service_requires_two_independent_urls(tmp_path: Path):
    service = ResearchService(gateway=object(), store=__import__("aurora.research.store", fromlist=["ResearchStore"]).ResearchStore(tmp_path / "research.db"))
    with pytest.raises(VerificationError, match="at least two"):
        service.verify_claim(claim(), ("https://one.invalid",), lambda *_: None)
