"""Deterministic reconciliation for interrupted Aurora filesystem activations.

Recovery is deliberately conservative: when the activation state is ambiguous,
Aurora either verifies the live postcondition and finalizes it, or restores the
known-good previous tree. It never guesses that an unverified staged tree is safe.
"""

from __future__ import annotations

from dataclasses import dataclass
import shutil
from pathlib import Path
from typing import Callable

from .snapshot import FilesystemSnapshot, Snapshot, SnapshotError


class ReconciliationError(RuntimeError):
    """Raised when an interrupted filesystem operation cannot be reconciled safely."""


@dataclass(frozen=True)
class ReconciliationResult:
    snapshot_id: str
    action: str
    target: Path


class FilesystemReconciler:
    """Recover a retained Aurora filesystem snapshot after process interruption."""

    def __init__(self, snapshot_manager: FilesystemSnapshot) -> None:
        self.manager = snapshot_manager

    def reconcile(
        self,
        snapshot_id: str,
        *,
        verify_target: Callable[[Path], bool] | None = None,
    ) -> ReconciliationResult:
        snapshot = self.manager.recover(snapshot_id)
        root = snapshot.backup.parent
        staged = root / "staged"
        old_target = snapshot.target.with_name(snapshot.target.name + ".aurora-old")
        target_exists = snapshot.target.exists()
        old_exists = old_target.exists()
        staged_exists = staged.exists()

        verified = (
            verify_target(snapshot.target)
            if target_exists and verify_target is not None
            else False
        )

        # A fully activated target with its old sibling still present is the
        # normal crash window between activation and cleanup.
        if target_exists and old_exists:
            if verified:
                shutil.rmtree(old_target)
                if staged_exists:
                    shutil.rmtree(staged)
                self.manager.mark(snapshot, "committed")
                return ReconciliationResult(snapshot_id, "finalized_activation", snapshot.target)
            self._restore_previous(snapshot, old_target, staged)
            self.manager.mark(snapshot, "rolled_back")
            return ReconciliationResult(snapshot_id, "restored_previous", snapshot.target)

        # Crash after old target was renamed but before staged activation.
        if not target_exists and old_exists:
            osrc = old_target
            self._restore_previous(snapshot, osrc, staged)
            self.manager.mark(snapshot, "rolled_back")
            return ReconciliationResult(snapshot_id, "restored_previous", snapshot.target)

        # No original target existed. A verified staged tree can safely become
        # the live target; an unverified staged tree is never activated.
        if not target_exists and staged_exists and not old_exists:
            if verify_target is None:
                raise ReconciliationError(
                    "staged tree exists without a verifier; refusing activation"
                )
            staged_verified = verify_target(staged)
            if not staged_verified:
                raise ReconciliationError(
                    "staged tree failed verification; refusing activation"
                )
            snapshot.target.parent.mkdir(parents=True, exist_ok=True)
            staged.replace(snapshot.target)
            self.manager.mark(snapshot, "committed")
            return ReconciliationResult(snapshot_id, "activated_staged", snapshot.target)

        # A target without old/staged may already be a completed activation
        # whose journal commit was interrupted.
        if target_exists and not old_exists and not staged_exists:
            if verify_target is None or not verify_target(snapshot.target):
                raise ReconciliationError(
                    "live target cannot be verified after interrupted activation"
                )
            self.manager.mark(snapshot, "committed")
            return ReconciliationResult(snapshot_id, "finalized_existing_target", snapshot.target)

        # A snapshot with no live target but an original backup can always be
        # restored to the known-good state.
        if not target_exists and not old_exists and not staged_exists and snapshot.had_original:
            self._restore_previous(snapshot, snapshot.backup, None)
            self.manager.mark(snapshot, "rolled_back")
            return ReconciliationResult(snapshot_id, "restored_backup", snapshot.target)

        raise ReconciliationError(
            f"ambiguous filesystem state for snapshot {snapshot_id}: "
            f"target={target_exists}, old={old_exists}, staged={staged_exists}"
        )

    def _restore_previous(
        self, snapshot: Snapshot, previous: Path, staged: Path | None
    ) -> None:
        if snapshot.target.exists():
            if snapshot.target.is_symlink() or not snapshot.target.is_dir():
                raise ReconciliationError("live target is not a real directory")
            shutil.rmtree(snapshot.target)
        if staged is not None and staged.exists():
            shutil.rmtree(staged)
        if snapshot.target.exists():
            raise ReconciliationError("failed to clear live target")
        if previous.exists():
            snapshot.target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(previous, snapshot.target)
        elif snapshot.had_original:
            raise ReconciliationError("known-good previous tree is missing")


__all__ = ["FilesystemReconciler", "ReconciliationError", "ReconciliationResult"]
