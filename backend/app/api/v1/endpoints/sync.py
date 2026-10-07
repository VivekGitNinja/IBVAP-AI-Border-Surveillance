"""Sync queue and offline status endpoints."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.db.session import get_db
from backend.app.schemas.common import SyncQueueOut
from backend.app.services.offline_queue import get_queue_status, get_pending_events
from backend.app.api.deps import current_user, require_permission

router = APIRouter()


@router.get("/status")
def sync_status(
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("read")),
):
    """Get offline queue status."""
    return get_queue_status(db)


@router.get("/pending", response_model=list[SyncQueueOut])
def list_pending(
    limit: int = 50,
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("read")),
):
    """List pending sync events."""
    return get_pending_events(db, limit)
