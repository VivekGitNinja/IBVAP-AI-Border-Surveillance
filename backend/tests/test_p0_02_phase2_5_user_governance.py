"""
IBVAP Gate 2 — Phase 2.5 User Administration & Session Governance Test Suite.
Verifies complete user lifecycle management, password complexity policies,
last-admin protection invariants, real-time deactivation token rejection,
and brute-force lockout mechanics.
"""

import time
import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.core.security import create_access_token, hash_password
from backend.app.db.session import SessionLocal
from backend.app.models.user import User

client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def setup_phase2_5_users():
    db = SessionLocal()
    try:
        # Ensure base roles exist
        users_to_ensure = [
            ("gov_admin", "ADMIN", True),
            ("gov_commander", "COMMANDER", True),
            ("gov_operator", "OPERATOR", True),
            ("gov_viewer", "VIEWER", True),
            ("gov_victim", "OPERATOR", True),
        ]
        for username, role, active in users_to_ensure:
            u = db.query(User).filter(User.username == username).first()
            if not u:
                db.add(User(
                    username=username,
                    password_hash=hash_password("Secur3P@ssw0rd!#"),
                    role=role,
                    full_name=f"Gov {role}",
                    active=active,
                ))
            else:
                u.active = active
                u.role = role
        db.commit()
    finally:
        db.close()


def _get_headers(username: str, role: str) -> dict:
    token = create_access_token(username, role)
    return {"Authorization": f"Bearer {token}"}


# ==============================================================================
# 1. USER LISTING & RBAC RESTRICTIONS
# ==============================================================================

def test_list_users_anonymous_rejected():
    """Unauthenticated request to /api/v1/users returns 401."""
    r = client.get("/api/v1/users")
    assert r.status_code == 401


def test_list_users_non_admin_forbidden():
    """Non-admin roles (COMMANDER, OPERATOR, VIEWER) receive 403 Forbidden."""
    for role in ("COMMANDER", "OPERATOR", "VIEWER", "AUDITOR"):
        headers = _get_headers(f"gov_{role.lower()}", role)
        r = client.get("/api/v1/users", headers=headers)
        assert r.status_code == 403
        assert "manage_users" in r.json()["detail"]


def test_list_users_admin_success():
    """ADMIN role successfully lists all system users."""
    headers = _get_headers("gov_admin", "ADMIN")
    r = client.get("/api/v1/users", headers=headers)
    assert r.status_code == 200
    users = r.json()
    assert isinstance(users, list)
    assert any(u["username"] == "gov_admin" for u in users)


# ==============================================================================
# 2. USER CREATION & PASSWORD COMPLEXITY ENFORCEMENT
# ==============================================================================

def test_create_user_short_password_rejected():
    """Password under 10 characters is rejected with 400."""
    headers = _get_headers("gov_admin", "ADMIN")
    payload = {
        "username": "short_user",
        "password": "Sh0rt!",
        "role": "OPERATOR",
    }
    r = client.post("/api/v1/users", headers=headers, json=payload)
    assert r.status_code == 400
    assert "at least 10 characters" in r.json()["detail"]


def test_create_user_trivial_password_rejected():
    """Trivial dictionary password is rejected with 400."""
    headers = _get_headers("gov_admin", "ADMIN")
    payload = {
        "username": "trivial_user",
        "password": "password123",
        "role": "OPERATOR",
    }
    r = client.post("/api/v1/users", headers=headers, json=payload)
    assert r.status_code == 400
    assert "trivial" in r.json()["detail"].lower() or "common" in r.json()["detail"].lower()


def test_create_user_missing_character_types():
    """Passwords lacking uppercase, digit, or special character are rejected with 400."""
    headers = _get_headers("gov_admin", "ADMIN")
    
    # Missing special character
    r1 = client.post("/api/v1/users", headers=headers, json={
        "username": "no_special",
        "password": "Password12345",
        "role": "OPERATOR",
    })
    assert r1.status_code == 400
    assert "special character" in r1.json()["detail"]

    # Missing uppercase
    r2 = client.post("/api/v1/users", headers=headers, json={
        "username": "no_upper",
        "password": "password!12345",
        "role": "OPERATOR",
    })
    assert r2.status_code == 400
    assert "uppercase" in r2.json()["detail"]


def test_create_user_success_and_password_hashed():
    """Valid user creation succeeds and password is stored hashed (not plaintext)."""
    headers = _get_headers("gov_admin", "ADMIN")
    payload = {
        "username": "gov_officer_new",
        "password": "St0ngP@ssw0rd!#2026",
        "role": "OPERATOR",
        "full_name": "New Tactical Officer",
    }
    r = client.post("/api/v1/users", headers=headers, json=payload)
    assert r.status_code == 201
    data = r.json()
    assert data["username"] == "gov_officer_new"
    assert data["role"] == "OPERATOR"
    assert data["active"] is True
    assert "password" not in data
    assert "password_hash" not in data

    # Verify DB state
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.username == "gov_officer_new").first()
        assert u is not None
        assert u.password_hash != "St0ngP@ssw0rd!#2026"
        assert u.password_hash.startswith("pbkdf2$")
    finally:
        db.close()


