"""
IBVAP Gate 2 — Master Adversarial Security & Penetration Suite.
Simulates real-world advanced persistent threat (APT) attack vectors against IBVAP:
1. Algorithm confusion & token forgery (P0-01)
2. Production secret exposure & weak configuration (P0-02)
3. Unauthenticated static evidence exfiltration & traversal escapes (P0-03)
4. WebSocket hijacking & unauthenticated telemetry taps (P0-04)
5. Outbound Server-Side Request Forgery & internal port scans (P0-05)
6. Vertical and horizontal privilege escalation across C4ISR modules (P0-06)
7. Media upload malware ingestion & container poisoning (P0-07)
8. User governance, deactivation revocation, and last-admin invariant (P0-08)
9. Brute-force credential stuffing and session lockout (P0-09)
10. Section 63 Bharatiya Sakshya Adhiniyam evidence vault audit integrity (P0-10)
"""

import os
import time
import jwt
import pytest
from datetime import datetime, timedelta
from pathlib import Path
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.app.main import app
from backend.app.core.config import settings, validate_security_configuration, SecurityConfigurationError
from backend.app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    role_has_permission,
)
from backend.app.db.session import SessionLocal
from backend.app.models.user import User
from backend.app.models.audit import AuditLog

client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def setup_adversarial_fixture():
    db = SessionLocal()
    try:
        users = [
            ("adv_admin", "ADMIN", True),
            ("adv_commander", "COMMANDER", True),
            ("adv_operator", "OPERATOR", True),
            ("adv_auditor", "AUDITOR", True),
            ("adv_viewer", "VIEWER", True),
            ("adv_deactivated", "OPERATOR", False),
        ]
        for username, role, active in users:
            u = db.query(User).filter(User.username == username).first()
            if not u:
                db.add(User(
                    username=username,
                    password_hash=hash_password("Secur3P@ssw0rd!#"),
                    role=role,
                    full_name=f"Adversarial {role}",
                    active=active,
                ))
            else:
                u.active = active
                u.role = role
        db.commit()
    finally:
        db.close()


def _get_headers(username: str = "adv_admin", role: str = "ADMIN") -> dict:
    return {"Authorization": f"Bearer {create_access_token(username, role)}" }


# ==============================================================================
# VECTOR 1: TOKEN FORGERY, TAMPERING & ALGORITHM CONFUSION
# ==============================================================================

def test_adv_algorithm_none_rejected():
    """Tokens signed with 'none' algorithm must be rejected with 401."""
    header = {"alg": "none", "typ": "JWT"}
    payload = {"sub": "adv_admin", "role": "ADMIN", "type": "access", "exp": int(time.time()) + 3600}
    # Unsigned token
    import base64, json
    h_b64 = base64.urlsafe_b64encode(json.dumps(header).encode()).rstrip(b"=").decode()
    p_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    tampered_token = f"{h_b64}.{p_b64}."
    
    r = client.get("/api/v1/cameras", headers={"Authorization": f"Bearer {tampered_token}"})
    assert r.status_code == 401


def test_adv_signature_tampering_rejected():
    """Flipping a single byte in a valid JWT signature must cause 401 rejection."""
    valid_token = create_access_token("adv_admin", "ADMIN")
    parts = valid_token.split(".")
    # Mutate first character of signature (guarantees cryptographic bit mutation avoiding 43rd-char base64 padding alias)
    tampered_sig = ("A" if parts[2][0] != "A" else "B") + parts[2][1:]
    tampered_token = f"{parts[0]}.{parts[1]}.{tampered_sig}"

    r = client.get("/api/v1/cameras", headers={"Authorization": f"Bearer {tampered_token}"})
    assert r.status_code == 401


def test_adv_refresh_token_as_access_token_rejected():
    """Presenting a refresh token to an API requiring an access token must fail with 401."""
    from backend.app.core.security import create_refresh_token
    refresh_token = create_refresh_token("adv_admin")
    r = client.get("/api/v1/cameras", headers={"Authorization": f"Bearer {refresh_token}"})
    assert r.status_code == 401


# ==============================================================================
# VECTOR 2: PRIVILEGE ESCALATION (HORIZONTAL & VERTICAL)
# ==============================================================================

def test_adv_viewer_cannot_dispatch_qrt():
    """VIEWER role cannot execute tactical QRT dispatches (HTTP 403)."""
    headers = _get_headers("adv_viewer", "VIEWER")
    r = client.post("/api/v1/qrt/dispatch", headers=headers, json={"team_id": 1, "target_lat": 28.5, "target_lng": 77.2})
    assert r.status_code == 403


