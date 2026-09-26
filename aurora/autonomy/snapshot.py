"""Staging and filesystem rollback for Aurora-managed acquisition targets.

This layer is intentionally limited to directories owned by Aurora. It never
pretends that a host package manager transaction is reversible. For managed
runtime directories, installation is performed in a sibling staging directory,
verified there, and committed by same-filesystem renames. A retained backup
provides deterministic rollback and crash recovery.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid


class SnapshotError(RuntimeError):
    """Raised when a filesystem snapshot cannot be safely created or restored."""


@dataclass(frozen=True)
class Snapshot:
    snapshot_id: str
    target: Path
    backup: Path
    had_original: bool


class FilesystemSnapshot:
    """Manage an Aurora-owned directory with staged commit and rollback."""

    def __init__(self, *, target: str | Path, snapshot_root: str | Path) -> None:
        self.target = Path(target).resolve()
        self.snapshot_root = Path(snapshot_root).resolve()

        # Snapshot storage must not be the target itself.
        if self.target == self.snapshot_root or self.target in self.snapshot_root.parents:
            raise SnapshotError("snapshot root must not be inside the managed target")

    def create(self) -> Snapshot:
        self.target.parent.mkdir(parents=True, exist_ok=True)
        self.snapshot_root.mkdir(parents=True, exist_ok=True)
        snapshot_id = uuid.uuid4().hex
        backup = self.snapshot_root / snapshot_id / "previous"
        backup.parent.mkdir(parents=True, exist_ok=False)

        had_original = self.target.exists()
        if had_original:
            if self.target.is_symlink() or not self.target.is_dir():
                raise SnapshotError("managed target must be a real directory")
            shutil.copytree(self.target, backup)
        return Snapshot(snapshot_id, self.target, backup, had_original)

    def stage_directory(self, snapshot: Snapshot) -> Path:
        stage = snapshot.backup.parent / "staged"
        stage.mkdir(parents=True, exist_ok=False)
        return stage

    def commit(self, snapshot: Snapshot, staged: str | Path) -> None:
        staged_path = Path(staged).resolve()
        if not staged_path.is_dir():
            raise SnapshotError("staged directory does not exist")
        if staged_path.parent != snapshot.backup.parent:
            raise SnapshotError("staged directory must be a snapshot sibling")

        old_target = snapshot.target.with_name(snapshot.target.name + ".aurora-old")
        try:
            if old_target.exists():
                shutil.rmtree(old_target)
            if snapshot.target.exists():
                os.replace(snapshot.target, old_target)
            os.replace(staged_path, snapshot.target)
            if old_target.exists():
                shutil.rmtree(old_target)
        except Exception as exc:
            try:
                if snapshot.target.exists():
                    shutil.rmtree(snapshot.target)
                if old_target.exists():
                    os.replace(old_target, snapshot.target)
                elif snapshot.had_original:
                    shutil.copytree(snapshot.backup, snapshot.target)
            except Exception as restore_exc:
                raise SnapshotError(
                    f"filesystem commit failed and automatic restore failed: {restore_exc}"
                ) from exc
            raise SnapshotError(f"filesystem commit failed: {exc}") from exc

    def rollback(self, snapshot: Snapshot) -> None:
        if self.target.exists():
            if not self.target.is_dir() or self.target.is_symlink():
                raise SnapshotError("managed target is not a real directory")
            shutil.rmtree(self.target)
        if snapshot.had_original:
            shutil.copytree(snapshot.backup, self.target)

    def discard(self, snapshot: Snapshot) -> None:
        shutil.rmtree(snapshot.backup.parent, ignore_errors=True)

    def recover(self, snapshot_id: str) -> Snapshot:
        root = self.snapshot_root / snapshot_id
        backup = root / "previous"
        if not root.is_dir() or not backup.is_dir():
            raise SnapshotError(f"snapshot is incomplete: {snapshot_id}")
        return Snapshot(snapshot_id, self.target, backup, True)
