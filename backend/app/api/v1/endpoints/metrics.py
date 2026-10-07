"""System metrics endpoint."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.db.session import get_db
from backend.app.models.camera import Camera
from backend.app.models.event import Event
from backend.app.models.incident import Incident
from backend.app.models.alert import Alert
from backend.app.models.detection import Detection
from backend.app.models.track import Track
from backend.app.models.sync_queue import SyncQueue
from backend.app.api.deps import require_permission

router = APIRouter()


@router.get("")
def get_metrics(
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("read")),
):
    """Get system-wide metrics for observability."""
    cameras = db.query(Camera).all()
    cameras_online = sum(1 for c in cameras if c.status == "ONLINE")

    active_tracks = (
        db.query(Track)
        .filter(Track.active == True)
        .count()
    )

    pending_sync = (
        db.query(SyncQueue)
        .filter(SyncQueue.status == "PENDING")
        .count()
    )

    return {
        "cameras_online": cameras_online,
        "cameras_total": len(cameras),
        "camera_fps": {
            c.name: c.fps for c in cameras if c.status == "ONLINE"
        },
        "detections_total": db.query(Detection).count(),
        "tracks_active": active_tracks,
        "incidents_total": db.query(Incident).count(),
        "alerts_total": db.query(Alert).filter(Alert.status == "NEW").count(),
        "events_total": db.query(Event).count(),
        "queue_depth": pending_sync,
        "sync_failures": (
            db.query(SyncQueue).filter(SyncQueue.status == "FAILED").count()
        ),
    }
