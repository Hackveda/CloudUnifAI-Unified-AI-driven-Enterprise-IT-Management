import importlib
import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CLOUDUNIFAI_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("CLOUDUNIFAI_API_KEY", "test-secret")
    import app.main as m
    importlib.reload(m)
    with TestClient(m.app) as c:
        yield c


def auth():
    return {"X-API-Key": "test-secret"}


def test_auth_and_health(client):
    assert client.get("/health").status_code == 200
    assert client.get("/api/incidents").status_code == 401
    assert client.get("/api/incidents", headers=auth()).status_code == 200


def test_correlate_deduplicate_approve_and_resolve(client):
    events = [
        {"source": "cloudwatch", "service": "checkout", "kind": "deployment", "severity": "low",
         "message": "Deployment v2.4 started", "external_id": "dep-100"},
        {"source": "prometheus", "service": "checkout", "kind": "alert", "severity": "critical",
         "message": "CPU high and database connections timeout", "external_id": "alert-101"},
    ]
    a = client.post("/api/events", json=events[0], headers=auth())
    assert a.status_code == 201
    iid = a.json()["incident_id"]
    b = client.post("/api/events", json=events[1], headers=auth())
    assert b.json()["incident_id"] == iid
    assert client.post("/api/events", json=events[1], headers=auth()).json()["duplicate"]
    detail = client.get("/api/incidents/" + iid, headers=auth()).json()
    assert len(detail["events"]) == 2
    assert detail["severity"] == "critical"
    assert detail["confidence"] > 0.0
    ml = client.get("/api/incidents/" + iid + "/ml-analysis", headers=auth())
    assert ml.status_code == 200
    assert ml.json()["mode"] == "recommendation_only"
    assert "notify-oncall" in ml.json()["authorized_playbook_candidates"]
    act = client.post("/api/incidents/" + iid + "/actions",
                      json={"actor": "oncall", "playbook": "notify-oncall"}, headers=auth())
    aid = act.json()["id"]
    assert client.post("/api/actions/" + aid + "/approve", json={"actor": "oncall"}, headers=auth()).status_code == 403
    approved = client.post("/api/actions/" + aid + "/approve", json={"actor": "incident-manager"}, headers=auth())
    assert approved.json()["status"] == "simulated_success"
    assert "SIMULATED" in approved.json()["outcome"]
    assert client.post("/api/incidents/" + iid + "/resolve", json={"actor": "oncall"}, headers=auth()).status_code == 200
    assert client.post("/api/incidents/" + iid + "/actions",
                       json={"actor": "oncall", "playbook": "notify-oncall"}, headers=auth()).status_code == 409


def test_demo_and_audit(client):
    response = client.post("/api/demo", headers=auth())
    assert response.status_code == 200
    assert response.json()["event_count"] == 4
    overview = client.get("/api/overview", headers=auth()).json()
    assert overview["total_events"] == 4
    assert len(overview["finops_signals"]) == 1
    assert len(client.get("/api/audit", headers=auth()).json()) == 4
