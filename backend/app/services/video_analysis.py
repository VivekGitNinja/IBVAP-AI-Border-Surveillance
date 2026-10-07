"""
IBVAP — Real Computer Vision Frame Analysis Engine
Processes actual video frames from uploaded media assets, RTSP feeds, or hardware devices.
Runs Ultralytics YOLO26/11 or OpenCV MOG2 motion detection, generates real detections,
prioritized security incidents, and tamper-evident SHA-256 evidence records.
"""

import asyncio
import hashlib
import json
import logging
import os
import sys
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any

import cv2
import numpy as np
from fastapi import WebSocket
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.db.session import SessionLocal
from backend.app.models.analysis_job import AnalysisJob
from backend.app.models.media_asset import MediaAsset
from backend.app.models.camera import Camera
from backend.app.models.detection import Detection
from backend.app.models.incident import Incident
from backend.app.models.evidence import Evidence
from backend.app.models.zone import Zone
from backend.app.models.plate_read import PlateRead
from backend.app.models.watchlist import Watchlist
from backend.app.models.alert import Alert
from backend.app.services.anpr import anpr_engine
from backend.app.services.face import face_service
from backend.app.services.evidence import transcode_and_seal_clip
from backend.app.services.c2 import dispatch_incident_webhook
from backend.app.services.live_pipeline import live_manager
from edge.detection.factory import create_detector
from edge.tracking.centroid import CentroidTracker
from edge.zones.fence import ZoneFence


logger = logging.getLogger("ibvap.analysis")

# WebSocket listeners subscribed to specific job IDs
_job_subscribers: Dict[int, List[WebSocket]] = {}
_subscriber_lock = threading.Lock()
_active_tasks: Dict[int, threading.Event] = {}
_active_threads: Dict[int, threading.Thread] = {}
_event_loop: Optional[asyncio.AbstractEventLoop] = None


def set_analysis_event_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _event_loop
    _event_loop = loop


def register_job_subscriber(job_id: int, ws: WebSocket) -> None:
    with _subscriber_lock:
        if job_id not in _job_subscribers:
            _job_subscribers[job_id] = []
        _job_subscribers[job_id].append(ws)


def unregister_job_subscriber(job_id: int, ws: WebSocket) -> None:
    with _subscriber_lock:
        if job_id in _job_subscribers:
            try:
                _job_subscribers[job_id].remove(ws)
            except ValueError:
                pass


def broadcast_job_event_sync(job_id: int, payload: dict) -> None:
    """Send real-time analysis progress or detection event to connected WebSockets."""
    with _subscriber_lock:
        sockets = list(_job_subscribers.get(job_id, []))
    if not sockets:
        return

    async def _send_all():
        dead = []
        for ws in sockets:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        if dead:
            with _subscriber_lock:
                for d in dead:
                    try:
                        _job_subscribers[job_id].remove(d)
                    except (ValueError, KeyError):
                        pass

    if _event_loop and _event_loop.is_running():
        asyncio.run_coroutine_threadsafe(_send_all(), _event_loop)


