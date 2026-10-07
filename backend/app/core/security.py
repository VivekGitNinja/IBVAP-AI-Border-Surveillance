"""
Security module — authentication, authorization, and password hashing.

Uses PBKDF2-HMAC-SHA256 for password hashing (Python 3.9 compatible).
Roles: ADMIN, COMMANDER, OPERATOR, AUDITOR, VIEWER
"""

from __future__ import annotations
import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
import uuid

import jwt

from backend.app.core.config import settings


TARGET_PBKDF2_ITERATIONS = 600000


def hash_password(password: str, iterations: int = TARGET_PBKDF2_ITERATIONS) -> str:
    """Hash a password with PBKDF2-HMAC-SHA256 (OWASP recommended work factor)."""
    if not isinstance(password, str) or not password:
        raise ValueError("Password cannot be empty")
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2${iterations}${salt.hex()}${dk.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    """Verify a password against its hash with constant-time comparison."""
    try:
        parts = encoded.split("$")
        if len(parts) == 4 and parts[0] == "pbkdf2":
            iterations = int(parts[1])
            if iterations < 1000:
                return False
            salt = bytes.fromhex(parts[2])
            dk_hex = parts[3]
            dk_len = len(bytes.fromhex(dk_hex))
            calc = hashlib.pbkdf2_hmac(
                "sha256", password.encode("utf-8"), salt, iterations, dklen=dk_len
            )
            return hmac.compare_digest(calc, bytes.fromhex(dk_hex))
        # Legacy scrypt / fallback format
        _, n, r, p, salt_hex, dk_hex = parts
        salt = bytes.fromhex(salt_hex)
        dk_len = len(bytes.fromhex(dk_hex))
        calc = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100000, dklen=dk_len)
        return hmac.compare_digest(calc, bytes.fromhex(dk_hex))
    except Exception:
        return False


def needs_rehash(encoded: str, target_iterations: int = TARGET_PBKDF2_ITERATIONS) -> bool:
    """Check whether an existing password hash should be upgraded to modern parameters."""
    try:
        parts = encoded.split("$")
        if len(parts) == 4 and parts[0] == "pbkdf2":
            iterations = int(parts[1])
            return iterations < target_iterations
        return True
    except Exception:
        return True


# ── JWT Tokens ────────────────────────────────────────────────

def create_access_token(
    subject: str,
    role: str,
    expires_delta: Optional[timedelta] = None,
    token_type: str = "access",
) -> str:
    """Create a signed JWT access token with defense-in-depth standard claims."""
    now = datetime.now(timezone.utc)
    if expires_delta:
        exp = now + expires_delta
    else:
        exp = now + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {
        "sub": str(subject),
        "role": role,
        "type": token_type,
        "iss": "ibvap-auth",
        "aud": "ibvap-api",
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": exp,
    }
    return jwt.encode(
        payload,
        settings.jwt_secret,
        algorithm="HS256",
    )


def create_refresh_token(
    subject: str,
    role: str = "VIEWER",
    expires_delta: Optional[timedelta] = None,
) -> str:
    """Create a signed JWT refresh token with type='refresh'."""
    return create_access_token(
        subject=subject,
        role=role,
        expires_delta=expires_delta or timedelta(days=7),
        token_type="refresh",
    )


def create_streaming_ticket(
    subject: str,
    scope: str,
    role: str = "VIEWER",
    expires_seconds: int = 300,
) -> str:
    """Create a short-lived scoped ticket for browser media or WebSocket streaming."""
    now = datetime.now(timezone.utc)
    exp = now + timedelta(seconds=expires_seconds)
    payload = {
        "sub": str(subject),
        "role": role,
        "type": "stream_ticket",
        "scope": scope,
        "iss": "ibvap-auth",
        "aud": "ibvap-api",
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": exp,
    }
    return jwt.encode(
        payload,
        settings.jwt_secret,
        algorithm="HS256",
    )


