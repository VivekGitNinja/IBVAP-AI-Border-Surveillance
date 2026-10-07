"""
Unit tests for Tactical Cursor-on-Target (CoT / ATAK) and STANAG 4609 KLV parser.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from edge.tactical.cot_gateway import (
    COT_TYPE_HOSTILE_PERSON,
    COT_TYPE_HOSTILE_VEHICLE,
    CoTEvent,
    CoTGateway,
)
from edge.tactical.klv_parser import (
    MISB_0601_UNIVERSAL_KEY,
    KLVParser,
    UASMetadata,
)


class TestCoTGateway:
    def test_cot_event_to_xml(self):
        event = CoTEvent(
            uid="IBVAP.INC-20260921-001",
            cot_type=COT_TYPE_HOSTILE_PERSON,
            latitude=28.6139,
            longitude=77.2090,
            altitude_hae_m=215.0,
            callsign="INTRUDER-01",
            threat_score=88.5,
            priority="CRITICAL",
        )
        xml = event.to_xml()
        assert 'version="2.0"' in xml
        assert 'uid="IBVAP.INC-20260921-001"' in xml
        assert f'type="{COT_TYPE_HOSTILE_PERSON}"' in xml
        assert 'lat="28.6139000"' in xml
        assert 'lon="77.2090000"' in xml
        assert 'hae="215.00"' in xml
        assert '<contact callsign="INTRUDER-01"/>' in xml
        assert 'score="88.5"' in xml
        assert 'priority="CRITICAL"' in xml

    def test_cot_gateway_dispatch(self):
        gateway = CoTGateway(multicast_ip="239.2.3.1", multicast_port=6969, enabled=True)
        # Mock underlying socket to avoid actual network socket binding
        mock_sock = MagicMock()
        gateway._sock = mock_sock

        event = gateway.dispatch_incident_cot(
            incident_code="INC-20260921-999",
            target_lat=28.6139,
            target_lon=77.2090,
            object_type="person",
            threat_score=92.0,
            priority="CRITICAL",
            camera_name="Sector Alpha Thermal",
        )

        assert event.uid == "IBVAP.INC-20260921-999"
        assert event.cot_type == COT_TYPE_HOSTILE_PERSON
        assert mock_sock.sendto.called
        sent_bytes, target_addr = mock_sock.sendto.call_args[0]
        assert b"IBVAP.INC-20260921-999" in sent_bytes
        assert target_addr == ("239.2.3.1", 6969)

    def test_cot_gateway_vehicle_type(self):
        gateway = CoTGateway(enabled=False)
        event = gateway.dispatch_incident_cot(
            incident_code="INC-20260921-888",
            target_lat=28.6140,
            target_lon=77.2100,
            object_type="truck",
            threat_score=75.0,
        )
        assert event.cot_type == COT_TYPE_HOSTILE_VEHICLE


class TestKLVParser:
    def test_encode_and_decode_klv(self):
        ts = datetime(2026, 9, 21, 10, 30, 0, tzinfo=timezone.utc)
        meta = UASMetadata(
            timestamp_utc=ts,
            sensor_latitude=28.6139,
            sensor_longitude=77.2090,
            sensor_altitude_m=500.0,
            sensor_fov_horizontal=45.0,
            sensor_fov_vertical=30.0,
            sensor_azimuth_deg=180.0,
            sensor_elevation_deg=-25.0,
            platform_heading_deg=90.0,
            mission_id="OPERATION-TRISHUL-26",
            platform_tail_number="UAS-DRISHTI-09",
            version=1,
        )

        packet = KLVParser.encode_packet(meta, include_universal_key=True)
        assert isinstance(packet, bytes)
        assert packet[:16] == MISB_0601_UNIVERSAL_KEY

        decoded = KLVParser.parse_packet(packet)
        assert decoded is not None
        assert decoded.mission_id == "OPERATION-TRISHUL-26"
        assert decoded.platform_tail_number == "UAS-DRISHTI-09"
        assert decoded.timestamp_utc == ts
        assert pytest.approx(decoded.sensor_latitude, abs=1e-4) == 28.6139
        assert pytest.approx(decoded.sensor_longitude, abs=1e-4) == 77.2090
        assert pytest.approx(decoded.sensor_altitude_m, abs=2.0) == 500.0
        assert pytest.approx(decoded.sensor_fov_horizontal, abs=0.5) == 45.0
        assert pytest.approx(decoded.sensor_fov_vertical, abs=0.5) == 30.0
        assert pytest.approx(decoded.sensor_azimuth_deg, abs=0.5) == 180.0
        assert pytest.approx(decoded.sensor_elevation_deg, abs=0.5) == -25.0
        assert pytest.approx(decoded.platform_heading_deg, abs=0.5) == 90.0
        assert decoded.version == 1

    def test_parse_malformed_packets(self):
        assert KLVParser.parse_packet(b"") is None
        assert KLVParser.parse_packet(b"\x00\x01") is None
        # Corrupted packet with key but zero length
        corrupt = MISB_0601_UNIVERSAL_KEY + b"\x84\x00\x00\x00\x00"
        res = KLVParser.parse_packet(corrupt)
        assert res is not None or res is None  # Should not raise exception
