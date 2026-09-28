"""Durable SQLite storage for research provenance, evidence and trusted knowledge."""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path
from .models import ClaimRecord, EvidenceRecord, EvidenceState, RedirectHop, ResearchFinding, SourceRecord

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
            CREATE TABLE IF NOT EXISTS claims (
                claim_id TEXT PRIMARY KEY,source_id TEXT NOT NULL,source_content_sha256 TEXT NOT NULL,
                claim TEXT NOT NULL,start_offset INTEGER NOT NULL,end_offset INTEGER NOT NULL,
                extraction_confidence REAL NOT NULL,state TEXT NOT NULL,
                FOREIGN KEY(source_id) REFERENCES sources(source_id)
            );
            CREATE TABLE IF NOT EXISTS evidence (
                evidence_id TEXT PRIMARY KEY,source_id TEXT NOT NULL,claim TEXT NOT NULL,state TEXT NOT NULL,
                verification_notes TEXT NOT NULL,verified_at TEXT,FOREIGN KEY(source_id) REFERENCES sources(source_id)
            );
            CREATE TABLE IF NOT EXISTS findings (
                finding_id TEXT PRIMARY KEY,claim TEXT NOT NULL,evidence_ids TEXT NOT NULL,
                confidence REAL NOT NULL,state TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS knowledge_artifacts (
                artifact_id TEXT PRIMARY KEY,
                finding_id TEXT NOT NULL,
                claim TEXT NOT NULL,
                evidence_ids TEXT NOT NULL,
                source_ids TEXT NOT NULL,
                source_content_sha256 TEXT NOT NULL,
                state TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS citation_edges (
                edge_id TEXT PRIMARY KEY,
                artifact_id TEXT NOT NULL,
                from_id TEXT NOT NULL,
                to_id TEXT NOT NULL,
                relation TEXT NOT NULL,
                FOREIGN KEY(artifact_id) REFERENCES knowledge_artifacts(artifact_id)
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
            """,(source.source_id,source.url,source.retrieved_at,source.content_sha256,source.status_code,
                 source.content_type,source.title,source.content_text,source.final_url,redirect_chain))

    def record_claim(self, claim: ClaimRecord):
        with self._connect() as db:
            db.execute("""
            INSERT OR REPLACE INTO claims
            (claim_id,source_id,source_content_sha256,claim,start_offset,end_offset,extraction_confidence,state)
            VALUES (?,?,?,?,?,?,?,?)
            """,(claim.claim_id,claim.source_id,claim.source_content_sha256,claim.claim,claim.start_offset,
                 claim.end_offset,claim.extraction_confidence,claim.state.value))

    def claims_for_source(self, source_id: str):
        with self._connect() as db:
            rows=db.execute("""
                SELECT claim_id,source_id,source_content_sha256,claim,start_offset,end_offset,
                       extraction_confidence,state
                FROM claims WHERE source_id=? ORDER BY start_offset,claim_id
            """,(source_id,)).fetchall()
        return tuple(
            ClaimRecord(
                row["claim_id"],row["source_id"],row["source_content_sha256"],row["claim"],
                row["start_offset"],row["end_offset"],row["extraction_confidence"],
                EvidenceState(row["state"])
            )
            for row in rows
        )

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

    def record_knowledge_artifact(self, artifact) -> None:
        with self._connect() as db:
            db.execute("""
            INSERT OR REPLACE INTO knowledge_artifacts
            (artifact_id,finding_id,claim,evidence_ids,source_ids,source_content_sha256,state,created_at)
            VALUES (?,?,?,?,?,?,?,?)
            """,(
                artifact.artifact_id, artifact.finding_id, artifact.claim,
                json.dumps(artifact.evidence_ids), json.dumps(artifact.source_ids),
                json.dumps(artifact.source_content_sha256), artifact.state.value,
                artifact.created_at,
            ))

    def record_citation_edge(self, edge) -> None:
        with self._connect() as db:
            db.execute("""
            INSERT OR REPLACE INTO citation_edges
            (edge_id,artifact_id,from_id,to_id,relation) VALUES (?,?,?,?,?)
            """,(edge.edge_id,edge.artifact_id,edge.from_id,edge.to_id,edge.relation.value))

    def knowledge_artifact(self, artifact_id: str):
        from .knowledge import KnowledgeArtifact
        with self._connect() as db:
            row=db.execute("""
                SELECT artifact_id,finding_id,claim,evidence_ids,source_ids,
                       source_content_sha256,state,created_at
                FROM knowledge_artifacts WHERE artifact_id=?
            """,(artifact_id,)).fetchone()
        if row is None:
            return None
        return KnowledgeArtifact(
            artifact_id=row["artifact_id"],
            finding_id=row["finding_id"],
            claim=row["claim"],
            evidence_ids=tuple(json.loads(row["evidence_ids"])),
            source_ids=tuple(json.loads(row["source_ids"])),
            source_content_sha256=tuple(tuple(item) for item in json.loads(row["source_content_sha256"])),
            state=EvidenceState(row["state"]),
            created_at=row["created_at"],
        )

    def citation_edges(self, artifact_id: str):
        from .knowledge import CitationEdge, CitationRelation
        with self._connect() as db:
            rows=db.execute("""
                SELECT edge_id,artifact_id,from_id,to_id,relation
                FROM citation_edges WHERE artifact_id=? ORDER BY rowid
            """,(artifact_id,)).fetchall()
        return tuple(
            CitationEdge(
                edge_id=row["edge_id"], artifact_id=row["artifact_id"],
                from_id=row["from_id"], to_id=row["to_id"],
                relation=CitationRelation(row["relation"]),
            )
            for row in rows
        )

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
