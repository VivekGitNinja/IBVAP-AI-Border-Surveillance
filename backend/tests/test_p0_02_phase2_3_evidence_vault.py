"""
IBVAP Gate 2 — Phase 2.3 Evidence Vault & Media Security Adversarial Test Suite.
Verifies removal of unauthenticated static mounts, strict path traversal prevention,
cryptographic SHA-256 Section 63 BSA audit trail logging, Range streaming, and upload sanitization.
"""

import os
import tempfile
import cv2
import numpy as np
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.core.config import settings
from backend.app.core.security import create_access_token
from backend.app.db.session import SessionLocal
from backend.app.models.audit import AuditLog
from backend.app.models.user import User

client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def ensure_test_users():
    db = SessionLocal()
    try:
        from backend.app.core.security import hash_password
        users_to_ensure = [
            ("operator_v3", "OPERATOR"),
            ("viewer_v3", "VIEWER"),
            ("admin_v3", "ADMIN"),
        ]
        for username, role in users_to_ensure:
            existing = db.query(User).filter(User.username == username).first()
            if not existing:
                db.add(User(
                    username=username,
                    password_hash=hash_password("password123"),
                    role=role,
                    full_name=f"Test {role}",
                    active=True,
                ))
        db.commit()
    finally:
        db.close()


def _get_token(username: str = "operator_v3", role: str = "OPERATOR") -> str:
    return create_access_token(username, role)


def _get_auth_headers(username: str = "operator_v3", role: str = "OPERATOR") -> dict:
    return {"Authorization": f"Bearer {_get_token(username, role)}"}


@pytest.fixture
def vault_test_file():
    """Create a temporary genuine test file inside settings.evidence_dir/clips/."""
    vault_root = Path(settings.evidence_dir).resolve()
    clips_dir = vault_root / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    
    test_file = clips_dir / "gate2_vault_evidence_test.mp4"
    # Create valid synthetic video file
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out = cv2.VideoWriter(str(test_file), fourcc, 10.0, (160, 120))
    for _ in range(5):
        frame = np.zeros((120, 160, 3), dtype=np.uint8)
        out.write(frame)
    out.release()
    
    yield test_file
    
    if test_file.exists():
        test_file.unlink()


# ==============================================================================
# 1. REMOVAL OF UNAUTHENTICATED STATIC MOUNT
# ==============================================================================

def test_unauthenticated_static_mount_removed():
    """Direct unauthenticated access to /data/evidence must return 404."""
    r = client.get("/data/evidence/clips/some_clip.mp4")
    assert r.status_code == 404, f"Expected 404, got {r.status_code}"


def test_unauthenticated_data_root_blocked():
    """Access to /data or /data/ must return 404, never list directories or serve SPA."""
    r = client.get("/data")
    assert r.status_code == 404
    r2 = client.get("/data/")
    assert r2.status_code == 404


# ==============================================================================
# 2. AUTHENTICATED EVIDENCE VAULT ENDPOINT
# ==============================================================================

def test_evidence_vault_requires_auth(vault_test_file):
    """Accessing vault without credentials returns 401 Unauthorized."""
    rel_path = f"clips/{vault_test_file.name}"
    r = client.get(f"/api/v1/evidence/vault/{rel_path}")
    assert r.status_code == 401


def test_evidence_vault_invalid_token(vault_test_file):
    """Accessing vault with forged/invalid token returns 401 Unauthorized."""
    rel_path = f"clips/{vault_test_file.name}"
    r = client.get(
        f"/api/v1/evidence/vault/{rel_path}",
        headers={"Authorization": "Bearer forged.invalid.token"}
    )
    assert r.status_code == 401


def test_evidence_vault_bearer_auth_success(vault_test_file):
    """Authorized user with Bearer token can access evidence with proper headers."""
    rel_path = f"clips/{vault_test_file.name}"
    headers = _get_auth_headers("operator_v3", "OPERATOR")
    r = client.get(f"/api/v1/evidence/vault/{rel_path}", headers=headers)
    assert r.status_code == 200
    assert r.headers.get("content-type") == "video/mp4"
    assert "Accept-Ranges" in r.headers
    assert len(r.headers.get("X-Evidence-SHA256", "")) == 64
    assert r.headers.get("X-Statutory-Compliance") == "Bharatiya Sakshya Adhiniyam, 2023 Section 63"
    assert len(r.content) > 0


def test_evidence_vault_query_token_success(vault_test_file):
    """Browsers loading <img> or <video> with ?token= query parameter authenticate successfully."""
    rel_path = f"clips/{vault_test_file.name}"
    token = _get_token("operator_v3", "OPERATOR")
    r = client.get(f"/api/v1/evidence/vault/{rel_path}?token={token}")
    assert r.status_code == 200
    assert r.headers.get("content-type") == "video/mp4"
    assert len(r.content) > 0


