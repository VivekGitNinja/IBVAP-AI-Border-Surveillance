"""
IBVAP Edge Camera Adapters Package
==================================

Vendor-neutral camera hardware and stream protocol adapters.
Supports RTSP, ONVIF (Profile S/T/M), USB/V4L2, File replay, and Synthetic test patterns.
"""

from edge.adapters.base import (
    CameraAdapter,
    CameraCapabilities,
    CameraProtocol,
    PTZCommand,
)
from edge.adapters.rtsp import RTSPCameraAdapter, sanitize_rtsp_url
from edge.adapters.onvif import ONVIFCameraAdapter
from edge.adapters.file import FileCameraAdapter
from edge.adapters.usb import USBCameraAdapter
from edge.adapters.synthetic import SyntheticTestPatternAdapter
from edge.adapters.discovery import discover_onvif_devices
from edge.adapters.factory import create_camera_adapter

__all__ = [
    "CameraAdapter",
    "CameraCapabilities",
    "CameraProtocol",
    "PTZCommand",
    "RTSPCameraAdapter",
    "ONVIFCameraAdapter",
    "FileCameraAdapter",
    "USBCameraAdapter",
    "SyntheticTestPatternAdapter",
    "discover_onvif_devices",
    "create_camera_adapter",
    "sanitize_rtsp_url",
]
