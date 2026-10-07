"""IBVAP database models."""

from backend.app.models.base import Base
from backend.app.models.user import User
from backend.app.models.camera import Camera
from backend.app.models.camera_health import CameraHealth
from backend.app.models.zone import Zone
from backend.app.models.detection import Detection
from backend.app.models.track import Track
from backend.app.models.event import Event
from backend.app.models.incident import Incident
from backend.app.models.alert import Alert
from backend.app.models.evidence import Evidence
from backend.app.models.audit import AuditLog
from backend.app.models.sync_queue import SyncQueue
from backend.app.models.system_config import SystemConfig
from backend.app.models.media_asset import MediaAsset
from backend.app.models.analysis_job import AnalysisJob
from backend.app.models.plate_read import PlateRead
from backend.app.models.watchlist import Watchlist
from backend.app.models.suppression import OperatorSuppression
from backend.app.models.outbox import IncidentOutboxEvent

__all__ = [
    "Base", "User", "Camera", "CameraHealth", "Zone", "Detection", "Track",
    "Event", "Incident", "Alert", "Evidence", "AuditLog", "SyncQueue",
    "SystemConfig", "MediaAsset", "AnalysisJob", "PlateRead", "Watchlist",
    "OperatorSuppression", "IncidentOutboxEvent",
]

