"""CloudUnifAI pilot: event ingestion, correlation, safe simulated remediations.

All automation here is explicitly SIMULATED. No cloud/ITSM changes are performed.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

APP_DIR = Path(__file__).resolve().parent
DB = os.getenv("CLOUDUNIFAI_DB", "/tmp/cloudunifai.db")
API_KEY = os.getenv("CLOUDUNIFAI_API_KEY", "")
SEVERITIES = {"critical": 4, "high": 3, "medium": 2, "low": 1}
app = FastAPI(title="CloudUnifAI", version="0.1.0", description="Enterprise IT intelligence — safe simulation MVP")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connection():
    db = sqlite3.connect(DB, timeout=20, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=20000")
    db.execute("PRAGMA journal_mode=WAL")
    try:
        yield db
    finally:
        db.close()


def init_db():
    Path(DB).parent.mkdir(parents=True, exist_ok=True)
    with connection() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY, dedupe_key TEXT UNIQUE NOT NULL,
            source TEXT NOT NULL, service TEXT NOT NULL, kind TEXT NOT NULL,
            severity TEXT NOT NULL, message TEXT NOT NULL,
            attributes TEXT NOT NULL, occurred_at TEXT NOT NULL,
            received_at TEXT NOT NULL, incident_id TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_events_incident ON events(incident_id);
        CREATE TABLE IF NOT EXISTS incidents (
            id TEXT PRIMARY KEY, service TEXT NOT NULL, status TEXT NOT NULL,
            severity TEXT NOT NULL, summary TEXT NOT NULL,
            root_cause TEXT NOT NULL, confidence REAL NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_incidents_service_status ON incidents(service,status);
        CREATE TABLE IF NOT EXISTS actions (
            id TEXT PRIMARY KEY, incident_id TEXT NOT NULL,
            playbook TEXT NOT NULL, status TEXT NOT NULL,
            requested_by TEXT NOT NULL, approved_by TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            outcome TEXT, FOREIGN KEY(incident_id) REFERENCES incidents(id)
        );
        CREATE TABLE IF NOT EXISTS audits (
            id TEXT PRIMARY KEY, at TEXT NOT NULL, actor TEXT NOT NULL,
            action TEXT NOT NULL, entity_id TEXT NOT NULL, detail TEXT NOT NULL
        );
        """)


@app.on_event("startup")
def startup():
    init_db()


def authorized(x_api_key: str | None = Header(None)):
    if not API_KEY:
        raise HTTPException(503, "Set CLOUDUNIFAI_API_KEY before using the API")
    if not x_api_key or not __import__("hmac").compare_digest(x_api_key, API_KEY):
        raise HTTPException(401, "Invalid API key")


def audit(db, actor: str, action: str, entity: str, detail: dict):
    db.execute("INSERT INTO audits VALUES (?,?,?,?,?,?)",
               (str(uuid.uuid4()), utcnow(), actor, action, entity, json.dumps(detail)))


class EventInput(BaseModel):
    source: str = Field(min_length=2, max_length=80)
    service: str = Field(min_length=2, max_length=100)
    kind: Literal["metric", "alert", "log", "ticket", "deployment", "billing", "change"]
    severity: Literal["low", "medium", "high", "critical"] = "medium"
    message: str = Field(min_length=3, max_length=2000)
    attributes: dict = Field(default_factory=dict)
    occurred_at: datetime | None = None
    external_id: str | None = Field(default=None, max_length=200)


class Actor(BaseModel):
    actor: str = Field(min_length=2, max_length=100)


class ActionRequest(Actor):
    playbook: Literal["canary-scale-db-pool", "rollback-deployment", "notify-oncall"]


def summarize(events: list[dict]) -> tuple[str, float]:
    texts = " ".join(e["message"].lower() for e in events)
    kinds = {e["kind"] for e in events}
    if "deploy" in texts or "rollout" in texts or "deployment" in kinds:
        return "Recent deployment correlated with service degradation; verify change timeline", 0.83 if len(kinds) > 1 else 0.63
    if ("connection" in texts or "pool" in texts) and ("cpu" in texts or "timeout" in texts):
        return "Possible connection pool saturation affecting application performance", 0.79
    if "cost" in texts or "spend" in texts or "billing" in kinds:
        return "Cloud spend anomaly; inspect resource utilization and recent provisioning", 0.69
    if "cpu" in texts or "memory" in texts:
        return "Resource saturation suspected; inspect capacity and competing workloads", 0.66
    return "Insufficient evidence for high-confidence diagnosis; investigate correlated signals", 0.35


