"""
USB / Webcam Camera Adapter
===========================

Cross-platform local hardware capture supporting:
- Linux: Video4Linux2 (V4L2)
- macOS: AVFoundation
- Windows: DirectShow (DSHOW)
"""

from __future__ import annotations
import sys
import cv2
import time
import logging
from typing import Optional, Dict, Any, Tuple
import numpy as np

from edge.adapters.base import CameraAdapter, CameraCapabilities, CameraProtocol

logger = logging.getLogger(__name__)


class USBCameraAdapter(CameraAdapter):
    """Adapter for physical USB webcams and integrated cameras."""

    def __init__(self, camera_id: int, stream_url: str, device_index: int = 0, **kwargs):
        super().__init__(camera_id, stream_url, **kwargs)
        self.device_index = device_index
        # Parse device index if present in URL (e.g. usb://1 -> 1)
        if stream_url.startswith("usb://") or stream_url.startswith("webcam://"):
            suffix = stream_url.split("://")[-1]
            if suffix.isdigit():
                self.device_index = int(suffix)
        elif stream_url.isdigit():
            self.device_index = int(stream_url)

        self._cap: Optional[cv2.VideoCapture] = None
        self._width = 640
        self._height = 480
        self._fps = 30.0

    def _select_backend(self) -> int:
        """Select native platform capture backend."""
        if sys.platform == "darwin":
            return cv2.CAP_AVFOUNDATION
        elif sys.platform.startswith("linux"):
            return cv2.CAP_V4L2
        elif sys.platform == "win32":
            return cv2.CAP_DSHOW
        return cv2.CAP_ANY

    def open(self) -> bool:
        """Open physical hardware camera device."""
        if self._cap is not None:
            self.release()

        backend = self._select_backend()
        try:
            self._cap = cv2.VideoCapture(self.device_index, backend)
            if not self._cap.isOpened():
                # Fallback to cv2.CAP_ANY
                self._cap = cv2.VideoCapture(self.device_index, cv2.CAP_ANY)

            if not self._cap.isOpened():
                logger.warning(f"USBCameraAdapter [{self.camera_id}]: Device index {self.device_index} not accessible.")
                self._is_opened = False
                return False

            self._width = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 640)
            self._height = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 480)
            fps_val = self._cap.get(cv2.CAP_PROP_FPS)
            self._fps = float(fps_val) if fps_val and 1.0 <= fps_val <= 60.0 else 30.0

            self._is_opened = True
            logger.info(
                f"USBCameraAdapter [{self.camera_id}]: Hardware device {self.device_index} online "
                f"({self._width}x{self._height} @ {self._fps:.1f} fps)"
            )
            return True
        except Exception as e:
            logger.error(f"USBCameraAdapter [{self.camera_id}]: Hardware error opening device: {e}")
            self._is_opened = False
            return False

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        """Read frame from physical USB sensor."""
        if not self._is_opened or self._cap is None:
            return False, None

        ret, frame = self._cap.read()
        if not ret or frame is None:
            return False, None

        self._frame_count += 1
        self._last_frame_time = time.time()

        if self._current_ptz_state.get("zoom", 1.0) > 1.001 or self._current_ptz_state.get("preset") != "HOME":
            frame = self.apply_digital_ptz(frame)

        return True, frame

    def release(self) -> None:
        """Release OS hardware device handle."""
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None
        self._is_opened = False

    def get_capabilities(self) -> CameraCapabilities:
        """Return USB camera capabilities."""
        return CameraCapabilities(
            can_ptz=True,
            is_hardware_ptz=False,
            can_continuous_move=True,
            can_absolute_move=True,
            can_relative_move=True,
            can_presets=True,
            supported_profiles=["UVC"],
            protocol=CameraProtocol.USB,
            max_zoom=4.0,
            pan_range=(-100.0, 100.0),
            tilt_range=(-45.0, 45.0),
            zoom_range=(1.0, 4.0),
            supports_night_vision=False,
            max_resolution=(self._width, self._height),
            nominal_fps=self._fps,
        )
