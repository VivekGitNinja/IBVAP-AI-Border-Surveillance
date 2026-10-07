"""
Tactical C4ISR interoperability modules.

Supports:
- Cursor-on-Target (CoT) MIL-STD / ATAK gateway
- STANAG 4609 / MISB ST 0601 KLV telemetry parser
"""

from edge.tactical.cot_gateway import CoTEvent, CoTGateway
from edge.tactical.klv_parser import KLVParser, UASMetadata

__all__ = [
    "CoTEvent",
    "CoTGateway",
    "KLVParser",
    "UASMetadata",
]
