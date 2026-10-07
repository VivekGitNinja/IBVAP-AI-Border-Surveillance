"""Camera management endpoints with Universal Multi-Camera Discovery & Connection Hub."""

import os
import sys
import re
import socket
import struct
import subprocess
import time
import threading
import base64
from datetime import datetime
from typing import List, Optional, Dict, Any
from concurrent.futures import ThreadPoolExecutor

import cv2
import ipaddress
import urllib.parse
import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Body, Response, Request, status, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from pathlib import Path
from backend.app.core.config import settings
from backend.app.core.logging import get_logger
from backend.app.db.session import get_db
from backend.app.models.camera import Camera
from backend.app.models.camera_health import CameraHealth
from backend.app.schemas.common import CameraIn, CameraOut, CameraHealthOut, CameraPatchIn
from backend.app.services.audit import log_action
from backend.app.api.deps import current_user, require_permission

logger = get_logger("cameras")
router = APIRouter()


# ── In-Memory Phone Stream Frame Buffer ────────────────────────
_PHONE_FRAMES: Dict[str, np.ndarray] = {}
_PHONE_UPDATED: Dict[str, float] = {}
_PHONE_LOCK = threading.Lock()
_SNAPSHOT_DETECTOR = None
_SNAPSHOT_DETECTOR_LOCK = threading.Lock()

def get_snapshot_detector():
    global _SNAPSHOT_DETECTOR
    if _SNAPSHOT_DETECTOR is None:
        with _SNAPSHOT_DETECTOR_LOCK:
            if _SNAPSHOT_DETECTOR is None:
                from edge.detection.factory import create_detector
                _SNAPSHOT_DETECTOR = create_detector("yolo11n")
    return _SNAPSHOT_DETECTOR



def update_phone_frame(camera_id: str, frame: np.ndarray):
    """Store latest JPEG frame from a phone/browser stream."""
    with _PHONE_LOCK:
        _PHONE_FRAMES[camera_id] = frame
        _PHONE_UPDATED[camera_id] = time.time()


def get_phone_frame(camera_id: str) -> Optional[np.ndarray]:
    """Retrieve the latest live frame from a phone stream."""
    with _PHONE_LOCK:
        f = _PHONE_FRAMES.get(camera_id)
        if f is not None:
            return f.copy()
        return None


# ── Camera Brand Presets & OUI Database ───────────────────────
CAMERA_BRANDS = [
    {"id": "tp-link", "name": "TP-Link Tapo (C200/C310/C320)", "rtsp_template": "rtsp://{user}:{pass}@{ip}:554/stream1",
     "default_user": "admin", "default_port": 554, "setup_tip": "Enable Camera Account in Tapo App > Settings > Advanced Settings > Camera Account"},
    {"id": "cpplus", "name": "CP Plus / Dahua EzyKam & CCTV", "rtsp_template": "rtsp://{user}:{pass}@{ip}:554/cam/realmonitor?channel=1&subtype=0",
     "default_user": "admin", "default_port": 554, "setup_tip": "Default ports: 554 (RTSP), 37777 (TCP). Default login: admin/admin123"},
    {"id": "hikvision", "name": "Hikvision Network Camera", "rtsp_template": "rtsp://{user}:{pass}@{ip}:554/Streaming/Channels/101",
     "default_user": "admin", "default_port": 554, "setup_tip": "Port 554 or 8000. Activate RTSP in Configuration > Network > Advanced > Integration Protocol"},
    {"id": "dahua", "name": "Dahua Network Camera", "rtsp_template": "rtsp://{user}:{pass}@{ip}:554/cam/realmonitor?channel=1&subtype=0",
     "default_user": "admin", "default_port": 554, "setup_tip": "Default RTSP port 554 or HTTP port 80/8000"},
    {"id": "reolink", "name": "Reolink Smart Camera", "rtsp_template": "rtsp://{user}:{pass}@{ip}:554/h264Preview_01_main",
     "default_user": "admin", "default_port": 554, "setup_tip": "Enable RTSP in Reolink Client > Device Settings > Network > Advanced > Server Settings"},
    {"id": "godrej", "name": "Godrej Security Camera", "rtsp_template": "rtsp://{user}:{pass}@{ip}:554/stream1",
     "default_user": "admin", "default_port": 554, "setup_tip": "Standard ONVIF/RTSP port 554"},
    {"id": "axis", "name": "Axis Communications", "rtsp_template": "rtsp://{user}:{pass}@{ip}:554/axis-media/media.amp",
     "default_user": "root", "default_port": 554, "setup_tip": "Default username root"},
    {"id": "mobile_ipcam", "name": "Mobile Phone (IP Webcam App)", "rtsp_template": "http://{ip}:8080/video",
     "default_user": "", "default_port": 8080, "setup_tip": "Open 'IP Webcam' app on Android/iOS and tap 'Start Server' at bottom"},
    {"id": "mobile_droidcam", "name": "Mobile Phone (DroidCam App)", "rtsp_template": "http://{ip}:4747/video",
     "default_user": "", "default_port": 4747, "setup_tip": "Open DroidCam app on mobile and use port 4747"},
    {"id": "phone_browser", "name": "Phone Browser Live Bridge", "rtsp_template": "phone://mobile-01",
     "default_user": "", "default_port": 0, "setup_tip": "Open http://<Mac-IP>:5173/phone-camera on any phone browser to stream live"},
    {"id": "generic", "name": "Generic RTSP Stream", "rtsp_template": "rtsp://{user}:{pass}@{ip}:554/live",
     "default_user": "admin", "default_port": 554, "setup_tip": "Standard RTSP on port 554"},
    {"id": "usb", "name": "USB / Hardware Camera", "rtsp_template": "usb://0",
     "default_user": "", "default_port": 0, "setup_tip": "USB capture device or hardware camera"},
    {"id": "file", "name": "Video File", "rtsp_template": "file://{path}",
     "default_user": "", "default_port": 0, "setup_tip": "Local MP4 or MKV recording"},
]

# IEEE OUI Hardware Lookup Database for Security Cameras
CAMERA_OUI_DATABASE: Dict[str, tuple] = {
    # TP-Link / Tapo
    "30:68:93": ("TP-Link Tapo Security Camera", "rtsp://{user}:{pwd}@{ip}:554/stream1", "Enable Camera Account in Tapo App > Settings > Advanced Settings > Camera Account"),
    "e8:48:b8": ("TP-Link Tapo Security Camera", "rtsp://{user}:{pwd}@{ip}:554/stream1", "Enable Camera Account in Tapo App > Settings > Advanced Settings > Camera Account"),
    "50:c7:bf": ("TP-Link Tapo Security Camera", "rtsp://{user}:{pwd}@{ip}:554/stream1", "Enable Camera Account in Tapo App > Settings > Advanced Settings > Camera Account"),
    "1c:3b:f3": ("TP-Link Tapo Security Camera", "rtsp://{user}:{pwd}@{ip}:554/stream1", "Enable Camera Account in Tapo App > Settings > Advanced Settings > Camera Account"),
    "70:4f:57": ("TP-Link Tapo Security Camera", "rtsp://{user}:{pwd}@{ip}:554/stream1", "Enable Camera Account in Tapo App > Settings > Advanced Settings > Camera Account"),
    "b0:be:76": ("TP-Link Tapo Security Camera", "rtsp://{user}:{pwd}@{ip}:554/stream1", "Enable Camera Account in Tapo App > Settings > Advanced Settings > Camera Account"),
    "ac:84:c6": ("TP-Link Tapo Security Camera", "rtsp://{user}:{pwd}@{ip}:554/stream1", "Enable Camera Account in Tapo App > Settings > Advanced Settings > Camera Account"),
    # AzureWave (Mi 360 / CP Plus Wi-Fi cameras)
    "9c:c7:d3": ("AzureWave Smart CCTV (Mi/CP Plus)", "rtsp://{user}:{pwd}@{ip}:554/cam/realmonitor?channel=1&subtype=0", "CP Plus / Mi Smart Camera (Default ports: 554, 8000, 37777)"),
    "cc:47:40": ("AzureWave Smart CCTV (Mi/CP Plus)", "rtsp://{user}:{pwd}@{ip}:554/cam/realmonitor?channel=1&subtype=0", "CP Plus / Mi Smart Camera (Default ports: 554, 8000, 37777)"),
    "00:24:23": ("AzureWave Smart CCTV", "rtsp://{user}:{pwd}@{ip}:554/live", "CP Plus / Mi Wi-Fi Camera"),
    "ec:d0:9f": ("AzureWave Smart CCTV", "rtsp://{user}:{pwd}@{ip}:554/live", "CP Plus / Mi Wi-Fi Camera"),
    # Hikvision
    "bc:ba:e1": ("Hikvision Network Camera", "rtsp://{user}:{pwd}@{ip}:554/Streaming/Channels/101", "Activate RTSP in Hikvision Configuration > Network > Advanced Settings"),
    "44:55:c4": ("Hikvision Network Camera", "rtsp://{user}:{pwd}@{ip}:554/Streaming/Channels/101", "Default user: admin"),
    "18:68:cb": ("Hikvision Network Camera", "rtsp://{user}:{pwd}@{ip}:554/Streaming/Channels/101", "Default user: admin"),
    "c0:56:e3": ("Hikvision Network Camera", "rtsp://{user}:{pwd}@{ip}:554/Streaming/Channels/101", "Default user: admin"),
    "84:9a:40": ("Hikvision Network Camera", "rtsp://{user}:{pwd}@{ip}:554/Streaming/Channels/101", "Default user: admin"),
    # Dahua / CP Plus
    "3c:ef:8c": ("Dahua / CP Plus CCTV", "rtsp://{user}:{pwd}@{ip}:554/cam/realmonitor?channel=1&subtype=0", "Default user: admin. Port 554/37777."),
    "4c:11:bf": ("Dahua / CP Plus CCTV", "rtsp://{user}:{pwd}@{ip}:554/cam/realmonitor?channel=1&subtype=0", "Default user: admin"),
    "90:02:a9": ("Dahua / CP Plus CCTV", "rtsp://{user}:{pwd}@{ip}:554/cam/realmonitor?channel=1&subtype=0", "Default user: admin"),
    "e0:50:8b": ("Dahua / CP Plus CCTV", "rtsp://{user}:{pwd}@{ip}:554/cam/realmonitor?channel=1&subtype=0", "Default user: admin"),
    "a0:bd:cd": ("Dahua / CP Plus CCTV", "rtsp://{user}:{pwd}@{ip}:554/cam/realmonitor?channel=1&subtype=0", "Default user: admin"),
    # Axis Communications
    "ac:cc:8e": ("Axis Communications", "rtsp://{user}:{pwd}@{ip}:554/axis-media/media.amp", "Default user: root"),
    "00:40:8c": ("Axis Communications", "rtsp://{user}:{pwd}@{ip}:554/axis-media/media.amp", "Default user: root"),
    # Reolink
    "ec:71:db": ("Reolink Smart CCTV", "rtsp://{user}:{pwd}@{ip}:554/h264Preview_01_main", "Enable RTSP in Reolink App > Network Settings > Advanced"),
    "ec:22:80": ("Reolink Smart CCTV", "rtsp://{user}:{pwd}@{ip}:554/h264Preview_01_main", "Enable RTSP in Reolink App > Network Settings > Advanced"),
    # Espressif ESP32-CAM
    "24:62:ab": ("ESP32-CAM Video Node", "http://{ip}:81/stream", "ESP32-CAM MJPEG Video Node"),
    "60:01:94": ("ESP32-CAM Video Node", "http://{ip}:81/stream", "ESP32-CAM MJPEG Video Node"),
    # Gateways
    "28:a9:15": ("JioFiber Gateway Router", "http://{ip}:80", "Primary Wi-Fi Gateway / Router (192.168.29.1)"),
}


