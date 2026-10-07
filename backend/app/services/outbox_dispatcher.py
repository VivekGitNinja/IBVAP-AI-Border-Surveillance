"""
Incident Outbox Dispatcher Service — Asynchronous Durable Event Delivery
========================================================================
Consumes committed rows from the `incident_outbox` table using PostgreSQL
`SELECT ... FOR UPDATE SKIP LOCKED` claim/lease semantics, delivers events
to external consumers (WebSockets, C2), and performs lease-fenced status completions.

CRITICAL INVARIANTS:
1. No database locks or transactions are held across external network operations.
2. The claim transaction strictly commits status = 'PROCESSING' with worker lease before network call.
3. Completion transitions (DELIVERED, FAILED, DEAD_LETTER) are strictly lease-fenced:
   WHERE id = :id AND worker_id = :worker_id AND lease_until >= :now
4. If a completion UPDATE affects 0 rows, the worker is deemed stale/zombie and aborts without modifying newer state.
5. Canonical identity (event_id, idempotency_key, incident_code) is preserved identically across retries.
"""

from __future__ import annotations

import os
import uuid
import time
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple, Callable

import sqlalchemy as sa
from sqlalchemy.orm import Session

from backend.app.core.config import settings
from backend.app.db.session import SessionLocal
from backend.app.models.outbox import IncidentOutboxEvent

logger = logging.getLogger(__name__)

DEFAULT_LEASE_DURATION_SEC = 30.0
DEFAULT_POLL_INTERVAL_SEC = 1.0
DEFAULT_BATCH_SIZE = 50
DEFAULT_MAX_RETRIES = 10
DEFAULT_BASE_BACKOFF_SEC = 2.0
DEFAULT_MAX_BACKOFF_SEC = 300.0


@dataclass(frozen=True)
class ClaimedEventSnapshot:
    """Immutable in-memory snapshot of an outbox event claimed by a worker."""
    id: int
    event_id: str
    event_type: str
    incident_code: str
    idempotency_key: str
    payload: Dict[str, Any]
    retry_count: int
    worker_id: str
    lease_until: datetime


class OutboxTelemetry:
    """Thread-safe counters for outbox dispatcher operations."""

    def __init__(self):
        self._lock = threading.Lock()
        self.claim_attempts = 0
        self.claim_successes = 0
        self.claim_conflicts = 0
        self.deliveries = 0
        self.delivery_failures = 0
        self.retry_count = 0
        self.dead_letters = 0
        self.lease_expirations = 0
        self.zombie_completion_rejections = 0

    def snapshot(self) -> Dict[str, int]:
        with self._lock:
            return {
                "outbox_claim_attempts": self.claim_attempts,
                "outbox_claim_successes": self.claim_successes,
                "outbox_claim_conflicts": self.claim_conflicts,
                "outbox_deliveries": self.deliveries,
                "outbox_delivery_failures": self.delivery_failures,
                "outbox_retry_count": self.retry_count,
                "outbox_dead_letters": self.dead_letters,
                "outbox_lease_expirations": self.lease_expirations,
                "outbox_zombie_completion_rejections": self.zombie_completion_rejections,
                "outbox_stale_worker_rejections": self.zombie_completion_rejections,
            }


