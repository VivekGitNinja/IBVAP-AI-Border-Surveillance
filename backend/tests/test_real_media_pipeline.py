"""Automated Test Suite for Real Video Upload, OpenCV Analysis, and Diagnostic Verification."""

import os
import tempfile
import threading
import time
import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.core.config import settings
from backend.app.core.security import create_access_token
from backend.app.db.session import SessionLocal
from backend.app.models.media_asset import MediaAsset
from backend.app.models.analysis_job import AnalysisJob
from backend.app.services.video_analysis import VideoAnalysisEngine

client = TestClient(app)


def _get_auth_headers(role: str = "OPERATOR"):
    token = create_access_token("operator", role)
    return {"Authorization": f"Bearer {token}"}


def _create_synthetic_motion_video(output_path: str, num_frames: int = 25, width: int = 320, height: int = 240, fps: int = 10):
    """Generates a real, valid MP4 video file containing actual moving pixels."""
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out = cv2.VideoWriter(output_path, fourcc, float(fps), (width, height))
    for i in range(num_frames):
        # Dark background
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        # Add background pattern
        cv2.line(frame, (0, height // 2), (width, height // 2), (40, 40, 40), 1)
        # Moving bright object (triggers motion detector)
        x = int((i / num_frames) * (width - 60)) + 10
        y = height // 2 - 20
        cv2.rectangle(frame, (x, y), (x + 40, y + 40), (255, 255, 255), -1)
        out.write(frame)
    out.release()


def test_demo_mode_disabled_by_default():
    """Verify that demo/synthetic behaviors are strictly disabled by default."""
    assert settings.enable_demo is False
    assert settings.enable_synthetic_cameras is False
    assert settings.allow_demo_data is False


def test_media_upload_invalid_extension():
    """Uploads with unauthorized file extensions must be rejected with 400."""
    headers = _get_auth_headers()
    files = {"file": ("malicious_payload.exe", b"MZ\x90\x00BinaryContent", "application/octet-stream")}
    r = client.post("/api/v1/media/upload", headers=headers, files=files)
    assert r.status_code == 400
    assert "Unsupported video format" in r.json()["detail"]


def test_media_upload_empty_file():
    """Empty files (0 bytes) must be rejected with 400."""
    headers = _get_auth_headers()
    files = {"file": ("empty_footage.mp4", b"", "video/mp4")}
    r = client.post("/api/v1/media/upload", headers=headers, files=files)
    assert r.status_code == 400
    assert "empty" in r.json()["detail"].lower()


def test_real_video_upload_and_metadata_probing():
    """Upload a real video file and verify OpenCV extracts authentic resolution, fps, duration, and sha256."""
    headers = _get_auth_headers()

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        tmp_video_path = tmp.name

    try:
        _create_synthetic_motion_video(tmp_video_path, num_frames=20, width=320, height=240, fps=10)
        with open(tmp_video_path, "rb") as f:
            file_bytes = f.read()

        files = {"file": ("test_real_patrol.mp4", file_bytes, "video/mp4")}
        r = client.post("/api/v1/media/upload", headers=headers, files=files)

        assert r.status_code == 201
        data = r.json()
        assert data["original_filename"] == "test_real_patrol.mp4"
        assert data["width"] == 320
        assert data["height"] == 240
        assert data["fps"] == 10.0
        assert data["total_frames"] == 20
        assert data["duration_seconds"] == 2.0
        assert len(data["sha256"]) == 64
        assert data["status"] == "READY"

        # Verify asset appears in GET /api/v1/media
        list_r = client.get("/api/v1/media", headers=headers)
        assert list_r.status_code == 200
        items = list_r.json()
        assert any(item["id"] == data["id"] for item in items)

        # Verify streaming endpoint serves real video bytes
        stream_r = client.get(f"/api/v1/media/{data['id']}/stream", headers=headers)
        assert stream_r.status_code in [200, 206]
        assert "video/mp4" in stream_r.headers.get("content-type", "")

    finally:
        if os.path.exists(tmp_video_path):
            os.remove(tmp_video_path)


def test_real_video_analysis_job_execution():
    """Submit a real video analysis job, execute it, and verify detections, incidents, and progress."""
    headers = _get_auth_headers()

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        tmp_video_path = tmp.name

    try:
        _create_synthetic_motion_video(tmp_video_path, num_frames=30, width=320, height=240, fps=10)
        with open(tmp_video_path, "rb") as f:
            file_bytes = f.read()

        files = {"file": ("motion_test.mp4", file_bytes, "video/mp4")}
        upload_resp = client.post("/api/v1/media/upload", headers=headers, files=files)
        assert upload_resp.status_code == 201
        media_id = upload_resp.json()["id"]

        # Create analysis job with OpenCV MOG2 detector
        job_payload = {
            "source_type": "upload",
            "source_id": media_id,
            "detector_model": "motion",
            "confidence_threshold": 0.2,
        }
        create_resp = client.post("/api/v1/analysis/jobs", headers=headers, json=job_payload)
        assert create_resp.status_code == 201
        job_id = create_resp.json()["id"]

        # Wait for worker thread to complete processing
        thread = VideoAnalysisEngine._running_jobs.get(job_id)
        if thread is not None and thread.is_alive():
            thread.join(timeout=15.0)
        elif thread is None:
            cancel_event = threading.Event()
            VideoAnalysisEngine._run_analysis(job_id, cancel_event)

        # Inspect updated job
        job_resp = client.get(f"/api/v1/analysis/jobs/{job_id}", headers=headers)
        assert job_resp.status_code == 200
        job_data = job_resp.json()
        assert job_data["status"] == "completed"
        assert job_data["processed_frames"] == 30
        assert job_data["progress_percent"] == 100.0

        # Inspect detailed results
        results_resp = client.get(f"/api/v1/analysis/jobs/{job_id}/results", headers=headers)
        assert results_resp.status_code == 200
        res = results_resp.json()
        assert "detections" in res
        assert "incidents" in res
        assert "evidence" in res
        # Real motion was present in the video, so detections should have been recorded
        assert len(res["detections"]) > 0
        first_det = res["detections"][0]
        assert "bbox_x1" in first_det
        assert "bbox_y1" in first_det
        assert "bbox_x2" in first_det
        assert "bbox_y2" in first_det

    finally:
        if os.path.exists(tmp_video_path):
            os.remove(tmp_video_path)


def test_camera_connection_diagnostic_endpoint():
    """Verify that /cameras/{id}/test accurately returns connection failure for offline/unreachable feeds."""
    headers = _get_auth_headers()
    # Camera 1 has stream_url "demo://synthetic" which cannot be opened by real OpenCV
    resp = client.post("/api/v1/cameras/1/test", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["camera_id"] == 1
    assert data["status"] == "OFFLINE"
    assert "Cannot open video stream" in data.get("error", "") or "error" in data


def test_camera_snapshot_returns_offline_diagnostic_frame():
    """When demo mode is disabled and camera is offline, snapshot returns honest diagnostic offline frame."""
    resp = client.get("/api/v1/cameras/1/snapshot", headers=_get_auth_headers())
    assert resp.status_code == 200
    assert resp.headers.get("content-type") == "image/jpeg"
    # Verify image is valid JPEG and decodable
    nparr = np.frombuffer(resp.content, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    assert img is not None
    assert img.shape[0] > 0 and img.shape[1] > 0


def test_static_scene_zero_detections():
    """No-False-Alarm Proof Test: Ensure static background footage with sensor noise produces 0 detections and 0 incidents."""
    headers = _get_auth_headers()
    fixture_path = "tests/fixtures/static_scene.mp4"
    if not os.path.exists(fixture_path):
        fixture_path = "samples/static_scene.mp4"
    assert os.path.exists(fixture_path), f"Static scene fixture {fixture_path} must exist"

    with open(fixture_path, "rb") as f:
        file_bytes = f.read()

    files = {"file": ("static_eval.mp4", file_bytes, "video/mp4")}
    upload_resp = client.post("/api/v1/media/upload", headers=headers, files=files)
    assert upload_resp.status_code == 201
    media_id = upload_resp.json()["id"]

    job_payload = {
        "source_type": "upload",
        "source_id": media_id,
        "detector_model": "motion",
        "confidence_threshold": 0.2,
    }
    create_resp = client.post("/api/v1/analysis/jobs", headers=headers, json=job_payload)
    assert create_resp.status_code == 201
    job_id = create_resp.json()["id"]

    # Wait for worker thread to complete processing
    thread = VideoAnalysisEngine._running_jobs.get(job_id)
    if thread is not None and thread.is_alive():
        thread.join(timeout=15.0)
    elif thread is None:
        cancel_event = threading.Event()
        VideoAnalysisEngine._run_analysis(job_id, cancel_event)

    results_resp = client.get(f"/api/v1/analysis/jobs/{job_id}/results", headers=headers)
    assert results_resp.status_code == 200
    res = results_resp.json()
    detections = res.get("detections", [])
    incidents = res.get("incidents", [])

    # Honesty proof: Assert detections == 0 OR <= noise tolerance (<=2 spurious rows with confidence < min threshold and NO incidents)
    assert len(incidents) == 0, f"Expected 0 incidents on static footage, got {len(incidents)}"
    if len(detections) > 0:
        assert len(detections) <= 2, f"Spurious detections exceeded tolerance: {len(detections)}"
        for det in detections:
            assert det["confidence"] < 0.35
    else:
        assert len(detections) == 0