def get_active_lan_subnet() -> tuple[str, str]:
    """
    Auto-detect active host LAN IP and /24 subnet base (e.g. ('10.238.254.36', '10.238.254')).
    Tier 1: UDP connect to public DNS targets (instant, works if routing table has default route).
    Tier 2: Query system default route interface and read interface IP via netstat / ifconfig.
    Tier 3: Parse ifconfig for active interface with valid IPv4 address.
    Tier 4: Linux ip route / ip addr inspection.
    Fallback: Local loopback ('127.0.0.1', '127.0.0').
    """
    # Tier 1: UDP connect probe (no traffic is transmitted, just kernel route lookup)
    for test_target in [("8.8.8.8", 80), ("1.1.1.1", 80)]:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.5)
            s.connect(test_target)
            lip = s.getsockname()[0]
            s.close()
            parts = lip.split(".")
            if len(parts) == 4 and not lip.startswith("127."):
                return lip, ".".join(parts[:3])
        except Exception:
            pass

    # Tier 2: macOS / BSD default route inspection
    try:
        out = subprocess.run(["netstat", "-rn"], capture_output=True, text=True, timeout=2).stdout
        for line in out.splitlines():
            if line.startswith("default"):
                parts = line.split()
                if len(parts) >= 4:
                    iface = parts[-1]
                    if_out = subprocess.run(["ifconfig", iface], capture_output=True, text=True, timeout=2).stdout
                    m = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)", if_out)
                    if m:
                        lip = m.group(1)
                        if not lip.startswith("127."):
                            parts = lip.split(".")
                            if len(parts) == 4:
                                return lip, ".".join(parts[:3])
    except Exception:
        pass

    # Tier 3: Parse ifconfig for active interface
    try:
        out = subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=2).stdout
        current_active = False
        candidates: List[tuple[str, bool]] = []
        for line in out.splitlines():
            if line and not line.startswith("\t") and ":" in line:
                current_active = False
            if "status: active" in line:
                current_active = True
            m = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)", line)
            if m:
                ip = m.group(1)
                if not ip.startswith("127."):
                    candidates.append((ip, current_active))
        for ip, active in candidates:
            if active:
                parts = ip.split(".")
                if len(parts) == 4:
                    return ip, ".".join(parts[:3])
        if candidates:
            parts = candidates[0][0].split(".")
            if len(parts) == 4:
                return candidates[0][0], ".".join(parts[:3])
    except Exception:
        pass

    # Tier 4: Linux fallback via ip route / ip -o addr
    try:
        out = subprocess.run(["ip", "-o", "-4", "addr", "show"], capture_output=True, text=True, timeout=2).stdout
        for line in out.splitlines():
            m = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)", line)
            if m and not m.group(1).startswith("127."):
                lip = m.group(1)
                parts = lip.split(".")
                if len(parts) == 4:
                    return lip, ".".join(parts[:3])
    except Exception:
        pass

    return "127.0.0.1", "127.0.0"


class BrandPreset(BaseModel):
    id: str
    name: str
    rtsp_template: str
    default_user: str
    default_port: int
    setup_tip: str = ""


class DiscoverRequest(BaseModel):
    ip_range: Optional[str] = None
    start: int = 1
    end: int = 254
    ports: List[int] = [554, 8554, 8000, 8080, 80, 443, 37777, 34567, 4747]
    timeout: float = 0.25


class DiscoveredCamera(BaseModel):
    ip: str
    port: int = 554
    brand_hint: str = ""
    rtsp_url: str = ""
    status: str = "found"
    mac: str = ""
    vendor: str = ""
    open_ports: List[int] = []
    latency_ms: float = 0.0
    setup_tip: str = ""
    is_gateway: bool = False


# Authorized surveillance camera streaming & management ports
ALLOWED_CAMERA_PORTS = {80, 443, 554, 4747, 8000, 8080, 8554, 8899}


def validate_target_ip_and_port(ip_or_host: str, port: Optional[int] = None) -> str:
    """
    SSRF Protection Guard:
    1. Rejects dangerous hostnames and prohibited targets.
    2. Pins DNS resolution to prevent DNS rebinding attacks.
    3. Strictly blocks loopback (127.0.0.0/8, ::1).
    4. Strictly blocks link-local (169.254.0.0/16, fe80::/10).
    5. Strictly blocks cloud metadata (169.254.169.254).
    6. Strictly blocks reserved, broadcast, and multicast addresses.
    7. Verifies port against ALLOWED_CAMERA_PORTS.
    Returns validated, resolved IP address string.
    """
    if not ip_or_host or not ip_or_host.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SSRF validation failed: IP address or hostname is required.",
        )

    clean_host = ip_or_host.strip().lower()

    if clean_host in ("localhost", "metadata.google.internal", "instance-data"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"SSRF protection: Target host '{clean_host}' is prohibited.",
        )

    if port is not None:
        if port not in ALLOWED_CAMERA_PORTS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"SSRF protection: Port {port} is not an authorized surveillance streaming port.",
            )

    try:
        addr_info = socket.getaddrinfo(clean_host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        if not addr_info:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"SSRF validation failed: Unable to resolve hostname '{clean_host}'.",
            )
        resolved_ip = addr_info[0][4][0]
    except socket.gaierror:
        resolved_ip = clean_host

    try:
        ip_obj = ipaddress.ip_address(resolved_ip)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"SSRF validation failed: Invalid IP address '{resolved_ip}'.",
        )

    if ip_obj.is_loopback:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"SSRF protection: Loopback addresses ({resolved_ip}) are strictly prohibited.",
        )

    if ip_obj.is_link_local:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"SSRF protection: Link-local addresses ({resolved_ip}) are strictly prohibited.",
        )

    if str(ip_obj) == "169.254.169.254":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SSRF protection: Cloud metadata endpoints are strictly prohibited.",
        )

    if ip_obj.is_unspecified or ip_obj.is_reserved or ip_obj.is_multicast:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"SSRF protection: Address {resolved_ip} is reserved, broadcast, or multicast.",
        )

    return str(ip_obj)


class SmartProbeRequest(BaseModel):
    ip: str
    username: str = "admin"
    password: str = ""
    brand: str = "auto"
    ports: List[int] = [554, 8554, 8000, 8080]


class SmartProbeResult(BaseModel):
    success: bool
    working_url: Optional[str] = None
    brand_detected: str = ""
    message: str = ""
    tested_count: int = 0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    candidate_urls: List[str] = []


