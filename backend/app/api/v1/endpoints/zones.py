"""Zone management endpoints."""

from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.app.db.session import get_db
from backend.app.models.zone import Zone
from backend.app.schemas.common import ZoneIn, ZonePatchIn, ZoneOut
from backend.app.services.audit import log_action
from backend.app.api.deps import current_user, require_permission

router = APIRouter()


@router.get("", response_model=list[ZoneOut])
def list_zones(
    camera_id: int | None = None,
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("read")),
):
    """List zones, optionally filtered by camera."""
    q = db.query(Zone)
    if camera_id is not None:
        q = q.filter(Zone.camera_id == camera_id)
    return q.all()


@router.post("", response_model=ZoneOut, status_code=201)
def add_zone(
    data: ZoneIn,
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("manage_zones")),
):
    """Create a new zone with validation."""
    dump = data.model_dump()
    dump["created_by"] = user.get("sub", "operator")
    z = Zone(**dump)
    db.add(z)
    db.commit()
    db.refresh(z)
    log_action(db, user["sub"], user.get("role", ""), "CREATE", "zone",
               str(z.id), {"name": z.name, "zone_type": z.zone_type})
    return z


@router.get("/{zone_id}", response_model=ZoneOut)
def get_zone(
    zone_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("read")),
):
    """Get zone details."""
    z = db.get(Zone, zone_id)
    if not z:
        raise HTTPException(404, "Zone not found")
    return z


@router.put("/{zone_id}", response_model=ZoneOut)
def update_zone(
    zone_id: int,
    data: ZoneIn,
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("manage_zones")),
):
    """Update an entire zone definition."""
    z = db.get(Zone, zone_id)
    if not z:
        raise HTTPException(404, "Zone not found")
    for k, v in data.model_dump().items():
        setattr(z, k, v)
    db.commit()
    db.refresh(z)
    log_action(db, user["sub"], user.get("role", ""), "UPDATE", "zone",
               str(z.id), {"name": z.name})
    return z


@router.patch("/{zone_id}", response_model=ZoneOut)
def patch_zone(
    zone_id: int,
    data: ZonePatchIn,
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("manage_zones")),
):
    """Partially update a zone."""
    z = db.get(Zone, zone_id)
    if not z:
        raise HTTPException(404, "Zone not found")
    updates = data.model_dump(exclude_unset=True)
    for k, v in updates.items():
        if v is not None:
            setattr(z, k, v)
    # Sync geometry/polygon if either was updated
    if "geometry" in updates and updates["geometry"] and not updates.get("polygon"):
        if updates["geometry"].get("points"):
            z.polygon = updates["geometry"]["points"]
    elif "polygon" in updates and updates["polygon"] and not updates.get("geometry"):
        z.geometry = {"type": "polygon", "points": updates["polygon"]}

    db.commit()
    db.refresh(z)
    log_action(db, user["sub"], user.get("role", ""), "PATCH", "zone",
               str(z.id), {"updated_fields": list(updates.keys())})
    return z


@router.delete("/{zone_id}")
def delete_zone(
    zone_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("manage_zones")),
):
    """Delete a zone."""
    z = db.get(Zone, zone_id)
    if not z:
        raise HTTPException(404, "Zone not found")
    db.delete(z)
    db.commit()
    log_action(db, user["sub"], user.get("role", ""), "DELETE", "zone",
               str(zone_id))
    return {"deleted": True, "zone_id": zone_id}
