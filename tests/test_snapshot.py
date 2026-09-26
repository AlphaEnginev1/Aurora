import pytest

from aurora.autonomy.snapshot import FilesystemSnapshot, SnapshotError


def test_staged_commit_replaces_managed_tree(tmp_path):
    target = tmp_path / "runtime"
    target.mkdir()
    (target / "old.txt").write_text("old", encoding="utf-8")
    manager = FilesystemSnapshot(
        target=target, snapshot_root=tmp_path / ".snapshots"
    )
    snapshot = manager.create()
    staged = manager.stage_directory(snapshot)
    (staged / "new.txt").write_text("new", encoding="utf-8")

    manager.commit(snapshot, staged)

    assert not (target / "old.txt").exists()
    assert (target / "new.txt").read_text(encoding="utf-8") == "new"


def test_rollback_restores_original_tree(tmp_path):
    target = tmp_path / "runtime"
    target.mkdir()
    (target / "old.txt").write_text("old", encoding="utf-8")
    manager = FilesystemSnapshot(
        target=target, snapshot_root=tmp_path / ".snapshots"
    )
    snapshot = manager.create()
    staged = manager.stage_directory(snapshot)
    (staged / "new.txt").write_text("new", encoding="utf-8")
    manager.commit(snapshot, staged)

    manager.rollback(snapshot)

    assert (target / "old.txt").read_text(encoding="utf-8") == "old"
    assert not (target / "new.txt").exists()


def test_snapshot_rejects_nested_storage(tmp_path):
    target = tmp_path / "runtime"
    target.mkdir()
    with pytest.raises(SnapshotError, match="must not be inside"):
        FilesystemSnapshot(target=target, snapshot_root=target / ".snapshots")
