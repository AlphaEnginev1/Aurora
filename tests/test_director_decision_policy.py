from aurora.orchestration.director_decision_performance import DirectorDecisionPerformance
from aurora.orchestration.director_decision_policy import DirectorDecisionPolicy


def test_policy_guidance_preserves_observed_decision_outcomes():
    performance = (
        DirectorDecisionPerformance(
            decision="continue",
            evaluations=4,
            average_score=0.75,
            successful_evaluations=3,
            success_rate=0.75,
        ),
        DirectorDecisionPerformance(
            decision="complete",
            evaluations=2,
            average_score=1.0,
            successful_evaluations=2,
            success_rate=1.0,
        ),
    )

    guidance = DirectorDecisionPolicy.guidance(performance)

    assert guidance[0].decision == "continue"
    assert guidance[0].evaluations == 4
    assert guidance[0].success_rate == 0.75
    assert guidance[1].decision == "complete"

    prompt = DirectorDecisionPolicy.prompt(guidance)

    assert "advisory" in prompt
    assert "continue: evaluations=4" in prompt
    assert "success_rate=0.750" in prompt
    assert "complete: evaluations=2" in prompt

from aurora.orchestration.director_decision_policy_active_store import DirectorDecisionPolicyActive
from aurora.orchestration.director_decision_policy_evolution import DirectorDecisionPolicyEvolution
from aurora.orchestration.director_decision_knowledge import DirectorDecisionKnowledge


def test_active_policy_prompt_preserves_governed_guidance_boundary():
    knowledge = DirectorDecisionKnowledge(
        knowledge_id="dkn-policy-active",
        alternative="complete",
        chosen_decision="continue",
        comparability_basis=("same_objective",),
        observed_direction="better",
        decision_events=2,
        observations=2,
        sources=("independent_execution",),
        claim="Repeated empirical observation.",
        created_at="2026-09-29T20:00:00+00:00",
    )
    proposal = DirectorDecisionPolicyEvolution.propose(
        knowledge,
        proposed_change="Use complete when the current context is materially comparable.",
        created_at="2026-09-29T20:01:00+00:00",
    )
    active = DirectorDecisionPolicyActive(
        proposal_id=proposal.proposal_id,
        validation_id="dpv-active",
        authorized_by="director-governance",
        activated_at="2026-09-29T20:02:00+00:00",
    )

    prompt = DirectorDecisionPolicy.active_prompt(active, proposal)

    assert "Active Director policy guidance" in prompt
    assert "proposed_change=Use complete" in prompt
    assert "does not override current evidence" in prompt
    assert "Re-evaluate current evidence before deciding" in prompt


def test_active_policy_prompt_rejects_registry_proposal_mismatch():
    knowledge = DirectorDecisionKnowledge(
        knowledge_id="dkn-policy-active-mismatch",
        alternative="complete",
        chosen_decision="continue",
        comparability_basis=("same_objective",),
        observed_direction="better",
        decision_events=2,
        observations=2,
        sources=("independent_execution",),
        claim="Repeated empirical observation.",
        created_at="2026-09-29T20:03:00+00:00",
    )
    proposal = DirectorDecisionPolicyEvolution.propose(
        knowledge,
        proposed_change="Use complete.",
        created_at="2026-09-29T20:04:00+00:00",
    )
    other = DirectorDecisionPolicyEvolution.propose(
        DirectorDecisionKnowledge(
            knowledge_id="dkn-policy-active-other",
            alternative="blocked",
            chosen_decision="continue",
            comparability_basis=("same_objective",),
            observed_direction="same",
            decision_events=2,
            observations=2,
            sources=("independent_execution",),
            claim="Repeated empirical observation.",
            created_at="2026-09-29T20:05:00+00:00",
        ),
        proposed_change="Use blocked.",
        created_at="2026-09-29T20:06:00+00:00",
    )
    active = DirectorDecisionPolicyActive(
        proposal_id=other.proposal_id,
        validation_id="dpv-active",
        authorized_by="director-governance",
    )

    import pytest
    with pytest.raises(ValueError, match="does not match"):
        DirectorDecisionPolicy.active_prompt(active, proposal)
