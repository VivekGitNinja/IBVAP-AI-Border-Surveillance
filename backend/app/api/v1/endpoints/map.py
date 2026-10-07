"""Map situational awareness endpoint.

Aggregates camera geographic coordinates, active alerts, and sector status.
"""

from typing import Dict, Any, List
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import or_

from backend.app.db.session import get_db
from backend.app.models.camera import Camera
from backend.app.models.incident import Incident
from backend.app.models.evidence import Evidence
from backend.app.api.deps import require_permission

router = APIRouter()


@router.get("")
def get_map_situational_awareness(
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("read")),
) -> Dict[str, Any]:
    """Retrieve full geospatial & tactical awareness payload for the border map."""
    cameras = db.query(Camera).filter(Camera.active == True).order_by(Camera.id).all()
    
    # Active threat incidents (OPEN or ESCALATED)
    open_incidents = (
        db.query(Incident)
        .filter(or_(Incident.status == "OPEN", Incident.status == "ESCALATED"))
        .order_by(Incident.created_at.desc())
        .limit(50)
        .all()
    )

    incident_ids = [inc.id for inc in open_incidents]
    evidence_map: Dict[int, str] = {}
    if incident_ids:
        evs = (
            db.query(Evidence)
            .filter(Evidence.incident_id.in_(incident_ids))
            .filter(Evidence.evidence_type == "snapshot")
            .all()
        )
        for ev in evs:
            if ev.incident_id not in evidence_map:
                evidence_map[ev.incident_id] = ev.file_path

    camera_list = []
    sector_stats: Dict[str, Dict[str, Any]] = {}

    for c in cameras:
        sec = c.sector or "Sector Alpha"
        if sec not in sector_stats:
            sector_stats[sec] = {"sector": sec, "total_cameras": 0, "online_cameras": 0, "incidents": 0}
        sector_stats[sec]["total_cameras"] += 1
        if c.status == "ONLINE":
            sector_stats[sec]["online_cameras"] += 1

        camera_list.append({
            "id": c.id,
            "name": c.name,
            "bop": c.bop,
            "sector": sec,
            "latitude": c.latitude,
            "longitude": c.longitude,
            "status": c.status,
            "health_score": c.health_score,
            "stream_url": c.stream_url,
            "fps": c.fps,
        })

    incident_list = []
    for inc in open_incidents:
        sec = inc.zone_name or "General"
        # Find matching camera if camera_id or name matches
        cam_id = inc.camera_id
        cam_lat = 0.0
        cam_lng = 0.0
        if cam_id:
            matching_cam = next((c for c in cameras if c.id == cam_id), None)
            if matching_cam:
                cam_lat = matching_cam.latitude
                cam_lng = matching_cam.longitude

        incident_list.append({
            "id": inc.id,
            "incident_code": inc.incident_code,
            "title": inc.title,
            "severity": inc.severity,
            "threat_score": inc.threat_score,
            "confidence": inc.confidence,
            "zone_name": inc.zone_name,
            "camera_id": cam_id,
            "latitude": cam_lat,
            "longitude": cam_lng,
            "evidence_snapshot": evidence_map.get(inc.id, ""),
            "created_at": inc.created_at.isoformat() if inc.created_at else "",
        })

    return {
        "status": "ready",
        "cameras": camera_list,
        "incidents": incident_list,
        "sectors": list(sector_stats.values()),
        "summary": {
            "total_cameras": len(cameras),
            "online_cameras": sum(1 for c in cameras if c.status == "ONLINE"),
            "open_incidents": len(open_incidents),
        }
    }
