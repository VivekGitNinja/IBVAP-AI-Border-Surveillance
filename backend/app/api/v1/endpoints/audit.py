"""Audit trail endpoints."""

from __future__ import annotations
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.db.session import get_db
from backend.app.schemas.common import AuditLogOut
from backend.app.services.audit import get_audit_trail, verify_audit_chain
from backend.app.api.deps import current_user, require_permission

router = APIRouter()


@router.get("", response_model=list[AuditLogOut])
def list_audit_logs(
    target_type: str | None = None,
    target_id: str | None = None,
    actor: str | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("view_audit")),
):
    """Query audit trail with optional filters."""
    return get_audit_trail(db, target_type, target_id, actor, limit)


@router.get("/verify")
def verify_chain(
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("view_audit")),
):
    """Verify audit log chain integrity."""
    return verify_audit_chain(db)
