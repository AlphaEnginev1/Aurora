"""Durable provider/model availability and operational health state.

Transport/service failures affect availability; evidence-based semantic quality
learning remains owned by ModelPerformanceStore.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Literal
from .errors import ProviderConfigurationError, ProviderHTTPError, ProviderTimeoutError, ProviderTransportError

AvailabilityState = Literal["healthy","degraded","cooldown","unavailable","probing"]

@dataclass(frozen=True)
class ProviderHealthSnapshot:
    provider_id: str
    model_id: str
    state: AvailabilityState
    consecutive_failures: int
    success_count: int
    failure_count: int
    cooldown_until: str | None
    last_error_code: str | None
    last_error_type: str | None
    last_error_message: str | None
    last_latency_ms: int | None
    updated_at: str
    @property
    def routable(self) -> bool:
        return self.state in {"healthy","degraded","probing"}
    @property
    def availability_score(self) -> float:
        return {"healthy":1.0,"degraded":0.6,"probing":0.8,"cooldown":0.0,"unavailable":0.0}[self.state]

@dataclass(frozen=True)
class ProviderAvailabilitySnapshot:
    """Aggregated provider-level availability over its known models."""
    provider_id: str
    state: AvailabilityState
    model_count: int
    routable_model_count: int
    healthy_model_count: int
    degraded_model_count: int
    probing_model_count: int
    unavailable_model_count: int
    cooldown_model_count: int
    availability_score: float
    updated_at: str

    @property
    def routable(self) -> bool:
        return self.routable_model_count > 0

class ProviderHealthStore:
    """SQLite-backed provider/model operational state machine."""
    def __init__(self,path:str|Path,*,failure_threshold:int=2,base_cooldown_seconds:float=15.0,max_cooldown_seconds:float=300.0)->None:
        if failure_threshold<1: raise ValueError("failure_threshold must be at least 1")
        if base_cooldown_seconds<=0 or max_cooldown_seconds<base_cooldown_seconds: raise ValueError("invalid cooldown configuration")
        self.path=Path(path); self.path.parent.mkdir(parents=True,exist_ok=True)
        self.failure_threshold=int(failure_threshold); self.base_cooldown_seconds=float(base_cooldown_seconds); self.max_cooldown_seconds=float(max_cooldown_seconds); self._lock=RLock(); self._initialize()
    def _connect(self):
        db=sqlite3.connect(self.path,timeout=30,isolation_level=None); db.row_factory=sqlite3.Row; db.execute("PRAGMA journal_mode=WAL"); db.execute("PRAGMA busy_timeout=30000"); return db
    def _initialize(self)->None:
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS provider_health(
                provider_id TEXT NOT NULL, model_id TEXT NOT NULL, state TEXT NOT NULL,
                consecutive_failures INTEGER NOT NULL DEFAULT 0,
                success_count INTEGER NOT NULL DEFAULT 0, failure_count INTEGER NOT NULL DEFAULT 0,
                cooldown_until TEXT, last_error_code TEXT, last_error_type TEXT,
                last_error_message TEXT, last_latency_ms INTEGER, updated_at TEXT NOT NULL,
                PRIMARY KEY(provider_id,model_id))""")
            db.execute("CREATE INDEX IF NOT EXISTS idx_provider_health_state ON provider_health(state,updated_at)")
    def snapshot(self,provider_id:str,model_id:str,*,now:datetime|None=None)->ProviderHealthSnapshot:
        provider_id=provider_id.strip(); model_id=model_id.strip()
        if not provider_id or not model_id: raise ValueError("provider_id and model_id must be non-empty")
        now=now or _utcnow()
        with self._lock,self._connect() as db:
            row=db.execute("SELECT * FROM provider_health WHERE provider_id=? AND model_id=?",(provider_id,model_id)).fetchone()
            if row is None:
                return ProviderHealthSnapshot(provider_id,model_id,"healthy",0,0,0,None,None,None,None,None,now.isoformat())
            expiry=_parse_datetime(row["cooldown_until"])
            if row["state"] in {"cooldown", "unavailable"} and expiry is not None and now>=expiry:
                db.execute("UPDATE provider_health SET state='probing',updated_at=? WHERE provider_id=? AND model_id=?",(now.isoformat(),provider_id,model_id))
                row=db.execute("SELECT * FROM provider_health WHERE provider_id=? AND model_id=?",(provider_id,model_id)).fetchone()
            return _row_to_snapshot(row,now.isoformat())
    def provider_snapshot(self,provider_id:str,*,now:datetime|None=None)->ProviderAvailabilitySnapshot:
        provider_id=provider_id.strip()
        if not provider_id: raise ValueError("provider_id must be non-empty")
        snapshots=self.list(provider_id=provider_id)
        now=now or _utcnow()
        if not snapshots:
            return ProviderAvailabilitySnapshot(provider_id,"healthy",0,0,0,0,0,0,0,1.0,now.isoformat())
        counts={state:sum(1 for item in snapshots if item.state==state) for state in ("healthy","degraded","probing","unavailable","cooldown")}
        routable=counts["healthy"]+counts["degraded"]+counts["probing"]
        if counts["healthy"]>0: state="healthy"
        elif counts["probing"]>0: state="probing"
        elif counts["degraded"]>0: state="degraded"
        elif counts["cooldown"]>0: state="cooldown"
        else: state="unavailable"
        score=max(item.availability_score for item in snapshots)
        return ProviderAvailabilitySnapshot(
            provider_id,state,len(snapshots),routable,counts["healthy"],counts["degraded"],
            counts["probing"],counts["unavailable"],counts["cooldown"],score,
            max(item.updated_at for item in snapshots),
        )
    def available_providers(self,provider_ids:tuple[str,...]|None=None)->tuple[ProviderAvailabilitySnapshot,...]:
        ids=provider_ids or tuple(dict.fromkeys(item.provider_id for item in self.list()))
        return tuple(snapshot for snapshot in (self.provider_snapshot(provider_id) for provider_id in ids) if snapshot.routable)
    def is_eligible(self,provider_id:str,model_id:str,*,allow_probing:bool=True)->bool:
        state=self.snapshot(provider_id,model_id).state
        return state in {"healthy","degraded"} or (allow_probing and state=="probing")
    def record_success(self,provider_id:str,model_id:str,*,latency_ms:int|None=None)->ProviderHealthSnapshot:
        now=_utcnow()
        with self._lock,self._connect() as db:
            db.execute("""INSERT INTO provider_health(
                provider_id,model_id,state,consecutive_failures,success_count,failure_count,
                cooldown_until,last_error_code,last_error_type,last_error_message,last_latency_ms,updated_at)
                VALUES(?,?, 'healthy',0,1,0,NULL,NULL,NULL,NULL,?,?)
                ON CONFLICT(provider_id,model_id) DO UPDATE SET
                state='healthy',consecutive_failures=0,success_count=provider_health.success_count+1,
                cooldown_until=NULL,last_error_code=NULL,last_error_type=NULL,last_error_message=NULL,
                last_latency_ms=excluded.last_latency_ms,updated_at=excluded.updated_at""",
                (provider_id.strip(),model_id.strip(),latency_ms,now.isoformat()))
        return self.snapshot(provider_id,model_id)
    def record_failure(self,provider_id:str,model_id:str,error:Exception,*,latency_ms:int|None=None)->ProviderHealthSnapshot:
        now=_utcnow(); current=self.snapshot(provider_id,model_id,now=now); category,_=classify_failure(error); failures=current.consecutive_failures+1
        if category=="transient" and failures>=self.failure_threshold:
            delay=min(self.max_cooldown_seconds,self.base_cooldown_seconds*(2**min(failures-self.failure_threshold,6)))
            state="cooldown"; cooldown=(now+timedelta(seconds=delay)).isoformat()
        elif category=="transient": state="degraded"; cooldown=None
        elif category=="configuration":
            state="unavailable"; cooldown=(now+timedelta(seconds=self.max_cooldown_seconds)).isoformat()
        else: state="degraded"; cooldown=None
        with self._lock,self._connect() as db:
            db.execute("""INSERT INTO provider_health(
                provider_id,model_id,state,consecutive_failures,success_count,failure_count,
                cooldown_until,last_error_code,last_error_type,last_error_message,last_latency_ms,updated_at)
                VALUES(?,?,?, ?,0,1,?,?,?,?,?,?)
                ON CONFLICT(provider_id,model_id) DO UPDATE SET
                state=excluded.state,consecutive_failures=excluded.consecutive_failures,
                failure_count=provider_health.failure_count+1,cooldown_until=excluded.cooldown_until,
                last_error_code=excluded.last_error_code,last_error_type=excluded.last_error_type,
                last_error_message=excluded.last_error_message,last_latency_ms=excluded.last_latency_ms,updated_at=excluded.updated_at""",
                (provider_id.strip(),model_id.strip(),state,failures,cooldown,getattr(error,"code",None),type(error).__name__,str(error)[:2000],latency_ms,now.isoformat()))
        return self.snapshot(provider_id,model_id)
    def list(self,*,state:str|None=None,provider_id:str|None=None,limit:int=200)->tuple[ProviderHealthSnapshot,...]:
        if limit<1: raise ValueError("limit must be at least 1")
        q="SELECT * FROM provider_health WHERE 1=1"; p=[]
        if state is not None: q+=" AND state=?"; p.append(state)
        if provider_id is not None: q+=" AND provider_id=?"; p.append(provider_id.strip())
        q+=" ORDER BY updated_at DESC,provider_id,model_id LIMIT ?"; p.append(limit)
        with self._lock,self._connect() as db: rows=db.execute(q,tuple(p)).fetchall()
        return tuple(_row_to_snapshot(row) for row in rows)

