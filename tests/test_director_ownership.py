from datetime import datetime, timezone
from pathlib import Path

import pytest

from aurora.orchestration.director_ownership import (
    DirectorOwnershipRejected,
    DirectorOwnershipStore,
)


def test_acquire_renew_takeover_and_fencing(tmp_path: Path):
    store = DirectorOwnershipStore(tmp_path / "ownership.db")

    a = store.acquire(
        "mission-1",
        "director-A",
        lease_seconds=30,
        now="2026-01-01T00:00:00+00:00",
    )
    assert a.owner_id == "director-A"
    assert a.fencing_epoch == 1

    with pytest.raises(DirectorOwnershipRejected):
        store.acquire(
            "mission-1",
            "director-B",
            lease_seconds=30,
            now="2026-01-01T00:00:10+00:00",
        )

    renewed = store.renew(
        "mission-1",
        "director-A",
        a.fencing_epoch,
        lease_seconds=30,
        now="2026-01-01T00:00:20+00:00",
    )
    assert renewed.fencing_epoch == 1

    b = store.acquire(
        "mission-1",
        "director-B",
        lease_seconds=30,
        now="2026-01-01T00:00:51+00:00",
    )
    assert b.owner_id == "director-B"
    assert b.fencing_epoch == 2
    assert b.fencing_epoch > a.fencing_epoch

    with pytest.raises(DirectorOwnershipRejected):
        store.require_owner(
            "mission-1",
            "director-A",
            1,
            now="2026-01-01T00:00:52+00:00",
        )

    current = store.require_owner(
        "mission-1",
        "director-B",
        2,
        now="2026-01-01T00:00:52+00:00",
    )
    assert current.owner_id == "director-B"


def test_persistence_survives_new_store_instance(tmp_path: Path):
    path = tmp_path / "ownership.db"
    first = DirectorOwnershipStore(path)
    ownership = first.acquire(
        "mission-2",
        "director-A",
        lease_seconds=30,
        now="2026-01-01T00:00:00+00:00",
    )

    restarted = DirectorOwnershipStore(path)
    loaded = restarted.get("mission-2")

    assert loaded == ownership


def test_stale_owner_cannot_release_after_takeover(tmp_path: Path):
    store = DirectorOwnershipStore(tmp_path / "ownership.db")
    a = store.acquire(
        "mission-3",
        "director-A",
        lease_seconds=5,
        now="2026-01-01T00:00:00+00:00",
    )
    b = store.acquire(
        "mission-3",
        "director-B",
        lease_seconds=30,
        now="2026-01-01T00:00:10+00:00",
    )

    with pytest.raises(DirectorOwnershipRejected):
        store.release("mission-3", "director-A", a.fencing_epoch, now="2026-01-01T00:00:11+00:00")

    assert store.require_owner(
        "mission-3",
        "director-B",
        b.fencing_epoch,
        now="2026-01-01T00:00:11+00:00",
    ).owner_id == "director-B"


def test_invalid_input_rejected(tmp_path: Path):
    store = DirectorOwnershipStore(tmp_path / "ownership.db")
    with pytest.raises(Exception):
        store.acquire("", "director-A")
    with pytest.raises(Exception):
        store.acquire("mission", "")
    with pytest.raises(Exception):
        store.acquire("mission", "director-A", lease_seconds=0)