def test_evidence_vault_range_request(vault_test_file):
    """Video players requesting byte ranges receive 206 Partial Content."""
    rel_path = f"clips/{vault_test_file.name}"
    headers = _get_auth_headers("operator_v3", "OPERATOR")
    headers["Range"] = "bytes=0-15"
    r = client.get(f"/api/v1/evidence/vault/{rel_path}", headers=headers)
    assert r.status_code == 206
    assert len(r.content) == 16
    assert "bytes 0-15/" in r.headers.get("Content-Range", "")


# ==============================================================================
# 3. PATH TRAVERSAL & ADVERSARIAL ESCAPE PREVENTION
# ==============================================================================

def test_evidence_vault_traversal_dotdot_rejected():
    """Attempting path traversal with ../ returns 403 Forbidden."""
    headers = _get_auth_headers("operator_v3", "OPERATOR")
    r = client.get("/api/v1/evidence/vault/../../etc/passwd", headers=headers)
    assert r.status_code in (403, 404)


def test_evidence_vault_traversal_urlencoded_dotdot():
    """Attempting path traversal with %2e%2e returns 403 Forbidden."""
    headers = _get_auth_headers("operator_v3", "OPERATOR")
    r = client.get("/api/v1/evidence/vault/%2e%2e%2f%2e%2e%2fetc/passwd", headers=headers)
    assert r.status_code in (403, 404)


def test_evidence_vault_null_byte_rejected():
    """Null-byte injection in path returns 400 Bad Request."""
    headers = _get_auth_headers("operator_v3", "OPERATOR")
    r = client.get("/api/v1/evidence/vault/clips/sample.mp4%00.jpg", headers=headers)
    assert r.status_code == 400
    assert "null byte" in r.json()["detail"].lower()


def test_evidence_vault_symlink_escape_blocked(tmp_path):
    """Symlinks inside the vault pointing outside vault root must be rejected with 403."""
    vault_root = Path(settings.evidence_dir).resolve()
    outside_target = tmp_path / "secret_host_file.txt"
    outside_target.write_text("SENSITIVE_DATA_OUTSIDE_VAULT")
    
    symlink_path = vault_root / "symlink_escape_probe.txt"
    try:
        os.symlink(str(outside_target), str(symlink_path))
        headers = _get_auth_headers("operator_v3", "OPERATOR")
        r = client.get("/api/v1/evidence/vault/symlink_escape_probe.txt", headers=headers)
        assert r.status_code == 403
        assert "Access denied" in r.json()["detail"]
    finally:
        if symlink_path.is_symlink() or symlink_path.exists():
            symlink_path.unlink()


def test_evidence_vault_nonexistent_file():
    """Requesting non-existent file inside vault returns 404 Not Found."""
    headers = _get_auth_headers("operator_v3", "OPERATOR")
    r = client.get("/api/v1/evidence/vault/clips/definitely_does_not_exist_99999.mp4", headers=headers)
    assert r.status_code == 404


# ==============================================================================
# 4. SECTION 63 BSA AUDIT TRAIL LOGGING
# ==============================================================================

def test_evidence_vault_bsa_audit_trail(vault_test_file):
    """Reading an evidence file must generate an immutable Section 63 BSA audit log entry."""
    headers = _get_auth_headers("operator_v3", "OPERATOR")
    rel_path = f"clips/{vault_test_file.name}"
    
    db = SessionLocal()
    try:
        count_before = db.query(AuditLog).filter(
            AuditLog.action == "READ_EVIDENCE_VAULT",
            AuditLog.target_id == rel_path,
        ).count()
        
        r = client.get(f"/api/v1/evidence/vault/{rel_path}", headers=headers)
        assert r.status_code == 200
        
        log_entry = db.query(AuditLog).filter(
            AuditLog.action == "READ_EVIDENCE_VAULT",
            AuditLog.target_id == rel_path,
        ).order_by(AuditLog.id.desc()).first()
        
        assert log_entry is not None
        assert log_entry.actor == "operator_v3"
        assert log_entry.actor_role == "OPERATOR"
        assert log_entry.target_type == "evidence"
        assert log_entry.entry_hash is not None and len(log_entry.entry_hash) == 64
        
        details = log_entry.details
        assert details.get("statutory_compliance") == "Bharatiya Sakshya Adhiniyam, 2023 §63"
        assert len(details.get("sha256", "")) == 64
    finally:
        db.close()


# ==============================================================================
# 5. MEDIA UPLOAD HARDENING & INTEGRITY CHECKS
# ==============================================================================

