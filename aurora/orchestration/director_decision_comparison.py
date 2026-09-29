"""Governed comparative evidence for Director decisions.

This module never infers a counterfactual outcome from a successful or failed
mission. It only records a comparative finding when an alternative has
independent execution evidence and a comparable observed outcome.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class DirectorDecisionComparison:
    decision_event_id: int
    chosen_decision: str
    alternative: str
    state: str
    evidence: tuple[str, ...]
    reason: str


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
        chosen_outcome: str | None,
        alternative_outcome: str | None,
        evidence: Iterable[str] = (),
        comparable: bool = False,
        alternative_executed: bool = False,
    ) -> DirectorDecisionComparison:
        if not alternative.strip():
            raise ValueError("alternative is empty")
        observed = tuple(dict.fromkeys(item for item in evidence if item))
        if not alternative_executed:
            return cls._result(
                decision_event_id, chosen_decision, alternative,
                "not_established", observed,
                "alternative_not_executed",
            )
        if not comparable:
            return cls._result(
                decision_event_id, chosen_decision, alternative,
                "not_established", observed,
                "evidence_not_comparable",
            )
        if chosen_outcome is None or alternative_outcome is None:
            return cls._result(
                decision_event_id, chosen_decision, alternative,
                "not_established", observed,
                "outcome_missing",
            )
        if chosen_outcome == alternative_outcome:
            return cls._result(
                decision_event_id, chosen_decision, alternative,
                "observational_difference", observed,
                "no_observed_outcome_difference",
            )
        # Different outcomes establish an observed difference only. Whether
        # the alternative was better/worse remains policy-dependent and is not
        # inferred here.
        return cls._result(
            decision_event_id, chosen_decision, alternative,
            "observational_difference", observed,
            "different_observed_outcomes_without_causal_attribution",
        )

    @staticmethod
    def _result(
        decision_event_id: int,
        chosen_decision: str,
        alternative: str,
        state: str,
        evidence: tuple[str, ...],
        reason: str,
    ) -> DirectorDecisionComparison:
        return DirectorDecisionComparison(
            decision_event_id=decision_event_id,
            chosen_decision=chosen_decision,
            alternative=alternative,
            state=state,
            evidence=evidence,
            reason=reason,
        )


__all__ = [
    "DirectorDecisionComparison",
    "DirectorDecisionComparisonEvaluator",
]
