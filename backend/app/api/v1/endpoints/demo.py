"""Demo scenario endpoints — deterministic demonstration for SIH."""

import hashlib
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.db.session import get_db
from backend.app.models.camera import Camera
from backend.app.models.zone import Zone
from backend.app.models.event import Event
from backend.app.models.incident import Incident
from backend.app.models.alert import Alert
from backend.app.models.evidence import Evidence
from backend.app.services.demo_scenarios import (
    get_available_scenarios, generate_scenario_events,
)
from backend.app.services.scoring import compute_threat_score
from backend.app.services.evidence import seal_evidence
from backend.app.services.timeline import create_timeline_event, append_timeline
from backend.app.api.deps import require_permission

router = APIRouter()


@router.get("/scenarios")
def list_scenarios(user: dict = Depends(require_permission("read"))):
    """List available demo scenarios."""
    return get_available_scenarios()


@router.post("/seed")
def seed_demo(
    scenario: str = "intrusion",
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("run_demo")),
):
    """Generate a complete demo incident from a scenario.

    Creates: camera, zones, events, incident, alerts, evidence,
    and a full timeline.
    """
    # Ensure a demo camera exists
    camera = db.query(Camera).first()
    if not camera:
        camera = Camera(
            name="BOP-01 Gate Camera",
            stream_url="demo://synthetic",
            location="Demo Border Sector",
            bop="BOP-01",
            status="ONLINE",
            health_score=98,
            latitude=28.6139,
            longitude=77.2090,
        )
        db.add(camera)
        db.flush()

    # Ensure demo zones exist
    existing_zones = db.query(Zone).filter(Zone.camera_id == camera.id).all()
    if not existing_zones:
        demo_zones = [
            Zone(
                camera_id=camera.id,
                name="Restricted Perimeter",
                zone_type="RESTRICTED",
                polygon=[[100, 100], [600, 100], [600, 500], [100, 500]],
                severity=1.0,
                dwell_threshold_seconds=20,
            ),
            Zone(
                camera_id=camera.id,
                name="Monitoring Zone",
                zone_type="MONITORING",
                polygon=[[200, 200], [500, 200], [500, 400], [200, 400]],
                severity=0.3,
                dwell_threshold_seconds=60,
            ),
            Zone(
                camera_id=camera.id,
                name="Patrol Zone",
                zone_type="PATROL",
                polygon=[[300, 150], [550, 150], [550, 450], [300, 450]],
                severity=0.5,
                dwell_threshold_seconds=30,
            ),
        ]
        for z in demo_zones:
            db.add(z)
        db.flush()
        existing_zones = demo_zones

    # Generate scenario events
    result = generate_scenario_events(scenario, camera_id=camera.id)
    scenario_data = result["scenario"]
    threat = result["threat_assessment"]
    now = datetime.utcnow()

    # Create event records
    event_ids = []
    for evt_data in result["events"]:
        evt = Event(
            camera_id=evt_data.get("camera_id", camera.id),
            event_type=evt_data["event_type"],
            object_type=evt_data["object_type"],
            confidence=evt_data["confidence"],
            severity=threat["score"] / 100.0 if threat["score"] > 40 else 0.1,
            payload={
                "track_id": f"T-{len(event_ids)+100:04d}",
                "bbox": evt_data.get("payload", {}).get("bbox", [400, 200, 500, 400]),
                "demo": True,
                "scenario": scenario,
                **evt_data.get("payload", {}),
            },
            occurred_at=now - timedelta(seconds=10 * len(event_ids)),
        )
        db.add(evt)
        db.flush()
        event_ids.append(evt.id)

    # Create incident — use microseconds + random to ensure uniqueness
    import random
    code = f"IBVAP-{now.strftime('%Y%m%d')}-{now.strftime('%H%M%S')}-{now.microsecond:06d}-{random.randint(1000,9999)}"
    fingerprint = hashlib.sha256(
        f"{scenario}:{camera.id}:{now.isoformat()}:{code}".encode()
    ).hexdigest()[:16]

    timeline = result["timeline"]
    timeline.append(create_timeline_event(
        event_type="alert_generated",
        description="Alert generated for operator review",
        source="alert_engine",
    ))

    incident = Incident(
        incident_code=code,
        title=scenario_data["name"],
        description=scenario_data["description"],
        severity=threat["severity"],
        threat_score=threat["score"],
        confidence=threat["confidence"],
        status="OPEN",
        reason_codes=threat["reasons"],
        event_ids=event_ids,
        camera_id=camera.id,
        camera_name=camera.name,
        zone_name=existing_zones[0].name if existing_zones else "",
        fingerprint=fingerprint,
        recommended_action=threat["recommended_action"],
        ai_assessment=threat["ai_assessment"],
        timeline=timeline,
        created_at=now,
    )
    db.add(incident)
    db.flush()

    # Create alert
    alert = Alert(
        incident_id=incident.id,
        priority=threat["severity"],
        status="NEW",
        message=f"{threat['severity']} incident: {scenario_data['name']} "
                f"(Score: {threat['score']}/100)",
        created_at=now,
    )
    db.add(alert)
    db.flush()

    # Create evidence
    payload = {
        "event_ids": event_ids,
        "scenario": scenario,
        "score": threat["score"],
        "reasons": threat["reasons"],
        "ai_assessment": threat["ai_assessment"],
        "demo": True,
    }
    manifest_path, manifest_hash, manifest_data = seal_evidence(
        code, payload, camera_id=camera.id, camera_name=camera.name,
        threat_score=threat["score"],
    )

    evidence = Evidence(
        incident_id=incident.id,
        evidence_type="snapshot",
        file_path=manifest_path,
        sha256=manifest_hash,
        manifest_path=manifest_path,
        manifest_data=manifest_data,
        threat_score=threat["score"],
        camera_id=camera.id,
        camera_name=camera.name,
        detection_metadata={
            "detections": len(event_ids),
            "scenario": scenario,
        },
        created_at=now,
    )
    db.add(evidence)
    db.commit()

    return {
        "incident_id": incident.id,
        "incident_code": code,
        "severity": threat["severity"],
        "threat_score": threat["score"],
        "confidence": threat["confidence"],
        "reasons": threat["reasons"],
        "recommended_action": threat["recommended_action"],
        "events_created": len(event_ids),
        "evidence_id": evidence.id,
        "scenario": scenario_data["name"],
        "timeline_events": len(timeline),
    }


@router.post("/seed/all")
def seed_all_scenarios(
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("run_demo")),
):
    """Generate one incident for each scenario."""
    scenarios = ["intrusion", "night_movement", "loitering", "vehicle",
                 "abandoned", "multi_camera"]
    results = []
    for s in scenarios:
        result = seed_demo(s, db, user=user)
        results.append(result)
    return {"incidents_created": len(results), "results": results}
