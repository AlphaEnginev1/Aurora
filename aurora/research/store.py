"""Durable SQLite storage for research provenance and evidence."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .models import EvidenceRecord, EvidenceState, ResearchFinding, SourceRecord


class ResearchStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS sources (
                    source_id TEXT PRIMARY KEY,
                    url TEXT NOT NULL,
                    retrieved_at TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    status_code INTEGER NOT NULL,
                    content_type TEXT,
                    title TEXT
                );
                CREATE TABLE IF NOT EXISTS evidence (
                    evidence_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    claim TEXT NOT NULL,
                    state TEXT NOT NULL,
                    verification_notes TEXT NOT NULL,
                    verified_at TEXT,
                    FOREIGN KEY(source_id) REFERENCES sources(source_id)
                );
                CREATE TABLE IF NOT EXISTS findings (
                    finding_id TEXT PRIMARY KEY,
                    claim TEXT NOT NULL,
                    evidence_ids TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    state TEXT NOT NULL
                );
                """
            )

    def record_source(self, source: SourceRecord) -> None:
        with self._connect() as db:
            db.execute(
                """
                INSERT OR REPLACE INTO sources
                (source_id, url, retrieved_at, content_sha256, status_code, content_type, title)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source.source_id,
                    source.url,
                    source.retrieved_at,
                    source.content_sha256,
                    source.status_code,
                    source.content_type,
                    source.title,
                ),
            )

    def record_evidence(self, evidence: EvidenceRecord) -> None:
        with self._connect() as db:
            db.execute(
                """
                INSERT OR REPLACE INTO evidence
                (evidence_id, source_id, claim, state, verification_notes, verified_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence.evidence_id,
                    evidence.source_id,
                    evidence.claim,
                    evidence.state.value,
                    evidence.verification_notes,
                    evidence.verified_at,
                ),
            )

    def record_finding(self, finding: ResearchFinding) -> None:
        import json

        with self._connect() as db:
            db.execute(
                """
                INSERT OR REPLACE INTO findings
                (finding_id, claim, evidence_ids, confidence, state)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    finding.finding_id,
                    finding.claim,
                    json.dumps(finding.evidence_ids),
                    finding.confidence,
                    finding.state.value,
                ),
            )

    def evidence_for_source(self, source_id: str) -> tuple[EvidenceRecord, ...]:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT evidence_id, source_id, claim, state, verification_notes, verified_at
                FROM evidence WHERE source_id = ? ORDER BY evidence_id
                """,
                (source_id,),
            ).fetchall()
        return tuple(
            EvidenceRecord(
                evidence_id=row["evidence_id"],
                source_id=row["source_id"],
                claim=row["claim"],
                state=EvidenceState(row["state"]),
                verification_notes=row["verification_notes"],
                verified_at=row["verified_at"],
            )
            for row in rows
        )
