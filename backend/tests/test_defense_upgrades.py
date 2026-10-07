"""
Unit and Integration Tests for Tier-1 Military Defense Upgrades
===============================================================
1. WGS84 Camera-to-GPS Geo-Registration Engine
2. Section 65B Indian Evidence Act Forensic Certificate
3. SAHI Watchtower Slicing Inference Engine
4. Adverse Weather De-Hazing and Fog Stripping Module
"""

import pytest
import numpy as np
from datetime import datetime

from backend.app.services.georegistration import project_pixel_to_gps
from backend.app.services.evidence import generate_section_65b_certificate
from edge.detection.factory import create_detector
from edge.detection.sahi_tiler import slice_frame, run_sahi_inference
from edge.modules.weather_filter import AdverseWeatherFilter


class TestWGS84GeoRegistration:
    def test_forward_projection_returns_valid_coordinates(self):
        res = project_pixel_to_gps(
            pixel_x=640,
            pixel_y=680,
            camera_lat=28.6139,
            camera_lon=77.2090,
            camera_height_m=12.0,
            tilt_deg=15.0,
            heading_deg=45.0,
        )
        assert "target_latitude" in res
        assert "target_longitude" in res
        assert "ground_distance_m" in res
        assert "bearing_deg" in res
        assert "coordinate_string" in res

        # Target should be displaced from camera origin
        assert res["ground_distance_m"] > 0.0
        assert res["bearing_deg"] == pytest.approx(45.0, abs=5.0)
        assert "N" in res["coordinate_string"]
        assert "E" in res["coordinate_string"]

    def test_azimuth_offset_changes_with_pixel_x(self):
        # Left of frame vs Right of frame should have different bearing
        res_left = project_pixel_to_gps(
            pixel_x=100, pixel_y=600,
            camera_lat=28.6139, camera_lon=77.2090,
            heading_deg=0.0
        )
        res_right = project_pixel_to_gps(
            pixel_x=1180, pixel_y=600,
            camera_lat=28.6139, camera_lon=77.2090,
            heading_deg=0.0
        )
        assert res_left["bearing_deg"] != res_right["bearing_deg"]
        # Left should have bearing < 360 (North-West) while Right has bearing > 0 (North-East)
        assert res_left["bearing_deg"] > 180.0
        assert res_right["bearing_deg"] < 180.0


class TestSection65BCertificate:
    def test_certificate_structure_and_statutory_declaration(self):
        cert = generate_section_65b_certificate(
            evidence_id=101,
            incident_code="INC-20260904-A1B2",
            sha256_digest="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            camera_name="BOP-01 Gate Optical Node",
            bop_sector="BOP-01 (Sector Alpha)",
        )
        assert cert["evidence_id"] == 101
        assert cert["incident_code"] == "INC-20260904-A1B2"
        assert "Section 65B(4)" in cert["legal_statute"]
        assert "Bharatiya Sakshya Adhiniyam" in cert["legal_statute"]
        assert "e3b0c442" in cert["legal_declaration"]
        assert "Bharatiya Sakshya Adhiniyam, 2023 §63" in cert["admissibility_status"]
        assert "judicial assessment" in cert["admissibility_status"]
        assert "WORM" in cert["storage_integrity"]


class TestSAHIWatchtowerTiler:
    def test_frame_slicing_grid(self):
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        tiles = slice_frame(frame, slice_width=640, slice_height=640, overlap_ratio=0.2)
        assert len(tiles) >= 6
        for tile, off_x, off_y in tiles:
            assert tile.shape[0] == 640
            assert tile.shape[1] == 640
            assert 0 <= off_x <= 1920 - 640
            assert 0 <= off_y <= 1080 - 640

    def test_sahi_inference_protocol(self):
        detector = create_detector("yolo26n")
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        dets = run_sahi_inference(detector, frame, slice_width=640, slice_height=640)
        assert isinstance(dets, list)


class TestAdverseWeatherFilter:
    def test_dark_channel_computation(self):
        wf = AdverseWeatherFilter()
        img = np.random.randint(50, 200, (100, 100, 3), dtype=np.uint8)
        dark = wf.compute_dark_channel(img.astype(np.float32) / 255.0)
        assert dark.shape == (100, 100)
        assert np.max(dark) <= 1.0

    def test_dehaze_frame_enhancement(self):
        wf = AdverseWeatherFilter()
        hazy_frame = np.full((120, 160, 3), 190, dtype=np.uint8)
        # Add high contrast object inside fog
        hazy_frame[40:80, 60:100] = 30
        enhanced, info = wf.process(hazy_frame, mode="force")
        assert enhanced.shape == hazy_frame.shape
        assert enhanced.dtype == np.uint8
        assert info["applied"] is True
        assert info["fog_density"] > 0.5
