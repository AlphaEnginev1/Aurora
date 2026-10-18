"""Governed model discovery and staging for Aurora inference."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping, Protocol, Sequence

from .models import ModelSpec
from .registry import ModelRegistry, ModelRegistryError
from .providers.registry import ProviderRegistry, ProviderRegistryError


class ModelDiscoveryError(RuntimeError):
    """Raised when model discovery or governed staging fails."""


class ModelDiscoveryProvider(Protocol):
    """Provider-specific model catalog discovery contract."""

    def discover(self) -> Sequence["DiscoveredModelCandidate"]:
        ...


@dataclass(frozen=True)
class DiscoveredModelCandidate:
    """A discovered model that is not active in Aurora until promoted."""

    candidate_id: str
    provider_id: str
    model: ModelSpec
    metadata: Mapping[str, Any]
    discovered_at: str
    state: str = "discovered"


@dataclass(frozen=True)
class ModelPromotionValidation:
    """Explicit validation gates required before activating a candidate."""

    policy_verified: bool
    security_verified: bool
    health_verified: bool
    reason: str = ""


class ModelCandidateStore:
    """SQLite-backed durable staging store for discovered model candidates."""

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
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS model_candidates (
                    candidate_id TEXT PRIMARY KEY,
                    provider_id TEXT NOT NULL,
                    model_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    discovered_at TEXT NOT NULL,
                    state TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_model_candidates_provider
                    ON model_candidates(provider_id, discovered_at);

                CREATE INDEX IF NOT EXISTS idx_model_candidates_state
                    ON model_candidates(state, updated_at);

                CREATE TABLE IF NOT EXISTS model_discovery_refresh (
                    provider_id TEXT PRIMARY KEY,
                    last_started_at TEXT NOT NULL,
                    last_completed_at TEXT,
                    last_success INTEGER NOT NULL DEFAULT 0,
                    staged_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT
                );
                """
            )

    def upsert(self, candidate: DiscoveredModelCandidate) -> None:
        if not candidate.candidate_id.strip():
            raise ModelDiscoveryError("candidate_id is empty")
        if not candidate.provider_id.strip():
            raise ModelDiscoveryError("candidate provider_id is empty")
        if candidate.state not in {"discovered", "synced", "rejected"}:
            raise ModelDiscoveryError(
                f"invalid model candidate state: {candidate.state}"
            )
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO model_candidates (
                    candidate_id, provider_id, model_json, metadata_json,
                    discovered_at, state, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(candidate_id) DO UPDATE SET
                    provider_id=excluded.provider_id,
                    model_json=excluded.model_json,
                    metadata_json=excluded.metadata_json,
                    discovered_at=excluded.discovered_at,
                    state=CASE
                        WHEN model_candidates.state = 'synced'
                             AND excluded.state = 'discovered'
                        THEN 'synced'
                        ELSE excluded.state
                    END,
                    updated_at=excluded.updated_at
                """,
                (
                    candidate.candidate_id,
                    candidate.provider_id,
                    json.dumps(asdict(candidate.model), sort_keys=True),
                    json.dumps(dict(candidate.metadata), sort_keys=True, default=str),
                    candidate.discovered_at,
                    candidate.state,
                    _now(),
                ),
            )

    def get(self, candidate_id: str) -> DiscoveredModelCandidate | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM model_candidates WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
        return _row_to_candidate(row) if row is not None else None

    def list(
        self,
        *,
        provider_id: str | None = None,
        state: str | None = None,
        limit: int = 100,
    ) -> tuple[DiscoveredModelCandidate, ...]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        query = "SELECT * FROM model_candidates WHERE 1=1"
        params: list[object] = []
        if provider_id is not None:
            query += " AND provider_id = ?"
            params.append(provider_id)
        if state is not None:
            query += " AND state = ?"
            params.append(state)
        query += " ORDER BY discovered_at DESC, candidate_id LIMIT ?"
        params.append(limit)
        with self._connect() as db:
            rows = db.execute(query, tuple(params)).fetchall()
        return tuple(_row_to_candidate(row) for row in rows)

    def set_state(self, candidate_id: str, state: str) -> None:
        if state not in {"discovered", "synced", "rejected"}:
            raise ModelDiscoveryError(f"invalid model candidate state: {state}")
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE model_candidates SET state = ?, updated_at = ? "
                "WHERE candidate_id = ?",
                (state, _now(), candidate_id),
            )
        if cursor.rowcount != 1:
            raise ModelDiscoveryError(
                f"unknown model candidate: {candidate_id}"
            )


class ModelDiscoveryService:
    """Discover provider catalogs and stage candidates without bypassing governance."""

    def __init__(
        self,
        provider_registry: ProviderRegistry,
        model_registry: ModelRegistry,
        candidate_store: ModelCandidateStore,
    ) -> None:
        self.provider_registry = provider_registry
        self.model_registry = model_registry
        self.candidate_store = candidate_store

    def refresh_all(
        self,
        *,
        provider_ids: Sequence[str] | None = None,
        min_interval_seconds: float = 900.0,
        force: bool = False,
    ) -> dict[str, tuple[DiscoveredModelCandidate, ...]]:
        """Refresh eligible provider catalogs without promoting candidates."""
        if min_interval_seconds < 0:
            raise ValueError("min_interval_seconds cannot be negative")
        ids = tuple(provider_ids) if provider_ids is not None else tuple(
            str(item["provider_id"])
            for item in self.provider_registry.catalog
            if bool(item["enabled"]) and bool(item["discovery_supported"])
        )
        result: dict[str, tuple[DiscoveredModelCandidate, ...]] = {}
        for provider_id in ids:
            provider_id = str(provider_id).strip()
            if not provider_id:
                continue
            with self.candidate_store._connect() as db:
                row = db.execute(
                    "SELECT last_completed_at FROM model_discovery_refresh WHERE provider_id=?",
                    (provider_id,),
                ).fetchone()
                previous = _parse_datetime(row["last_completed_at"]) if row and row["last_completed_at"] else None
                if (
                    not force
                    and previous is not None
                    and (datetime.now(timezone.utc) - previous).total_seconds() < min_interval_seconds
                ):
                    continue
                db.execute(
                    """INSERT INTO model_discovery_refresh(provider_id,last_started_at,last_completed_at,last_success,staged_count,last_error)
                       VALUES(?,?,NULL,0,0,NULL)
                       ON CONFLICT(provider_id) DO UPDATE SET
                       last_started_at=excluded.last_started_at,last_success=0,last_error=NULL""",
                    (provider_id, _now()),
                )
            try:
                staged = self.discover_and_stage(provider_id)
            except Exception as exc:
                result[provider_id] = ()
                with self.candidate_store._connect() as db:
                    db.execute(
                        "UPDATE model_discovery_refresh SET last_completed_at=?,last_success=0,last_error=? WHERE provider_id=?",
                        (_now(), str(exc)[:2000], provider_id),
                    )
            else:
                result[provider_id] = tuple(staged)
                with self.candidate_store._connect() as db:
                    db.execute(
                        "UPDATE model_discovery_refresh SET last_completed_at=?,last_success=1,staged_count=?,last_error=NULL WHERE provider_id=?",
                        (_now(), len(staged), provider_id),
                    )
        return result

    def discover_and_stage(
        self,
        provider_id: str,
    ) -> tuple[DiscoveredModelCandidate, ...]:
        try:
            provider = self.provider_registry.create_discovery(provider_id)
        except ProviderRegistryError as exc:
            raise ModelDiscoveryError(str(exc)) from exc

        try:
            discovered = tuple(provider.discover())
        except Exception as exc:
            raise ModelDiscoveryError(
                f"model discovery failed for provider {provider_id!r}"
            ) from exc

        staged: list[DiscoveredModelCandidate] = []
        for candidate in discovered:
            if candidate.provider_id != provider_id:
                raise ModelDiscoveryError(
                    "discovered candidate provider identity does not match "
                    f"requested provider {provider_id!r}"
                )
            if candidate.model.provider != provider_id:
                raise ModelDiscoveryError(
                    f"candidate {candidate.model.id!r} references provider "
                    f"{candidate.model.provider!r}, expected {provider_id!r}"
                )
            inactive_model = _inactive_model(candidate.model)
            staged_candidate = DiscoveredModelCandidate(
                candidate_id=candidate.candidate_id,
                provider_id=candidate.provider_id,
                model=inactive_model,
                metadata=dict(candidate.metadata),
                discovered_at=candidate.discovered_at or _now(),
                state="discovered",
            )
            self.candidate_store.upsert(staged_candidate)
            staged.append(staged_candidate)
        return tuple(staged)

    def activate_free_discovered(
        self,
        *,
        provider_id: str,
        limit: int = 500,
    ) -> tuple[ModelSpec, ...]:
        """Activate every policy-approved free model discovered from a trusted catalog."""
        if limit < 1:
            raise ValueError("limit must be at least 1")
        from .free_policy import is_discovered_free_model

        activated: list[ModelSpec] = []
        # synced means governance already approved the candidate. It is durable
        # approval, not a guarantee that the current in-memory registry still contains
        # the model, because the registry is rebuilt on every process start.
        # Rehydrate both newly discovered and previously synced candidates.
        candidates = (
            self.candidate_store.list(
                provider_id=provider_id, state="discovered", limit=limit
            )
            + self.candidate_store.list(
                provider_id=provider_id, state="synced", limit=limit
            )
        )
        seen_ids: set[str] = set()
        for candidate in candidates:
            if candidate.candidate_id in seen_ids:
                continue
            seen_ids.add(candidate.candidate_id)
            model = candidate.model
            metadata = dict(candidate.metadata)
            # NVIDIA's static free-policy entries are metadata only. In
            # free_endpoint mode, only the trusted discovery adapter's explicit
            # authoritative catalog receipt can authorize activation.
            catalog_free = metadata.get("free_endpoint_catalog") is True
            if not is_discovered_free_model(
                provider_id, model.id, discovered_free_endpoint=catalog_free
            ):
                continue
            approved = ModelSpec(
                id=model.id,
                provider=model.provider,
                endpoint=model.endpoint,
                capabilities=model.capabilities,
                context_window=model.context_window,
                enabled=True,
                specialties=model.specialties,
                quality_score=model.quality_score,
                latency_score=model.latency_score,
                cost_score=1.0,
                healthy=True,
                available=True,
                pricing_tier="free",
            )
            try:
                self.model_registry.register(approved)
            except ModelRegistryError as exc:
                if "duplicate model registration" not in str(exc):
                    raise ModelDiscoveryError(str(exc)) from exc
                self.model_registry.replace(approved)
            self.candidate_store.set_state(candidate.candidate_id, "synced")
            activated.append(approved)
        return tuple(activated)

    def activate_eligible_discovered(
        self,
        *,
        provider_id: str,
        cost_policy: str = "free_only",
        health_store: "ProviderHealthStore | None" = None,
        limit: int = 500,
    ) -> tuple[ModelSpec, ...]:
        """Activate governed candidates that satisfy current policy and health."""
        if limit < 1:
            raise ValueError("limit must be at least 1")
        normalized_policy = str(cost_policy or "free_only").strip().lower()
        if normalized_policy not in {"free_only", "all"}:
            raise ModelDiscoveryError(f"unsupported discovery cost policy: {cost_policy}")
        from .free_policy import is_discovered_free_model

        candidates = (
            self.candidate_store.list(provider_id=provider_id, state="discovered", limit=limit)
            + self.candidate_store.list(provider_id=provider_id, state="synced", limit=limit)
        )
        activated: list[ModelSpec] = []
        seen: set[str] = set()
        for candidate in candidates:
            if candidate.candidate_id in seen:
                continue
            seen.add(candidate.candidate_id)
            model = candidate.model
            metadata = dict(candidate.metadata)
            # Never let static model metadata or /models pricing fields stand in
            # for the official NVIDIA Free Endpoint catalog. Other providers are
            # checked against their explicit central free-model policy.
            catalog_free = metadata.get("free_endpoint_catalog") is True
            if normalized_policy == "free_only" and not is_discovered_free_model(
                provider_id, model.id, discovered_free_endpoint=catalog_free
            ):
                continue
            if health_store is not None and not health_store.is_eligible(
                model.provider, model.id
            ):
                continue
            pricing_tier = (
                "free"
                if normalized_policy == "free_only"
                else (model.pricing_tier if model.pricing_tier in {"free", "paid"} else "unknown")
            )
            approved = ModelSpec(
                id=model.id,
                provider=model.provider,
                endpoint=model.endpoint,
                capabilities=model.capabilities,
                context_window=model.context_window,
                enabled=True,
                specialties=model.specialties,
                quality_score=model.quality_score,
                latency_score=model.latency_score,
                cost_score=(1.0 if pricing_tier == "free" else model.cost_score),
                healthy=True,
                available=True,
                pricing_tier=pricing_tier,
            )
            try:
                self.model_registry.register(approved)
            except ModelRegistryError as exc:
                if "duplicate model registration" not in str(exc):
                    raise ModelDiscoveryError(str(exc)) from exc
                self.model_registry.replace(approved)
            self.candidate_store.set_state(candidate.candidate_id, "synced")
            activated.append(approved)
        return tuple(activated)

    def promote(
        self,
        candidate_id: str,
        approved_model: ModelSpec,
        validation: ModelPromotionValidation,
    ) -> ModelSpec:
        candidate = self.candidate_store.get(candidate_id)
        if candidate is None:
            raise ModelDiscoveryError(f"unknown model candidate: {candidate_id}")
        if candidate.state != "discovered":
            raise ModelDiscoveryError(
                f"model candidate is not promotable from state={candidate.state!r}"
            )
        if not (
            validation.policy_verified
            and validation.security_verified
            and validation.health_verified
        ):
            raise ModelDiscoveryError(
                "model candidate failed promotion gates: "
                "policy, security and health must all be verified"
            )
        if approved_model.id != candidate.model.id:
            raise ModelDiscoveryError(
                "approved model id does not match discovered candidate"
            )
        if approved_model.provider != candidate.provider_id:
            raise ModelDiscoveryError(
                "approved model provider does not match discovered candidate"
            )
        if not approved_model.enabled or not approved_model.healthy or not approved_model.available:
            raise ModelDiscoveryError(
                "approved model must be enabled, healthy and available"
            )
        try:
            self.model_registry.register(approved_model)
        except ModelRegistryError as exc:
            raise ModelDiscoveryError(str(exc)) from exc
        self.candidate_store.set_state(candidate_id, "synced")
        return approved_model


def candidate_id(provider_id: str, model_id: str) -> str:
    payload = f"{provider_id.strip()}:{model_id.strip()}"
    return "model-candidate-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _inactive_model(model: ModelSpec) -> ModelSpec:
    return ModelSpec(
        id=model.id,
        provider=model.provider,
        endpoint=model.endpoint,
        capabilities=model.capabilities,
        context_window=model.context_window,
        enabled=False,
        specialties=model.specialties,
        quality_score=model.quality_score,
        latency_score=model.latency_score,
        cost_score=model.cost_score,
        healthy=False,
        available=False,
        pricing_tier=model.pricing_tier,
    )


def _row_to_candidate(row: sqlite3.Row) -> DiscoveredModelCandidate:
    raw_model = json.loads(row["model_json"])
    return DiscoveredModelCandidate(
        candidate_id=str(row["candidate_id"]),
        provider_id=str(row["provider_id"]),
        model=ModelSpec(
            id=raw_model["id"],
            provider=raw_model["provider"],
            endpoint=raw_model["endpoint"],
            capabilities=tuple(raw_model.get("capabilities", ())),
            context_window=raw_model.get("context_window"),
            enabled=bool(raw_model.get("enabled", False)),
            specialties=tuple(raw_model.get("specialties", ())),
            quality_score=float(raw_model.get("quality_score", 0.5)),
            latency_score=float(raw_model.get("latency_score", 0.5)),
            cost_score=float(raw_model.get("cost_score", 0.5)),
            healthy=bool(raw_model.get("healthy", False)),
            available=bool(raw_model.get("available", False)),
            pricing_tier=str(raw_model.get("pricing_tier", "unknown")),
        ),
        metadata=json.loads(row["metadata_json"]),
        discovered_at=str(row["discovered_at"]),
        state=str(row["state"]),
    )


def _parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "DiscoveredModelCandidate",
    "ModelCandidateStore",
    "ModelDiscoveryError",
    "ModelDiscoveryProvider",
    "ModelDiscoveryService",
    "ModelPromotionValidation",
    "candidate_id",
]
