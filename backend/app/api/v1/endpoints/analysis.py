"""
IBVAP — Computer Vision Analysis Job Endpoints
Manages video analysis lifecycle: job submission, progress querying,
cancellation, and detailed forensic results retrieval.
"""

import os
import sys
import time
import uuid
import hashlib
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

import cv2
import numpy as np

from backend.app.core.config import settings

from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from backend.app.db.session import get_db
from backend.app.models.analysis_job import AnalysisJob
from backend.app.models.media_asset import MediaAsset
from backend.app.models.camera import Camera
from backend.app.models.detection import Detection
from backend.app.models.incident import Incident
from backend.app.models.evidence import Evidence
from backend.app.models.plate_read import PlateRead
from backend.app.services.video_analysis import VideoAnalysisEngine
from backend.app.api.deps import require_permission

router = APIRouter()


class CreateAnalysisJobIn(BaseModel):
    source_type: str = Field(default="upload", description="upload, rtsp, camera")
    source_id: Optional[int] = Field(default=None, description="MediaAsset ID or Camera ID")
    source_url: Optional[str] = Field(default="", description="RTSP URL or stream path")
    detector_model: Optional[str] = Field(default="yolo26n", description="yolo26n, yolo11n, motion")
    confidence_threshold: Optional[float] = Field(default=0.25, ge=0.05, le=0.95)
    zone_ids: Optional[List[int]] = Field(default=None, description="Ad-hoc zone IDs to evaluate")
    enable_anpr: Optional[bool] = Field(default=False, description="Enable automatic number plate recognition")
    enable_tracking: Optional[bool] = Field(default=True, description="Enable multi-object tracking")
    enable_behavior: Optional[bool] = Field(default=True, description="Enable behavior analytics (loitering, crowd, rapid)")
    enable_night_mode: Optional[bool] = Field(default=True, description="Enable night detection & CLAHE enhancement")
    enable_face: Optional[bool] = Field(default=False, description="Enable face detection / watchlist matching")


class AnalysisJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_type: str
    source_id: Optional[int]
    source_url: str
    status: str
    progress_percent: float
    total_frames: int
    processed_frames: int
    detections_count: int
    incidents_count: int
    fps: float
    detector_model: str
    confidence_threshold: float
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    error_message: str
    summary: dict
    created_at: datetime


@router.post("/jobs", response_model=AnalysisJobOut, status_code=status.HTTP_201_CREATED)
def create_analysis_job(
    data: CreateAnalysisJobIn,
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("write")),
):
    """
    Create and launch an asynchronous computer vision analysis job for an uploaded video, RTSP, or camera.
    """
    # 1. Validate source existence
    source_type = "upload" if data.source_type in ("upload", "media") else data.source_type

    if source_type == "upload":
        if not data.source_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="source_id is required for uploaded media")
        asset = db.get(MediaAsset, data.source_id)
        if not asset:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"MediaAsset #{data.source_id} not found")
    elif source_type == "camera":
        if not data.source_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="source_id is required for camera source")
        cam = db.get(Camera, data.source_id)
        if not cam:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Camera #{data.source_id} not found")
    elif source_type == "rtsp":
        if not data.source_url:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="source_url is required for rtsp source")
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unsupported source_type: {data.source_type}")

    # 2. Create job record
    job = AnalysisJob(
        source_type=source_type,
        source_id=data.source_id,
        source_url=data.source_url or "",
        status="queued",
        detector_model=data.detector_model or "yolo26n",
        confidence_threshold=data.confidence_threshold or 0.25,
        summary={
            "zone_ids": data.zone_ids or [],
            "enable_anpr": bool(data.enable_anpr),
            "enable_tracking": bool(data.enable_tracking),
            "enable_behavior": bool(data.enable_behavior),
            "enable_night_mode": bool(data.enable_night_mode),
            "enable_face": bool(data.enable_face),
        },
        created_by=user.get("sub", "operator"),
    )
    db.add(job)
    db.commit()
    db.refresh(job)


    # 3. Launch background worker thread
    VideoAnalysisEngine.start_job(job.id)

    return job


@router.get("/jobs", response_model=List[AnalysisJobOut])
def list_analysis_jobs(
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("read")),
):
    """List all computer vision analysis jobs."""
    return db.query(AnalysisJob).order_by(AnalysisJob.created_at.desc()).all()


