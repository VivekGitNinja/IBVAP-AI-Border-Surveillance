"""Operator suppression model for feedback closed-loop."""

from __future__ import annotations
from typing import Optional
from datetime import datetime
from sqlalchemy import String, DateTime, Text, Boolean, Integer, Index
from sqlalchemy.orm import Mapped, mapped_column
from backend.app.models.base import Base


class OperatorSuppression(Base):
    """Authoritative record of operator incident dismissal and alert suppression."""
    __tablename__ = "operator_suppressions"
    __table_args__ = (
        Index("ix_operator_suppressions_key_active", "suppression_key", "is_revoked", "expires_at"),
        Index("ix_operator_suppressions_dossier", "dossier_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    suppression_key: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    entity_type: Mapped[str] = mapped_column(String(20), default="track")  # "track" or "dossier"
    camera_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    track_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    dossier_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    incident_id: Mapped[int] = mapped_column(Integer, nullable=False)
    dismissal_reason: Mapped[str] = mapped_column(String(80), nullable=False)
    operator_notes: Mapped[str] = mapped_column(Text, default="")
    operator_id: Mapped[str] = mapped_column(String(80), default="OPERATOR")
    initial_zone_type: Mapped[str] = mapped_column(String(40), default="BUFFER")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
    is_revoked: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    revocation_reason: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
