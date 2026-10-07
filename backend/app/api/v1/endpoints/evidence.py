"""Evidence management and verification endpoints."""

from datetime import datetime
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.db.session import get_db
from backend.app.models.evidence import Evidence
from backend.app.services.evidence import verify_manifest, verify_evidence_chain
from backend.app.services.audit import log_action
from backend.app.schemas.common import EvidenceOut, EvidenceVerification
from backend.app.api.deps import current_user, require_permission

router = APIRouter()


@router.get("/vault/{path:path}")
def stream_vault_evidence(
    path: str,
    request: Request,
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("read")),
):
    """
    Securely stream or download forensic surveillance artifacts from the encrypted vault.
    Enforces authentication, Section 63 BSA audit trail logging, and strict path traversal guards.
    """
    import mimetypes
    import urllib.parse
    from backend.app.services.evidence import compute_file_hash

    # 1. Null-byte rejection
    if "\x00" in path or "%00" in path:
        raise HTTPException(status_code=400, detail="Invalid character in path: null byte detected")

    unquoted = urllib.parse.unquote(path)
    if "\x00" in unquoted:
        raise HTTPException(status_code=400, detail="Invalid character in path: null byte detected")

    # 2. Strict traversal sequence check
    norm_parts = unquoted.replace("\\", "/").split("/")
    if ".." in norm_parts or any(p.strip() == ".." for p in norm_parts):
        raise HTTPException(status_code=403, detail="Access denied: Path traversal detected")

    # 3. Clean leading prefixes if passed (e.g. data/evidence/)
    clean_path = unquoted.lstrip("/")
    if clean_path.startswith("data/evidence/"):
        clean_path = clean_path[len("data/evidence/"):]
    elif clean_path.startswith("data/"):
        clean_path = clean_path[len("data/"):]

    vault_root = Path(settings.evidence_dir).resolve()
    target_path = (vault_root / clean_path).resolve()

    # Fallback search in clips/ subdirectory if directly missing
    if not target_path.exists():
        fallback_clip = (vault_root / "clips" / clean_path).resolve()
        if fallback_clip.exists():
            target_path = fallback_clip

    # 4. Traversal check against resolved vault_root
    try:
        if not target_path.is_relative_to(vault_root):
            raise HTTPException(status_code=403, detail="Access denied: Path traversal outside evidence vault")
    except (ValueError, AttributeError):
        if not str(target_path).startswith(str(vault_root)):
            raise HTTPException(status_code=403, detail="Access denied: Path traversal outside evidence vault")

    if not target_path.is_file():
        raise HTTPException(status_code=404, detail="Evidence file not found in vault")

    # 5. Determine content type
    ext = target_path.suffix.lower()
    mime_map = {
        ".mp4": "video/mp4",
        ".webm": "video/webm",
        ".mov": "video/quicktime",
        ".avi": "video/x-msvideo",
        ".mkv": "video/x-matroska",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".json": "application/json",
        ".pdf": "application/pdf",
        ".txt": "text/plain",
    }
    media_type = mime_map.get(ext) or mimetypes.guess_type(str(target_path))[0] or "application/octet-stream"

    # 6. Cryptographic digest computation for BSA §63 audit trail
    file_sha256 = compute_file_hash(str(target_path)) or ""
    client_ip = request.client.host if (request and request.client) else "unknown"

    log_action(
        db=db,
        actor=user.get("sub", "system"),
        actor_role=user.get("role", "OPERATOR"),
        action="READ_EVIDENCE_VAULT",
        target_type="evidence",
        target_id=unquoted,
        details={
            "file_path": str(target_path),
            "sha256": file_sha256,
            "statutory_compliance": "Bharatiya Sakshya Adhiniyam, 2023 §63",
            "client_ip": client_ip,
            "timestamp": datetime.utcnow().isoformat(),
        },
        ip_address=client_ip,
    )

    return FileResponse(
        path=str(target_path),
        media_type=media_type,
        headers={
            "Accept-Ranges": "bytes",
            "X-Evidence-SHA256": file_sha256,
            "X-Statutory-Compliance": "Bharatiya Sakshya Adhiniyam, 2023 Section 63",
        },
    )