@router.get("/jobs/{job_id}", response_model=AnalysisJobOut)
def get_analysis_job(
    job_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("read")),
):
    """Retrieve current status and progress of an analysis job."""
    job = db.get(AnalysisJob, job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Analysis job not found")
    return job


@router.get("/jobs/{job_id}/results")
def get_analysis_results(
    job_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("read")),
):
    """Retrieve full analysis report including detections, incidents, and forensic evidence."""
    job = db.get(AnalysisJob, job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Analysis job not found")

    detections = (
        db.query(Detection)
        .filter(Detection.job_id == job_id)
        .order_by(Detection.frame_index.asc())
        .limit(200)
        .all()
    )

    incidents = (
        db.query(Incident)
        .filter(Incident.job_id == job_id)
        .order_by(Incident.created_at.asc())
        .all()
    )

    incident_ids = [inc.id for inc in incidents]
    evidence_list = []
    if incident_ids:
        evidence_list = (
            db.query(Evidence)
            .filter(Evidence.incident_id.in_(incident_ids))
            .all()
        )

    # Source info
    source_name = "Unknown Source"
    if job.source_type == "upload" and job.source_id:
        asset = db.get(MediaAsset, job.source_id)
        if asset:
            source_name = asset.original_filename
    elif job.source_type == "camera" and job.source_id:
        cam = db.get(Camera, job.source_id)
        if cam:
            source_name = cam.name
    elif job.source_type == "rtsp":
        source_name = job.source_url

    # Plate reads for this analysis job
    plate_reads = (
        db.query(PlateRead)
        .filter(PlateRead.job_id == job_id)
        .order_by(PlateRead.frame_index.asc())
        .all()
    )

    return {
        "job_id": job.id,
        "source_name": source_name,
        "source_type": job.source_type,
        "source_id": job.source_id,
        "source_url": job.source_url,
        "status": job.status,
        "total_frames": job.total_frames,
        "processed_frames": job.processed_frames,
        "fps": job.fps,
        "detector_model": job.detector_model,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "summary": job.summary,
        "detections_count": job.detections_count,
        "incidents_count": job.incidents_count,
        "plate_reads": [
            {
                "id": p.id,
                "plate_number": p.plate_text,
                "plate_text": p.plate_text,
                "confidence": p.confidence,
                "frame_index": p.frame_index,
                "timestamp_ms": p.timestamp_ms,
                "bbox": p.bbox or {},
                "vehicle_type": getattr(p, "vehicle_type", None) or "Motor Vehicle",
            }
            for p in plate_reads
        ],
        "detections": [
            {
                "id": d.id,
                "frame": d.frame_index,
                "label": d.label,
                "confidence": d.confidence,
                "track_id": d.track_id,
                "bbox": [d.bbox_x1, d.bbox_y1, d.bbox_x2, d.bbox_y2],
                "bbox_x1": d.bbox_x1,
                "bbox_y1": d.bbox_y1,
                "bbox_x2": d.bbox_x2,
                "bbox_y2": d.bbox_y2,
                "timestamp_ms": d.payload.get("timestamp_ms", 0.0) if d.payload else 0.0,
                "plate_text": (d.payload or {}).get("plate_text"),
                "plate_conf": (d.payload or {}).get("plate_conf", 0.0),
                "plate_bbox": (d.payload or {}).get("plate_bbox"),
                "metadata": d.payload or {},
            }
            for d in detections
        ],
        "incidents": [
            {
                "id": inc.id,
                "code": inc.incident_code,
                "title": inc.title,
                "severity": inc.severity,
                "threat_score": inc.threat_score,
                "zone_name": inc.zone_name,
                "track_ids": inc.track_ids or [],
                "status": inc.status,
                "created_at": inc.created_at.isoformat() if inc.created_at else None,
            }
            for inc in incidents
        ],
        "evidence": [
            {
                "id": ev.id,
                "incident_id": ev.incident_id,
                "sha256": ev.sha256,
                "file_path": ev.file_path,
                "filename": os.path.basename(ev.file_path),
                "threat_score": ev.threat_score,
            }
            for ev in evidence_list
        ],
    }


@router.get("/jobs/{job_id}/tracks")
def get_analysis_job_tracks(
    job_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("read")),
):
    """
    Retrieve summarized multi-object tracks for an analysis job.
    Returns persistent track_id, class, first/last frame, downsampled normalized path,
    speed, zones touched, and night frame percentage.
    """
    import math
    from collections import Counter

    job = db.get(AnalysisJob, job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Analysis job not found")

    # Fetch frame dimensions for coordinate normalization
    frame_w = 1280
    frame_h = 720
    fps = job.fps if job.fps and job.fps > 0 else 15.0

    if job.source_type == "upload" and job.source_id:
        asset = db.get(MediaAsset, job.source_id)
        if asset and asset.width and asset.height and asset.width > 0 and asset.height > 0:
            frame_w = asset.width
            frame_h = asset.height
            if asset.fps and asset.fps > 0:
                fps = asset.fps

    # Fetch all detections for this job that have a track_id
    detections = (
        db.query(Detection)
        .filter(
            Detection.job_id == job_id,
            Detection.track_id.isnot(None),
            Detection.track_id != "",
        )
        .order_by(Detection.frame_index.asc(), Detection.id.asc())
        .all()
    )

    if not detections:
        return {"job_id": job_id, "tracks": [], "total_tracks": 0}

    # Dynamically detect frame bounds if default was used
    if frame_w == 1280 and frame_h == 720:
        max_x = max((d.bbox_x2 for d in detections), default=1280)
        max_y = max((d.bbox_y2 for d in detections), default=720)
        frame_w = max(max_x, 640)
        frame_h = max(max_y, 480)

    # Correlate incidents with tracks for zones_touched
    incidents = db.query(Incident).filter(Incident.job_id == job_id).all()
    track_zones: Dict[str, set] = {}
    for inc in incidents:
        if inc.zone_name and inc.track_ids:
            for tid in inc.track_ids:
                track_zones.setdefault(str(tid), set()).add(inc.zone_name)

    # Group detections by track_id
    track_groups: Dict[str, List[Detection]] = {}
    for d in detections:
        track_groups.setdefault(str(d.track_id), []).append(d)

    tracks_out = []
    for tid, t_dets in track_groups.items():
        if not t_dets:
            continue

        # Determine dominant class label
        classes = [d.label for d in t_dets if d.label]
        class_name = Counter(classes).most_common(1)[0][0] if classes else "unknown"

        first_frame = t_dets[0].frame_index
        last_frame = t_dets[-1].frame_index

        first_ts = (
            (t_dets[0].payload or {}).get("timestamp_ms")
            if t_dets[0].payload and "timestamp_ms" in t_dets[0].payload
            else round((first_frame / fps) * 1000.0, 1)
        )
        last_ts = (
            (t_dets[-1].payload or {}).get("timestamp_ms")
            if t_dets[-1].payload and "timestamp_ms" in t_dets[-1].payload
            else round((last_frame / fps) * 1000.0, 1)
        )

        # Centroid trajectory
        centroids = []
        speeds = []
        prev_cx = None
        prev_cy = None
        prev_t = None
        night_count = 0

        for d in t_dets:
            cx = (d.bbox_x1 + d.bbox_x2) / 2.0
            cy = (d.bbox_y1 + d.bbox_y2) / 2.0
            norm_x = round(min(1.0, max(0.0, cx / frame_w)), 4)
            norm_y = round(min(1.0, max(0.0, cy / frame_h)), 4)
            centroids.append([norm_x, norm_y, d.frame_index])

            t_ms = (
                (d.payload or {}).get("timestamp_ms")
                if d.payload and "timestamp_ms" in d.payload
                else (d.frame_index / fps) * 1000.0
            )

            if prev_cx is not None and prev_cy is not None and prev_t is not None:
                dt_sec = max(0.001, (t_ms - prev_t) / 1000.0)
                px_dist = math.sqrt((cx - prev_cx) ** 2 + (cy - prev_cy) ** 2)
                speed = px_dist / dt_sec
                speeds.append(speed)

            prev_cx = cx
            prev_cy = cy
            prev_t = t_ms

            if (d.payload or {}).get("night"):
                night_count += 1

        # Downsample trajectory if exceptionally dense
        if len(centroids) > 100:
            step = max(1, len(centroids) // 80)
            downsampled_path = centroids[::step]
            if downsampled_path[-1] != centroids[-1]:
                downsampled_path.append(centroids[-1])
        else:
            downsampled_path = centroids

        max_speed = round(max(speeds), 1) if speeds else 0.0
        night_pct = round((night_count / len(t_dets)) * 100.0, 1)
        zones = sorted(list(track_zones.get(str(tid), set())))

        tracks_out.append({
            "track_id": tid,
            "class": class_name,
            "first_frame": first_frame,
            "last_frame": last_frame,
            "first_timestamp_ms": first_ts,
            "last_timestamp_ms": last_ts,
            "path": downsampled_path,
            "points_count": len(downsampled_path),
            "max_speed": max_speed,
            "zones_touched": zones,
            "night_frame_pct": night_pct,
            "detections_count": len(t_dets),
        })

    # Sort tracks by first_frame asc
    tracks_out.sort(key=lambda x: x["first_frame"])

    return {
        "job_id": job_id,
        "tracks": tracks_out,
        "total_tracks": len(tracks_out),
    }



@router.post("/jobs/{job_id}/cancel")
def cancel_analysis_job(
    job_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("write")),
):
    """Cancel an ongoing analysis job."""
    job = db.get(AnalysisJob, job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Analysis job not found")

    user_role = user.get("role", "OPERATOR")
    user_sub = user.get("sub", "")
    if user_role not in ("ADMIN", "COMMANDER") and job.created_by and job.created_by != user_sub:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Operators may only cancel their own analysis jobs.",
        )

    cancelled = VideoAnalysisEngine.cancel_job(job_id)
    if not cancelled and job.status == "processing":
        job.status = "cancelled"
        db.commit()

    return {"cancelled": True, "job_id": job_id}


@router.get("/jobs/{job_id}/report")
def export_analysis_job_report(
    job_id: int,
    format: str = Query("json", pattern="^(json|pdf)$"),
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("read")),
):
    """Export comprehensive forensic & tactical report for an analysis job in JSON or PDF format."""
    from fastapi.responses import Response
    from backend.app.services.report import build_report_data, generate_pdf_report

    try:
        data = build_report_data(job_id, db)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))

    if format.lower() == "pdf":
        pdf_bytes = generate_pdf_report(data)
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="ibvap_report_job_{job_id}.pdf"',
                "X-Statutory-Authority": "Bharatiya Sakshya Adhiniyam, 2023 Section 63",
            },
        )

    return data


