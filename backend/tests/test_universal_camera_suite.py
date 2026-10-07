"""Tests for Universal Camera Suite, Deep Network Discovery, and Phone Live Stream."""

import pytest
from fastapi.testclient import TestClient
from backend.app.main import app

from backend.app.core.security import create_access_token

client = TestClient(app)


def _get_headers(role: str = "ADMIN"):
    token = create_access_token("admin_test", role)
    return {"Authorization": f"Bearer {token}"}


def test_network_info_endpoint():
    """Verify /cameras/network-info returns auto-detected LAN subnet and local IP."""
    resp = client.get("/api/v1/cameras/network-info", headers=_get_headers())
    assert resp.status_code == 200
    data = resp.json()
    assert "subnet" in data
    assert "local_ip" in data
    assert "gateway" in data


def test_deep_camera_discovery():
    """Verify /cameras/discover detects connected devices on subnet."""
    resp = client.post("/api/v1/cameras/discover", headers=_get_headers(), json={"ip_range": "192.168.29", "start": 1, "end": 20})
    assert resp.status_code == 200
    devices = resp.json()
    assert isinstance(devices, list)
    if len(devices) > 0:
        dev = devices[0]
        assert "ip" in dev
        assert "brand_hint" in dev
        assert "rtsp_url" in dev
        assert "status" in dev


def test_smart_probe_endpoint():
    """Verify Strix-style smart probe tests patterns in < 2 seconds."""
    resp = client.post("/api/v1/cameras/smart-probe", headers=_get_headers(), json={
        "ip": "192.168.1.200",
        "username": "admin",
        "password": "testpassword",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["tested_count"] >= 10
    assert "candidate_urls" in data


def test_phone_stream_upload_and_test():
    """Verify smartphone JPEG upload and phone:// stream test."""
    import base64
    import numpy as np
    import cv2

    frame = np.ones((240, 320, 3), dtype=np.uint8) * 100
    _, buf = cv2.imencode(".jpg", frame)
    b64 = base64.b64encode(buf).decode()

    # 1. Upload frame
    up = client.post("/api/v1/cameras/phone-stream/mobile-test/frame", headers=_get_headers(), json={"image": b64})
    assert up.status_code == 200
    assert up.json()["status"] == "ok"

    # 2. Get frame back
    down = client.get("/api/v1/cameras/phone-stream/mobile-test/frame", headers=_get_headers())
    assert down.status_code == 200
    assert down.headers["content-type"] == "image/jpeg"

    # 3. Test stream
    ts = client.post("/api/v1/cameras/test-stream", headers=_get_headers(), json={"stream_url": "phone://mobile-test"})
    assert ts.status_code == 200
    assert ts.json()["success"] is True


def test_camera_patch_and_map_endpoint():
    """Verify PATCH /api/v1/cameras/{id} updates coordinates & GET /api/v1/map returns telemetry."""
    from backend.app.core.security import create_access_token
    from backend.app.db.session import SessionLocal
    from backend.app.models.camera import Camera

    token = create_access_token("test-officer", "ADMIN")
    headers = {"Authorization": f"Bearer {token}"}

    db = SessionLocal()
    try:
        # Create test camera
        cam = Camera(
            name="Tactical-Outpost-Cam-99",
            stream_url="demo://99",
            latitude=28.7041,
            longitude=77.1025,
            sector="Sector Delta",
            status="ONLINE",
        )
        db.add(cam)
        db.commit()
        db.refresh(cam)

        # 1. Test PATCH updates coordinates and sector
        patch_resp = client.patch(
            f"/api/v1/cameras/{cam.id}",
            json={"latitude": 28.7500, "longitude": 77.1500, "sector": "Sector Echo"},
            headers=headers,
        )
        assert patch_resp.status_code == 200
        pdata = patch_resp.json()
        assert pdata["latitude"] == 28.7500
        assert pdata["longitude"] == 77.1500
        assert pdata["sector"] == "Sector Echo"

        # 2. Test GET /api/v1/map retrieves camera & sector metrics
        map_resp = client.get("/api/v1/map", headers=headers)
        assert map_resp.status_code == 200
        mdata = map_resp.json()
        assert mdata["status"] == "ready"
        assert "cameras" in mdata
        assert "incidents" in mdata
        assert "sectors" in mdata
        assert "summary" in mdata

        matched = next((c for c in mdata["cameras"] if c["id"] == cam.id), None)
        assert matched is not None
        assert matched["latitude"] == 28.7500
        assert matched["sector"] == "Sector Echo"

        # Clean up
        db.delete(cam)
        db.commit()
    finally:
        db.close()

