"""Database base module — imports all models so metadata is populated."""

from backend.app.models.base import Base  # noqa: F401
from backend.app.models.user import User  # noqa: F401
from backend.app.models.camera import Camera  # noqa: F401
from backend.app.models.camera_health import CameraHealth  # noqa: F401
from backend.app.models.zone import Zone  # noqa: F401
from backend.app.models.detection import Detection  # noqa: F401
from backend.app.models.track import Track  # noqa: F401
from backend.app.models.event import Event  # noqa: F401
from backend.app.models.incident import Incident  # noqa: F401
from backend.app.models.alert import Alert  # noqa: F401
from backend.app.models.evidence import Evidence  # noqa: F401
from backend.app.models.audit import AuditLog  # noqa: F401
from backend.app.models.sync_queue import SyncQueue  # noqa: F401
from backend.app.models.system_config import SystemConfig  # noqa: F401
from backend.app.models.media_asset import MediaAsset  # noqa: F401
from backend.app.models.analysis_job import AnalysisJob  # noqa: F401
from backend.app.models.plate_read import PlateRead  # noqa: F401
from backend.app.models.watchlist import Watchlist  # noqa: F401
from backend.app.models.suppression import OperatorSuppression  # noqa: F401
from backend.app.models.outbox import IncidentOutboxEvent  # noqa: F401

__all__ = [
    "Base", "User", "Camera", "CameraHealth", "Zone", "Detection", "Track",
    "Event", "Incident", "Alert", "Evidence", "AuditLog", "SyncQueue",
    "SystemConfig", "MediaAsset", "AnalysisJob", "PlateRead", "Watchlist",
    "OperatorSuppression", "IncidentOutboxEvent",
]