def test_media_upload_rejects_executable_renamed_to_mp4():
    """Windows PE (.exe) binary disguised as .mp4 must be rejected with 400."""
    headers = _get_auth_headers("operator_v3", "OPERATOR")
    payload = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00\xff\xff\x00\x00" + (b"\x00" * 200)
    files = {"file": ("trojan_recording.mp4", payload, "video/mp4")}
    r = client.post("/api/v1/media/upload", headers=headers, files=files)
    assert r.status_code == 400
    assert "executable" in r.json()["detail"].lower()


def test_media_upload_rejects_elf_binary():
    """Linux ELF binary disguised as .mp4 must be rejected with 400."""
    headers = _get_auth_headers("operator_v3", "OPERATOR")
    payload = b"\x7fELF\x02\x01\x01\x00" + (b"\x00" * 200)
    files = {"file": ("rootkit_capture.mp4", payload, "video/mp4")}
    r = client.post("/api/v1/media/upload", headers=headers, files=files)
    assert r.status_code == 400
    assert "elf" in r.json()["detail"].lower()


def test_media_upload_rejects_shell_script():
    """Shell script disguised as .mp4 must be rejected with 400."""
    headers = _get_auth_headers("operator_v3", "OPERATOR")
    payload = b"#!/bin/bash\necho 'Compromised'\n"
    files = {"file": ("exploit.mp4", payload, "video/mp4")}
    r = client.post("/api/v1/media/upload", headers=headers, files=files)
    assert r.status_code == 400
    assert "script" in r.json()["detail"].lower()


def test_media_upload_rejects_html_xss():
    """HTML / XSS payload disguised as .mp4 must be rejected with 400."""
    headers = _get_auth_headers("operator_v3", "OPERATOR")
    payload = b"<!DOCTYPE html><html><head><script>alert(document.cookie)</script></head></html>"
    files = {"file": ("xss_payload.mp4", payload, "video/mp4")}
    r = client.post("/api/v1/media/upload", headers=headers, files=files)
    assert r.status_code == 400
    assert "script" in r.json()["detail"].lower() or "html" in r.json()["detail"].lower()


def test_media_upload_rejects_svg_payload():
    """SVG with script disguised as .jpg or .png must be rejected with 400."""
    headers = _get_auth_headers("operator_v3", "OPERATOR")
    payload = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    files = {"file": ("probe.png", payload, "image/png")}
    r = client.post("/api/v1/media/upload", headers=headers, files=files)
    assert r.status_code == 400
    assert "script" in r.json()["detail"].lower() or "html" in r.json()["detail"].lower()


def test_media_upload_rejects_random_corrupted_bytes():
    """Random garbage bytes claiming to be .mp4 must fail container check with 400."""
    headers = _get_auth_headers("operator_v3", "OPERATOR")
    payload = b"GARBAGE_RANDOM_DATA_WITHOUT_VALID_BOX_HEADER" + (b"\xaa" * 500)
    files = {"file": ("corrupted.mp4", payload, "video/mp4")}
    r = client.post("/api/v1/media/upload", headers=headers, files=files)
    assert r.status_code == 400
    assert "invalid mp4" in r.json()["detail"].lower()


def test_media_upload_rejects_oversized_image():
    """Image upload exceeding 20MB limit must be rejected with 413."""
    headers = _get_auth_headers("operator_v3", "OPERATOR")
    # Simulate valid PNG header followed by 21MB of zeros
    png_header = b"\x89PNG\r\n\x1a\n"
    oversized_data = png_header + (b"\x00" * (21 * 1024 * 1024))
    files = {"file": ("huge_photo.png", oversized_data, "image/png")}
    r = client.post("/api/v1/media/upload", headers=headers, files=files)
    assert r.status_code == 413
    assert "exceeds maximum limit" in r.json()["detail"].lower()


def test_media_upload_legitimate_jpeg():
    """Legitimate JPEG image is successfully uploaded, probed, and saved with UUID name."""
    headers = _get_auth_headers("operator_v3", "OPERATOR")
    
    # Generate authentic JPEG in memory
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    img[:] = (0, 255, 0)
    success, encoded_jpg = cv2.imencode(".jpg", img)
    assert success
    jpg_bytes = encoded_jpg.tobytes()
    
    files = {"file": ("surveillance_target.jpg", jpg_bytes, "image/jpeg")}
    r = client.post("/api/v1/media/upload", headers=headers, files=files)
    assert r.status_code == 201
    data = r.json()
    assert data["original_filename"] == "surveillance_target.jpg"
    assert data["width"] == 100
    assert data["height"] == 100
    assert data["status"] == "READY"
    assert len(data["sha256"]) == 64
    
    # Verify cleanup in DB
    db = SessionLocal()
    try:
        from backend.app.models.media_asset import MediaAsset
        asset = db.get(MediaAsset, data["id"])
        assert asset is not None
        if os.path.exists(asset.file_path):
            os.unlink(asset.file_path)
        db.delete(asset)
        db.commit()
    finally:
        db.close()
