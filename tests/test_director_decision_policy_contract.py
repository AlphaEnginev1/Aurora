import pytest

from aurora.orchestration.director_decision_policy_contract import (
    DirectorDecisionPolicyContract,
    DirectorDecisionPolicyContractError,
)


def _contract():
    return DirectorDecisionPolicyContract.from_proposal(
        proposal_id="dpp-contract",
        validation_id="dpv-contract",
        knowledge_id="dkn-contract",
        alternative="complete",
        chosen_decision="continue",
        observed_direction="better",
        comparability_basis=("same_objective", "same_environment_class"),
        proposed_change="Consider complete in comparable contexts.",
        evidence_summary="Repeated independent observations.",
        authorized_by="director-governance",
        created_at="2026-09-29T20:30:00+00:00",
    )


def test_contract_is_deterministic_and_typed():
    first = _contract()
    second = _contract()

    assert first.contract_id == second.contract_id
    assert first.baseline_decision == "continue"
    assert first.candidate_decision == "complete"
    assert first.directive == "consider_candidate"
    assert first.advisory_record()["advisory_only"] is True


def test_contract_requires_exact_governed_comparability():
    contract = _contract()

    assert contract.applicability(("same_environment_class", "same_objective"))
    assert not contract.applicability(("same_objective",))


def test_contract_rejects_non_active_or_invalid_directive():
    with pytest.raises(DirectorDecisionPolicyContractError, match="only active"):
        DirectorDecisionPolicyContract(
            contract_id="dpc-invalid",
            version=1,
            proposal_id="dpp",
            validation_id="dpv",
            knowledge_id="dkn",
            baseline_decision="continue",
            candidate_decision="complete",
            observed_direction="better",
            comparability_basis=("same_objective",),
            directive="consider_candidate",
            proposed_change="change",
            evidence_summary="evidence",
            authorized_by="authority",
            state="proposed",
        )
    with pytest.raises(DirectorDecisionPolicyContractError, match="invalid policy directive"):
        DirectorDecisionPolicyContract(
            contract_id="dpc-invalid-directive",
            version=1,
            proposal_id="dpp",
            validation_id="dpv",
            knowledge_id="dkn",
            baseline_decision="continue",
            candidate_decision="complete",
            observed_direction="better",
            comparability_basis=("same_objective",),
            directive="force_candidate",
            proposed_change="change",
            evidence_summary="evidence",
            authorized_by="authority",
        )


def test_contract_record_is_stable_for_auditing():
    contract = _contract()
    record = contract.advisory_record()

    assert record["contract_id"] == contract.contract_id
    assert record["version"] == 1
    assert record["proposal_id"] == "dpp-contract"
    assert record["validation_id"] == "dpv-contract"
    assert record["knowledge_id"] == "dkn-contract"
    assert record["comparability_basis"] == [
        "same_environment_class",
        "same_objective",
    ]
    assert record["directive"] == "consider_candidate"
    assert record["authorized_by"] == "director-governance"
    assert record["advisory_only"] is True
    assert record["must_re_evaluate_current_evidence"] is True


def test_contract_directive_tracks_observed_direction():
    base = _contract()
    worse = DirectorDecisionPolicyContract.from_proposal(
        proposal_id=base.proposal_id,
        validation_id=base.validation_id,
        knowledge_id=base.knowledge_id,
        alternative=base.candidate_decision,
        chosen_decision=base.baseline_decision,
        observed_direction="worse",
        comparability_basis=base.comparability_basis,
        proposed_change=base.proposed_change,
        evidence_summary=base.evidence_summary,
        authorized_by=base.authorized_by,
        created_at="2026-09-29T20:31:00+00:00",
    )
    same = DirectorDecisionPolicyContract.from_proposal(
        proposal_id=base.proposal_id,
        validation_id=base.validation_id,
        knowledge_id=base.knowledge_id,
        alternative=base.candidate_decision,
        chosen_decision=base.baseline_decision,
        observed_direction="same",
        comparability_basis=base.comparability_basis,
        proposed_change=base.proposed_change,
        evidence_summary=base.evidence_summary,
        authorized_by=base.authorized_by,
        created_at="2026-09-29T20:32:00+00:00",
    )

    assert worse.directive == "avoid_candidate"
    assert same.directive == "no_change"
