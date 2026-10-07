"""
IBVAP — Real Media Upload & Video Asset Management Endpoints
Provides secure multipart file uploads, video metadata probing with OpenCV,
path traversal validation, and streaming media services.
"""

import hashlib
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import cv2
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.db.session import get_db
from backend.app.models.media_asset import MediaAsset
from backend.app.api.deps import current_user, require_permission

router = APIRouter()


class MediaAssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    original_filename: str
    file_size: int
    sha256: str
    duration_seconds: float
    width: int
    height: int
    fps: float
    total_frames: int
    status: str
    uploaded_by: str
    created_at: datetime


def _sanitize_filename(filename: str) -> str:
    """Sanitize filename to prevent directory traversal and shell injection."""
    clean = re.sub(r"[^\w\.-]", "_", os.path.basename(filename))
    return clean or "uploaded_video.mp4"


def _probe_video_metadata(file_path: str) -> dict:
    """Probe video dimensions, frame count, framerate, and duration using OpenCV."""
    # Check if image file
    _, ext = os.path.splitext(file_path)
    if ext.lower() in (".jpg", ".jpeg", ".png", ".webp"):
        img = cv2.imread(file_path)
        if img is not None:
            ih, iw = img.shape[:2]
            return {
                "width": iw,
                "height": ih,
                "fps": 1.0,
                "total_frames": 1,
                "duration_seconds": 1.0,
                "valid": True,
            }

    cap = cv2.VideoCapture(file_path)
    if not cap.isOpened():
        img = cv2.imread(file_path)
        if img is not None:
            ih, iw = img.shape[:2]
            return {
                "width": iw,
                "height": ih,
                "fps": 1.0,
                "total_frames": 1,
                "duration_seconds": 1.0,
                "valid": True,
            }
        return {
            "width": 0,
            "height": 0,
            "fps": 0.0,
            "total_frames": 0,
            "duration_seconds": 0.0,
            "valid": False,
        }

    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

    # Estimate duration
    duration = (total_frames / fps) if (fps > 0 and total_frames > 0) else 0.0

    cap.release()
    return {
        "width": width,
        "height": height,
        "fps": fps,
        "total_frames": total_frames,
        "duration_seconds": round(duration, 2),
        "valid": True,
    }


