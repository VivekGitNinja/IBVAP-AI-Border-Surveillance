"""Incident management endpoints."""

from __future__ import annotations
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.db.session import get_db
from backend.app.models.incident import Incident
from backend.app.models.alert import Alert
from backend.app.models.audit import AuditLog
from backend.app.schemas.common import IncidentOut, IncidentUpdate, AlertOut
from backend.app.services.audit import log_action
from backend.app.services.timeline import build_incident_timeline
from backend.app.api.deps import current_user, require_permission

router = APIRouter()


@router.get("", response_model=list[IncidentOut])
def list_incidents(
    status: str | None = None,
    severity: str | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("read")),
):
    """List incidents with optional filters."""
    q = db.query(Incident)
    if status:
        q = q.filter(Incident.status == status)
    if severity:
        q = q.filter(Incident.severity == severity)
    return q.order_by(Incident.created_at.desc()).limit(limit).all()


@router.get("/alerts", response_model=list[AlertOut])
def list_alerts(
    status: str | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("read")),
):
    """List alerts."""
    q = db.query(Alert)
    if status:
        q = q.filter(Alert.status == status)
    return q.order_by(Alert.created_at.desc()).limit(limit).all()


@router.get("/{incident_id}", response_model=IncidentOut)
def get_incident(
    incident_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("read")),
):
    """Get full incident details."""
    x = db.get(Incident, incident_id)
    if not x:
        raise HTTPException(404, "Incident not found")
    return x


@router.post("/{incident_id}/acknowledge", response_model=IncidentOut)
def acknowledge_incident(
    incident_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("acknowledge_incidents")),
):
    """Acknowledge an incident (human verification)."""
    x = db.get(Incident, incident_id)
    if not x:
        raise HTTPException(404, "Incident not found")

    x.status = "ACKNOWLEDGED"
    x.acknowledged_at = datetime.utcnow()
    x.acknowledged_by = user["sub"]

    # Add timeline event
    timeline = x.timeline or []
    timeline.append({
        "timestamp": datetime.utcnow().isoformat(),
        "event_type": "operator_acknowledged",
        "description": f"Acknowledged by {user['sub']}",
        "source": "operator",
    })
    x.timeline = timeline

    # Update related alerts
    for a in db.query(Alert).filter(
        Alert.incident_id == x.id, Alert.status == "NEW"
    ):
        a.status = "ACKNOWLEDGED"
        a.acknowledged_by = user["sub"]
        a.acknowledged_at = x.acknowledged_at

    log_action(db, user["sub"], user.get("role", ""), "ACKNOWLEDGE",
               "incident", str(x.id), {"code": x.incident_code})
    db.commit()
    db.refresh(x)
    return x


@router.post("/{incident_id}/escalate", response_model=IncidentOut)
def escalate_incident(
    incident_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("escalate_incidents")),
):
    """Escalate an incident."""
    x = db.get(Incident, incident_id)
    if not x:
        raise HTTPException(404, "Incident not found")

    x.status = "ESCALATED"
    x.escalated_at = datetime.utcnow()
    x.escalated_by = user["sub"]

    timeline = x.timeline or []
    timeline.append({
        "timestamp": datetime.utcnow().isoformat(),
        "event_type": "incident_escalated",
        "description": f"Escalated by {user['sub']}",
        "source": "operator",
    })
    x.timeline = timeline

    log_action(db, user["sub"], user.get("role", ""), "ESCALATE",
               "incident", str(x.id), {"code": x.incident_code})
    db.commit()
    db.refresh(x)
    return x


from backend.app.services.feedback import dismiss_incident as execute_dismissal, IncidentDismissIn


@router.post("/{incident_id}/dismiss", response_model=IncidentOut)
def dismiss_incident(
    incident_id: int,
    payload: Optional[IncidentDismissIn] = None,
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("acknowledge_incidents")),
):
    """Dismiss an incident as false positive and register authoritative operator suppression."""
    x = db.get(Incident, incident_id)
    if not x:
        raise HTTPException(404, "Incident not found")

    data = payload or IncidentDismissIn(reason="ENVIRONMENT_FALSE_ALARM", duration_seconds=300)
    execute_dismissal(db, incident_id, data, user["sub"])

    log_action(db, user["sub"], user.get("role", ""), "DISMISS",
               "incident", str(x.id), {"code": x.incident_code, "reason": data.reason})
    db.refresh(x)
    return x


@router.post("/{incident_id}/close", response_model=IncidentOut)
def close_incident(
    incident_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("acknowledge_incidents")),
):
    """Close an incident."""
    x = db.get(Incident, incident_id)
    if not x:
        raise HTTPException(404, "Incident not found")

    x.status = "CLOSED"
    x.closed_at = datetime.utcnow()
    x.closed_by = user["sub"]

    timeline = x.timeline or []
    timeline.append({
        "timestamp": datetime.utcnow().isoformat(),
        "event_type": "incident_closed",
        "description": f"Closed by {user['sub']}",
        "source": "operator",
    })
    x.timeline = timeline

    log_action(db, user["sub"], user.get("role", ""), "CLOSE",
               "incident", str(x.id), {"code": x.incident_code})
    db.commit()
    db.refresh(x)
    return x


@router.get("/{incident_id}/timeline")
def incident_timeline(
    incident_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("read")),
):
    """Get incident timeline."""
    x = db.get(Incident, incident_id)
    if not x:
        raise HTTPException(404, "Incident not found")

    # Use stored timeline or rebuild
    timeline = x.timeline or []
    if not timeline:
        from backend.app.models.event import Event
        events = db.query(Event).filter(
            Event.id.in_(x.event_ids or [])
        ).all()
        events_data = [
            {
                "id": e.id,
                "event_type": e.event_type,
                "object_type": e.object_type,
                "confidence": e.confidence,
                "occurred_at": e.occurred_at.isoformat(),
                "payload": e.payload,
            }
            for e in events
        ]
        timeline = build_incident_timeline(events_data, {
            "threat_score": x.threat_score,
            "severity": x.severity,
            "acknowledged_at": x.acknowledged_at,
            "acknowledged_by": x.acknowledged_by,
            "escalated_at": x.escalated_at,
            "escalated_by": x.escalated_by,
            "closed_at": x.closed_at,
            "closed_by": x.closed_by,
        })

    return {"incident_id": incident_id, "timeline": timeline}
