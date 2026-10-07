"""
RTSP Camera Adapter
===================

Universal RTSP stream ingestion with TCP interleaved transport, socket timeout
hardening, credential sanitization, resolution/FPS negotiation, and graceful digital PTZ.
"""

from __future__ import annotations
import os
import re
import cv2
import time
import logging
from typing import Optional, Dict, Any, Tuple
import numpy as np

from edge.adapters.base import CameraAdapter, CameraCapabilities, CameraProtocol

logger = logging.getLogger(__name__)


def sanitize_rtsp_url(url: str) -> str:
    """Remove password and sensitive tokens from RTSP stream URL for logging."""
    if not url:
        return ""
    return re.sub(r":([^:@/]+)@", r":***@", url)


class RTSPCameraAdapter(CameraAdapter):
    """Adapter for standard IP surveillance cameras transmitting over RTSP."""

    def __init__(self, camera_id: int, stream_url: str, **kwargs):
        super().__init__(camera_id, stream_url, **kwargs)
        self._cap: Optional[cv2.VideoCapture] = None
        self._sanitized_url = sanitize_rtsp_url(stream_url)
        self._width = 1280
        self._height = 720
        self._fps = 15.0

    def open(self) -> bool:
        """Establish RTSP connection using TCP transport and 3s socket timeout."""
        if self._cap is not None:
            self.release()

        # Enforce low-latency TCP transport and prevent UDP socket drops
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
            "rtsp_transport;tcp|stimeout;3000000|buffer_size;1024000|max_delay;500000"
        )
        try:
            self._cap = cv2.VideoCapture(self.stream_url, cv2.CAP_FFMPEG)
            if not self._cap.isOpened():
                logger.warning(f"RTSPCameraAdapter [{self.camera_id}]: Failed to open {self._sanitized_url}")
                self._is_opened = False
                return False

            self._width = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1280)
            self._height = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 720)
            fps_val = self._cap.get(cv2.CAP_PROP_FPS)
            self._fps = float(fps_val) if fps_val and 1.0 <= fps_val <= 60.0 else 15.0

            self._is_opened = True
            logger.info(
                f"RTSPCameraAdapter [{self.camera_id}]: Connected to {self._sanitized_url} "
                f"({self._width}x{self._height} @ {self._fps:.1f} fps)"
            )
            return True
        except Exception as e:
            logger.error(f"RTSPCameraAdapter [{self.camera_id}]: Connection exception: {e}")
            self._is_opened = False
            return False

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        """Read frame from RTSP stream and apply digital viewport PTZ if active."""
        if not self._is_opened or self._cap is None:
            return False, None

        ret, frame = self._cap.read()
        if not ret or frame is None:
            return False, None

        self._frame_count += 1
        self._last_frame_time = time.time()

        # Apply digital PTZ viewport transformation if requested
        if self._current_ptz_state.get("zoom", 1.0) > 1.001 or self._current_ptz_state.get("preset") != "HOME":
            frame = self.apply_digital_ptz(frame)

        return True, frame

    def release(self) -> None:
        """Release VideoCapture handle."""
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception as e:
                logger.debug(f"RTSPCameraAdapter [{self.camera_id}]: Error during release: {e}")
            self._cap = None
        self._is_opened = False

    def get_capabilities(self) -> CameraCapabilities:
        """Return capabilities for this RTSP endpoint."""
        return CameraCapabilities(
            can_ptz=True,               # Supported via digital viewport PTZ
            is_hardware_ptz=False,      # Pure RTSP stream has no hardware PTZ backchannel
            can_continuous_move=True,
            can_absolute_move=True,
            can_relative_move=True,
            can_presets=True,
            supported_profiles=["RTSP/RTP"],
            protocol=CameraProtocol.RTSP,
            max_zoom=4.0,
            pan_range=(-100.0, 100.0),
            tilt_range=(-45.0, 45.0),
            zoom_range=(1.0, 4.0),
            supports_night_vision=True,
            max_resolution=(self._width, self._height),
            nominal_fps=self._fps,
        )

    def probe_stream(self) -> Dict[str, Any]:
        """Perform comprehensive stream inspection."""
        return {
            "camera_id": self.camera_id,
            "url": self._sanitized_url,
            "is_opened": self._is_opened,
            "resolution": f"{self._width}x{self._height}",
            "fps": round(self._fps, 2),
            "transport": "TCP",
            "frame_count": self._frame_count,
            "last_frame_time": self._last_frame_time,
        }
