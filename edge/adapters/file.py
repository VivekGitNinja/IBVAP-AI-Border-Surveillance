"""
Video File Ingestion Adapter
============================

Ingests recorded surveillance footage from MP4/AVI/MKV files with loop control,
seek operations, framerate-synchronized playback, and digital PTZ inspection.
"""

from __future__ import annotations
import os
import cv2
import time
import logging
from typing import Optional, Dict, Any, Tuple
import numpy as np

from edge.adapters.base import CameraAdapter, CameraCapabilities, CameraProtocol

logger = logging.getLogger(__name__)


class FileCameraAdapter(CameraAdapter):
    """Adapter for recorded video files (forensics, replay, testing)."""

    def __init__(self, camera_id: int, stream_url: str, loop: bool = True, sync_fps: bool = True, **kwargs):
        super().__init__(camera_id, stream_url, **kwargs)
        self.file_path = stream_url.replace("file://", "")
        self.loop = loop
        self.sync_fps = sync_fps
        self._cap: Optional[cv2.VideoCapture] = None
        self._total_frames = 0
        self._width = 1280
        self._height = 720
        self._fps = 25.0
        self._last_read_ts = 0.0

    def open(self) -> bool:
        """Open video file and read metadata."""
        if not os.path.exists(self.file_path):
            logger.error(f"FileCameraAdapter [{self.camera_id}]: File not found: {self.file_path}")
            self._is_opened = False
            return False

        if self._cap is not None:
            self.release()

        try:
            self._cap = cv2.VideoCapture(self.file_path)
            if not self._cap.isOpened():
                self._is_opened = False
                return False

            self._total_frames = int(self._cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            self._width = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1280)
            self._height = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 720)
            fps_val = self._cap.get(cv2.CAP_PROP_FPS)
            self._fps = float(fps_val) if fps_val and 1.0 <= fps_val <= 60.0 else 25.0

            self._is_opened = True
            logger.info(
                f"FileCameraAdapter [{self.camera_id}]: Opened {self.file_path} "
                f"({self._total_frames} frames, {self._width}x{self._height} @ {self._fps:.1f} fps)"
            )
            return True
        except Exception as e:
            logger.error(f"FileCameraAdapter [{self.camera_id}]: Error opening file: {e}")
            self._is_opened = False
            return False

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        """Read frame with optional FPS pacing and loop-around."""
        if not self._is_opened or self._cap is None:
            return False, None

        if self.sync_fps and self._last_read_ts > 0:
            target_delay = 1.0 / self._fps
            elapsed = time.time() - self._last_read_ts
            if elapsed < target_delay:
                time.sleep(target_delay - elapsed)

        ret, frame = self._cap.read()
        self._last_read_ts = time.time()

        if not ret or frame is None:
            if self.loop and self._total_frames > 0:
                self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ret, frame = self._cap.read()
                if not ret or frame is None:
                    return False, None
            else:
                return False, None

        self._frame_count += 1
        self._last_frame_time = time.time()

        # Apply digital PTZ viewport transformation if requested
        if self._current_ptz_state.get("zoom", 1.0) > 1.001 or self._current_ptz_state.get("preset") != "HOME":
            frame = self.apply_digital_ptz(frame)

        return True, frame

    def seek(self, frame_number: int) -> bool:
        """Seek to specific frame position."""
        if self._cap is not None and self._is_opened:
            pos = max(0, min(self._total_frames - 1, frame_number))
            return bool(self._cap.set(cv2.CAP_PROP_POS_FRAMES, pos))
        return False

    def release(self) -> None:
        """Release video handle."""
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None
        self._is_opened = False

    def get_capabilities(self) -> CameraCapabilities:
        """Return video file capabilities."""
        return CameraCapabilities(
            can_ptz=True,
            is_hardware_ptz=False,
            can_continuous_move=True,
            can_absolute_move=True,
            can_relative_move=True,
            can_presets=True,
            supported_profiles=["FILE"],
            protocol=CameraProtocol.FILE,
            max_zoom=4.0,
            pan_range=(-100.0, 100.0),
            tilt_range=(-45.0, 45.0),
            zoom_range=(1.0, 4.0),
            supports_night_vision=False,
            max_resolution=(self._width, self._height),
            nominal_fps=self._fps,
        )
