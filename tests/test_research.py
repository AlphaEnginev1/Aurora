from pathlib import Path
import pytest
from aurora.research import EvidenceRecord, EvidenceState, ResearchFinding, ResearchRequest, ResearchService, ResearchStore
from aurora.research.models import SourceRecord
from aurora.autonomy.internet import InternetResponse

def test_verified_finding_requires_every_referenced_evidence_record():
    evidence=(EvidenceRecord("e1","s1","claim one").verify("source verified"),EvidenceRecord("e2","s1","claim two"))
    finding=ResearchFinding("f1","combined claim",evidence_ids=("e1","e2"),confidence=0.9)
    assert not finding.verified(evidence)
    assert finding.state is EvidenceState.PENDING

def test_research_store_persists_source_and_evidence(tmp_path: Path):
    store=ResearchStore(tmp_path/"research.db")
    source=SourceRecord("s1","https://example.invalid/source","2026-09-27T00:00:00+00:00","abc",200)
    store.record_source(source)
    store.record_evidence(EvidenceRecord("e1","s1","claim",state=EvidenceState.VERIFIED))
    restored=store.evidence_for_source("s1")
    assert len(restored)==1
    assert restored[0].state is EvidenceState.VERIFIED
    assert restored[0].claim=="claim"

class FakeGateway:
    def __init__(self,payload: bytes=b"evidence")->None:
        self.payload=payload; self.urls:list[str]=[]
    def get(self,url:str)->bytes:
        self.urls.append(url); return self.payload

class MetadataGateway(FakeGateway):
    def get_with_metadata(self,url:str)->InternetResponse:
        self.urls.append(url)
        return InternetResponse(
            requested_url=url, final_url="https://example.invalid/final", status_code=200,
            content_type="text/html", body=self.payload,
            redirects=(
                type("Hop",(),{"source_url":url,"target_url":"https://example.invalid/final","status_code":301})(),
            ),
        )

def test_research_records_redirect_safe_provenance_and_restores_it(tmp_path: Path):
    store=ResearchStore(tmp_path/"research.db")
    service=ResearchService(MetadataGateway(b"source-content"),store)
    source=service.retrieve(ResearchRequest("r1","investigate",("https://example.invalid/start",)))[0]
    assert source.url=="https://example.invalid/start"
    assert source.final_url=="https://example.invalid/final"
    assert source.redirect_chain[0].status_code==301
    restored=store.source(source.source_id)
    assert restored is not None
    assert restored.final_url==source.final_url
    assert restored.redirect_chain==source.redirect_chain

def test_research_service_records_content_hash_and_rejects_invalid_limits(tmp_path: Path):
    gateway=FakeGateway(b"source-content"); store=ResearchStore(tmp_path/"research.db"); service=ResearchService(gateway,store)
    sources=service.retrieve(ResearchRequest(request_id="r1",objective="investigate",required_sources=("https://example.invalid/a",)))
    assert gateway.urls==["https://example.invalid/a"]
    assert sources[0].content_sha256!=""
    assert sources[0].status_code==200
    with pytest.raises(ValueError,match="max_sources"):
        service.retrieve(ResearchRequest(request_id="r2",objective="investigate",max_sources=0))

def test_empty_report_contains_no_trusted_findings(tmp_path: Path):
    service=ResearchService(FakeGateway(),ResearchStore(tmp_path/"research.db"))
    assert service.empty_report(ResearchRequest("r1","investigate")).trusted_findings==()
