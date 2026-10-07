"""Tests for audit trail, incidents, evidence, and API integration."""

from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app.db.session import SessionLocal
from backend.app.models.user import User
from backend.app.core.security import hash_password, create_access_token

client = TestClient(app)


def _auth_headers(role="OPERATOR"):
    username = role.lower()
    token = create_access_token(username, role)
    return {"Authorization": f"Bearer {token}"}


def _ensure_operator():
    """Ensure the test operator user exists."""
    db = SessionLocal()
    try:
        if not db.query(User).filter(User.username == "operator").first():
            db.add(User(
                username="operator",
                password_hash=hash_password("operator123"),
                role="OPERATOR",
            ))
            db.commit()
    finally:
        db.close()


def _seed(scenario="intrusion"):
    """Seed a demo incident."""
    _ensure_operator()
    return client.post(f"/api/v1/demo/seed?scenario={scenario}", headers=_auth_headers("ADMIN"))


def test_demo_creates_incident():
    """Demo seed should create a valid incident."""
    r = _seed("intrusion")
    assert r.status_code == 200
    data = r.json()
    assert "incident_id" in data
    assert data["threat_score"] > 0
    assert data["events_created"] > 0


def test_incident_acknowledge():
    """Acknowledge flow should update status."""
    r = _seed("intrusion")
    inc_id = r.json()["incident_id"]
    r = client.post(f"/api/v1/incidents/{inc_id}/acknowledge", headers=_auth_headers())
    assert r.status_code == 200
    assert r.json()["status"] == "ACKNOWLEDGED"


def test_incident_escalate():
    """Escalate flow should update status."""
    r = _seed("night_movement")
    inc_id = r.json()["incident_id"]
    r = client.post(f"/api/v1/incidents/{inc_id}/escalate", headers=_auth_headers("COMMANDER"))
    assert r.status_code == 200
    assert r.json()["status"] == "ESCALATED"


def test_incident_dismiss():
    """Dismiss flow should close as false positive."""
    r = _seed("loitering")
    inc_id = r.json()["incident_id"]
    r = client.post(f"/api/v1/incidents/{inc_id}/dismiss", headers=_auth_headers())
    assert r.status_code == 200
    assert r.json()["status"] == "DISMISSED"


def test_incident_timeline():
    """Incident should have a timeline endpoint."""
    r = _seed("intrusion")
    inc_id = r.json()["incident_id"]
    r = client.get(f"/api/v1/incidents/{inc_id}/timeline", headers=_auth_headers())
    assert r.status_code == 200
    assert "timeline" in r.json()
    assert len(r.json()["timeline"]) > 0


def test_evidence_verify():
    """Evidence should be verifiable."""
    r = _seed("intrusion")
    inc_id = r.json()["incident_id"]
    evidence_id = r.json()["evidence_id"]
    r = client.get(f"/api/v1/evidence/verify/{evidence_id}", headers=_auth_headers())
    assert r.status_code == 200
    assert r.json()["valid"] is True


def test_cameras_list():
    """Should list demo cameras."""
    r = client.get("/api/v1/cameras", headers=_auth_headers())
    assert r.status_code == 200
    assert len(r.json()) >= 1


def test_zones_list():
    """Should list zones after demo seed."""
    _seed("intrusion")
    r = client.get("/api/v1/zones", headers=_auth_headers())
    assert r.status_code == 200
    assert len(r.json()) >= 1


def test_auth_token():
    """Should obtain JWT token."""
    _ensure_operator()
    r = client.post("/api/v1/auth/token", data={
        "username": "operator", "password": "operator123"
    })
    assert r.status_code == 200
    assert "access_token" in r.json()


def test_system_status():
    """System status should return operational info when authenticated, and reject anonymous access."""
    # Anonymous access must be rejected with 401
    assert client.get("/api/v1/status").status_code == 401
    assert client.get("/api/v1/health/status").status_code == 401

    headers = _auth_headers("OPERATOR")
    r1 = client.get("/api/v1/status", headers=headers)
    assert r1.status_code == 200
    data1 = r1.json()
    assert data1["status"] == "operational"
    assert "cameras_total" in data1

    r2 = client.get("/api/v1/health/status", headers=headers)
    assert r2.status_code == 200
    data2 = r2.json()
    assert data2["status"] == "operational"
