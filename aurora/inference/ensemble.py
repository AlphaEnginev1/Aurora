"""Critical-decision model ensemble built on Aurora's existing ModelRouter.

The ensemble is deliberately conservative:
- candidate models are selected by the existing dynamic router;
- members execute independently;
- disagreement never produces an implicit winner;
- a dynamically routed resolver model may reconcile disagreement;
- every run is durably recorded for audit and later outcome learning.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping, Sequence
from uuid import uuid4

from .models import InferenceRequest, InferenceResponse
from .router import ModelRouter, ModelRoutingError


class ModelEnsembleError(RuntimeError):
    """Raised when a critical-decision ensemble cannot operate safely."""


@dataclass(frozen=True)
class EnsembleMemberResult:
    """Outcome of one independent ensemble member execution."""

    model: str
    provider: str | None
    execution_id: str | None
    content: str
    success: bool
    error: str | None = None
    routing_score: float | None = None


@dataclass(frozen=True)
class CriticalDecisionResult:
    """Auditable result of one critical-decision ensemble."""

    ensemble_id: str
    status: str
    decision: str | None
    rationale: str
    members: tuple[EnsembleMemberResult, ...]
    candidate_models: tuple[str, ...]
    resolver_model: str | None
    agreement_ratio: float
    created_at: str


class ModelEnsembleStore:
    """SQLite ledger for critical-decision ensemble executions."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=30000")
        return db

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS model_ensemble_runs (
                    ensemble_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    decision TEXT,
                    rationale TEXT NOT NULL,
                    candidate_models_json TEXT NOT NULL,
                    members_json TEXT NOT NULL,
                    resolver_model TEXT,
                    agreement_ratio REAL NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_model_ensemble_status "
                "ON model_ensemble_runs(status)"
            )

    def record(self, result: CriticalDecisionResult) -> None:
        members = [
            {
                "model": item.model,
                "provider": item.provider,
                "execution_id": item.execution_id,
                "content": item.content,
                "success": item.success,
                "error": item.error,
                "routing_score": item.routing_score,
            }
            for item in result.members
        ]
        with self._connect() as db:
            db.execute(
                """
                INSERT OR REPLACE INTO model_ensemble_runs (
                    ensemble_id, status, decision, rationale,
                    candidate_models_json, members_json, resolver_model,
                    agreement_ratio, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.ensemble_id,
                    result.status,
                    result.decision,
                    result.rationale,
                    json.dumps(list(result.candidate_models), sort_keys=True),
                    json.dumps(members, ensure_ascii=False, sort_keys=True),
                    result.resolver_model,
                    result.agreement_ratio,
                    result.created_at,
                ),
            )

    def get(self, ensemble_id: str) -> CriticalDecisionResult | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM model_ensemble_runs WHERE ensemble_id = ?",
                (ensemble_id,),
            ).fetchone()
        if row is None:
            return None
        raw_members = json.loads(row["members_json"])
        members = tuple(EnsembleMemberResult(**item) for item in raw_members)
        return CriticalDecisionResult(
            ensemble_id=str(row["ensemble_id"]),
            status=str(row["status"]),
            decision=row["decision"],
            rationale=str(row["rationale"]),
            members=members,
            candidate_models=tuple(json.loads(row["candidate_models_json"])),
            resolver_model=row["resolver_model"],
            agreement_ratio=float(row["agreement_ratio"]),
            created_at=str(row["created_at"]),
        )


class ModelEnsemble:
    """Execute provider-neutral critical decisions using ModelRouter."""

    VALID_STATUSES = frozenset({"consensus", "resolved", "disagreement", "failed"})

    def __init__(
        self,
        router: ModelRouter,
        *,
        store: ModelEnsembleStore | None = None,
        maximum_models: int = 5,
        minimum_successful_models: int = 2,
        concurrency: int = 4,
    ) -> None:
        if maximum_models < 2:
            raise ModelEnsembleError("maximum_models must be at least 2")
        if minimum_successful_models < 2:
            raise ModelEnsembleError("minimum_successful_models must be at least 2")
        if minimum_successful_models > maximum_models:
            raise ModelEnsembleError(
                "minimum_successful_models cannot exceed maximum_models"
            )
        if concurrency < 1:
            raise ModelEnsembleError("concurrency must be at least 1")
        self.router = router
        self.store = store
        self.maximum_models = maximum_models
        self.minimum_successful_models = minimum_successful_models
        self.concurrency = concurrency

    def run_critical_decision(
        self,
        request: InferenceRequest,
        *,
        capability: str = "reasoning",
        ensemble_size: int = 3,
        preferred_models: Sequence[str] | None = None,
        resolve_disagreement: bool = True,
    ) -> CriticalDecisionResult:
        if request.model is not None:
            raise ModelEnsembleError(
                "critical-decision ensembles require dynamic model selection"
            )
        if not 2 <= ensemble_size <= self.maximum_models:
            raise ModelEnsembleError(
                f"ensemble_size must be between 2 and {self.maximum_models}"
            )

        ensemble_id = f"ensemble-{uuid4().hex}"
        created_at = _now()
        try:
            routing = self.router.route(
                request,
                capability=capability,
                preferred_models=preferred_models,
            )
        except ModelRoutingError as exc:
            result = CriticalDecisionResult(
                ensemble_id=ensemble_id,
                status="failed",
                decision=None,
                rationale=f"candidate routing failed: {exc}",
                members=(),
                candidate_models=(),
                resolver_model=None,
                agreement_ratio=0.0,
                created_at=created_at,
            )
            self._record(result)
            return result

        candidate_models = tuple(
            model_id for model_id, _ in routing.candidates[:ensemble_size]
        )
        if len(candidate_models) < self.minimum_successful_models:
            result = CriticalDecisionResult(
                ensemble_id=ensemble_id,
                status="failed",
                decision=None,
                rationale=(
                    "insufficient distinct eligible models for a critical decision"
                ),
                members=(),
                candidate_models=candidate_models,
                resolver_model=None,
                agreement_ratio=0.0,
                created_at=created_at,
            )
            self._record(result)
            return result

        members = self._execute_members(
            request,
            candidate_models=candidate_models,
            ensemble_id=ensemble_id,
        )
        successful = tuple(item for item in members if item.success)
        if len(successful) < self.minimum_successful_models:
            result = CriticalDecisionResult(
                ensemble_id=ensemble_id,
                status="failed",
                decision=None,
                rationale=(
                    "critical decision failed closed because fewer than "
                    f"{self.minimum_successful_models} models completed successfully"
                ),
                members=members,
                candidate_models=candidate_models,
                resolver_model=None,
                agreement_ratio=0.0,
                created_at=created_at,
            )
            self._record(result)
            return result

        agreement_ratio = _agreement_ratio(successful)
        if _all_same(successful):
            result = CriticalDecisionResult(
                ensemble_id=ensemble_id,
                status="consensus",
                decision=successful[0].content,
                rationale="all successful ensemble members returned the same normalized result",
                members=members,
                candidate_models=candidate_models,
                resolver_model=None,
                agreement_ratio=agreement_ratio,
                created_at=created_at,
            )
            self._record(result)
            return result

        if not resolve_disagreement:
            result = CriticalDecisionResult(
                ensemble_id=ensemble_id,
                status="disagreement",
                decision=None,
                rationale="ensemble members disagreed and automatic resolution was disabled",
                members=members,
                candidate_models=candidate_models,
                resolver_model=None,
                agreement_ratio=agreement_ratio,
                created_at=created_at,
            )
            self._record(result)
            return result

        resolver_pool = tuple(
            model_id
            for model_id, _ in routing.candidates
            if model_id not in candidate_models
        )
        if not resolver_pool:
            result = CriticalDecisionResult(
                ensemble_id=ensemble_id,
                status="disagreement",
                decision=None,
                rationale=(
                    "ensemble members disagreed and no unused eligible model was "
                    "available to resolve the disagreement"
                ),
                members=members,
                candidate_models=candidate_models,
                resolver_model=None,
                agreement_ratio=agreement_ratio,
                created_at=created_at,
            )
            self._record(result)
            return result

        resolver_request = _build_resolver_request(
            request,
            ensemble_id=ensemble_id,
            members=successful,
            allowed_models=resolver_pool,
        )
        try:
            response = self.router.generate(
                resolver_request,
                capability=capability,
                preferred_models=resolver_pool,
            )
        except Exception as exc:
            result = CriticalDecisionResult(
                ensemble_id=ensemble_id,
                status="disagreement",
                decision=None,
                rationale=f"dynamic disagreement resolver failed: {exc}",
                members=members,
                candidate_models=candidate_models,
                resolver_model=None,
                agreement_ratio=agreement_ratio,
                created_at=created_at,
            )
            self._record(result)
            return result

        decision = response.content.strip()
        if not decision:
            result = CriticalDecisionResult(
                ensemble_id=ensemble_id,
                status="disagreement",
                decision=None,
                rationale="dynamic disagreement resolver returned an empty decision",
                members=members,
                candidate_models=candidate_models,
                resolver_model=response.selected_model or response.model,
                agreement_ratio=agreement_ratio,
                created_at=created_at,
            )
            self._record(result)
            return result

        result = CriticalDecisionResult(
            ensemble_id=ensemble_id,
            status="resolved",
            decision=decision,
            rationale=(
                "ensemble disagreement was resolved by an independently routed "
                "unused model using the competing answers as review evidence"
            ),
            members=members,
            candidate_models=candidate_models,
            resolver_model=response.selected_model or response.model,
            agreement_ratio=agreement_ratio,
            created_at=created_at,
        )
        self._record(result)
        return result

    def _execute_members(
        self,
        request: InferenceRequest,
        *,
        candidate_models: tuple[str, ...],
        ensemble_id: str,
    ) -> tuple[EnsembleMemberResult, ...]:
        results: dict[str, EnsembleMemberResult] = {}
        worker_count = min(self.concurrency, len(candidate_models))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(
                    self._execute_one,
                    request,
                    model_id=model_id,
                    ensemble_id=ensemble_id,
                ): model_id
                for model_id in candidate_models
            }
            for future in as_completed(futures):
                model_id = futures[future]
                results[model_id] = future.result()
        return tuple(results[model_id] for model_id in candidate_models)

    def _execute_one(
        self,
        request: InferenceRequest,
        *,
        model_id: str,
        ensemble_id: str,
    ) -> EnsembleMemberResult:
        metadata = dict(request.metadata or {})
        metadata.update(
            {
                "ensemble_id": ensemble_id,
                "ensemble_mode": "critical_member",
                "ensemble_member_model": model_id,
            }
        )
        member_request = InferenceRequest(
            messages=list(request.messages),
            model=model_id,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            metadata=metadata,
            request_id=f"{request.request_id}:{model_id}",
        )
        try:
            response = self.router.generate(
                member_request,
                capability=str(metadata.get("capability", "reasoning")),
            )
        except Exception as exc:
            return EnsembleMemberResult(
                model=model_id,
                provider=None,
                execution_id=None,
                content="",
                success=False,
                error=str(exc)[:4000],
            )
        return EnsembleMemberResult(
            model=response.selected_model or response.model,
            provider=response.provider,
            execution_id=response.execution_id,
            content=response.content,
            success=True,
            routing_score=response.routing_score,
        )

    def _record(self, result: CriticalDecisionResult) -> None:
        if self.store is not None:
            self.store.record(result)


def _build_resolver_request(
    request: InferenceRequest,
    *,
    ensemble_id: str,
    members: Sequence[EnsembleMemberResult],
    allowed_models: Sequence[str],
) -> InferenceRequest:
    evidence = "

".join(
        f"MODEL {index}: {item.model}\nANSWER:\n{item.content}"
        for index, item in enumerate(members, start=1)
    )
    review_message = (
        "You are resolving a critical Aurora decision. Review the independent "
        "candidate answers below. Select or synthesize the best-supported answer "
        "using only the original request and these candidate outputs. Do not invent "
        "facts or claim evidence that is not present. Return only the resolved answer.\n\n"
        f"ENSEMBLE_ID: {ensemble_id}\n\n{evidence}"
    )
    metadata = dict(request.metadata or {})
    metadata.update(
        {
            "ensemble_id": ensemble_id,
            "ensemble_mode": "critical_resolver",
            "allowed_models": tuple(allowed_models),
        }
    )
    return InferenceRequest(
        messages=[*request.messages, {"role": "user", "content": review_message}],
        model=None,
        temperature=request.temperature,
        max_tokens=request.max_tokens,
        metadata=metadata,
        request_id=f"{request.request_id}:resolver",
    )


def _normalize(content: str) -> str:
    return " ".join(content.split()).strip().casefold()


def _all_same(members: Sequence[EnsembleMemberResult]) -> bool:
    normalized = {_normalize(item.content) for item in members}
    return len(normalized) == 1 and "" not in normalized


def _agreement_ratio(members: Sequence[EnsembleMemberResult]) -> float:
    normalized = [_normalize(item.content) for item in members if _normalize(item.content)]
    if not normalized:
        return 0.0
    counts: dict[str, int] = {}
    for value in normalized:
        counts[value] = counts.get(value, 0) + 1
    return max(counts.values()) / len(normalized)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "CriticalDecisionResult",
    "EnsembleMemberResult",
    "ModelEnsemble",
    "ModelEnsembleError",
    "ModelEnsembleStore",
]
