"""
Phase 2.1 — Core Authentication & Secret Hardening Security Test Suite.

Verifies:
- Production/staging fail-closed startup validation on missing, weak, or default secrets.
- Masking and non-disclosure of secrets in configuration representations.
- Cryptographic JWT signing, validation, claims integrity, and algorithm enforcement.
- Prevention of algorithm confusion and alg=none attacks.
- Fail-closed active user verification against database in current_user dependency.
- Deactivated and deleted account rejection with HTTP 401.
- Authentication rate-limiting, concurrency thread-safety, and lockout semantics.
"""

import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import jwt
import pytest
from fastapi import HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient

from backend.app.api.deps import current_user
from backend.app.core.config import (
    KNOWN_INSECURE_SECRETS,
    Settings,
    settings,
    validate_security_configuration,
)
from backend.app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from backend.app.db.session import SessionLocal
from backend.app.main import app
from backend.app.models.user import User

client = TestClient(app)


# ═════════════════════════════════════════════════════════════════════════════
# 1. SECRET MANAGEMENT & FAIL-CLOSED STARTUP VALIDATION
# ═════════════════════════════════════════════════════════════════════════════

def test_production_startup_fails_closed_when_jwt_secret_empty():
    """Production mode must immediately abort startup if JWT_SECRET is empty."""
    s = Settings(
        environment="production",
        jwt_secret="",
        c2_webhook_secret="strong-c2-secret-minimum-16-chars",
    )
    with pytest.raises(RuntimeError, match="JWT_SECRET must be configured"):
        validate_security_configuration(s)


def test_production_startup_fails_closed_when_jwt_secret_insecure_default():
    """Production mode must refuse to boot with known repository fallback JWT secrets."""
    for bad_secret in KNOWN_INSECURE_SECRETS:
        s = Settings(
            environment="production",
            jwt_secret=bad_secret,
            c2_webhook_secret="strong-c2-secret-minimum-16-chars",
        )
        with pytest.raises(RuntimeError, match="insecure repository default"):
            validate_security_configuration(s)


def test_production_startup_fails_closed_when_jwt_secret_too_short():
    """Production mode requires >= 32 character entropy for HMAC-SHA256."""
    s = Settings(
        environment="production",
        jwt_secret="short-key-16-ch",
        c2_webhook_secret="strong-c2-secret-minimum-16-chars",
    )
    with pytest.raises(RuntimeError, match="at least 32 characters"):
        validate_security_configuration(s)


def test_production_startup_fails_closed_when_c2_secret_empty():
    """Production mode must abort startup if C2_WEBHOOK_SECRET is empty."""
    s = Settings(
        environment="production",
        jwt_secret="strong-jwt-secret-with-plenty-of-characters-and-entropy-32-plus",
        c2_webhook_secret="",
    )
    with pytest.raises(RuntimeError, match="C2_WEBHOOK_SECRET must be configured"):
        validate_security_configuration(s)


def test_production_startup_fails_closed_when_c2_secret_insecure_default():
    """Production mode must refuse to boot with known repository fallback C2 secrets."""
    for bad_secret in KNOWN_INSECURE_SECRETS:
        s = Settings(
            environment="production",
            jwt_secret="strong-jwt-secret-with-plenty-of-characters-and-entropy-32-plus",
            c2_webhook_secret=bad_secret,
        )
        with pytest.raises(RuntimeError, match="insecure repository default"):
            validate_security_configuration(s)


def test_production_startup_fails_closed_when_c2_secret_too_short():
    """Production mode requires >= 16 characters for C2 webhook HMAC."""
    s = Settings(
        environment="production",
        jwt_secret="strong-jwt-secret-with-plenty-of-characters-and-entropy-32-plus",
        c2_webhook_secret="short",
    )
    with pytest.raises(RuntimeError, match="at least 16 characters"):
        validate_security_configuration(s)


def test_staging_startup_fails_closed_on_insecure_secrets():
    """Staging environment must enforce the same fail-closed controls as production."""
    s = Settings(
        environment="staging",
        jwt_secret="short",
        c2_webhook_secret="strong-c2-secret-minimum-16-chars",
    )
    with pytest.raises(RuntimeError):
        validate_security_configuration(s)


def test_development_startup_allows_local_dev_keys():
    """Development environment must permit local iteration without breaking dev workflow."""
    s = Settings(
        environment="development",
        jwt_secret="dev-secret",
        c2_webhook_secret="dev-c2",
    )
    # Must complete without raising RuntimeError
    validate_security_configuration(s)


