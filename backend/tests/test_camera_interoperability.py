"""
Unit and Integration Tests for Camera Interoperability & Camera Adapters
========================================================================

Verifies:
1. Universal CameraAdapter hierarchy: RTSP, ONVIF (Profile S/T/M), File, USB, Synthetic.
2. Honest capability negotiation (is_hardware_ptz, digital fallback).
3. WS-Security UsernameToken PasswordDigest generation.
4. ONVIF Probe XML parsing.
5. API PTZ execution through the unified adapter layer.
"""

import os
import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.core.security import create_access_token
from backend.app.db.session import SessionLocal
from backend.app.models.camera import Camera

from edge.adapters import (
    create_camera_adapter,
    CameraCapabilities,
    CameraProtocol,
    PTZCommand,
    RTSPCameraAdapter,
    ONVIFCameraAdapter,
    FileCameraAdapter,
    USBCameraAdapter,
    SyntheticTestPatternAdapter,
    discover_onvif_devices,
    sanitize_rtsp_url,
)
from edge.adapters.onvif import build_ws_security_header
from edge.adapters.discovery import parse_probe_match

client = TestClient(app)
_token = create_access_token("operator-adapter-test", role="ADMIN")
AUTH_HEADERS = {"Authorization": f"Bearer {_token}"}


class TestCameraAdaptersUnit:
    """Unit tests for adapter classes and protocol contracts."""

    def test_sanitize_rtsp_url(self):
        """Verify passwords in RTSP URLs are stripped from logs."""
        raw = "rtsp://admin:SecretPass123!@192.168.1.100:554/h264Preview_01_main"
        clean = sanitize_rtsp_url(raw)
        assert "SecretPass123!" not in clean
        assert "rtsp://admin:***@192.168.1.100:554/h264Preview_01_main" == clean

    def test_factory_instantiation(self):
        """Verify factory returns appropriate adapter type based on stream URL."""
        a_rtsp = create_camera_adapter(1, "rtsp://192.168.1.10/live")
        assert isinstance(a_rtsp, RTSPCameraAdapter)
        assert a_rtsp.get_capabilities().protocol == CameraProtocol.RTSP

        a_onvif = create_camera_adapter(2, "onvif://192.168.1.20/live")
        assert isinstance(a_onvif, ONVIFCameraAdapter)
        assert a_onvif.get_capabilities().protocol == CameraProtocol.ONVIF_PROFILE_S

        a_file = create_camera_adapter(3, "file:///data/test.mp4")
        assert isinstance(a_file, FileCameraAdapter)
        assert a_file.get_capabilities().protocol == CameraProtocol.FILE

        a_usb = create_camera_adapter(4, "usb://0")
        assert isinstance(a_usb, USBCameraAdapter)
        assert a_usb.get_capabilities().protocol == CameraProtocol.USB

        a_syn = create_camera_adapter(5, "demo://test")
        assert isinstance(a_syn, SyntheticTestPatternAdapter)
        assert a_syn.get_capabilities().protocol == CameraProtocol.SYNTHETIC

    def test_synthetic_adapter_frame_generation(self):
        """Verify synthetic adapter produces valid BGR frames with calibration grid."""
        adapter = SyntheticTestPatternAdapter(camera_id=99, stream_url="synthetic://0", width=640, height=360, fps=30.0)
        assert adapter.open() is True
        assert adapter.is_opened() is True

        ret, frame = adapter.read()
        assert ret is True
        assert frame is not None
        assert frame.shape == (360, 640, 3)
        assert frame.dtype == np.uint8

        # Test digital PTZ execution on synthetic adapter
        res = adapter.execute_ptz(PTZCommand(direction="zoom_in", speed=1.0))
        assert res["status"] == "success"
        assert res["ptz_state"]["zoom"] > 1.0

        # Read zoomed frame
        ret_z, frame_z = adapter.read()
        assert ret_z is True
        assert frame_z.shape == (360, 640, 3)

        adapter.release()
        assert adapter.is_opened() is False

    def test_ws_security_header_format(self):
        """Verify ONVIF WS-Security PasswordDigest complies with Oasis standard."""
        header = build_ws_security_header("admin", "P@ssw0rd")
        assert "<wsse:Username>admin</wsse:Username>" in header
        assert "PasswordDigest" in header
        assert "<wsse:Nonce" in header
        assert "<wsu:Created>" in header

    def test_onvif_capabilities_reporting(self):
        """Verify ONVIF adapter reports supported profiles and honest hardware PTZ flag."""
        adapter = ONVIFCameraAdapter(camera_id=10, stream_url="rtsp://192.168.1.50/media")
        caps = adapter.get_capabilities()
        assert "Profile S" in caps.supported_profiles
        assert "Profile T" in caps.supported_profiles
        assert "Profile M" in caps.supported_profiles
        # Without a live physical ONVIF daemon responding to SOAP, hardware PTZ is truthfully False
        assert caps.is_hardware_ptz is False

    def test_probe_match_parsing(self):
        """Verify ONVIF ProbeMatch XML parser extracts IP and hardware scopes."""
        sample_xml = """<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope">
          <soap:Body>
            <d:ProbeMatches xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery">
              <d:ProbeMatch>
                <d:XAddrs>http://192.168.1.120:80/onvif/device_service</d:XAddrs>
                <d:Scopes>onvif://www.onvif.org/type/NetworkVideoTransmitter onvif://www.onvif.org/hardware/DS-2CD2043G2 onvif://www.onvif.org/name/Hikvision</d:Scopes>
              </d:ProbeMatch>
            </d:ProbeMatches>
          </soap:Body>
        </soap:Envelope>"""
        parsed = parse_probe_match(sample_xml, "192.168.1.120")
        assert parsed is not None
        assert parsed["ip"] == "192.168.1.120"
        assert "http://192.168.1.120:80/onvif/device_service" in parsed["xaddrs"]
        assert parsed["model"] == "DS-2CD2043G2"
        assert parsed["manufacturer"] == "Hikvision"


class TestCameraAdaptersIntegration:
    """Integration tests verifying API endpoints route through CameraAdapters."""

    def test_ptz_endpoint_routes_through_adapter(self):
        """Verify POST /cameras/{id}/ptz triggers adapter and returns is_hardware flag."""
        db = SessionLocal()
        try:
            cam = db.query(Camera).filter(Camera.id == 1).first()
            if not cam:
                cam = Camera(id=1, name="Test Border Tower 1", stream_url="demo://cam1", status="ONLINE")
                db.add(cam)
                db.commit()
        finally:
            db.close()

        resp = client.post("/api/v1/cameras/1/ptz", json={"direction": "left", "speed": 0.5}, headers=AUTH_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert "is_hardware" in data
        assert data["ptz_state"]["pan"] < 0

        # Reset home
        resp_h = client.post("/api/v1/cameras/1/ptz", json={"direction": "home"}, headers=AUTH_HEADERS)
        assert resp_h.status_code == 200
        assert resp_h.json()["ptz_state"]["pan"] == 0.0

    def test_ptz_goto_preset_routes_through_adapter(self):
        """Verify POST /cameras/{id}/ptz/goto executes through adapter."""
        resp = client.post("/api/v1/cameras/1/ptz/goto", json={"preset": "WATCHTOWER"}, headers=AUTH_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert "is_hardware" in data
        assert data["ptz_state"]["preset"] == "WATCHTOWER"
        assert data["ptz_state"]["pan"] == 45.0
        assert data["ptz_state"]["tilt"] == 10.0