def classify_failure(error:Exception)->tuple[str,float|None]:
    if isinstance(error,ProviderHTTPError):
        status=error.status_code
        if status==429 or status is None or status>=500: return "transient",1.0
        if status in {401,403}: return "configuration",None
        return "response",None
    if isinstance(error,(ProviderTimeoutError,ProviderTransportError)): return "transient",1.0
    if isinstance(error,ProviderConfigurationError): return "configuration",None
    return "response",None

def _row_to_snapshot(row:sqlite3.Row,default_updated_at:str|None=None)->ProviderHealthSnapshot:
    state=str(row["state"])
    if state not in {"healthy","degraded","cooldown","unavailable","probing"}: state="unavailable"
    return ProviderHealthSnapshot(str(row["provider_id"]),str(row["model_id"]),state,
        int(row["consecutive_failures"]),int(row["success_count"]),int(row["failure_count"]),
        row["cooldown_until"],row["last_error_code"],row["last_error_type"],row["last_error_message"],
        int(row["last_latency_ms"]) if row["last_latency_ms"] is not None else None,
        str(row["updated_at"] or default_updated_at or ""))
def _parse_datetime(value:object)->datetime|None:
    if not value: return None
    try: return datetime.fromisoformat(str(value))
    except ValueError: return None
def _utcnow()->datetime: return datetime.now(timezone.utc)
__all__=["AvailabilityState","ProviderHealthSnapshot","ProviderAvailabilitySnapshot","ProviderHealthStore","classify_failure"]