class LiveWindowRequest(BaseModel):
    camera_id: int
    seconds: int = Field(default=10, ge=1, le=300)
    detector_model: Optional[str] = Field(default="yolo26n")
    confidence_threshold: Optional[float] = Field(default=0.25, ge=0.05, le=0.95)
    enable_face: Optional[bool] = Field(default=True)
    enable_zones: Optional[bool] = Field(default=True)


@router.post("/live-window")
def run_live_window_analysis(
    req: LiveWindowRequest,
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("write")),
):
    """
    Execute a bounded live computer vision analysis window directly on a live camera / RTSP stream.
    Runs object detection (YOLO/motion), YuNet face detection, SFace watchlist matching,
    and virtual fence intrusion analysis.
    Produces real detections and tamper-evident BSA 2023 §63 incidents with source="live".
    """
    from backend.app.models.zone import Zone
    from backend.app.services.face import face_service
    from edge.zones.fence import ZoneFence
    from edge.tracking.centroid import CentroidTracker
    from edge.detection.factory import create_detector

    cam = db.get(Camera, req.camera_id)
    if not cam:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Camera #{req.camera_id} not found")

    url = cam.stream_url or ""
    if not url:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Camera #{req.camera_id} has no stream_url configured")

    # 1. Open VideoCapture
    try:
        if url.startswith("usb://") or url.startswith("camera://") or url.startswith("webcam://") or url.isdigit():
            dev_idx = int(url if url.isdigit() else (url.split("://")[-1] or "0"))
            backend = cv2.CAP_AVFOUNDATION if sys.platform == "darwin" else cv2.CAP_ANY
            cap = cv2.VideoCapture(dev_idx, backend)
        elif url.startswith("file://"):
            fpath = url.replace("file://", "")
            if not os.path.exists(fpath):
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Video file not found: {fpath}")
            cap = cv2.VideoCapture(fpath)
        elif url.startswith("rtsp://"):
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|timeout;3000000"
            cap = cv2.VideoCapture(url)
        else:
            cap = cv2.VideoCapture(url)

        if not cap.isOpened():
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Failed to open video capture for: {url}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error initializing stream capture: {e}")

    # 2. Setup AI Detector
    detector = create_detector(
        preferred=req.detector_model or "yolo26n",
        confidence_threshold=req.confidence_threshold or 0.25,
    )

    # 3. Setup Virtual Fence & Tracker
    fence = None
    tracker = None
    if req.enable_zones:
        active_zones = db.query(Zone).filter(Zone.camera_id == cam.id, Zone.enabled == True).all()
        if not active_zones:
            active_zones = db.query(Zone).filter(Zone.camera_id == None, Zone.enabled == True).all()
        if active_zones:
            zones_dict = [
                {
                    "id": z.id,
                    "name": z.name,
                    "zone_type": z.zone_type,
                    "geometry": z.geometry,
                    "polygon": z.polygon,
                    "direction": z.direction,
                    "armed_schedule": z.armed_schedule,
                    "night_only": z.night_only,
                    "min_confidence": z.min_confidence,
                    "severity": z.severity,
                    "dwell_threshold_seconds": z.dwell_threshold_seconds,
                }
                for z in active_zones
            ]
            fence = ZoneFence(
                zones=zones_dict,
                default_cooldown_seconds=settings.zone_cooldown_seconds,
                loitering_seconds=settings.loitering_seconds,
                crowd_min_count=settings.crowd_min_count,
                crowd_window_seconds=settings.crowd_window_seconds,
                rapid_speed_threshold=settings.rapid_speed_threshold,
            )
            tracker = CentroidTracker()

    evidence_dir = Path(settings.evidence_dir) / "live"
    evidence_dir.mkdir(parents=True, exist_ok=True)

    # 4. Live loop bounded by wall-clock seconds
    start_mono = time.monotonic()
    start_utc = datetime.now(timezone.utc)
    deadline = start_mono + req.seconds

    frame_count = 0
    total_detections = 0
    total_faces = 0
    total_matches = 0
    total_zone_intrusions = 0

    face_detections_log: List[Dict[str, Any]] = []
    created_incidents: List[Dict[str, Any]] = []
    sample_detections: List[Dict[str, Any]] = []
    latest_frame_path: Optional[str] = None
    watchlist_cooldown: Dict[int, float] = {}

    try:
        while time.monotonic() < deadline:
            ret, frame = cap.read()
            if not ret or frame is None:
                time.sleep(0.02)
                continue

            frame_count += 1
            frame_utc = datetime.now(timezone.utc)
            frame_iso = frame_utc.isoformat()
            h, w = frame.shape[:2]

            # A. Object detection
            dets = detector.detect(frame, frame_id=frame_count)
            for d in dets:
                total_detections += 1
                bx = [int(v) for v in d.bbox]
                det_rec = Detection(
                    camera_id=cam.id,
                    label=d.class_name,
                    confidence=float(d.confidence),
                    bbox_x1=bx[0],
                    bbox_y1=bx[1],
                    bbox_x2=bx[2],
                    bbox_y2=bx[3],
                    frame_index=frame_count,
                    source="live",
                    payload={"class_id": d.class_id, "live_wallclock": frame_iso},
                    detected_at=frame_utc,
                )
                db.add(det_rec)
                if len(sample_detections) < 20:
                    sample_detections.append({
                        "label": d.class_name,
                        "confidence": round(float(d.confidence), 3),
                        "bbox": bx,
                        "timestamp": frame_iso,
                    })

            # B. Zone intrusion evaluation
            if fence and tracker and dets:
                det_dicts = [{"bbox": d.bbox, "class_name": d.class_name, "confidence": d.confidence} for d in dets]
                tracks = tracker.update(det_dicts)
                alerts = fence.evaluate(tracks, current_timestamp=frame_utc.timestamp(), db_session=db)
                for alert in alerts:
                    total_zone_intrusions += 1
                    inc_code = f"INC-LIVE-CAM{cam.id}-ZN-{uuid.uuid4().hex[:6].upper()}"
                    ev_fn = f"ev_live_cam{cam.id}_zn_{frame_count}_{uuid.uuid4().hex[:4]}.jpg"
                    ev_fp = evidence_dir / ev_fn

                    ann = frame.copy()
                    abox = alert.get("bbox", (0, 0, 0, 0))
                    cv2.rectangle(ann, (int(abox[0]), int(abox[1])), (int(abox[2]), int(abox[3])), (0, 0, 255), 2)
                    cv2.putText(ann, f"ZONE BREACH: {alert.get('zone_name')}",
                                (int(abox[0]), max(25, int(abox[1]) - 10)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 0, 255), 2)
                    cv2.putText(ann, f"UTC: {frame_iso}", (15, h - 15),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
                    cv2.imwrite(str(ev_fp), ann)

                    with open(ev_fp, "rb") as ef:
                        ev_hash = hashlib.sha256(ef.read()).hexdigest()

                    inc = Incident(
                        incident_code=inc_code,
                        title=f"Live Zone Intrusion: {alert.get('zone_name')}",
                        description=alert.get("description") or f"Live boundary crossing detected on {cam.name}",
                        severity=alert.get("severity") or "HIGH",
                        threat_score=float(alert.get("threat_score") or 82.0),
                        confidence=float(alert.get("confidence") or 0.88),
                        status="OPEN",
                        camera_id=cam.id,
                        camera_name=cam.name,
                        reason_codes=["ZONE_INTRUSION", f"ZONE_{alert.get('zone_id')}"],
                        ai_assessment={
                            "source": "live",
                            "camera_id": cam.id,
                            "zone_name": alert.get("zone_name"),
                            "frame_index": frame_count,
                            "timestamp": frame_iso,
                            "statute": "BSA_2023_SEC_63",
                        },
                    )
                    db.add(inc)
                    db.flush()

                    ev = Evidence(
                        incident_id=inc.id,
                        evidence_type="snapshot",
                        file_path=str(ev_fp),
                        sha256=ev_hash,
                        manifest_path=str(ev_fp) + ".json",
                        manifest_data={"source": "live", "camera_id": cam.id, "statute": "BSA_2023_SEC_63"},
                    )
                    db.add(ev)
                    created_incidents.append({
                        "id": inc.id,
                        "code": inc.incident_code,
                        "title": inc.title,
                        "type": "ZONE_INTRUSION",
                        "severity": inc.severity,
                        "timestamp": frame_iso,
                        "evidence_hash": ev_hash,
                    })

            # C. Face detection & Watchlist matching
            if req.enable_face:
                detected_faces = face_service.detect_faces(frame, detections=dets)
                for f_info in detected_faces:
                    total_faces += 1
                    fb = f_info.get("bbox", {})
                    fx1 = max(0, int(fb.get("x1", 0.0) * w))
                    fy1 = max(0, int(fb.get("y1", 0.0) * h))
                    fx2 = min(w, int(fb.get("x2", 1.0) * w))
                    fy2 = min(h, int(fb.get("y2", 1.0) * h))

                    face_detections_log.append({
                        "frame": frame_count,
                        "bbox": [fx1, fy1, fx2, fy2],
                        "confidence": round(float(f_info.get("confidence", 0.8)), 3),
                        "timestamp": frame_iso,
                    })

                    fcrop = frame[fy1:fy2, fx1:fx2]
                    if fcrop.size == 0:
                        continue

                    f_emb = face_service.extract_embedding(fcrop, raw_face=f_info.get("raw_face"), full_frame=frame)
                    if f_emb is not None:
                        match_res = face_service.match_watchlist(f_emb, db)
                        if match_res:
                            subject, similarity = match_res
                            now_t = frame_utc.timestamp()
                            last_t = watchlist_cooldown.get(subject.id, 0.0)
                            if (now_t - last_t) >= 5.0:
                                watchlist_cooldown[subject.id] = now_t
                                total_matches += 1
                                inc_code = f"INC-LIVE-CAM{cam.id}-FRS-S{subject.id}-{uuid.uuid4().hex[:4].upper()}"
                                ev_fn = f"ev_live_cam{cam.id}_frs_{subject.id}_{frame_count}.jpg"
                                ev_fp = evidence_dir / ev_fn

                                ann = frame.copy()
                                cv2.rectangle(ann, (fx1, fy1), (fx2, fy2), (0, 0, 255), 2)
                                cv2.putText(
                                    ann,
                                    f"MATCH: {subject.name.upper()} ({similarity:.1%})",
                                    (fx1, max(25, fy1 - 10)),
                                    cv2.FONT_HERSHEY_SIMPLEX,
                                    0.65,
                                    (0, 0, 255),
                                    2,
                                )
                                cv2.putText(ann, f"UTC: {frame_iso}", (15, h - 15),
                                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
                                cv2.imwrite(str(ev_fp), ann)

                                with open(ev_fp, "rb") as ef:
                                    ev_hash = hashlib.sha256(ef.read()).hexdigest()

                                threat_score = round(min(100.0, 85.0 + similarity * 15.0), 1)
                                inc = Incident(
                                    incident_code=inc_code,
                                    title=f"Watchlist Match Alert: {subject.name} ({similarity:.1%} Match)",
                                    description=f"Facial recognition match confirmed for enrolled subject '{subject.name}' with similarity {similarity:.4f} on {cam.name}.",
                                    severity="CRITICAL",
                                    threat_score=threat_score,
                                    confidence=similarity,
                                    status="OPEN",
                                    camera_id=cam.id,
                                    camera_name=cam.name,
                                    reason_codes=["WATCHLIST_MATCH", f"SUBJECT_{subject.id}"],
                                    ai_assessment={
                                        "model": "OpenCV SFace",
                                        "subject_id": subject.id,
                                        "subject_name": subject.name,
                                        "similarity": similarity,
                                        "frame": frame_count,
                                        "timestamp": frame_iso,
                                        "source": "live",
                                        "legal_citation": "Bharatiya Sakshya Adhiniyam, 2023 — Section 63",
                                    },
                                )
                                db.add(inc)
                                db.flush()

                                ev = Evidence(
                                    incident_id=inc.id,
                                    evidence_type="snapshot",
                                    file_path=str(ev_fp),
                                    sha256=ev_hash,
                                    manifest_path=str(ev_fp) + ".json",
                                    manifest_data={
                                        "source": "live",
                                        "camera_id": cam.id,
                                        "subject_id": subject.id,
                                        "similarity": similarity,
                                        "statute": "BSA_2023_SEC_63",
                                    },
                                )
                                db.add(ev)
                                created_incidents.append({
                                    "id": inc.id,
                                    "code": inc.incident_code,
                                    "title": inc.title,
                                    "type": "WATCHLIST_MATCH",
                                    "severity": inc.severity,
                                    "timestamp": frame_iso,
                                    "similarity": similarity,
                                    "subject_name": subject.name,
                                    "evidence_hash": ev_hash,
                                })

            # Save latest frame snapshot
            if frame_count % 10 == 1 or time.monotonic() >= deadline:
                latest_fn = f"live_cam{cam.id}_latest.jpg"
                latest_fp = evidence_dir / latest_fn
                cv2.imwrite(str(latest_fp), frame)
                latest_frame_path = str(latest_fp)

            time.sleep(0.04)  # ~25 FPS pacing

    finally:
        cap.release()

    end_mono = time.monotonic()
    end_utc = datetime.now(timezone.utc)
    duration = end_mono - start_mono
    actual_fps = frame_count / duration if duration > 0 else 0.0

    db.commit()

    return {
        "success": True,
        "camera_id": cam.id,
        "camera_name": cam.name,
        "source": "live",
        "stream_url": url,
        "seconds_requested": req.seconds,
        "duration_seconds": round(duration, 2),
        "frames_processed": frame_count,
        "fps": round(actual_fps, 1),
        "detections_count": total_detections,
        "faces_detected": total_faces,
        "watchlist_matches": total_matches,
        "zone_intrusions": total_zone_intrusions,
        "incidents_count": len(created_incidents),
        "start_timestamp": start_utc.isoformat(),
        "end_timestamp": end_utc.isoformat(),
        "face_detections": face_detections_log[:50],
        "incidents": created_incidents,
        "sample_detections": sample_detections[:20],
        "latest_frame_path": latest_frame_path,
    }

