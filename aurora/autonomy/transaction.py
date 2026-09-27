"""Crash-recoverable transaction journal for capability acquisition."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import json
from pathlib import Path


class TransactionError(RuntimeError):
    """Raised when an acquisition transaction cannot be safely advanced."""


@dataclass(frozen=True)
class TransactionRecord:
    transaction_id: str
    operation_key: str
    state: str
    tool: str
    source: str
    target: str | None = None
    detail: str | None = None


class AcquisitionTransaction:
    """Append-only transaction journal with deterministic operation identity."""

    TERMINAL = frozenset({"committed", "rolled_back", "failed", "recovery_required"})
    NON_RESTARTABLE = frozenset({"running", "commit_pending"})

    def __init__(self, *, journal_dir: str | Path, operation_key: str, tool: str, source: str, target: str | None = None) -> None:
        self.journal_dir = Path(journal_dir)
        self.operation_key = operation_key
        self.tool = tool
        self.source = source
        self.target = target
        self.transaction_id = hashlib.sha256(operation_key.encode("utf-8")).hexdigest()[:24]
        self.path = self.journal_dir / f"{self.transaction_id}.jsonl"

    @contextmanager
    def lock(self):
        """Hold an inter-process lock for one acquisition operation."""
        self.journal_dir.mkdir(parents=True, exist_ok=True)
        lock_path = self.journal_dir / f"{self.transaction_id}.lock"
        with lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @property
    def latest(self) -> TransactionRecord | None:
        if not self.path.exists():
            return None
        records = self._read()
        return records[-1] if records else None

    def begin(self) -> TransactionRecord:
        latest = self.latest
        if latest and latest.state == "committed":
            return latest
        if latest and latest.state in self.NON_RESTARTABLE:
            raise TransactionError(
                f"transaction requires recovery: {self.transaction_id}"
            )
        if latest and latest.state == "recovery_required":
            raise TransactionError(
                f"transaction requires recovery: {self.transaction_id}"
            )
        return self._append("running")

    def commit_pending(self, detail: str | None = None) -> TransactionRecord:
        latest = self.latest
        if not latest or latest.state != "running":
            raise TransactionError("commit_pending requires a running transaction")
        return self._append("commit_pending", detail)

    def commit(self, detail: str | None = None) -> TransactionRecord:
        return self._append("committed", detail)

    def fail(self, detail: str) -> TransactionRecord:
        return self._append("failed", detail)

    def rollback(self, detail: str | None = None) -> TransactionRecord:
        return self._append("rolled_back", detail)

    def require_recovery(self, detail: str) -> TransactionRecord:
        return self._append("recovery_required", detail)

    def _append(self, state: str, detail: str | None = None) -> TransactionRecord:
        if state not in self.TERMINAL | {"running", "commit_pending"}:
            raise TransactionError(f"invalid transaction state: {state}")
        self.journal_dir.mkdir(parents=True, exist_ok=True)
        record = TransactionRecord(self.transaction_id, self.operation_key, state, self.tool, self.source, self.target, detail)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.__dict__, sort_keys=True) + "\n")
            handle.flush()
        return record

    def _read(self) -> list[TransactionRecord]:
        records: list[TransactionRecord] = []
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    records.append(TransactionRecord(**json.loads(line)))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
            raise TransactionError(f"transaction journal is unreadable: {self.path}") from exc
        return records


def operation_key(*, tool: str, source: str, version: str | None, artifact_sha256: str | None, target: str | None) -> str:
    payload = {"tool": tool, "source": source, "version": version, "artifact_sha256": artifact_sha256, "target": target}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))