def test_production_startup_succeeds_with_strong_unique_secrets():
    """Production startup succeeds when strong, unique secrets are provided."""
    s = Settings(
        environment="production",
        jwt_secret="military-grade-unique-2026-ibvap-key-with-64-bytes-entropy-secure",
        c2_webhook_secret="tactical-c2-hmac-unique-key-2026-auth",
    )
    # Validation must pass cleanly
    validate_security_configuration(s)


def test_settings_repr_and_str_mask_jwt_secret():
    """Settings string and repr outputs must redact jwt_secret to prevent log leakage."""
    s = Settings(jwt_secret="super-confidential-secret-key-12345")
    assert "super-confidential-secret-key-12345" not in repr(s)
    assert "super-confidential-secret-key-12345" not in str(s)
    assert "***REDACTED***" in repr(s)


def test_settings_repr_and_str_mask_c2_secret():
    """Settings string and repr outputs must redact c2_webhook_secret to prevent log leakage."""
    s = Settings(c2_webhook_secret="c2-secret-must-not-leak")
    assert "c2-secret-must-not-leak" not in repr(s)
    assert "c2-secret-must-not-leak" not in str(s)
    assert "***REDACTED***" in repr(s)


# ═════════════════════════════════════════════════════════════════════════════
# 2. JWT TOKEN LIFECYCLE, CRYPTOGRAPHY & CLAIMS ENFORCEMENT
# ═════════════════════════════════════════════════════════════════════════════

def test_create_access_token_injects_all_standard_claims():
    """create_access_token must inject sub, role, type, iss, aud, jti, iat, and exp."""
    token = create_access_token("operator", "OPERATOR")
    payload = decode_access_token(token)

    assert payload["sub"] == "operator"
    assert payload["role"] == "OPERATOR"
    assert payload["type"] == "access"
    assert payload["iss"] == "ibvap-auth"
    assert payload["aud"] == "ibvap-api"
    assert "jti" in payload
    assert len(payload["jti"]) == 32  # UUID hex
    assert "iat" in payload
    assert "exp" in payload
    assert payload["exp"] > payload["iat"]


def test_jwt_signature_verification_succeeds_with_correct_key():
    """A valid token decoded with the correct key returns the claims dict."""
    token = create_access_token("admin", "ADMIN")
    payload = decode_access_token(token)
    assert payload["sub"] == "admin"
    assert payload["role"] == "ADMIN"


