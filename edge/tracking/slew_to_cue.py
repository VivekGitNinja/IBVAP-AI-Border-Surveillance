"""
Automated Slew-to-Cue PTZ Target Tracking Coordinator
=====================================================

Coordinates fixed wide-angle border surveillance nodes with motorized ONVIF PTZ
cameras. When a high-threat intrusion crosses a restricted polygon or tripwire,
Slew-to-Cue automatically computes target azimuth/elevation, optical zoom level,
and issues instant absolute target lock commands.
"""

from __future__ import annotations
import math
import time
import logging
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple, Any

from edge.adapters.base import PTZCommand

logger = logging.getLogger(__name__)


@dataclass
class PTZNodeCalibration:
    """Geographic positioning and optical calibration for a motorized PTZ node."""
    camera_id: int
    name: str
    latitude: float
    longitude: float
    height_m: float = 12.0
    heading_datum_deg: float = 0.0     # True North reference offset
    min_tilt_deg: float = -80.0
    max_tilt_deg: float = 20.0
    max_zoom: float = 30.0             # 30x optical zoom capability
    focal_length_mm: float = 4.5


@dataclass
class SlewCueResult:
    """Calculated spherical targeting telemetry for PTZ lock-on."""
    target_track_id: Any
    ptz_camera_id: int
    azimuth_deg: float                # Pan angle relative to True North (0-360)
    pan_command_deg: float            # Pan angle normalized to PTZ range (-180 to +180)
    tilt_command_deg: float           # Elevation angle (-90 to +90)
    distance_meters: float            # Ground slant distance to intruder
    recommended_zoom: float           # Magnification factor (1.0 to max_zoom)
    ptz_command: PTZCommand           # Concrete command ready for adapter dispatch
    timestamp: float = field(default_factory=time.time)


def haversine_distance_and_bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> Tuple[float, float]:
    """
    Calculate great-circle ground distance (meters) and initial bearing (degrees from True North).
    """
    R = 6371000.0  # Earth radius in meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    # Haversine distance
    a = math.sin(delta_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    distance = R * c

    # Forward azimuth bearing
    y = math.sin(delta_lambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(delta_lambda)
    bearing = (math.degrees(math.atan2(y, x)) + 360.0) % 360.0

    return distance, bearing


class SlewToCueCoordinator:
    """Orchestrates automated PTZ camera cueing based on fixed camera detection breaches."""

    def __init__(self, ptz_nodes: Optional[Dict[int, PTZNodeCalibration]] = None, threat_lock_seconds: float = 6.0):
        self.ptz_nodes: Dict[int, PTZNodeCalibration] = ptz_nodes or {}
        self.threat_lock_seconds = threat_lock_seconds
        self._active_locks: Dict[int, Dict[str, Any]] = {}  # ptz_id -> lock details

    def register_ptz_node(self, calibration: PTZNodeCalibration) -> None:
        """Register or update a motorized PTZ node calibration."""
        self.ptz_nodes[calibration.camera_id] = calibration

    def compute_cue(
        self,
        ptz_camera_id: int,
        target_lat: float,
        target_lon: float,
        target_track_id: Any,
        target_threat_score: float = 85.0,
        target_elevation_m: float = 0.0,
    ) -> Optional[SlewCueResult]:
        """
        Compute absolute spherical angles and generate Slew-to-Cue command.
        
        Args:
            ptz_camera_id: ID of the motorized PTZ camera designated for intercept
            target_lat, target_lon: Estimated GPS coordinates of intruder ground footprint
            target_track_id: ByteTrack / ReID track identifier
            target_threat_score: Priority threat score (0-100)
            target_elevation_m: Ground elevation of target relative to sea level
        """
        node = self.ptz_nodes.get(ptz_camera_id)
        if not node:
            logger.warning(f"SlewToCue: PTZ camera node {ptz_camera_id} not registered.")
            return None

        now = time.time()
        # Check if PTZ is locked on another higher-priority target
        active = self._active_locks.get(ptz_camera_id)
        if active and (now - active["timestamp"] < self.threat_lock_seconds):
            if active["track_id"] != target_track_id and active["threat_score"] > target_threat_score:
                logger.info(f"SlewToCue: PTZ {ptz_camera_id} busy with higher threat {active['track_id']}")
                return None

        # 1. Compute ground distance and True North azimuth
        distance_m, bearing_deg = haversine_distance_and_bearing(
            node.latitude, node.longitude, target_lat, target_lon
        )

        # 2. Normalize azimuth to PTZ pan range (-180° to +180°)
        rel_bearing = (bearing_deg - node.heading_datum_deg + 360.0) % 360.0
        pan_deg = rel_bearing if rel_bearing <= 180.0 else rel_bearing - 360.0

        # 3. Compute elevation / tilt angle
        delta_h = (node.height_m - target_elevation_m)
        tilt_rad = -math.atan2(delta_h, max(1.0, distance_m))
        tilt_deg = max(node.min_tilt_deg, min(node.max_tilt_deg, math.degrees(tilt_rad)))

        # 4. Compute optical zoom level to frame a person (~1.8m tall) at distance
        # Standard human target subtends 1.8m. Aim for person occupying ~30% of vertical sensor height.
        if distance_m <= 15.0:
            zoom_level = 1.0
        else:
            zoom_level = min(node.max_zoom, max(1.0, distance_m / 18.0))

        # 5. Build executable PTZ command
        cmd = PTZCommand(
            direction="absolute",
            speed=1.0,                       # Maximum slew velocity for rapid intercept
            pan_degrees=round(pan_deg, 2),
            tilt_degrees=round(tilt_deg, 2),
            zoom_level=round(zoom_level, 2),
        )

        # Update active lock
        self._active_locks[ptz_camera_id] = {
            "track_id": target_track_id,
            "threat_score": target_threat_score,
            "timestamp": now,
        }

        return SlewCueResult(
            target_track_id=target_track_id,
            ptz_camera_id=ptz_camera_id,
            azimuth_deg=round(bearing_deg, 2),
            pan_command_deg=round(pan_deg, 2),
            tilt_command_deg=round(tilt_deg, 2),
            distance_meters=round(distance_m, 2),
            recommended_zoom=round(zoom_level, 2),
            ptz_command=cmd,
            timestamp=now,
        )
