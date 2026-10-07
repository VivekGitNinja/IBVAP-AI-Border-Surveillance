"""
Base Camera Adapter & Capability Abstractions
=============================================

Vendor-neutral camera hardware and stream protocol abstraction layer.
Enforces honest capability reporting, graceful digital fallbacks, and standard
control interfaces across RTSP, ONVIF (Profile S/T/M), USB, Video Files, and Synthetic Generators.
"""

from __future__ import annotations
import abc
import enum
import time
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, field
import numpy as np


class CameraProtocol(str, enum.Enum):
    RTSP = "RTSP"
    ONVIF_PROFILE_S = "ONVIF_PROFILE_S"
    ONVIF_PROFILE_T = "ONVIF_PROFILE_T"
    ONVIF_PROFILE_M = "ONVIF_PROFILE_M"
    USB = "USB"
    FILE = "FILE"
    SYNTHETIC = "SYNTHETIC"
    PHONE = "PHONE"


@dataclass
class CameraCapabilities:
    """Honest, verified capability contract for a connected camera."""
    can_ptz: bool = False
    is_hardware_ptz: bool = False
    can_continuous_move: bool = False
    can_absolute_move: bool = False
    can_relative_move: bool = False
    can_presets: bool = False
    supported_profiles: List[str] = field(default_factory=list)
    protocol: CameraProtocol = CameraProtocol.RTSP
    max_zoom: float = 1.0
    pan_range: Tuple[float, float] = (-180.0, 180.0)
    tilt_range: Tuple[float, float] = (-90.0, 90.0)
    zoom_range: Tuple[float, float] = (1.0, 1.0)
    supports_night_vision: bool = False
    max_resolution: Tuple[int, int] = (1920, 1080)
    nominal_fps: float = 15.0


@dataclass
class PTZCommand:
    """Normalized PTZ operation request."""
    direction: str                     # "left", "right", "up", "down", "zoom_in", "zoom_out", "home", "stop"
    speed: float = 0.5                 # 0.0 to 1.0 normalized velocity
    duration_seconds: float = 0.5      # For continuous move pulses
    pan_degrees: Optional[float] = None  # For absolute move
    tilt_degrees: Optional[float] = None
    zoom_level: Optional[float] = None
    preset: Optional[str] = None       # For preset operations


