"""
Camera Health Persistence Worker & Decoupled Bounded Buffer (GAP-P0-01 Phase 5)
================================================================================

Remediates reader-thread database coupling:
1. RTSP reader thread must NEVER execute synchronous database queries, transactions, or connection waits.
2. Lightweight in-memory health metrics are enqueued non-blockingly to a bounded coalescing buffer.
3. Dedicated background worker (`CameraHealthPersistenceWorker`) drains the buffer and commits health records.
4. Database slowdowns, timeouts, or connection failures are completely isolated from RTSP frame acquisition.
"""

from __future__ import annotations

import os
import time
import logging
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Any, List, Optional, Callable

from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from backend.app.db.session import SessionLocal
from backend.app.models.camera import Camera
from backend.app.models.camera_health import CameraHealth
from backend.app.models.event import Event

logger = logging.getLogger(__name__)

DEFAULT_HEALTH_BUFFER_CAPACITY = 100
DEFAULT_HEALTH_POLL_INTERVAL_SEC = 0.5
DEFAULT_HEALTH_RETRY_BACKOFF_SEC = 0.5
DEFAULT_MAX_RETRIES = 3


@dataclass
class HealthWorkerTelemetry:
    """Thread-safe telemetry counters for decoupled camera health persistence."""
    health_events_generated: int = 0
    health_events_enqueued: int = 0
    health_events_coalesced: int = 0
    health_events_dropped_overflow: int = 0
    health_db_writes_attempted: int = 0
    health_db_writes_succeeded: int = 0
    health_db_failures: int = 0
    health_persistence_retries: int = 0
    shutdown_drain_result: Dict[str, Any] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "health_events_generated": self.health_events_generated,
                "health_events_enqueued": self.health_events_enqueued,
                "health_events_coalesced": self.health_events_coalesced,
                "health_events_dropped_overflow": self.health_events_dropped_overflow,
                "health_db_writes_attempted": self.health_db_writes_attempted,
                "health_db_writes_succeeded": self.health_db_writes_succeeded,
                "health_db_failures": self.health_db_failures,
                "health_persistence_retries": self.health_persistence_retries,
                "shutdown_drain_result": self.shutdown_drain_result.copy(),
            }


class BoundedHealthBuffer:
    """Bounded, thread-safe health event buffer with camera-aware state coalescing.

    Invariants:
    1. Maximum capacity is strictly bounded (never grows indefinitely).
    2. Reader thread enqueue is non-blocking (O(1) memory operation under fast mutex).
    3. Repeated status updates for the same camera are coalesced into the existing slot,
       preserving the newest meaningful metrics without bloating the queue.
    4. State transitions (e.g. OFFLINE -> RECOVERING) are preserved.
    5. If buffer reaches capacity and cannot be coalesced, events are dropped with telemetry.
    """

    def __init__(self, capacity: int = DEFAULT_HEALTH_BUFFER_CAPACITY):
        self.capacity = capacity
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._items: List[Dict[str, Any]] = []
        self._camera_to_index: Dict[int, int] = {}
        self.telemetry = HealthWorkerTelemetry()

    @property
    def queue_depth(self) -> int:
        with self._lock:
            return len(self._items)

    def enqueue(self, health_dict: Dict[str, Any], is_transition: bool = False) -> bool:
        """Non-blocking enqueue with camera-aware state coalescing.

        Returns:
            bool: True if enqueued or coalesced, False if dropped due to buffer overflow.
        """
        camera_id = health_dict.get("camera_id")
        if camera_id is None:
            return False

        with self._lock:
            self.telemetry.health_events_generated += 1

            # Check if camera already has a pending item in the queue
            if camera_id in self._camera_to_index:
                idx = self._camera_to_index[camera_id]
                existing = self._items[idx]
                # If existing pending item has the same status, or if this is not a state transition:
                # Coalesce: update the existing pending slot with the newest metrics
                if not is_transition or existing.get("status") == health_dict.get("status"):
                    self._items[idx] = health_dict.copy()
                    self.telemetry.health_events_coalesced += 1
                    return True

            # If capacity reached:
            if len(self._items) >= self.capacity:
                # If this camera already has a slot, coalesce into it regardless of transition
                if camera_id in self._camera_to_index:
                    idx = self._camera_to_index[camera_id]
                    self._items[idx] = health_dict.copy()
                    self.telemetry.health_events_coalesced += 1
                    return True
                else:
                    # Bounded overflow: drop event to protect memory and reader
                    self.telemetry.health_events_dropped_overflow += 1
                    logger.warning(
                        f"BoundedHealthBuffer: Overflow dropped health event for camera {camera_id} "
                        f"(capacity {self.capacity} reached)"
                    )
                    return False

            # Append new item
            new_idx = len(self._items)
            self._items.append(health_dict.copy())
            self._camera_to_index[camera_id] = new_idx
            self.telemetry.health_events_enqueued += 1
            self._cond.notify()
            return True

    def pop(self, timeout: float = 1.0) -> Optional[Dict[str, Any]]:
        """Pop next health event in FIFO order."""
        with self._cond:
            while not self._items:
                if not self._cond.wait(timeout=timeout):
                    return None
            item = self._items.pop(0)
            # Rebuild index mapping
            self._camera_to_index.clear()
            for idx, entry in enumerate(self._items):
                cid = entry.get("camera_id")
                if cid is not None:
                    self._camera_to_index[cid] = idx
            return item

    def pop_all(self) -> List[Dict[str, Any]]:
        """Drain all current items immediately."""
        with self._lock:
            items = list(self._items)
            self._items.clear()
            self._camera_to_index.clear()
            return items

    def clear(self) -> None:
        """Clear all buffer items."""
        with self._lock:
            self._items.clear()
            self._camera_to_index.clear()


