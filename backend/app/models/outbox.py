"""Incident outbox model for transactional outbox pattern."""

from __future__ import annotations
from typing import Optional
from datetime import datetime
import uuid

from sqlalchemy import String, DateTime, Text, Integer, Index, JSON
from sqlalchemy.orm import Mapped, mapped_column
from backend.app.models.base import Base

# Canonical RFC 4122 namespace for IBVAP deterministic UUIDv5 derivation
NAMESPACE_IBVAP = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


def derive_event_id() -> str:
    """Generate unique immutable UUIDv4 string for an outbox event."""
    return str(uuid.uuid4())


def derive_idempotency_key(incident_code: str, event_type: str) -> str:
    """Derive deterministic UUIDv5 string from canonical namespace and identity."""
    return str(uuid.uuid5(NAMESPACE_IBVAP, f"{incident_code}:{event_type}"))


class IncidentOutboxEvent(Base):
    """Authoritative durable outbox for asynchronous incident event dispatch."""
    __tablename__ = "incident_outbox"
    __table_args__ = (
        Index("ix_incident_outbox_claim", "status", "next_retry_at", "lease_until"),
        Index("ix_incident_outbox_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)        # Unique immutable UUIDv4
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)                  # "incident_created", etc.
    incident_code: Mapped[str] = mapped_column(String(40), index=True, nullable=False)   # Human-readable natural key
    idempotency_key: Mapped[str] = mapped_column(String(36), unique=True, nullable=False) # Deterministic UUIDv5
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="PENDING", index=True)       # PENDING, PROCESSING, DELIVERED, FAILED, DEAD_LETTER
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lease_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    worker_id: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    next_retry_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
