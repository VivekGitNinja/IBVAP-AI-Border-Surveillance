"""
ONVIF WS-Discovery Network Prober
=================================

Sends WS-Discovery multicast probes (239.255.255.250:3702) to discover
ONVIF-compliant IP surveillance cameras (NVTs) across local network subnets.
"""

from __future__ import annotations
import re
import time
import socket
import logging
import xml.etree.ElementTree as ET
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

WS_DISCOVERY_MULTICAST_GROUP = "239.255.255.250"
WS_DISCOVERY_PORT = 3702

PROBE_XML_TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://www.w3.org/2003/05/soap-envelope"
               xmlns:wsa="http://schemas.xmlsoap.org/ws/2004/08/addressing"
               xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"
               xmlns:dn="http://www.onvif.org/ver10/network/wsdl">
  <soap:Header>
    <wsa:MessageID>uuid:{msg_id}</wsa:MessageID>
    <wsa:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</wsa:To>
    <wsa:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</wsa:Action>
  </soap:Header>
  <soap:Body>
    <d:Probe>
      <d:Types>dn:NetworkVideoTransmitter</d:Types>
    </d:Probe>
  </soap:Body>
</soap:Envelope>"""


def discover_onvif_devices(timeout: float = 1.0) -> List[Dict[str, Any]]:
    """Broadcast WS-Discovery probe and collect responding ONVIF cameras."""
    import uuid
    msg_id = str(uuid.uuid4())
    probe_bytes = PROBE_XML_TEMPLATE.format(msg_id=msg_id).encode("utf-8")

    discovered: List[Dict[str, Any]] = []
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(timeout)

    try:
        # Multicast TTL 2 for LAN traversal
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.sendto(probe_bytes, (WS_DISCOVERY_MULTICAST_GROUP, WS_DISCOVERY_PORT))

        start = time.time()
        while time.time() - start < timeout:
            try:
                data, addr = sock.recvfrom(65535)
                xml_text = data.decode("utf-8", errors="ignore")
                device_info = parse_probe_match(xml_text, addr[0])
                if device_info and not any(d["ip"] == device_info["ip"] for d in discovered):
                    discovered.append(device_info)
            except socket.timeout:
                break
            except Exception:
                pass
    except Exception as e:
        logger.debug(f"WS-Discovery broadcast unavailable: {e}")
    finally:
        sock.close()

    return discovered


def parse_probe_match(xml_str: str, source_ip: str) -> Optional[Dict[str, Any]]:
    """Extract hardware metadata, scopes, and XAddrs from ONVIF ProbeMatch XML."""
    try:
        # Extract XAddrs
        xaddrs_match = re.search(r"<[^>]*XAddrs[^>]*>([^<]+)</", xml_str)
        xaddrs = xaddrs_match.group(1).split() if xaddrs_match else []

        # Extract Scopes
        scopes_match = re.search(r"<[^>]*Scopes[^>]*>([^<]+)</", xml_str)
        scopes = scopes_match.group(1).split() if scopes_match else []

        manufacturer = "Unknown"
        model = "IP Camera"
        device_name = "IP Camera"
        for s in scopes:
            if "onvif://www.onvif.org/hardware/" in s:
                model = s.split("/")[-1]
            elif "onvif://www.onvif.org/manufacturer/" in s:
                manufacturer = s.split("/")[-1]
            elif "onvif://www.onvif.org/name/" in s:
                device_name = s.split("/")[-1]
                if manufacturer == "Unknown":
                    manufacturer = device_name

        return {
            "ip": source_ip,
            "xaddrs": xaddrs,
            "manufacturer": manufacturer,
            "model": model,
            "name": device_name,
            "scopes": scopes,
            "onvif_profile": "Profile S/T",
        }
    except Exception:
        return None