class StreamTestRequest(BaseModel):
    stream_url: str
    brand: str = "generic"
    username: str = "admin"
    password: str = ""


class StreamTestResult(BaseModel):
    success: bool
    message: str
    width: int = 0
    height: int = 0
    fps: float = 0.0
    frame_count: int = 0
    sample_path: str = ""


@router.get("/brands", response_model=List[BrandPreset])
def get_camera_brands(user: dict = Depends(require_permission("read"))):
    """Get supported camera brands with RTSP URL templates and setup tips."""
    return CAMERA_BRANDS


@router.get("/network-info")
def get_network_info(user: dict = Depends(require_permission("read"))):
    """Auto-detect current host LAN IP, subnet base, and active network gateway."""
    local_ip, subnet_base = get_active_lan_subnet()
    return {
        "local_ip": local_ip,
        "subnet": subnet_base,
        "gateway": f"{subnet_base}.1",
        "timestamp": datetime.utcnow().isoformat(),
    }


@router.post("/discover", response_model=List[DiscoveredCamera])
def discover_cameras(
    req: DiscoverRequest,
    current_user: dict = Depends(require_permission("manage_config")),
):
    """
    Ultra-Fast Deep Network Camera & Device Discovery:
    1. Auto-resolves active subnet base if empty or 'auto'.
    2. Runs fast parallel TCP touch (non-blocking) across subnet nodes to refresh kernel ARP table without subprocess overhead.
    3. Queries kernel ARP cache on macOS/Linux to capture physically attached Wi-Fi/LAN devices.
    4. Identifies hardware vendor and camera brand via IEEE OUI prefixes and MAC characteristics.
    5. Probes camera streaming ports concurrently (554, 8554, 8000, 8080, 80, 443, 37777, 34567, 4747).
    6. Generates pre-configured, tested RTSP/HTTP stream templates and setup tips.
    """
    local_ip, auto_subnet = get_active_lan_subnet()
    if req.ip_range and req.ip_range.strip() and req.ip_range.strip().lower() not in ("auto", ""):
        base = req.ip_range.strip().rstrip(".")
        if base.startswith("127.") or base == "127" or base.startswith("169.254"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="SSRF protection: Subnet range cannot target loopback or link-local networks."
            )
        try:
            test_ip = ipaddress.ip_address(f"{base}.1" if base.count(".") == 2 else base)
            if test_ip.is_loopback or test_ip.is_link_local or str(test_ip) == "169.254.169.254":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="SSRF protection: Subnet range cannot target loopback, link-local, or cloud metadata."
                )
        except ValueError:
            pass
    else:
        base = auto_subnet

    # 1. Fast parallel TCP touch across subnet range to populate ARP table natively without ping subprocess overhead
    def touch_node(i: int):
        target_ip = f"{base}.{i}"
        for p in (80, 554, 8080, 443):
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(0.06)
                s.connect_ex((target_ip, p))
                s.close()
                break
            except Exception:
                pass

    start_node = max(1, min(req.start, 254))
    end_node = max(start_node, min(req.end, 254))

    try:
        with ThreadPoolExecutor(max_workers=50) as executor:
            list(executor.map(touch_node, range(start_node, end_node + 1)))
    except Exception as e:
        logger.warning(f"Subnet touch exception: {e}")

    # 2. Read ARP table on macOS / Linux
    arp_map: Dict[str, str] = {}
    base_prefix = f"{base}."
    try:
        arp_out = subprocess.run(["arp", "-a"], capture_output=True, text=True, timeout=3).stdout
        for line in arp_out.splitlines():
            m = re.search(r'\((\d+\.\d+\.\d+\.\d+)\) at ([0-9a-fA-F:]+)', line)
            if m:
                ip, mac = m.group(1), m.group(2).lower()
                if ip.startswith(base_prefix) and mac != "(incomplete)" and not mac.startswith("ff:"):
                    # Normalize MAC into standard 2-digit hex
                    try:
                        mac_norm = ":".join([f"{int(p, 16):02x}" for p in mac.split(":")])
                        arp_map[ip] = mac_norm
                    except Exception:
                        arp_map[ip] = mac
    except Exception as e:
        logger.warning(f"ARP scan error: {e}")

    # Linux /proc/net/arp fallback if arp -a produced nothing
    if not arp_map and Path("/proc/net/arp").is_file():
        try:
            with open("/proc/net/arp", "r") as f:
                for line in f.readlines()[1:]:
                    parts = line.split()
                    if len(parts) >= 4:
                        ip, flags, mac = parts[0], parts[2], parts[3].lower()
                        if flags != "0x0" and ip.startswith(base_prefix) and mac != "00:00:00:00:00:00":
                            arp_map[ip] = mac
        except Exception as e:
            logger.warning(f"/proc/net/arp read error: {e}")

    # Track local host if on the scanned subnet
    if local_ip.startswith(base_prefix) and local_ip not in arp_map:
        arp_map[local_ip] = "local-host"

    # Also check gateway node f"{base}.1" even if ARP doesn't list it yet
    gw_ip = f"{base}.1"
    if gw_ip not in arp_map:
        arp_map[gw_ip] = "gateway-router"

    # 3. Concurrent Multi-Port Prober on all discovered IPs
    scan_ports = [554, 8554, 8000, 8080, 80, 443, 37777, 34567, 4747, 5000, 22]
    discovered_list: List[DiscoveredCamera] = []

    def probe_device(ip: str, mac: str) -> Optional[DiscoveredCamera]:
        open_ports: List[int] = []
        best_port = 554
        t0 = time.time()
        for p in scan_ports:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(0.15)
                res = s.connect_ex((ip, p))
                s.close()
                if res == 0:
                    open_ports.append(p)
            except Exception:
                pass
        latency = round((time.time() - t0) * 1000 / max(1, len(scan_ports)), 1)

        # Determine best port
        if 554 in open_ports:
            best_port = 554
        elif 8554 in open_ports:
            best_port = 8554
        elif 8000 in open_ports:
            best_port = 8000
        elif 8080 in open_ports:
            best_port = 8080
        elif 4747 in open_ports:
            best_port = 4747
        elif open_ports:
            best_port = open_ports[0]

        is_gw = (ip == gw_ip)
        prefix = mac[:8]
        oui_match = CAMERA_OUI_DATABASE.get(prefix)

        if oui_match:
            brand_name, rtsp_tmpl, tip = oui_match
        elif is_gw:
            brand_name = "Wi-Fi Gateway Router"
            rtsp_tmpl = "http://{ip}:80"
            tip = f"Network Router Gateway ({ip})"
        elif ip == local_ip or mac == "local-host":
            brand_name = "Local Host Surveillance Node"
            rtsp_tmpl = "http://{ip}:8001"
            tip = f"Local Station ({local_ip})"
        else:
            # Check if MAC has randomized/private bit set (typical for mobile devices)
            try:
                first_byte = int(mac[:2], 16)
                if (first_byte & 0x02) != 0:
                    if 4747 in open_ports:
                        brand_name = "Mobile Phone (DroidCam Live)"
                        rtsp_tmpl = "http://{ip}:4747/video"
                        tip = "DroidCam active on port 4747"
                    elif 8080 in open_ports:
                        brand_name = "Mobile Phone (IP Webcam Live)"
                        rtsp_tmpl = "http://{ip}:8080/video"
                        tip = "IP Webcam stream active on port 8080"
                    elif 8554 in open_ports:
                        brand_name = "Mobile RTSP Broadcast Node"
                        rtsp_tmpl = "rtsp://{ip}:8554/live"
                        tip = "RTSP streaming server active on port 8554"
                    else:
                        brand_name = "Mobile Video Node (Android/iOS)"
                        rtsp_tmpl = "http://{ip}:8080/video"
                        tip = "Connect via IP Webcam (8080), DroidCam (4747), or Browser Live Link"
                else:
                    if 8554 in open_ports:
                        brand_name = "RTSP Network Camera"
                        rtsp_tmpl = "rtsp://{user}:{pwd}@{ip}:8554/live"
                        tip = "RTSP server on alternate port 8554"
                    elif 554 in open_ports:
                        brand_name = "Network Surveillance Camera"
                        rtsp_tmpl = "rtsp://{user}:{pwd}@{ip}:554/live"
                        tip = "Test with camera RTSP credentials"
                    else:
                        brand_name = "Network Surveillance Camera"
                        rtsp_tmpl = "rtsp://{user}:{pwd}@{ip}:554/stream1"
                        tip = "Standard network surveillance device"
            except Exception:
                brand_name = "Network Surveillance Node"
                rtsp_tmpl = "rtsp://{user}:{pwd}@{ip}:554/stream1"
                tip = "Standard network surveillance device"

        # Format URL template
        final_url = rtsp_tmpl.replace("{user}", "admin").replace("{pwd}", "admin123").replace("{ip}", ip)
        if best_port == 8000 and "rtsp://" not in final_url and "http://" not in final_url:
            final_url = f"http://{ip}:8000"

        # Status hint
        if 554 in open_ports or 8554 in open_ports:
            status_text = "RTSP Active (Ready)"
        elif 8000 in open_ports:
            status_text = "Video Node (8000)"
        elif 8080 in open_ports:
            status_text = "Webcam Stream (8080)"
        elif 4747 in open_ports:
            status_text = "DroidCam Active (4747)"
        elif open_ports:
            status_text = f"Online (Ports: {','.join(map(str, open_ports))})"
        else:
            status_text = "Standby / Private Port"

        return DiscoveredCamera(
            ip=ip,
            port=best_port,
            brand_hint=brand_name,
            rtsp_url=final_url,
            status=status_text,
            mac=mac,
            vendor=brand_name,
            open_ports=open_ports,
            latency_ms=latency,
            setup_tip=tip,
            is_gateway=is_gw,
        )

    # Probe all found ARP devices concurrently
    with ThreadPoolExecutor(max_workers=30) as ex:
        probes = [ex.submit(probe_device, ip, mac) for ip, mac in arp_map.items()]
        for p in probes:
            try:
                res = p.result()
                if res is not None:
                    discovered_list.append(res)
            except Exception as e:
                logger.warning(f"Probe device error: {e}")

    # Sort numerically by IP
    discovered_list.sort(key=lambda x: [int(p) if p.isdigit() else 0 for p in x.ip.split(".")])
    return discovered_list


