"""Staging and filesystem rollback for Aurora-managed acquisition.

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
    transaction_id: str | None = None
    transaction_id: str | None = None


class FilesystemSnapshot:
    """Manage an Aurora-owned directory with staged commit and rollback."""

    def __init__(self, *, target: str | Path, snapshot_root: str | Path) -> None:
        self.target = Path(target).resolve()
        self.snapshot_root = Path(snapshot_root).resolve()
        if self.target == self.snapshot_root or self.target in self.snapshot_root.parents:
            raise SnapshotError("snapshot root must not be inside the managed target")

    def _manifest(self, snapshot: Snapshot) -> Path:
        return snapshot.backup.parent / "manifest.json"

    def _write_manifest(self, snapshot: Snapshot, state: str) -> None:
        path = self._manifest(snapshot)
        payload = {
            "snapshot_id": snapshot.snapshot_id,
            "target": str(snapshot.target),
            "backup": str(snapshot.backup),
            "had_original": snapshot.had_original,
            "transaction_id": snapshot.transaction_id,
            "transaction_id": snapshot.transaction_id,
            "state": state,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".manifest-", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, path)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)

    def create(self, transaction_id: str | None = None) -> Snapshot:
        self.target.parent.mkdir(parents=True, exist_ok=True)
        self.snapshot_root.mkdir(parents=True, exist_ok=True)
        snapshot_id = uuid.uuid4().hex
        root = self.snapshot_root / snapshot_id
        backup = root / "previous"
        root.mkdir(parents=True, exist_ok=False)

        had_original = self.target.exists()
        if had_original:
            if self.target.is_symlink() or not self.target.is_dir():
                raise SnapshotError("managed target must be a real directory")
            shutil.copytree(self.target, backup)
        snapshot = Snapshot(
            snapshot_id,
            self.target,
            backup,
            had_original,
            transaction_id,
        )
        self._write_manifest(snapshot, "prepared")
        return snapshot

    def stage_directory(self, snapshot: Snapshot) -> Path:
        stage = snapshot.backup.parent / "staged"
        stage.mkdir(parents=True, exist_ok=False)
        self._write_manifest(snapshot, "staged")
        return stage

    def mark(self, snapshot: Snapshot, state: str) -> None:
        self._write_manifest(snapshot, state)

    def commit(self, snapshot: Snapshot, staged: str | Path) -> None:
        staged_path = Path(staged).resolve()
        if not staged_path.is_dir():
            raise SnapshotError("staged directory does not exist")
        if staged_path.parent != snapshot.backup.parent:
            raise SnapshotError("staged directory must be a snapshot sibling")

        old_target = snapshot.target.with_name(snapshot.target.name + ".aurora-old")
        self._write_manifest(snapshot, "commit_pending")
        try:
            if old_target.exists():
                shutil.rmtree(old_target)
            if snapshot.target.exists():
                self._write_manifest(snapshot, "swap_started")
                os.replace(snapshot.target, old_target)
            os.replace(staged_path, snapshot.target)
            self._write_manifest(snapshot, "activated")
            if old_target.exists():
                shutil.rmtree(old_target)
            self._write_manifest(snapshot, "committed")
        except Exception as exc:
            try:
                if snapshot.target.exists():
                    shutil.rmtree(snapshot.target)
                if old_target.exists():
                    os.replace(old_target, snapshot.target)
                elif snapshot.had_original:
                    shutil.copytree(snapshot.backup, snapshot.target)
                self._write_manifest(snapshot, "rolled_back")
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
        self._write_manifest(snapshot, "rolled_back")

    def discard(self, snapshot: Snapshot) -> None:
        shutil.rmtree(snapshot.backup.parent, ignore_errors=True)

    def recover(self, snapshot_id: str) -> Snapshot:
        root = self.snapshot_root / snapshot_id
        manifest = root / "manifest.json"
        if not manifest.is_file():
            raise SnapshotError(f"snapshot manifest is missing: {snapshot_id}")
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            snapshot = Snapshot(
                snapshot_id=str(data["snapshot_id"]),
                target=Path(data["target"]).resolve(),
                backup=Path(data["backup"]).resolve(),
                had_original=bool(data["had_original"]),
                transaction_id=(str(data["transaction_id"]) if data.get("transaction_id") is not None else None),
                transaction_id=(
                    str(data["transaction_id"])
                    if data.get("transaction_id") is not None
                    else None
                ),
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise SnapshotError(f"snapshot manifest is unreadable: {snapshot_id}") from exc
        if snapshot.snapshot_id != snapshot_id or snapshot.backup.parent != root:
            raise SnapshotError(f"snapshot manifest identity mismatch: {snapshot_id}")
        if snapshot.had_original and not snapshot.backup.is_dir():
            raise SnapshotError(f"snapshot backup is missing: {snapshot_id}")
        if not root.is_dir():
            raise SnapshotError(f"snapshot root is missing: {snapshot_id}")
        return snapshot
