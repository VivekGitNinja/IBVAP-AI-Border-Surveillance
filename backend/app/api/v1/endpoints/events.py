"""Events endpoints."""

from __future__ import annotations
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.db.session import get_db
from backend.app.models.event import Event
from backend.app.schemas.common import EventOut
from backend.app.api.deps import require_permission

router = APIRouter()


@router.get("", response_model=list[EventOut])
def list_events(
    camera_id: int | None = None,
    event_type: str | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("read")),
):
    """List events with optional filters."""
    q = db.query(Event)
    if camera_id:
        q = q.filter(Event.camera_id == camera_id)
    if event_type:
        q = q.filter(Event.event_type == event_type)
    return q.order_by(Event.occurred_at.desc()).limit(limit).all()
