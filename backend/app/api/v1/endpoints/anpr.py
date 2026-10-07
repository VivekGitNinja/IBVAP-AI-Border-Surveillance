"""ANPR (Automatic Number Plate Recognition) checkpost endpoints."""

from datetime import datetime, timedelta
import random
from typing import List, Optional
import cv2
import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form
from pydantic import BaseModel
from sqlalchemy.orm import Session
from backend.app.db.session import get_db
from backend.app.models.plate_read import PlateRead
from backend.app.models.incident import Incident
from backend.app.models.alert import Alert
from backend.app.services.anpr import anpr_engine
from backend.app.services.c2 import dispatch_incident_webhook
from backend.app.services.live_pipeline import live_manager
from backend.app.api.deps import require_permission

router = APIRouter()

class PlateIn(BaseModel):
    plate_number: str
    vehicle_type: str = "LMV"
    bop: str = "BOP-01"
    confidence: float = 0.92
    status: str = "CLEARED"
    notes: Optional[str] = None

class WatchlistPlateIn(BaseModel):
    plate_number: str
    reason: str
    agency: str = "Intelligence Bureau / State Police"
    threat_level: str = "HIGH"
    vehicle_model: Optional[str] = None

# Initial vehicle watchlist (stolen / flagged vehicles)
WATCHLIST_DB = [
    {
        "id": 1,
        "plate_number": "JK 02 C 5678",
        "reason": "Reported Stolen — Suspected Cross-Border Infiltration Vector",
        "agency": "NIA / J&K Police",
        "threat_level": "CRITICAL",
        "vehicle_model": "Mahindra Scorpio (White)",
        "added_at": (datetime.utcnow() - timedelta(days=2)).isoformat(),
    },
    {
        "id": 2,
        "plate_number": "UP 16 AK 4432",
        "reason": "Wanted in Contraband / Hawala Smuggling Case",
        "agency": "Narcotics Control Bureau",
        "threat_level": "HIGH",
        "vehicle_model": "Toyota Innova (Silver)",
        "added_at": (datetime.utcnow() - timedelta(days=5)).isoformat(),
    },
    {
        "id": 3,
        "plate_number": "PB 10 Z 9901",
        "reason": "Surveillance Flag — Suspicious Border Reconnaissance",
        "agency": "SSB Intelligence Unit",
        "threat_level": "MEDIUM",
        "vehicle_model": "Tata Xenon Pickup",
        "added_at": (datetime.utcnow() - timedelta(days=1)).isoformat(),
    },
    {
        "id": 4,
        "plate_number": "KA 02 MN 1826",
        "reason": "Border Lookout Notice — Flagged High-Speed Vehicle",
        "agency": "Special Operations Group / Traffic Enforcement",
        "threat_level": "CRITICAL",
        "vehicle_model": "Volvo XC90 (Black)",
        "added_at": (datetime.utcnow() - timedelta(hours=12)).isoformat(),
    },
]

