"""Governed skill acquisition orchestration."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3
import json

from .acquisition import SkillAcquisitionRuntime, SkillCandidate, SkillGap
from .models import SkillSpec
from .registry import SkillRegistry


class SkillAcquisitionPipelineError(RuntimeError):
    """Raised when a governed acquisition stage fails."""


@dataclass(frozen=True)
class SkillAcquisitionEvidence:
    source_ids: tuple[str, ...]
    acquired_tools: tuple[str, ...]
    validation_returncode: int
    validation_stdout: str
    validation_stderr: str
    state: str


@dataclass(frozen=True)
class PreparedSkillCandidate:
    candidate: SkillCandidate
    evidence: SkillAcquisitionEvidence


class SkillAcquisitionPipeline:
    """Durable approval boundary for newly discovered specialist skills."""

    def __init__(self, runtime: SkillAcquisitionRuntime, artifact_path: str | Path | None = None) -> None:
        self.runtime = runtime
        self.artifact_path = Path(artifact_path or runtime.path.parent / "skill-acquisition-artifacts.db")
        self.artifact_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute(
                """CREATE TABLE IF NOT EXISTS candidate_artifacts (
                    candidate_id TEXT PRIMARY KEY,
                    skill_json TEXT NOT NULL,
                    source_ids_json TEXT NOT NULL,
                    acquired_tools_json TEXT NOT NULL,
                    validation_returncode INTEGER NOT NULL,
                    validation_stdout TEXT NOT NULL,
                    validation_stderr TEXT NOT NULL,
                    state TEXT NOT NULL
                )"""
            )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.artifact_path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=30000")
        return db