def test_jwt_decode_fails_with_wrong_signing_key():
    """Token signed with an attacker key must be rejected by decode_access_token."""
    attacker_key = "attacker-controlled-secret-key-that-does-not-match-ibvap"
    forged_token = jwt.encode(
        {"sub": "admin", "role": "ADMIN", "type": "access", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        attacker_key,
        algorithm="HS256",
    )
    with pytest.raises(jwt.InvalidSignatureError):
        decode_access_token(forged_token)


def test_jwt_decode_fails_when_token_expired():
    """Expired token must be rejected with jwt.ExpiredSignatureError."""
    past_exp = datetime.now(timezone.utc) - timedelta(seconds=10)
    expired_token = jwt.encode(
        {"sub": "operator", "role": "OPERATOR", "type": "access", "exp": past_exp},
        settings.jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(expired_token)


def test_jwt_decode_fails_on_malformed_token_string():
    """Random garbage or non-JWT string must raise jwt.DecodeError."""
    with pytest.raises(jwt.DecodeError):
        decode_access_token("not.a.valid.jwt.payload")


def test_jwt_decode_fails_on_truncated_token():
    """Truncated token (e.g. signature stripped) must be rejected."""
    token = create_access_token("operator", "OPERATOR")
    parts = token.split(".")
    truncated = f"{parts[0]}.{parts[1]}"
    with pytest.raises(jwt.DecodeError):
        decode_access_token(truncated)


def test_jwt_rejects_algorithm_none_forgery():
    """Token with alg='none' must be rejected unconditionally."""
    forged_none_token = jwt.encode(
        {"sub": "admin", "role": "ADMIN", "type": "access", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        key="",
        algorithm="none",
    )
    with pytest.raises(jwt.InvalidAlgorithmError):
        decode_access_token(forged_none_token)


def test_jwt_rejects_asymmetric_algorithm_confusion():
    """Attempting to verify an RS256 token against HMAC must be rejected."""
    # PyJWT restricts decode to algorithms=["HS256"]
    payload = {
        "sub": "admin",
        "role": "ADMIN",
        "exp": int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()),
    }
    # Craft token header declaring RS256
    import base64
    import json
    header_b64 = base64.urlsafe_b64encode(json.dumps({"alg": "RS256", "typ": "JWT"}).encode()).decode().rstrip("=")
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    sig_b64 = base64.urlsafe_b64encode(b"0" * 32).decode().rstrip("=")
    fake_token = f"{header_b64}.{payload_b64}.{sig_b64}"
    with pytest.raises(jwt.InvalidAlgorithmError):
        decode_access_token(fake_token)


def test_jwt_enforces_token_type_access():
    """Access token decoder must accept type='access'."""
    token = create_access_token("commander", "COMMANDER", token_type="access")
    payload = decode_access_token(token, verify_type="access")
    assert payload["type"] == "access"


def test_jwt_rejects_wrong_token_type_refresh():
    """Access token decoder must reject tokens with type='refresh'."""
    refresh_token = create_access_token("commander", "COMMANDER", token_type="refresh")
    with pytest.raises(jwt.InvalidTokenError, match="Invalid token type"):
        decode_access_token(refresh_token, verify_type="access")


# ═════════════════════════════════════════════════════════════════════════════
# 3. ACCOUNT STATE, ACTIVE STATUS & CURRENT_USER DEPENDENCY
# ═════════════════════════════════════════════════════════════════════════════

def test_current_user_dependency_success_for_active_user():
    """Valid token for an active user returns authenticated payload with authoritative role."""
    token = create_access_token("operator", "OPERATOR")
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    db = SessionLocal()
    try:
        user_info = current_user(creds=creds, db=db)
        assert user_info["sub"] == "operator"
        assert user_info["role"] == "OPERATOR"
        assert "user_id" in user_info
    finally:
        db.close()


def test_current_user_dependency_rejects_missing_bearer_credentials():
    """Missing bearer credentials raises HTTP 401."""
    with pytest.raises(HTTPException) as exc:
        current_user(creds=None)
    assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert "Authentication credentials were not provided" in exc.value.detail


def test_current_user_dependency_rejects_expired_token_with_401():
    """Expired token presented to current_user raises HTTP 401 with expired detail."""
    past_exp = datetime.now(timezone.utc) - timedelta(seconds=30)
    token = jwt.encode(
        {"sub": "operator", "role": "OPERATOR", "type": "access", "exp": past_exp},
        settings.jwt_secret,
        algorithm="HS256",
    )
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    with pytest.raises(HTTPException) as exc:
        current_user(creds=creds)
    assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert "expired" in exc.value.detail.lower()


def test_current_user_dependency_rejects_tampered_token_with_401():
    """Tampered token presented to current_user raises HTTP 401."""
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="invalid.tampered.token")
    with pytest.raises(HTTPException) as exc:
        current_user(creds=creds)
    assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert "Invalid or expired token" in exc.value.detail


def test_current_user_dependency_fails_closed_on_deactivated_account():
    """Token for a deactivated user (active=False) MUST be rejected with HTTP 401."""
    db = SessionLocal()
    temp_username = f"deactivated_{uuid.uuid4().hex[:8]}"
    try:
        # Create deactivated user
        u = User(
            username=temp_username,
            password_hash=hash_password("Pass123!"),
            role="OPERATOR",
            active=False,
        )
        db.add(u)
        db.commit()

        # Mint token for the deactivated user
        token = create_access_token(temp_username, "OPERATOR")
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

        # current_user MUST reject the token because account is deactivated
        with pytest.raises(HTTPException) as exc:
            current_user(creds=creds, db=db)

        assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert "deactivated" in exc.value.detail.lower()
    finally:
        u_cleanup = db.query(User).filter(User.username == temp_username).first()
        if u_cleanup:
            db.delete(u_cleanup)
            db.commit()
        db.close()


def test_current_user_dependency_fails_closed_in_production_for_deleted_user(monkeypatch):
    """In production mode, a token for a user that does not exist in DB must be rejected with 401."""
    monkeypatch.setattr(settings, "environment", "production")
    token = create_access_token("ghost_user_does_not_exist", "OPERATOR")
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    db = SessionLocal()
    try:
        with pytest.raises(HTTPException) as exc:
            current_user(creds=creds, db=db)
        assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert "not found" in exc.value.detail.lower()
    finally:
        db.close()


def test_current_user_dependency_attaches_authoritative_db_role():
    """If a token claims 'ADMIN' but DB has 'VIEWER', the DB role MUST override."""
    db = SessionLocal()
    temp_username = f"demoted_{uuid.uuid4().hex[:8]}"
    try:
        u = User(
            username=temp_username,
            password_hash=hash_password("Pass123!"),
            role="VIEWER",  # Demoted in DB
            active=True,
        )
        db.add(u)
        db.commit()

        # Token claims stale role ADMIN
        token = create_access_token(temp_username, "ADMIN")
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

        payload = current_user(creds=creds, db=db)
        # Authoritative DB role must be applied
        assert payload["role"] == "VIEWER"
    finally:
        u_cleanup = db.query(User).filter(User.username == temp_username).first()
        if u_cleanup:
            db.delete(u_cleanup)
            db.commit()
        db.close()


# ═════════════════════════════════════════════════════════════════════════════
# 4. AUTHENTICATION ENDPOINTS, LOCKOUT & RATE LIMITING
# ═════════════════════════════════════════════════════════════════════════════

def test_auth_login_succeeds_for_active_user_with_valid_credentials():
    """Valid credentials at /api/v1/auth/login yield access_token."""
    r = client.post("/api/v1/auth/login", data={"username": "operator", "password": "operator123"})
    assert r.status_code == 200
    data = r.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"

    # Verify returned token claims
    payload = decode_access_token(data["access_token"])
    assert payload["sub"] == "operator"
    assert payload["role"] == "OPERATOR"


def test_auth_login_rejects_invalid_password():
    """Invalid password returns 401."""
    r = client.post("/api/v1/auth/login", data={"username": "operator", "password": "wrongpassword123"})
    assert r.status_code == 401
    assert "invalid credentials" in r.json()["detail"].lower()


def test_auth_login_rejects_inactive_user_with_401():
    """Inactive user attempting to login is rejected with HTTP 401."""
    db = SessionLocal()
    temp_username = f"locked_{uuid.uuid4().hex[:8]}"
    try:
        u = User(
            username=temp_username,
            password_hash=hash_password("LockedPassword123!"),
            role="OPERATOR",
            active=False,  # Inactive
        )
        db.add(u)
        db.commit()

        r = client.post(
            "/api/v1/auth/login",
            data={"username": temp_username, "password": "LockedPassword123!"},
        )
        assert r.status_code == 401
        assert "invalid credentials" in r.json()["detail"].lower()
    finally:
        u_cleanup = db.query(User).filter(User.username == temp_username).first()
        if u_cleanup:
            db.delete(u_cleanup)
            db.commit()
        db.close()


def test_auth_login_rate_limiting_triggers_429_after_consecutive_failures():
    """5 consecutive failed login attempts trigger HTTP 429 lockout on the 6th."""
    test_user = f"rate_victim_{uuid.uuid4().hex[:6]}"
    for _ in range(5):
        client.post("/api/v1/auth/login", data={"username": test_user, "password": "bad"})

    # 6th attempt must return 429
    r = client.post("/api/v1/auth/login", data={"username": test_user, "password": "bad"})
    assert r.status_code == 429
    assert "too many failed" in r.json()["detail"].lower()


def test_auth_login_rate_limiting_concurrent_attempts_thread_safety():
    """Concurrent failed login attempts must not cause race conditions in the rate limiter."""
    test_user = f"race_user_{uuid.uuid4().hex[:6]}"
    results = []

    def attempt_login():
        r = client.post("/api/v1/auth/login", data={"username": test_user, "password": "bad"})
        results.append(r.status_code)

    threads = [threading.Thread(target=attempt_login) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # All 10 concurrent requests completed and recorded their failures safely
    assert len(results) == 10
    # The very next request MUST be locked out with 429
    r_post = client.post("/api/v1/auth/login", data={"username": test_user, "password": "bad"})
    assert r_post.status_code == 429


def test_auth_token_endpoint_returns_valid_bearer_token():
    """POST /api/v1/auth/token also returns valid bearer token."""
    r = client.post("/api/v1/auth/token", data={"username": "admin", "password": "admin123"})
    assert r.status_code == 200
    token = r.json()["access_token"]
    payload = decode_access_token(token)
    assert payload["sub"] == "admin"
    assert payload["role"] == "ADMIN"