# Simulated live scanned plates database
SCANNED_PLATES = [
    {
        "id": 1,
        "plate_number": "JK 02 C 5678",
        "vehicle_type": "SUV / LMV",
        "vehicle_model": "Mahindra Scorpio",
        "bop": "BOP-01 Road Checkpost",
        "confidence": 0.94,
        "status": "STOLEN_FLAGGED",
        "state_origin": "Jammu & Kashmir",
        "speed_kmh": 42,
        "lane": "Inbound Gate 2",
        "barrier_status": "INTERCEPT_ENGAGED",
        "timestamp": (datetime.utcnow() - timedelta(minutes=4)).isoformat(),
        "crop_url": "/api/v1/cameras/4/snapshot",
    },
    {
        "id": 2,
        "plate_number": "DL 01 AB 1234",
        "vehicle_type": "Civilian Sedan",
        "vehicle_model": "Maruti Dzire",
        "bop": "BOP-01 Gate",
        "confidence": 0.97,
        "status": "CLEARED",
        "state_origin": "Delhi NCT",
        "speed_kmh": 28,
        "lane": "Inbound Gate 1",
        "barrier_status": "OPEN",
        "timestamp": (datetime.utcnow() - timedelta(minutes=14)).isoformat(),
        "crop_url": "/api/v1/cameras/1/snapshot",
    },
    {
        "id": 3,
        "plate_number": "ARMY 21B 00921",
        "vehicle_type": "Military Convoy",
        "vehicle_model": "Ashok Leyland Stallion",
        "bop": "BOP-02 Watchtower Post",
        "confidence": 0.99,
        "status": "MILITARY_PRIORITY",
        "state_origin": "Indian Armed Forces",
        "speed_kmh": 35,
        "lane": "Military Transit Corridor",
        "barrier_status": "AUTOMATIC_PASS",
        "timestamp": (datetime.utcnow() - timedelta(minutes=25)).isoformat(),
        "crop_url": "/api/v1/cameras/3/snapshot",
    },
    {
        "id": 4,
        "plate_number": "HR 26 DQ 9921",
        "vehicle_type": "Heavy Commercial (HMV)",
        "vehicle_model": "Tata Prima Truck",
        "bop": "BOP-03 Freight Checkpoint",
        "confidence": 0.88,
        "status": "CUSTOMS_HOLD",
        "state_origin": "Haryana",
        "speed_kmh": 18,
        "lane": "Commercial Cargo Lane",
        "barrier_status": "INSPECTION_HOLD",
        "timestamp": (datetime.utcnow() - timedelta(minutes=38)).isoformat(),
        "crop_url": "/api/v1/cameras/4/snapshot",
    },
    {
        "id": 5,
        "plate_number": "UP 16 AK 4432",
        "vehicle_type": "MUV / Passenger",
        "vehicle_model": "Toyota Innova",
        "bop": "BOP-03 Freight Checkpoint",
        "confidence": 0.91,
        "status": "SUSPICIOUS_MATCH",
        "state_origin": "Uttar Pradesh",
        "speed_kmh": 58,
        "lane": "Outbound Highway",
        "barrier_status": "INTERCEPT_TRIGGERED",
        "timestamp": (datetime.utcnow() - timedelta(minutes=52)).isoformat(),
        "crop_url": "/api/v1/cameras/4/snapshot",
    },
]

BARRIER_STATE = {"status": "ARMED", "barrier_raised": False, "mode": "AUTO_INTERCEPT"}

@router.get("/plates")
def list_scanned_plates(
    status: Optional[str] = None,
    bop: Optional[str] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("read")),
):
    """List scanned vehicle license plates with OCR metadata from real database."""
    query = db.query(PlateRead).order_by(PlateRead.created_at.desc())
    if search:
        s = search.strip().upper()
        query = query.filter(PlateRead.plate_text.ilike(f"%{s}%"))
    records = query.limit(100).all()

    results = []
    for r in records:
        clean_r_plate = r.plate_text.replace(" ", "").upper()
        is_flagged = any(w["plate_number"].replace(" ", "").upper() == clean_r_plate for w in WATCHLIST_DB)
        plate_status = "STOLEN_FLAGGED" if is_flagged else "CLEARED"
        if status and plate_status.lower() != status.lower():
            continue
        results.append({
            "id": r.id,
            "plate_number": r.plate_text,
            "vehicle_type": "Motor Vehicle",
            "vehicle_model": "Identified via OCR",
            "bop": f"Camera #{r.camera_id}" if r.camera_id else (f"Job #{r.job_id}" if r.job_id else "Checkpost 1"),
            "confidence": r.confidence,
            "status": plate_status,
            "state_origin": r.plate_text[:2] if len(r.plate_text) >= 2 else "IND",
            "speed_kmh": 35,
            "lane": "Primary Checkpost",
            "barrier_status": "INTERCEPT_ENGAGED" if is_flagged else "CLEARED",
            "timestamp": r.created_at.isoformat(),
            "crop_url": "/api/v1/cameras/1/snapshot",
        })
    return results


