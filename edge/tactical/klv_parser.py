"""
STANAG 4609 / MISB ST 0601 UAS Datalink Local Set (LDS) KLV Metadata Parser.

Implements military-standard Key-Length-Value (KLV) decoding and encoding
for airborne and stationary border surveillance platforms (UAS / Aerostat / PTZ).

Standards:
- NATO STANAG 4609 (Digital Motion Imagery Standard)
- MISB ST 0601 (UAS Datalink Local Set)
- SMPTE ST 336 (Data Encoding Using Key-Length-Value)
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple


# MISB 0601 16-Byte Universal 16-byte Key for UAS Datalink Local Set
# 06 0E 2B 34 02 0B 01 01 0E 01 03 01 01 00 00 00
MISB_0601_UNIVERSAL_KEY = bytes([
    0x06, 0x0E, 0x2B, 0x34, 0x02, 0x0B, 0x01, 0x01,
    0x0E, 0x01, 0x03, 0x01, 0x01, 0x00, 0x00, 0x00
])

# Standard MISB 0601 Tag IDs
TAG_CHECKSUM = 1               # uint16 (CCITT-16)
TAG_UNIX_TIMESTAMP = 2         # uint64 (microseconds since epoch)
TAG_MISSION_ID = 3             # string
TAG_PLATFORM_TAIL_NUMBER = 4   # string
TAG_PLATFORM_HEADING_ANGLE = 5 # uint16 (0 .. 360 deg)
TAG_PLATFORM_PITCH_ANGLE = 6   # int16 (-20 .. 20 deg)
TAG_PLATFORM_ROLL_ANGLE = 7    # int16 (-50 .. 50 deg)
TAG_SENSOR_LATITUDE = 13       # int32 (-90 .. +90 deg)
TAG_SENSOR_LONGITUDE = 14      # int32 (-180 .. +180 deg)
TAG_SENSOR_ALTITUDE = 15       # uint16 (-900 .. +19000 m HAE)
TAG_SENSOR_HFOV = 16           # uint16 (0 .. 180 deg)
TAG_SENSOR_VFOV = 17           # uint16 (0 .. 180 deg)
TAG_SENSOR_AZIMUTH = 18        # uint32 (0 .. 360 deg)
TAG_SENSOR_ELEVATION = 19      # int32 (-180 .. +180 deg)
TAG_SENSOR_ROLL = 20           # uint32 (0 .. 360 deg)
TAG_UAS_LDS_VERSION = 65       # uint8


@dataclass
class UASMetadata:
    """Decoded UAS telemetry and sensor metadata."""
    timestamp_utc: Optional[datetime] = None
    sensor_latitude: Optional[float] = None
    sensor_longitude: Optional[float] = None
    sensor_altitude_m: Optional[float] = None
    sensor_fov_horizontal: Optional[float] = None
    sensor_fov_vertical: Optional[float] = None
    sensor_azimuth_deg: Optional[float] = None
    sensor_elevation_deg: Optional[float] = None
    sensor_roll_deg: Optional[float] = None
    platform_heading_deg: Optional[float] = None
    mission_id: Optional[str] = None
    platform_tail_number: Optional[str] = None
    version: Optional[int] = 1
    raw_tags: Dict[int, bytes] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert metadata to JSON-serializable dictionary."""
        return {
            "timestamp_utc": self.timestamp_utc.isoformat() if self.timestamp_utc else None,
            "sensor_latitude": round(self.sensor_latitude, 6) if self.sensor_latitude is not None else None,
            "sensor_longitude": round(self.sensor_longitude, 6) if self.sensor_longitude is not None else None,
            "sensor_altitude_m": round(self.sensor_altitude_m, 2) if self.sensor_altitude_m is not None else None,
            "sensor_fov_horizontal": round(self.sensor_fov_horizontal, 2) if self.sensor_fov_horizontal is not None else None,
            "sensor_fov_vertical": round(self.sensor_fov_vertical, 2) if self.sensor_fov_vertical is not None else None,
            "sensor_azimuth_deg": round(self.sensor_azimuth_deg, 2) if self.sensor_azimuth_deg is not None else None,
            "sensor_elevation_deg": round(self.sensor_elevation_deg, 2) if self.sensor_elevation_deg is not None else None,
            "sensor_roll_deg": round(self.sensor_roll_deg, 2) if self.sensor_roll_deg is not None else None,
            "platform_heading_deg": round(self.platform_heading_deg, 2) if self.platform_heading_deg is not None else None,
            "mission_id": self.mission_id,
            "platform_tail_number": self.platform_tail_number,
            "version": self.version,
        }