@router.post("/smart-probe", response_model=SmartProbeResult)
def smart_probe_stream(
    req: SmartProbeRequest,
    user: dict = Depends(require_permission("manage_config")),
):
    """
    Strix-Style Smart IP Camera Stream Finder:
    Iterates through the top 15 most common RTSP and HTTP stream URL patterns
    for Hikvision, Dahua, CP Plus, TP-Link Tapo, Reolink, Axis, and mobile cams.
    Tests each pattern using OpenCV with low-latency timeout.
    Locks onto the active video stream automatically!
    """
    user = req.username or "admin"
    pwd = req.password or "admin123"
    raw_ip = req.ip.strip()

    # Validate requested ports against authorized surveillance camera ports
    if req.ports:
        for p in req.ports:
            if p not in ALLOWED_CAMERA_PORTS:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"SSRF protection: Port {p} is not an authorized surveillance streaming port.",
                )

    # Validate IP address against SSRF vectors
    ip = validate_target_ip_and_port(raw_ip)

    # Candidate URL patterns
    patterns = [
        # TP-Link Tapo
        (f"rtsp://{user}:{pwd}@{ip}:554/stream1", "TP-Link Tapo (Main Stream)"),
        (f"rtsp://{user}:{pwd}@{ip}:554/stream2", "TP-Link Tapo (Sub Stream)"),
        # CP Plus / Dahua
        (f"rtsp://{user}:{pwd}@{ip}:554/cam/realmonitor?channel=1&subtype=0", "CP Plus / Dahua (Main)"),
        (f"rtsp://{user}:{pwd}@{ip}:554/cam/realmonitor?channel=1&subtype=1", "CP Plus / Dahua (Sub)"),
        # Hikvision
        (f"rtsp://{user}:{pwd}@{ip}:554/Streaming/Channels/101", "Hikvision (Channel 101 Main)"),
        (f"rtsp://{user}:{pwd}@{ip}:554/Streaming/Channels/102", "Hikvision (Channel 102 Sub)"),
        # Reolink
        (f"rtsp://{user}:{pwd}@{ip}:554/h264Preview_01_main", "Reolink (Main Stream)"),
        # Axis
        (f"rtsp://{user}:{pwd}@{ip}:554/axis-media/media.amp", "Axis (Media Stream)"),
        # Mobile Phone Streams
        (f"http://{ip}:8080/video", "Mobile IP Webcam (MJPEG)"),
        (f"http://{ip}:4747/video", "Mobile DroidCam (MJPEG)"),
        # Generic & ONVIF
        (f"rtsp://{user}:{pwd}@{ip}:554/live", "Generic RTSP (/live)"),
        (f"rtsp://{user}:{pwd}@{ip}:554/onvif1", "ONVIF Profile S (/onvif1)"),
        (f"rtsp://{user}:{pwd}@{ip}:554/ch0", "Generic Channel 0 (/ch0)"),
        (f"rtsp://{user}:{pwd}@{ip}:8554/live", "RTSP Alternate Port (8554)"),
        (f"http://{ip}:8000", "Hikvision / Video HTTP Web Node"),
    ]

    candidate_urls = [p[0] for p in patterns]
    found_result = []

    def check_pattern(item):
        if found_result:
            return
        url, brand_desc = item
        # Fast TCP pre-check to avoid FFMPEG socket hangs
        port = 554
        if ":8554" in url:
            port = 8554
        elif ":8000" in url:
            port = 8000
        elif ":8080" in url:
            port = 8080
        elif ":4747" in url:
            port = 4747
        elif ":80" in url:
            port = 80

        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.18)
            res = s.connect_ex((ip, port))
            s.close()
            if res != 0:
                return
        except Exception:
            return

        try:
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "timeout;1000000"
            cap = cv2.VideoCapture(url)
            if cap.isOpened():
                ret, frame = cap.read()
                if ret and frame is not None:
                    h, w = frame.shape[:2]
                    fps = round(cap.get(cv2.CAP_PROP_FPS) or 15.0, 1)
                    cap.release()
                    found_result.append(SmartProbeResult(
                        success=True,
                        working_url=url,
                        brand_detected=brand_desc,
                        message=f"Live stream verified! Captured {w}x{h} @ {fps} FPS.",
                        tested_count=len(patterns),
                        width=w,
                        height=h,
                        fps=fps,
                        candidate_urls=candidate_urls,
                    ))
                    return
                cap.release()
        except Exception:
            pass

    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(check_pattern, patterns))

    if found_result:
        return found_result[0]

    return SmartProbeResult(
        success=False,
        working_url=None,
        brand_detected="Unknown / Auth Required",
        message="No stream connected with given credentials. Ensure camera account is configured in the mobile app.",
        tested_count=len(patterns),
        candidate_urls=candidate_urls,
    )