def _validate_media_magic_bytes(header: bytes, ext: str) -> None:
    """
    Inspect magic bytes of uploaded content against expected signatures.
    Rejects executables, scripts, HTML/SVG, and format mismatches.
    """
    if not header:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty (0 bytes).")

    # Reject executable headers
    if header.startswith(b"MZ"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Windows executable binary (.exe/.dll) detected.")
    if header.startswith(b"\x7fELF"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Linux ELF binary detected.")
    if len(header) >= 4 and header[:4] in (b"\xca\xfe\xba\xbe", b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Mach-O executable binary detected.")
    if header.startswith(b"#!"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Executable shell script header detected.")

    # Reject HTML, XML, SVG scripts
    head_stripped = header.lstrip().lower()
    dangerous_tags = (b"<!doctype", b"<html", b"<svg", b"<?xml", b"<?php", b"<script", b"<iframe")
    if any(head_stripped.startswith(t) for t in dangerous_tags) or b"<script" in head_stripped or b"<svg" in head_stripped:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="HTML/SVG script injection detected in uploaded media.")

    # Extension-specific magic bytes
    ext = ext.lower()
    if ext in ("mp4", "mov"):
        # MP4/MOV: ISO box header has 4-byte size followed by box type at offset 4 (e.g. ftyp, moov, mdat, wide, free, skip)
        # or ftyp / moov appears in the first 128 bytes
        has_ftyp_or_box = (
            (len(header) >= 8 and header[4:8] in (b"ftyp", b"moov", b"mdat", b"wide", b"free", b"skip"))
            or b"ftyp" in header[:128]
            or b"moov" in header[:128]
        )
        if not has_ftyp_or_box:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid MP4/MOV container magic bytes.")
    elif ext == "avi":
        if not (header.startswith(b"RIFF") and len(header) >= 12 and header[8:12] == b"AVI "):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid AVI container magic bytes.")
    elif ext in ("mkv", "webm"):
        if not header.startswith(b"\x1a\x45\xdf\xa3"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid Matroska/WebM container magic bytes.")
    elif ext in ("jpg", "jpeg"):
        if not header.startswith(b"\xff\xd8\xff"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JPEG image magic bytes.")
    elif ext == "png":
        if not header.startswith(b"\x89PNG\r\n\x1a\n"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid PNG image magic bytes.")
    elif ext == "webp":
        if not (header.startswith(b"RIFF") and len(header) >= 12 and header[8:12] == b"WEBP"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid WebP image magic bytes.")


@router.post("/upload", response_model=MediaAssetOut, status_code=status.HTTP_201_CREATED)
async def upload_video(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("write")),
):
    """
    Upload a real video or image file for computer vision analysis.
    Validates magic bytes, prevents path traversal, enforces type-specific size limits,
    and validates decodeability with OpenCV.
    """
    raw_filename = file.filename or "video.mp4"
    clean_name = _sanitize_filename(raw_filename)

    # Validate file extension
    ext = clean_name.split(".")[-1].lower() if "." in clean_name else ""
    allowed_exts = [e.strip().lower() for e in settings.allowed_video_extensions.split(",") if e.strip()]
    if ext not in allowed_exts:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported video format: '.{ext}'. Allowed formats: {', '.join(allowed_exts)}",
        )

    # Size threshold: 20MB for images, 500MB (or settings) for video
    is_image = ext in ("jpg", "jpeg", "png", "webp")
    max_bytes = (20 * 1024 * 1024) if is_image else (settings.max_upload_size_mb * 1024 * 1024)

    # Inspect initial magic bytes before streaming full payload to disk
    first_chunk = await file.read(8192)
    if not first_chunk or len(first_chunk) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty (0 bytes).",
        )
    _validate_media_magic_bytes(first_chunk, ext)

    # Target upload path
    upload_dir = Path(settings.upload_dir).resolve()
    upload_dir.mkdir(parents=True, exist_ok=True)

    stored_filename = f"{uuid.uuid4().hex}_{clean_name}"
    target_path = (upload_dir / stored_filename).resolve()

    # Prevent path traversal
    try:
        if not target_path.is_relative_to(upload_dir):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid target file path")
    except (ValueError, AttributeError):
        if not str(target_path).startswith(str(upload_dir)):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid target file path")

    # Read and stream to disk with SHA-256 calculation and size limit enforcement
    hasher = hashlib.sha256()
    hasher.update(first_chunk)
    total_bytes = len(first_chunk)

    if total_bytes > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Uploaded file exceeds maximum limit of {max_bytes // (1024 * 1024)} MB",
        )

    try:
        with open(target_path, "wb") as f_out:
            f_out.write(first_chunk)
            while True:
                chunk = await file.read(1024 * 1024)  # 1MB chunks
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > max_bytes:
                    target_path.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"Uploaded file exceeds maximum limit of {max_bytes // (1024 * 1024)} MB",
                    )
                hasher.update(chunk)
                f_out.write(chunk)

        sha256_hash = hasher.hexdigest()

        # Re-encode to H.264 for universal HTML5 browser playback if ffmpeg is available
        from backend.app.services.evidence import transcode_and_seal_clip
        final_path, final_sha256, is_playable = transcode_and_seal_clip(str(target_path))
        target_path = Path(final_path)
        total_bytes = os.path.getsize(final_path)
        sha256_hash = final_sha256

        # Probe real video metadata with OpenCV
        meta = _probe_video_metadata(str(target_path))
        if not meta.get("valid", False):
            target_path.unlink(missing_ok=True)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded media file failed integrity decode probe.",
            )

        asset = MediaAsset(
            original_filename=clean_name,
            stored_filename=stored_filename,
            file_path=str(target_path),
            mime_type=file.content_type or (f"image/{ext}" if is_image else f"video/{ext}"),
            file_size=total_bytes,
            sha256=sha256_hash,
            duration_seconds=meta["duration_seconds"],
            width=meta["width"],
            height=meta["height"],
            fps=meta["fps"],
            total_frames=meta["total_frames"],
            status="READY",
            uploaded_by=user.get("sub", "operator"),
        )

        db.add(asset)
        db.commit()
        db.refresh(asset)
        return asset

    except HTTPException:
        target_path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        target_path.unlink(missing_ok=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Upload failed: {str(exc)}")


@router.get("", response_model=List[MediaAssetOut])
@router.get("/", response_model=List[MediaAssetOut])
def list_media_assets(
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("read")),
):
    """List all uploaded video media assets."""
    return db.query(MediaAsset).order_by(MediaAsset.created_at.desc()).all()


@router.get("/{media_id}", response_model=MediaAssetOut)
def get_media_asset(
    media_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("read")),
):
    """Retrieve metadata for a specific uploaded media asset."""
    asset = db.get(MediaAsset, media_id)
    if not asset:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media asset not found")
    return asset


@router.get("/{media_id}/stream")
def stream_media_file(
    media_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("read")),
):
    """Stream uploaded video file for HTML5 video player playback."""
    asset = db.get(MediaAsset, media_id)
    if not asset or not os.path.exists(asset.file_path):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video file not found on disk")

    return FileResponse(
        path=asset.file_path,
        media_type=asset.mime_type,
        filename=asset.original_filename,
        content_disposition_type="inline",
        headers={"Accept-Ranges": "bytes"},
    )


