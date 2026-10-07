"""
Synthetic Test Pattern Camera Adapter
=====================================

Deterministic, mathematically calibrated synthetic test pattern generator
for CI/CD testing, algorithm benchmarking, and edge verification without physical cameras.
Truthfully labeled as SYNTHETIC with honest capability metadata.
"""

from __future__ import annotations
import cv2
import time
import math
import logging
from typing import Optional, Dict, Any, Tuple
import numpy as np

from edge.adapters.base import CameraAdapter, CameraCapabilities, CameraProtocol, PTZCommand

logger = logging.getLogger(__name__)


class SyntheticTestPatternAdapter(CameraAdapter):
    """Adapter producing synthetic surveillance test frames for CI/CD and verification."""

    def __init__(self, camera_id: int, stream_url: str = "synthetic://0", width: int = 1280, height: int = 720, fps: float = 20.0, **kwargs):
        super().__init__(camera_id, stream_url, **kwargs)
        self.width = width
        self.height = height
        self.fps = fps
        self._target_speed_px_per_sec = 80.0
        self._start_time = 0.0

    def open(self) -> bool:
        """Initialize synthetic frame engine."""
        self._is_opened = True
        self._start_time = time.time()
        self._frame_count = 0
        logger.info(f"SyntheticTestPatternAdapter [{self.camera_id}]: Initialized ({self.width}x{self.height} @ {self.fps} fps)")
        return True

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        """Generate next deterministic test frame."""
        if not self._is_opened:
            return False, None

        self._frame_count += 1
        now = time.time()
        self._last_frame_time = now

        # Pace to target FPS
        time.sleep(1.0 / self.fps)

        frame = np.zeros((self.height, self.width, 3), dtype=np.uint8)

        # 1. Background gradient (simulating tactical terrain / sky)
        horizon = int(self.height * 0.5)
        frame[:horizon, :] = (25, 28, 35)    # Dark tactical sky
        frame[horizon:, :] = (35, 42, 40)    # Terrain ground plane

        # 2. Calibration grid lines (100px intervals)
        for gx in range(0, self.width, 100):
            cv2.line(frame, (gx, 0), (gx, self.height), (45, 52, 50), 1)
        for gy in range(0, self.height, 100):
            cv2.line(frame, (0, gy), (self.width, gy), (45, 52, 50), 1)

        # 3. Virtual Perimeter Security Fence (Zero Line)
        fence_y = horizon + 60
        cv2.line(frame, (0, fence_y), (self.width, fence_y), (70, 85, 80), 2)
        for px in range(40, self.width, 60):
            cv2.line(frame, (px, fence_y - 30), (px, fence_y + 40), (60, 75, 70), 1)

        # 4. Deterministic moving target (oscillating intruder vector)
        t = (self._frame_count % 300) / 300.0
        target_x = int(180 + (self.width - 360) * (0.5 + 0.5 * math.sin(t * 2 * math.pi)))
        target_y = int(fence_y - 20 + 30 * math.cos(t * 2 * math.pi))

        # Target silhouette (person-sized rectangular shape with head)
        cv2.rectangle(frame, (target_x - 20, target_y - 70), (target_x + 20, target_y + 30), (120, 140, 130), -1)
        cv2.circle(frame, (target_x, target_y - 85), 15, (120, 140, 130), -1)

        # 5. Contrast step wedge (evaluates dynamic range & night enhancement)
        for step_idx in range(8):
            step_val = int(step_idx * (255 / 7))
            sx = 20 + step_idx * 30
            sy = 20
            cv2.rectangle(frame, (sx, sy), (sx + 26, sy + 30), (step_val, step_val, step_val), -1)

        # 6. Honest telemetry overlay
        cv2.putText(frame, f"SYNTHETIC TEST PATTERN | CAM-{self.camera_id} | FRAME #{self._frame_count:06d}",
                    (20, self.height - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 240, 255), 1, cv2.LINE_AA)
        cv2.putText(frame, f"ISO-12233 CALIBRATION GRID | TARGET POS: ({target_x}, {target_y})",
                    (20, self.height - 45), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 200, 200), 1, cv2.LINE_AA)

        # Apply digital PTZ viewport transformation if requested
        if self._current_ptz_state.get("zoom", 1.0) > 1.001 or self._current_ptz_state.get("preset") != "HOME":
            frame = self.apply_digital_ptz(frame)

        return True, frame

    def release(self) -> None:
        """Stop generator."""
        self._is_opened = False

    def get_capabilities(self) -> CameraCapabilities:
        """Return capabilities for synthetic pattern generator."""
        return CameraCapabilities(
            can_ptz=True,
            is_hardware_ptz=False,
            can_continuous_move=True,
            can_absolute_move=True,
            can_relative_move=True,
            can_presets=True,
            supported_profiles=["SYNTHETIC"],
            protocol=CameraProtocol.SYNTHETIC,
            max_zoom=4.0,
            pan_range=(-100.0, 100.0),
            tilt_range=(-45.0, 45.0),
            zoom_range=(1.0, 4.0),
            supports_night_vision=True,
            max_resolution=(self.width, self.height),
            nominal_fps=self.fps,
        )