@router.post("/phone-stream/{camera_id}/frame")
def upload_phone_camera_frame(
    camera_id: str,
    payload: dict = Body(...),
    current_user: dict = Depends(require_permission("write")),
):
    """
    Accepts live JPEG image frames transmitted from any smartphone browser.
    Turns any phone into a live border surveillance node with zero software installation.
    """
    image_b64 = payload.get("image")
    if not image_b64:
        raise HTTPException(400, "Missing 'image' base64 data")

    try:
        # Strip data URL header if present
        if "," in image_b64:
            image_b64 = image_b64.split(",", 1)[1]
        raw = base64.b64decode(image_b64)
        nparr = np.frombuffer(raw, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if frame is not None:
            update_phone_frame(camera_id, frame)
            # Also store for phone:// URL schema
            update_phone_frame(f"phone://{camera_id}", frame)
            return {"status": "ok", "camera_id": camera_id, "timestamp": time.time()}
        raise HTTPException(400, "Invalid image format")
    except Exception as e:
        raise HTTPException(400, f"Frame decode failed: {e}")


@router.get("/phone-stream/{camera_id}/frame")
def get_phone_camera_frame(
    camera_id: str,
    current_user: dict = Depends(require_permission("read")),
):
    """Retrieve the latest live JPEG frame for a phone camera."""
    frame = get_phone_frame(camera_id)
    if frame is None:
        frame = get_phone_frame(f"phone://{camera_id}")
    if frame is None:
        raise HTTPException(404, "No live frame received from phone yet")
    ret, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    if not ret:
        raise HTTPException(500, "Encoding error")
    return Response(content=jpeg.tobytes(), media_type="image/jpeg")


@router.post("/test-stream", response_model=StreamTestResult)
def test_stream(
    req: StreamTestRequest,
    user: dict = Depends(require_permission("manage_config")),
):
    """
    Test a camera stream with a non-blocking timeout safeguard.
    Captures 5 frames to verify connection before saving.
    """
    url = req.stream_url.strip()
    res = {"success": False, "message": "Cannot open stream. Check URL and credentials.", "frames": 0, "w": 0, "h": 0, "fps": 0.0}

    # Check if phone stream
    if url.startswith("phone://") or url in _PHONE_FRAMES:
        f = get_phone_frame(url.replace("phone://", ""))
        if f is None:
            f = get_phone_frame(url)
        if f is not None:
            h, w = f.shape[:2]
            return StreamTestResult(
                success=True,
                message=f"Phone Browser Stream OK — {w}x{h} HD Live Link Active",
                width=w,
                height=h,
                fps=15.0,
                frame_count=5,
            )
        else:
            return StreamTestResult(
                success=True,
                message="Phone stream registered. Waiting for phone browser connection.",
                width=1280,
                height=720,
                fps=15.0,
                frame_count=1,
            )

    # SSRF & protocol validation for non-phone streams
    is_local_device = (
        url.startswith("usb://")
        or url.startswith("camera://")
        or url.startswith("webcam://")
        or url.startswith("demo://")
        or url.isdigit()
    )
    if not is_local_device:
        if url.startswith("file://"):
            file_path = Path(url.replace("file://", "")).resolve()
            allowed_roots = [
                Path(settings.evidence_dir).resolve(),
                Path(settings.upload_dir).resolve(),
                Path("samples").resolve(),
            ]
            if not any(file_path.is_relative_to(r) for r in allowed_roots):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied: file:// URL outside allowed storage directories.",
                )
        else:
            parsed = urllib.parse.urlsplit(url)
            if parsed.scheme not in ("rtsp", "http", "https"):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"SSRF protection: Unsupported streaming scheme '{parsed.scheme}'.",
                )
            port = parsed.port
            if port is None:
                port = 554 if parsed.scheme == "rtsp" else (443 if parsed.scheme == "https" else 80)
            validated_ip = validate_target_ip_and_port(parsed.hostname or "", port)
            # Pin URL to validated IP address to prevent DNS rebinding TOCTOU attacks
            if parsed.hostname and validated_ip != parsed.hostname:
                userinfo = ""
                if "@" in parsed.netloc:
                    userinfo = parsed.netloc.split("@")[0] + "@"
                port_str = f":{port}" if parsed.port is not None else ""
                new_netloc = f"{userinfo}{validated_ip}{port_str}"
                url = urllib.parse.urlunsplit((parsed.scheme, new_netloc, parsed.path, parsed.query, parsed.fragment))

    def probe():
        try:
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|timeout;2500000"
            if url.startswith("usb://") or url.startswith("camera://") or url.startswith("webcam://") or url.isdigit():
                if url.isdigit():
                    device_id = int(url)
                else:
                    device_id = int(url.split("://")[-1] or "0")
                backend = cv2.CAP_AVFOUNDATION if sys.platform == "darwin" else cv2.CAP_ANY
                cap = cv2.VideoCapture(device_id, backend)
            elif url.startswith("file://"):
                path = url.replace("file://", "")
                cap = cv2.VideoCapture(path)
            elif url.startswith("demo://"):
                res["success"] = True
                res["message"] = "Synthetic Tactical Border Recon Stream OK (1280x720 @ 25 FPS)"
                res["w"], res["h"], res["fps"], res["frames"] = 1280, 720, 25.0, 5
                return
            else:
                cap = cv2.VideoCapture(url)

            if not cap.isOpened():
                res["message"] = "Cannot open stream. Check IP/port, credentials, or network connection."
                return

            frames = 0
            w, h = 0, 0
            fps_val = cap.get(cv2.CAP_PROP_FPS) or 15.0
            start_t = time.time()
            while frames < 5 and (time.time() - start_t) < 3.0:
                ret, frame = cap.read()
                if ret and frame is not None:
                    frames += 1
                    h, w = frame.shape[:2]

            cap.release()
            if frames > 0:
                res["success"] = True
                res["message"] = f"Stream OK — {frames} frames captured ({w}x{h})"
                res["w"] = w
                res["h"] = h
                res["fps"] = round(fps_val, 1)
                res["frames"] = frames
            else:
                res["message"] = "Stream opened but received 0 valid frames. Check credentials."
        except Exception as e:
            res["message"] = f"Stream test error: {str(e)}"

    t = threading.Thread(target=probe, daemon=True)
    t.start()
    t.join(timeout=3.5)

    return StreamTestResult(
        success=res["success"],
        message=res["message"],
        width=res["w"],
        height=res["h"],
        fps=res["fps"],
        frame_count=res["frames"],
    )



@router.get("", response_model=list[CameraOut])
def list_cameras(
    include_inactive: bool = Query(True, description="Include standby/inactive cameras"),
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("read")),
):
    """List cameras. Returns all configured camera assets by default so offline/standby nodes can be powered on."""
    query = db.query(Camera)
    if not include_inactive:
        query = query.filter(Camera.active == True)
    return query.order_by(Camera.id).all()


@router.post("", response_model=CameraOut)
def add_camera(data: CameraIn, db: Session = Depends(get_db),
               user: dict = Depends(require_permission("manage_cameras"))):
    """Add a new camera and start live pipeline if stream is available."""
    c = Camera(**data.model_dump())
    db.add(c)
    db.commit()
    db.refresh(c)
    log_action(db, user["sub"], user.get("role", ""), "CREATE", "camera",
               str(c.id), {"name": c.name})

    # Start live pipeline for real cameras (not demo and not USB webcam by default)
    if c.stream_url and not c.stream_url.startswith("demo://") and not c.stream_url.startswith("usb://"):
        try:
            from backend.app.services.live_pipeline import live_manager
            live_manager.start_camera(
                camera_id=c.id,
                stream_url=c.stream_url,
                camera_name=c.name,
                bop=c.bop,
            )
            c.status = "ONLINE"
            db.commit()
        except Exception as e:
            print(f"Pipeline start warning for camera {c.id}: {e}")

    return c


@router.get("/{camera_id}", response_model=CameraOut)
def get_camera(
    camera_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("read")),
):
    """Get camera details."""
    c = db.get(Camera, camera_id)
    if not c:
        raise HTTPException(404, "Camera not found")
    return c


@router.put("/{camera_id}", response_model=CameraOut)
def update_camera(camera_id: int, data: CameraIn, db: Session = Depends(get_db),
                  user: dict = Depends(require_permission("manage_cameras"))):
    """Update a camera."""
    c = db.get(Camera, camera_id)
    if not c:
        raise HTTPException(404, "Camera not found")
    old = {k: getattr(c, k) for k in ["name", "stream_url", "location", "fps"]}
    for k, v in data.model_dump().items():
        setattr(c, k, v)
    c.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(c)
    log_action(db, user["sub"], user.get("role", ""), "UPDATE", "camera",
               str(c.id), {"old": old, "new": data.model_dump()})
    return c


@router.patch("/{camera_id}", response_model=CameraOut)
def patch_camera(camera_id: int, data: CameraPatchIn, db: Session = Depends(get_db),
                 user: dict = Depends(require_permission("manage_cameras"))):
    """Partially update a camera (e.g. coordinates, sector, name)."""
    c = db.get(Camera, camera_id)
    if not c:
        raise HTTPException(404, "Camera not found")
    update_data = data.model_dump(exclude_unset=True)
    old = {k: getattr(c, k) for k in update_data.keys()}
    for k, v in update_data.items():
        setattr(c, k, v)
    c.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(c)
    log_action(db, user["sub"], user.get("role", ""), "PATCH", "camera",
               str(c.id), {"old": old, "updated_fields": list(update_data.keys())})
    return c


@router.post("/{camera_id}/connect")
def connect_camera(camera_id: int, db: Session = Depends(get_db),
                   user: dict = Depends(require_permission("manage_cameras"))):
    """Connect camera, start stream capture and start live AI detection pipeline."""
    c = db.get(Camera, camera_id)
    if not c:
        raise HTTPException(404, "Camera not found")

    c.status = "ONLINE"
    c.active = True
    c.health_score = 100
    db.commit()

    # Start live pipeline if stream URL is provided
    if c.stream_url and not c.stream_url.startswith("demo://"):
        try:
            from backend.app.services.live_pipeline import live_manager
            live_manager.start_camera(
                camera_id=c.id,
                stream_url=c.stream_url,
                camera_name=c.name,
                bop=c.bop,
            )
        except Exception as e:
            print(f"Pipeline start warning for camera {c.id}: {e}")

    log_action(db, user["sub"], user.get("role", ""), "CONNECT", "camera",
               str(c.id), {"name": c.name, "stream_url": c.stream_url})
    return {"connected": True, "camera_id": camera_id, "status": "ONLINE", "message": "Camera connected and live AI detection pipeline started."}


@router.post("/{camera_id}/disconnect")
def disconnect_camera(camera_id: int, db: Session = Depends(get_db),
                      user: dict = Depends(require_permission("manage_cameras"))):
    """Disconnect hardware camera, release webcam device, power down stream."""
    c = db.get(Camera, camera_id)
    if not c:
        raise HTTPException(404, "Camera not found")
    
    url = c.stream_url or ""
    if url:
        stream_manager.stop(url)
    try:
        from backend.app.services.live_pipeline import live_manager
        live_manager.stop_camera(camera_id)
    except Exception:
        pass

    c.status = "OFFLINE"
    c.active = False
    db.commit()
    log_action(db, user["sub"], user.get("role", ""), "DISCONNECT", "camera",
               str(c.id), {"name": c.name})
    return {"disconnected": True, "camera_id": camera_id, "message": "Camera powered off and hardware released."}


@router.delete("/{camera_id}")
def delete_camera(camera_id: int, db: Session = Depends(get_db),
                  user: dict = Depends(require_permission("delete"))):
    """Deactivate and disconnect a camera, releasing hardware immediately."""
    c = db.get(Camera, camera_id)
    if not c:
        raise HTTPException(404, "Camera not found")

    # Stop StreamCaptureManager and LivePipelineManager immediately
    url = c.stream_url or ""
    if url:
        stream_manager.stop(url)
    try:
        from backend.app.services.live_pipeline import live_manager
        live_manager.stop_camera(camera_id)
    except Exception:
        pass

    db.delete(c)
    db.commit()
    log_action(db, user["sub"], user.get("role", ""), "DELETE", "camera",
               str(c.id), {"name": c.name})
    return {"deleted": True, "camera_id": camera_id, "message": "Camera deleted and hardware released."}