class CameraAdapter(abc.ABC):
    """Abstract Base Class for all video ingestion and camera control adapters."""

    def __init__(self, camera_id: int, stream_url: str, **kwargs):
        self.camera_id = camera_id
        self.stream_url = stream_url
        self.kwargs = kwargs
        self._is_opened = False
        self._last_frame_time = 0.0
        self._frame_count = 0
        self._current_ptz_state = {
            "pan": 0.0,
            "tilt": 0.0,
            "zoom": 1.0,
            "preset": "HOME",
            "is_hardware": False,
        }

    @abc.abstractmethod
    def open(self) -> bool:
        """Initialize connection to physical camera or stream source."""
        pass

    @abc.abstractmethod
    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        """Acquire latest video frame from the stream."""
        pass

    @abc.abstractmethod
    def release(self) -> None:
        """Gracefully terminate stream connection and release system resources."""
        pass

    def is_opened(self) -> bool:
        """Query if capture pipeline is currently active."""
        return self._is_opened

    @abc.abstractmethod
    def get_capabilities(self) -> CameraCapabilities:
        """Return honest hardware and stream capabilities."""
        pass

    def execute_ptz(self, cmd: PTZCommand) -> Dict[str, Any]:
        """Execute PTZ command. Default implementation provides digital viewport PTZ."""
        caps = self.get_capabilities()
        d = cmd.direction.lower().strip()
        step = max(2.0, min(25.0, cmd.speed * 20.0))

        if d == "left":
            self._current_ptz_state["pan"] = max(caps.pan_range[0], self._current_ptz_state["pan"] - step)
            self._current_ptz_state["preset"] = "MANUAL"
        elif d == "right":
            self._current_ptz_state["pan"] = min(caps.pan_range[1], self._current_ptz_state["pan"] + step)
            self._current_ptz_state["preset"] = "MANUAL"
        elif d == "up":
            self._current_ptz_state["tilt"] = min(caps.tilt_range[1], self._current_ptz_state["tilt"] + step)
            self._current_ptz_state["preset"] = "MANUAL"
        elif d == "down":
            self._current_ptz_state["tilt"] = max(caps.tilt_range[0], self._current_ptz_state["tilt"] - step)
            self._current_ptz_state["preset"] = "MANUAL"
        elif d == "zoom_in":
            self._current_ptz_state["zoom"] = min(caps.max_zoom, self._current_ptz_state["zoom"] + 0.25 * cmd.speed)
            self._current_ptz_state["preset"] = "MANUAL"
        elif d == "zoom_out":
            self._current_ptz_state["zoom"] = max(caps.zoom_range[0], self._current_ptz_state["zoom"] - 0.25 * cmd.speed)
            self._current_ptz_state["preset"] = "MANUAL"
        elif d in ("home", "reset"):
            self._current_ptz_state["pan"] = 0.0
            self._current_ptz_state["tilt"] = 0.0
            self._current_ptz_state["zoom"] = 1.0
            self._current_ptz_state["preset"] = "HOME"

        return {
            "status": "success",
            "camera_id": self.camera_id,
            "command": d,
            "is_hardware": caps.is_hardware_ptz,
            "ptz_state": self._current_ptz_state.copy(),
        }

    def get_ptz_status(self) -> Dict[str, Any]:
        """Return current pan, tilt, zoom coordinates."""
        caps = self.get_capabilities()
        state = self._current_ptz_state.copy()
        state["is_hardware"] = caps.is_hardware_ptz
        return state

    def get_presets(self) -> List[Dict[str, Any]]:
        """Return configured tactical presets."""
        return [
            {"id": "HOME", "name": "Sector Gate Alpha (Home)", "pan": 0.0, "tilt": 0.0, "zoom": 1.0},
            {"id": "WATCHTOWER", "name": "Perimeter Watchtower North", "pan": 45.0, "tilt": 10.0, "zoom": 2.2},
            {"id": "TRENCH", "name": "Anti-Infiltration Trench", "pan": -30.0, "tilt": -12.0, "zoom": 1.8},
            {"id": "ROAD_JUNCTION", "name": "Supply Road Intersect", "pan": 75.0, "tilt": 5.0, "zoom": 3.0},
            {"id": "PRESET_CHECKPOINT", "name": "Vehicle Entry Choke Point", "pan": 15.0, "tilt": -12.0, "zoom": 2.2},
        ]

    def goto_preset(self, preset_name: str) -> Dict[str, Any]:
        """Move camera to preset."""
        target = None
        for p in self.get_presets():
            if p["id"].lower() == preset_name.lower() or p["name"].lower() == preset_name.lower():
                target = p
                break
        if not target:
            raise ValueError(f"Preset '{preset_name}' not recognized")

        self._current_ptz_state["pan"] = target["pan"]
        self._current_ptz_state["tilt"] = target["tilt"]
        self._current_ptz_state["zoom"] = target["zoom"]
        self._current_ptz_state["preset"] = target["id"]

        caps = self.get_capabilities()
        return {
            "status": "success",
            "camera_id": self.camera_id,
            "preset": target["name"],
            "is_hardware": caps.is_hardware_ptz,
            "ptz_state": self._current_ptz_state.copy(),
        }

    def apply_digital_ptz(self, frame: np.ndarray) -> np.ndarray:
        """Apply software digital zoom and pan/tilt crop if hardware PTZ is unavailable."""
        zoom = max(1.0, min(4.0, float(self._current_ptz_state.get("zoom", 1.0))))
        pan = float(self._current_ptz_state.get("pan", 0.0))
        tilt = float(self._current_ptz_state.get("tilt", 0.0))

        if zoom <= 1.001 and abs(pan) < 0.1 and abs(tilt) < 0.1:
            return frame

        import cv2
        h, w = frame.shape[:2]
        crop_w = int(w / zoom)
        crop_h = int(h / zoom)

        # Map pan/tilt degrees to pixel shift
        pan_shift = int((pan / 100.0) * (w - crop_w) * 0.5)
        tilt_shift = int((-tilt / 45.0) * (h - crop_h) * 0.5)

        cx = w // 2 + pan_shift
        cy = h // 2 + tilt_shift

        x1 = max(0, min(w - crop_w, cx - crop_w // 2))
        y1 = max(0, min(h - crop_h, cy - crop_h // 2))
        x2 = min(w, x1 + crop_w)
        y2 = min(h, y1 + crop_h)

        cropped = frame[y1:y2, x1:x2]
        if cropped.size == 0:
            return frame
        return cv2.resize(cropped, (w, h), interpolation=cv2.INTER_LINEAR)
