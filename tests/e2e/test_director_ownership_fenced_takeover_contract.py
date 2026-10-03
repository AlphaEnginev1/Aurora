from dataclasses import dataclass


class OwnershipRejected(RuntimeError):
    pass


@dataclass(frozen=True)
class Ownership:
    mission_id: str
    owner_id: str
    fencing_epoch: int
    lease_until: int


class ContractOwnershipAuthority:
    """Test-only executable specification for Increment 11 ownership semantics."""

    def __init__(self):
        self._records = {}
        self.events = []

    def acquire(self, mission_id, owner_id, now, lease_seconds):
        current = self._records.get(mission_id)
        if current is not None and current.lease_until > now:
            self.events.append(("director_ownership_rejected", mission_id, owner_id, current.fencing_epoch))
            raise OwnershipRejected("mission ownership is currently held")
        epoch = 1 if current is None else current.fencing_epoch + 1
        ownership = Ownership(
            mission_id=mission_id,
            owner_id=owner_id,
            fencing_epoch=epoch,
            lease_until=now + lease_seconds,
        )
        self._records[mission_id] = ownership
        self.events.append(
            ("director_ownership_takeover" if current is not None else "director_ownership_acquired",
             mission_id, owner_id, epoch)
        )
        return ownership

    def renew(self, mission_id, owner_id, fencing_epoch, now, lease_seconds):
        current = self._current(mission_id)
        if (
            current.owner_id != owner_id
            or current.fencing_epoch != fencing_epoch
            or current.lease_until <= now
        ):
            raise OwnershipRejected("renewal rejected by ownership fence")
        updated = Ownership(
            mission_id=current.mission_id,
            owner_id=current.owner_id,
            fencing_epoch=current.fencing_epoch,
            lease_until=now + lease_seconds,
        )
        self._records[mission_id] = updated
        self.events.append(("director_ownership_renewed", mission_id, owner_id, fencing_epoch))
        return updated

    def require_owner(self, mission_id, owner_id, fencing_epoch, now):
        current = self._current(mission_id)
        if (
            current.owner_id != owner_id
            or current.fencing_epoch != fencing_epoch
            or current.lease_until <= now
        ):
            self.events.append(("director_ownership_fence_rejected", mission_id, owner_id, fencing_epoch))
            raise OwnershipRejected("stale or expired Director ownership")
        return current

    def _current(self, mission_id):
        current = self._records.get(mission_id)
        if current is None:
            raise OwnershipRejected("mission has no Director owner")
        return current


def test_director_ownership_fenced_takeover_contract():
    authority = ContractOwnershipAuthority()
    mission_id = "mission-handoff-contract"

    owner_a = authority.acquire(
        mission_id=mission_id,
        owner_id="director-A",
        now=100,
        lease_seconds=30,
    )
    assert owner_a.fencing_epoch == 1

    try:
        authority.acquire(
            mission_id=mission_id,
            owner_id="director-B",
            now=110,
            lease_seconds=30,
        )
    except OwnershipRejected:
        pass
    else:
        raise AssertionError("Director B must be rejected while A's lease is valid")

    renewed = authority.renew(
        mission_id=mission_id,
        owner_id="director-A",
        fencing_epoch=owner_a.fencing_epoch,
        now=120,
        lease_seconds=30,
    )
    assert renewed.fencing_epoch == 1

    owner_b = authority.acquire(
        mission_id=mission_id,
        owner_id="director-B",
        now=151,
        lease_seconds=30,
    )
    assert owner_b.owner_id == "director-B"
    assert owner_b.fencing_epoch == 2

    try:
        authority.require_owner(
            mission_id=mission_id,
            owner_id="director-A",
            fencing_epoch=1,
            now=152,
        )
    except OwnershipRejected:
        pass
    else:
        raise AssertionError("stale Director A must be fenced after takeover")

    current = authority.require_owner(
        mission_id=mission_id,
        owner_id="director-B",
        fencing_epoch=2,
        now=152,
    )
    assert current.owner_id == "director-B"
    assert current.fencing_epoch == 2

    event_types = [item[0] for item in authority.events]
    assert event_types == [
        "director_ownership_acquired",
        "director_ownership_rejected",
        "director_ownership_renewed",
        "director_ownership_takeover",
        "director_ownership_fence_rejected",
    ]
