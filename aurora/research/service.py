"""Governed research retrieval orchestration."""
from __future__ import annotations
import hashlib
from datetime import datetime, timezone
from uuid import uuid4
from aurora.autonomy.internet import InternetGateway, InternetPolicyError
from .models import ResearchRequest, ResearchReport, SourceRecord
from .store import ResearchStore

class ResearchService:
    """Retrieve and durably retain bounded source content through InternetGateway."""
    MAX_CONTENT_BYTES = 1_048_576
    def __init__(self,gateway:InternetGateway,store:ResearchStore)->None:
        self.gateway=gateway; self.store=store
    def retrieve(self,request:ResearchRequest)->tuple[SourceRecord,...]:
        if not request.request_id.strip() or not request.objective.strip(): raise ValueError("research request requires request_id and objective")
        if request.max_sources<1: raise ValueError("max_sources must be positive")
        if len(request.required_sources)>request.max_sources: raise ValueError("required_sources exceeds max_sources")
        sources=[]
        for url in request.required_sources[:request.max_sources]:
            try: payload=self.gateway.get(url)
            except InternetPolicyError: raise
            if len(payload)>self.MAX_CONTENT_BYTES: raise InternetPolicyError("research source exceeds maximum content size")
            source=SourceRecord(
                source_id=f"src-{uuid4().hex}",url=url,retrieved_at=datetime.now(timezone.utc).isoformat(),
                content_sha256=hashlib.sha256(payload).hexdigest(),status_code=200,content_text=payload.decode("utf-8",errors="replace")
            )
            self.store.record_source(source); sources.append(source)
        return tuple(sources)
    def empty_report(self,request:ResearchRequest)->ResearchReport:
        return ResearchReport(request_id=request.request_id,findings=(),source_ids=())
