"""Governed research retrieval orchestration."""
from __future__ import annotations
import hashlib
from datetime import datetime, timezone
from uuid import uuid4
from aurora.autonomy.internet import InternetGateway, InternetPolicyError, InternetResponse
from .models import ClaimRecord, RedirectHop, ResearchRequest, ResearchReport, SourceRecord
from .discovery import ResearchDiscoveryProvider
from .store import ResearchStore
from .parser import extract_claims, parse_source
from .verification import EvidenceRelation, ResearchVerifier, VerificationAssessment, VerificationError

class ResearchService:
    """Retrieve and durably retain bounded source content through InternetGateway."""
    MAX_CONTENT_BYTES = 1_048_576

    def __init__(self,gateway:InternetGateway,store:ResearchStore,discovery:ResearchDiscoveryProvider | None = None)->None:
        self.gateway=gateway; self.store=store; self.discovery=discovery

    def discover(self, request: ResearchRequest) -> tuple[str, ...]:
        """Discover candidate URLs through an explicitly configured provider."""
        if self.discovery is None:
            raise InternetPolicyError("research discovery provider is not configured")
        results = self.discovery.search(request.objective, limit=request.max_sources)
        return tuple(item.url for item in results)

    def retrieve(self,request:ResearchRequest)->tuple[SourceRecord,...]:
        if not request.request_id.strip() or not request.objective.strip():
            raise ValueError("research request requires request_id and objective")
        if request.max_sources<1:
            raise ValueError("max_sources must be positive")
        if len(request.required_sources)>request.max_sources:
            raise ValueError("required_sources exceeds max_sources")
        sources=[]
        for url in request.required_sources[:request.max_sources]:
            try:
                response = self.gateway.get_with_metadata(url)
            except AttributeError:
                payload = self.gateway.get(url)
                response = InternetResponse(
                    requested_url=url, final_url=url, status_code=200,
                    content_type=None, body=payload,
                )
            except InternetPolicyError:
                raise
            if len(response.body)>self.MAX_CONTENT_BYTES:
                raise InternetPolicyError("research source exceeds maximum content size")
            source=SourceRecord(
                source_id=f"src-{uuid4().hex}",
                url=response.requested_url,
                retrieved_at=datetime.now(timezone.utc).isoformat(),
                content_sha256=hashlib.sha256(response.body).hexdigest(),
                status_code=response.status_code,
                content_type=response.content_type,
                content_text=response.body.decode("utf-8",errors="replace"),
                final_url=response.final_url,
                redirect_chain=tuple(
                    RedirectHop(hop.source_url, hop.target_url, hop.status_code)
                    for hop in response.redirects
                ),
            )
            self.store.record_source(source)
            sources.append(source)
        return tuple(sources)

    def extract_claims(self, source: SourceRecord) -> tuple[ClaimRecord, ...]:
        """Parse one retrieved source and durably retain candidate claims."""
        parsed = parse_source(source)
        claims = extract_claims(parsed)
        for claim in claims:
            self.store.record_claim(claim)
        return claims

    def retrieve_and_extract(
        self, request: ResearchRequest
    ) -> tuple[tuple[SourceRecord, ...], tuple[ClaimRecord, ...]]:
        """Retrieve sources and extract only pending candidate claims."""
        sources = self.retrieve(request)
        claims = tuple(
            claim
            for source in sources
            for claim in self.extract_claims(source)
        )
        return sources, claims

    def verify_claim(
        self,
        claim: ClaimRecord,
        independent_sources: tuple[str, ...],
        relation_classifier,
    ) -> VerificationAssessment:
        """Retrieve independent sources, classify their relation, and persist evidence.

        The classifier is an explicit trust boundary: it must return support,
        contradict, or unclear for each independent source. No extraction score
        or single-source observation can promote a claim.
        """
        unique_urls = tuple(dict.fromkeys(url.strip() for url in independent_sources if url.strip()))
        if len(unique_urls) < 2:
            raise VerificationError("verification requires at least two independent sources")

        sources = self.retrieve(
            ResearchRequest(
                request_id=f"verify-{claim.claim_id}",
                objective=claim.claim,
                required_sources=unique_urls,
                max_sources=len(unique_urls),
            )
        )
        relations = []
        for source in sources:
            parsed = parse_source(source)
            relation = relation_classifier(claim, source, parsed.text)
            if not isinstance(relation, EvidenceRelation):
                raise VerificationError("relation classifier returned invalid evidence relation")
            if relation.source_id != source.source_id:
                raise VerificationError("relation classifier source identity mismatch")
            relations.append(relation)

        assessment = ResearchVerifier().assess(claim, relations)
        for evidence in ResearchVerifier().build_evidence(claim, assessment):
            self.store.record_evidence(evidence)
        return assessment

    def empty_report(self,request:ResearchRequest)->ResearchReport:
        return ResearchReport(request_id=request.request_id,findings=(),source_ids=())
