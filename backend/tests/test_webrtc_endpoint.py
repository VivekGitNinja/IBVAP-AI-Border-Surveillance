"""
Unit and integration tests for Tactical WebRTC Stream Negotiation Endpoints.
"""

from fastapi.testclient import TestClient
import pytest

from backend.app.main import app
from backend.app.core.security import create_access_token

client = TestClient(app)
_token = create_access_token("operator", role="OPERATOR")
AUTH_HEADERS = {"Authorization": f"Bearer {_token}"}


class TestWebRTCEndpoint:
    def test_webrtc_offer_negotiation(self):
        offer_sdp = (
            "v=0\r\n"
            "o=- 12345678 2 IN IP4 127.0.0.1\r\n"
            "s=-\r\n"
            "t=0 0\r\n"
            "m=video 9 UDP/TLS/RTP/SAVPF 96\r\n"
            "c=IN IP4 0.0.0.0\r\n"
            "a=recvonly\r\n"
        )
        payload = {
            "sdp": offer_sdp,
            "type": "offer",
            "stream_id": "cam-bop-01",
            "codec_preference": "H264",
        }

        # Unauthorized
        resp_unauth = client.post("/api/v1/webrtc/offer", json=payload)
        assert resp_unauth.status_code == 401

        # Authorized
        resp = client.post("/api/v1/webrtc/offer", json=payload, headers=AUTH_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] == "answer"
        assert data["stream_id"] == "cam-bop-01"
        assert data["codec"] == "H264"
        assert "session_id" in data
        assert "v=0" in data["sdp"]
        assert "a=fingerprint:sha-256" in data["sdp"]

        session_id = data["session_id"]

        # 2. Ingest ICE candidate
        ice_payload = {
            "session_id": session_id,
            "candidate": "candidate:1 1 UDP 2130706431 192.168.1.100 50000 typ host",
            "sdpMid": "0",
            "sdpMLineIndex": 0,
        }
        resp_ice = client.post("/api/v1/webrtc/ice-candidate", json=ice_payload, headers=AUTH_HEADERS)
        assert resp_ice.status_code == 200
        assert resp_ice.json()["status"] == "candidate_registered"

        # 3. List active sessions
        resp_list = client.get("/api/v1/webrtc/sessions", headers=AUTH_HEADERS)
        assert resp_list.status_code == 200
        sessions = resp_list.json()
        assert any(s["session_id"] == session_id for s in sessions)

        # 4. Get session stats
        resp_stats = client.get(f"/api/v1/webrtc/sessions/{session_id}/stats", headers=AUTH_HEADERS)
        assert resp_stats.status_code == 200
        stats = resp_stats.json()
        assert stats["session_id"] == session_id
        assert stats["fps"] > 0
        assert stats["bitrate_kbps"] > 0

        # 5. Teardown session
        resp_del = client.delete(f"/api/v1/webrtc/sessions/{session_id}", headers=AUTH_HEADERS)
        assert resp_del.status_code == 200
        assert resp_del.json()["status"] == "terminated"

        # 6. Verify deleted session 404
        resp_stats_404 = client.get(f"/api/v1/webrtc/sessions/{session_id}/stats", headers=AUTH_HEADERS)
        assert resp_stats_404.status_code == 404

    def test_webrtc_invalid_offer(self):
        resp = client.post(
            "/api/v1/webrtc/offer",
            json={"sdp": "   ", "type": "offer", "stream_id": "cam-01"},
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 400

    def test_webrtc_ice_candidate_not_found(self):
        resp = client.post(
            "/api/v1/webrtc/ice-candidate",
            json={"session_id": "nonexistent-sess-999", "candidate": "dummy"},
            headers=AUTH_HEADERS,
        )
        assert resp.status_code == 404