class CameraHealthPersistenceWorker:
    """Dedicated background worker for persisting camera health measurements to database.

    Runs decoupled from the RTSP reader thread, ensuring that database timeouts, connection
    pool exhaustion, and transaction delays cannot block or stall RTSP video streaming.
    """

    def __init__(
        self,
        buffer: Optional[BoundedHealthBuffer] = None,
        session_factory: Optional[Callable[[], Session]] = None,
        poll_interval_sec: float = DEFAULT_HEALTH_POLL_INTERVAL_SEC,
        retry_backoff_sec: float = DEFAULT_HEALTH_RETRY_BACKOFF_SEC,
        max_retries: int = DEFAULT_MAX_RETRIES,
        worker_id: Optional[str] = None,
    ):
        self.buffer = buffer or BoundedHealthBuffer()
        self.session_factory = session_factory or SessionLocal
        self.poll_interval_sec = poll_interval_sec
        self.retry_backoff_sec = retry_backoff_sec
        self.max_retries = max_retries
        self.worker_id = worker_id or f"health-worker-{os.getpid()}"

        self.telemetry = self.buffer.telemetry
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._started = False
        self._lifecycle_lock = threading.Lock()

    @property
    def queue_depth(self) -> int:
        return self.buffer.queue_depth

    def enqueue(self, health_dict: Dict[str, Any], is_transition: bool = False) -> bool:
        """Enqueue health dictionary from camera pipeline (non-blocking)."""
        return self.buffer.enqueue(health_dict, is_transition=is_transition)

    def persist_event(self, db: Session, health_dict: Dict[str, Any]) -> bool:
        """Atomically persist one health event to database.

        Updates:
        1. CameraHealth time-series row
        2. Camera status, health_score, last_heartbeat
        3. Event diagnostic entry if degraded or offline
        """
        camera_id = health_dict.get("camera_id")
        if camera_id is None:
            return False

        now_utc = datetime.utcnow()
        try:
            # 1. Record CameraHealth time-series entry
            ch = CameraHealth(
                camera_id=camera_id,
                health_score=float(health_dict.get("health_score", 100.0)),
                status=str(health_dict.get("status", "HEALTHY")),
                fps_actual=float(health_dict.get("fps_actual", 0.0)),
                brightness=float(health_dict.get("brightness", 0.0)),
                blur_score=float(health_dict.get("blur_score", 0.0)),
                frame_delta=float(health_dict.get("frame_delta", 0.0)),
                resolution_width=int(health_dict.get("resolution_width", 0)),
                resolution_height=int(health_dict.get("resolution_height", 0)),
                latency_ms=float(health_dict.get("latency_ms", 0.0)),
                stream_uptime_seconds=float(health_dict.get("stream_uptime_seconds", 0.0)),
                reconnect_count=int(health_dict.get("reconnect_count", 0)),
                extra_data={
                    "substate": health_dict.get("substate"),
                    "warnings": health_dict.get("warnings", []),
                    "read_failures": health_dict.get("read_failures", 0),
                },
                measured_at=now_utc,
            )
            db.add(ch)

            raw_status = str(health_dict.get("status", "HEALTHY")).upper()
            persisted_status = raw_status

            # 2. Update or create Camera record
            cam = db.get(Camera, camera_id)
            if not cam:
                cam = Camera(
                    id=camera_id,
                    name=str(health_dict.get("camera_name", f"Camera {camera_id}")),
                    stream_url=f"camera://{camera_id}",
                    status=persisted_status,
                    health_score=float(health_dict.get("health_score", 100.0)),
                    last_heartbeat=now_utc,
                )
                db.add(cam)
                db.flush()
            else:
                cam.status = persisted_status
                cam.health_score = float(health_dict.get("health_score", 100.0))
                cam.last_heartbeat = now_utc

            # 3. If degraded or offline, record Event diagnostic
            status = health_dict.get("status", "HEALTHY")
            substate = health_dict.get("substate")
            if status in ("DEGRADED", "OFFLINE") or substate in ("FROZEN", "BLURRY", "BRIGHTNESS_DEGRADED"):
                ev = Event(
                    camera_id=camera_id,
                    event_type="camera_health",
                    object_type="camera",
                    severity=1.0 if status == "OFFLINE" else 0.5,
                    payload=health_dict,
                    occurred_at=now_utc,
                )
                db.add(ev)

            db.commit()
            with self.telemetry._lock:
                self.telemetry.health_db_writes_succeeded += 1
            return True

        except Exception as e:
            db.rollback()
            with self.telemetry._lock:
                self.telemetry.health_db_failures += 1
            logger.debug(f"CameraHealthPersistenceWorker: Error persisting health for camera {camera_id}: {e}")
            raise e

    def _worker_loop(self) -> None:
        """Continuous background drainage loop."""
        logger.info(f"CameraHealthPersistenceWorker {self.worker_id}: Started.")
        while not self._stop_event.is_set():
            event = self.buffer.pop(timeout=self.poll_interval_sec)
            if event is None:
                continue

            with self.telemetry._lock:
                self.telemetry.health_db_writes_attempted += 1

            success = False
            for attempt in range(self.max_retries + 1):
                if self._stop_event.is_set():
                    break
                try:
                    with self.session_factory() as db:
                        success = self.persist_event(db, event)
                        if success:
                            break
                except Exception as e:
                    if attempt < self.max_retries:
                        with self.telemetry._lock:
                            self.telemetry.health_persistence_retries += 1
                        backoff = self.retry_backoff_sec * (attempt + 1)
                        logger.debug(
                            f"CameraHealthPersistenceWorker {self.worker_id}: Retrying camera "
                            f"{event.get('camera_id')} persistence in {backoff:.2f}s (attempt {attempt + 1}): {e}"
                        )
                        self._stop_event.wait(timeout=backoff)
                    else:
                        logger.warning(
                            f"CameraHealthPersistenceWorker {self.worker_id}: Exhausted retries for camera "
                            f"{event.get('camera_id')}; event discarded to protect buffer."
                        )

        logger.info(f"CameraHealthPersistenceWorker {self.worker_id}: Stopped.")

    def start(self) -> None:
        """Start background worker thread."""
        with self._lifecycle_lock:
            if self._started and self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._worker_loop,
                name=f"health-worker-{self.worker_id[:16]}",
                daemon=True,
            )
            self._thread.start()
            self._started = True

    def stop(self, timeout: float = 5.0) -> Dict[str, Any]:
        """Signal worker to stop, drain up to bounded backlog, and join thread."""
        with self._lifecycle_lock:
            if not self._started:
                return {"drained": 0, "dropped": 0, "clean": True}

            self._stop_event.set()
            start_time = time.time()
            drain_count = 0
            dropped_count = 0

            # Wake up worker if waiting
            with self.buffer._cond:
                self.buffer._cond.notify_all()

            # Bounded drain
            remaining = self.buffer.pop_all()
            if remaining:
                try:
                    with self.session_factory() as db:
                        for item in remaining:
                            if time.time() - start_time > (timeout * 0.8):
                                dropped_count += 1
                                continue
                            try:
                                if self.persist_event(db, item):
                                    drain_count += 1
                                else:
                                    dropped_count += 1
                            except Exception:
                                dropped_count += 1
                except Exception as drain_err:
                    logger.debug(f"Drain error during shutdown: {drain_err}")
                    dropped_count += len(remaining) - drain_count

            remaining_timeout = max(0.1, timeout - (time.time() - start_time))
            if self._thread is not None and self._thread.is_alive():
                self._thread.join(timeout=remaining_timeout)
                self._thread = None

            self._started = False
            result = {
                "drained": drain_count,
                "dropped": dropped_count,
                "clean": True,
            }
            self.telemetry.shutdown_drain_result = result
            return result

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


# Global Singleton Instance
global_health_worker = CameraHealthPersistenceWorker()


def get_camera_health_worker() -> CameraHealthPersistenceWorker:
    """Retrieve global singleton CameraHealthPersistenceWorker."""
    return global_health_worker