class KLVParser:
    """STANAG 4609 / MISB ST 0601 KLV packet decoder and encoder."""

    @staticmethod
    def _read_ber_length(data: bytes, offset: int) -> Tuple[int, int]:
        """Parse Basic Encoding Rules (BER) length field.
        Returns: (length, new_offset)
        """
        if offset >= len(data):
            raise ValueError("Unexpected EOF while reading BER length")

        first_byte = data[offset]
        offset += 1

        if first_byte < 0x80:
            # Short form: single byte
            return first_byte, offset

        # Long form: high bit set, lower 7 bits indicate number of bytes
        num_octets = first_byte & 0x7F
        if offset + num_octets > len(data):
            raise ValueError("Truncated BER length field")

        length = 0
        for _ in range(num_octets):
            length = (length << 8) | data[offset]
            offset += 1

        return length, offset

    @staticmethod
    def _write_ber_length(length: int) -> bytes:
        """Encode length into BER octets."""
        if length < 0x80:
            return bytes([length])
        
        # Determine number of octets needed
        octets = []
        val = length
        while val > 0:
            octets.insert(0, val & 0xFF)
            val >>= 8
        
        return bytes([0x80 | len(octets)]) + bytes(octets)

    @classmethod
    def parse_packet(cls, packet: bytes) -> Optional[UASMetadata]:
        """Parse a raw MISB 0601 KLV packet into a UASMetadata structure.
        
        Accepts packets either starting with the 16-byte universal key,
        or bare Local Set tag-length-value streams.
        """
        if not packet or len(packet) < 4:
            return None

        offset = 0

        # Check if 16-byte universal key is present at start
        if len(packet) >= 16 and packet[:16] == MISB_0601_UNIVERSAL_KEY:
            offset = 16
            try:
                set_len, offset = cls._read_ber_length(packet, offset)
                packet_payload = packet[offset:offset + set_len]
            except Exception:
                return None
        else:
            packet_payload = packet

        meta = UASMetadata()
        idx = 0
        payload_len = len(packet_payload)

        try:
            while idx < payload_len:
                tag = packet_payload[idx]
                idx += 1

                if idx >= payload_len:
                    break

                val_len, idx = cls._read_ber_length(packet_payload, idx)
                if idx + val_len > payload_len:
                    break

                val_bytes = packet_payload[idx:idx + val_len]
                idx += val_len
                meta.raw_tags[tag] = val_bytes

                cls._decode_tag(tag, val_bytes, meta)

            return meta
        except Exception:
            return None

    @classmethod
    def _decode_tag(cls, tag: int, val: bytes, meta: UASMetadata) -> None:
        """Decode a specific MISB 0601 tag into UASMetadata attributes."""
        try:
            if tag == TAG_UNIX_TIMESTAMP and len(val) == 8:
                micros = struct.unpack(">Q", val)[0]
                meta.timestamp_utc = datetime.fromtimestamp(micros / 1_000_000.0, tz=timezone.utc)

            elif tag == TAG_MISSION_ID:
                meta.mission_id = val.decode("utf-8", errors="replace").strip("\x00")

            elif tag == TAG_PLATFORM_TAIL_NUMBER:
                meta.platform_tail_number = val.decode("utf-8", errors="replace").strip("\x00")

            elif tag == TAG_PLATFORM_HEADING_ANGLE and len(val) == 2:
                # uint16 (0 .. 2^16-1) -> 0.0 .. 360.0 degrees
                raw = struct.unpack(">H", val)[0]
                meta.platform_heading_deg = (raw / 65535.0) * 360.0

            elif tag == TAG_SENSOR_LATITUDE and len(val) == 4:
                # int32: -2^31 .. +2^31-1 -> -90.0 .. +90.0 degrees
                raw = struct.unpack(">i", val)[0]
                meta.sensor_latitude = (raw / 2147483647.0) * 90.0

            elif tag == TAG_SENSOR_LONGITUDE and len(val) == 4:
                # int32: -2^31 .. +2^31-1 -> -180.0 .. +180.0 degrees
                raw = struct.unpack(">i", val)[0]
                meta.sensor_longitude = (raw / 2147483647.0) * 180.0

            elif tag == TAG_SENSOR_ALTITUDE and len(val) == 2:
                # uint16: 0 .. 65535 -> -900 .. +19000 meters HAE
                raw = struct.unpack(">H", val)[0]
                meta.sensor_altitude_m = (raw / 65535.0) * 19900.0 - 900.0

            elif tag == TAG_SENSOR_HFOV and len(val) == 2:
                # uint16: 0 .. 65535 -> 0.0 .. 180.0 degrees
                raw = struct.unpack(">H", val)[0]
                meta.sensor_fov_horizontal = (raw / 65535.0) * 180.0

            elif tag == TAG_SENSOR_VFOV and len(val) == 2:
                # uint16: 0 .. 65535 -> 0.0 .. 180.0 degrees
                raw = struct.unpack(">H", val)[0]
                meta.sensor_fov_vertical = (raw / 65535.0) * 180.0

            elif tag == TAG_SENSOR_AZIMUTH and len(val) == 4:
                # uint32: 0 .. 2^32-1 -> 0.0 .. 360.0 degrees
                raw = struct.unpack(">I", val)[0]
                meta.sensor_azimuth_deg = (raw / 4294967295.0) * 360.0

            elif tag == TAG_SENSOR_ELEVATION and len(val) == 4:
                # int32: -2^31 .. 2^31-1 -> -180.0 .. +180.0 degrees
                raw = struct.unpack(">i", val)[0]
                meta.sensor_elevation_deg = (raw / 2147483647.0) * 180.0

            elif tag == TAG_SENSOR_ROLL and len(val) == 4:
                # uint32: 0 .. 2^32-1 -> 0.0 .. 360.0 degrees
                raw = struct.unpack(">I", val)[0]
                meta.sensor_roll_deg = (raw / 4294967295.0) * 360.0

            elif tag == TAG_UAS_LDS_VERSION and len(val) == 1:
                meta.version = val[0]
        except Exception:
            pass

    @classmethod
    def encode_packet(cls, meta: UASMetadata, include_universal_key: bool = True) -> bytes:
        """Encode a UASMetadata instance into a compliant MISB 0601 KLV byte string."""
        tags_payload = bytearray()

        # Tag 2: Timestamp (uint64 microseconds)
        if meta.timestamp_utc:
            micros = int(meta.timestamp_utc.timestamp() * 1_000_000)
            val = struct.pack(">Q", micros)
            tags_payload.append(TAG_UNIX_TIMESTAMP)
            tags_payload.extend(cls._write_ber_length(len(val)))
            tags_payload.extend(val)

        # Tag 3: Mission ID
        if meta.mission_id:
            val = meta.mission_id.encode("utf-8")
            tags_payload.append(TAG_MISSION_ID)
            tags_payload.extend(cls._write_ber_length(len(val)))
            tags_payload.extend(val)

        # Tag 4: Platform Tail Number
        if meta.platform_tail_number:
            val = meta.platform_tail_number.encode("utf-8")
            tags_payload.append(TAG_PLATFORM_TAIL_NUMBER)
            tags_payload.extend(cls._write_ber_length(len(val)))
            tags_payload.extend(val)

        # Tag 5: Heading
        if meta.platform_heading_deg is not None:
            heading = max(0.0, min(360.0, meta.platform_heading_deg))
            raw = int((heading / 360.0) * 65535.0)
            val = struct.pack(">H", max(0, min(65535, raw)))
            tags_payload.append(TAG_PLATFORM_HEADING_ANGLE)
            tags_payload.extend(cls._write_ber_length(len(val)))
            tags_payload.extend(val)

        # Tag 13: Latitude
        if meta.sensor_latitude is not None:
            lat = max(-90.0, min(90.0, meta.sensor_latitude))
            raw = int((lat / 90.0) * 2147483647.0)
            val = struct.pack(">i", raw)
            tags_payload.append(TAG_SENSOR_LATITUDE)
            tags_payload.extend(cls._write_ber_length(len(val)))
            tags_payload.extend(val)

        # Tag 14: Longitude
        if meta.sensor_longitude is not None:
            lon = max(-180.0, min(180.0, meta.sensor_longitude))
            raw = int((lon / 180.0) * 2147483647.0)
            val = struct.pack(">i", raw)
            tags_payload.append(TAG_SENSOR_LONGITUDE)
            tags_payload.extend(cls._write_ber_length(len(val)))
            tags_payload.extend(val)

        # Tag 15: Altitude
        if meta.sensor_altitude_m is not None:
            alt = max(-900.0, min(19000.0, meta.sensor_altitude_m))
            raw = int(((alt + 900.0) / 19900.0) * 65535.0)
            val = struct.pack(">H", max(0, min(65535, raw)))
            tags_payload.append(TAG_SENSOR_ALTITUDE)
            tags_payload.extend(cls._write_ber_length(len(val)))
            tags_payload.extend(val)

        # Tag 16: HFOV
        if meta.sensor_fov_horizontal is not None:
            hfov = max(0.0, min(180.0, meta.sensor_fov_horizontal))
            raw = int((hfov / 180.0) * 65535.0)
            val = struct.pack(">H", max(0, min(65535, raw)))
            tags_payload.append(TAG_SENSOR_HFOV)
            tags_payload.extend(cls._write_ber_length(len(val)))
            tags_payload.extend(val)

        # Tag 17: VFOV
        if meta.sensor_fov_vertical is not None:
            vfov = max(0.0, min(180.0, meta.sensor_fov_vertical))
            raw = int((vfov / 180.0) * 65535.0)
            val = struct.pack(">H", max(0, min(65535, raw)))
            tags_payload.append(TAG_SENSOR_VFOV)
            tags_payload.extend(cls._write_ber_length(len(val)))
            tags_payload.extend(val)

        # Tag 18: Azimuth
        if meta.sensor_azimuth_deg is not None:
            az = max(0.0, min(360.0, meta.sensor_azimuth_deg))
            raw = int((az / 360.0) * 4294967295.0)
            val = struct.pack(">I", max(0, min(4294967295, raw)))
            tags_payload.append(TAG_SENSOR_AZIMUTH)
            tags_payload.extend(cls._write_ber_length(len(val)))
            tags_payload.extend(val)

        # Tag 19: Elevation
        if meta.sensor_elevation_deg is not None:
            el = max(-180.0, min(180.0, meta.sensor_elevation_deg))
            raw = int((el / 180.0) * 2147483647.0)
            val = struct.pack(">i", raw)
            tags_payload.append(TAG_SENSOR_ELEVATION)
            tags_payload.extend(cls._write_ber_length(len(val)))
            tags_payload.extend(val)

        # Tag 65: Version
        version_val = meta.version or 1
        tags_payload.append(TAG_UAS_LDS_VERSION)
        tags_payload.extend(cls._write_ber_length(1))
        tags_payload.append(version_val & 0xFF)

        if not include_universal_key:
            return bytes(tags_payload)

        # Wrap with Universal Key and Length
        ber_len = cls._write_ber_length(len(tags_payload))
        return MISB_0601_UNIVERSAL_KEY + ber_len + bytes(tags_payload)
