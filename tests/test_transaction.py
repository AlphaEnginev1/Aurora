import pytest

from aurora.autonomy.transaction import (
    AcquisitionTransaction,
    TransactionError,
    operation_key,
)


def test_operation_key_is_stable():
    a = operation_key(
        tool="demo", source="pypi", version="1.0", artifact_sha256=None, target="/x"
    )
    b = operation_key(
        tool="demo", source="pypi", version="1.0", artifact_sha256=None, target="/x"
    )
    assert a == b


def test_transaction_lifecycle_and_idempotent_commit(tmp_path):
    tx = AcquisitionTransaction(
        journal_dir=tmp_path,
        operation_key="demo-operation",
        tool="demo",
        source="pypi",
        target="/x",
    )
    first = tx.begin()
    assert first.state == "running"
    committed = tx.commit("verified")
    assert committed.state == "committed"
    assert tx.latest == committed

    reopened = AcquisitionTransaction(
        journal_dir=tmp_path,
        operation_key="demo-operation",
        tool="demo",
        source="pypi",
        target="/x",
    )
    assert reopened.begin().state == "committed"


def test_running_transaction_requires_recovery(tmp_path):
    tx = AcquisitionTransaction(
        journal_dir=tmp_path,
        operation_key="demo-operation",
        tool="demo",
        source="pypi",
    )
    tx.begin()
    with pytest.raises(TransactionError, match="requires recovery"):
        tx.begin()


def test_recovery_required_blocks_retry(tmp_path):
    tx = AcquisitionTransaction(
        journal_dir=tmp_path,
        operation_key="demo-operation",
        tool="demo",
        source="pypi",
    )
    tx.begin()
    tx.require_recovery("installer interrupted")
    with pytest.raises(TransactionError, match="requires recovery"):
        tx.begin()


def test_failed_transaction_can_be_restarted(tmp_path):
    tx = AcquisitionTransaction(
        journal_dir=tmp_path,
        operation_key="demo-operation",
        tool="demo",
        source="pypi",
    )
    tx.begin()
    tx.fail("installer returned non-zero")
    assert tx.begin().state == "running"


def test_commit_pending_requires_recovery(tmp_path):
    tx = AcquisitionTransaction(
        journal_dir=tmp_path,
        operation_key="demo-operation",
        tool="demo",
        source="pypi",
    )
    tx.begin()
    pending = tx.commit_pending("swap")
    assert pending.state == "commit_pending"
    with pytest.raises(TransactionError, match="requires recovery"):
        tx.begin()


def test_transaction_lock_serializes_parallel_holders(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Lock
    from time import sleep

    tx = AcquisitionTransaction(
        journal_dir=tmp_path,
        operation_key="parallel-operation",
        tool="demo",
        source="pypi",
    )
    guard = Lock()
    active = 0
    peak = 0

    def hold():
        nonlocal active, peak
        with tx.lock():
            with guard:
                active += 1
                peak = max(peak, active)
            sleep(0.02)
            with guard:
                active -= 1

    with ThreadPoolExecutor(max_workers=2) as pool:
        tuple(pool.map(lambda _: hold(), range(2)))

    assert peak == 1
