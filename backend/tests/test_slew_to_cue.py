"""
Unit tests for Automated Slew-to-Cue PTZ Target Tracking Coordinator.
"""

import math
import pytest

from edge.tracking.slew_to_cue import (
    SlewToCueCoordinator,
    PTZNodeCalibration,
    SlewCueResult,
    haversine_distance_and_bearing,
)


class TestSlewToCueCoordinator:
    @pytest.fixture
    def coordinator(self):
        calib = PTZNodeCalibration(
            camera_id=10,
            name="BOP-Tower-01-PTZ",
            latitude=28.6139,
            longitude=77.2090,
            height_m=15.0,
            heading_datum_deg=0.0,
            min_tilt_deg=-75.0,
            max_tilt_deg=15.0,
            max_zoom=30.0,
        )
        coord = SlewToCueCoordinator(ptz_nodes={10: calib}, threat_lock_seconds=2.0)
        return coord

    def test_haversine_distance_and_bearing(self):
        # Coordinates ~111 meters North (0.001 deg latitude)
        lat1, lon1 = 28.6139, 77.2090
        lat2, lon2 = 28.6149, 77.2090

        dist, bearing = haversine_distance_and_bearing(lat1, lon1, lat2, lon2)
        assert 100.0 < dist < 120.0
        assert -1.0 <= bearing <= 1.0 or 359.0 <= bearing <= 360.0

    def test_compute_cue_success(self, coordinator):
        # Target positioned ~150 meters Northeast
        target_lat = 28.6149
        target_lon = 77.2100

        result = coordinator.compute_cue(
            ptz_camera_id=10,
            target_lat=target_lat,
            target_lon=target_lon,
            target_track_id=101,
            target_threat_score=85.0,
        )

        assert isinstance(result, SlewCueResult)
        assert result.ptz_camera_id == 10
        assert result.target_track_id == 101
        assert result.distance_meters > 50.0
        assert 0.0 <= result.azimuth_deg <= 360.0
        assert -180.0 <= result.pan_command_deg <= 180.0
        assert -75.0 <= result.tilt_command_deg <= 15.0
        assert 1.0 <= result.recommended_zoom <= 30.0
        assert result.ptz_command.direction == "absolute"
        assert result.ptz_command.pan_degrees == result.pan_command_deg
        assert result.ptz_command.tilt_degrees == result.tilt_command_deg

    def test_compute_cue_unregistered_camera(self, coordinator):
        result = coordinator.compute_cue(
            ptz_camera_id=999,
            target_lat=28.6149,
            target_lon=77.2100,
            target_track_id=102,
        )
        assert result is None

    def test_threat_preemption_and_lock(self, coordinator):
        # First target with high threat score (90)
        res1 = coordinator.compute_cue(
            ptz_camera_id=10,
            target_lat=28.6149,
            target_lon=77.2100,
            target_track_id=201,
            target_threat_score=90.0,
        )
        assert res1 is not None

        # Second target with lower threat score (40) immediately afterwards should be rejected
        res2 = coordinator.compute_cue(
            ptz_camera_id=10,
            target_lat=28.6150,
            target_lon=77.2110,
            target_track_id=202,
            target_threat_score=40.0,
        )
        assert res2 is None  # Blocked by active lock

        # Third target with higher threat score (95) preempts
        res3 = coordinator.compute_cue(
            ptz_camera_id=10,
            target_lat=28.6150,
            target_lon=77.2110,
            target_track_id=203,
            target_threat_score=95.0,
        )
        assert res3 is not None
        assert res3.target_track_id == 203