class IncidentOutboxDispatcher:
    """Durable asynchronous outbox dispatcher implementing claim/lease and fenced completion."""

    def __init__(
        self,
        session_factory: Optional[Callable[[], Session]] = None,
        worker_id: Optional[str] = None,
        lease_duration_sec: float = DEFAULT_LEASE_DURATION_SEC,
        poll_interval_sec: float = DEFAULT_POLL_INTERVAL_SEC,
        batch_size: int = DEFAULT_BATCH_SIZE,
        max_retries: int = DEFAULT_MAX_RETRIES,
        base_backoff_sec: float = DEFAULT_BASE_BACKOFF_SEC,
        max_backoff_sec: float = DEFAULT_MAX_BACKOFF_SEC,
        event_broadcaster: Optional[Callable[[Dict[str, Any]], Any]] = None,
        c2_sender: Optional[Callable[[Dict[str, Any]], bool]] = None,
    ):
        self._session_factory = session_factory or SessionLocal
        self.worker_id = worker_id or f"outbox-worker-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self.lease_duration_sec = lease_duration_sec
        self.poll_interval_sec = poll_interval_sec
        self.batch_size = batch_size
        self.max_retries = max_retries
        self.base_backoff_sec = base_backoff_sec
        self.max_backoff_sec = max_backoff_sec
        self._event_broadcaster = event_broadcaster
        self._c2_sender = c2_sender

        self.telemetry = OutboxTelemetry()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def set_event_broadcaster(self, broadcaster: Callable[[Dict[str, Any]], Any]) -> None:
        """Inject or update the external event broadcaster (e.g., WebSocket bridge)."""
        self._event_broadcaster = broadcaster

    def set_c2_sender(self, sender: Callable[[Dict[str, Any]], bool]) -> None:
        """Inject or update C2 outbound sender."""
        self._c2_sender = sender

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE A: CLAIM TRANSACTION (< 2ms, NO NETWORK I/O)
    # ─────────────────────────────────────────────────────────────────────────

    def _claim_batch(self, db: Session, now_utc: datetime) -> List[ClaimedEventSnapshot]:
        """Claim up to batch_size eligible outbox records under a short-lived DB transaction.

        Eligible rows:
        - status IN ('PENDING', 'FAILED') AND next_retry_at <= now_utc
        - status = 'PROCESSING' AND lease_until < now_utc (expired lease recovery)
        """
        query = db.query(IncidentOutboxEvent).filter(
            sa.or_(
                sa.and_(
                    IncidentOutboxEvent.status.in_(["PENDING", "FAILED"]),
                    IncidentOutboxEvent.next_retry_at <= now_utc,
                ),
                sa.and_(
                    IncidentOutboxEvent.status == "PROCESSING",
                    IncidentOutboxEvent.lease_until < now_utc,
                ),
            )
        ).order_by(IncidentOutboxEvent.created_at.asc()).limit(self.batch_size)

        bind = db.get_bind()
        if bind and bind.dialect.name == "postgresql":
            query = query.with_for_update(skip_locked=True)
        elif bind and bind.dialect.name == "sqlite":
            try:
                db.execute(sa.text("BEGIN IMMEDIATE"))
            except Exception:
                pass

        records = query.all()
        if not records:
            return []

        lease_expiry = now_utc + timedelta(seconds=self.lease_duration_sec)
        snapshots: List[ClaimedEventSnapshot] = []

        for r in records:
            if r.status == "PROCESSING":
                with self.telemetry._lock:
                    self.telemetry.lease_expirations += 1
                logger.warning(
                    f"Dispatcher {self.worker_id}: Reclaiming expired lease for "
                    f"event {r.event_id} (id={r.id}, previous_worker={r.worker_id})"
                )

            r.status = "PROCESSING"
            r.worker_id = self.worker_id
            r.lease_until = lease_expiry
            r.retry_count = (r.retry_count or 0) + 1

            payload_copy = dict(r.payload) if isinstance(r.payload, dict) else r.payload
            snapshots.append(
                ClaimedEventSnapshot(
                    id=r.id,
                    event_id=r.event_id,
                    event_type=r.event_type,
                    incident_code=r.incident_code,
                    idempotency_key=r.idempotency_key,
                    payload=payload_copy,
                    retry_count=r.retry_count,
                    worker_id=self.worker_id,
                    lease_until=lease_expiry,
                )
            )

        db.commit()  # Claim transaction commits immediately. Row locks released.
        return snapshots

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE B: EXTERNAL DISPATCH (NO DB TRANSACTIONS HELD)
    # ─────────────────────────────────────────────────────────────────────────

    def _dispatch_external(self, snapshot: ClaimedEventSnapshot) -> Tuple[bool, Optional[str]]:
        """Dispatch claimed event to external destinations without holding database transactions."""
        payload = snapshot.payload.copy() if isinstance(snapshot.payload, dict) else {}
        # Ensure canonical identity primitives are preserved identically
        payload["event_id"] = snapshot.event_id
        payload["event_type"] = snapshot.event_type
        payload["incident_code"] = snapshot.incident_code
        payload["idempotency_key"] = snapshot.idempotency_key
        if "type" not in payload:
            payload["type"] = snapshot.event_type

        try:
            # 1. WebSocket Delivery
            if self._event_broadcaster:
                self._event_broadcaster(payload)

            # 2. C2 Delivery (if configured)
            if self._c2_sender:
                self._c2_sender(payload)
            elif getattr(settings, "c2_webhook_url", None):
                from backend.app.services.c2 import send_webhook_http
                send_webhook_http(
                    url=settings.c2_webhook_url,
                    payload=payload,
                    secret=getattr(settings, "c2_webhook_secret", "") or "",
                    timeout=3.0,
                    max_retries=1,
                )

            with self.telemetry._lock:
                self.telemetry.deliveries += 1
            return True, None
        except Exception as exc:
            err = str(exc)
            logger.warning(
                f"Dispatcher {self.worker_id}: External delivery error for "
                f"event {snapshot.event_id} (code: {snapshot.incident_code}): {err}"
            )
            with self.telemetry._lock:
                self.telemetry.delivery_failures += 1
            return False, err

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE C: FENCED COMPLETION (< 2ms, SEPARATE DB TRANSACTION)
    # ─────────────────────────────────────────────────────────────────────────

    def _complete_event(
        self,
        snapshot: ClaimedEventSnapshot,
        success: bool,
        error_message: Optional[str],
    ) -> bool:
        """Perform fenced state transition for claimed event.

        Fencing predicate:
            WHERE id = :id AND worker_id = :worker_id AND lease_until >= :now
        If rowcount == 0, worker ownership expired/reclaimed; transition is safely aborted.
        """
        with self._session_factory() as db:
            now_utc = datetime.utcnow()
            try:
                if success:
                    # Terminal success: DELIVERED
                    stmt = (
                        sa.update(IncidentOutboxEvent)
                        .where(
                            IncidentOutboxEvent.id == snapshot.id,
                            IncidentOutboxEvent.worker_id == snapshot.worker_id,
                            IncidentOutboxEvent.lease_until >= now_utc,
                        )
                        .values(
                            status="DELIVERED",
                            delivered_at=now_utc,
                            worker_id=None,
                            lease_until=None,
                            error_message=None,
                        )
                    )
                    res = db.execute(stmt)
                    db.commit()

                    if res.rowcount == 0:
                        with self.telemetry._lock:
                            self.telemetry.zombie_completion_rejections += 1
                        logger.warning(
                            f"Dispatcher {snapshot.worker_id}: Zombie completion rejected for "
                            f"event {snapshot.event_id} (id={snapshot.id}). Row affected 0 (lease expired/reclaimed)."
                        )
                        return False
                    return True
                else:
                    # Failure transition: FAILED or DEAD_LETTER
                    backoff_sec = min(
                        self.base_backoff_sec * (2 ** max(0, snapshot.retry_count - 1)),
                        self.max_backoff_sec,
                    )
                    next_retry = now_utc + timedelta(seconds=backoff_sec)
                    is_dead_letter = (snapshot.retry_count >= self.max_retries)
                    target_status = "DEAD_LETTER" if is_dead_letter else "FAILED"

                    stmt = (
                        sa.update(IncidentOutboxEvent)
                        .where(
                            IncidentOutboxEvent.id == snapshot.id,
                            IncidentOutboxEvent.worker_id == snapshot.worker_id,
                            IncidentOutboxEvent.lease_until >= now_utc,
                        )
                        .values(
                            status=target_status,
                            next_retry_at=next_retry,
                            worker_id=None,
                            lease_until=None,
                            error_message=error_message,
                        )
                    )
                    res = db.execute(stmt)
                    db.commit()

                    if res.rowcount == 0:
                        with self.telemetry._lock:
                            self.telemetry.zombie_completion_rejections += 1
                        logger.warning(
                            f"Dispatcher {snapshot.worker_id}: Zombie failure transition rejected for "
                            f"event {snapshot.event_id} (id={snapshot.id}). Row affected 0."
                        )
                        return False

                    with self.telemetry._lock:
                        if is_dead_letter:
                            self.telemetry.dead_letters += 1
                        else:
                            self.telemetry.retry_count += 1
                    return True
            except Exception as e:
                db.rollback()
                logger.error(
                    f"Dispatcher {snapshot.worker_id}: Completion transaction failed for "
                    f"event {snapshot.event_id}: {e}"
                )
                return False

    # ─────────────────────────────────────────────────────────────────────────
    # STEP EXECUTION & WORKER LIFECYCLE
    # ─────────────────────────────────────────────────────────────────────────

    def run_once(self) -> int:
        """Execute one claim-dispatch-complete iteration synchronously.

        Returns the number of claimed events processed.
        """
        with self.telemetry._lock:
            self.telemetry.claim_attempts += 1

        snapshots: List[ClaimedEventSnapshot] = []
        with self._session_factory() as db:
            now_utc = datetime.utcnow()
            try:
                snapshots = self._claim_batch(db, now_utc)
            except Exception as e:
                db.rollback()
                with self.telemetry._lock:
                    self.telemetry.claim_conflicts += 1
                logger.warning(f"Dispatcher {self.worker_id}: Batch claim failed: {e}")
                return 0

        if not snapshots:
            return 0

        with self.telemetry._lock:
            self.telemetry.claim_successes += len(snapshots)

        for snapshot in snapshots:
            if self._stop_event.is_set():
                break
            success, err_msg = self._dispatch_external(snapshot)
            self._complete_event(snapshot, success, err_msg)

        return len(snapshots)

    def _worker_loop(self) -> None:
        """Continuous background worker loop with interruptible polling."""
        logger.info(f"IncidentOutboxDispatcher {self.worker_id} background loop started.")
        while not self._stop_event.is_set():
            try:
                processed = self.run_once()
                if processed == 0:
                    self._stop_event.wait(timeout=self.poll_interval_sec)
            except Exception as e:
                logger.error(f"Dispatcher {self.worker_id} unhandled error in worker loop: {e}", exc_info=True)
                self._stop_event.wait(timeout=min(self.poll_interval_sec, 2.0))
        logger.info(f"IncidentOutboxDispatcher {self.worker_id} background loop stopped.")

    def start(self) -> None:
        """Start background polling thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._worker_loop,
            name=f"outbox-disp-{self.worker_id[:16]}",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Signal dispatcher to stop and join worker thread."""
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
            self._thread = None

    def is_running(self) -> bool:
        """Check if background worker thread is alive."""
        return self._thread is not None and self._thread.is_alive()


# Global Outbox Dispatcher Singleton
global_outbox_dispatcher = IncidentOutboxDispatcher()


def get_outbox_dispatcher() -> IncidentOutboxDispatcher:
    """Retrieve global outbox dispatcher instance."""
    return global_outbox_dispatcher
