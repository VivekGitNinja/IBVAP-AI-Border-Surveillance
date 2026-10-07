"""
Tactical Cursor-on-Target (CoT) & ATAK / WinTAK Integration Gateway
===================================================================

Generates and broadcasts MIL-STD-2525 / CoT XML event streams for real-time
tactical situation awareness on military handhelds (ATAK, WinTAK, TAK Server).
"""

from __future__ import annotations
import time
import socket
import logging
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


# Standard MIL-STD CoT Types for Border Security
COT_TYPE_HOSTILE_PERSON = "a-h-G-U-C"      # Hostile Ground Unit Combatant / Intruder
COT_TYPE_UNKNOWN_GROUND = "a-u-G"          # Unknown Ground Target
COT_TYPE_HOSTILE_VEHICLE = "a-h-G-E-V"    # Hostile Ground Vehicle
COT_TYPE_FRIENDLY_SENTRY = "a-f-G-U"      # Friendly Border Patrol / BOP Sentry
COT_TYPE_SENSOR_POI = "b-m-p-s-p-i"        # Sensor Detection Point of Interest


@dataclass
class CoTEvent:
    """Standardized Cursor-on-Target situational awareness event."""
    uid: str
    cot_type: str
    latitude: float
    longitude: float
    altitude_hae_m: float = 0.0
    circular_error_m: float = 5.0
    linear_error_m: float = 5.0
    callsign: str = "INTRUDER"
    threat_score: float = 75.0
    priority: str = "HIGH"
    remarks: str = "Perimeter boundary breach detected by IBVAP edge sensor"
    stale_duration_sec: float = 120.0
    how: str = "m-g"                       # Machine Generated (Video Analytics)

    def to_xml(self) -> str:
        """Serialize event into standard CoT XML schema."""
        now = datetime.now(timezone.utc)
        stale = now + timedelta(seconds=self.stale_duration_sec)

        time_now = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        time_stale = stale.strftime("%Y-%m-%dT%H:%M:%SZ")

        xml = (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<event version="2.0" uid="{self.uid}" type="{self.cot_type}" how="{self.how}" '
            f'time="{time_now}" start="{time_now}" stale="{time_stale}">\n'
            f'  <point lat="{self.latitude:.7f}" lon="{self.longitude:.7f}" '
            f'hae="{self.altitude_hae_m:.2f}" ce="{self.circular_error_m:.1f}" le="{self.linear_error_m:.1f}"/>\n'
            f'  <detail>\n'
            f'    <contact callsign="{self.callsign}"/>\n'
            f'    <remarks>{self.remarks}</remarks>\n'
            f'    <threat score="{self.threat_score:.1f}" priority="{self.priority}"/>\n'
            f'    <track speed="0.0" course="0.0"/>\n'
            f'    <precisionlocation geopointsrc="IBVAP-AI-GEOREGISTERED"/>\n'
            f'  </detail>\n'
            f'</event>'
        )
        return xml

    def to_bytes(self) -> bytes:
        return self.to_xml().encode("utf-8")


class CoTGateway:
    """Dispatches CoT intrusion markers to tactical networks and ATAK mesh nodes."""

    def __init__(
        self,
        multicast_ip: str = "239.2.3.1",
        multicast_port: int = 6969,
        unicast_target: Optional[str] = None,
        enabled: bool = True,
    ):
        self.multicast_ip = multicast_ip
        self.multicast_port = multicast_port
        self.unicast_target = unicast_target
        self.enabled = enabled
        self._sock: Optional[socket.socket] = None

    def _get_socket(self) -> socket.socket:
        if self._sock is None:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            # Set TTL for local/multicast router hops
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            self._sock = sock
        return self._sock

    def dispatch_incident_cot(
        self,
        incident_code: str,
        target_lat: float,
        target_lon: float,
        object_type: str = "person",
        threat_score: float = 85.0,
        priority: str = "CRITICAL",
        camera_name: str = "Sector Alpha Sentry",
    ) -> CoTEvent:
        """Create and transmit a CoT intrusion event over the tactical network."""
        cot_type = COT_TYPE_HOSTILE_VEHICLE if object_type in ("car", "truck", "vehicle") else COT_TYPE_HOSTILE_PERSON
        callsign = f"INTRUDER-{object_type.upper()}-{incident_code[-6:]}"
        remarks = f"BREACH at {camera_name} (RPS Threat: {threat_score:.0f}, Priority: {priority})"

        event = CoTEvent(
            uid=f"IBVAP.{incident_code}",
            cot_type=cot_type,
            latitude=target_lat,
            longitude=target_lon,
            callsign=callsign,
            threat_score=threat_score,
            priority=priority,
            remarks=remarks,
        )

        if self.enabled:
            payload = event.to_bytes()
            try:
                sock = self._get_socket()
                # Broadcast over tactical mesh multicast
                sock.sendto(payload, (self.multicast_ip, self.multicast_port))
                if self.unicast_target:
                    host, port = self.unicast_target.split(":")
                    sock.sendto(payload, (host, int(port)))
                logger.debug(f"CoTGateway: Transmitted event {event.uid} to {self.multicast_ip}:{self.multicast_port}")
            except Exception as e:
                logger.warning(f"CoTGateway: Broadcast failed: {e}")

        return event

    def close(self):
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
