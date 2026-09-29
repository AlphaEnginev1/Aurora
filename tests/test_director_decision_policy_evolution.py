from pathlib import Path

import pytest

from aurora.orchestration.director_decision_knowledge import DirectorDecisionKnowledge
from aurora.orchestration.director_decision_policy_evolution import (
    DirectorDecisionPolicyEvolution,
    DirectorDecisionPolicyEvolutionError,
)
from aurora.orchestration.director_decision_policy_proposal_store import (
    DirectorDecisionPolicyProposalStore,
)


def _knowledge() -> DirectorDecisionKnowledge:
    return DirectorDecisionKnowledge(
        knowledge_id="dkn-1",
        alternative="complete",
        chosen_decision="continue",
        comparability_basis=("same_objective", "same_environment_class"),
        observed_direction="better",
        decision_events=2,
        observations=2,
        sources=("independent_execution",),
        claim="Repeated comparable observations.",
        created_at="2026-09-29T18:00:00+00:00",
    )


def test_policy_evolution_creates_non_active_proposal():
    proposal = DirectorDecisionPolicyEvolution.propose(
        _knowledge(),
        proposed_change="Prefer complete when the same objective and environment class recur.",
        created_at="2026-09-29T18:01:00+00:00",
    )
    assert proposal.state == "proposed"
    assert proposal.knowledge_id == "dkn-1"
    assert proposal.comparability_basis == tuple(sorted(_knowledge().comparability_basis))
    assert DirectorDecisionPolicyEvolution.approval_boundary(proposal) == "explicit_policy_approval_required"


def test_policy_evolution_requires_promoted_empirical_knowledge():
    knowledge = _knowledge()
    object.__setattr__(knowledge, "state", "raw_observation")
    with pytest.raises(DirectorDecisionPolicyEvolutionError, match="promoted empirical"):
        DirectorDecisionPolicyEvolution.propose(knowledge, proposed_change="change")


def test_policy_evolution_never_creates_active_proposal():
    proposal = DirectorDecisionPolicyEvolution.propose(
        _knowledge(),
        proposed_change="Change strategy",
    )
    assert proposal.state != "active"


def test_policy_proposal_store_persists_durable_proposals(tmp_path: Path):
    proposal = DirectorDecisionPolicyEvolution.propose(
        _knowledge(),
        proposed_change="Review complete before continuing.",
        created_at="2026-09-29T18:02:00+00:00",
    )
    store = DirectorDecisionPolicyProposalStore(tmp_path / "tasks.db")
    store.record(proposal)
    store.record(proposal)
    assert store.get(proposal.proposal_id) == proposal
    assert len(store.list_all()) == 1


def test_policy_proposal_store_rejects_non_proposed_state(tmp_path: Path):
    proposal = DirectorDecisionPolicyEvolution.propose(
        _knowledge(),
        proposed_change="Review complete before continuing.",
    )
    object.__setattr__(proposal, "state", "active")
    with pytest.raises(ValueError, match="only proposed"):
        DirectorDecisionPolicyProposalStore(tmp_path / "tasks.db").record(proposal)
