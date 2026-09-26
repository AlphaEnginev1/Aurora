import pytest

from aurora.autonomy.reconciliation import FilesystemReconciler, ReconciliationError
from aurora.autonomy.snapshot import FilesystemSnapshot


def _manager(tmp_path):
    target = tmp_path / "runtime"
    target.mkdir()
    (target / "old.txt").write_text("old", encoding="utf-8")
    return target, FilesystemSnapshot(target=target, snapshot_root=tmp_path / ".snapshots")


def test_reconcile_crash_before_activation_restores_previous(tmp_path):
    target, manager = _manager(tmp_path)
    snapshot = manager.create()
    staged = manager.stage_directory(snapshot)
    (staged / "new.txt").write_text("new", encoding="utf-8")
    old_target = target.with_name(target.name + ".aurora-old")
    target.rename(old_target)

    result = FilesystemReconciler(manager).reconcile(
        snapshot.snapshot_id, verify_target=lambda p: (p / "new.txt").exists()
    )

    assert result.action == "restored_previous"
    assert (target / "old.txt").read_text(encoding="utf-8") == "old"
    assert not (target / "new.txt").exists()


def test_reconcile_crash_after_activation_finalizes_verified_target(tmp_path):
    target, manager = _manager(tmp_path)
    snapshot = manager.create()
    staged = manager.stage_directory(snapshot)
    (staged / "new.txt").write_text("new", encoding="utf-8")
    old_target = target.with_name(target.name + ".aurora-old")
    target.rename(old_target)
    staged.rename(target)

    result = FilesystemReconciler(manager).reconcile(
        snapshot.snapshot_id, verify_target=lambda p: (p / "new.txt").exists()
    )

    assert result.action == "finalized_activation"
    assert (target / "new.txt").read_text(encoding="utf-8") == "new"
    assert not old_target.exists()


def test_reconcile_new_target_only_activates_verified_stage(tmp_path):
    target = tmp_path / "runtime"
    manager = FilesystemSnapshot(target=target, snapshot_root=tmp_path / ".snapshots")
    snapshot = manager.create()
    staged = manager.stage_directory(snapshot)
    (staged / "new.txt").write_text("new", encoding="utf-8")

    with pytest.raises(ReconciliationError, match="without a verifier"):
        FilesystemReconciler(manager).reconcile(snapshot.snapshot_id)


def test_reconcile_does_not_activate_unverified_stage(tmp_path):
    target = tmp_path / "runtime"
    manager = FilesystemSnapshot(target=target, snapshot_root=tmp_path / ".snapshots")
    snapshot = manager.create()
    staged = manager.stage_directory(snapshot)
    (staged / "new.txt").write_text("new", encoding="utf-8")

    with pytest.raises(ReconciliationError, match="failed verification"):
        FilesystemReconciler(manager).reconcile(
            snapshot.snapshot_id, verify_target=lambda _: False
        )
