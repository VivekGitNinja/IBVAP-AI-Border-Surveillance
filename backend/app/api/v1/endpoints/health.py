"""Health check endpoints."""

import time
from fastapi import APIRouter, Depends
from sqlalchemy import text

from backend.app.db.session import SessionLocal
from backend.app.core.cache import cached
from backend.app.api.deps import require_permission

router = APIRouter()

# Track startup time
_start_time = time.time()


@router.get("")
def health():
    """Basic health check."""
    return {"status": "ok", "service": "ibvap-api", "version": "2.0.0"}


@router.get("/detailed")
def health_detailed():
    """Detailed health check including database connectivity."""
    checks = {}

    # Database check
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "unavailable"

    # Redis check (optional)
    try:
        from backend.app.core.config import settings
        import redis
        r = redis.from_url(settings.redis_url, socket_timeout=2)
        r.ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "unavailable (non-critical)"

    overall = "ok" if checks.get("database") == "ok" else "degraded"
    return {"status": overall, "checks": checks}


@router.get("/status")
@cached(ttl_seconds=3, key_prefix="system_status")
def system_status(
    user: dict = Depends(require_permission("read")),
):
    """High-level system status for the dashboard header."""
    db = SessionLocal()
    try:
        from backend.app.models.camera import Camera
        from backend.app.models.incident import Incident
        from backend.app.models.alert import Alert
        from backend.app.models.sync_queue import SyncQueue

        cameras_total = db.query(Camera).count()
        cameras_online = db.query(Camera).filter(Camera.status == "ONLINE").count()
        active_incidents = db.query(Incident).filter(
            Incident.status.in_(["OPEN", "ACKNOWLEDGED"])
        ).count()
        active_alerts = db.query(Alert).filter(Alert.status == "NEW").count()
        sync_pending = db.query(SyncQueue).filter(SyncQueue.status == "PENDING").count()

        return {
            "status": "operational",
            "version": "2.0.0",
            "uptime_seconds": round(time.time() - _start_time, 1),
            "cameras_online": cameras_online,
            "cameras_total": cameras_total,
            "active_incidents": active_incidents,
            "active_alerts": active_alerts,
            "edge_nodes": max(1, cameras_online),
            "sync_pending": sync_pending,
        }
    finally:
        db.close()