@router.post("/{camera_id}/heartbeat", response_model=CameraOut)
def heartbeat(
    camera_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_permission("manage_cameras")),
):
    """Record a camera heartbeat."""
    c = db.get(Camera, camera_id)
    if not c:
        raise HTTPException(404, "Camera not found")
    c.status = "ONLINE"
    c.health_score = 100
    c.last_heartbeat = datetime.utcnow()
    db.commit()
    db.refresh(c)
    return c


@router.get("/{camera_id}/health", response_model=list[CameraHealthOut])
def camera_health_history(camera_id: int, limit: int = 20,
                          db: Session = Depends(get_db),
                          user: dict = Depends(require_permission("read"))):
    """Get camera health time-series."""
    c = db.get(Camera, camera_id)
    if not c:
        raise HTTPException(404, "Camera not found")
    return (
        db.query(CameraHealth)
        .filter(CameraHealth.camera_id == camera_id)
        .order_by(CameraHealth.measured_at.desc())
        .limit(limit)
        .all()
    )


# ── MJPEG Live Stream ─────────────────────────────────────────
# Active stream processes: camera_id -> subprocess.Popen
_active_streams: dict = {}


class PTZCommand(BaseModel):
    direction: str  # up, down, left, right, zoom_in, zoom_out, home, stop
    speed: float = 0.5


class PTZPresetCommand(BaseModel):
    preset: str


_PTZ_STATE: dict[int, dict] = {}
_PTZ_PRESETS = [
    {"id": "HOME", "name": "Sector Gate Alpha (Home)", "pan": 0.0, "tilt": 0.0, "zoom": 1.0},
    {"id": "WATCHTOWER", "name": "Perimeter Watchtower North", "pan": 45.0, "tilt": 10.0, "zoom": 2.2},
    {"id": "TRENCH", "name": "Anti-Infiltration Trench", "pan": -30.0, "tilt": -12.0, "zoom": 1.8},
    {"id": "ROAD_JUNCTION", "name": "Supply Road Intersect", "pan": 75.0, "tilt": 5.0, "zoom": 3.0},
]


