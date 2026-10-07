"""
Unit tests for Live Source Support:
1. Camera testing with macOS AVFoundation / device indices and TCP RTSP.
2. GET /api/v1/cameras/{id}/mjpeg StreamingResponse.
3. POST /api/v1/analysis/live-window endpoint validation and execution.
"""

import os
import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.models.camera import Camera
from backend.app.db.session import SessionLocal
from backend.app.core.security import create_access_token

client = TestClient(app)


def auth_headers(role="ADMIN"):
    token = create_access_token("test-live-admin", role)
    return {"Authorization": f"Bearer {token}"}


def test_camera_test_invalid_camera():
    """Assert 404 on testing non-existent camera."""
    response = client.post("/api/v1/cameras/999999/test", headers=auth_headers())
    assert response.status_code == 404


def test_camera_test_no_stream_url():
    """Assert OFFLINE status returned when camera has no stream URL."""
    db = SessionLocal()
    try:
        cam = Camera(
            name="Empty URL Cam",
            location="Zone A",
            stream_url="",
            status="OFFLINE",
        )
        db.add(cam)
        db.commit()

        response = client.post(f"/api/v1/cameras/{cam.id}/test", headers=auth_headers())
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert data["status"] == "OFFLINE"
        assert "No stream URL configured" in data["error"]
    finally:
        db.close()


def test_camera_mjpeg_stream_header():
    """Assert /mjpeg endpoint returns multipart/x-mixed-replace content type."""
    db = SessionLocal()
    try:
        cam = Camera(
            name="MJPEG Test Cam",
            location="Gate 1",
            stream_url="demo://tactical",
            status="ONLINE",
        )
        db.add(cam)
        db.commit()

        with client.stream("GET", f"/api/v1/cameras/{cam.id}/mjpeg?max_frames=2", headers=auth_headers()) as response:
            assert response.status_code == 200
            assert "multipart/x-mixed-replace" in response.headers.get("content-type", "")
            for chunk in response.iter_raw():
                assert b"--frame" in chunk or len(chunk) > 0
                break
    finally:
        db.close()


def test_analysis_live_window_invalid_camera():
    """Assert 404 when requesting live window on non-existent camera."""
    response = client.post(
        "/api/v1/analysis/live-window",
        json={"camera_id": 999999, "seconds": 2},
        headers=auth_headers(),
    )
    assert response.status_code == 404


def test_analysis_live_window_execution():
    """Assert live-window endpoint runs and returns expected metrics schema."""
    sample_file = "samples/day_crossing.mp4"
    if not os.path.exists(sample_file):
        pytest.skip("Sample video file not present")

    db = SessionLocal()
    try:
        cam = Camera(
            name="Live Window Test Cam",
            location="Perimeter",
            stream_url=f"file://{os.path.abspath(sample_file)}",
            status="ONLINE",
        )
        db.add(cam)
        db.commit()

        response = client.post(
            "/api/v1/analysis/live-window",
            json={
                "camera_id": cam.id,
                "seconds": 2,
                "detector_model": "yolo26n",
                "enable_face": True,
                "enable_zones": False,
            },
            headers=auth_headers(),
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["camera_id"] == cam.id
        assert data["source"] == "live"
        assert data["frames_processed"] > 0
        assert "fps" in data
        assert "start_timestamp" in data
        assert "end_timestamp" in data
        assert "detections_count" in data
    finally:
        db.close()
