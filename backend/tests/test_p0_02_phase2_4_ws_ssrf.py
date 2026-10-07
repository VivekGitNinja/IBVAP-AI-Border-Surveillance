"""
IBVAP Gate 2 — Phase 2.4 WebSocket Authentication & SSRF Hardening Test Suite.
Verifies fail-closed authentication on all WebSocket endpoints and comprehensive SSRF
protection on camera discovery, smart-probing, and stream testing endpoints.
"""

import os
import time
import pytest
from datetime import timedelta
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.app.main import app
from backend.app.core.security import create_access_token, hash_password
from backend.app.db.session import SessionLocal
from backend.app.models.user import User
from backend.app.models.camera import Camera

client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def setup_phase2_4_fixtures():
    db = SessionLocal()
    try:
        # Create active test users
        users = [
            ("ws_operator", "OPERATOR", True),
            ("ws_inactive", "OPERATOR", False),
            ("ws_commander", "COMMANDER", True),
        ]
        for username, role, active in users:
            existing = db.query(User).filter(User.username == username).first()
            if not existing:
                db.add(User(
                    username=username,
                    password_hash=hash_password("password123"),
                    role=role,
                    full_name=f"Test {username}",
                    active=active,
                ))
            else:
                existing.active = active
                existing.role = role

        # Ensure test camera 1 exists for live stream tests
        cam1 = db.get(Camera, 1)
        if not cam1:
            db.add(Camera(
                id=1,
                name="Test-Surveillance-Cam-1",
                stream_url="demo://1",
                latitude=28.7041,
                longitude=77.1025,
                sector="Sector Alpha",
                status="ONLINE",
            ))
        db.commit()
    finally:
        db.close()


def _get_headers(username: str = "ws_commander", role: str = "COMMANDER") -> dict:
    token = create_access_token(username, role)
    return {"Authorization": f"Bearer {token}"}


# ==============================================================================
# 1. WEBSOCKET AUTHENTICATION & AUTHORIZATION TESTS
# ==============================================================================

def test_ws_events_unauthenticated_rejected():
    """Connecting to /ws/events without token must be closed with code 4001."""
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/events"):
            pass
    assert exc.value.code == 4001


def test_ws_events_invalid_token_rejected():
    """Connecting to /ws/events with forged token must be closed with code 4001."""
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/events?token=invalid.tampered.token"):
            pass
    assert exc.value.code == 4001


def test_ws_events_expired_token_rejected():
    """Connecting to /ws/events with expired token must be closed with code 4001."""
    expired_token = create_access_token("ws_operator", "OPERATOR", expires_delta=timedelta(seconds=-10))
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(f"/ws/events?token={expired_token}"):
            pass
    assert exc.value.code == 4001


def test_ws_events_inactive_user_rejected():
    """Connecting with token belonging to deactivated user must be rejected with 4001."""
    token = create_access_token("ws_inactive", "OPERATOR")
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(f"/ws/events?token={token}"):
            pass
    assert exc.value.code == 4001


def test_ws_events_valid_token_success():
    """Connecting to /ws/events with valid token connects and handles ping/pong."""
    token = create_access_token("ws_operator", "OPERATOR")
    with client.websocket_connect(f"/ws/events?token={token}") as ws:
        ws.send_text("ping")
        data = ws.receive_json()
        assert data == {"type": "pong"}


def test_ws_analysis_unauthenticated_rejected():
    """Connecting to /ws/analysis/{job_id} without token must be closed with 4001."""
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/analysis/1"):
            pass
    assert exc.value.code == 4001


def test_ws_analysis_invalid_token_rejected():
    """Connecting to /ws/analysis/{job_id} with invalid token must be closed with 4001."""
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/analysis/1?token=bad.token.here"):
            pass
    assert exc.value.code == 4001


def test_ws_analysis_valid_token_success():
    """Connecting to /ws/analysis/{job_id} with valid token succeeds."""
    token = create_access_token("ws_operator", "OPERATOR")
    with client.websocket_connect(f"/ws/analysis/9999?token={token}") as ws:
        ws.send_text("ping")
        data = ws.receive_json()
        assert data == {"type": "pong"}


def test_ws_live_unauthenticated_rejected():
    """Connecting to /ws/live/{camera_id} without token must be closed with 4001."""
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/live/1"):
            pass
    assert exc.value.code == 4001


def test_ws_live_invalid_token_rejected():
    """Connecting to /ws/live/{camera_id} with invalid token must be closed with 4001."""
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/live/1?token=fake_token"):
            pass
    assert exc.value.code == 4001


def test_ws_live_valid_token_receives_frame():
    """Connecting to /ws/live/{camera_id} with valid token streams JPEG frames."""
    token = create_access_token("ws_operator", "OPERATOR")
    with client.websocket_connect(f"/ws/live/1?token={token}") as ws:
        frame = ws.receive_bytes()
        assert len(frame) > 0
        assert frame[:2] == b"\xff\xd8"  # JPEG Magic Bytes


# ==============================================================================
# 2. SSRF HARDENING ON SMART-PROBE ENDPOINT
# ==============================================================================

def test_smart_probe_rejects_loopback_ip():
    """Smart probe targeting 127.0.0.1 must be rejected with 400."""
    headers = _get_headers()
    r = client.post("/api/v1/cameras/smart-probe", headers=headers, json={"ip": "127.0.0.1"})
    assert r.status_code == 400
    assert "loopback" in r.json()["detail"].lower()


