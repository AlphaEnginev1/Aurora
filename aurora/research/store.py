"""Durable SQLite storage for research provenance and evidence."""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path
from .models import EvidenceRecord, EvidenceState, RedirectHop, ResearchFinding, SourceRecord

class ResearchStore:
    def __init__(self, path: str | Path) -> None:
        self.path=Path(path); self.path.parent.mkdir(parents=True, exist_ok=True); self._initialize()
    def _connect(self):
        connection=sqlite3.connect(self.path); connection.row_factory=sqlite3.Row; connection.execute("PRAGMA busy_timeout=30000"); return connection
    def _initialize(self):
        with self._connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS sources (
                source_id TEXT PRIMARY KEY,url TEXT NOT NULL,retrieved_at TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,status_code INTEGER NOT NULL,content_type TEXT,title TEXT,
                content_text TEXT NOT NULL DEFAULT '',
                final_url TEXT,redirect_chain_json TEXT NOT NULL DEFAULT '[]'
            );
            CREATE TABLE IF NOT EXISTS evidence (
                evidence_id TEXT PRIMARY KEY,source_id TEXT NOT NULL,claim TEXT NOT NULL,state TEXT NOT NULL,
                verification_notes TEXT NOT NULL,verified_at TEXT,FOREIGN KEY(source_id) REFERENCES sources(source_id)
            );
            CREATE TABLE IF NOT EXISTS findings (
                finding_id TEXT PRIMARY KEY,claim TEXT NOT NULL,evidence_ids TEXT NOT NULL,
                confidence REAL NOT NULL,state TEXT NOT NULL
            );
            """)
            columns={row["name"] for row in db.execute("PRAGMA table_info(sources)").fetchall()}
            if "content_text" not in columns:
                db.execute("ALTER TABLE sources ADD COLUMN content_text TEXT NOT NULL DEFAULT ''")
            if "final_url" not in columns:
                db.execute("ALTER TABLE sources ADD COLUMN final_url TEXT")
            if "redirect_chain_json" not in columns:
                db.execute("ALTER TABLE sources ADD COLUMN redirect_chain_json TEXT NOT NULL DEFAULT '[]'")
    def record_source(self, source: SourceRecord):
        redirect_chain=json.dumps([
            {"source_url": hop.source_url, "target_url": hop.target_url, "status_code": hop.status_code}
            for hop in source.redirect_chain
        ], separators=(",", ":"))
        with self._connect() as db:
            db.execute("""
            INSERT OR REPLACE INTO sources
            (source_id,url,retrieved_at,content_sha256,status_code,content_type,title,content_text,final_url,redirect_chain_json)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,(source.source_id,source.url,source.retrieved_at,source.content_sha256,source.status_code,source.content_type,source.title,source.content_text,source.final_url,redirect_chain))
    def record_evidence(self,evidence:EvidenceRecord):
        with self._connect() as db:
            db.execute("""
            INSERT OR REPLACE INTO evidence
            (evidence_id,source_id,claim,state,verification_notes,verified_at) VALUES (?,?,?,?,?,?)
            """,(evidence.evidence_id,evidence.source_id,evidence.claim,evidence.state.value,evidence.verification_notes,evidence.verified_at))
    def record_finding(self,finding:ResearchFinding):
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO findings (finding_id,claim,evidence_ids,confidence,state) VALUES (?,?,?,?,?)",
                       (finding.finding_id,finding.claim,json.dumps(finding.evidence_ids),finding.confidence,finding.state.value))
    def evidence_for_source(self,source_id:str):
        with self._connect() as db:
            rows=db.execute("SELECT evidence_id,source_id,claim,state,verification_notes,verified_at FROM evidence WHERE source_id=? ORDER BY evidence_id",(source_id,)).fetchall()
        return tuple(EvidenceRecord(row["evidence_id"],row["source_id"],row["claim"],EvidenceState(row["state"]),row["verification_notes"],row["verified_at"]) for row in rows)

    def source(self, source_id: str) -> SourceRecord | None:
        with self._connect() as db:
            row=db.execute("""
                SELECT source_id,url,retrieved_at,content_sha256,status_code,content_type,title,
                       content_text,final_url,redirect_chain_json
                FROM sources WHERE source_id=?
            """,(source_id,)).fetchone()
        if row is None:
            return None
        hops=tuple(
            RedirectHop(item["source_url"],item["target_url"],item["status_code"])
            for item in json.loads(row["redirect_chain_json"] or "[]")
        )
        return SourceRecord(
            source_id=row["source_id"],url=row["url"],retrieved_at=row["retrieved_at"],
            content_sha256=row["content_sha256"],status_code=row["status_code"],
            content_type=row["content_type"],title=row["title"],content_text=row["content_text"],
            final_url=row["final_url"],redirect_chain=hops,
        )
