"""Governed research retrieval orchestration."""
from __future__ import annotations
import hashlib
from datetime import datetime, timezone
from uuid import uuid4
from aurora.autonomy.internet import InternetGateway, InternetPolicyError, InternetResponse
from .models import RedirectHop, ResearchRequest, ResearchReport, SourceRecord
from .discovery import ResearchDiscoveryProvider
from .store import ResearchStore

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

    def empty_report(self,request:ResearchRequest)->ResearchReport:
        return ResearchReport(request_id=request.request_id,findings=(),source_ids=())