@router.post("/scan-file")
async def scan_plate_file(
    file: UploadFile = File(...),
    bop: str = Form("BOP-01 Road Checkpost"),
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("write")),
):
    """Upload vehicle image and perform real-time ANPR localization and OCR."""
    contents = await file.read()
    nparr = np.frombuffer(contents, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(status_code=400, detail="Invalid image file")

    candidates = anpr_engine.process_frame(img)
    if not candidates:
        return {
            "recognized": False,
            "plate_number": None,
            "confidence": 0.0,
            "status": "NO_PLATE_DETECTED",
            "message": "No license plate localized or read in provided image",
        }

    best = candidates[0]
    plate_clean = best["text"]
    clean_no_space = plate_clean.replace(" ", "").upper()
    is_flagged = any(w["plate_number"].replace(" ", "").upper() == clean_no_space for w in WATCHLIST_DB)
    computed_status = "STOLEN_FLAGGED" if is_flagged else "CLEARED"

    rec = PlateRead(
        plate_text=plate_clean,
        confidence=best["confidence"],
        frame_index=0,
        timestamp_ms=0.0,
        bbox=best.get("bbox", {}),
        method="tesseract",
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)

    if is_flagged:
        now = datetime.utcnow()
        inc_code = f"IBVAP-{now.strftime('%Y%m%d')}-{now.strftime('%H%M%S')}-ANPR-STOLEN"
        inc = Incident(
            incident_code=inc_code,
            title=f"Stolen Vehicle Intercept: {plate_clean} (Barrier Triggered)",
            description=f"Vehicle registration plate '{plate_clean}' matched stolen vehicle lookout at {bop}. Automated barrier engaged.",
            severity="CRITICAL",
            threat_score=98.0,
            confidence=best["confidence"],
            status="OPEN",
            zone_name="Primary Checkpost",
            camera_name=bop,
            reason_codes=["STOLEN_VEHICLE_MATCH", f"PLATE_{clean_no_space}"],
            ai_assessment={
                "model": "ANPR OCR",
                "plate_number": plate_clean,
                "confidence": best["confidence"],
                "stolen_flagged": True,
                "barrier_engaged": True,
                "legal_citation": "Bharatiya Sakshya Adhiniyam, 2023 — Section 63",
            },
        )
        db.add(inc)
        db.flush()
        alert = Alert(
            incident_id=inc.id,
            priority="CRITICAL",
            status="NEW",
            message=f"CRITICAL: Stolen vehicle '{plate_clean}' detected at {bop} — Intercept Barrier Armed",
        )
        db.add(alert)
        db.commit()

        try:
            dispatch_incident_webhook({
                "incident_code": inc.incident_code,
                "title": inc.title,
                "severity": inc.severity,
                "threat_score": inc.threat_score,
                "confidence": inc.confidence,
                "zone_name": inc.zone_name,
                "camera_name": bop,
                "plate_number": plate_clean,
            })
            live_manager._on_event({
                "type": "incident_created",
                "data": {
                    "id": inc.id,
                    "incident_code": inc.incident_code,
                    "incident_type": inc.title,
                    "severity": inc.severity,
                    "threat_score": inc.threat_score,
                    "confidence": inc.confidence,
                    "bop": bop,
                    "camera_name": bop,
                    "summary": inc.description,
                }
            })
        except Exception:
            pass

    import base64
    crop_b64 = None
    if "plate_crop" in best and best["plate_crop"] is not None and getattr(best["plate_crop"], "size", 0) > 0:
        try:
            ok, buf = cv2.imencode(".jpg", best["plate_crop"])
            if ok:
                crop_b64 = f"data:image/jpeg;base64,{base64.b64encode(buf).decode('utf-8')}"
        except Exception:
            pass

    return {
        "id": rec.id,
        "recognized": True,
        "plate_number": plate_clean,
        "confidence": best["confidence"],
        "status": computed_status,
        "state_origin": plate_clean[:2] if len(plate_clean) >= 2 else "IND",
        "bop": bop,
        "barrier_status": "INTERCEPT_ENGAGED" if is_flagged else "CLEARED",
        "timestamp": rec.created_at.isoformat(),
        "bbox": best.get("bbox", {}),
        "crop_image": crop_b64,
    }


@router.post("/scan")
def scan_plate(
    data: PlateIn,
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("write")),
):
    """Record an ANPR scan, persist in database, and check against stolen watchlist."""
    plate_clean = data.plate_number.strip().upper()
    clean_no_space = plate_clean.replace(" ", "").upper()
    is_flagged = any(w["plate_number"].replace(" ", "").upper() == clean_no_space for w in WATCHLIST_DB)
    computed_status = "STOLEN_FLAGGED" if is_flagged else data.status

    rec = PlateRead(
        plate_text=plate_clean,
        confidence=data.confidence,
        frame_index=0,
        timestamp_ms=0.0,
        bbox={},
        method="manual_entry",
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)

    if is_flagged:
        now = datetime.utcnow()
        inc_code = f"IBVAP-{now.strftime('%Y%m%d')}-{now.strftime('%H%M%S')}-ANPR-STOLEN"
        inc = Incident(
            incident_code=inc_code,
            title=f"Stolen Vehicle Intercept: {plate_clean} (Barrier Triggered)",
            description=f"Vehicle registration plate '{plate_clean}' matched stolen vehicle lookout at {data.bop}. Automated barrier engaged.",
            severity="CRITICAL",
            threat_score=98.0,
            confidence=data.confidence,
            status="OPEN",
            zone_name="Primary Checkpost",
            camera_name=data.bop,
            reason_codes=["STOLEN_VEHICLE_MATCH", f"PLATE_{clean_no_space}"],
            ai_assessment={
                "model": "ANPR OCR",
                "plate_number": plate_clean,
                "confidence": data.confidence,
                "stolen_flagged": True,
                "barrier_engaged": True,
                "legal_citation": "Bharatiya Sakshya Adhiniyam, 2023 — Section 63",
            },
        )
        db.add(inc)
        db.flush()
        alert = Alert(
            incident_id=inc.id,
            priority="CRITICAL",
            status="NEW",
            message=f"CRITICAL: Stolen vehicle '{plate_clean}' detected at {data.bop} — Intercept Barrier Armed",
        )
        db.add(alert)
        db.commit()

        try:
            dispatch_incident_webhook({
                "incident_code": inc.incident_code,
                "title": inc.title,
                "severity": inc.severity,
                "threat_score": inc.threat_score,
                "confidence": inc.confidence,
                "zone_name": inc.zone_name,
                "camera_name": data.bop,
                "plate_number": plate_clean,
            })
            live_manager._on_event({
                "type": "incident_created",
                "data": {
                    "id": inc.id,
                    "incident_code": inc.incident_code,
                    "incident_type": inc.title,
                    "severity": inc.severity,
                    "threat_score": inc.threat_score,
                    "confidence": inc.confidence,
                    "bop": data.bop,
                    "camera_name": data.bop,
                    "summary": inc.description,
                }
            })
        except Exception:
            pass

    return {
        "id": rec.id,
        "plate_number": plate_clean,
        "vehicle_type": data.vehicle_type,
        "vehicle_model": "Standard Vehicle",
        "bop": data.bop,
        "confidence": data.confidence,
        "status": computed_status,
        "state_origin": plate_clean[:2] if len(plate_clean) >= 2 else "IND",
        "speed_kmh": 35,
        "lane": "Inbound Gate 1",
        "barrier_status": "INTERCEPT_ENGAGED" if is_flagged else "CLEARED",
        "timestamp": rec.created_at.isoformat(),
        "crop_url": "/api/v1/cameras/1/snapshot",
    }

