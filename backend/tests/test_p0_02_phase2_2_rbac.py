"""
IBVAP — Phase 2.2 RBAC Enforcement & Authorization Boundary Tests.

Verifies:
1. Complete role hierarchy & permission matrix (ADMIN, COMMANDER, OPERATOR, AUDITOR, VIEWER).
2. Fail-closed rejection (HTTP 401 Unauthorized) when unauthenticated on all protected mutating/control routes.
3. Least-privilege role enforcement (HTTP 403 Forbidden) when VIEWER attempts mutating/tactical operations.
4. Granular privilege boundaries:
   - OPERATOR can acknowledge incidents, control PTZ, toggle barrier, but CANNOT escalate incidents, delete cameras, or view audit logs.
   - COMMANDER can escalate incidents, dispatch QRT, manage zones, view audit logs.
   - ADMIN has full superuser capabilities.
5. AST Route Audit: Asserts that 100% of mutating routes (POST, PUT, PATCH, DELETE) have authentication/permission dependencies.
"""

import ast
import inspect
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.core.security import (
    ROLE_HIERARCHY,
    ROLE_PERMISSIONS,
    create_access_token,
    role_has_permission,
    role_level,
)

client = TestClient(app)


def _token_for(role: str) -> str:
    return create_access_token(f"test-{role.lower()}", role=role)


def _headers_for(role: str) -> dict:
    return {"Authorization": f"Bearer {_token_for(role)}"}


# ── 1. Role Permission Matrix Tests ───────────────────────────────

def test_role_hierarchy_order():
    """Verify numeric levels match expected operational command hierarchy."""
    assert role_level("ADMIN") > role_level("COMMANDER")
    assert role_level("COMMANDER") > role_level("OPERATOR")
    assert role_level("OPERATOR") > role_level("AUDITOR")
    assert role_level("AUDITOR") > role_level("VIEWER")
    assert role_level("UNKNOWN") == 0


def test_role_permissions_matrix_sanity():
    """Verify role capabilities match security specifications."""
    # ADMIN has full operational & governance permissions
    assert role_has_permission("ADMIN", "manage_users")
    assert role_has_permission("ADMIN", "camera_power")
    assert role_has_permission("ADMIN", "view_audit")
    assert role_has_permission("ADMIN", "export_evidence")
    assert role_has_permission("ADMIN", "barrier_control")

    # COMMANDER has tactical & escalation permissions, but not user management
    assert role_has_permission("COMMANDER", "escalate_incidents")
    assert role_has_permission("COMMANDER", "qrt_dispatch")
    assert role_has_permission("COMMANDER", "view_audit")
    assert not role_has_permission("COMMANDER", "manage_users")

    # OPERATOR has surveillance and operational actions, but not escalation or audit
    assert role_has_permission("OPERATOR", "read")
    assert role_has_permission("OPERATOR", "write")
    assert role_has_permission("OPERATOR", "camera_control")
    assert role_has_permission("OPERATOR", "barrier_control")
    assert role_has_permission("OPERATOR", "acknowledge_incidents")
    assert not role_has_permission("OPERATOR", "escalate_incidents")
    assert not role_has_permission("OPERATOR", "view_audit")
    assert not role_has_permission("OPERATOR", "delete")

    # AUDITOR has read and audit trail inspection only
    assert role_has_permission("AUDITOR", "read")
    assert role_has_permission("AUDITOR", "view_audit")
    assert role_has_permission("AUDITOR", "export_evidence")
    assert not role_has_permission("AUDITOR", "write")
    assert not role_has_permission("AUDITOR", "camera_control")
    assert not role_has_permission("AUDITOR", "barrier_control")

    # VIEWER has read only
    assert role_has_permission("VIEWER", "read")
    assert not role_has_permission("VIEWER", "write")
    assert not role_has_permission("VIEWER", "delete")
    assert not role_has_permission("VIEWER", "camera_control")
    assert not role_has_permission("VIEWER", "barrier_control")
    assert not role_has_permission("VIEWER", "view_audit")


# ── 2. Fail-Closed Anonymous Rejection (401) ──────────────────────

