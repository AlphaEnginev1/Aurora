from pathlib import Path

import pytest

from aurora.orchestration.director_decision_policy_contract import (
    DirectorDecisionPolicyContract,
)
from aurora.orchestration.director_decision_policy_contract_store import (
    DirectorDecisionPolicyContractStore,
    DirectorDecisionPolicyContractStoreError,
)


def _contract(created_at: str = "2026-09-29T20:40:00+00:00"):
    return DirectorDecisionPolicyContract.from_proposal(
        proposal_id="dpp-store",
        validation_id="dpv-store",
        knowledge_id="dkn-store",
        alternative="complete",
        chosen_decision="continue",
        observed_direction="better",
        comparability_basis=("same_objective", "same_environment_class"),
        proposed_change="Consider complete in comparable contexts.",
        evidence_summary="Repeated independent observations.",
        authorized_by="director-governance",
        created_at=created_at,
    )


def test_contract_store_persists_and_is_idempotent(tmp_path: Path):
    store = DirectorDecisionPolicyContractStore(tmp_path / "runtime.sqlite")
    contract = _contract()

    store.record(contract)
    store.record(contract)

    assert store.get(contract.contract_id) == contract
    assert store.list_all() == (contract,)


def test_contract_store_rejects_mutation_of_existing_contract_id(tmp_path: Path):
    store = DirectorDecisionPolicyContractStore(tmp_path / "runtime.sqlite")
    contract = _contract()
    store.record(contract)

    changed = DirectorDecisionPolicyContract(
        contract_id=contract.contract_id,
        version=contract.version,
        proposal_id=contract.proposal_id,
        validation_id=contract.validation_id,
        knowledge_id=contract.knowledge_id,
        baseline_decision=contract.baseline_decision,
        candidate_decision=contract.candidate_decision,
        observed_direction=contract.observed_direction,
        comparability_basis=contract.comparability_basis,
        directive=contract.directive,
        proposed_change="Different immutable text.",
        evidence_summary=contract.evidence_summary,
        authorized_by=contract.authorized_by,
        created_at=contract.created_at,
    )

    with pytest.raises(DirectorDecisionPolicyContractStoreError, match="immutable"):
        store.record(changed)
