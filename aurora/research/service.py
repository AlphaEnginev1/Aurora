"""Governed research retrieval orchestration."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from uuid import uuid4

from aurora.autonomy.internet import InternetGateway, InternetPolicyError

from .models import ResearchRequest, ResearchReport, SourceRecord
from .store import ResearchStore


class ResearchService:
    """Retrieve research sources only through Aurora's governed InternetGateway.

    This first phase intentionally does not infer claims from arbitrary content.
    Retrieval and provenance are durable; synthesis/verification is a separate,
    fail-closed step.
    """

    def __init__(self, gateway: InternetGateway, store: ResearchStore) -> None:
        self.gateway = gateway
        self.store = store

    def retrieve(self, request: ResearchRequest) -> tuple[SourceRecord, ...]:
        if not request.request_id.strip() or not request.objective.strip():
            raise ValueError("research request requires request_id and objective")
        if request.max_sources < 1:
            raise ValueError("max_sources must be positive")
        if len(request.required_sources) > request.max_sources:
            raise ValueError("required_sources exceeds max_sources")

        sources: list[SourceRecord] = []
        for url in request.required_sources[: request.max_sources]:
            try:
                payload = self.gateway.get(url)
            except InternetPolicyError:
                raise
            source = SourceRecord(
                source_id=f"src-{uuid4().hex}",
                url=url,
                retrieved_at=datetime.now(timezone.utc).isoformat(),
                content_sha256=hashlib.sha256(payload).hexdigest(),
                status_code=200,
            )
            self.store.record_source(source)
            sources.append(source)
        return tuple(sources)

    def empty_report(self, request: ResearchRequest) -> ResearchReport:
        return ResearchReport(
            request_id=request.request_id,
            findings=(),
            source_ids=(),
        )
