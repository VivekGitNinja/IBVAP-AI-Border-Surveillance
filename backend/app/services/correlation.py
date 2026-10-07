"""
Incident Correlation Engine.
============================

Combines temporally, geographically, and kinematically related events from multiple cameras
into correlated incidents and shared entity dossiers.
Does NOT claim biometric certainty across cameras — uses explainable tracklet evidence
and multi-modal topological constraints.

Classification:
    Production-structured cross-camera association, pending field validation.
"""

from __future__ import annotations
import math
import logging
from datetime import datetime, timedelta
from typing import Any, Optional, List, Dict

logger = logging.getLogger(__name__)

# Maximum time gap (seconds) between events to consider them correlated
CORRELATION_TIME_WINDOW_SECONDS = 300  # 5 minutes

# Maximum distance (meters) between cameras for spatial correlation
CORRELATION_DISTANCE_METERS = 500.0


def should_correlate(
    existing_incident_time: datetime,
    event_time: datetime,
    time_window_seconds: int = CORRELATION_TIME_WINDOW_SECONDS,
) -> bool:
    """Check if an event falls within the correlation window of an existing incident."""
    if existing_incident_time is None or event_time is None:
        return False
    delta = abs((event_time - existing_incident_time).total_seconds())
    return delta <= time_window_seconds


def compute_distance_meters(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> float:
    """Haversine distance between two GPS coordinates in meters."""
    R = 6371000  # Earth radius in meters
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def cameras_spatially_correlated(
    cam1_lat: float, cam1_lon: float,
    cam2_lat: float, cam2_lon: float,
    max_distance: float = CORRELATION_DISTANCE_METERS,
) -> bool:
    """Check if two cameras are within spatial correlation distance."""
    if cam1_lat == 0 and cam1_lon == 0:
        return True  # Unknown location → always try temporal correlation
    if cam2_lat == 0 and cam2_lon == 0:
        return True
    dist = compute_distance_meters(cam1_lat, cam1_lon, cam2_lat, cam2_lon)
    return dist <= max_distance


def build_correlation_key(
    event_type: str,
    object_type: str,
    zone_name: str,
    time_bucket_minutes: int = 5,
    occurred_at: datetime | None = None,
) -> str:
    """Build a deduplication/correlation fingerprint for an event.

    Events with the same key in the same time bucket are candidates for correlation.
    """
    ts = occurred_at or datetime.utcnow()
    bucket = ts.strftime(f"%Y%m%d%H{time_bucket_minutes}")
    return f"{event_type}:{object_type}:{zone_name}:{bucket}"


def correlate_incidents(
    existing_incidents: list[dict[str, Any]],
    new_event: dict[str, Any],
    camera_lat: float = 0.0,
    camera_lon: float = 0.0,
) -> str | None:
    """Find an existing incident to correlate with, or None.

    Returns the incident_code of the correlated incident.
    """
    for inc in existing_incidents:
        if inc.get("status") in ("CLOSED",):
            continue
        inc_time = inc.get("created_at")
        event_time = new_event.get("occurred_at")
        if inc_time and event_time:
            if not should_correlate(inc_time, event_time):
                continue
        # Check object type similarity
        if inc.get("object_type", "") == new_event.get("object_type", ""):
            return inc.get("incident_code")
    return None


# ── ADVANCED TOPOLOGY-AWARE INCIDENT CORRELATION ──────────────────────────────

_site_associator: Optional[Any] = None


def get_site_cross_camera_associator(topology: Optional[Any] = None) -> Any:
    """Get or create singleton CrossCameraAssociator for the site."""
    global _site_associator
    if _site_associator is None:
        from edge.correlation.topology import CameraTopologyGraph
        from edge.correlation.cross_camera import CrossCameraAssociator
        topo = topology or CameraTopologyGraph()
        _site_associator = CrossCameraAssociator(topology=topo)
    elif topology is not None:
        _site_associator.topology = topology
    return _site_associator


def correlate_incident_with_tracklet(
    db: Any,
    incident: Any,
    tracklet: Any,
    associator: Optional[Any] = None,
) -> list[int]:
    """
    Correlate a newly created or updated incident with previous multi-camera incidents
    via the CrossCameraAssociator and GlobalEntityDossier.
    Performs symmetric updates on Incident.correlated_ids and appends a fused timeline entry.

    Returns list of correlated incident IDs.
    """
    from backend.app.models.incident import Incident

    assoc_engine = associator or get_site_cross_camera_associator()
    if assoc_engine is None or tracklet is None:
        return []

    # Query active dossier for this tracklet
    dossier = assoc_engine.get_dossier_for_track(tracklet.camera_id, tracklet.track_id)
    if dossier is None:
        return []

    correlated_ids = list(incident.correlated_ids or [])
    newly_linked_ids: list[int] = []

    # Find incidents from other tracklets in the same dossier
    for other_inc_id in dossier.associated_incident_ids:
        if other_inc_id != incident.id and other_inc_id not in correlated_ids:
            correlated_ids.append(other_inc_id)
            newly_linked_ids.append(other_inc_id)

            # Symmetric update on the other incident
            try:
                other_inc = db.query(Incident).filter(Incident.id == other_inc_id).first()
                if other_inc:
                    other_corr = list(other_inc.correlated_ids or [])
                    if incident.id and incident.id not in other_corr:
                        other_corr.append(incident.id)
                        other_inc.correlated_ids = other_corr

                        # Add timeline entry to the other incident
                        other_tl = list(other_inc.timeline or [])
                        other_tl.append({
                            "timestamp": datetime.utcnow().isoformat(),
                            "event_type": "cross_camera_correlation",
                            "description": f"Spatially correlated with Incident #{incident.id} ({incident.incident_code}) at Camera {incident.camera_id} via Dossier {dossier.dossier_id}",
                            "source": "correlation_engine",
                            "confidence": 0.85,
                            "payload": {
                                "correlated_incident_id": incident.id,
                                "dossier_id": dossier.dossier_id,
                                "evidence_status": dossier.evidence_status,
                            }
                        })
                        other_inc.timeline = other_tl
            except Exception as e:
                logger.warning(f"Error updating reciprocal correlation on Incident #{other_inc_id}: {e}")

    if newly_linked_ids:
        incident.correlated_ids = correlated_ids
        tl = list(incident.timeline or [])
        tl.append({
            "timestamp": datetime.utcnow().isoformat(),
            "event_type": "cross_camera_correlation",
            "description": f"Spatially correlated across towers with Incidents {newly_linked_ids} via Dossier {dossier.dossier_id} ({dossier.evidence_status})",
            "source": "correlation_engine",
            "confidence": 0.85,
            "payload": {
                "correlated_incident_ids": newly_linked_ids,
                "dossier_id": dossier.dossier_id,
                "evidence_status": dossier.evidence_status,
            }
        })
        incident.timeline = tl

    # Ensure this incident is registered in the dossier
    if incident.id and incident.id not in dossier.associated_incident_ids:
        dossier.associated_incident_ids.append(incident.id)

    return newly_linked_ids