@router.get("/watchlist")
def get_anpr_watchlist(user: dict = Depends(require_permission("read"))):
    """Get active blacklisted/stolen vehicle watchlist."""
    return WATCHLIST_DB

@router.post("/watchlist")
def add_to_watchlist(
    data: WatchlistPlateIn,
    user: dict = Depends(require_permission("write")),
):
    """Add a license plate to the border intelligence watchlist."""
    record = {
        "id": len(WATCHLIST_DB) + 1,
        "plate_number": data.plate_number.strip().upper(),
        "reason": data.reason,
        "agency": data.agency,
        "threat_level": data.threat_level,
        "vehicle_model": data.vehicle_model or "Unknown Model",
        "added_at": datetime.utcnow().isoformat(),
    }
    WATCHLIST_DB.insert(0, record)
    return record

@router.post("/barrier/toggle")
def toggle_barrier(user: dict = Depends(require_permission("barrier_control"))):
    """Remotely engage or release checkpost vehicle intercept barrier."""
    BARRIER_STATE["barrier_raised"] = not BARRIER_STATE["barrier_raised"]
    BARRIER_STATE["status"] = "BARRIER_OPEN" if BARRIER_STATE["barrier_raised"] else "BARRIER_ENGAGED"
    return BARRIER_STATE

@router.get("/stats")
def get_anpr_stats(user: dict = Depends(require_permission("read"))):
    """Get checkpost ANPR telemetry."""
    total = len(SCANNED_PLATES)
    flagged = sum(1 for p in SCANNED_PLATES if "flag" in p["status"].lower() or "stolen" in p["status"].lower())
    avg_conf = sum(p["confidence"] for p in SCANNED_PLATES) / max(total, 1)
    return {
        "total_vehicles_24h": total,
        "flagged_intercepts": flagged,
        "cleared_vehicles": total - flagged,
        "avg_ocr_confidence": round(avg_conf * 100, 1),
        "barrier_state": BARRIER_STATE,
    }