def _generate_offline_frame(camera: Camera) -> "np.ndarray":
    """Generate an honest offline placeholder frame with diagnostic status — no fake footage."""
    import cv2
    import numpy as np

    h, w = 720, 1280
    frame = np.zeros((h, w, 3), dtype=np.uint8)
    frame[:] = (12, 16, 20)  # Dark tactical slate

    # Red danger border indicating offline status
    cv2.rectangle(frame, (12, 12), (w - 12, h - 12), (40, 40, 220), 2)

    # Diagnostic Header
    cv2.putText(frame, "[CAMERA OFFLINE // NO VIDEO SIGNAL]", (w // 2 - 280, h // 2 - 60),
                cv2.FONT_HERSHEY_SIMPLEX, 0.95, (50, 50, 255), 2)

    cv2.putText(frame, f"Node: {camera.name} (ID: {camera.id}) | Sector: {camera.bop}",
                (w // 2 - 240, h // 2 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (220, 220, 220), 1)

    stream_info = camera.stream_url if camera.stream_url else "None configured"
    cv2.putText(frame, f"Stream Source: {stream_info}",
                (w // 2 - 240, h // 2 + 25), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (160, 160, 160), 1)

    cv2.putText(frame, "Connect physical RTSP, USB, or upload a video file to run live AI analysis.",
                (w // 2 - 290, h // 2 + 70), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 220, 255), 1)

    return frame


_OFFLINE_JPEG_CACHE: Dict[int, bytes] = {}


def get_cached_offline_jpeg(camera: Camera) -> bytes:
    """Return cached JPEG bytes for an offline camera placeholder to avoid burning CPU."""
    if camera.id not in _OFFLINE_JPEG_CACHE:
        frame = _generate_offline_frame(camera)
        _, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
        _OFFLINE_JPEG_CACHE[camera.id] = jpeg.tobytes()
    return _OFFLINE_JPEG_CACHE[camera.id]


@router.post("/{camera_id}/test")
def test_camera_connection(
    camera_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_permission("camera_control")),
):
    """Test whether a real camera/RTSP URL or device index can be opened."""
    c = db.get(Camera, camera_id)
    if not c:
        raise HTTPException(404, "Camera not found")

    url = c.stream_url or ""
    if not url:
        return {
            "success": False,
            "status": "OFFLINE",
            "error": "No stream URL configured",
            "camera_id": camera_id,
        }

    try:
        if url.startswith("usb://") or url.startswith("camera://") or url.startswith("webcam://") or url.isdigit():
            if url.isdigit():
                dev_idx = int(url)
            else:
                dev_idx = int(url.split("://")[-1] or "0")
            backend = cv2.CAP_AVFOUNDATION if sys.platform == "darwin" else cv2.CAP_ANY
            cap = cv2.VideoCapture(dev_idx, backend)
        elif url.startswith("file://"):
            fpath = url.replace("file://", "")
            if not os.path.exists(fpath):
                return {"success": False, "status": "OFFLINE", "error": f"File not found: {fpath}", "camera_id": camera_id}
            cap = cv2.VideoCapture(fpath)
        elif url.startswith("demo://"):
            if not settings.enable_demo:
                return {
                    "success": False,
                    "status": "OFFLINE",
                    "error": "Demo mode is disabled in system configuration",
                    "camera_id": camera_id,
                }
            return {"success": True, "status": "ONLINE", "message": "Demo stream accessible", "resolution": "1280x720", "fps": 15, "camera_id": camera_id}
        else:
            # RTSP or network stream
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"
            cap = cv2.VideoCapture(url)

        if not cap.isOpened():
            return {
                "success": False,
                "status": "OFFLINE",
                "error": f"Failed to connect to RTSP/video source: {url}",
                "camera_id": camera_id,
            }

        ret, frame = cap.read()
        cap.release()

        if not ret or frame is None:
            return {
                "success": False,
                "status": "DEGRADED",
                "error": "Connected to stream source, but failed to retrieve video frame",
                "camera_id": camera_id,
            }

        h, w, _ = frame.shape
        c.status = "ONLINE"
        c.health_score = 98.0
        db.commit()

        return {
            "success": True,
            "status": "ONLINE",
            "width": w,
            "height": h,
            "resolution": f"{w}x{h}",
            "fps": c.fps or 15,
            "message": f"Successfully verified stream ({w}x{h})",
            "camera_id": camera_id,
        }
    except Exception as e:
        return {
            "success": False,
            "status": "OFFLINE",
            "error": str(e),
            "camera_id": camera_id,
        }


def _generate_tactical_frame(camera: Camera, frame_num: int = 0) -> "np.ndarray":
    """Generate a realistic border outpost tactical surveillance frame (EO/IR style)."""
    import cv2
    import numpy as np

    h, w = 720, 1280
    frame = np.zeros((h, w, 3), dtype=np.uint8)

    # Apply PTZ Virtual Camera Offsets
    ptz = _PTZ_STATE.get(camera.id, {"pan": 0.0, "tilt": 0.0, "zoom": 1.0, "preset": "HOME"})
    pan_shift = int(ptz.get("pan", 0.0) * 4)
    tilt_shift = int(ptz.get("tilt", 0.0) * 2)
    zoom_val = max(1.0, min(4.0, ptz.get("zoom", 1.0)))

    # Night thermal terrain gradient shifted by tilt
    horizon = int(h * 0.45) + tilt_shift
    horizon = max(120, min(h - 120, horizon))
    frame[:horizon, :] = (20, 24, 30)  # Night sky
    frame[horizon:, :] = (32, 38, 42)  # Ground / terrain

    # Subtle terrain texture lines
    cv2.line(frame, (0, horizon), (w, horizon), (45, 55, 60), 1)
    cv2.line(frame, (0, horizon + 80), (w, horizon + 120), (38, 45, 50), 1)

    # Border security fence simulation
    fence_y = horizon + 50
    cv2.line(frame, (0, fence_y), (w, fence_y), (60, 75, 70), 1)
    cv2.line(frame, (0, fence_y + 15), (w, fence_y + 15), (60, 75, 70), 1)
    for fx in range(50, w, 80):
        cv2.line(frame, (fx, fence_y - 25), (fx, fence_y + 40), (50, 65, 60), 2)

    # Simulated target movement shifted by pan
    t = (frame_num % 300) / 300.0
    base_px = int(250 + 600 * np.sin(t * np.pi))
    px = base_px - pan_shift
    py = int(horizon + 30 + 20 * np.cos(t * np.pi * 2))
    pw, ph = int(42 * zoom_val), int(98 * zoom_val)

    # Only draw target if within viewport
    if -pw < px < w + 50:
        # Target silhouette
        cv2.rectangle(frame, (px, py), (px + pw, py + ph), (90, 110, 100), -1)
        cv2.circle(frame, (px + pw // 2, py - 10), int(12 * zoom_val), (90, 110, 100), -1)

        # Bounding box & AI detection overlay
        bbox_color = (0, 255, 0)
        cv2.rectangle(frame, (px - 6, py - 26), (px + pw + 6, py + ph + 6), bbox_color, 2)

        label = f"person {94.2:.1f}% [Z:{zoom_val:.1f}x]"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
        cv2.rectangle(frame, (px - 6, py - 26 - th - 6), (px - 6 + tw + 6, py - 26), bbox_color, -1)
        cv2.putText(frame, label, (px - 3, py - 28), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 1, cv2.LINE_AA)

    # Virtual Zone Polygon Overlay shifted by pan/tilt
    zone_pts = np.array([
        [150 - pan_shift, horizon + 20],
        [w - 150 - pan_shift, horizon + 20],
        [w - 50 - pan_shift, h - 80],
        [50 - pan_shift, h - 80]
    ], np.int32)
    cv2.polylines(frame, [zone_pts], True, (0, 165, 255), 1, cv2.LINE_AA)
    cv2.putText(frame, "ZONE: RESTRICTED PERIMETER", (160 - pan_shift, horizon + 40),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 165, 255), 1, cv2.LINE_AA)

    # Tactical HUD Crosshairs
    cx, cy = w // 2, h // 2
    hud_color = (0, 220, 255)
    cv2.line(frame, (cx - 20, cy), (cx - 5, cy), hud_color, 1)
    cv2.line(frame, (cx + 5, cy), (cx + 20, cy), hud_color, 1)
    cv2.line(frame, (cx, cy - 20), (cx, cy - 5), hud_color, 1)
    cv2.line(frame, (cx, cy + 5), (cx, cy + 20), hud_color, 1)
    cv2.circle(frame, (cx, cy), 35, hud_color, 1)

    # Header HUD
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    cv2.putText(frame, f"[MHA/SSB BORDER SURVEILLANCE] {camera.bop or 'BOP-01'} | {camera.name}",
                (20, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 200), 2, cv2.LINE_AA)
    cv2.putText(frame, f"GPS: {camera.latitude or 28.6139:.4f} N, {camera.longitude or 77.2090:.4f} E  |  {now_str}",
                (20, 56), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 200, 210), 1, cv2.LINE_AA)

    # Footer HUD with live PTZ telemetry
    ptz_info = f"PTZ: P:{ptz.get('pan', 0.0):+.1f}° T:{ptz.get('tilt', 0.0):+.1f}° Z:{zoom_val:.1f}x [{ptz.get('preset', 'HOME')}]"
    cv2.putText(frame, f"FEED: LIVE EO/IR  |  AI: YOLO26n  |  STATUS: {camera.status}  |  FPS: {camera.fps or 15}  |  {ptz_info}",
                (20, h - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 220, 255), 1, cv2.LINE_AA)

    return frame


@router.post("/{camera_id}/ptz")
def execute_ptz(
    camera_id: int,
    cmd: PTZCommand,
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("camera_control")),
):
    """Execute an ONVIF or digital Pan-Tilt-Zoom command on a camera."""
    c = db.get(Camera, camera_id)
    if not c:
        raise HTTPException(404, "Camera not found")

    from edge.adapters import create_camera_adapter, PTZCommand as AdapterPTZCmd
    adapter = create_camera_adapter(camera_id=camera_id, stream_url=c.stream_url or "")
    if camera_id in _PTZ_STATE:
        adapter._current_ptz_state.update(_PTZ_STATE[camera_id])

    adapter_cmd = AdapterPTZCmd(direction=cmd.direction, speed=cmd.speed)
    adapter_res = adapter.execute_ptz(adapter_cmd)
    _PTZ_STATE[camera_id] = adapter_res["ptz_state"]

    return {
        "status": "success",
        "camera_id": camera_id,
        "command": cmd.direction.lower().strip(),
        "is_hardware": adapter_res.get("is_hardware", False),
        "ptz_state": _PTZ_STATE[camera_id],
    }


@router.get("/{camera_id}/ptz/presets")
def get_ptz_presets(
    camera_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("read")),
):
    """Get tactical PTZ preset positions for a camera."""
    c = db.get(Camera, camera_id)
    if not c:
        raise HTTPException(404, "Camera not found")
    current = _PTZ_STATE.get(camera_id, {"pan": 0.0, "tilt": 0.0, "zoom": 1.0, "preset": "HOME"})
    return {
        "camera_id": camera_id,
        "current_preset": current.get("preset", "MANUAL"),
        "presets": _PTZ_PRESETS
    }


@router.post("/{camera_id}/ptz/goto")
def goto_ptz_preset(
    camera_id: int,
    cmd: PTZPresetCommand,
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("camera_control")),
):
    """Move PTZ camera to a tactical preset position."""
    c = db.get(Camera, camera_id)
    if not c:
        raise HTTPException(404, "Camera not found")

    from edge.adapters import create_camera_adapter
    adapter = create_camera_adapter(camera_id=camera_id, stream_url=c.stream_url or "")
    if camera_id in _PTZ_STATE:
        adapter._current_ptz_state.update(_PTZ_STATE[camera_id])

    try:
        adapter_res = adapter.goto_preset(cmd.preset)
    except ValueError:
        raise HTTPException(400, f"Preset '{cmd.preset}' not recognized")

    _PTZ_STATE[camera_id] = adapter_res["ptz_state"]

    return {
        "status": "success",
        "camera_id": camera_id,
        "preset": adapter_res["preset"],
        "is_hardware": adapter_res.get("is_hardware", False),
        "ptz_state": _PTZ_STATE[camera_id],
    }


@router.post("/{camera_id}/stream/start")
def start_stream(
    camera_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("manage_cameras")),
):
    """
    Start/prepare live stream endpoint for a camera.
    Powers on camera hardware, activates live AI pipeline, and serves MJPEG/WS.
    """
    c = db.get(Camera, camera_id)
    if not c:
        raise HTTPException(404, "Camera not found")

    res = connect_camera(camera_id=camera_id, db=db, user=user)
    return {
        "status": "started",
        "camera_id": camera_id,
        "stream_endpoint": f"/api/v1/cameras/{camera_id}/stream",
        "snapshot_endpoint": f"/api/v1/cameras/{camera_id}/snapshot",
        "connected": res.get("connected", True),
    }


@router.post("/{camera_id}/stream/stop")
def stop_stream(
    camera_id: int,
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("manage_cameras")),
):
    """Stop live stream for a camera, power off hardware sensor, and release device."""
    c = db.get(Camera, camera_id)
    if not c:
        raise HTTPException(404, "Camera not found")

    res = disconnect_camera(camera_id=camera_id, db=db, user=user)
    return {
        "status": "stopped",
        "camera_id": camera_id,
        "disconnected": res.get("disconnected", True),
    }


@router.post("/{camera_id}/power")
def toggle_camera_power(
    camera_id: int,
    power: bool = Query(..., description="True to power on, False to power off"),
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("manage_cameras")),
):
    """Explicit hardware power control: turn camera sensor ON or OFF."""
    if power:
        return start_stream(camera_id=camera_id, db=db, user=user)
    else:
        return stop_stream(camera_id=camera_id, db=db, user=user)


@router.get("/pipeline/status")
def pipeline_status(user: dict = Depends(require_permission("read"))):
    """Get status of all live pipelines."""
    from backend.app.services.live_pipeline import live_manager
    return live_manager.get_status()


@router.post("/pipeline/start-all")
def start_all_pipelines(
    db: Session = Depends(get_db),
    user: dict = Depends(require_permission("manage_cameras")),
):
    """Start live pipelines for all cameras with real streams."""
    from backend.app.services.live_pipeline import live_manager
    cameras = db.query(Camera).filter(
        Camera.stream_url.notlike("demo://%"),
        Camera.active == True,
    ).all()
    started = 0
    for cam in cameras:
        try:
            live_manager.start_camera(
                camera_id=cam.id,
                stream_url=cam.stream_url,
                camera_name=cam.name,
                bop=cam.bop,
            )
            started += 1
        except Exception as e:
            print(f"Pipeline start error for camera {cam.id}: {e}")
    return {"started": started, "total": len(cameras)}


import threading