@router.delete("/{media_id}")
def delete_media_asset(
    media_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("delete")),
):
    """Delete an uploaded media asset and remove file from disk."""
    asset = db.get(MediaAsset, media_id)
    if not asset:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media asset not found")

    if os.path.exists(asset.file_path):
        try:
            os.unlink(asset.file_path)
        except OSError:
            pass

    db.delete(asset)
    db.commit()
    return {"deleted": True, "media_id": media_id}


@router.get("/samples/list")
def list_sample_fixtures(user: dict = Depends(require_permission("read"))):
    """List available air-gapped demo video fixtures in samples/ directory."""
    samples_dir = Path("samples").resolve()
    if not samples_dir.exists():
        return []
    items = []
    for f in sorted(samples_dir.glob("*.mp4")):
        meta = _probe_video_metadata(str(f))
        items.append({
            "filename": f.name,
            "size_bytes": f.stat().st_size,
            "duration_seconds": meta.get("duration_seconds", 0.0),
            "width": meta.get("width", 0),
            "height": meta.get("height", 0),
            "fps": meta.get("fps", 0.0),
        })
    return items


@router.post("/samples/import", response_model=MediaAssetOut, status_code=status.HTTP_201_CREATED)
def import_sample_fixture(
    filename: str = Form(..., description="Filename in samples/, e.g. day_crossing.mp4"),
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("write")),
):
    """Import a sample video fixture as an active media asset for analysis."""
    safe_name = _sanitize_filename(filename)
    source_file = Path("samples").resolve() / safe_name
    if not source_file.exists():
        raise HTTPException(status_code=404, detail=f"Sample fixture '{safe_name}' not found in samples/")

    # Check if already imported
    existing = db.query(MediaAsset).filter(MediaAsset.original_filename == safe_name).first()
    if existing and os.path.exists(existing.file_path):
        return existing

    storage_dir = Path(settings.storage_dir) / "uploads"
    storage_dir.mkdir(parents=True, exist_ok=True)
    target_path = storage_dir / f"sample_{uuid.uuid4().hex[:8]}_{safe_name}"

    with open(source_file, "rb") as src, open(target_path, "wb") as dst:
        dst.write(src.read())

    sha256_hash = hashlib.sha256()
    with open(target_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha256_hash.update(chunk)
    file_sha256 = sha256_hash.hexdigest()

    meta = _probe_video_metadata(str(target_path))

    asset = MediaAsset(
        filename=target_path.name,
        original_filename=safe_name,
        file_path=str(target_path),
        file_size=target_path.stat().st_size,
        mime_type="video/mp4",
        sha256=file_sha256,
        duration_seconds=meta.get("duration_seconds", 0.0),
        width=meta.get("width", 0),
        height=meta.get("height", 0),
        fps=meta.get("fps", 0.0),
        total_frames=meta.get("total_frames", 0),
        status="ready",
        uploaded_by="operator",
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return asset