class VideoAnalysisEngine:
    """Executes deterministic computer vision analysis on real video files or streams."""

    _running_jobs = _active_threads

    @classmethod
    def start_job(cls, job_id: int) -> None:
        """Launch background worker thread for an analysis job."""
        cancel_event = threading.Event()
        _active_tasks[job_id] = cancel_event

        worker = threading.Thread(
            target=cls._run_analysis,
            args=(job_id, cancel_event),
            daemon=True,
            name=f"analysis-job-{job_id}",
        )
        _active_threads[job_id] = worker
        worker.start()

    @classmethod
    def cancel_job(cls, job_id: int) -> bool:
        if job_id in _active_tasks:
            _active_tasks[job_id].set()
            return True
        return False

    @classmethod
    def _run_analysis(cls, job_id: int, cancel_event: threading.Event) -> None:
        db: Session = SessionLocal()
        job = db.get(AnalysisJob, job_id)
        if not job:
            db.close()
            return

        try:
            job.status = "initializing"
            job.started_at = datetime.utcnow()
            db.commit()
            broadcast_job_event_sync(job_id, {"event": "job_progress", "job_id": job_id, "status": "initializing", "progress_percent": 0.0})

            # 1. Resolve media source
            source_path = ""
            media_id: Optional[int] = None
            camera_id: Optional[int] = None

            if job.source_type in ("upload", "media"):
                asset = db.get(MediaAsset, job.source_id)
                if not asset or not os.path.exists(asset.file_path):
                    raise FileNotFoundError(f"Uploaded media file not found: {job.source_id}")
                source_path = asset.file_path
                media_id = asset.id
            elif job.source_type == "camera":
                cam = db.get(Camera, job.source_id)
                if not cam:
                    raise ValueError(f"Camera ID {job.source_id} not found")
                source_path = cam.stream_url
                camera_id = cam.id
            elif job.source_type == "rtsp":
                source_path = job.source_url
            else:
                raise ValueError(f"Unknown source type: {job.source_type}")

            # 2. Open OpenCV VideoCapture
            if str(source_path).startswith("usb://") or str(source_path).startswith("camera://") or str(source_path).startswith("webcam://") or str(source_path).isdigit():
                dev_idx = int(source_path if str(source_path).isdigit() else (source_path.split("://")[-1] or "0"))
                backend = cv2.CAP_AVFOUNDATION if sys.platform == "darwin" else cv2.CAP_ANY
                cap = cv2.VideoCapture(dev_idx, backend)
            elif str(source_path).startswith("rtsp://"):
                os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"
                cap = cv2.VideoCapture(source_path)
            else:
                cap = cv2.VideoCapture(source_path)
            if not cap.isOpened():
                raise RuntimeError(f"OpenCV could not open video source: {source_path}")

            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
            if fps <= 0:
                fps = 25.0

            job.total_frames = total_frames
            job.status = "processing"
            db.commit()

            detector = create_detector(
                preferred=job.detector_model or "yolo26n",
                confidence_threshold=job.confidence_threshold or 0.25,
                min_area=settings.motion_min_area,
                persistence_frames=settings.motion_persistence_frames,
                conf_floor=settings.motion_conf_floor,
            )
            logger.info(f"Job {job_id}: Processing with {detector.name} ({total_frames} frames @ {fps:.1f} fps)")

            fallback_detector = None
            try:
                from edge.detection.motion import MotionDetector
                if not isinstance(detector, MotionDetector):
                    fallback_detector = create_detector(
                        "motion",
                        confidence_threshold=0.2,
                        min_area=settings.motion_min_area,
                        persistence_frames=settings.motion_persistence_frames,
                        conf_floor=settings.motion_conf_floor,
                    )
            except Exception:
                pass

            # 4. Resolve virtual fence zones for this job
            active_zones = []
            explicit_zone_ids = []
            if isinstance(job.summary, dict):
                explicit_zone_ids = job.summary.get("zone_ids") or []

            if explicit_zone_ids:
                active_zones = db.query(Zone).filter(Zone.id.in_(explicit_zone_ids), Zone.enabled == True).all()
            elif camera_id is not None:
                active_zones = db.query(Zone).filter(Zone.camera_id == camera_id, Zone.enabled == True).all()

            if not active_zones:
                # Include general / camera-agnostic active zones
                active_zones = db.query(Zone).filter(Zone.camera_id == None, Zone.enabled == True).all()

            zones_dict_list = [
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
                zones=zones_dict_list,
                default_cooldown_seconds=settings.zone_cooldown_seconds,
                loitering_seconds=settings.loitering_seconds,
                crowd_min_count=settings.crowd_min_count,
                crowd_window_seconds=settings.crowd_window_seconds,
                rapid_speed_threshold=settings.rapid_speed_threshold,
            )
            tracker = CentroidTracker()
            night_frames_count = 0
            baseline_threat_cooldown: Dict[str, float] = {}
            watchlist_match_cooldown: Dict[int, float] = {}

            job_summary = job.summary or {}
            enable_anpr = bool(job_summary.get("enable_anpr", settings.enable_anpr))
            enable_face = bool(job_summary.get("enable_face", settings.enable_face_recognition))
            annotated_clip_frames = []
            primary_incident_id = None

            frame_index = 0
            processed_frames = 0
            detections_recorded = 0
            incidents_recorded = 0
            start_proc_time = time.time()
            last_progress_broadcast = 0.0

            # Evidence output folder
            evidence_dir = Path(settings.evidence_dir) / "clips"
            evidence_dir.mkdir(parents=True, exist_ok=True)

            # Frame sampling rate (process every 1st or 2nd frame depending on length)
            frame_stride = 1 if total_frames < 300 else 2

            while not cancel_event.is_set():
                ret, frame = cap.read()
                if not ret or frame is None:
                    break

                frame_index += 1
                if frame_index % frame_stride != 0:
                    continue

                processed_frames += 1
                timestamp_ms = (frame_index / fps) * 1000.0
                frame_h, frame_w = frame.shape[:2]
                frame_dt = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc) + timedelta(milliseconds=timestamp_ms)

                # Night-time detection and optional CLAHE enhancement
                gray_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                mean_luma = float(np.mean(gray_frame))
                is_night = mean_luma < settings.night_luma_threshold
                if is_night:
                    night_frames_count += 1
                    if settings.night_enhance:
                        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
                        det_frame = cv2.cvtColor(clahe.apply(gray_frame), cv2.COLOR_GRAY2BGR)
                    else:
                        det_frame = frame
                else:
                    det_frame = frame

                # Run detection on frame
                detections = detector.detect(det_frame, frame_id=frame_index)
                if not detections and fallback_detector is not None:
                    detections = fallback_detector.detect(det_frame, frame_id=frame_index)

                # ANPR pipeline: extract license plates from vehicle detections
                if enable_anpr:
                    found_prs = anpr_engine.process_frame_vehicles(
                        frame=frame,
                        detections=detections,
                        job_id=job_id,
                        frame_index=frame_index,
                        timestamp_ms=timestamp_ms,
                        db_session=db,
                    )
                    if found_prs:
                        from backend.app.api.v1.endpoints.anpr import WATCHLIST_DB
                        for pr in found_prs:
                            p_clean = pr.plate_text.replace(" ", "").upper()
                            is_flagged = any(w["plate_number"].replace(" ", "").upper() == p_clean for w in WATCHLIST_DB)

                            last_plate_ts = watchlist_match_cooldown.get(f"plate_{p_clean}")
                            now_ts = frame_dt.timestamp()
                            if last_plate_ts is None or (now_ts - last_plate_ts >= 8.0):
                                watchlist_match_cooldown[f"plate_{p_clean}"] = now_ts
                                incidents_recorded += 1

                                sev = "CRITICAL" if is_flagged else "HIGH"
                                threat_score = 98.0 if is_flagged else 75.0
                                inc_code = f"INC-JOB{job_id}-ANPR-{p_clean}-{uuid.uuid4().hex[:4].upper()}"
                                ev_fn = f"ev_job_{job_id}_anpr_{p_clean}_{frame_index}.jpg"
                                ev_fp = evidence_dir / ev_fn

                                ann_p_frame = frame.copy()
                                cv2.putText(
                                    ann_p_frame,
                                    f"{'STOLEN LOOKOUT MATCH' if is_flagged else 'ANPR VEHICLE LOG'}: {pr.plate_text} ({pr.confidence:.0%})",
                                    (20, 40),
                                    cv2.FONT_HERSHEY_SIMPLEX,
                                    0.7,
                                    (0, 0, 255) if is_flagged else (0, 240, 255),
                                    2,
                                )
                                cv2.imwrite(str(ev_fp), ann_p_frame)
                                with open(ev_fp, "rb") as ef:
                                    ev_hash = hashlib.sha256(ef.read()).hexdigest()

                                inc = Incident(
                                    incident_code=inc_code,
                                    title=f"Stolen Vehicle Intercept: {pr.plate_text} (Barrier Triggered)" if is_flagged else f"Vehicle Plate Scanned: {pr.plate_text}",
                                    description=f"Vehicle registration plate '{pr.plate_text}' matched stolen vehicle database with {pr.confidence:.0%} confidence at {timestamp_ms/1000.0:.2f}s." if is_flagged else f"Vehicle license plate '{pr.plate_text}' identified via ANPR OCR.",
                                    severity=sev,
                                    threat_score=threat_score,
                                    confidence=pr.confidence,
                                    status="OPEN",
                                    camera_id=camera_id,
                                    camera_name=f"Upload Media #{media_id}" if media_id else "Camera Feed",
                                    job_id=job_id,
                                    media_id=media_id,
                                    reason_codes=["STOLEN_VEHICLE_MATCH" if is_flagged else "ANPR_VEHICLE_LOG", f"PLATE_{p_clean}"],
                                    ai_assessment={
                                        "model": "ANPR OCR",
                                        "plate_number": pr.plate_text,
                                        "confidence": pr.confidence,
                                        "stolen_flagged": is_flagged,
                                        "frame": frame_index,
                                        "timestamp_ms": round(timestamp_ms, 2),
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
                                    manifest_data={"job_id": job_id, "plate": pr.plate_text, "stolen": is_flagged, "statute": "BSA_2023_SEC_63"},
                                    file_size_bytes=os.path.getsize(ev_fp),
                                    threat_score=threat_score,
                                    camera_id=camera_id,
                                    camera_name=inc.camera_name,
                                    detection_metadata={"plate_text": pr.plate_text, "confidence": pr.confidence, "stolen": is_flagged},
                                )
                                db.add(ev)

                                alert = Alert(
                                    incident_id=inc.id,
                                    priority=sev,
                                    status="NEW",
                                    message=f"{sev}: Vehicle license plate '{pr.plate_text}' {'matched STOLEN WATCHLIST (Barrier Armed)' if is_flagged else 'logged via ANPR checkpost'} (Score: {threat_score:.0f}/100)",
                                )
                                db.add(alert)
                                db.flush()

                                try:
                                    live_manager._on_event({
                                        "type": "incident_created",
                                        "data": {
                                            "id": inc.id,
                                            "incident_code": inc.incident_code,
                                            "incident_type": inc.title,
                                            "severity": inc.severity,
                                            "threat_score": inc.threat_score,
                                            "confidence": inc.confidence,
                                            "bop": "Checkpost",
                                            "camera_name": inc.camera_name,
                                            "summary": inc.description,
                                        }
                                    })
                                except Exception:
                                    pass

                                dispatch_incident_webhook(
                                    {
                                        "incident_code": inc.incident_code,
                                        "title": inc.title,
                                        "severity": inc.severity,
                                        "threat_score": inc.threat_score,
                                        "confidence": inc.confidence,
                                        "zone_name": inc.zone_name,
                                        "camera_id": camera_id,
                                        "track_ids": inc.track_ids or [],
                                    },
                                    {"id": ev.id, "evidence_type": ev.evidence_type, "sha256": ev.sha256}
                                )

                                broadcast_job_event_sync(job_id, {
                                    "event": "incident_created",
                                    "job_id": job_id,
                                    "incident": {
                                        "id": inc.id,
                                        "incident_code": inc_code,
                                        "title": inc.title,
                                        "severity": inc.severity,
                                        "threat_score": inc.threat_score,
                                        "timestamp_ms": round(timestamp_ms, 2),
                                        "label": "vehicle",
                                        "track_id": f"PLATE-{p_clean}",
                                        "confidence": float(pr.confidence),
                                        "evidence_file": ev_fn,
                                        "sha256": ev_hash,
                                    }
                                })

                # Face intelligence: detect faces and match against enrolled watchlist
                if enable_face:
                    detected_faces = face_service.detect_faces(frame, detections=detections)
                    for f_info in detected_faces:
                        fb = f_info.get("bbox", {})
                        fx1 = max(0, int(fb.get("x1", 0.0) * frame_w))
                        fy1 = max(0, int(fb.get("y1", 0.0) * frame_h))
                        fx2 = min(frame_w, int(fb.get("x2", 1.0) * frame_w))
                        fy2 = min(frame_h, int(fb.get("y2", 1.0) * frame_h))
                        fcrop = frame[fy1:fy2, fx1:fx2]
                        if fcrop.size == 0:
                            continue
                        f_emb = face_service.extract_embedding(fcrop, raw_face=f_info.get("raw_face"), full_frame=frame)
                        if f_emb is not None:
                            match_res = face_service.match_watchlist(f_emb, db)
                            if match_res:
                                subject, similarity = match_res
                                last_m_ts = watchlist_match_cooldown.get(subject.id)
                                now_ts = frame_dt.timestamp()
                                if last_m_ts is None or (now_ts - last_m_ts >= 10.0):
                                    watchlist_match_cooldown[subject.id] = now_ts
                                    incidents_recorded += 1
                                    match_code = f"INC-JOB{job_id}-FRS-S{subject.id}-{uuid.uuid4().hex[:4].upper()}"
                                    ev_fn = f"ev_job_{job_id}_frs_{subject.id}_{frame_index}.jpg"
                                    ev_fp = evidence_dir / ev_fn
                                    ann_face_frame = frame.copy()
                                    cv2.rectangle(ann_face_frame, (fx1, fy1), (fx2, fy2), (0, 0, 255), 2)
                                    cv2.putText(
                                        ann_face_frame,
                                        f"MATCH: {subject.name.upper()} ({similarity:.0%})",
                                        (fx1, max(20, fy1 - 8)),
                                        cv2.FONT_HERSHEY_SIMPLEX,
                                        0.6,
                                        (0, 0, 255),
                                        2,
                                    )
                                    cv2.imwrite(str(ev_fp), ann_face_frame)
                                    with open(ev_fp, "rb") as ef:
                                        ev_hash = hashlib.sha256(ef.read()).hexdigest()

                                    threat_score = round(min(100.0, 85.0 + similarity * 15.0), 1)
                                    inc = Incident(
                                        incident_code=match_code,
                                        title=f"Watchlist Match Alert: {subject.name} ({similarity:.1%} Match)",
                                        description=f"Facial recognition match confirmed for enrolled subject '{subject.name}' with similarity {similarity:.4f} at {timestamp_ms/1000.0:.2f}s.",
                                        severity="CRITICAL",
                                        threat_score=threat_score,
                                        confidence=similarity,
                                        status="OPEN",
                                        camera_id=camera_id,
                                        camera_name=f"Upload Media #{media_id}" if media_id else "Camera Feed",
                                        job_id=job_id,
                                        media_id=media_id,
                                        reason_codes=["WATCHLIST_MATCH", f"SUBJECT_{subject.id}"],
                                        ai_assessment={
                                            "model": "OpenCV SFace",
                                            "subject_id": subject.id,
                                            "subject_name": subject.name,
                                            "similarity": similarity,
                                            "frame": frame_index,
                                            "timestamp_ms": round(timestamp_ms, 2),
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
                                        manifest_data={"job_id": job_id, "subject_id": subject.id, "similarity": similarity, "statute": "BSA_2023_SEC_63"},
                                        file_size_bytes=os.path.getsize(ev_fp),
                                        threat_score=threat_score,
                                        camera_id=camera_id,
                                        camera_name=inc.camera_name,
                                        detection_metadata={"subject_name": subject.name, "similarity": similarity},
                                    )
                                    db.add(ev)

                                    alert = Alert(
                                        incident_id=inc.id,
                                        priority="CRITICAL",
                                        status="NEW",
                                        message=f"CRITICAL: Watchlist suspect '{subject.name}' detected in uploaded media (Similarity: {similarity:.1%}, Score: {threat_score:.0f}/100)",
                                    )
                                    db.add(alert)
                                    db.flush()

                                    try:
                                        live_manager._on_event({
                                            "type": "incident_created",
                                            "data": {
                                                "id": inc.id,
                                                "incident_code": inc.incident_code,
                                                "incident_type": inc.title,
                                                "severity": inc.severity,
                                                "threat_score": inc.threat_score,
                                                "confidence": inc.confidence,
                                                "bop": "Sector",
                                                "camera_name": inc.camera_name,
                                                "summary": inc.description,
                                            }
                                        })
                                    except Exception:
                                        pass

                                    dispatch_incident_webhook(
                                        {
                                            "incident_code": inc.incident_code,
                                            "title": inc.title,
                                            "severity": inc.severity,
                                            "threat_score": inc.threat_score,
                                            "confidence": inc.confidence,
                                            "zone_name": inc.zone_name,
                                            "camera_id": camera_id,
                                            "track_ids": inc.track_ids or [],
                                        },
                                        {"id": ev.id, "evidence_type": ev.evidence_type, "sha256": ev.sha256}
                                    )

                # Multi-object tracking (assigns persistent track_id to each detection)
                active_tracks = tracker.update(detections, frame_time=frame_dt)

                for det in detections:
                    detections_recorded += 1
                    x1, y1, x2, y2 = [int(v) for v in det.bbox]
                    tid = det.track_id or f"T-{frame_index}"

                    # Extract plate metadata if attached by ANPR
                    det_meta = getattr(det, "metadata", {}) or {}
                    det_payload = {
                        "timestamp_ms": round(timestamp_ms, 2),
                        "night": is_night,
                        "luma": round(mean_luma, 1),
                    }
                    if "plate_text" in det_meta:
                        det_payload["plate_text"] = det_meta["plate_text"]
                        det_payload["plate_conf"] = det_meta.get("plate_conf", 0.0)
                        if "plate_bbox" in det_meta:
                            det_payload["plate_bbox"] = det_meta["plate_bbox"]

                    # Save detection in DB with track_id and night/plate metadata
                    db_det = Detection(
                        camera_id=camera_id,
                        job_id=job_id,
                        media_id=media_id,
                        track_id=tid,
                        label=det.class_name,
                        confidence=float(det.confidence),
                        bbox_x1=x1,
                        bbox_y1=y1,
                        bbox_x2=x2,
                        bbox_y2=y2,
                        frame_index=frame_index,
                        source=detector.name,
                        payload=det_payload,
                    )
                    db.add(db_det)

                    # Virtual fence zone evaluation
                    zone_events = fence.check_position(
                        track_id=tid,
                        position=tuple(det.center),
                        frame_time=frame_dt,
                        frame_width=frame_w,
                        frame_height=frame_h,
                        is_night=is_night,
                        confidence=float(det.confidence),
                    )

                    valid_zone_events = [ze for ze in zone_events if ze.get("event_type") in ("zone_intrusion", "direction_violation")]
                    triggered_zone_incident = False
                    if valid_zone_events:
                        triggered_zone_incident = True
                        incidents_recorded += 1
                        # Select primary (highest severity) zone event to consolidate overlapping zones in this frame
                        zevt = max(valid_zone_events, key=lambda z: float(z.get("severity", 0.5)))
                        zid = zevt["zone_id"]
                        zname = zevt["zone_name"]
                        zsev = float(zevt.get("severity", 0.5))
                        threat_score = min(100.0, zsev * 100.0 + (10.0 if is_night else 0.0))
                        incident_code = f"INC-JOB{job_id}-Z{zid}-T{tid}-{uuid.uuid4().hex[:6].upper()}"

                        all_zids = {z["zone_id"] for z in valid_zone_events if z.get("zone_id") is not None}
                        all_znames = [z.get("zone_name", "") for z in valid_zone_events if z.get("zone_name")]

                        # Build annotated evidence snapshot with virtual fence overlay
                        annotated_frame = frame.copy()
                        fence.draw_zones_on_frame(
                            annotated_frame,
                            triggered_zone_ids=all_zids,
                            frame_width=frame_w,
                            frame_height=frame_h,
                        )
                        cv2.rectangle(annotated_frame, (x1, y1), (x2, y2), (0, 0, 255), 2)
                        cv2.putText(
                                annotated_frame,
                                f"{det.class_name.upper()} #{tid} [{zevt['event_type'].upper()}]",
                                (x1, max(20, y1 - 8)),
                                cv2.FONT_HERSHEY_SIMPLEX,
                                0.6,
                                (0, 0, 255),
                                2,
                            )

                        if settings.face_blur:
                            faces_to_blur = face_service.detect_faces(annotated_frame)
                            if faces_to_blur:
                                annotated_frame = face_service.apply_face_blur(annotated_frame, faces_to_blur)

                        if len(annotated_clip_frames) < 60:
                            annotated_clip_frames.append(annotated_frame.copy())

                        ev_filename = f"ev_job_{job_id}_frame_{frame_index}_{uuid.uuid4().hex[:4]}.jpg"
                        ev_path = evidence_dir / ev_filename
                        cv2.imwrite(str(ev_path), annotated_frame)

                        with open(ev_path, "rb") as ef:
                            ev_sha256 = hashlib.sha256(ef.read()).hexdigest()

                        inc = Incident(
                            incident_code=incident_code,
                            title=f"Virtual Fence Alert: {zevt['event_type'].replace('_', ' ').title()} ({zname})",
                            description=f"Track #{tid} ({det.class_name}) triggered {zevt['event_type']} in {zname} at {timestamp_ms/1000.0:.2f}s.",
                            severity="CRITICAL" if threat_score >= 85 else ("HIGH" if threat_score >= 70 else "MEDIUM"),
                            threat_score=threat_score,
                            confidence=float(det.confidence),
                            status="OPEN",
                            camera_id=camera_id,
                            camera_name=f"Upload Media #{media_id}" if media_id else "Camera Feed",
                            zone_name=zname,
                            track_ids=[tid],
                            job_id=job_id,
                            media_id=media_id,
                            reason_codes=[f"ZONE_{zevt['event_type'].upper()}", f"TRACK_{tid}"],
                            ai_assessment={
                                "model": detector.name,
                                "frame": frame_index,
                                "timestamp_ms": round(timestamp_ms, 2),
                                "bbox": [x1, y1, x2, y2],
                                "track_id": tid,
                                "zone_id": zid,
                                "zone_name": zname,
                                "all_triggered_zones": all_znames,
                                "direction": zevt.get("direction", "none"),
                                "night": is_night,
                                "legal_citation": "Bharatiya Sakshya Adhiniyam, 2023 — Section 63",
                            },
                        )
                        db.add(inc)
                        db.flush()
                        if primary_incident_id is None:
                            primary_incident_id = inc.id

                        ev = Evidence(
                            incident_id=inc.id,
                            evidence_type="snapshot",
                            file_path=str(ev_path),
                            sha256=ev_sha256,
                            manifest_path=str(ev_path) + ".json",
                            manifest_data={"job_id": job_id, "frame": frame_index, "sha256": ev_sha256, "statute": "BSA_2023_SEC_63"},
                            file_size_bytes=os.path.getsize(ev_path),
                            threat_score=threat_score,
                            camera_id=camera_id,
                            camera_name=inc.camera_name,
                            detection_metadata={"label": det.class_name, "track_id": tid, "confidence": float(det.confidence), "bbox": [x1, y1, x2, y2]},
                        )
                        db.add(ev)

                        alert = Alert(
                            incident_id=inc.id,
                            priority=inc.severity,
                            status="NEW",
                            message=f"{inc.severity} incident: {inc.title} (Score: {threat_score:.0f}/100)",
                        )
                        db.add(alert)
                        db.flush()

                        try:
                            live_manager._on_event({
                                "type": "incident_created",
                                "data": {
                                    "id": inc.id,
                                    "incident_code": inc.incident_code,
                                    "incident_type": inc.title,
                                    "severity": inc.severity,
                                    "threat_score": inc.threat_score,
                                    "confidence": inc.confidence,
                                    "bop": zname or "Perimeter",
                                    "camera_name": inc.camera_name,
                                    "summary": inc.description,
                                }
                            })
                        except Exception:
                            pass

                        dispatch_incident_webhook(
                            {
                                "incident_code": inc.incident_code,
                                "title": inc.title,
                                "severity": inc.severity,
                                "threat_score": inc.threat_score,
                                "confidence": inc.confidence,
                                "zone_name": inc.zone_name,
                                "camera_id": camera_id,
                                "track_ids": inc.track_ids or [],
                            },
                            {"id": ev.id, "evidence_type": ev.evidence_type, "sha256": ev.sha256}
                        )

                        broadcast_job_event_sync(job_id, {
                            "event": "incident_created",
                            "job_id": job_id,
                            "incident": {
                                "id": inc.id,
                                "incident_code": incident_code,
                                "title": inc.title,
                                "severity": inc.severity,
                                "threat_score": inc.threat_score,
                                "timestamp_ms": round(timestamp_ms, 2),
                                "label": det.class_name,
                                "track_id": tid,
                                "zone_name": zname,
                                "confidence": float(det.confidence),
                                "bbox": [x1, y1, x2, y2],
                                "evidence_file": ev_filename,
                                "sha256": ev_sha256,
                            }
                        })

                    # If no zone incident triggered, check baseline threat
                    is_threat = (
                        det.class_name in ("person", "car", "truck", "bus", "motorcycle")
                        or (det.class_name == "motion" and det.confidence >= float(job.confidence_threshold or 0.20))
                    )
                    now_ts = frame_dt.timestamp()
                    last_baseline = baseline_threat_cooldown.get(tid)
                    if not triggered_zone_incident and is_threat and (last_baseline is None or (now_ts - last_baseline >= 8.0)):
                        baseline_threat_cooldown[tid] = now_ts
                        incidents_recorded += 1
                        threat_score = 85.0 if det.class_name == "person" else 75.0
                        incident_code = f"INC-JOB{job_id}-F{frame_index}-T{tid}-{uuid.uuid4().hex[:6].upper()}"

                        annotated_frame = frame.copy()
                        fence.draw_zones_on_frame(annotated_frame, frame_width=frame_w, frame_height=frame_h)
                        cv2.rectangle(annotated_frame, (x1, y1), (x2, y2), (0, 40, 255), 2)
                        cv2.putText(
                            annotated_frame,
                            f"{det.class_name.upper()} #{tid} {det.confidence:.0%}",
                            (x1, max(20, y1 - 8)),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.6,
                            (0, 40, 255),
                            2,
                        )

                        if settings.face_blur:
                            faces_to_blur = face_service.detect_faces(annotated_frame)
                            if faces_to_blur:
                                annotated_frame = face_service.apply_face_blur(annotated_frame, faces_to_blur)

                        if len(annotated_clip_frames) < 60:
                            annotated_clip_frames.append(annotated_frame.copy())

                        ev_filename = f"ev_job_{job_id}_frame_{frame_index}_{uuid.uuid4().hex[:4]}.jpg"
                        ev_path = evidence_dir / ev_filename
                        cv2.imwrite(str(ev_path), annotated_frame)

                        with open(ev_path, "rb") as ef:
                            ev_sha256 = hashlib.sha256(ef.read()).hexdigest()

                        inc = Incident(
                            incident_code=incident_code,
                            title=f"AI Perimeter Alert: {det.class_name.capitalize()} Detected in Sector",
                            description=f"Automated CV assessment detected {det.class_name} (Track #{tid}) with {det.confidence:.0%} confidence at {timestamp_ms / 1000.0:.2f}s.",
                            severity="HIGH" if threat_score >= 80 else "MEDIUM",
                            threat_score=threat_score,
                            confidence=float(det.confidence),
                            status="OPEN",
                            camera_id=camera_id,
                            camera_name=f"Upload Media #{media_id}" if media_id else "Camera Feed",
                            track_ids=[tid],
                            job_id=job_id,
                            media_id=media_id,
                            reason_codes=[f"DETECTED_{det.class_name.upper()}", f"TRACK_{tid}"],
                            ai_assessment={
                                "model": detector.name,
                                "frame": frame_index,
                                "timestamp_ms": round(timestamp_ms, 2),
                                "bbox": [x1, y1, x2, y2],
                                "track_id": tid,
                                "night": is_night,
                                "legal_citation": "Bharatiya Sakshya Adhiniyam, 2023 — Section 63",
                            },
                        )
                        db.add(inc)
                        db.flush()
                        if primary_incident_id is None:
                            primary_incident_id = inc.id

                        ev = Evidence(
                            incident_id=inc.id,
                            evidence_type="snapshot",
                            file_path=str(ev_path),
                            sha256=ev_sha256,
                            manifest_path=str(ev_path) + ".json",
                            manifest_data={"job_id": job_id, "frame": frame_index, "sha256": ev_sha256, "statute": "BSA_2023_SEC_63"},
                            file_size_bytes=os.path.getsize(ev_path),
                            threat_score=threat_score,
                            camera_id=camera_id,
                            camera_name=inc.camera_name,
                            detection_metadata={"label": det.class_name, "track_id": tid, "confidence": float(det.confidence), "bbox": [x1, y1, x2, y2]},
                        )
                        db.add(ev)

                        alert = Alert(
                            incident_id=inc.id,
                            priority=inc.severity,
                            status="NEW",
                            message=f"{inc.severity} incident: {inc.title} (Score: {threat_score:.0f}/100)",
                        )
                        db.add(alert)
                        db.flush()

                        try:
                            live_manager._on_event({
                                "type": "incident_created",
                                "data": {
                                    "id": inc.id,
                                    "incident_code": inc.incident_code,
                                    "incident_type": inc.title,
                                    "severity": inc.severity,
                                    "threat_score": inc.threat_score,
                                    "confidence": inc.confidence,
                                    "bop": "Perimeter",
                                    "camera_name": inc.camera_name,
                                    "summary": inc.description,
                                }
                            })
                        except Exception:
                            pass

                        dispatch_incident_webhook(
                            {
                                "incident_code": inc.incident_code,
                                "title": inc.title,
                                "severity": inc.severity,
                                "threat_score": inc.threat_score,
                                "confidence": inc.confidence,
                                "zone_name": inc.zone_name,
                                "camera_id": camera_id,
                                "track_ids": inc.track_ids or [],
                            },
                            {"id": ev.id, "evidence_type": ev.evidence_type, "sha256": ev.sha256}
                        )

                        broadcast_job_event_sync(job_id, {
                            "event": "incident_created",
                            "job_id": job_id,
                            "incident": {
                                "id": inc.id,
                                "incident_code": incident_code,
                                "title": inc.title,
                                "severity": inc.severity,
                                "threat_score": inc.threat_score,
                                "timestamp_ms": round(timestamp_ms, 2),
                                "label": det.class_name,
                                "track_id": tid,
                                "confidence": float(det.confidence),
                                "bbox": [x1, y1, x2, y2],
                                "evidence_file": ev_filename,
                                "sha256": ev_sha256,
                            }
                        })

                    # Broadcast detection
                    broadcast_job_event_sync(job_id, {
                        "event": "detection_created",
                        "job_id": job_id,
                        "detection": {
                            "frame_index": frame_index,
                            "timestamp_ms": round(timestamp_ms, 2),
                            "label": det.class_name,
                            "track_id": tid,
                            "confidence": round(float(det.confidence), 2),
                            "bbox": [x1, y1, x2, y2],
                            "night": is_night,
                        }
                    })

                # Evaluate behavioral rules (loitering, crowd, rapid movement)
                behavior_events = fence.check_behaviors(
                    tracks=active_tracks,
                    frame_time=frame_dt,
                    frame_width=frame_w,
                    frame_height=frame_h,
                )
                for bevt in behavior_events:
                    incidents_recorded += 1
                    b_type = bevt["event_type"]
                    b_sev = bevt.get("severity", 0.75)
                    threat_score = round(b_sev * 100.0, 1)
                    tid_ref = bevt.get("track_id") or "GROUP"
                    incident_code = f"INC-JOB{job_id}-{b_type.upper()[:6]}-T{tid_ref}-{uuid.uuid4().hex[:6].upper()}"

                    annotated_frame = frame.copy()
                    fence.draw_zones_on_frame(annotated_frame, frame_width=frame_w, frame_height=frame_h)
                    pos = bevt.get("position", (frame_w // 2, frame_h // 2))
                    cv2.putText(
                        annotated_frame,
                        f"BEHAVIOR ALERT: {b_type.upper()} ({bevt.get('zone_name', '')})",
                        (max(10, int(pos[0]) - 50), max(25, int(pos[1]))),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.6,
                        (0, 140, 255),
                        2,
                    )

                    ev_filename = f"ev_job_{job_id}_behavior_{b_type}_{frame_index}_{uuid.uuid4().hex[:4]}.jpg"
                    ev_path = evidence_dir / ev_filename
                    cv2.imwrite(str(ev_path), annotated_frame)

                    with open(ev_path, "rb") as ef:
                        ev_sha256 = hashlib.sha256(ef.read()).hexdigest()

                    t_ids = [bevt["track_id"]] if "track_id" in bevt else bevt.get("track_ids", [])
                    inc = Incident(
                        incident_code=incident_code,
                        title=f"Tactical Behavior Alert: {b_type.replace('_', ' ').title()}",
                        description=f"Behavior rule '{b_type}' triggered at {timestamp_ms / 1000.0:.2f}s in {bevt.get('zone_name', 'Sector')}.",
                        severity="CRITICAL" if threat_score >= 80 else "HIGH",
                        threat_score=threat_score,
                        confidence=0.90,
                        status="OPEN",
                        camera_id=camera_id,
                        camera_name=f"Upload Media #{media_id}" if media_id else "Camera Feed",
                        zone_name=bevt.get("zone_name", ""),
                        track_ids=t_ids,
                        job_id=job_id,
                        media_id=media_id,
                        reason_codes=[f"BEHAVIOR_{b_type.upper()}"],
                        ai_assessment={
                            "rule": b_type,
                            "frame": frame_index,
                            "timestamp_ms": round(timestamp_ms, 2),
                            "details": bevt,
                            "legal_citation": "Bharatiya Sakshya Adhiniyam, 2023 — Section 63",
                        },
                    )
                    db.add(inc)
                    db.flush()

                    ev = Evidence(
                        incident_id=inc.id,
                        evidence_type="snapshot",
                        file_path=str(ev_path),
                        sha256=ev_sha256,
                        manifest_path=str(ev_path) + ".json",
                        manifest_data={"job_id": job_id, "frame": frame_index, "sha256": ev_sha256, "statute": "BSA_2023_SEC_63"},
                        file_size_bytes=os.path.getsize(ev_path),
                        threat_score=threat_score,
                        camera_id=camera_id,
                        camera_name=inc.camera_name,
                        detection_metadata={"behavior": b_type, "details": bevt},
                    )
                    db.add(ev)

                    alert = Alert(
                        incident_id=inc.id,
                        priority=inc.severity,
                        status="NEW",
                        message=f"{inc.severity} incident: {inc.title} (Score: {threat_score:.0f}/100)",
                    )
                    db.add(alert)
                    db.flush()

                    try:
                        live_manager._on_event({
                            "type": "incident_created",
                            "data": {
                                "id": inc.id,
                                "incident_code": inc.incident_code,
                                "incident_type": inc.title,
                                "severity": inc.severity,
                                "threat_score": inc.threat_score,
                                "confidence": inc.confidence,
                                "bop": inc.zone_name or "Sector",
                                "camera_name": inc.camera_name,
                                "summary": inc.description,
                            }
                        })
                    except Exception:
                        pass

                    dispatch_incident_webhook(
                        {
                            "incident_code": inc.incident_code,
                            "title": inc.title,
                            "severity": inc.severity,
                            "threat_score": inc.threat_score,
                            "confidence": inc.confidence,
                            "zone_name": inc.zone_name,
                            "camera_id": camera_id,
                            "track_ids": inc.track_ids or [],
                        },
                        {"id": ev.id, "evidence_type": ev.evidence_type, "sha256": ev.sha256}
                    )

                    broadcast_job_event_sync(job_id, {
                        "event": "incident_created",
                        "job_id": job_id,
                        "incident": {
                            "id": inc.id,
                            "incident_code": incident_code,
                            "title": inc.title,
                            "severity": inc.severity,
                            "threat_score": inc.threat_score,
                            "timestamp_ms": round(timestamp_ms, 2),
                            "zone_name": bevt.get("zone_name", ""),
                            "evidence_file": ev_filename,
                            "sha256": ev_sha256,
                        }
                    })

                # Commit batch periodically
                if frame_index % 20 == 0:
                    db.commit()

                # Calculate progress and FPS
                now = time.time()
                elapsed = now - start_proc_time
                curr_fps = round(processed_frames / elapsed, 1) if elapsed > 0 else 0.0
                progress_pct = round(min(99.0, (frame_index / max(1, total_frames)) * 100.0), 1)

                if now - last_progress_broadcast > 0.4:
                    last_progress_broadcast = now
                    job.progress_percent = progress_pct
                    job.processed_frames = processed_frames
                    job.detections_count = detections_recorded
                    job.incidents_count = incidents_recorded
                    job.fps = curr_fps
                    db.commit()

                    broadcast_job_event_sync(job_id, {
                        "event": "job_progress",
                        "job_id": job_id,
                        "status": "processing",
                        "progress_percent": progress_pct,
                        "processed_frames": processed_frames,
                        "total_frames": total_frames,
                        "detections_count": detections_recorded,
                        "incidents_count": incidents_recorded,
                        "fps": curr_fps,
                    })

            cap.release()

            # Generate signed & transcoded evidence video clip (Task 4.1)
            if annotated_clip_frames and primary_incident_id:
                try:
                    raw_clip_filename = f"ev_job_{job_id}_clip.mp4"
                    raw_clip_path = evidence_dir / raw_clip_filename
                    h_c, w_c = annotated_clip_frames[0].shape[:2]
                    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                    out_writer = cv2.VideoWriter(str(raw_clip_path), fourcc, min(25.0, fps or 25.0), (w_c, h_c))
                    for f in annotated_clip_frames:
                        out_writer.write(f)
                    out_writer.release()

                    final_clip_path, final_clip_hash, is_playable = transcode_and_seal_clip(str(raw_clip_path))
                    clip_size = os.path.getsize(final_clip_path) if os.path.exists(final_clip_path) else 0

                    ev_clip = Evidence(
                        incident_id=primary_incident_id,
                        evidence_type="clip",
                        file_path=final_clip_path,
                        sha256=final_clip_hash,
                        manifest_path=str(final_clip_path) + ".json",
                        manifest_data={
                            "job_id": job_id,
                            "incident_id": primary_incident_id,
                            "playable": is_playable,
                            "frames_count": len(annotated_clip_frames),
                            "sha256": final_clip_hash,
                            "statute": "BSA_2023_SEC_63",
                        },
                        file_size_bytes=clip_size,
                        threat_score=85.0,
                        camera_id=camera_id,
                        camera_name=f"Upload Media #{media_id}" if media_id else "Camera Feed",
                        detection_metadata={"playable": is_playable, "frames": len(annotated_clip_frames)},
                    )
                    db.add(ev_clip)
                    db.commit()
                    logger.info(f"Generated evidence clip for Job #{job_id}: {final_clip_path} (playable={is_playable})")
                except Exception as clip_err:
                    logger.warning(f"Could not generate evidence clip: {clip_err}")

            # Finalize job status
            if cancel_event.is_set():
                job.status = "cancelled"
                job.error_message = "Analysis cancelled by operator."
            else:
                job.status = "completed"
                job.progress_percent = 100.0
                job.processed_frames = processed_frames
                job.detections_count = detections_recorded
                job.incidents_count = incidents_recorded
                job.finished_at = datetime.utcnow()
                job.summary = {
                    "total_frames": total_frames,
                    "processed_frames": processed_frames,
                    "detections_count": detections_recorded,
                    "incidents_count": incidents_recorded,
                    "detector": detector.name,
                    "duration_seconds": round(time.time() - start_proc_time, 2),
                    "night_frames": night_frames_count,
                    "is_night": bool(night_frames_count > (processed_frames * 0.5)),
                    "track_summaries": tracker.get_track_summaries(),
                }

            db.commit()

            broadcast_job_event_sync(job_id, {

                "event": "job_completed" if job.status == "completed" else "job_cancelled",
                "job_id": job_id,
                "status": job.status,
                "summary": job.summary or {},
            })

        except Exception as exc:
            logger.error(f"Job {job_id} processing failed: {exc}", exc_info=True)
            try:
                db.rollback()
                job = db.get(AnalysisJob, job_id)
                if job:
                    job.status = "failed"
                    job.error_message = str(exc)
                    job.finished_at = datetime.utcnow()
                    db.commit()
            except Exception as rollback_err:
                logger.error(f"Error recording job failure: {rollback_err}")
            broadcast_job_event_sync(job_id, {
                "event": "job_failed",
                "job_id": job_id,
                "status": "failed",
                "error": str(exc),
            })
        finally:
            _active_tasks.pop(job_id, None)
            _active_threads.pop(job_id, None)
            db.close()
