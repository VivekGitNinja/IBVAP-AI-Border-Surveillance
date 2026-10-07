"""Plate search and management API endpoints."""

from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session
from sqlalchemy import desc

from backend.app.db.session import get_db
from backend.app.models.plate_read import PlateRead
from backend.app.api.deps import require_permission

router = APIRouter()


class PlateReadOut(BaseModel):
    id: int
    job_id: Optional[int] = None
    detection_id: Optional[int] = None
    camera_id: Optional[int] = None
    plate_text: str
    confidence: float
    frame_index: int
    timestamp_ms: float
    bbox: dict = {}
    method: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


@router.get("", response_model=List[PlateReadOut])
def search_plates(
    q: Optional[str] = Query(None, description="Plate search filter"),
    job_id: Optional[int] = Query(None, description="Filter by analysis job ID"),
    camera_id: Optional[int] = Query(None, description="Filter by camera ID"),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("read")),
):
    """Query real license plate reads extracted from video analysis or cameras."""
    query = db.query(PlateRead)
    if q:
        clean_q = q.strip().upper()
        query = query.filter(PlateRead.plate_text.ilike(f"%{clean_q}%"))
    if job_id is not None:
        query = query.filter(PlateRead.job_id == job_id)
    if camera_id is not None:
        query = query.filter(PlateRead.camera_id == camera_id)

    records = query.order_by(desc(PlateRead.created_at)).limit(limit).all()
    return records
