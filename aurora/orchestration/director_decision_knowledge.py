"""Governed promotion of replicated Director comparative evidence into empirical knowledge."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from typing import Iterable

from .director_decision_comparison_store import (
    DirectorDecisionComparisonStore,
    DirectorDecisionReplicationSummary,
)


class DirectorDecisionKnowledgeError(ValueError):
    """Raised when comparative evidence crosses an unsafe promotion boundary."""


@dataclass(frozen=True)
class DirectorDecisionKnowledge:
    """Promoted empirical knowledge without causal or policy authority."""

    knowledge_id: str
    alternative: str
    chosen_decision: str
    comparability_basis: tuple[str, ...]
    observed_direction: str
    decision_events: int
    observations: int
    sources: tuple[str, ...]
    claim: str
    state: str = "promoted_empirical"
    created_at: str = ""

    def __post_init__(self) -> None:
        if self.state != "promoted_empirical":
            raise DirectorDecisionKnowledgeError(
                "promoted knowledge must remain empirical"
            )
        if not self.alternative.strip() or not self.chosen_decision.strip():
            raise DirectorDecisionKnowledgeError(
                "promoted knowledge requires a decision and alternative"
            )
        if not self.comparability_basis:
            raise DirectorDecisionKnowledgeError(
                "promoted knowledge requires a comparability basis"
            )
        if self.observed_direction not in {"better", "worse", "same"}:
            raise DirectorDecisionKnowledgeError(
                "promoted knowledge requires a determinate observed direction"
            )
        if self.decision_events < 2 or self.observations < 2:
            raise DirectorDecisionKnowledgeError(
                "promoted knowledge requires replicated observations"
            )
        if not self.sources:
            raise DirectorDecisionKnowledgeError(
                "promoted knowledge requires evidence sources"
            )
        if not self.claim.strip():
            raise DirectorDecisionKnowledgeError(
                "promoted knowledge requires a claim"
            )
        if not self.created_at:
            object.__setattr__(
                self,
                "created_at",
                datetime.now(timezone.utc).isoformat(),
            )


@dataclass(frozen=True)
class DirectorDecisionKnowledgePromotion:
    """Result of a conservative knowledge-promotion gate."""

    promoted: bool
    knowledge: DirectorDecisionKnowledge | None
    reason: str


class DirectorDecisionKnowledgePromoter:
    """Promote only replicated, directionally consistent observations."""

    def __init__(
        self,
        comparison_store: DirectorDecisionComparisonStore,
        *,
        min_decision_events: int = 2,
        min_observations: int = 2,
    ) -> None:
        if min_decision_events < 2:
            raise DirectorDecisionKnowledgeError(
                "min_decision_events must be at least 2"
            )
        if min_observations < 2:
            raise DirectorDecisionKnowledgeError(
                "min_observations must be at least 2"
            )
        self.comparison_store = comparison_store
        self.min_decision_events = min_decision_events
        self.min_observations = min_observations

    def promote(
        self,
        summary: DirectorDecisionReplicationSummary,
        *,
        created_at: str | None = None,
    ) -> DirectorDecisionKnowledgePromotion:
        if summary.decision_events < self.min_decision_events:
            return DirectorDecisionKnowledgePromotion(
                promoted=False,
                knowledge=None,
                reason="insufficient_decision_replication",
            )
        if summary.observations < self.min_observations:
            return DirectorDecisionKnowledgePromotion(
                promoted=False,
                knowledge=None,
                reason="insufficient_observation_replication",
            )
        if summary.consistent_direction not in {"better", "worse", "same"}:
            return DirectorDecisionKnowledgePromotion(
                promoted=False,
                knowledge=None,
                reason="direction_not_consistent",
            )

        basis = tuple(summary.comparability_basis)
        rows = list(
            self.comparison_store.list_for_cohort(
                alternative=summary.alternative,
                chosen_decision=summary.chosen_decision,
                comparability_basis=basis,
            )
        )
        if len(rows) != summary.observations:
            return DirectorDecisionKnowledgePromotion(
                promoted=False,
                knowledge=None,
                reason="summary_snapshot_is_stale",
            )
        if len({item.decision_event_id for item in rows}) != summary.decision_events:
            return DirectorDecisionKnowledgePromotion(
                promoted=False,
                knowledge=None,
                reason="summary_snapshot_is_stale",
            )

        if len({item.decision_event_id for item in rows}) < self.min_decision_events:
            return DirectorDecisionKnowledgePromotion(
                promoted=False,
                knowledge=None,
                reason="replication_rows_do_not_meet_threshold",
            )
        if any(item.causal_attribution != "unsupported" for item in rows):
            raise DirectorDecisionKnowledgeError(
                "causal attribution must remain unsupported during promotion"
            )
        if any(item.observed_direction == "undetermined" for item in rows):
            return DirectorDecisionKnowledgePromotion(
                promoted=False,
                knowledge=None,
                reason="undetermined_observation_present",
            )
        if {item.observed_direction for item in rows} != {summary.consistent_direction}:
            return DirectorDecisionKnowledgePromotion(
                promoted=False,
                knowledge=None,
                reason="summary_does_not_match_observations",
            )

        knowledge_id = "dkn-" + hashlib.sha256(
            (
                f"{summary.alternative}|{summary.chosen_decision}|"
                f"{'|'.join(basis)}|{summary.consistent_direction}"
            ).encode("utf-8")
        ).hexdigest()[:32]
        claim = (
            "Repeated comparable observations show "
            f"alternative '{summary.alternative}' with observed direction "
            f"'{summary.consistent_direction}' relative to chosen decision "
            f"'{summary.chosen_decision}' across {summary.decision_events} "
            f"decision events and {summary.observations} observations. "
            "This is empirical observational knowledge only; it does not "
            "establish causality or authorize an automatic policy rule."
        )
        knowledge = DirectorDecisionKnowledge(
            knowledge_id=knowledge_id,
            alternative=summary.alternative,
            chosen_decision=summary.chosen_decision,
            comparability_basis=basis,
            observed_direction=summary.consistent_direction,
            decision_events=summary.decision_events,
            observations=summary.observations,
            sources=summary.sources,
            claim=claim,
            created_at=created_at or "",
        )
        return DirectorDecisionKnowledgePromotion(
            promoted=True,
            knowledge=knowledge,
            reason="replicated_consistent_observation",
        )


__all__ = [
    "DirectorDecisionKnowledge",
    "DirectorDecisionKnowledgeError",
    "DirectorDecisionKnowledgePromotion",
    "DirectorDecisionKnowledgePromoter",
]
