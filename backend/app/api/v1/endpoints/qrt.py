"""Tactical QRT (Quick Reaction Team) Dispatch and Mission Control endpoints."""

from datetime import datetime, timedelta
import random
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from backend.app.api.deps import require_permission

router = APIRouter()

class DispatchIn(BaseModel):
    team_id: int
    incident_id: int
    target_sector: str = "BOP-01 Sector Alpha"
    orders: str = "Interception & Perimeter Containment"

class StatusUpdateIn(BaseModel):
    team_id: int
    status: str  # STANDBY, EN_ROUTE, ENGAGED, SECURED, RETURNING
    notes: Optional[str] = None

class RadioBroadcastIn(BaseModel):
    callsign: str
    message: str
    priority: str = "FLASH_TACTICAL"

QRT_TEAMS = [
    {
        "id": 1,
        "name": "Cheetah-1 QRT Strike Unit",
        "callsign": "CHEETAH-LEADER",
        "bop": "BOP-01 (Sector Alpha)",
        "current_sector": "Sector Alpha Perimeter Grid 12",
        "status": "STANDBY_IMMEDIATE",
        "strength": 8,
        "commander": "Sub-Inspector V. K. Sharma",
        "vehicle": "Mahindra Marksman Light Armored Vehicle",
        "weapons_readiness": "Full Battle Load (AK-203, Glock 17, Night Vision Gen-3)",
        "radio_channel": "VHF Tac-1 (142.850 MHz Encrypted)",
        "assigned_incident": None,
        "eta_minutes": 0,
        "last_sitrep": "All units staged at forward armory. Ready to scramble.",
        "fuel_percent": 94,
    },
    {
        "id": 2,
        "name": "Cobra-2 Fast Interceptor",
        "callsign": "COBRA-ACTUAL",
        "bop": "BOP-02 (Sector Beta)",
        "current_sector": "Watchtower Ridge Patrol Route",
        "status": "PATROL_ACTIVE",
        "strength": 6,
        "commander": "Assistant Commandant R. Rathore",
        "vehicle": "Armed Polaris All-Terrain Vehicle (ATV)",
        "weapons_readiness": "Light Interceptor Load (Tavor TAR-21, Stun Grenades)",
        "radio_channel": "VHF Tac-2 (143.125 MHz Encrypted)",
        "assigned_incident": None,
        "eta_minutes": 0,
        "last_sitrep": "Routine mounted perimeter sweep. Zero contact.",
        "fuel_percent": 82,
    },
    {
        "id": 3,
        "name": "Falcon-3 Night Sniper Section",
        "callsign": "FALCON-EYE",
        "bop": "BOP-03 (Sector Gamma)",
        "current_sector": "BOP-03 Road Checkpoint",
        "status": "STANDBY_IMMEDIATE",
        "strength": 4,
        "commander": "Head Constable M. Singh",
        "vehicle": "Modified Tata Xenon 4x4",
        "weapons_readiness": "Designated Marksman (Dragunov SVD, Thermal Scope)",
        "radio_channel": "VHF Tac-3 (144.200 MHz Encrypted)",
        "assigned_incident": None,
        "eta_minutes": 0,
        "last_sitrep": "Elevated hide position occupied. Thermal optics operational.",
        "fuel_percent": 100,
    },
    {
        "id": 4,
        "name": "Rhino-4 Heavy Support Team",
        "callsign": "RHINO-COMMAND",
        "bop": "BOP-04 (Sector Delta)",
        "current_sector": "Sector Delta Base Depot",
        "status": "BASE_RESERVE",
        "strength": 12,
        "commander": "Inspector A. D. Joshi",
        "vehicle": "Tata Armored Troop Carrier",
        "weapons_readiness": "Heavy Suppressive (INSAS LMG, Anti-Material Rifle)",
        "radio_channel": "VHF Command Net (148.500 MHz Encrypted)",
        "assigned_incident": None,
        "eta_minutes": 0,
        "last_sitrep": "Sector reserve ready for second-tier reinforcement.",
        "fuel_percent": 89,
    },
]

DISPATCH_LOGS = [
    {
        "id": 1,
        "team_name": "Cheetah-1 QRT Strike Unit",
        "incident_code": "IBVAP-DEMO-INTRUSION-01",
        "target_sector": "BOP-01 Gate Sector Alpha",
        "action": "Vectored to Virtual Fence Breach",
        "status": "MISSION_COMPLETED",
        "timestamp": (datetime.utcnow() - timedelta(hours=1, minutes=20)).isoformat(),
    }
]

@router.get("/teams")
def list_qrt_teams(user: dict = Depends(require_permission("read"))):
    """List all deployed Quick Reaction Team units and real-time operational status."""
    return QRT_TEAMS

@router.post("/dispatch")
def dispatch_qrt(
    data: DispatchIn,
    user: dict = Depends(require_permission("qrt_dispatch")),
):
    """Scramble and vector a Quick Reaction Team to an incident location."""
    team = next((t for t in QRT_TEAMS if t["id"] == data.team_id), None)
    if not team:
        raise HTTPException(404, "QRT unit not found")

    eta = random.randint(3, 7)
    team["status"] = "EN_ROUTE"
    team["assigned_incident"] = data.incident_id
    team["current_sector"] = data.target_sector
    team["eta_minutes"] = eta
    team["last_sitrep"] = f"DISPATCH FLASH: En route to {data.target_sector}. ETA: {eta} minutes. Weapons hot."

    log = {
        "id": len(DISPATCH_LOGS) + 1,
        "team_name": team["name"],
        "incident_code": f"INCIDENT-REF-{data.incident_id}",
        "target_sector": data.target_sector,
        "action": data.orders,
        "status": "EN_ROUTE",
        "timestamp": datetime.utcnow().isoformat(),
    }
    DISPATCH_LOGS.insert(0, log)
    return {"dispatched": True, "team": team, "eta_minutes": eta, "log": log}

@router.post("/status")
def update_qrt_status(
    data: StatusUpdateIn,
    user: dict = Depends(require_permission("qrt_status")),
):
    """Update mission engagement status of a QRT unit."""
    team = next((t for t in QRT_TEAMS if t["id"] == data.team_id), None)
    if not team:
        raise HTTPException(404, "QRT unit not found")

    team["status"] = data.status
    if data.notes:
        team["last_sitrep"] = data.notes
    if data.status in ("STANDBY_IMMEDIATE", "BASE_RESERVE", "SECURED"):
        team["eta_minutes"] = 0
        team["assigned_incident"] = None

    return team

@router.get("/logs")
def list_dispatch_logs(user: dict = Depends(require_permission("read"))):
    """Get historical QRT deployment & engagement ledger."""
    return DISPATCH_LOGS

@router.post("/radio/broadcast")
def radio_broadcast(
    data: RadioBroadcastIn,
    user: dict = Depends(require_permission("qrt_broadcast")),
):
    """Simulate encrypted tactical VHF radio SITREP broadcast."""
    return {
        "broadcast_id": random.randint(10000, 99999),
        "callsign": data.callsign,
        "frequency": "142.850 MHz Encrypted (Type-1 NSA/DRDO Crypto)",
        "priority": data.priority,
        "message": data.message,
        "transmitted_at": datetime.utcnow().isoformat(),
    }
