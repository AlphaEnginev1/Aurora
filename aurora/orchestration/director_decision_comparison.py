"""Governed comparative evidence for Director decisions.

This module never infers a counterfactual outcome from a successful or failed
mission. It only records a comparative finding when an alternative has
independent execution evidence and a comparable observed outcome.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class DirectorDecisionComparativeEvidence:
    """Explicit evidence record for an independently observed alternative."""

    source: str
    alternative_execution_id: str
    chosen_execution_id: str
    chosen_outcome: str
    alternative_outcome: str
    comparability_basis: tuple[str, ...]
    observed_direction: str = "undetermined"
    causal_attribution: str = "unsupported"

    def __post_init__(self) -> None:
        if self.source not in {"independent_execution", "historical_comparable"}:
            raise ValueError("invalid comparative evidence source")
        if not self.alternative_execution_id.strip():
            raise ValueError("alternative_execution_id is empty")
        if not self.chosen_execution_id.strip():
            raise ValueError("chosen_execution_id is empty")
        if not self.chosen_outcome.strip() or not self.alternative_outcome.strip():
            raise ValueError("comparative outcomes must be non-empty")
        if not self.comparability_basis:
            raise ValueError("comparability_basis is required")
        if self.observed_direction not in {"better", "worse", "same", "undetermined"}:
            raise ValueError("invalid observed_direction")
        if self.causal_attribution != "unsupported":
            raise ValueError("causal attribution is not supported by this contract")


@dataclass(frozen=True)
class DirectorDecisionComparison:
    decision_event_id: int
    chosen_decision: str
    alternative: str
    state: str
    evidence: tuple[str, ...]
    reason: str
    observed_direction: str = "undetermined"
    causal_attribution: str = "unsupported"


class DirectorDecisionComparisonEvaluator:
    """Evaluate whether comparative/counterfactual evidence is established."""

    STATES = frozenset({
        "not_established",
        "observational_difference",
        "potentially_better_observed",
        "potentially_worse_observed",
    })

    @classmethod
    def evaluate(
        cls,
        *,
        decision_event_id: int,
        chosen_decision: str,
        alternative: str,
        chosen_outcome: str | None = None,
        alternative_outcome: str | None = None,
        evidence: Iterable[str] = (),
        comparable: bool = False,
        alternative_executed: bool = False,
        comparative_evidence: DirectorDecisionComparativeEvidence | None = None,
    ) -> DirectorDecisionComparison:
        if not alternative.strip():
            raise ValueError("alternative is empty")
        observed = tuple(dict.fromkeys(item for item in evidence if item))
        if comparative_evidence is None:
            if not alternative_executed:
                return cls._result(decision_event_id, chosen_decision, alternative, "not_established", observed, "alternative_not_executed")
            if not comparable:
                return cls._result(decision_event_id, chosen_decision, alternative, "not_established", observed, "evidence_not_comparable")
            if chosen_outcome is None or alternative_outcome is None:
                return cls._result(decision_event_id, chosen_decision, alternative, "not_established", observed, "outcome_missing")
            if chosen_outcome == alternative_outcome:
                return cls._result(decision_event_id, chosen_decision, alternative, "observational_difference", observed, "no_observed_outcome_difference")
            return cls._result(decision_event_id, chosen_decision, alternative, "observational_difference", observed, "different_observed_outcomes_without_causal_attribution")
        if comparative_evidence.chosen_outcome != chosen_outcome or comparative_evidence.alternative_outcome != alternative_outcome:
            raise ValueError("comparative evidence outcomes do not match evaluation inputs")
        observed = tuple(dict.fromkeys((*observed, comparative_evidence.source, *comparative_evidence.comparability_basis)))
        if comparative_evidence.observed_direction == "better":
            state, reason = "potentially_better_observed", "comparable_observed_difference_with_no_causal_attribution"
        elif comparative_evidence.observed_direction == "worse":
            state, reason = "potentially_worse_observed", "comparable_observed_difference_with_no_causal_attribution"
        elif comparative_evidence.observed_direction == "same":
            state, reason = "observational_difference", "no_observed_outcome_difference"
        else:
            state, reason = "observational_difference", "comparable_difference_direction_undetermined"
        return cls._result(
            decision_event_id, chosen_decision, alternative, state, observed, reason,
            observed_direction=comparative_evidence.observed_direction,
            causal_attribution=comparative_evidence.causal_attribution,
        )

    @staticmethod
    def _result(
        decision_event_id: int,
        chosen_decision: str,
        alternative: str,
        state: str,
        evidence: tuple[str, ...],
        reason: str,
        *,
        observed_direction: str = "undetermined",
        causal_attribution: str = "unsupported",
    ) -> DirectorDecisionComparison:
        return DirectorDecisionComparison(
            decision_event_id=decision_event_id,
            chosen_decision=chosen_decision,
            alternative=alternative,
            state=state,
            evidence=evidence,
            reason=reason,
            observed_direction=observed_direction,
            causal_attribution=causal_attribution,
        )


__all__ = [
    "DirectorDecisionComparativeEvidence",
    "DirectorDecisionComparison",
    "DirectorDecisionComparisonEvaluator",
]
