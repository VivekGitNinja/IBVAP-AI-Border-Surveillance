"""
IBVAP — Gate 3 Pre-Production Security Hardening & Deployment Assurance Test Suite
Verifies:
1. Gate 3.1: Observability & Information Exposure (Prometheus auth, /health sanitization, /status protection)
2. Gate 3.2: Scoped Streaming Ticket Transport (isolation, scope enforcement, API route immunity)
3. Gate 3.3: Password Hashing Hardening (600,000 iterations, legacy verification, auto-rehash on login)
4. Gate 3.4: Object-Level Authorization (Analysis job cancellation cross-operator IDOR rejection)
5. Gate 3.5: SSRF TOCTOU IP Pinning & Hostname Validation
6. Gate 3.6: Unhandled Exception Masking with Trace ID Correlation
"""

import time
import pytest
from unittest.mock import patch, MagicMock
from starlette.testclient import TestClient

from backend.app.main import app
from backend.app.db.session import SessionLocal, get_db
from backend.app.models.user import User
from backend.app.models.analysis_job import AnalysisJob
from backend.app.core.security import (
    hash_password,
    verify_password,
    needs_rehash,
    create_access_token,
    create_streaming_ticket,
    verify_streaming_ticket,
    TARGET_PBKDF2_ITERATIONS,
)
from backend.app.api.v1.endpoints.cameras import validate_target_ip_and_port
from backend.app.core.config import settings


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def auth_headers_operator():
    token = create_access_token("operator", "OPERATOR")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def auth_headers_admin():
    token = create_access_token("admin", "ADMIN")
    return {"Authorization": f"Bearer {token}"}


# ═════════════════════════════════════════════════════════════════════════════
# GATE 3.1: OBSERVABILITY & INFORMATION EXPOSURE
# ═════════════════════════════════════════════════════════════════════════════