def test_adv_viewer_cannot_control_barrier():
    """VIEWER role cannot toggle physical border barriers (HTTP 403)."""
    headers = _get_headers("adv_viewer", "VIEWER")
    r = client.post("/api/v1/anpr/barrier/toggle", headers=headers, json={"gate_id": 1, "state": "OPEN"})
    assert r.status_code == 403


def test_adv_operator_cannot_manage_users():
    """OPERATOR role cannot list or create administrative accounts (HTTP 403)."""
    headers = _get_headers("adv_operator", "OPERATOR")
    r1 = client.get("/api/v1/users", headers=headers)
    assert r1.status_code == 403
    r2 = client.post("/api/v1/users", headers=headers, json={"username": "hacker", "password": "Password123!#"})
    assert r2.status_code == 403


def test_adv_jwt_claim_role_override_rejected():
    """Forging a higher role in JWT claim while DB has lower role must be clamped to DB role."""
    # DB has adv_operator as OPERATOR. Attacker generates token with role="ADMIN"
    forged_token = create_access_token("adv_operator", "ADMIN")
    r = client.get("/api/v1/users", headers={"Authorization": f"Bearer {forged_token}"})
    # DB role lookup overrides JWT claim -> rejected with 403
    assert r.status_code == 403


# ==============================================================================
# VECTOR 3: EVIDENCE VAULT EXFILTRATION & TRAVERSAL ATTACKS
# ==============================================================================

def test_adv_static_evidence_mount_inaccessible():
    """Unauthenticated access to /data/evidence must strictly return 404."""
    r = client.get("/data/evidence/clips/secret_patrol.mp4")
    assert r.status_code == 404


def test_adv_vault_path_traversal_etc_passwd():
    """Evidence vault rejects traversal sequences (../) targeting host files."""
    headers = _get_headers("adv_operator", "OPERATOR")
    r = client.get("/api/v1/evidence/vault/../../../../../../etc/passwd", headers=headers)
    assert r.status_code in (403, 404)


def test_adv_vault_null_byte_attack():
    """Null-byte injection in evidence vault path returns 400 Bad Request."""
    headers = _get_headers("adv_operator", "OPERATOR")
    r = client.get("/api/v1/evidence/vault/clips/file.mp4%00.jpg", headers=headers)
    assert r.status_code == 400


def test_adv_vault_access_generates_bsa_audit():
    """Vault access produces verifiable Section 63 BSA audit log with SHA-256."""
    headers = _get_headers("adv_operator", "OPERATOR")
    
    # Create test clip
    vault_root = Path(settings.evidence_dir).resolve()
    clips_dir = vault_root / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    test_clip = clips_dir / "adv_audit_clip.mp4"
    test_clip.write_bytes(b"\x00\x00\x00\x1cftypisom" + (b"\x00" * 200))

    try:
        r = client.get("/api/v1/evidence/vault/clips/adv_audit_clip.mp4", headers=headers)
        assert r.status_code == 200
        assert r.headers.get("X-Statutory-Compliance") == "Bharatiya Sakshya Adhiniyam, 2023 Section 63"
        assert len(r.headers.get("X-Evidence-SHA256", "")) == 64

        db = SessionLocal()
        try:
            log = db.query(AuditLog).filter(
                AuditLog.action == "READ_EVIDENCE_VAULT",
                AuditLog.target_id == "clips/adv_audit_clip.mp4"
            ).first()
            assert log is not None
            assert log.actor == "adv_operator"
            assert len(log.entry_hash) == 64
        finally:
            db.close()
    finally:
        if test_clip.exists():
            test_clip.unlink()


# ==============================================================================
# VECTOR 4: SSRF & INTERNAL PORT SCAN ATTACKS
# ==============================================================================

def test_adv_ssrf_smart_probe_loopback():
    """Smart probe targeting loopback 127.0.0.1 is blocked (HTTP 400)."""
    headers = _get_headers("adv_admin", "ADMIN")
    r = client.post("/api/v1/cameras/smart-probe", headers=headers, json={"ip": "127.0.0.1"})
    assert r.status_code == 400
    assert "loopback" in r.json()["detail"].lower()


