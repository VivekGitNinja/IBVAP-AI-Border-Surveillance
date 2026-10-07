"""
IBVAP — Operator Feedback Closed-Loop & Dynamic Incident Triage Service
======================================================================
Provides authoritative DB-persisted operator incident dismissal, suppression tracking,
deterministic PostgreSQL advisory transaction locking, and safe sibling triage propagation.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.models.incident import Incident
from backend.app.models.suppression import OperatorSuppression

logger = logging.getLogger(__name__)


class DismissalReason:
    ENVIRONMENT_FALSE_ALARM = "ENVIRONMENT_FALSE_ALARM"
    ANIMAL_WILDLIFE = "ANIMAL_WILDLIFE"
    AUTHORIZED_PERSONNEL = "AUTHORIZED_PERSONNEL"
    BENIGN_ACTIVITY = "BENIGN_ACTIVITY"
    SYSTEM_TESTING = "SYSTEM_TESTING"
    OTHER = "OTHER"


class IncidentDismissIn(BaseModel):
    """Schema for operator incident dismissal request."""
    reason: str = Field(..., description="Categorical dismissal reason")
    duration_seconds: int = Field(300, ge=10, le=86400, description="Suppression duration in seconds (default 5m)")
    notes: str = Field("", description="Optional operator contextual notes")


def derive_advisory_lock_id(canonical_key: str) -> int:
    """Derive deterministic signed 64-bit integer from canonical suppression key for PostgreSQL pg_advisory_xact_lock."""
    digest = hashlib.sha256(canonical_key.encode("utf-8")).digest()[:8]
    return int.from_bytes(digest, byteorder="big", signed=True)


def acquire_advisory_xact_lock(session: Session, lock_id: int) -> None:
    """Acquire transaction-scoped advisory lock on PostgreSQL or serialize on SQLite.

    lock_id MUST be a signed 64-bit integer.
    """
    if not isinstance(lock_id, int) or isinstance(lock_id, bool):
        raise TypeError(f"acquire_advisory_xact_lock requires an int lock_id, got {type(lock_id).__name__}")
    if not (-9223372036854775808 <= lock_id <= 9223372036854775807):
        raise ValueError(f"lock_id {lock_id} is outside signed 64-bit integer range")

    bind = session.get_bind()
    if bind and bind.dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(:lid)"), {"lid": lock_id})
    elif bind and bind.dialect.name == "sqlite":
        try:
            session.execute(text("BEGIN IMMEDIATE"))
        except Exception:
            pass


class SiteFeedbackRegistry:
    """Thread-safe in-memory optimization cache for active operator suppressions.
    
    The database (operator_suppressions table) remains the sole authoritative source of truth.
    """

    def __init__(self, default_ttl_seconds: float = 60.0):
        self._lock = threading.RLock()
        # key -> (expires_at_timestamp, reason)
        self._suppressions: Dict[str, Tuple[float, str]] = {}
        self.default_ttl = default_ttl_seconds

    def is_suppressed(self, key: str) -> bool:
        """Check if suppression key is actively suppressed in memory."""
        now = time.time()
        with self._lock:
            entry = self._suppressions.get(key)
            if not entry:
                return False
            expires_at, _ = entry
            if now >= expires_at:
                del self._suppressions[key]
                return False
            return True

    def get_suppression_reason(self, key: str) -> Optional[str]:
        """Return active suppression reason if present."""
        now = time.time()
        with self._lock:
            entry = self._suppressions.get(key)
            if not entry:
                return None
            expires_at, reason = entry
            if now >= expires_at:
                del self._suppressions[key]
                return None
            return reason

    def record_suppression(
        self,
        keys: List[str],
        expires_at: datetime | float,
        reason: str,
    ) -> None:
        """Update in-memory cache with new active suppressions."""
        if isinstance(expires_at, datetime):
            if expires_at.tzinfo is None:
                exp_ts = time.time() + (expires_at - datetime.utcnow()).total_seconds()
            else:
                exp_ts = expires_at.timestamp()
        else:
            exp_ts = float(expires_at)

        with self._lock:
            for k in keys:
                self._suppressions[k] = (exp_ts, reason)

    def invalidate_keys(self, keys: List[str]) -> None:
        """Invalidate cache entries upon revocation."""
        with self._lock:
            for k in keys:
                self._suppressions.pop(k, None)

    def warm_from_db(self, session: Session) -> int:
        """Warm up in-memory cache from authoritative database upon startup."""
        now = datetime.utcnow()
        try:
            active_records = session.query(OperatorSuppression).filter(
                OperatorSuppression.is_revoked == False,
                OperatorSuppression.expires_at > now,
            ).all()

            count = 0
            with self._lock:
                for rec in active_records:
                    self._suppressions[rec.suppression_key] = (
                        rec.expires_at.timestamp(),
                        rec.dismissal_reason,
                    )
                    count += 1
            logger.info(f"Warmed {count} active suppressions from database.")
            return count
        except Exception as e:
            logger.warning(f"Failed to warm SiteFeedbackRegistry from DB: {e}")
            return 0

    def clear(self) -> None:
        """Clear all in-memory cache entries."""
        with self._lock:
            self._suppressions.clear()

    @classmethod
    def get_instance(cls) -> "SiteFeedbackRegistry":
        return get_site_feedback_registry()

    def register_suppression(
        self,
        target_key: str,
        camera_id: int,
        target_id: Optional[int] = None,
        dossier_id: Optional[str] = None,
        reason: str = "FALSE_POSITIVE",
        expires_at: Optional[datetime] = None,
        zone_type: Optional[str] = None,
        notes: Optional[str] = None,
        created_by: Optional[str] = None,
    ) -> None:
        """Convenience method to register a single suppression in cache."""
        exp = expires_at or (datetime.utcnow() + timedelta(seconds=self.default_ttl))
        self.record_suppression([target_key], exp, reason)

    def invalidate(self, key: str) -> None:
        """Convenience method to invalidate a single suppression key."""
        self.invalidate_keys([key])


# Global Singleton Registry
_GLOBAL_FEEDBACK_REGISTRY: Optional[SiteFeedbackRegistry] = None
_REGISTRY_LOCK = threading.Lock()


def get_site_feedback_registry() -> SiteFeedbackRegistry:
    """Obtain or initialize the site-wide singleton feedback registry."""
    global _GLOBAL_FEEDBACK_REGISTRY
    if _GLOBAL_FEEDBACK_REGISTRY is None:
        with _REGISTRY_LOCK:
            if _GLOBAL_FEEDBACK_REGISTRY is None:
                _GLOBAL_FEEDBACK_REGISTRY = SiteFeedbackRegistry()
    return _GLOBAL_FEEDBACK_REGISTRY


def propagate_sibling_dismissals(
    db: Session,
    parent_inc: Incident,
    reason: str,
    user_id: str,
) -> List[int]:
    """Execute safety-gated sibling incident dismissal matrix.
    
    Guarantees:
    - BUFFER, MONITORED, PUBLIC siblings transition to DISMISSED.
    - RESTRICTED and SENSITIVE siblings are BLOCKED from dismissal.
    """
    sibling_ids: List[int] = []
    if parent_inc.correlated_ids and isinstance(parent_inc.correlated_ids, list):
        sibling_ids.extend([cid for cid in parent_inc.correlated_ids if isinstance(cid, int) and cid != parent_inc.id])

    dossier_id = None
    if isinstance(parent_inc.ai_assessment, dict):
        dossier_id = parent_inc.ai_assessment.get("dossier_id")

    if dossier_id:
        dossier_siblings = db.query(Incident).filter(
            Incident.id != parent_inc.id,
            Incident.status == "OPEN",
        ).all()
        for sib in dossier_siblings:
            if isinstance(sib.ai_assessment, dict) and sib.ai_assessment.get("dossier_id") == dossier_id:
                if sib.id not in sibling_ids:
                    sibling_ids.append(sib.id)

    if not sibling_ids:
        return []

    now = datetime.utcnow()
    propagated: List[int] = []

    siblings = db.query(Incident).filter(Incident.id.in_(sibling_ids)).all()
    for sib in siblings:
        if sib.status == "DISMISSED":
            continue

        sib_zone_type = (sib.zone_name or "").upper()
        if isinstance(sib.ai_assessment, dict) and sib.ai_assessment.get("zone_type"):
            sib_zone_type = sib.ai_assessment["zone_type"].upper()

        if sib_zone_type in ("RESTRICTED", "SENSITIVE"):
            # SAFETY BLOCK: Cannot dismiss an active restricted breach via sibling dismissal
            timeline = list(sib.timeline or [])
            timeline.append({
                "timestamp": now.isoformat(),
                "event_type": "sibling_dismissal_blocked",
                "description": f"Sibling dismissal from {parent_inc.incident_code} BLOCKED due to {sib_zone_type} zone severity.",
                "source": "feedback_engine",
                "confidence": 1.0,
            })
            sib.timeline = timeline
            logger.info(f"Blocked sibling dismissal for incident {sib.id} ({sib.incident_code}) due to {sib_zone_type} severity.")
        else:
            # Propagate dismissal
            sib.status = "DISMISSED"
            sib.closed_at = now
            sib.closed_by = user_id
            timeline = list(sib.timeline or [])
            timeline.append({
                "timestamp": now.isoformat(),
                "event_type": "correlated_dismissal",
                "description": f"Correlated dismissal from {parent_inc.incident_code} (Reason: {reason}) by {user_id}",
                "source": "feedback_engine",
                "confidence": 1.0,
            })
            sib.timeline = timeline
            propagated.append(sib.id)
            logger.info(f"Propagated dismissal to sibling incident {sib.id} ({sib.incident_code}).")

    return propagated


def dismiss_incident(
    db: Session,
    incident_id: int,
    data: IncidentDismissIn,
    user_id: str,
) -> Dict[str, Any]:
    """Execute authoritative operator dismissal with PostgreSQL advisory lock serialization."""
    inc = db.query(Incident).filter(Incident.id == incident_id).with_for_update().first()
    if not inc:
        raise ValueError(f"Incident with ID {incident_id} not found")

    target_id = inc.fingerprint
    camera_id = inc.camera_id
    dossier_id = None
    if isinstance(inc.ai_assessment, dict):
        dossier_id = inc.ai_assessment.get("dossier_id")

    canonical_keys: List[str] = []
    if camera_id is not None and target_id:
        canonical_keys.append(f"camera:{camera_id}:target:{target_id}")
    if dossier_id:
        canonical_keys.append(f"dossier:{dossier_id}")

    if not canonical_keys:
        canonical_keys.append(f"incident:{incident_id}")

    # Acquire PostgreSQL Advisory Transaction Locks in sorted order
    sorted_keys = sorted(canonical_keys)
    for k in sorted_keys:
        lid = derive_advisory_lock_id(k)
        acquire_advisory_xact_lock(db, lid)

    now = datetime.utcnow()
    expires = now + timedelta(seconds=data.duration_seconds)
    zone_type = (inc.zone_name or "BUFFER").upper()
    if isinstance(inc.ai_assessment, dict) and inc.ai_assessment.get("zone_type"):
        zone_type = inc.ai_assessment["zone_type"].upper()

    for k in canonical_keys:
        supp = OperatorSuppression(
            suppression_key=k,
            entity_type="dossier" if "dossier:" in k else "track",
            camera_id=camera_id,
            track_id=int(target_id) if target_id and target_id.isdigit() else None,
            dossier_id=dossier_id,
            incident_id=inc.id,
            dismissal_reason=data.reason,
            operator_notes=data.notes,
            operator_id=user_id,
            initial_zone_type=zone_type,
            created_at=now,
            expires_at=expires,
            is_revoked=False,
        )
        db.add(supp)

    inc.status = "DISMISSED"
    inc.closed_at = now
    inc.closed_by = user_id
    timeline = list(inc.timeline or [])
    timeline.append({
        "timestamp": now.isoformat(),
        "event_type": "operator_dismissal",
        "description": f"Dismissed by {user_id}: {data.reason} (Suppressed for {data.duration_seconds}s)",
        "source": "operator_console",
        "payload": {
            "reason": data.reason,
            "duration_seconds": data.duration_seconds,
            "notes": data.notes,
            "suppression_keys": canonical_keys,
        },
    })
    inc.timeline = timeline

    propagated = propagate_sibling_dismissals(db, inc, data.reason, user_id)
    db.commit()

    # Post-commit: Update in-memory cache
    registry = get_site_feedback_registry()
    registry.record_suppression(canonical_keys, expires, data.reason)

    return {
        "incident_id": inc.id,
        "incident_code": inc.incident_code,
        "status": "DISMISSED",
        "suppression_keys": canonical_keys,
        "expires_at": expires.isoformat(),
        "propagated_sibling_ids": propagated,
    }
