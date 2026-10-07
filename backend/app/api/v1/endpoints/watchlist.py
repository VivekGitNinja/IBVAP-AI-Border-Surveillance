"""Watchlist management and facial enrollment API endpoints."""

from datetime import datetime
import os
import uuid
from typing import List, Optional
import cv2
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session
from sqlalchemy import desc

from backend.app.db.session import get_db
from backend.app.models.watchlist import Watchlist
from backend.app.models.incident import Incident
from backend.app.services.face import face_service, STORAGE_FACES_DIR
from backend.app.api.deps import require_permission

router = APIRouter()


class WatchlistOut(BaseModel):
    id: int
    name: str
    face_image_path: str
    has_embedding: bool
    notes: str
    created_by: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


@router.get("", response_model=List[WatchlistOut])
def list_watchlist(
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("read")),
):
    """List all enrolled watchlist targets."""
    subjects = db.query(Watchlist).order_by(desc(Watchlist.created_at)).all()
    results = []
    for s in subjects:
        valid_img = s.face_image_path if (s.face_image_path and s.face_image_path != "none" and os.path.exists(s.face_image_path)) else None
        results.append(WatchlistOut(
            id=s.id,
            name=s.name,
            face_image_path=valid_img,
            has_embedding=bool(s.embedding),
            notes=s.notes,
            created_by=s.created_by,
            created_at=s.created_at,
            updated_at=s.updated_at,
        ))
    return results


@router.post("/enroll", response_model=WatchlistOut, status_code=status.HTTP_201_CREATED)
async def enroll_target(
    name: str = Form(..., min_length=2, max_length=120),
    notes: str = Form(""),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("write")),
):
    """Enroll a new facial recognition suspect/target into the watchlist."""
    os.makedirs(STORAGE_FACES_DIR, exist_ok=True)
    ext = os.path.splitext(file.filename or "face.jpg")[1] or ".jpg"
    filename = f"{uuid.uuid4().hex}{ext}"
    target_path = os.path.join(STORAGE_FACES_DIR, filename)

    contents = await file.read()
    with open(target_path, "wb") as f:
        f.write(contents)

    # Attempt to load and compute embedding
    img = cv2.imread(target_path)
    if img is None:
        if os.path.exists(target_path):
            os.remove(target_path)
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid image file")

    embedding, err = face_service.enroll_face(img)
    if embedding is None:
        if os.path.exists(target_path):
            os.remove(target_path)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=err or "no detectable face",
        )

    subject = Watchlist(
        name=name.strip(),
        face_image_path=target_path,
        embedding=embedding,
        notes=notes.strip(),
        created_by=user.get("sub", "operator"),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(subject)
    db.commit()
    db.refresh(subject)

    return WatchlistOut(
        id=subject.id,
        name=subject.name,
        face_image_path=subject.face_image_path,
        has_embedding=bool(subject.embedding),
        notes=subject.notes,
        created_by=subject.created_by,
        created_at=subject.created_at,
        updated_at=subject.updated_at,
    )


@router.delete("/{subject_id}")
def delete_target(
    subject_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("delete")),
):
    """Delete an enrolled subject from the watchlist."""
    subject = db.query(Watchlist).filter(Watchlist.id == subject_id).first()
    if not subject:
        raise HTTPException(status_code=404, detail=f"Subject {subject_id} not found")

    if subject.face_image_path and os.path.exists(subject.face_image_path):
        try:
            os.remove(subject.face_image_path)
        except OSError:
            pass

    db.delete(subject)
    db.commit()
    return {"status": "success", "message": f"Subject {subject_id} deleted"}


@router.get("/{subject_id}/image")
def get_target_image(
    subject_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("read")),
):
    """Serve the enrolled face crop for authenticated users."""
    subject = db.query(Watchlist).filter(Watchlist.id == subject_id).first()
    if not subject or not subject.face_image_path:
        raise HTTPException(status_code=404, detail="Subject image not found")

    if not os.path.exists(subject.face_image_path):
        raise HTTPException(status_code=404, detail="Image file missing from storage")

    return FileResponse(subject.face_image_path)


@router.get("/matches/recent")
def list_recent_matches(
    limit: int = 20,
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("read")),
):
    """Get recent facial watchlist match incidents."""
    matches = (
        db.query(Incident)
        .filter(Incident.title.like("%Watchlist%"))
        .order_by(desc(Incident.created_at))
        .limit(limit)
        .all()
    )
    return matches