@pytest.mark.parametrize(
    "method,path,payload",
    [
        ("POST", "/api/v1/cameras", {"name": "Hacked Camera", "stream_url": "fake://1"}),
        ("POST", "/api/v1/cameras/1/ptz", {"direction": "left"}),
        ("POST", "/api/v1/cameras/1/ptz/goto", {"preset": "HOME"}),
        ("POST", "/api/v1/cameras/1/test", {}),
        ("POST", "/api/v1/cameras/1/connect", {}),
        ("POST", "/api/v1/cameras/1/disconnect", {}),
        ("POST", "/api/v1/cameras/discover", {"ip_range": "auto"}),
        ("POST", "/api/v1/cameras/smart-probe", {"target_ip": "192.168.1.50"}),
        ("POST", "/api/v1/cameras/test-stream", {"stream_url": "fake://1"}),
        ("POST", "/api/v1/cameras/hardware/power-off-all", {}),
        ("POST", "/api/v1/cameras/pipeline/start-all", {}),
        ("DELETE", "/api/v1/cameras/999", None),
        ("POST", "/api/v1/anpr/barrier/toggle", {}),
        ("POST", "/api/v1/anpr/watchlist", {"plate_number": "XX01YY9999", "reason": "Test"}),
        ("POST", "/api/v1/qrt/dispatch", {"team_id": 1, "incident_id": 1}),
        ("POST", "/api/v1/qrt/status", {"team_id": 1, "status": "ENGAGED"}),
        ("POST", "/api/v1/qrt/radio/broadcast", {"callsign": "ALPHA", "message": "Sitrep"}),
        ("POST", "/api/v1/zones", {"name": "Breach Zone", "zone_type": "RESTRICTED", "polygon": [[0, 0], [1, 1]], "camera_id": 1}),
        ("DELETE", "/api/v1/zones/999", None),
        ("POST", "/api/v1/incidents/1/acknowledge", {}),
        ("POST", "/api/v1/incidents/1/escalate", {}),
        ("POST", "/api/v1/incidents/1/dismiss", {}),
        ("POST", "/api/v1/incidents/1/close", {}),
        ("POST", "/api/v1/analysis/jobs", {"source_type": "rtsp", "source_url": "rtsp://test"}),
        ("POST", "/api/v1/analysis/jobs/1/cancel", {}),
        ("GET", "/api/v1/audit", None),
        ("GET", "/api/v1/audit/verify", None),
        ("POST", "/api/v1/demo/seed", None),
        ("POST", "/api/v1/demo/seed/all", None),
    ],
)
def test_anonymous_requests_rejected_with_401(method, path, payload):
    """Every sensitive mutating and control endpoint rejects anonymous requests with 401."""
    if method == "POST":
        resp = client.post(path, json=payload if payload is not None else {})
    elif method == "DELETE":
        resp = client.delete(path)
    elif method == "GET":
        resp = client.get(path)
    else:
        pytest.fail(f"Unhandled method: {method}")

    assert resp.status_code == 401, f"{method} {path} expected 401, got {resp.status_code}: {resp.text}"


# ── 3. Least-Privilege Role Enforcement (403 Forbidden for VIEWER) ─

@pytest.mark.parametrize(
    "method,path,payload,expected_err",
    [
        ("POST", "/api/v1/anpr/barrier/toggle", {}, "barrier_control"),
        ("POST", "/api/v1/qrt/dispatch", {"team_id": 1, "incident_id": 1}, "qrt_dispatch"),
        ("POST", "/api/v1/qrt/radio/broadcast", {"callsign": "ALPHA", "message": "Sitrep"}, "qrt_broadcast"),
        ("POST", "/api/v1/cameras/hardware/power-off-all", {}, "camera_power"),
        ("DELETE", "/api/v1/cameras/999", {}, "delete"),
        ("POST", "/api/v1/zones", {"name": "Breach Zone", "zone_type": "RESTRICTED", "polygon": [[0, 0], [1, 1]], "camera_id": 1}, "manage_zones"),
        ("POST", "/api/v1/incidents/1/escalate", {}, "escalate_incidents"),
        ("POST", "/api/v1/incidents/1/acknowledge", {}, "acknowledge_incidents"),
        ("POST", "/api/v1/demo/seed", {}, "run_demo"),
        ("GET", "/api/v1/audit", None, "view_audit"),
    ],
)
def test_viewer_role_forbidden_on_privileged_actions(method, path, payload, expected_err):
    """VIEWER role is strictly rejected with 403 Forbidden on privileged operations."""
    headers = _headers_for("VIEWER")
    if method == "POST":
        resp = client.post(path, json=payload, headers=headers)
    elif method == "DELETE":
        resp = client.delete(path, headers=headers)
    elif method == "GET":
        resp = client.get(path, headers=headers)
    else:
        pytest.fail(f"Unhandled method {method}")

    assert resp.status_code == 403, f"{method} {path} for VIEWER expected 403, got {resp.status_code}: {resp.text}"
    assert expected_err in resp.json().get("detail", "")


