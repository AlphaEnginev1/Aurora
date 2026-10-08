"""Autonomous, rate-limited model discovery refresh orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import Mapping, Sequence

from .discovery import DiscoveredModelCandidate, ModelDiscoveryService
from .models import ModelSpec
from .provider_health import ProviderHealthStore
from .reserve_pool import DynamicReservePool
from .router import ModelRouter


@dataclass(frozen=True)
class DiscoveryProviderRefreshReport:
    provider_id: str
    discovered_count: int
    activated_count: int
    health_eligible_count: int
    health_blocked_count: int
    candidates: tuple[DiscoveredModelCandidate, ...]


@dataclass(frozen=True)
class AutonomousDiscoveryRefreshReport:
    refreshed: bool
    providers: tuple[DiscoveryProviderRefreshReport, ...]
    activated_models: tuple[ModelSpec, ...]

    @property
    def activated_count(self) -> int:
        return len(self.activated_models)


class AutonomousDiscoveryRefreshService:
    """Connect durable discovery, governance, health and the dynamic reserve view.

    Discovery is advisory and rate-limited. Durable candidates remain the source of
    truth for restart recovery; operational health is consulted before activation;
    DynamicReservePool remains the single reserve-ranking layer.
    """

    def __init__(
        self,
        *,
        discovery: ModelDiscoveryService,
        router: ModelRouter,
        health_store: ProviderHealthStore | None = None,
        provider_ids: Sequence[str] | None = None,
        interval_seconds: float = 900.0,
        activation_limit: int = 500,
    ) -> None:
        if interval_seconds < 0:
            raise ValueError("interval_seconds cannot be negative")
        if activation_limit < 1:
            raise ValueError("activation_limit must be at least 1")
        self.discovery = discovery
        self.router = router
        self.health_store = health_store or router.health_store
        self.provider_ids = tuple(provider_ids) if provider_ids is not None else None
        self.interval_seconds = float(interval_seconds)
        self.activation_limit = int(activation_limit)
        self._last_run = 0.0

    def refresh_if_due(self, *, force: bool = False) -> AutonomousDiscoveryRefreshReport:
        now = monotonic()
        if (
            not force
            and self._last_run
            and now - self._last_run < self.interval_seconds
        ):
            return AutonomousDiscoveryRefreshReport(False, (), ())
        self._last_run = now

        results = self.discovery.refresh_all(
            provider_ids=self.provider_ids,
            min_interval_seconds=self.interval_seconds,
            force=force,
        )
        reports: list[DiscoveryProviderRefreshReport] = []
        activated: list[ModelSpec] = []

        for provider_id, candidates in results.items():
            health_eligible = 0
            health_blocked = 0
            for candidate in candidates:
                if self.health_store is None or self.health_store.is_eligible(
                    candidate.provider_id, candidate.model.id
                ):
                    health_eligible += 1
                else:
                    health_blocked += 1

            models = self.discovery.activate_eligible_discovered(
                provider_id=provider_id,
                cost_policy=getattr(self.router, "cost_policy", "free_only"),
                health_store=self.health_store,
                limit=self.activation_limit,
            )
            activated.extend(models)
            reports.append(
                DiscoveryProviderRefreshReport(
                    provider_id=provider_id,
                    discovered_count=len(candidates),
                    activated_count=len(models),
                    health_eligible_count=health_eligible,
                    health_blocked_count=health_blocked,
                    candidates=tuple(candidates),
                )
            )

        # ModelRegistry is authoritative for routing, so no parallel model list is
        # maintained here. The reserve pool reads the same router/health state.
        return AutonomousDiscoveryRefreshReport(
            refreshed=True,
            providers=tuple(reports),
            activated_models=tuple(activated),
        )

    def build_reserve_pool(
        self,
        request,
        *,
        capability: str | None = None,
        exclude: Sequence[tuple[str, str]] = (),
        max_candidates: int = 500,
    ):
        """Return the governed reserve view after the latest durable refresh."""
        pool = DynamicReservePool(self.router, max_candidates=max_candidates)
        return pool.build(
            request,
            capability=capability,
            exclude=exclude,
        )


__all__ = [
    "AutonomousDiscoveryRefreshReport",
    "AutonomousDiscoveryRefreshService",
    "DiscoveryProviderRefreshReport",
]