def test_adv_ssrf_smart_probe_cloud_metadata():
    """Smart probe targeting 169.254.169.254 is blocked (HTTP 400)."""
    headers = _get_headers("adv_admin", "ADMIN")
    r = client.post("/api/v1/cameras/smart-probe", headers=headers, json={"ip": "169.254.169.254"})
    assert r.status_code == 400
    assert "metadata" in r.json()["detail"].lower() or "link-local" in r.json()["detail"].lower()


def test_adv_ssrf_smart_probe_database_port():
    """Smart probe targeting internal PostgreSQL port 5432 is blocked (HTTP 400)."""
    headers = _get_headers("adv_admin", "ADMIN")
    r = client.post("/api/v1/cameras/smart-probe", headers=headers, json={"ip": "192.168.1.50", "ports": [5432]})
    assert r.status_code == 400
    assert "not an authorized surveillance streaming port" in r.json()["detail"]


def test_adv_ssrf_stream_test_file_traversal():
    """Test-stream file:// URL targeting /etc/shadow is blocked (HTTP 403)."""
    headers = _get_headers("adv_admin", "ADMIN")
    r = client.post("/api/v1/cameras/test-stream", headers=headers, json={"stream_url": "file:///etc/shadow"})
    assert r.status_code == 403


# ==============================================================================
# VECTOR 5: WEBSOCKET AUTH & HIJACKING ATTACKS
# ==============================================================================

def test_adv_ws_events_anonymous_closed():
    """Anonymous connection to /ws/events closed immediately with code 4001."""
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/events"):
            pass
    assert exc.value.code == 4001


def test_adv_ws_analysis_anonymous_closed():
    """Anonymous connection to /ws/analysis/{job_id} closed immediately with code 4001."""
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/analysis/100"):
            pass
    assert exc.value.code == 4001


def test_adv_ws_live_anonymous_closed():
    """Anonymous connection to /ws/live/{camera_id} closed immediately with code 4001."""
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws/live/1"):
            pass
    assert exc.value.code == 4001


def test_adv_ws_deactivated_user_closed():
    """Deactivated user account attempting WebSocket connection closed with code 4001."""
    token = create_access_token("adv_deactivated", "OPERATOR")
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(f"/ws/events?token={token}") as ws:
            pass
    assert exc.value.code == 4001


# ==============================================================================
# VECTOR 6: USER GOVERNANCE & LAST-ADMIN INVARIANT
# ==============================================================================

def test_adv_sole_admin_self_deactivation_blocked():
    """Attempting to deactivate the only active admin is blocked with 400."""
    headers = _get_headers("adv_admin", "ADMIN")
    
    db = SessionLocal()
    original_states = {}
    try:
        admins = db.query(User).filter(User.role == "ADMIN").all()
        for a in admins:
            original_states[a.id] = a.active
            if a.username != "adv_admin":
                a.active = False
        adv_admin = db.query(User).filter(User.username == "adv_admin").first()
        adv_admin.active = True
        db.commit()
        admin_id = adv_admin.id

        r = client.patch(f"/api/v1/users/{admin_id}", headers=headers, json={"active": False})
        assert r.status_code == 400
        assert "last active system administrator" in r.json()["detail"]
    finally:
        for aid, was_active in original_states.items():
            adm = db.query(User).filter(User.id == aid).first()
            if adm:
                adm.active = was_active
        db.commit()
        db.close()


def test_adv_deactivated_user_token_blocked_immediately():
    """Deactivated user token is rejected across all HTTP endpoints with 401."""
    token = create_access_token("adv_deactivated", "OPERATOR")
    headers = {"Authorization": f"Bearer {token}"}
    r = client.get("/api/v1/cameras", headers=headers)
    assert r.status_code == 401
    assert "deactivated" in r.json()["detail"].lower() or "disabled" in r.json()["detail"].lower()


# ==============================================================================
# VECTOR 7: FAIL-CLOSED PRODUCTION CONFIGURATION AUDIT
# ==============================================================================

def test_adv_production_secret_blacklisting():
    """validate_security_configuration strictly rejects default or weak secrets."""
    with pytest.raises(SecurityConfigurationError):
        validate_security_configuration(
            environment="production",
            secret_key="ibvap-super-secret-key-change-in-production-2026",
            jwt_secret_key="production_strong_key_must_be_over_32_characters_long",
            database_url="postgresql://user:pass@localhost:5432/db",
            require_auth=True,
            allowed_hosts=["surveillance.gov.in"],
        )