def test_smart_probe_rejects_loopback_subrange():
    """Smart probe targeting 127.0.1.5 must be rejected with 400."""
    headers = _get_headers()
    r = client.post("/api/v1/cameras/smart-probe", headers=headers, json={"ip": "127.0.1.5"})
    assert r.status_code == 400
    assert "loopback" in r.json()["detail"].lower()


def test_smart_probe_rejects_localhost_hostname():
    """Smart probe targeting 'localhost' must be rejected with 400."""
    headers = _get_headers()
    r = client.post("/api/v1/cameras/smart-probe", headers=headers, json={"ip": "localhost"})
    assert r.status_code == 400
    assert "prohibited" in r.json()["detail"].lower()


def test_smart_probe_rejects_link_local_ip():
    """Smart probe targeting link-local IP 169.254.10.20 must be rejected with 400."""
    headers = _get_headers()
    r = client.post("/api/v1/cameras/smart-probe", headers=headers, json={"ip": "169.254.10.20"})
    assert r.status_code == 400
    assert "link-local" in r.json()["detail"].lower()


def test_smart_probe_rejects_cloud_metadata():
    """Smart probe targeting AWS/GCP metadata IP 169.254.169.254 must be rejected with 400."""
    headers = _get_headers()
    r = client.post("/api/v1/cameras/smart-probe", headers=headers, json={"ip": "169.254.169.254"})
    assert r.status_code == 400
    assert "metadata" in r.json()["detail"].lower() or "link-local" in r.json()["detail"].lower()


def test_smart_probe_rejects_prohibited_ports():
    """Smart probe with internal system ports (Postgres 5432, SSH 22, Redis 6379) must be rejected with 400."""
    headers = _get_headers()
    for bad_port in (22, 5432, 6379, 3306):
        r = client.post("/api/v1/cameras/smart-probe", headers=headers, json={"ip": "192.168.1.50", "ports": [bad_port]})
        assert r.status_code == 400, f"Expected 400 for port {bad_port}, got {r.status_code}"
        assert "not an authorized surveillance streaming port" in r.json()["detail"]


def test_smart_probe_authorized_lan_ip():
    """Smart probe with legitimate LAN IP and surveillance ports executes normally."""
    headers = _get_headers()
    r = client.post("/api/v1/cameras/smart-probe", headers=headers, json={
        "ip": "192.168.1.188",
        "ports": [554, 8554],
        "username": "admin",
        "password": "password",
    })
    assert r.status_code == 200
    assert "candidate_urls" in r.json()


# ==============================================================================
# 3. SSRF HARDENING ON TEST-STREAM ENDPOINT
# ==============================================================================

def test_test_stream_rejects_loopback():
    """Stream test targeting 127.0.0.1 must be rejected with 400."""
    headers = _get_headers()
    r = client.post("/api/v1/cameras/test-stream", headers=headers, json={"stream_url": "http://127.0.0.1:8000"})
    assert r.status_code == 400
    assert "loopback" in r.json()["detail"].lower()


def test_test_stream_rejects_cloud_metadata():
    """Stream test targeting cloud metadata endpoint must be rejected with 400."""
    headers = _get_headers()
    r = client.post("/api/v1/cameras/test-stream", headers=headers, json={"stream_url": "http://169.254.169.254/latest/meta-data"})
    assert r.status_code == 400
    assert "metadata" in r.json()["detail"].lower() or "link-local" in r.json()["detail"].lower()


def test_test_stream_rejects_postgres_port():
    """Stream test targeting PostgreSQL port 5432 must be rejected with 400."""
    headers = _get_headers()
    r = client.post("/api/v1/cameras/test-stream", headers=headers, json={"stream_url": "rtsp://192.168.1.50:5432/stream"})
    assert r.status_code == 400
    assert "not an authorized surveillance streaming port" in r.json()["detail"]


def test_test_stream_rejects_unsupported_scheme():
    """Stream test using gopher:// or ftp:// schemes must be rejected with 400."""
    headers = _get_headers()
    r = client.post("/api/v1/cameras/test-stream", headers=headers, json={"stream_url": "gopher://192.168.1.50:554/stream"})
    assert r.status_code == 400
    assert "unsupported" in r.json()["detail"].lower()


def test_test_stream_rejects_file_traversal_outside_storage():
    """Stream test using file:///etc/passwd must be rejected with 403."""
    headers = _get_headers()
    r = client.post("/api/v1/cameras/test-stream", headers=headers, json={"stream_url": "file:///etc/passwd"})
    assert r.status_code == 403
    assert "Access denied" in r.json()["detail"]


# ==============================================================================
# 4. SSRF HARDENING ON DISCOVERY ENDPOINT
# ==============================================================================

def test_discover_rejects_loopback_range():
    """Discovery scan targeting 127.0.0.x must be rejected with 400."""
    headers = _get_headers()
    r = client.post("/api/v1/cameras/discover", headers=headers, json={"ip_range": "127.0.0", "start": 1, "end": 10})
    assert r.status_code == 400
    assert "loopback" in r.json()["detail"].lower()


def test_discover_rejects_link_local_range():
    """Discovery scan targeting 169.254.x.x must be rejected with 400."""
    headers = _get_headers()
    r = client.post("/api/v1/cameras/discover", headers=headers, json={"ip_range": "169.254.1", "start": 1, "end": 10})
    assert r.status_code == 400
    assert "link-local" in r.json()["detail"].lower()