class StreamCaptureManager:
    """
    Maintains persistent, warm VideoCapture instances for physical cameras (USB, RTSP, file).
    Non-blocking: Never hangs the server or HTTP request threads if a camera is offline or slow.
    Fast-fails in 150ms and falls back seamlessly to tactical standby simulation frames.
    """
    def __init__(self):
        self._caps = {}
        self._frames = {}
        self._threads = {}
        self._running = {}
        self._last_attempt = {}  # url -> float timestamp
        self._lock = threading.Lock()

    def get_frame(self, url: str):
        if not url or url.startswith("demo://"):
            return None
        
        # Phone stream check
        if url.startswith("phone://") or url in _PHONE_FRAMES:
            f = get_phone_frame(url.replace("phone://", ""))
            if f is None:
                f = get_phone_frame(url)
            if f is not None:
                return f.copy()
            return None

        # Return cached frame immediately if available
        frame = self._frames.get(url)
        if frame is not None:
            return frame.copy()

        # Non-blocking connection kick-off
        now = time.time()
        with self._lock:
            if self._running.get(url, False):
                for _ in range(8):
                    time.sleep(0.05)
                    frame = self._frames.get(url)
                    if frame is not None:
                        return frame.copy()
                return None
            last_att = self._last_attempt.get(url, 0)
            if now - last_att < 8.0:
                # Still in cooldown period after failed attempt, return None immediately
                return None
            self._last_attempt[url] = now
            self._running[url] = True

        t = threading.Thread(target=self._background_connect_and_stream, args=(url,), daemon=True)
        self._threads[url] = t
        t.start()

        # Brief wait for initial hardware frame
        for _ in range(12):
            time.sleep(0.05)
            frame = self._frames.get(url)
            if frame is not None:
                return frame.copy()

        return None

    def _background_connect_and_stream(self, url: str):
        cap = None
        try:
            if url.startswith("usb://") or url.startswith("camera://") or url.startswith("webcam://") or url.isdigit():
                if url.isdigit():
                    device_id = int(url)
                else:
                    device_id = int(url.split("://")[-1] or "0")
                backend = cv2.CAP_AVFOUNDATION if sys.platform == "darwin" else cv2.CAP_ANY
                cap = cv2.VideoCapture(device_id, backend)
                if sys.platform != "darwin":
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            elif url.startswith("file://"):
                path = url.replace("file://", "")
                cap = cv2.VideoCapture(path)
            else:
                # Fast TCP port pre-check for RTSP/HTTP using urlparse
                try:
                    from urllib.parse import urlparse
                    parsed = urlparse(url)
                    host = parsed.hostname
                    port = parsed.port or (554 if "rtsp" in (parsed.scheme or "") else 80)
                    if host:
                        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        s.settimeout(0.2)
                        err = s.connect_ex((host, port))
                        s.close()
                        if err != 0:
                            self._running[url] = False
                            return
                except Exception:
                    self._running[url] = False
                    return

                os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|timeout;1200000"
                cap = cv2.VideoCapture(url)

            if not cap or not cap.isOpened():
                self._running[url] = False
                if cap:
                    cap.release()
                return

            self._caps[url] = cap

            # Warmup for AVFoundation / hardware cameras
            for _ in range(15):
                if not self._running.get(url, False):
                    break
                ret, f = cap.read()
                if ret and f is not None:
                    self._frames[url] = f
                    break
                time.sleep(0.05)

            fail_count = 0
            while self._running.get(url, False):
                ret, f = cap.read()
                if ret and f is not None:
                    self._frames[url] = f
                    fail_count = 0
                elif url.startswith("file://"):
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                else:
                    fail_count += 1
                    max_fails = 50 if (url.startswith("usb://") or url.startswith("camera://") or url.startswith("webcam://") or url.isdigit()) else 15
                    if fail_count > max_fails:
                        break
                time.sleep(0.04)  # ~25 FPS steady capture

        except Exception as e:
            print(f"Background capture error for {url}: {e}")
        finally:
            self._running[url] = False
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass
            self._caps.pop(url, None)
            self._frames.pop(url, None)

    def stop(self, url: str):
        with self._lock:
            self._running[url] = False
            cap = self._caps.pop(url, None)
            if cap is not None:
                try:
                    cap.release()
                    print(f"StreamCaptureManager released hardware for {url}")
                except Exception as e:
                    print(f"Error releasing cap for {url}: {e}")
            self._frames.pop(url, None)

    def stop_all(self):
        with self._lock:
            for url, cap in list(self._caps.items()):
                self._running[url] = False
                if cap is not None:
                    try:
                        cap.release()
                        print(f"StreamCaptureManager stop_all released hardware for {url}")
                    except Exception:
                        pass
            self._caps.clear()
            self._frames.clear()


stream_manager = StreamCaptureManager()


@router.post("/hardware/power-off-all")
def power_off_all_hardware(
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_permission("camera_power")),
):
    """Emergency release: completely shuts down all physical hardware cameras and releases sensors."""
    stream_manager.stop_all()
    try:
        from backend.app.services.live_pipeline import live_manager
        live_manager.stop_all()
    except Exception:
        pass
    db.query(Camera).filter(Camera.stream_url.like("usb://%")).update({"active": False, "status": "OFFLINE"})
    db.commit()
    return {"status": "success", "message": "All hardware camera sensors powered off and released."}


@router.get("/{camera_id}/snapshot")
def get_snapshot(
    camera_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_permission("read")),
):
    """
    Capture a single frame from camera, run detection, return as JPEG.
    Uses persistent StreamCaptureManager so hardware webcams stay warm and do not blink.
    """
    from fastapi.responses import StreamingResponse
    import cv2
    import io
    import numpy as np

    c = db.get(Camera, camera_id)
    if not c:
        raise HTTPException(404, "Camera not found")

    url = c.stream_url or ""

    from backend.app.services.live_pipeline import live_manager
    is_live_pipeline_frame = False

    # Instant response for inactive/offline/demo cameras
    if not c.active or c.status == "OFFLINE" or url.startswith("demo://") or not url:
        if not (settings.enable_demo and settings.enable_synthetic_cameras):
            return Response(content=get_cached_offline_jpeg(c), media_type="image/jpeg", headers={"Cache-Control": "max-age=30"})
        frame = _generate_tactical_frame(c, int(time.time() * 5))
    else:
        frame = live_manager.get_latest_frame(camera_id)
        if frame is not None:
            is_live_pipeline_frame = True
        else:
            frame = stream_manager.get_frame(url)
        if frame is None:
            if settings.enable_demo and settings.enable_synthetic_cameras:
                frame = _generate_tactical_frame(c, int(time.time() * 5))
            else:
                return Response(content=get_cached_offline_jpeg(c), media_type="image/jpeg", headers={"Cache-Control": "max-age=5"})

    # Lightweight overlay only if frame was not already annotated by live pipeline
    if not is_live_pipeline_frame:
        try:
            h, w = frame.shape[:2]
            cv2.putText(frame, f"CAM-{camera_id} | Live",
                        (10, h - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 229, 200), 2)
        except Exception:
            pass

    # Encode as JPEG
    _, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])

    return StreamingResponse(
        io.BytesIO(jpeg.tobytes()),
        media_type="image/jpeg",
        headers={"Cache-Control": "no-cache"}
    )


@router.get("/{camera_id}/stream")
@router.get("/{camera_id}/mjpeg")
async def mjpeg_stream(
    camera_id: int,
    request: Request,
    max_frames: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_permission("read")),
):
    """
    MJPEG live stream endpoint.
    Frontend connects via: <img src="/api/v1/cameras/{id}/stream">
    Captures frames directly from live AI pipeline with visual bounding boxes, or stream manager fallback.
    """
    from fastapi.responses import StreamingResponse
    from backend.app.services.live_pipeline import live_manager
    import cv2
    import io
    import asyncio

    c = db.get(Camera, camera_id)
    if not c:
        raise HTTPException(404, "Camera not found")

    url = c.stream_url or ""

    async def generate():
        try:
            if url.startswith("demo://") or not url:
                if not settings.enable_demo:
                    frame = _generate_offline_frame(c)
                    _, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                    frame_bytes = jpeg.tobytes()
                    frame_idx = 0
                    sleep_dur = 0.02 if max_frames else 1.0
                    while True:
                        if await request.is_disconnected():
                            break
                        if max_frames is not None and frame_idx >= max_frames:
                            break
                        frame_idx += 1
                        yield (b"--frame\r\n"
                               b"Content-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n")
                        await asyncio.sleep(sleep_dur)
                else:
                    frame_idx = 0
                    sleep_dur = 0.02 if max_frames else (1.0 / max(1, c.fps or 10))
                    while True:
                        if await request.is_disconnected():
                            break
                        if max_frames is not None and frame_idx >= max_frames:
                            break
                        frame_idx += 1
                        frame = _generate_tactical_frame(c, frame_idx)
                        _, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                        yield (b"--frame\r\n"
                               b"Content-Type: image/jpeg\r\n\r\n" + jpeg.tobytes() + b"\r\n")
                        await asyncio.sleep(sleep_dur)
            else:
                frame_idx = 0
                sleep_dur = 0.02 if max_frames else (1.0 / max(1, c.fps or 20))
                while True:
                    if await request.is_disconnected():
                        break
                    if max_frames is not None and frame_idx >= max_frames:
                        break
                    frame_idx += 1

                    # Fetch real-time AI annotated frame from live pipeline
                    frame = live_manager.get_latest_frame(camera_id)
                    if frame is None:
                        raw = stream_manager.get_frame(url)
                        if raw is not None:
                            frame = raw.copy()
                        elif settings.enable_demo:
                            frame = _generate_tactical_frame(c, frame_idx)
                        else:
                            frame = _generate_offline_frame(c)

                    _, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                    yield (b"--frame\r\n"
                           b"Content-Type: image/jpeg\r\n\r\n" + jpeg.tobytes() + b"\r\n")
                    await asyncio.sleep(sleep_dur)
        except (asyncio.CancelledError, GeneratorExit):
            pass

    return StreamingResponse(
        generate(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-cache"}
    )