def incident_view(db, incident_id: str):
    row = db.execute("SELECT * FROM incidents WHERE id=?", (incident_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Incident not found")
    item = dict(row)
    item["events"] = [dict(e) | {"attributes": json.loads(e["attributes"])}
                      for e in db.execute("SELECT * FROM events WHERE incident_id=? ORDER BY occurred_at", (incident_id,))]
    item["actions"] = [dict(a) for a in db.execute("SELECT * FROM actions WHERE incident_id=? ORDER BY created_at DESC", (incident_id,))]
    return item


@app.get("/", include_in_schema=False)
def ui():
    return FileResponse(APP_DIR / "static" / "index.html")


@app.get("/health")
def health():
    return {"status": "ok", "mode": "simulation", "database": "ready"}


@app.post("/api/events", dependencies=[Depends(authorized)], status_code=201)
def ingest(event: EventInput):
    now = utcnow()
    occurred = (event.occurred_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    event_id = str(uuid.uuid4())
    dedupe = event.external_id or hashlib.sha256(json.dumps({
        "source": event.source, "service": event.service, "kind": event.kind,
        "message": event.message, "occurred_at": occurred,
    }, sort_keys=True).encode()).hexdigest()
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        duplicate = db.execute("SELECT id,incident_id FROM events WHERE dedupe_key=?", (dedupe,)).fetchone()
        if duplicate:
            db.execute("COMMIT")
            return {"id": duplicate["id"], "incident_id": duplicate["incident_id"], "duplicate": True}
        # One active incident per service in this pilot. Production requires topology + temporal windows.
        incident = db.execute("SELECT * FROM incidents WHERE service=? AND status='open' ORDER BY created_at DESC LIMIT 1",
                              (event.service,)).fetchone()
        if incident is None:
            incident_id = str(uuid.uuid4())
            db.execute("INSERT INTO incidents VALUES (?,?,?,?,?,?,?,?,?)",
                       (incident_id, event.service, "open", event.severity, event.message, "", 0.0, now, now))
        else:
            incident_id = incident["id"]
        db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (event_id, dedupe, event.source, event.service, event.kind, event.severity,
                    event.message, json.dumps(event.attributes), occurred, now, incident_id))
        evs = [dict(e) for e in db.execute("SELECT * FROM events WHERE incident_id=?", (incident_id,))]
        root, confidence = summarize(evs)
        highest = max(evs, key=lambda e: SEVERITIES[e["severity"]])["severity"]
        db.execute("UPDATE incidents SET severity=?,root_cause=?,confidence=?,updated_at=? WHERE id=?",
                   (highest, root, confidence, now, incident_id))
        audit(db, "adapter:" + event.source, "event.ingested", incident_id, {"event_id": event_id, "kind": event.kind})
        db.execute("COMMIT")
    return {"id": event_id, "incident_id": incident_id, "duplicate": False}


@app.get("/api/incidents", dependencies=[Depends(authorized)])
def incidents(status: Literal["open", "resolved", "all"] = "all", limit: int = Query(50, ge=1, le=200)):
    with connection() as db:
        query = "SELECT * FROM incidents"
        args: list = []
        if status != "all":
            query += " WHERE status=?"
            args.append(status)
        return [dict(x) for x in db.execute(query + " ORDER BY updated_at DESC LIMIT ?", (*args, limit))]


@app.get("/api/incidents/{incident_id}", dependencies=[Depends(authorized)])
def incident_detail(incident_id: str):
    with connection() as db:
        return incident_view(db, incident_id)


@app.get("/api/overview", dependencies=[Depends(authorized)])
def overview():
    with connection() as db:
        counts = {r["status"]: r["n"] for r in db.execute("SELECT status,count(*) n FROM incidents GROUP BY status")}
        sources = [dict(r) for r in db.execute("SELECT source,count(*) AS events FROM events GROUP BY source ORDER BY events DESC")]
        action_counts = {r["status"]: r["n"] for r in db.execute("SELECT status,count(*) n FROM actions GROUP BY status")}
        costs = [dict(r) for r in db.execute(
            "SELECT service,COUNT(*) AS anomaly_events FROM events WHERE kind='billing' GROUP BY service ORDER BY anomaly_events DESC")]
        return {"open": counts.get("open", 0), "resolved": counts.get("resolved", 0),
                "total_events": db.execute("SELECT count(*) FROM events").fetchone()[0],
                "actions": action_counts, "sources": sources, "finops_signals": costs,
                "mode": "simulation"}


@app.get("/api/incidents/{incident_id}/recommendations", dependencies=[Depends(authorized)])
def recommendations(incident_id: str):
    with connection() as db:
        incident = incident_view(db, incident_id)
    root = incident["root_cause"].lower()
    options = [{"playbook": "notify-oncall", "risk": "low", "reason": "Escalate to human operator", "requires_approval": True}]
    if "connection" in root or "resource" in root:
        options.insert(0, {"playbook": "canary-scale-db-pool", "risk": "medium", "reason": "Test canary capacity with rollback capability", "requires_approval": True})
    if "deploy" in root:
        options.insert(0, {"playbook": "rollback-deployment", "risk": "high", "reason": "Investigate reverting correlated change", "requires_approval": True})
    return {"incident_id": incident_id, "root_cause": incident["root_cause"],
            "confidence": incident["confidence"], "recommendations": options}


@app.post("/api/incidents/{incident_id}/actions", dependencies=[Depends(authorized)], status_code=201)
def request_action(incident_id: str, req: ActionRequest):
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        incident = db.execute("SELECT status FROM incidents WHERE id=?", (incident_id,)).fetchone()
        if not incident:
            raise HTTPException(404, "Incident not found")
        if incident["status"] != "open":
            raise HTTPException(409, "Incident is resolved")
        aid, now = str(uuid.uuid4()), utcnow()
        db.execute("INSERT INTO actions (id,incident_id,playbook,status,requested_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                   (aid, incident_id, req.playbook, "pending_approval", req.actor, now, now))
        audit(db, req.actor, "action.requested", aid, {"playbook": req.playbook})
        db.execute("COMMIT")
        return {"id": aid, "status": "pending_approval", "mode": "simulation"}


@app.post("/api/actions/{action_id}/approve", dependencies=[Depends(authorized)])
def approve_action(action_id: str, req: Actor):
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        action = db.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
        if not action:
            raise HTTPException(404, "Action not found")
        if action["status"] != "pending_approval":
            raise HTTPException(409, "Action is not pending")
        if action["requested_by"] == req.actor:
            raise HTTPException(403, "Requester cannot approve own action")
        result = "SIMULATED: canary executed; checks passed; no production systems were modified"
        db.execute("UPDATE actions SET status='simulated_success',approved_by=?,updated_at=?,outcome=? WHERE id=?",
                   (req.actor, utcnow(), result, action_id))
        audit(db, req.actor, "action.simulated", action_id, {"outcome": result})
        db.execute("COMMIT")
    return {"id": action_id, "status": "simulated_success", "outcome": result}


@app.post("/api/incidents/{incident_id}/resolve", dependencies=[Depends(authorized)])
def resolve_incident(incident_id: str, req: Actor):
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        count = db.execute("UPDATE incidents SET status='resolved',updated_at=? WHERE id=? AND status='open'",
                           (utcnow(), incident_id)).rowcount
        if not count:
            raise HTTPException(404, "Open incident not found")
        audit(db, req.actor, "incident.resolved", incident_id, {})
        db.execute("COMMIT")
    return {"id": incident_id, "status": "resolved"}


@app.get("/api/audit", dependencies=[Depends(authorized)])
def audit_log(limit: int = Query(100, ge=1, le=500)):
    with connection() as db:
        return [dict(r) | {"detail": json.loads(r["detail"])} for r in db.execute(
            "SELECT * FROM audits ORDER BY at DESC LIMIT ?", (limit,))]


@app.post("/api/demo", dependencies=[Depends(authorized)])
def demo():
    """Generate synthetic enterprise signals for a representative service incident."""
    demo_id = str(uuid.uuid4())[:8]
    payloads = [
        EventInput(source="cloudwatch", service="checkout-api-" + demo_id, kind="deployment",
                   severity="low", message="Deployment v2.4 rolled out to checkout API"),
        EventInput(source="prometheus", service="checkout-api-" + demo_id, kind="alert",
                   severity="critical", message="HTTP 5xx spike; CPU high; database connection timeout"),
        EventInput(source="servicenow", service="checkout-api-" + demo_id, kind="ticket",
                   severity="high", message="Customer checkout errors after deployment"),
        EventInput(source="aws-billing", service="checkout-api-" + demo_id, kind="billing",
                   severity="medium", message="Cloud spend anomaly: elevated compute cost"),
    ]
    results = [ingest(p) for p in payloads]
    return {"incident_id": results[0]["incident_id"], "event_count": len(results), "synthetic": True}
