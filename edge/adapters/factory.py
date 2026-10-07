"""
Camera Adapter Factory
======================

Instantiates appropriate vendor-neutral CameraAdapter based on stream URL protocol.
"""

from __future__ import annotations
from typing import Optional
from edge.adapters.base import CameraAdapter
from edge.adapters.rtsp import RTSPCameraAdapter
from edge.adapters.onvif import ONVIFCameraAdapter
from edge.adapters.file import FileCameraAdapter
from edge.adapters.usb import USBCameraAdapter
from edge.adapters.synthetic import SyntheticTestPatternAdapter


def create_camera_adapter(camera_id: int, stream_url: str, **kwargs) -> CameraAdapter:
    """Factory creating the appropriate CameraAdapter for any video source."""
    url = (stream_url or "").strip()

    if url.startswith("onvif://"):
        clean_url = url.replace("onvif://", "rtsp://")
        return ONVIFCameraAdapter(camera_id=camera_id, stream_url=clean_url, **kwargs)
    elif kwargs.get("enable_onvif", False) or kwargs.get("onvif_ip"):
        return ONVIFCameraAdapter(camera_id=camera_id, stream_url=url, **kwargs)
    elif url.startswith("file://") or url.endswith((".mp4", ".avi", ".mkv", ".mov")):
        return FileCameraAdapter(camera_id=camera_id, stream_url=url, **kwargs)
    elif url.startswith("usb://") or url.startswith("webcam://") or url.startswith("camera://") or url.isdigit():
        return USBCameraAdapter(camera_id=camera_id, stream_url=url, **kwargs)
    elif url.startswith("synthetic://") or url.startswith("demo://") or not url:
        return SyntheticTestPatternAdapter(camera_id=camera_id, stream_url=url, **kwargs)
    else:
        # Standard RTSP / network stream
        return RTSPCameraAdapter(camera_id=camera_id, stream_url=url, **kwargs)