def verify_streaming_ticket(token: str, expected_scope: str = "") -> dict[str, Any]:
    """Verify a short-lived streaming ticket or general access token against expected scope."""
    payload = jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=["HS256"],
        options={"verify_aud": False, "verify_iss": False},
    )
    if "iss" in payload and payload.get("iss") != "ibvap-auth":
        raise jwt.InvalidIssuerError(f"Invalid ticket issuer: {payload.get('iss')}")
    if "aud" in payload and payload.get("aud") != "ibvap-api":
        raise jwt.InvalidAudienceError(f"Invalid ticket audience: {payload.get('aud')}")
    token_type = payload.get("type")
    # General access tokens are permitted for streaming
    if token_type == "access":
        return payload
    if token_type != "stream_ticket":
        raise jwt.InvalidTokenError(f"Invalid token type for streaming: {token_type}")

    if expected_scope:
        ticket_scope = payload.get("scope", "")
        if not ticket_scope:
            raise jwt.InvalidTokenError("Streaming ticket missing required scope claim")
        # Exact match or wildcard hierarchy match
        if ticket_scope != expected_scope:
            prefix = expected_scope.split(":")[0]
            if ticket_scope not in (f"{prefix}:*", f"{prefix}:all", "*", "all"):
                raise jwt.InvalidTokenError(
                    f"Streaming ticket scope mismatch: expected '{expected_scope}', got '{ticket_scope}'"
                )
    return payload


def decode_access_token(
    token: str,
    verify_type: Optional[str] = "access",
    expected_issuer: Optional[str] = "ibvap-auth",
    expected_audience: Optional[str] = "ibvap-api",
) -> dict[str, Any]:
    """Decode and verify a JWT access token with strict claims and algorithm enforcement."""
    payload = jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=["HS256"],
        options={"verify_aud": False, "verify_iss": False},
    )
    if expected_issuer is not None and "iss" in payload:
        if payload.get("iss") != expected_issuer:
            raise jwt.InvalidIssuerError(
                f"Invalid token issuer: expected '{expected_issuer}', got '{payload.get('iss')}'"
            )
    if expected_audience is not None and "aud" in payload:
        if payload.get("aud") != expected_audience:
            raise jwt.InvalidAudienceError(
                f"Invalid token audience: expected '{expected_audience}', got '{payload.get('aud')}'"
            )
    if verify_type is not None and "type" in payload:
        if payload.get("type") != verify_type:
            raise jwt.InvalidTokenError(
                f"Invalid token type: expected {verify_type}, got {payload.get('type')}"
            )
    return payload


# ── Role-Based Access Control ─────────────────────────────────

ROLE_HIERARCHY = {
    "ADMIN": 5,
    "COMMANDER": 4,
    "OPERATOR": 3,
    "AUDITOR": 2,
    "VIEWER": 1,
}

ROLE_PERMISSIONS = {
    "ADMIN": {
        "read", "write", "delete", "manage_users", "manage_cameras",
        "camera_control", "camera_power", "manage_zones", "manage_config",
        "view_audit", "export_evidence", "acknowledge_incidents",
        "escalate_incidents", "run_demo", "barrier_control",
        "qrt_dispatch", "qrt_status", "qrt_broadcast",
    },
    "COMMANDER": {
        "read", "write", "delete", "manage_cameras",
        "camera_control", "camera_power", "manage_zones", "manage_config",
        "view_audit", "export_evidence", "acknowledge_incidents",
        "escalate_incidents", "run_demo", "barrier_control",
        "qrt_dispatch", "qrt_status", "qrt_broadcast",
    },
    "OPERATOR": {
        "read", "write", "camera_control", "barrier_control",
        "qrt_status", "qrt_broadcast", "acknowledge_incidents", "run_demo",
    },
    "AUDITOR": {
        "read", "view_audit", "export_evidence",
    },
    "VIEWER": {
        "read",
    },
}


def role_has_permission(role: str, permission: str) -> bool:
    """Check if a role has a specific permission."""
    perms = ROLE_PERMISSIONS.get(role, set())
    return permission in perms


has_permission = role_has_permission


def role_level(role: str) -> int:
    """Get numeric level for a role."""
    return ROLE_HIERARCHY.get(role, 0)
