from pathlib import Path

import pytest

from aurora.research.models import EvidenceState, SourceRecord
from aurora.research.parser import SourceParsingError, extract_claims, parse_source
from aurora.research.store import ResearchStore


def _source(content: str, content_type: str = "text/plain") -> SourceRecord:
    return SourceRecord(
        source_id="s1",
        url="https://example.invalid/source",
        retrieved_at="2026-09-28T00:00:00+00:00",
        content_sha256="abc123",
        status_code=200,
        content_type=content_type,
        content_text=content,
    )


def test_html_parser_extracts_visible_text_and_ignores_script():
    parsed = parse_source(_source(
        "<html><head><title>Ignored</title><script>bad()</script></head>"
        "<body><h1>Research</h1><p>Aurora uses governed retrieval to investigate sources.</p>"
        "<p>Claims remain pending until independently verified.</p></body></html>",
        "text/html",
    ))
    assert "Aurora uses governed retrieval to investigate sources." in parsed.text
    assert "Claims remain pending until independently verified." in parsed.text
    assert "bad()" not in parsed.text
    assert "Ignored" not in parsed.text


def test_claim_extraction_is_deterministic_and_provenance_bound():
    source = _source(
        "Aurora uses governed retrieval to investigate external sources. "
        "Every extracted claim remains pending until independent verification."
    )
    first = extract_claims(parse_source(source))
    second = extract_claims(parse_source(source))
    assert first == second
    assert len(first) == 2
    assert all(claim.source_id == source.source_id for claim in first)
    assert all(claim.source_content_sha256 == source.content_sha256 for claim in first)
    assert all(claim.state is EvidenceState.PENDING for claim in first)
    assert all(0 <= claim.start_offset < claim.end_offset for claim in first)
    assert all(0.0 < claim.extraction_confidence <= 1.0 for claim in first)


def test_claim_persistence_preserves_source_hash_and_pending_state(tmp_path: Path):
    store = ResearchStore(tmp_path / "research.db")
    source = _source(
        "Aurora uses governed retrieval to investigate external sources."
    )
    store.record_source(source)
    claims = extract_claims(parse_source(source))
    for claim in claims:
        store.record_claim(claim)
    restored = store.claims_for_source(source.source_id)
    assert restored == claims
    assert restored[0].source_content_sha256 == source.content_sha256
    assert restored[0].state is EvidenceState.PENDING


def test_unsupported_content_type_fails_closed():
    with pytest.raises(SourceParsingError, match="unsupported"):
        parse_source(_source("binary", "application/pdf"))


def test_empty_source_fails_closed():
    with pytest.raises(SourceParsingError, match="no parseable text"):
        parse_source(_source("   \n\t"))


def test_claim_span_points_into_normalized_parsed_text():
    source = _source(
        "Aurora uses governed retrieval to investigate external sources. "
        "The source snapshot is preserved for later verification."
    )
    parsed = parse_source(source)
    claim = extract_claims(parsed)[0]
    assert parsed.text[claim.start_offset:claim.end_offset] == claim.claim
