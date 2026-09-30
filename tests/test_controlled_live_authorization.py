import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from aurora.production_authorization import (
    AuthorizationRecord,
    ControlledLiveAuthorization,
    LiveAuthorizationError,
)


PREREQUISITES = {
    "LIVE-RESEARCH-DISCOVERY": "VERIFIED",
    "OS-NETWORK-ENFORCEMENT": "VERIFIED",
    "LIVE-NVIDIA": "VERIFIED",
    "LIVE-GEMINI": "VERIFIED",
    "PRODUCTION-OBSERVABILITY": "VERIFIED",
    "RECOVERY-DRILLS": "VERIFIED",
}
CANONICAL_COMMIT = "d43db17e99803810fd951dd624d56978d0155f8d"


def _record(**overrides) -> AuthorizationRecord:
    now = datetime.now(timezone.utc)
    payload = dict(
        authorization_id="auth-test-001",
        approved_by="human-operator",
        issued_at=(now - timedelta(minutes=1)).isoformat(),
        expires_at=(now + timedelta(hours=1)).isoformat(),
        scope=frozenset({"director_live_execution", "live_external_research"}),
        prerequisites=PREREQUISITES,
        live_enabled=True,
        revoked=False,
        kill_switch=False,
        canonical_commit=CANONICAL_COMMIT,
    )
    payload.update(overrides)
    return AuthorizationRecord(**payload)


def test_live_authorization_denies_by_default_without_record():
    gate = ControlledLiveAuthorization(expected_prerequisites=PREREQUISITES)
    with pytest.raises(LiveAuthorizationError, match="authorization record is required"):
        gate.require(None, operation="director_live_execution", canonical_commit=CANONICAL_COMMIT)


def test_live_authorization_requires_explicit_enablement_and_approved_actor():
    gate = ControlledLiveAuthorization(expected_prerequisites=PREREQUISITES)
    with pytest.raises(LiveAuthorizationError, match="live execution is not enabled"):
        gate.require(_record(live_enabled=False), operation="director_live_execution", canonical_commit=CANONICAL_COMMIT)

    with pytest.raises(LiveAuthorizationError, match="approved_by"):
        gate.require(_record(approved_by=""), operation="director_live_execution", canonical_commit=CANONICAL_COMMIT)


def test_live_authorization_rejects_expired_future_invalidated_and_kill_switch_records():
    gate = ControlledLiveAuthorization(expected_prerequisites=PREREQUISITES)
    now = datetime.now(timezone.utc)
    with pytest.raises(LiveAuthorizationError, match="expired"):
        gate.require(
            _record(expires_at=(now - timedelta(seconds=1)).isoformat()),
            operation="director_live_execution",
            canonical_commit=CANONICAL_COMMIT,
        )
    with pytest.raises(LiveAuthorizationError, match="issued in the future"):
        gate.require(
            _record(issued_at=(now + timedelta(minutes=5)).isoformat()),
            operation="director_live_execution",
            canonical_commit=CANONICAL_COMMIT,
        )
    with pytest.raises(LiveAuthorizationError, match="revoked"):
        gate.require(
            _record(revoked=True),
            operation="director_live_execution",
            canonical_commit=CANONICAL_COMMIT,
        )
    with pytest.raises(LiveAuthorizationError, match="kill switch"):
        gate.require(
            _record(kill_switch=True),
            operation="director_live_execution",
            canonical_commit=CANONICAL_COMMIT,
        )


def test_live_authorization_requires_all_verified_prerequisites():
    incomplete = dict(PREREQUISITES)
    incomplete["LIVE-GEMINI"] = "PENDING"
    gate = ControlledLiveAuthorization(expected_prerequisites=PREREQUISITES)
    with pytest.raises(LiveAuthorizationError, match="LIVE-GEMINI"):
        gate.require(
            _record(prerequisites=incomplete),
            operation="director_live_execution",
            canonical_commit=CANONICAL_COMMIT,
        )


def test_live_authorization_requires_known_nonempty_scope():
    gate = ControlledLiveAuthorization(expected_prerequisites=PREREQUISITES)
    with pytest.raises(LiveAuthorizationError, match="scope is empty"):
        gate.require(_record(scope=frozenset()), operation="director_live_execution", canonical_commit=CANONICAL_COMMIT)
    with pytest.raises(LiveAuthorizationError, match="unknown scope"):
        gate.require(_record(scope=frozenset({"unrestricted_shell"})), operation="director_live_execution", canonical_commit=CANONICAL_COMMIT)
    with pytest.raises(LiveAuthorizationError, match="not authorized"):
        gate.require(_record(scope=frozenset({"live_external_research"})), operation="director_live_execution", canonical_commit=CANONICAL_COMMIT)


def test_live_authorization_binds_to_canonical_verified_commit():
    gate = ControlledLiveAuthorization(expected_prerequisites=PREREQUISITES, expected_canonical_commit=CANONICAL_COMMIT)
    with pytest.raises(LiveAuthorizationError, match="canonical commit"):
        gate.require(_record(canonical_commit="stale-commit"), operation="director_live_execution", canonical_commit=CANONICAL_COMMIT)


def test_live_authorization_success_returns_immutable_snapshot():
    gate = ControlledLiveAuthorization(expected_prerequisites=PREREQUISITES, expected_canonical_commit=CANONICAL_COMMIT)
    decision = gate.require(_record(), operation="director_live_execution", canonical_commit=CANONICAL_COMMIT)
    assert decision.authorized is True
    assert decision.authorization_id == "auth-test-001"
    assert decision.operation == "director_live_execution"
    assert decision.scope == frozenset({"director_live_execution", "live_external_research"})
    assert decision.canonical_commit == CANONICAL_COMMIT


def test_live_authorization_rejects_credential_material_in_record():
    gate = ControlledLiveAuthorization(expected_prerequisites=PREREQUISITES)
    with pytest.raises(LiveAuthorizationError, match="credential"):
        gate.require(
            _record(metadata={"api_key": "secret-value"}),
            operation="director_live_execution",
            canonical_commit=CANONICAL_COMMIT,
        )


def test_live_authorization_loads_human_record_from_json(tmp_path: Path):
    path = tmp_path / "live-authorization.json"
    data = {
        "authorization_id": "auth-file-001",
        "approved_by": "human-operator",
        "issued_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        "scope": ["director_live_execution"],
        "prerequisites": PREREQUISITES,
        "live_enabled": True,
        "revoked": False,
        "kill_switch": False,
        "canonical_commit": CANONICAL_COMMIT,
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    record = ControlledLiveAuthorization.from_file(path)
    assert record.authorization_id == "auth-file-001"
    assert record.scope == frozenset({"director_live_execution"})
    gate = ControlledLiveAuthorization(expected_prerequisites=PREREQUISITES, expected_canonical_commit=CANONICAL_COMMIT)
    assert gate.require(record, operation="director_live_execution", canonical_commit=CANONICAL_COMMIT).authorized


def test_live_authorization_does_not_mutate_input_record():
    gate = ControlledLiveAuthorization(expected_prerequisites=PREREQUISITES, expected_canonical_commit=CANONICAL_COMMIT)
    record = _record()
    before = record
    decision = gate.require(record, operation="director_live_execution", canonical_commit=CANONICAL_COMMIT)
    assert record is before
    assert decision.authorization_id == before.authorization_id