@router.get("/{incident_id}", response_model=list[EvidenceOut])
def list_evidence(
    incident_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("read")),
):
    """Get all evidence for an incident (authenticated)."""
    return (
        db.query(Evidence)
        .filter(Evidence.incident_id == incident_id)
        .order_by(Evidence.created_at)
        .all()
    )


@router.get("/{evidence_id}/verify")
@router.get("/verify/{evidence_id}")
def verify_evidence(
    evidence_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("read")),
):
    """Verify integrity of an evidence record against disk file hash (authenticated)."""
    import os
    from backend.app.services.evidence import compute_file_hash

    e = db.get(Evidence, evidence_id)
    if not e:
        raise HTTPException(404, "Evidence not found")

    computed = None
    if e.file_path and os.path.exists(e.file_path):
        computed = compute_file_hash(e.file_path)
    elif e.manifest_path and os.path.exists(e.manifest_path):
        computed = compute_file_hash(e.manifest_path)

    match = bool(computed and e.sha256 and (computed.lower() == e.sha256.lower()))

    log_action(
        db,
        user.get("sub", "system"),
        user.get("role", "OPERATOR"),
        "VERIFY",
        "evidence",
        str(evidence_id),
        {"match": match, "sha256": e.sha256, "computed": computed},
    )

    return {
        "match": match,
        "valid": match,
        "evidence_id": evidence_id,
        "stored_hash": e.sha256,
        "computed_hash": computed,
        "manifest_path": e.manifest_path,
        "file_path": e.file_path,
        "statutory_compliance": "Bharatiya Sakshya Adhiniyam, 2023 §63",
        "verified_at": datetime.utcnow().isoformat(),
    }


@router.get("/chain/{incident_id}")
def verify_evidence_chain_endpoint(
    incident_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("read")),
):
    """Verify hash-chain integrity of all evidence for an incident (authenticated)."""
    evidence = (
        db.query(Evidence)
        .filter(Evidence.incident_id == incident_id)
        .order_by(Evidence.created_at)
        .all()
    )
    records = [
        {
            "sha256": e.sha256,
            "previous_hash": getattr(e, "previous_hash", ""),
            "created_at": e.created_at.isoformat() if e.created_at else "",
        }
        for e in evidence
    ]
    return verify_evidence_chain(records)


@router.get("/certificate/{evidence_id}")
def get_section_65b_certificate(
    evidence_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("export_evidence")),
):
    """Generate Section 63 BSA electronic evidence certificate (authenticated). Implemented technical controls aligned with Bharatiya Sakshya Adhiniyam, 2023 Section 63 requirements. Per-case statutory certificate generation remains an operational/legal prerequisite."""
    from backend.app.services.evidence import generate_section_65b_certificate
    from backend.app.models.incident import Incident

    e = db.get(Evidence, evidence_id)
    if not e:
        raise HTTPException(404, "Evidence not found")

    inc = db.get(Incident, e.incident_id) if e.incident_id else None
    inc_code = inc.incident_code if inc else f"INC-{e.incident_id}"

    cert = generate_section_65b_certificate(
        evidence_id=e.id,
        incident_code=inc_code,
        sha256_digest=e.sha256,
        camera_name=e.camera_name or "BOP Surveillance Node",
    )
    return cert


@router.get("/clip/{incident_id}/{filename}")
def get_evidence_clip(
    incident_id: int,
    filename: str,
    user: dict = Depends(require_permission("read")),
):
    """Securely stream or download an evidence video clip with strict authentication and traversal guard."""
    safe_filename = Path(filename).name
    clip_dir = Path(settings.evidence_dir) / "clips"
    clip_path = clip_dir / f"incident_{incident_id}" / safe_filename
    if not clip_path.is_file():
        clip_path = clip_dir / safe_filename
        if not clip_path.is_file():
            raise HTTPException(404, "Evidence video clip not found")
    return FileResponse(clip_path, media_type="video/mp4")