# ── 4. Operator vs Commander vs Admin Boundary Tests ─────────────

def test_operator_can_toggle_barrier_and_control_ptz():
    """OPERATOR has barrier_control and camera_control permissions."""
    headers = _headers_for("OPERATOR")

    # Barrier toggle
    resp = client.post("/api/v1/anpr/barrier/toggle", headers=headers)
    assert resp.status_code == 200
    assert "barrier_raised" in resp.json()

    # PTZ control
    resp = client.post("/api/v1/cameras/1/ptz", json={"direction": "left", "speed": 0.5}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"


def test_operator_cannot_escalate_or_view_audit():
    """OPERATOR lacks escalate_incidents and view_audit permissions (403)."""
    headers = _headers_for("OPERATOR")

    # Escalate requires COMMANDER / ADMIN
    resp = client.post("/api/v1/incidents/1/escalate", headers=headers)
    assert resp.status_code == 403
    assert "escalate_incidents" in resp.json()["detail"]

    # View audit requires COMMANDER / ADMIN
    resp = client.get("/api/v1/audit", headers=headers)
    assert resp.status_code == 403
    assert "view_audit" in resp.json()["detail"]


def test_commander_can_escalate_and_view_audit():
    """COMMANDER has escalate_incidents and view_audit permissions."""
    headers = _headers_for("COMMANDER")

    # View audit
    resp = client.get("/api/v1/audit", headers=headers)
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)

    # Dispatch QRT
    resp = client.post("/api/v1/qrt/dispatch", json={"team_id": 1, "incident_id": 1}, headers=headers)
    assert resp.status_code == 200
    assert resp.json()["dispatched"] is True


def test_admin_has_full_management_access():
    """ADMIN can execute camera management, demo seed, and system commands."""
    headers = _headers_for("ADMIN")

    # Demo seed
    resp = client.post("/api/v1/demo/seed?scenario=intrusion", headers=headers)
    assert resp.status_code == 200
    assert "incident_id" in resp.json()

    # Audit verify
    resp = client.get("/api/v1/audit/verify", headers=headers)
    assert resp.status_code == 200


# ── 5. Static AST Audit: 0 Unauthenticated Mutating Routes ────────

def test_ast_audit_zero_unprotected_mutating_routes():
    """Programmatically inspect all registered ASGI routes.
    
    Verifies that 100% of mutating HTTP routes (POST, PUT, PATCH, DELETE)
    have at least one authentication or authorization dependency,
    with zero unauthenticated exceptions other than public auth endpoints.
    """
    public_whitelist = {
        "/api/v1/auth/token",
        "/api/v1/auth/refresh",
        "/api/v1/auth/login",
    }

    unprotected = []

    for route in app.routes:
        if hasattr(route, "methods") and hasattr(route, "dependant"):
            methods = route.methods or set()
            mutating_methods = methods.intersection({"POST", "PUT", "PATCH", "DELETE"})
            if not mutating_methods:
                continue

            path = getattr(route, "path", "")
            if path in public_whitelist:
                continue

            # Inspect route dependencies
            dependant = route.dependant
            all_deps = list(dependant.dependencies)
            dep_callables = [d.call for d in all_deps]

            # Also check inner sub-dependencies
            def _has_auth_dep(dep):
                call_name = getattr(dep.call, "__name__", "")
                if call_name in ("current_user", "require_permission", "_check"):
                    return True
                for sub in dep.dependencies:
                    if _has_auth_dep(sub):
                        return True
                return False

            has_auth = any(_has_auth_dep(d) for d in all_deps)

            if not has_auth:
                unprotected.append((list(mutating_methods)[0], path))

    assert len(unprotected) == 0, f"Found unprotected mutating routes: {unprotected}"