def test_gate3_metrics_endpoint_network_and_auth_guards(client):
    """Metrics endpoint: allowed from management IPs, rejected with 401 for untrusted IPs without Bearer."""
    # Localhost / management client succeeds
    res_local = client.get("/metrics")
    assert res_local.status_code == 200
    assert "process_cpu_seconds_total" in res_local.text or "# HELP" in res_local.text

    # Remote untrusted client without token -> 401
    remote_client = TestClient(app, client=("192.168.1.100", 54321))
    res_remote_anon = remote_client.get("/metrics")
    assert res_remote_anon.status_code == 401
    assert "management network" in res_remote_anon.json()["detail"].lower()

    # Remote untrusted client with valid Bearer token -> 200
    token = create_access_token("admin", "ADMIN")
    res_remote_auth = remote_client.get(
        "/metrics",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_remote_auth.status_code == 200


def test_metrics_production_external_client_cannot_impersonate_testclient():
    """
    Regression Test: In production mode, external clients cannot impersonate testclient
    or localhost, and testclient is never trusted without explicit valid Bearer authentication.
    """
    orig_env = settings.environment
    try:
        # 1. Simulate production environment
        settings.environment = "production"

        # Production client claiming to be testclient (default TestClient client host)
        # MUST be rejected with 401 when running in production
        prod_testclient = TestClient(app, client=("testclient", 50000))
        res = prod_testclient.get("/metrics")
        assert res.status_code == 401, f"Production mode must NOT trust testclient: {res.status_code}"
        assert "management network" in res.json()["detail"].lower()

        # External client attempting to spoof headers (X-Forwarded-For, Host, etc.)
        external_client = TestClient(app, client=("203.0.113.195", 54321))
        spoofed_res = external_client.get(
            "/metrics",
            headers={
                "X-Forwarded-For": "testclient",
                "X-Real-IP": "127.0.0.1",
                "Host": "testclient",
            },
        )
        assert spoofed_res.status_code == 401, "External client with spoofed headers must be rejected"

        # Authenticated caller in production succeeds
        token = create_access_token("admin", "ADMIN")
        auth_res = prod_testclient.get(
            "/metrics",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert auth_res.status_code == 200, "Authenticated production caller must succeed"

        # Loopback literal (127.0.0.1) in production succeeds
        loopback_client = TestClient(app, client=("127.0.0.1", 12345))
        res_loopback = loopback_client.get("/metrics")
        assert res_loopback.status_code == 200, "127.0.0.1 loopback literal allowed in production"

    finally:
        settings.environment = orig_env


def test_gate3_health_endpoints_exposure_and_sanitization(client, auth_headers_operator):
    """Health endpoints: /health is public liveness; /detailed sanitizes DB errors; /status requires auth."""
    # 1. Public liveness
    res = client.get("/api/v1/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "service": "ibvap-api", "version": "2.0.0"}

    # 2. Detailed health with healthy DB
    res_det = client.get("/api/v1/health/detailed")
    assert res_det.status_code == 200
    assert res_det.json()["checks"]["database"] == "ok"

    # 3. Detailed health with DB outage -> reports 'unavailable' without leaking exceptions or credentials
    with patch("backend.app.api.v1.endpoints.health.SessionLocal") as mock_sess:
        mock_db = MagicMock()
        mock_db.execute.side_effect = Exception("FATAL: password authentication failed for user 'postgres' at 10.0.0.1")
        mock_sess.return_value = mock_db
        res_fail = client.get("/api/v1/health/detailed")
        assert res_fail.status_code == 200
        data = res_fail.json()
        assert data["status"] == "degraded"
        assert data["checks"]["database"] == "unavailable"
        assert "password" not in str(data)
        assert "10.0.0.1" not in str(data)

    # 4. /status and /health/status require authentication (prevent operational telemetry disclosure)
    assert client.get("/api/v1/status").status_code == 401
    assert client.get("/api/v1/health/status").status_code == 401
    assert client.get("/api/v1/status", headers=auth_headers_operator).status_code == 200
    assert client.get("/api/v1/health/status", headers=auth_headers_operator).status_code == 200


def test_gate3_root_and_traversal_protection(client):
    """API root returns operational info; path traversal attempts fail-closed."""
    res_root = client.get("/")
    assert res_root.status_code == 200
    assert "status" in res_root.json()

    # Traversal attempts
    res_trav = client.get("/../../etc/passwd")
    assert res_trav.status_code in (400, 403, 404)


# ═════════════════════════════════════════════════════════════════════════════
# GATE 3.2: SCOPED STREAMING TICKET TRANSPORT
# ═════════════════════════════════════════════════════════════════════════════

def test_gate3_streaming_ticket_issuance_and_scope_verification(client, auth_headers_operator):
    """Streaming tickets: issued scoped, expire quickly, and rejected on scope mismatch or general APIs."""
    # 1. Anonymous ticket generation is rejected
    res_anon = client.post("/api/v1/auth/stream-ticket", json={"scope": "ws:live:1", "camera_id": 1})
    assert res_anon.status_code == 401

    # 2. Authenticated operator generates scoped streaming ticket via JSON body
    res_auth = client.post(
        "/api/v1/auth/stream-ticket",
        json={"scope": "ws:live:1", "camera_id": 1},
        headers=auth_headers_operator,
    )
    assert res_auth.status_code == 200
    ticket_data = res_auth.json()
    ticket_token = ticket_data["stream_ticket"]
    assert ticket_data["scope"] == "ws:live:1"
    assert ticket_data["expires_in"] == 300

    # 3. Validating ticket with matching scope succeeds
    payload = verify_streaming_ticket(ticket_token, expected_scope="ws:live:1")
    assert payload["sub"] == "operator"
    assert payload["scope"] == "ws:live:1"

    # 4. Validating ticket with mismatched scope fails
    with pytest.raises(Exception) as excinfo:
        verify_streaming_ticket(ticket_token, expected_scope="ws:live:2")
    assert "scope mismatch" in str(excinfo.value).lower()

    # 5. Streaming ticket CANNOT be used on regular API endpoints (prevents token escalation)
    res_api_abuse = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {ticket_token}"})
    assert res_api_abuse.status_code == 401

    # 6. Expired streaming ticket is rejected
    expired_ticket = create_streaming_ticket(
        subject="operator",
        scope="ws:live:1",
        role="OPERATOR",
        expires_seconds=-1,
    )
    with pytest.raises(Exception) as exc_exp:
        verify_streaming_ticket(expired_ticket, expected_scope="ws:live:1")
    assert "expired" in str(exc_exp.value).lower()


# ═════════════════════════════════════════════════════════════════════════════
# GATE 3.3: PASSWORD HASHING HARDENING & UPGRADE
# ═════════════════════════════════════════════════════════════════════════════

def test_gate3_password_hashing_600k_and_migration():
    """PBKDF2 uses 600k iterations; legacy 100k hashes verify and are flagged for rehash."""
    # 1. New hashes use target 600k iterations
    raw_pass = "TestSecret2026!"
    new_hash = hash_password(raw_pass)
    assert f"pbkdf2${TARGET_PBKDF2_ITERATIONS}$" in new_hash
    assert verify_password(raw_pass, new_hash) is True
    assert needs_rehash(new_hash) is False

    # 2. Legacy 100k hash
    legacy_hash = hash_password(raw_pass, iterations=100000)
    assert "pbkdf2$100000$" in legacy_hash
    # Must still verify
    assert verify_password(raw_pass, legacy_hash) is True
    # Must be flagged for rehash
    assert needs_rehash(legacy_hash) is True


def test_gate3_login_auto_upgrades_legacy_password_hash(client):
    """User login automatically upgrades legacy 100k hash to 600k hash in database."""
    db = SessionLocal()
    test_user_name = "legacy_user_test"
    try:
        # Remove if exists
        db.query(User).filter(User.username == test_user_name).delete()
        db.commit()

        # Seed user with legacy 100k hash
        legacy_h = hash_password("legacy_pass_123", iterations=100000)
        user = User(
            username=test_user_name,
            password_hash=legacy_h,
            role="OPERATOR",
            full_name="Legacy Test User",
            active=True,
        )
        db.add(user)
        db.commit()

        # Login with correct password
        login_res = client.post(
            "/api/v1/auth/token",
            data={"username": test_user_name, "password": "legacy_pass_123"},
        )
        assert login_res.status_code == 200

        # Verify DB hash was automatically upgraded to 600,000 iterations
        db.refresh(user)
        assert f"pbkdf2${TARGET_PBKDF2_ITERATIONS}$" in user.password_hash
        assert needs_rehash(user.password_hash) is False
    finally:
        db.query(User).filter(User.username == test_user_name).delete()
        db.commit()
        db.close()


# ═════════════════════════════════════════════════════════════════════════════
# GATE 3.4: OBJECT-LEVEL AUTHORIZATION
# ═════════════════════════════════════════════════════════════════════════════

def test_gate3_analysis_job_cancel_cross_operator_idor_rejection(client):
    """Operators cannot cancel analysis jobs created by other operators; Commanders/Admins can."""
    db = SessionLocal()
    job_id = None
    try:
        # Create analysis job owned by 'operator_alpha'
        job = AnalysisJob(
            source_type="upload",
            source_id=1,
            status="processing",
            created_by="operator_alpha",
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        job_id = job.id

        # Operator Beta attempts to cancel Operator Alpha's job -> 403 Forbidden
        token_beta = create_access_token("operator_beta", "OPERATOR")
        res_beta = client.post(
            f"/api/v1/analysis/jobs/{job_id}/cancel",
            headers={"Authorization": f"Bearer {token_beta}"},
        )
        assert res_beta.status_code == 403
        assert "Operators may only cancel their own" in res_beta.json()["detail"]

        # Operator Alpha can cancel their own job -> 200 OK
        token_alpha = create_access_token("operator_alpha", "OPERATOR")
        res_alpha = client.post(
            f"/api/v1/analysis/jobs/{job_id}/cancel",
            headers={"Authorization": f"Bearer {token_alpha}"},
        )
        assert res_alpha.status_code == 200
        assert res_alpha.json()["cancelled"] is True

        # Commander can cancel any job
        job.status = "processing"
        db.commit()
        token_cmd = create_access_token("border_commander", "COMMANDER")
        res_cmd = client.post(
            f"/api/v1/analysis/jobs/{job_id}/cancel",
            headers={"Authorization": f"Bearer {token_cmd}"},
        )
        assert res_cmd.status_code == 200
    finally:
        if job_id:
            job_to_del = db.get(AnalysisJob, job_id)
            if job_to_del:
                db.delete(job_to_del)
                db.commit()
        db.close()


# ═════════════════════════════════════════════════════════════════════════════
# GATE 3.5: SSRF TOCTOU IP PINNING & NETWORK GUARDS
# ═════════════════════════════════════════════════════════════════════════════

def test_gate3_ssrf_ip_validation_and_prohibited_targets():
    """Validates that loopback, cloud metadata, link-local, and unauthorized ports are blocked."""
    # Loopback blocked
    with pytest.raises(Exception) as exc:
        validate_target_ip_and_port("127.0.0.1", 554)
    assert "Loopback" in str(exc.value)

    # Cloud metadata blocked
    with pytest.raises(Exception) as exc:
        validate_target_ip_and_port("169.254.169.254", 80)
    assert "prohibited" in str(exc.value).lower() or "link-local" in str(exc.value).lower()

    # Unauthorized port blocked
    with pytest.raises(Exception) as exc:
        validate_target_ip_and_port("192.168.1.50", 22)
    assert "authorized surveillance streaming port" in str(exc.value)

    # Valid IP on allowed port passes
    res_ip = validate_target_ip_and_port("192.168.1.50", 554)
    assert res_ip == "192.168.1.50"


# ═════════════════════════════════════════════════════════════════════════════
# GATE 3.6: UNHANDLED EXCEPTION MASKING WITH TRACE ID
# ═════════════════════════════════════════════════════════════════════════════

def test_gate3_unhandled_exception_masking(client, auth_headers_admin):
    """Unhandled internal server error returns sanitized JSON with trace_id, never leaking code or traces."""
    def crash_db():
        raise RuntimeError("psycopg2.OperationalError: server closed connection unexpectedly at /var/run/postgresql/.s.PGSQL.5432")

    app.dependency_overrides[get_db] = crash_db
    try:
        res = client.get("/api/v1/cameras", headers=auth_headers_admin)
        assert res.status_code == 500
        data = res.json()
        assert "detail" in data
        assert "trace_id" in data
        assert "Internal server error" in data["detail"]
        assert "psycopg2" not in str(data)
        assert "/var/run/postgresql" not in str(data)
        assert "X-Trace-ID" in res.headers
    finally:
        app.dependency_overrides.pop(get_db, None)