def test_create_user_duplicate_username_rejected():
    """Duplicate username creation fails with 409 Conflict."""
    headers = _get_headers("gov_admin", "ADMIN")
    payload = {
        "username": "gov_admin",
        "password": "St0ngP@ssw0rd!#2026",
        "role": "OPERATOR",
    }
    r = client.post("/api/v1/users", headers=headers, json=payload)
    assert r.status_code == 409
    assert "already exists" in r.json()["detail"]


# ==============================================================================
# 3. USER MODIFICATION & LAST ADMIN PROTECTION
# ==============================================================================

def test_update_user_attributes():
    """Admin can update user full name and role."""
    headers = _get_headers("gov_admin", "ADMIN")
    
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.username == "gov_victim").first()
        user_id = u.id
    finally:
        db.close()

    r = client.patch(f"/api/v1/users/{user_id}", headers=headers, json={
        "full_name": "Updated Gov Victim",
        "role": "COMMANDER",
    })
    assert r.status_code == 200
    assert r.json()["full_name"] == "Updated Gov Victim"
    assert r.json()["role"] == "COMMANDER"


def test_prevent_last_admin_deactivation():
    """Attempting to deactivate or demote the sole remaining admin must be rejected."""
    headers = _get_headers("gov_admin", "ADMIN")

    db = SessionLocal()
    original_states = {}
    try:
        # Keep gov_admin active and make any other admin inactive
        admins = db.query(User).filter(User.role == "ADMIN").all()
        for a in admins:
            original_states[a.id] = a.active
            if a.username != "gov_admin":
                a.active = False
        gov_admin = db.query(User).filter(User.username == "gov_admin").first()
        gov_admin.active = True
        db.commit()
        admin_id = gov_admin.id

        # Attempt to deactivate sole admin
        r1 = client.patch(f"/api/v1/users/{admin_id}", headers=headers, json={"active": False})
        assert r1.status_code == 400
        assert "last active system administrator" in r1.json()["detail"]

        # Attempt to demote sole admin to VIEWER
        r2 = client.patch(f"/api/v1/users/{admin_id}", headers=headers, json={"role": "VIEWER"})
        assert r2.status_code == 400
        assert "last active system administrator" in r2.json()["detail"]

        # Attempt to delete sole admin
        r3 = client.delete(f"/api/v1/users/{admin_id}", headers=headers)
        assert r3.status_code == 400
        assert "last active system administrator" in r3.json()["detail"]
    finally:
        for aid, was_active in original_states.items():
            adm = db.query(User).filter(User.id == aid).first()
            if adm:
                adm.active = was_active
        db.commit()
        db.close()


# ==============================================================================
# 4. USER DEACTIVATION & IMMEDIATE TOKEN REVOCATION
# ==============================================================================

def test_user_deactivation_token_revocation():
    """Deactivating a user immediately invalidates all existing JWT tokens."""
    admin_headers = _get_headers("gov_admin", "ADMIN")
    
    # Create active test victim and ensure admin is active
    db = SessionLocal()
    try:
        admin = db.query(User).filter(User.username == "gov_admin").first()
        if admin:
            admin.active = True
        u = db.query(User).filter(User.username == "gov_victim").first()
        u.active = True
        db.commit()
        victim_id = u.id
    finally:
        db.close()

    victim_token = create_access_token("gov_victim", "OPERATOR")
    victim_headers = {"Authorization": f"Bearer {victim_token}"}

    # Verify token works when active
    r_before = client.get("/api/v1/cameras", headers=victim_headers)
    assert r_before.status_code == 200

    # Deactivate user via DELETE /api/v1/users/{id}
    del_r = client.delete(f"/api/v1/users/{victim_id}", headers=admin_headers)
    assert del_r.status_code == 200
    assert del_r.json()["active"] is False

    # Existing token must immediately return 401 across endpoints
    r_after = client.get("/api/v1/cameras", headers=victim_headers)
    assert r_after.status_code == 401
    assert "deactivated" in r_after.json()["detail"].lower() or "disabled" in r_after.json()["detail"].lower()


# ==============================================================================
# 5. ACCOUNT LOCKOUT & SESSION GOVERNANCE
# ==============================================================================

def test_account_lockout_after_failed_attempts():
    """5 consecutive failed logins trigger 15-minute account lockout (HTTP 429)."""
    username = "attacker_target_user"
    
    # Ensure user exists
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.username == username).first()
        if not u:
            db.add(User(
                username=username,
                password_hash=hash_password("ValidPassword123!#"),
                role="OPERATOR",
                active=True,
            ))
            db.commit()
    finally:
        db.close()

    # 5 failed login attempts
    for _ in range(5):
        r = client.post("/api/v1/auth/token", data={"username": username, "password": "WrongPassword123!"})
        assert r.status_code in (401, 429)

    # 6th attempt must be rejected with 429
    r_locked = client.post("/api/v1/auth/token", data={"username": username, "password": "WrongPassword123!"})
    assert r_locked.status_code == 429
    assert "locked" in r_locked.json()["detail"].lower()

    # Attempt with CORRECT password during lockout must STILL return 429
    r_correct_during_lockout = client.post("/api/v1/auth/token", data={"username": username, "password": "ValidPassword123!#"})
    assert r_correct_during_lockout.status_code == 429
    assert "locked" in r_correct_during_lockout.json()["detail"].lower()
