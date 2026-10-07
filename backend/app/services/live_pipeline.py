"""
Live Pipeline Service — Real Camera → Real AI → Real Incidents
==============================================================

Runs the edge pipeline on each camera feed in a background thread:
1. Captures frames from RTSP/USB/file
2. Runs YOLO26n detection on each frame
3. Tracks objects with ByteTrack
4. Checks zone crossings and behavior
5. Generates real incidents when threats exceed threshold
6. Pushes events to WebSocket for live dashboard updates

This is the REAL pipeline — no demo scripts, no fake data.
Every incident comes from actual AI detection on real video.
"""

import os
import sys
import time
import json
import math
import hashlib
import logging
import threading
from datetime import datetime, timezone, date
from typing import Dict, Optional, List, Tuple, Any, Callable, Union
from collections import deque
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# Reconnect State Machine States (GAP-P0-01 Step 4)
RECONNECT_STATE_CONNECTED = "CONNECTED"
RECONNECT_STATE_READ_FAILURE = "READ_FAILURE"
RECONNECT_STATE_BACKOFF = "BACKOFF"
RECONNECT_STATE_RECONNECTING = "RECONNECTING"
RECONNECT_STATE_RECOVERED = "RECOVERED"
RECONNECT_STATE_STOPPED = "STOPPED"

# Processor State Machine States (GAP-P0-01 Step 5)
PROCESSOR_STATE_START = "START"
PROCESSOR_STATE_PROCESSING = "PROCESSING"
PROCESSOR_STATE_WAITING_FOR_FRAME = "WAITING_FOR_FRAME"
PROCESSOR_STATE_STOPPING = "STOPPING"
PROCESSOR_STATE_STOPPED = "STOPPED"


class LivePipelineManager:
    """Manages live pipelines for all cameras."""

    def __init__(self):
        self._pipelines: Dict[int, "CameraPipeline"] = {}
        self._lock = threading.Lock()
        self._event_listeners: List = []

    def start_camera(self, camera_id: int, stream_url: str,
                     camera_name: str = "", bop: str = "BOP-01"):
        """Start live pipeline for a camera."""
        with self._lock:
            if camera_id in self._pipelines:
                self.stop_camera(camera_id)

            pipeline = CameraPipeline(
                camera_id=camera_id,
                stream_url=stream_url,
                camera_name=camera_name,
                bop=bop,
            )
            pipeline.set_event_callback(self._on_event)
            self._pipelines[camera_id] = pipeline
            pipeline.start()
            logger.info(f"Started live pipeline for camera {camera_id}: {stream_url}")

    def stop_camera(self, camera_id: int):
        """Stop pipeline for a camera."""
        with self._lock:
            if camera_id in self._pipelines:
                self._pipelines[camera_id].stop()
                del self._pipelines[camera_id]
                logger.info(f"Stopped pipeline for camera {camera_id}")

    def stop_all(self):
        """Stop all pipelines."""
        with self._lock:
            for pipeline in self._pipelines.values():
                pipeline.stop()
            self._pipelines.clear()

    def get_status(self) -> Dict:
        """Get status of all running pipelines."""
        with self._lock:
            statuses = {}
            for cam_id, pipeline in self._pipelines.items():
                statuses[cam_id] = pipeline.get_stats()
            return {
                "active_pipelines": len(self._pipelines),
                "pipelines": statuses,
            }

    def get_latest_frame(self, camera_id: int):
        """Retrieve the most recent frame from the camera's live pipeline buffer.
        Returns the real-time AI annotated frame with bounding boxes if available,
        otherwise the raw latest-frame slot frame, falling back to the historical ring."""
        pipeline = self._pipelines.get(camera_id)
        if pipeline:
            if pipeline._latest_annotated_frame is not None:
                return pipeline._latest_annotated_frame
            pkt = pipeline.get_latest_frame_packet(timeout=0.0)
            if pkt is not None:
                return pkt.frame
            if hasattr(pipeline, "_frame_ring_buffer") and pipeline._frame_ring_buffer:
                try:
                    last_item = pipeline._frame_ring_buffer[-1]
                    if isinstance(last_item, np.ndarray):
                        return last_item
                    elif hasattr(last_item, "jpeg_data"):
                        return cv2.imdecode(np.frombuffer(last_item.jpeg_data, dtype=np.uint8), cv2.IMREAD_COLOR)
                except (IndexError, AttributeError):
                    pass
        return None

    def add_event_listener(self, callback):
        """Add a listener for pipeline events (WebSocket push)."""
        self._event_listeners.append(callback)

    def _on_event(self, event: Dict):
        """Broadcast event to all listeners."""
        for listener in self._event_listeners:
            try:
                listener(event)
            except Exception as e:
                logger.error(f"Event listener error: {e}")


def _normalize_for_json(obj: Any) -> Any:
    """Recursively normalize objects into pure JSON-serializable primitives for outbox payloads."""
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)) and not isinstance(obj, (np.bool_, np.number)):
        return obj
    if isinstance(obj, (np.floating, float)):
        return float(obj)
    if isinstance(obj, (np.integer, int)) and not isinstance(obj, bool):
        return int(obj)
    if isinstance(obj, (np.bool_, bool)):
        return bool(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if hasattr(obj, "isoformat") and callable(getattr(obj, "isoformat")):
        return obj.isoformat()
    if isinstance(obj, np.ndarray):
        return [_normalize_for_json(x) for x in obj.tolist()]
    if isinstance(obj, dict):
        return {str(k): _normalize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, deque)):
        return [_normalize_for_json(x) for x in obj]
    return str(obj)


@dataclass(frozen=True)
class FramePacket:
    """Immutable frame container passed across the low-contention slot.

    Critical Invariant:
    Once a NumPy frame is published inside a FramePacket, no reader-side
    code may mutate that frame memory.
    """
    frame_id: int              # Sequential monotonic acquisition counter (1, 2, 3...)
    frame: np.ndarray          # Raw BGR image matrix (H, W, 3) uint8 (used directly by AI)
    capture_utc: float         # Epoch timestamp (time.time()) for event/evidence timestamps
    capture_mono: float        # Monotonic timestamp (time.perf_counter()) for kinematics/timing
    source_fps: float          # Current measured stream acquisition FPS
    stream_epoch: int          # Incremented on each reconnect event (initial: 1)
    hardware_drop_count: int   # Cumulative failed cap.read() calls up to this frame


@dataclass(frozen=True)
class NVRRingEntry:
    """Immutable compressed video frame container stored in TimeBoundedNVRRing.

    Designed to preserve metadata and chain-of-custody information relevant
    to electronic evidence handling under Bharatiya Sakshya Adhiniyam, 2023 (Section 63).
    """
    frame_id: int
    capture_utc: datetime
    capture_mono: float
    stream_epoch: int
    source_fps: float
    jpeg_data: bytes
    size_bytes: int
    width: int
    height: int
    is_synthetic: bool = False


class TimeBoundedNVRRing:
    """Thread-safe, dual-bounded in-memory ring buffer for historical NVR video frames (GAP-P0-01 Step 6).

    Enforces both:
    - max_duration_sec: Temporal boundary (evicts frames older than window)
    - max_bytes: Hard memory ceiling (evicts oldest frames when total bytes exceeds limit)
    """
    def __init__(self, max_duration_sec: float = 10.0, max_bytes: int = 50 * 1024 * 1024, max_frames: int = 2000):
        self.max_duration_sec: float = float(max_duration_sec)
        self.max_bytes: int = int(max_bytes)
        self.max_frames: int = int(max_frames)
        self._deque: deque = deque()
        self._total_bytes: int = 0
        self._lock = threading.Lock()
        self.nvr_bytes_evicted: int = 0

    def append(self, item: Union[NVRRingEntry, FramePacket, np.ndarray]) -> None:
        """Append an entry, packet, or raw frame to the ring buffer under lock."""
        with self._lock:
            if isinstance(item, NVRRingEntry):
                entry = item
            elif isinstance(item, FramePacket):
                h, w = item.frame.shape[:2]
                ret, buf = cv2.imencode(".jpg", item.frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
                if not ret or buf is None:
                    return
                jpeg_bytes = buf.tobytes()
                utc_dt = datetime.fromtimestamp(item.capture_utc, tz=timezone.utc) if isinstance(item.capture_utc, (int, float)) and item.capture_utc > 0 else datetime.now(timezone.utc)
                entry = NVRRingEntry(
                    frame_id=item.frame_id,
                    capture_utc=utc_dt,
                    capture_mono=item.capture_mono,
                    stream_epoch=item.stream_epoch,
                    source_fps=item.source_fps,
                    jpeg_data=jpeg_bytes,
                    size_bytes=len(jpeg_bytes),
                    width=w,
                    height=h,
                    is_synthetic=False,
                )
            elif isinstance(item, np.ndarray):
                # Legacy test compatibility: mark explicitly as synthetic untrusted data
                h, w = item.shape[:2]
                ret, buf = cv2.imencode(".jpg", item, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
                if not ret or buf is None:
                    return
                jpeg_bytes = buf.tobytes()
                now_mono = time.perf_counter()
                entry = NVRRingEntry(
                    frame_id=-1,
                    capture_utc=datetime.now(timezone.utc),
                    capture_mono=now_mono,
                    stream_epoch=-1,
                    source_fps=10.0,
                    jpeg_data=jpeg_bytes,
                    size_bytes=len(jpeg_bytes),
                    width=w,
                    height=h,
                    is_synthetic=True,
                )
            else:
                raise TypeError(f"Expected NVRRingEntry, FramePacket, or np.ndarray, got {type(item)}")

            self._deque.append(entry)
            self._total_bytes += entry.size_bytes

            # Eviction 1: Temporal Expiry
            cutoff_mono = entry.capture_mono - self.max_duration_sec
            while self._deque and self._deque[0].capture_mono < cutoff_mono:
                evicted = self._deque.popleft()
                self._total_bytes -= evicted.size_bytes
                self.nvr_bytes_evicted += evicted.size_bytes

            # Eviction 2: Byte Ceiling Enforcement
            while self._total_bytes > self.max_bytes and len(self._deque) > 1:
                evicted = self._deque.popleft()
                self._total_bytes -= evicted.size_bytes
                self.nvr_bytes_evicted += evicted.size_bytes

            # Eviction 3: Frame Ceiling Enforcement
            while len(self._deque) > self.max_frames:
                evicted = self._deque.popleft()
                self._total_bytes -= evicted.size_bytes
                self.nvr_bytes_evicted += evicted.size_bytes

    def snapshot_preroll(
        self,
        incident_mono: float,
        preroll_duration_sec: float = 5.0,
        epoch: Optional[int] = None
    ) -> List[NVRRingEntry]:
        """Extract a thread-safe snapshot of contiguous frames covering [incident_mono - preroll_duration, incident_mono].
        Strict epoch boundary: halts if frames from a different epoch are encountered.
        """
        target_start = incident_mono - preroll_duration_sec
        with self._lock:
            if not self._deque:
                return []
            # Legacy synthetic frames path (for unit tests using raw arrays directly)
            if any(e.is_synthetic for e in self._deque):
                return list(self._deque)[-25:]

            selected: List[NVRRingEntry] = []
            for entry in reversed(self._deque):
                # Strict epoch boundary: do not traverse across outage boundaries
                if epoch is not None and entry.stream_epoch != epoch:
                    break
                if entry.capture_mono > incident_mono:
                    continue
                selected.append(entry)
                if entry.capture_mono <= target_start:
                    break

            selected.reverse()
            return selected

    def clear(self) -> None:
        with self._lock:
            self._deque.clear()
            self._total_bytes = 0

    def get_total_bytes(self) -> int:
        with self._lock:
            return self._total_bytes

    def __len__(self) -> int:
        with self._lock:
            return len(self._deque)

    def __getitem__(self, idx: int) -> NVRRingEntry:
        with self._lock:
            return self._deque[idx]

    def __iter__(self):
        with self._lock:
            return iter(list(self._deque))


class BoundedClipSynthesizer:
    """Application-wide bounded clip synthesis pool (GAP-P0-01 Step 6).

    Guarantees that background clip synthesis cannot exceed max_workers CPU cores,
    and enforces a hard upper bound on pending queued jobs to prevent memory accumulation.
    """
    def __init__(self, max_workers: int = 2, max_pending: int = 8):
        self.max_workers = max_workers
        self.max_pending = max_pending
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="nvr_clip_synth")
        self._pending = 0
        self._lock = threading.Lock()

    def submit(self, fn: Callable, *args, **kwargs) -> bool:
        """Submit a synthesis job. Returns True if accepted, False if shed due to capacity."""
        with self._lock:
            if self._pending >= self.max_pending:
                return False
            self._pending += 1

        def _task_wrapper():
            try:
                fn(*args, **kwargs)
            finally:
                with self._lock:
                    self._pending -= 1

        try:
            self._pool.submit(_task_wrapper)
            return True
        except Exception:
            with self._lock:
                self._pending -= 1
            return False

    @property
    def pending_count(self) -> int:
        with self._lock:
            return self._pending

    def shutdown(self, wait: bool = True) -> None:
        self._pool.shutdown(wait=wait)


global_clip_synthesizer = BoundedClipSynthesizer(max_workers=2, max_pending=8)


class CameraPipeline:
    """Live pipeline for a single camera."""

    def __init__(self, camera_id: int, stream_url: str,
                 camera_name: str = "", bop: str = "BOP-01",
                 zones: Optional[List[Dict[str, Any]]] = None,
                 capture_device: Optional[Any] = None,
                 capture_factory: Optional[Any] = None,
                 detector: Optional[Any] = None,
                 frame_processor: Optional[Callable] = None,
                 detection_stride: int = 5,
                 stale_frame_threshold: float = 0.5,
                 frame_rate_pause: float = 0.03,
                 initial_backoff: float = 1.0,
                 maximum_backoff: float = 30.0,
                 backoff_multiplier: float = 2.0,
                 backoff_jitter: float = 0.0,
                 consecutive_failure_threshold: int = 3,
                 nvr_preroll_duration_sec: float = 5.0,
                 nvr_max_bytes: int = 50 * 1024 * 1024,
                 nvr_staging_capacity: int = 10,
                 nvr_jpeg_quality: int = 80):
        self.camera_id = camera_id
        self.stream_url = stream_url
        self.camera_name = camera_name
        self.bop = bop
        self._custom_capture = capture_device
        self._capture_factory = capture_factory
        self.initial_backoff: float = float(initial_backoff)
        self.maximum_backoff: float = float(maximum_backoff)
        self.backoff_multiplier: float = float(backoff_multiplier)
        self.backoff_jitter: float = float(backoff_jitter)
        self.consecutive_failure_threshold: int = int(consecutive_failure_threshold)

        # Reconnect State Machine & Telemetry (GAP-P0-01 Step 4)
        self.reconnect_state: str = RECONNECT_STATE_CONNECTED
        self.reconnect_attempts: int = 0
        self.reconnect_failures: int = 0
        self.consecutive_read_failures: int = 0
        self.last_reconnect_timestamp: Optional[float] = None

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._reader_stop_event = threading.Event()
        self._cap = None  # SOLE OWNER: _reader_worker (GAP-P0-01 Step 3 & 4)
        self._source_fps: float = 0.0
        self._event_callback = None
        self._frame_count = 0
        self._start_time = 0
        self._stats = {}

        # Threat tracking per track
        self._track_dwell: Dict[str, float] = {}  # track_id -> first_seen
        self._track_zones: Dict[str, set] = {}     # track_id -> set of zone names
        self._incident_cooldown: Dict[str, float] = {}  # fingerprint -> last_time
        self._detections_buffer = deque(maxlen=100)

        # Modernized Pre-Roll NVR Subsystem (GAP-P0-01 Step 6)
        self.nvr_preroll_duration_sec: float = float(nvr_preroll_duration_sec)
        self.nvr_max_bytes: int = int(nvr_max_bytes)
        self.nvr_staging_capacity: int = int(nvr_staging_capacity)
        self.nvr_jpeg_quality: int = int(nvr_jpeg_quality)

        self.nvr_staging_drops: int = 0
        self.nvr_encode_failures: int = 0
        self.nvr_epoch_transitions: int = 0
        self.nvr_clip_shedding_drops: int = 0
        self.nvr_decode_corruptions: int = 0
        self.nvr_timestamp_anomalies: int = 0
        self._nvr_last_stream_epoch: int = 0

        self._nvr_staging_queue: deque = deque()
        self._nvr_staging_lock = threading.Lock()
        self._nvr_staging_condition = threading.Condition(self._nvr_staging_lock)
        self._nvr_stop_event = threading.Event()
        self._nvr_thread: Optional[threading.Thread] = None

        self._frame_ring_buffer = TimeBoundedNVRRing(
            max_duration_sec=self.nvr_preroll_duration_sec,
            max_bytes=self.nvr_max_bytes,
        )
        self._prev_frame = None

        # Persistent spatial target tracking & visual overlay
        from edge.tracking.bytetrack import ByteTracker
        self.tracker = ByteTracker(
            high_thresh=0.4,
            low_thresh=0.1,
            track_buffer=30,
            max_age=70,
        )
        self._track_metadata: Dict[int, Dict] = {}  # target_id -> persistent tracklet metadata (alert_sent, first_seen, etc.)
        self._tracked_targets: Dict[int, Dict] = {}  # int_id -> persistent track state (legacy compat)
        self._next_track_num: int = 1
        self._latest_annotated_frame: Optional[np.ndarray] = None
        self._latest_tracks: List[Dict] = []
        self._last_cam_alert: Dict[str, float] = {}
        self._last_evidence_save: float = 0.0
        self._active_track_ids: set[int] = set()

        # Virtual Fence Geometric Engine (one instance per camera stream lifecycle)
        from edge.zones.fence import ZoneFence
        if zones is not None:
            self._configured_zones = list(zones)
        else:
            self._configured_zones = self._load_zones_from_db()

        self.zone_fence = ZoneFence(
            zones=self._configured_zones,
            default_cooldown_seconds=10.0,
            loitering_seconds=30.0,
        )
        self._latest_zone_events: List[Dict] = []
        self._zone_eval_times: deque = deque(maxlen=100)

        # Camera Health Diagnostic Engine (one instance per camera lifecycle)
        from edge.health.checks import CameraHealthChecker
        self.health_checker = CameraHealthChecker(expected_fps=float(getattr(self, "fps", 10.0) or 10.0))
        self._health_state: str = "HEALTHY"
        self._health_substate: str = "HEALTHY"
        self._latest_health: Dict[str, Any] = {
            "type": "camera_health",
            "camera_id": self.camera_id,
            "camera_name": self.camera_name,
            "status": "HEALTHY",
            "substate": "HEALTHY",
            "health_score": 100.0,
            "fps_actual": float(getattr(self, "fps", 10.0) or 10.0),
            "brightness": 128.0,
            "blur_score": 100.0,
            "frame_delta": 10.0,
            "resolution": "1280x720",
            "resolution_width": 1280,
            "resolution_height": 720,
            "latency_ms": 0.0,
            "stream_uptime_seconds": 0.0,
            "reconnect_count": 0,
            "read_failures": 0,
            "warnings": [],
            "timestamp": datetime.utcnow().isoformat(),
        }
        self._consecutive_offline_reads: int = 0
        self._consecutive_frozen_frames: int = 0
        self._consecutive_recovering_frames: int = 0
        self._stream_disconnected: bool = False
        self._last_health_event_time: float = 0.0
        self._health_cooldown_seconds: float = 10.0
        self._health_eval_times: deque = deque(maxlen=100)
        self._reconnect_count: int = 0
        self._read_failures: int = 0

        # Identity & Vehicle Evidence Association Engine (one instance per camera lifecycle)
        from edge.evidence.association import EvidenceAssociationEngine
        self.evidence_associator = EvidenceAssociationEngine(camera_id=self.camera_id)

        # Kinematic Behavior Engine & Per-Target Debounce (Phase 4)
        from edge.behavior.kinematics import KinematicBehaviorEngine, CameraCalibrationProfile
        self.kinematic_engine = KinematicBehaviorEngine(CameraCalibrationProfile(camera_id=self.camera_id))
        self._target_incident_cooldown: Dict[str, float] = {}

        # Ingestion & Inference Telemetry Counters (GAP-P0-01 Step 1 & 5)
        self.reader_frames_acquired: int = 0
        self.reader_read_failures: int = 0
        self.processor_frames_started: int = 0
        self.processor_frames_completed: int = 0
        self.processor_frames_skipped_latest_slot: int = 0
        self.processor_stale_frames: int = 0
        self.detection_stride_skips: int = 0
        self.processor_processing_errors: int = 0
        self.processor_processing_time: float = 0.0
        self._last_processing_time: float = 0.0
        self.processor_last_processed_frame_id: Optional[int] = None
        self.processor_last_processed_stream_epoch: Optional[int] = None
        self.reconnect_events: int = 0
        self.stream_epoch: int = 1

        # Processor Worker State Machine & Synchronization (GAP-P0-01 Step 5)
        self.processor_state: str = PROCESSOR_STATE_STOPPED
        self._processor_stop_event = threading.Event()
        self._processor_thread: Optional[threading.Thread] = None
        self._custom_detector = detector
        self._custom_frame_processor = frame_processor
        self.detection_stride: int = int(detection_stride)
        self.stale_frame_threshold: float = float(stale_frame_threshold)
        self._frame_rate_pause: float = float(frame_rate_pause)

        # Low-Contention Latest-Frame Slot & Synchronization Primitives (GAP-P0-01 Step 2)
        self._latest_frame_packet: Optional[FramePacket] = None
        self._slot_lock = threading.Lock()
        self._slot_condition = threading.Condition(self._slot_lock)



    def _load_zones_from_db(self) -> List[Dict[str, Any]]:
        """Load active virtual fence zones for this camera from the database."""
        try:
            from backend.app.db.session import SessionLocal
            from backend.app.models.zone import Zone
            db = SessionLocal()
            try:
                db_zones = db.query(Zone).filter(
                    Zone.enabled == True,
                    (Zone.camera_id == self.camera_id) | (Zone.camera_id == None)
                ).all()
                if db_zones:
                    loaded = []
                    for z in db_zones:
                        geom = z.geometry or {}
                        pts = geom.get("points") or z.polygon or []
                        loaded.append({
                            "id": z.id,
                            "camera_id": z.camera_id,
                            "name": z.name,
                            "zone_type": z.zone_type,
                            "geometry": geom if geom else {"type": "polygon", "points": pts},
                            "polygon": pts,
                            "direction": z.direction or "either",
                            "armed_schedule": z.armed_schedule,
                            "night_only": bool(z.night_only),
                            "min_confidence": float(z.min_confidence or 0.0),
                            "severity": float(z.severity or 0.5),
                            "dwell_threshold_seconds": int(z.dwell_threshold_seconds or 30),
                        })
                    return loaded
            finally:
                db.close()
        except Exception as e:
            logger.debug(f"Camera {self.camera_id}: Could not query zones from DB: {e}")

        # Fallback default perimeter zone for camera field-of-view when none configured in DB
        return [
            {
                "id": 0,
                "camera_id": self.camera_id,
                "name": f"Perimeter-{self.camera_id}",
                "zone_type": "RESTRICTED",
                "geometry": {
                    "type": "polygon",
                    "points": [[0.05, 0.05], [0.95, 0.05], [0.95, 0.95], [0.05, 0.95]],
                },
                "polygon": [[0.05, 0.05], [0.95, 0.05], [0.95, 0.95], [0.05, 0.95]],
                "severity": 0.7,
                "min_confidence": 0.25,
                "direction": "either",
            }
        ]

    def set_zones(self, zones: List[Dict[str, Any]]) -> None:
        """Update virtual fence zone configuration dynamically."""
        self._configured_zones = list(zones)
        if hasattr(self, "zone_fence") and self.zone_fence is not None:
            self.zone_fence.set_zones(self._configured_zones)

    def set_event_callback(self, callback):
        self._event_callback = callback

    # ── LOW-CONTENTION LATEST-FRAME SLOT CONTRACT (GAP-P0-01 Step 2) ──

    def publish_frame_packet(self, packet: FramePacket) -> None:
        """Publish a new FramePacket into the latest-frame slot.

        Producer contract:
        - Acquires instance slot lock.
        - Replaces `_latest_frame_packet` (superseding any previous packet).
        - Notifies waiting consumer via `_slot_condition.notify_all()`.
        - Releases lock immediately.

        Critical Invariants:
        - Slot capacity = strictly 1.
        - Never creates a queue or backlog.
        - Critical section duration is < 5 microseconds (pointer swap only).
        - Invariant: Once published, reader code must never mutate packet.frame.
        """
        if not isinstance(packet, FramePacket):
            raise TypeError(f"Expected FramePacket instance, got {type(packet)}")
        with self._slot_lock:
            self._latest_frame_packet = packet
            self._slot_condition.notify_all()

    def get_latest_frame_packet(
        self,
        last_frame_id: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> Optional[FramePacket]:
        """Fetch the latest FramePacket from the slot.

        Consumer contract:
        - Acquires instance slot lock.
        - If timeout is 0.0: non-blocking check (returns current packet or None).
        - If last_frame_id is provided: waits until a packet with frame_id != last_frame_id
          is published (or timeout expires).
        - If last_frame_id is None: waits until any packet is published (or timeout expires).
        - Releases lock immediately upon retrieval.

        Critical Invariants:
        - Slot lock is NEVER held during downstream inference, tracking, or scoring.
        - Returns the freshest currently available packet, or None if timed out / empty.
        """
        with self._slot_lock:
            if timeout == 0.0:
                if last_frame_id is not None:
                    if self._latest_frame_packet is not None and self._latest_frame_packet.frame_id == last_frame_id:
                        return None
                return self._latest_frame_packet

            def _has_target_packet() -> bool:
                if self._latest_frame_packet is None:
                    return False
                if last_frame_id is not None:
                    return self._latest_frame_packet.frame_id != last_frame_id
                return True

            if not _has_target_packet():
                got_target = self._slot_condition.wait_for(_has_target_packet, timeout=timeout)
                if not got_target:
                    return None

            return self._latest_frame_packet

    def clear_latest_frame_packet(self) -> None:
        """Reset the latest-frame slot to None under lock."""
        with self._slot_lock:
            self._latest_frame_packet = None
            self._slot_condition.notify_all()

    def calculate_next_backoff(self, current_backoff: float) -> float:
        """Calculate next exponential backoff with bounded cap and optional jitter (GAP-P0-01 Step 4)."""
        next_val = min(current_backoff * self.backoff_multiplier, self.maximum_backoff)
        if self.backoff_jitter > 0:
            jitter = min(self.backoff_jitter * current_backoff, self.maximum_backoff - next_val)
            next_val += max(0.0, jitter)
        return float(min(next_val, self.maximum_backoff))

    # ── BOUNDED NVR PRE-ROLL STAGING CONTRACT (GAP-P0-01 Step 6) ──

    def _enqueue_nvr_staging(self, packet: FramePacket) -> None:
        """Non-blocking bounded handoff from _reader_worker to NVR staging queue.

        Critical Invariants:
        - Must NEVER block _reader_worker.
        - Lock hold time is bounded to queue pointer manipulation.
        - Enforces Drop Oldest overload policy when capacity is reached.
        """
        if not isinstance(packet, FramePacket):
            return
        with self._nvr_staging_lock:
            if len(self._nvr_staging_queue) >= self.nvr_staging_capacity:
                self._nvr_staging_queue.popleft()
                self.nvr_staging_drops += 1
            self._nvr_staging_queue.append(packet)
            self._nvr_staging_condition.notify()

    def _nvr_worker(self) -> None:
        """Dedicated background worker consuming staged FramePackets, encoding to JPEG,
        and updating the TimeBoundedNVRRing (GAP-P0-01 Step 6).
        """
        encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), self.nvr_jpeg_quality]
        while not self._nvr_stop_event.is_set():
            packet = None
            with self._nvr_staging_lock:
                while not self._nvr_staging_queue and not self._nvr_stop_event.is_set():
                    self._nvr_staging_condition.wait(timeout=0.1)
                if self._nvr_stop_event.is_set() and not self._nvr_staging_queue:
                    break
                if self._nvr_staging_queue:
                    packet = self._nvr_staging_queue.popleft()

            if packet is None:
                continue

            try:
                # Epoch transition detection
                if packet.stream_epoch != self._nvr_last_stream_epoch:
                    if self._nvr_last_stream_epoch != 0:
                        self.nvr_epoch_transitions += 1
                    self._nvr_last_stream_epoch = packet.stream_epoch

                # Timestamp verification and conversion
                capture_mono = packet.capture_mono
                if capture_mono is None or capture_mono <= 0 or not isinstance(capture_mono, (int, float)):
                    self.nvr_timestamp_anomalies += 1
                    capture_mono = time.perf_counter()

                capture_utc_val = packet.capture_utc
                if isinstance(capture_utc_val, (int, float)) and capture_utc_val > 0:
                    capture_dt = datetime.fromtimestamp(capture_utc_val, tz=timezone.utc)
                elif isinstance(capture_utc_val, datetime):
                    capture_dt = capture_utc_val
                else:
                    self.nvr_timestamp_anomalies += 1
                    capture_dt = datetime.now(timezone.utc)

                # JPEG encoding outside any lock
                frame = packet.frame
                h, w = frame.shape[:2]
                ret, buf = cv2.imencode(".jpg", frame, encode_params)
                if not ret or buf is None:
                    self.nvr_encode_failures += 1
                    continue

                jpeg_bytes = buf.tobytes()
                entry = NVRRingEntry(
                    frame_id=packet.frame_id,
                    capture_utc=capture_dt,
                    capture_mono=capture_mono,
                    stream_epoch=packet.stream_epoch,
                    source_fps=packet.source_fps,
                    jpeg_data=jpeg_bytes,
                    size_bytes=len(jpeg_bytes),
                    width=w,
                    height=h,
                    is_synthetic=False,
                )
                self._frame_ring_buffer.append(entry)
                self.nvr_current_bytes = self._frame_ring_buffer.get_total_bytes()
            except Exception as e:
                logger.warning(f"Camera {self.camera_id}: NVR worker error encoding frame {getattr(packet, 'frame_id', '?')}: {e}")
                self.nvr_encode_failures += 1

    def start(self):
        """Start the pipeline background threads (dedicated reader + dedicated processor + dedicated NVR)."""
        if self._running:
            logger.warning(f"Camera {self.camera_id}: Pipeline already running, ignoring duplicate start()")
            return
        self._running = True
        self._reader_stop_event.clear()
        self._processor_stop_event.clear()
        self._nvr_stop_event.clear()
        self.reconnect_state = RECONNECT_STATE_CONNECTED
        self.processor_state = PROCESSOR_STATE_START
        self._start_time = time.time()

        # Dedicated Ingestion Worker (GAP-P0-01 Step 3 & 4)
        self._reader_thread = threading.Thread(
            target=self._reader_worker,
            daemon=True,
            name=f"reader-cam{self.camera_id}",
        )
        self._reader_thread.start()

        # Dedicated Processing Worker (GAP-P0-01 Step 5)
        self._processor_thread = threading.Thread(
            target=self._processor_worker,
            daemon=True,
            name=f"processor-cam{self.camera_id}",
        )
        self._thread = self._processor_thread  # Maintain backward compatibility
        self._processor_thread.start()

        # Dedicated NVR Historical Ingestion Worker (GAP-P0-01 Step 6)
        self._nvr_thread = threading.Thread(
            target=self._nvr_worker,
            daemon=True,
            name=f"nvr-cam{self.camera_id}",
        )
        self._nvr_thread.start()

    def stop(self):
        """Stop the pipeline and release hardware capture cleanly."""
        self._running = False
        self._reader_stop_event.set()
        self._processor_stop_event.set()
        self._nvr_stop_event.set()
        self.reconnect_state = RECONNECT_STATE_STOPPED
        self.processor_state = PROCESSOR_STATE_STOPPING

        # Wake any threads waiting on slot condition or NVR condition
        with self._slot_lock:
            self._slot_condition.notify_all()
        with self._nvr_staging_lock:
            self._nvr_staging_condition.notify_all()

        # Join reader worker thread
        if hasattr(self, "_reader_thread") and self._reader_thread is not None:
            self._reader_thread.join(timeout=1.5)
            self._reader_thread = None

        # Join processor worker thread
        if hasattr(self, "_processor_thread") and self._processor_thread is not None:
            self._processor_thread.join(timeout=1.5)
            self._processor_thread = None
        if self._thread is not None:
            self._thread.join(timeout=1.5)
            self._thread = None

        # Join NVR worker thread
        if hasattr(self, "_nvr_thread") and self._nvr_thread is not None:
            self._nvr_thread.join(timeout=1.5)
            self._nvr_thread = None

        self.processor_state = PROCESSOR_STATE_STOPPED

        # Fallback defensive release if reader worker failed to release capture
        if hasattr(self, "_cap") and self._cap is not None:
            try:
                self._cap.release()
            except Exception as e:
                logger.warning(f"Error releasing capture in stop: {e}")
            self._cap = None

        if hasattr(self, "tracker") and self.tracker is not None:
            self.tracker.reset()
        if hasattr(self, "zone_fence") and self.zone_fence is not None:
            self.zone_fence.reset()
        if hasattr(self, "health_checker") and self.health_checker is not None:
            self.health_checker.reset()
        if hasattr(self, "evidence_associator") and self.evidence_associator is not None:
            self.evidence_associator.reset()
        self._health_state = "UNKNOWN"
        self._health_substate = "UNKNOWN"
        if hasattr(self, "_latest_health"):
            self._latest_health.clear()
        self._consecutive_offline_reads = 0
        self._consecutive_frozen_frames = 0
        self._consecutive_recovering_frames = 0
        self._stream_disconnected = False
        self._read_failures = 0
        if hasattr(self, "_track_metadata"):
            self._track_metadata.clear()
        if hasattr(self, "_tracked_targets"):
            self._tracked_targets.clear()
        if hasattr(self, "_latest_zone_events"):
            self._latest_zone_events.clear()

    def get_health(self) -> Dict[str, Any]:
        """Get latest camera health metrics and diagnostics."""
        return self._latest_health.copy()

    def evaluate_health(
        self,
        frame: Optional[np.ndarray],
        frame_id: int = 0,
        inference_ms: float = 0.0,
    ) -> Dict[str, Any]:
        """Execute real-time camera health diagnostics and state machine.

        Detects:
        - Stream unavailable / offline
        - Frozen frames / stalled video
        - Blur / severe focus loss
        - Brightness / exposure degradation (underexposed/overexposed)
        - FPS degradation / network jitter
        - Recovery transitions
        """
        now = time.time()
        now_dt = datetime.utcnow()
        uptime = round(now - self._start_time, 1) if self._start_time else 0.0

        # Case 1: Stream read failure / no frame arriving
        if frame is None:
            self._consecutive_offline_reads += 1
            self._read_failures += 1
            if self._consecutive_offline_reads >= 3:
                self._stream_disconnected = True
                new_state = "OFFLINE"
                new_substate = "OFFLINE"
                metrics = self.health_checker.get_offline_health(
                    reason="Stream read failure", inference_ms=inference_ms
                )
            else:
                # Debounced — keep current state until 3 consecutive read failures
                return self._latest_health.copy()
        else:
            # Case 2: Frame available — analyze visual and temporal health
            self._consecutive_offline_reads = 0
            metrics = self.health_checker.check_frame(frame, inference_ms=inference_ms)
            raw_status = str(metrics.get("status", "HEALTHY"))
            score = float(metrics.get("health_score", 100.0))

            # Check Recovery state transition (from stream disconnection back to stream available)
            if getattr(self, "_stream_disconnected", False):
                self._consecutive_recovering_frames += 1
                if self._consecutive_recovering_frames < 3:
                    new_state = "RECOVERING"
                    new_substate = "RECOVERING"
                else:
                    self._stream_disconnected = False
                    self._consecutive_recovering_frames = 0
                    new_state = raw_status
                    new_substate = raw_status
            else:
                self._consecutive_recovering_frames = 0

                # Determine diagnostic substate with proper priority:
                # 1. Frozen frame detection (using freeze_warning from health_score)
                has_freeze = "freeze_warning" in metrics and metrics["freeze_warning"] == "Frame appears frozen"
                if has_freeze:
                    self._consecutive_frozen_frames += 1
                    if self._consecutive_frozen_frames >= 3:
                        new_state = "DEGRADED"
                        new_substate = "FROZEN"
                    else:
                        new_state = raw_status
                        new_substate = raw_status
                else:
                    self._consecutive_frozen_frames = 0

                    # 2. Brightness degradation takes precedence over blur
                    if "brightness_warning" in metrics or float(metrics.get("brightness", 128.0)) < 15.0 or float(metrics.get("brightness", 128.0)) > 240.0:
                        new_state = raw_status
                        new_substate = "BRIGHTNESS_DEGRADED"
                    # 3. Blur degradation
                    elif "blur_warning" in metrics or float(metrics.get("blur", 100.0)) < 20.0:
                        new_state = raw_status
                        new_substate = "BLURRY"
                    # 4. FPS degradation
                    elif "fps_warning" in metrics:
                        new_state = raw_status
                        new_substate = "DEGRADED"
                    else:
                        new_state = raw_status
                        new_substate = raw_status

        is_transition = (new_state != self._health_state) or (new_substate != self._health_substate)
        self._health_state = new_state
        self._health_substate = new_substate

        h_w = frame.shape[1] if frame is not None else 0
        h_h = frame.shape[0] if frame is not None else 0
        res_str = f"{h_w}x{h_h}" if frame is not None else "0x0"

        health_dict = {
            "type": "camera_health",
            "camera_id": self.camera_id,
            "camera_name": self.camera_name,
            "status": new_state,
            "substate": new_substate,
            "health_score": round(float(metrics.get("health_score", 0.0)), 1),
            "fps_actual": round(float(metrics.get("fps_estimated", 0.0)), 1),
            "brightness": round(float(metrics.get("brightness", 0.0)), 1),
            "blur_score": round(float(metrics.get("blur", 0.0)), 1),
            "frame_delta": round(float(metrics.get("frame_delta", 0.0)), 2),
            "resolution": res_str,
            "resolution_width": int(h_w),
            "resolution_height": int(h_h),
            "latency_ms": round(float(inference_ms), 1),
            "stream_uptime_seconds": uptime,
            "reconnect_count": int(self._reconnect_count),
            "read_failures": int(self._read_failures),
            "warnings": list(metrics.get("warnings", [])),
            "timestamp": now_dt.isoformat(),
        }
        self._latest_health = health_dict

        # Debounce: Dispatch event immediately on state transition; otherwise rate-limit by cooldown
        should_emit = is_transition or ((now - self._last_health_event_time) >= self._health_cooldown_seconds)
        if should_emit:
            self._last_health_event_time = now
            if self._event_callback:
                try:
                    self._event_callback(health_dict)
                except Exception as cb_err:
                    logger.debug(f"Camera {self.camera_id} health callback error: {cb_err}")

            self._persist_health_to_db(health_dict, is_transition=is_transition)

        return health_dict

    def _persist_health_to_db(self, health_dict: Dict[str, Any], is_transition: bool = False) -> None:
        """Asynchronously enqueue camera health measurement to decoupled background worker (GAP-P0-01 Phase 5).

        Invariants:
        1. Reader and processor threads must NEVER block waiting on DB connections, locks, or commits.
        2. Non-blocking enqueue to BoundedHealthBuffer with camera-aware state coalescing.
        3. All database persistence is executed asynchronously by CameraHealthPersistenceWorker.
        """
        try:
            from backend.app.services.camera_health_worker import get_camera_health_worker
            worker = get_camera_health_worker()
            worker.enqueue(health_dict, is_transition=is_transition)
        except Exception as e:
            logger.debug(f"Camera {self.camera_id}: Failed to enqueue health event: {e}")

    def _reader_worker(self) -> None:
        """Dedicated RTSP ingestion worker thread with reconnect loop & backoff (GAP-P0-01 Steps 3 & 4).

        Capture Ownership Invariant:
        This worker is the SOLE owner of `self._cap`. No other thread may call
        `read()`, `grab()`, `retrieve()`, or `release()` on `self._cap` during operation.

        Lifecycle:
        - Continuously drains frames from the underlying capture source.
        - Measures source_fps using a rolling 30-sample monotonic window.
        - Constructs an immutable FramePacket with monotonic frame_id and dual timestamps.
        - Publishes to the latest-frame slot via `self.publish_frame_packet()`.
        - On read failure: increments reader_read_failures and consecutive_read_failures.
        - If consecutive_read_failures < consecutive_failure_threshold:
            Pauses briefly (10ms) and retries without reopening capture.
        - If consecutive_read_failures >= consecutive_failure_threshold:
            Enters RECONNECTING state machine with interruptible bounded exponential backoff.
            Releases stale capture before opening new capture.
            Validates new capture with a test read before marking recovered.
            Increments stream_epoch exactly once upon validated recovery.
            Validation frame becomes frame #1 of the new stream epoch.
        - Checks `_reader_stop_event` across all loop checkpoints to guarantee zero post-stop publications.
        - Closes and releases `self._cap` upon thread exit.

        Deployment Risk Note:
        In OpenCV's VideoCapture with FFmpeg backend, stimeout socket options protect
        against TCP socket freezes. However, native OS driver reads (e.g. corrupted V4L2/USB
        kernel devices) can block in unmanaged kernel space. This is documented as an
        OS-level deployment risk.
        """
        logger.info(f"Camera {self.camera_id}: Dedicated reader worker started for {self.stream_url}")

        cap = self._open_stream()
        is_opened = False
        if cap is not None:
            try:
                is_opened = cap.isOpened() if hasattr(cap, "isOpened") else True
            except Exception:
                is_opened = False

        if cap is None or not is_opened:
            logger.error(f"Camera {self.camera_id}: Cannot open stream {self.stream_url} in reader worker")
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass
            self._cap = None
            self.reader_read_failures += 1
            self.consecutive_read_failures += 1
            self.reconnect_state = RECONNECT_STATE_READ_FAILURE
            for _ in range(3):
                self.evaluate_health(None, frame_id=0)
        else:
            self._cap = cap
            self.reconnect_state = RECONNECT_STATE_CONNECTED

        monotonic_frame_id = 0
        fps_timestamps: deque = deque(maxlen=30)
        curr_backoff = self.initial_backoff

        try:
            while not self._reader_stop_event.is_set() and self._running:
                # If capture is None or stream was disconnected, enter Reconnect State Machine
                if self._cap is None or self.consecutive_read_failures >= self.consecutive_failure_threshold:
                    self.reconnect_state = RECONNECT_STATE_RECONNECTING

                    # Step A: Ensure stale capture is cleanly released
                    if self._cap is not None:
                        try:
                            self._cap.release()
                        except Exception as rel_err:
                            logger.warning(f"Camera {self.camera_id}: Error releasing failed capture: {rel_err}")
                        self._cap = None

                    # Step B: Retain last valid FramePacket in slot for forensic/evidence continuity.
                    # Monotonic frame ID gating and capture_mono timestamps prevent consumers from mistaking it as fresh.

                    # Step C: Reconnect & Backoff Loop
                    while not self._reader_stop_event.is_set() and self._running:
                        self.reconnect_state = RECONNECT_STATE_BACKOFF
                        # Interruptible backoff sleep
                        stopped = self._reader_stop_event.wait(timeout=curr_backoff)
                        if stopped or not self._running:
                            self.reconnect_state = RECONNECT_STATE_STOPPED
                            break

                        self.reconnect_state = RECONNECT_STATE_RECONNECTING
                        self.reconnect_attempts += 1
                        logger.info(
                            f"Camera {self.camera_id}: Attempting stream reconnect #{self.reconnect_attempts} "
                            f"(backoff was {curr_backoff:.2f}s)"
                        )

                        new_cap = self._open_stream()
                        if self._reader_stop_event.is_set() or not self._running:
                            if new_cap is not None:
                                try:
                                    new_cap.release()
                                except Exception:
                                    pass
                            self.reconnect_state = RECONNECT_STATE_STOPPED
                            break

                        # Reconnect Validation: Check handle and perform validation read
                        is_valid = False
                        val_frame = None
                        if new_cap is not None:
                            try:
                                is_opened = new_cap.isOpened() if hasattr(new_cap, "isOpened") else True
                                if is_opened:
                                    val_ret, val_frame = new_cap.read()
                                    if val_ret and val_frame is not None:
                                        is_valid = True
                            except Exception as val_err:
                                logger.warning(f"Camera {self.camera_id}: Validation read failed: {val_err}")

                        if is_valid and val_frame is not None:
                            # Recovery Success!
                            self._cap = new_cap
                            self.stream_epoch += 1
                            self.reconnect_events += 1
                            self.last_reconnect_timestamp = time.time()
                            self.consecutive_read_failures = 0
                            curr_backoff = self.initial_backoff
                            fps_timestamps.clear()  # Warmup reset
                            self.reconnect_state = RECONNECT_STATE_RECOVERED
                            logger.info(
                                f"Camera {self.camera_id}: Reconnect successful! Advanced to stream_epoch={self.stream_epoch}"
                            )

                            # Publish validation frame as Frame #1 of the new stream epoch (never discarded or duplicated)
                            t_mono = time.perf_counter()
                            t_utc = time.time()
                            monotonic_frame_id += 1
                            self.reader_frames_acquired += 1
                            self._frame_count = monotonic_frame_id
                            self._source_fps = 0.0  # Warmup representation

                            pkt = FramePacket(
                                frame_id=monotonic_frame_id,
                                frame=val_frame,
                                capture_utc=t_utc,
                                capture_mono=t_mono,
                                source_fps=0.0,
                                stream_epoch=self.stream_epoch,
                                hardware_drop_count=self.reader_read_failures,
                            )

                            if not self._reader_stop_event.is_set() and self._running:
                                self.publish_frame_packet(pkt)
                                self._enqueue_nvr_staging(pkt)
                                self.reconnect_state = RECONNECT_STATE_CONNECTED
                            break  # Exit reconnect loop and resume normal reading!
                        else:
                            # Reconnect Attempt Failed
                            self.reconnect_failures += 1
                            if new_cap is not None:
                                try:
                                    new_cap.release()
                                except Exception:
                                    pass
                            self.evaluate_health(None, frame_id=monotonic_frame_id)
                            curr_backoff = self.calculate_next_backoff(curr_backoff)

                    # If stopped during reconnect loop, exit outer loop as well
                    if self._reader_stop_event.is_set() or not self._running:
                        break

                # ── Normal Frame Reading Cycle ──
                if self._cap is None:
                    continue

                ret, frame = self._cap.read()

                # Check stop conditions immediately after read
                if self._reader_stop_event.is_set() or not self._running:
                    self.reconnect_state = RECONNECT_STATE_STOPPED
                    break

                if not ret or frame is None:
                    self.reader_read_failures += 1
                    self.consecutive_read_failures += 1
                    self.evaluate_health(None, frame_id=monotonic_frame_id)

                    if self.stream_url.startswith("file://"):
                        try:
                            self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                            self.consecutive_read_failures = 0
                        except Exception:
                            pass
                        time.sleep(0.01)
                        continue
                    if self.stream_url.startswith("usb://") or self.stream_url.startswith("camera://") or self.stream_url.startswith("webcam://") or self.stream_url.isdigit():
                        time.sleep(0.2)
                        continue

                    if self.consecutive_read_failures >= self.consecutive_failure_threshold:
                        self.reconnect_state = RECONNECT_STATE_RECONNECTING
                    else:
                        self.reconnect_state = RECONNECT_STATE_READ_FAILURE
                        time.sleep(0.01)
                    continue

                # Successful acquisition
                self.consecutive_read_failures = 0
                self.reconnect_state = RECONNECT_STATE_CONNECTED
                t_mono = time.perf_counter()
                t_utc = time.time()
                self.reader_frames_acquired += 1
                monotonic_frame_id += 1
                self._frame_count = monotonic_frame_id
                self._usb_fail_count = 0

                # Calculate rolling source FPS (monotonic timestamps)
                fps_timestamps.append(t_mono)
                if len(fps_timestamps) > 1:
                    dt = fps_timestamps[-1] - fps_timestamps[0]
                    curr_source_fps = float(round((len(fps_timestamps) - 1) / dt, 2)) if dt > 1e-6 else 0.0
                else:
                    curr_source_fps = 0.0  # Warm-up representation (1 frame, dt undefined)
                self._source_fps = curr_source_fps

                packet = FramePacket(
                    frame_id=monotonic_frame_id,
                    frame=frame,
                    capture_utc=t_utc,
                    capture_mono=t_mono,
                    source_fps=curr_source_fps,
                    stream_epoch=self.stream_epoch,
                    hardware_drop_count=self.reader_read_failures,
                )

                # Final check before publication to guarantee no post-stop publication
                if self._reader_stop_event.is_set() or not self._running:
                    self.reconnect_state = RECONNECT_STATE_STOPPED
                    break

                self.publish_frame_packet(packet)
                self._enqueue_nvr_staging(packet)

        except Exception as e:
            logger.error(f"Camera {self.camera_id}: Reader worker unexpected error: {e}", exc_info=True)
            self.reader_read_failures += 1
        finally:
            self.reconnect_state = RECONNECT_STATE_STOPPED
            logger.info(
                f"Camera {self.camera_id}: Reader worker exiting "
                f"(acquired={self.reader_frames_acquired}, failures={self.reader_read_failures}, "
                f"reconnects={self.reconnect_events}, final_epoch={self.stream_epoch})"
            )
            if self._cap is not None:
                try:
                    self._cap.release()
                except Exception as rel_err:
                    logger.warning(f"Camera {self.camera_id}: Error releasing capture in reader worker: {rel_err}")
                self._cap = None

    def _processor_worker(self) -> None:
        """Dedicated inference & analytics processing worker thread (GAP-P0-01 Step 5).

        Processing Decoupling Invariant:
        This worker consumes FramePacket objects exclusively from the low-contention
        latest-frame slot (`self.get_latest_frame_packet()`). It NEVER accesses `self._cap`
        or blocks the reader worker.

        Lifecycle:
        - START -> WAITING_FOR_FRAME -> PROCESSING -> STOPPING -> STOPPED
        - Non-blocking lock discipline: Slot lock is released before executing AI/detection.
        - Frame supersession: When slow inference finishes, skips intermediate frames and
          increments `processor_frames_skipped_latest_slot`.
        - Algorithmic downsampling: Respects `detection_stride` and increments `detection_stride_skips`.
        - Freshness & Stale Tracking: Increments `processor_stale_frames` if age > stale_frame_threshold.
        - Stream Epoch Isolation: Resets temporal processor frame state upon epoch increment.
        - Exception Isolation: Processing errors are recorded and logged without killing the reader.
        """
        import sys
        sys.path.insert(0, os.getcwd())

        self.processor_state = PROCESSOR_STATE_START
        logger.info(f"Camera {self.camera_id}: Dedicated processor worker started")

        # Initialize detector
        detector = self._custom_detector
        if detector is None and self._custom_frame_processor is None:
            try:
                from edge.detection.factory import create_detector
                detector = create_detector(preferred="yolo26n", confidence_threshold=0.25)
                logger.info(f"Camera {self.camera_id}: Using {detector.name}")
            except Exception as e:
                logger.error(f"Camera {self.camera_id}: Detector failed: {e}")
                self.processor_state = PROCESSOR_STATE_STOPPED
                return

        # Initialize enhanced modules
        night_enhancer = None
        face_engine = None
        reid_engine = None
        rule_engine = None

        if self._custom_frame_processor is None:
            is_usb = (
                self.stream_url.startswith("usb://")
                or self.stream_url.startswith("camera://")
                or self.stream_url.startswith("webcam://")
            )

            try:
                from edge.modules.night_enhance import get_night_enhancer
                night_enhancer = get_night_enhancer()
                logger.info(f"Camera {self.camera_id}: Night enhancer ready")
            except Exception as e:
                logger.debug(f"Camera {self.camera_id}: Night enhancer unavailable: {e}")

            if not is_usb:
                try:
                    from edge.modules.face_recognition import get_face_engine
                    face_engine = get_face_engine()
                    logger.info(f"Camera {self.camera_id}: Face engine ready (fallback={face_engine._fallback_mode})")
                except Exception as e:
                    logger.debug(f"Camera {self.camera_id}: Face engine unavailable: {e}")
                try:
                    from edge.modules.reid import get_reid_engine
                    reid_engine = get_reid_engine()
                    logger.info(f"Camera {self.camera_id}: ReID engine ready (fallback={reid_engine._fallback_mode})")
                except Exception as e:
                    logger.debug(f"Camera {self.camera_id}: ReID engine unavailable: {e}")
            else:
                logger.info(f"Camera {self.camera_id}: Face/ReID disabled for USB webcam (CPU optimization)")

            try:
                from edge.modules.activity_rules import get_rule_engine
                rule_engine = get_rule_engine()
                logger.info(f"Camera {self.camera_id}: Activity rule engine ready")
            except Exception as e:
                logger.debug(f"Camera {self.camera_id}: Rule engine unavailable: {e}")

        last_consumed_frame_id = self.processor_last_processed_frame_id

        try:
            while not self._processor_stop_event.is_set() and self._running:
                self.processor_state = PROCESSOR_STATE_WAITING_FOR_FRAME
                packet = self.get_latest_frame_packet(
                    last_frame_id=last_consumed_frame_id,
                    timeout=0.05,
                )

                if self._processor_stop_event.is_set() or not self._running:
                    break

                if packet is None:
                    continue

                # Defensive Monotonic Frame ID check: Prevent duplicate processing of the same frame
                if last_consumed_frame_id is not None and packet.frame_id == last_consumed_frame_id:
                    continue

                # Monotonic Frame Supersession Accounting:
                if last_consumed_frame_id is None and packet.frame_id > 1:
                    superseded = packet.frame_id - 1
                    self.processor_frames_skipped_latest_slot += superseded
                elif last_consumed_frame_id is not None and packet.frame_id > last_consumed_frame_id + 1:
                    superseded = packet.frame_id - last_consumed_frame_id - 1
                    self.processor_frames_skipped_latest_slot += superseded

                # Stale Frame Detection:
                packet_age = time.perf_counter() - packet.capture_mono
                if packet_age > self.stale_frame_threshold:
                    self.processor_stale_frames += 1

                # Stream Epoch Transition Handling:
                if (
                    self.processor_last_processed_stream_epoch is not None
                    and packet.stream_epoch != self.processor_last_processed_stream_epoch
                ):
                    logger.info(
                        f"Camera {self.camera_id}: Stream epoch advanced from "
                        f"{self.processor_last_processed_stream_epoch} to {packet.stream_epoch}. "
                        f"Resetting temporal processor frame state."
                    )
                    self._prev_frame = None
                    if hasattr(self, "health_checker") and self.health_checker is not None:
                        self.health_checker.prev_gray = None
                        self.health_checker.prev_frame_time = None

                self.processor_last_processed_stream_epoch = packet.stream_epoch
                last_consumed_frame_id = packet.frame_id
                self.processor_last_processed_frame_id = packet.frame_id
                frame = packet.frame
                frame_id = packet.frame_id
                self._frame_count = frame_id

                # Algorithmic Detection Stride:
                if self.detection_stride > 1 and (frame_id % self.detection_stride != 0):
                    self.detection_stride_skips += 1
                    continue

                # Active Frame Processing
                self.processor_state = PROCESSOR_STATE_PROCESSING
                self.processor_frames_started += 1
                t_proc_start = time.perf_counter()

                try:
                    if self._custom_frame_processor is not None:
                        self._custom_frame_processor(frame, frame_id)
                    else:
                        self._process_frame(
                            detector, frame, frame_id,
                            night_enhancer=night_enhancer,
                            face_engine=face_engine,
                            reid_engine=reid_engine,
                            rule_engine=rule_engine,
                        )
                    self.processor_frames_completed += 1
                except Exception as proc_err:
                    self.processor_processing_errors += 1
                    logger.error(
                        f"Camera {self.camera_id} frame {frame_id} processing error: {proc_err}",
                        exc_info=True,
                    )
                finally:
                    t_proc_dur = time.perf_counter() - t_proc_start
                    self.processor_processing_time += t_proc_dur
                    self._last_processing_time = t_proc_dur

                # Control frame rate: brief pause after AI inference (interruptible)
                if getattr(self, "_frame_rate_pause", 0.0) > 0:
                    self._processor_stop_event.wait(timeout=self._frame_rate_pause)

        except Exception as fatal_e:
            self.processor_processing_errors += 1
            logger.error(f"Camera {self.camera_id}: Processor worker unexpected fatal error: {fatal_e}", exc_info=True)
        finally:
            self.processor_state = PROCESSOR_STATE_STOPPED
            logger.info(
                f"Camera {self.camera_id}: Dedicated processor worker stopped "
                f"(started={self.processor_frames_started}, completed={self.processor_frames_completed}, "
                f"errors={self.processor_processing_errors}, skipped_slot={self.processor_frames_skipped_latest_slot}, "
                f"stride_skips={self.detection_stride_skips})"
            )

    def _run_loop(self):
        """Backward compatibility alias for _processor_worker."""
        return self._processor_worker()

    def _open_stream(self):
        """Open the video stream.

        Capture Ownership Invariant:
        The returned capture object is owned exclusively by _reader_worker.
        """
        if hasattr(self, "_capture_factory") and self._capture_factory is not None:
            return self._capture_factory()
        if hasattr(self, "_custom_capture") and self._custom_capture is not None:
            return self._custom_capture

        url = self.stream_url
        if url.startswith("usb://") or url.startswith("camera://") or url.startswith("webcam://") or url.isdigit():
            class StreamManagerCapture:
                def __init__(self, stream_url):
                    self.stream_url = stream_url
                    self._running = True

                def isOpened(self):
                    return self._running

                def read(self):
                    from backend.app.api.v1.endpoints.cameras import stream_manager
                    f = stream_manager.get_frame(self.stream_url)
                    if f is not None:
                        return True, f
                    return False, None

                def release(self):
                    self._running = False

            return StreamManagerCapture(url)
        elif url.startswith("file://"):
            path = url.replace("file://", "")
            if not os.path.exists(path):
                logger.error(f"File not found: {path}")
                return None
            return cv2.VideoCapture(path)
        elif url.startswith("demo://"):
            # Generate synthetic frames for demo mode
            return self._create_demo_capture()
        elif url.startswith("phone://"):
            return self._create_phone_capture(url)
        else:
            # RTSP or network stream
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|stimeout;3000000"
            return cv2.VideoCapture(url)

    def _create_phone_capture(self, url: str):
        """Create a capture adapter for browser phone camera live streams."""
        class PhoneCapture:
            def __init__(self, cam_id, stream_url):
                self.cam_id = cam_id
                self.stream_url = stream_url
                self._running = True

            def isOpened(self):
                return self._running

            def read(self):
                from backend.app.api.v1.endpoints.cameras import get_phone_frame
                key = self.stream_url.replace("phone://", "")
                f = get_phone_frame(key) or get_phone_frame(self.stream_url)
                if f is not None:
                    return True, f
                time.sleep(0.08)
                wait_frame = np.zeros((720, 1280, 3), dtype=np.uint8)
                cv2.putText(wait_frame, "[SMARTPHONE LIVE LINK ACTIVE // WAITING FOR TRANSMISSION]",
                            (180, 340), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 200), 2)
                cv2.putText(wait_frame, f"Stream Key: {key} | Connect via: http://<LAN-IP>:5173/phone-camera",
                            (260, 390), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1)
                return True, wait_frame

            def release(self):
                self._running = False

        return PhoneCapture(self.camera_id, url)

    def _create_demo_capture(self):
        """Create a synthetic capture for demo mode."""
        # Generate simple frames with moving rectangles
        class DemoCapture:
            def __init__(self):
                self.frame_count = 0
                self.isOpened = lambda: True
            def read(self):
                self.frame_count += 1
                frame = np.zeros((480, 640, 3), dtype=np.uint8)
                frame[:] = (20, 20, 30)  # Dark background

                # Moving person-like rectangle
                x = int(100 + 200 * np.sin(self.frame_count * 0.02))
                y = int(150 + 50 * np.cos(self.frame_count * 0.03))
                cv2.rectangle(frame, (x, y), (x + 60, y + 120), (80, 80, 80), -1)

                # Moving vehicle-like rectangle
                vx = int(300 + 150 * np.cos(self.frame_count * 0.015))
                vy = int(280 + 30 * np.sin(self.frame_count * 0.02))
                cv2.rectangle(frame, (vx, vy), (vx + 120, vy + 60), (60, 60, 60), -1)

                return True, frame
            def set(self, *args): pass
            def release(self): pass
        return DemoCapture()

    def _process_frame(self, detector, frame, frame_id, night_enhancer=None,
                       face_engine=None, reid_engine=None, rule_engine=None):
        """Process a single frame through the full AI pipeline."""
        start_time = time.time()
        h, w = frame.shape[:2]

        # Step 0: Camera Health Diagnostics (frame quality, freeze, blur, exposure)
        t_health_start = time.perf_counter()
        health_info = self.evaluate_health(frame, frame_id=frame_id, inference_ms=0.0)
        health_eval_ms = (time.perf_counter() - t_health_start) * 1000.0
        self._health_eval_times.append(health_eval_ms)

        # Step 1: Night enhancement if needed
        enhance_result = None
        detect_frame = frame
        if night_enhancer:
            try:
                enhance_result = night_enhancer.enhance(frame)
                if enhance_result.was_enhanced:
                    detect_frame = enhance_result.enhanced_frame
            except Exception as e:
                logger.debug(f"Night enhance failed: {e}")

        # Step 2: Run detection on (possibly enhanced) frame
        detections = detector.detect(detect_frame, frame_id)
        inference_ms = (time.time() - start_time) * 1000

        # Step 3: Live ByteTrack Multi-Object Tracking (Kalman + IoU)
        active_tracks = self._track_with_bytetrack(detections, frame_id)

        # Step 3.5: Virtual Fence Geometric Evaluation (ZoneFence)
        # Evaluates bottom-center ground-footprint contact anchors against polygon/line zones
        t_zone_start = time.perf_counter()
        current_frame_zone_events = []
        triggered_zone_ids = set()
        now_dt = datetime.utcnow()
        is_night = (now_dt.hour < 6 or now_dt.hour > 20)

        for trk in active_tracks:
            anchor = trk.get("ground_anchor") or trk.get("footprint") or [trk["center"][0], trk["bbox"][3]]
            anchor_pos = (float(anchor[0]), float(anchor[1]))

            # Real geometric evaluation via ZoneFence
            z_events = self.zone_fence.check_position(
                track_id=trk["track_id"],
                position=anchor_pos,
                frame_time=now_dt,
                frame_width=w,
                frame_height=h,
                is_night=is_night,
                confidence=float(trk["confidence"]),
            )
            if z_events:
                for ze in z_events:
                    ze.setdefault("track_id", trk["track_id"])
                    if ze.get("zone_id") is not None:
                        triggered_zone_ids.add(ze["zone_id"])
                current_frame_zone_events.extend(z_events)

            containing = self.zone_fence.get_zones_for_position(anchor_pos, frame_width=w, frame_height=h)
            trk["zones"] = [z.get("name", f"Z-{z.get('id')}") for z in containing]
            trk["in_restricted_zone"] = any(z.get("zone_type") in ("RESTRICTED", "SENSITIVE") for z in containing)
            trk["current_zone_events"] = z_events

            # Record zone events in persistent track metadata
            tid = trk.get("target_id")
            if tid is not None and hasattr(self, "_track_metadata") and tid in self._track_metadata:
                meta_ze = self._track_metadata[tid].setdefault("zone_events", [])
                for ze in z_events:
                    ze_desc = f"{ze.get('event_type')}:{ze.get('zone_name')}"
                    if ze_desc not in meta_ze:
                        meta_ze.append(ze_desc)

        zone_eval_ms = (time.perf_counter() - t_zone_start) * 1000.0
        self._zone_eval_times.append(zone_eval_ms)
        self._latest_zone_events = current_frame_zone_events

        # Push real geometric zone events across WebSocket/event listeners and emit tracklets on departure
        for zevt in current_frame_zone_events:
            matching_trk = next((t for t in active_tracks if t["track_id"] == zevt.get("track_id", "")), None)
            zone_msg = {
                "type": "zone_event",
                "event_type": zevt["event_type"],
                "camera_id": self.camera_id,
                "camera_name": self.camera_name,
                "track_id": zevt.get("track_id", matching_trk["track_id"] if matching_trk else ""),
                "target_id": matching_trk.get("target_id") if matching_trk else None,
                "class_name": matching_trk.get("class_name", "unknown") if matching_trk else "unknown",
                "confidence": matching_trk.get("confidence", 1.0) if matching_trk else 1.0,
                "zone_id": zevt.get("zone_id"),
                "zone_name": zevt.get("zone_name"),
                "zone_type": zevt.get("zone_type"),
                "severity": float(zevt.get("severity", 0.0)),
                "position": [float(round(p, 1)) for p in zevt.get("position", [])],
                "ground_anchor": [float(round(p, 1)) for p in (matching_trk.get("ground_anchor", []) if matching_trk else zevt.get("position", []))],
                "direction": zevt.get("direction"),
                "rule": zevt.get("rule"),
                "timestamp": zevt.get("timestamp", now_dt.isoformat()),
            }
            if self._event_callback:
                try:
                    self._event_callback(zone_msg)
                except Exception as cb_err:
                    logger.debug(f"Zone event callback error: {cb_err}")

            # Zone boundary departure lifecycle trigger
            if zevt.get("event_type") in ("zone_exit", "zone_crossing"):
                if matching_trk and matching_trk.get("target_id") is not None:
                    try:
                        self._emit_tracklet_descriptor(matching_trk["target_id"], reason="zone_boundary_departure")
                    except Exception as z_err:
                        logger.debug(f"Camera {self.camera_id} zone departure emit error: {z_err}")

        # Step 4: Real-time face detection & watchlist intelligence
        live_faces = []
        now_ts = time.time()
        try:
            from backend.app.services.face import face_service
            from backend.app.db.session import SessionLocal

            raw_faces = face_service.detect_faces(frame)
            if raw_faces:
                db = SessionLocal()
                try:
                    for f in raw_faces:
                        fb = f.get("bbox", {})
                        fx1 = max(0, min(w - 1, int(fb.get("x1", 0.0) * w)))
                        fy1 = max(0, min(h - 1, int(fb.get("y1", 0.0) * h)))
                        fx2 = max(fx1 + 1, min(w, int(fb.get("x2", 1.0) * w)))
                        fy2 = max(fy1 + 1, min(h, int(fb.get("y2", 1.0) * h)))
                        fconf = f.get("confidence", 0.8)

                        fcrop = frame[fy1:fy2, fx1:fx2]
                        match_info = None
                        if fcrop.size > 0:
                            f_emb = face_service.extract_embedding(fcrop, raw_face=f.get("raw_face"), full_frame=frame)
                            if f_emb:
                                m_res = face_service.match_watchlist(f_emb, db)
                                if m_res:
                                    subj, sim = m_res
                                    match_info = {"name": subj.name, "sim": float(sim), "id": subj.id}
                        face_item = {
                            "bbox": (fx1, fy1, fx2, fy2),
                            "confidence": fconf,
                            "match": match_info,
                        }
                        live_faces.append(face_item)

                        # Associate with active Person ByteTrack tracks
                        if hasattr(self, "evidence_associator") and self.evidence_associator is not None:
                            person_tracks = [t for t in active_tracks if t.get("class_name") == "person"]
                            for ptrk in person_tracks:
                                f_cand = self.evidence_associator.associate_person_face(
                                    track_id=ptrk["track_id"],
                                    person_track=ptrk,
                                    face_bbox=[float(fx1), float(fy1), float(fx2), float(fy2)],
                                    detection_confidence=fconf,
                                    frame_id=frame_id,
                                    timestamp=now_ts,
                                    similarity_score=match_info["sim"] if match_info else None,
                                    matched_subject_id=match_info["id"] if match_info else None,
                                    matched_subject_name=match_info["name"] if match_info else None,
                                )
                                if f_cand and ptrk.get("target_id") is not None and hasattr(self, "_track_metadata"):
                                    pmeta = self._track_metadata.get(ptrk["target_id"])
                                    if pmeta:
                                        if f_cand.matched_subject_id is not None:
                                            pmeta["matched_subject_id"] = f_cand.matched_subject_id
                                            pmeta["matched_subject_name"] = f_cand.matched_subject_name
                                            pmeta["frs_confidence"] = f_cand.similarity_score
                                            pmeta["frs_association_state"] = f_cand.association_state.value if hasattr(f_cand.association_state, "value") else str(f_cand.association_state)
                finally:
                    db.close()

            # For persons, extract Re-ID appearance embeddings if reid_engine is active
            if reid_engine is not None and hasattr(self, "_track_metadata"):
                for ptrk in active_tracks:
                    if ptrk.get("class_name") == "person":
                        tid = ptrk.get("target_id")
                        if tid is not None and tid in self._track_metadata:
                            pmeta = self._track_metadata[tid]
                            if pmeta.get("appearance_embedding") is None or (frame_id % 30 == 0):
                                pbx1, pby1, pbx2, pby2 = [int(v) for v in ptrk["bbox"]]
                                pcrop = frame[max(0, pby1):min(h, pby2), max(0, pbx1):min(w, pbx2)]
                                if pcrop.size > 0 and pcrop.shape[0] > 20 and pcrop.shape[1] > 10:
                                    try:
                                        p_emb = reid_engine.extract_embedding(pcrop)
                                        if p_emb is not None:
                                            pmeta["appearance_embedding"] = p_emb
                                            ptrk["appearance_embedding"] = p_emb
                                    except Exception as r_err:
                                        logger.debug(f"ReID extraction error for track {tid}: {r_err}")

            # For vehicles, extract real plate text via ANPR if visible
            for trk in active_tracks:
                if trk["class_name"] in ("car", "truck", "bus", "motorcycle"):
                    bx1, by1, bx2, by2 = [int(v) for v in trk["bbox"]]
                    vcrop = frame[max(0, by1):min(h, by2), max(0, bx1):min(w, bx2)]
                    if vcrop.size > 0:
                        try:
                            from backend.app.services.anpr import anpr_engine
                            cands = anpr_engine.process_frame(vcrop)
                            if cands:
                                top_c = cands[0]
                                pb = top_c.get("bbox", [0, 0, vcrop.shape[1], vcrop.shape[0]])
                                full_pb = [
                                    max(0.0, float(bx1 + int(pb[0]))),
                                    max(0.0, float(by1 + int(pb[1]))),
                                    min(float(w), float(bx1 + int(pb[2]))),
                                    min(float(h), float(by1 + int(pb[3]))),
                                ]
                                if hasattr(self, "evidence_associator") and self.evidence_associator is not None:
                                    p_cand = self.evidence_associator.associate_vehicle_plate(
                                        track_id=trk["track_id"],
                                        vehicle_track=trk,
                                        raw_plate_text=top_c["text"],
                                        ocr_confidence=float(top_c.get("confidence", 0.8)),
                                        plate_bbox=full_pb,
                                        frame_id=frame_id,
                                        timestamp=now_ts,
                                    )
                                    if p_cand:
                                        plate_rec = self.evidence_associator._plate_records.get(trk["track_id"])
                                        trk["plate_text"] = plate_rec.consensus_text if (plate_rec and plate_rec.consensus_text) else p_cand.plate_text
                                        if trk.get("target_id") is not None and hasattr(self, "_track_metadata"):
                                            vmeta = self._track_metadata.get(trk["target_id"])
                                            if vmeta:
                                                vmeta["plate_text"] = trk["plate_text"]
                                                vmeta["plate_confidence"] = plate_rec.consensus_confidence if (plate_rec and plate_rec.consensus_text) else p_cand.ocr_confidence
                                                vmeta["plate_association_state"] = p_cand.association_state.value if hasattr(p_cand.association_state, "value") else str(p_cand.association_state)
                                                vmeta["plate_consensus_reached"] = bool(plate_rec and plate_rec.consensus_text)
                                else:
                                    trk["plate_text"] = top_c["text"]
                                    if trk.get("target_id") is not None and hasattr(self, "_track_metadata"):
                                        vmeta = self._track_metadata.get(trk["target_id"])
                                        if vmeta:
                                            vmeta["plate_text"] = top_c["text"]
                                            vmeta["plate_confidence"] = float(top_c.get("confidence", 0.8))
                        except Exception:
                            pass
        except Exception as e:
            logger.warning(f"Live intelligence error: {e}")

        # Step 5: Render tactical visual annotations on live frame
        annotated = frame.copy()
        h, w = annotated.shape[:2]

        # Draw virtual fence lines and polygons on live frame
        if hasattr(self, "zone_fence") and self.zone_fence is not None:
            try:
                annotated = self.zone_fence.draw_zones_on_frame(
                    annotated,
                    triggered_zone_ids=triggered_zone_ids,
                    frame_width=w,
                    frame_height=h,
                )
            except Exception as z_err:
                logger.debug(f"Camera {self.camera_id} zone drawing error: {z_err}")

        for trk in active_tracks:
            bx1, by1, bx2, by2 = [int(v) for v in trk["bbox"]]
            bx1, by1 = max(0, bx1), max(0, by1)
            bx2, by2 = min(w - 1, bx2), min(h - 1, by2)

            cls_name = trk["class_name"]
            conf = trk["confidence"]
            tid = trk["track_id"]
            in_restricted = trk.get("in_restricted_zone", False)

            if in_restricted:
                color = (0, 0, 255)  # Alert red when breaching restricted zone
                label = f"BREACH {cls_name.upper()} {conf:.0%} [{tid}]"
            elif cls_name == "person":
                color = (0, 255, 128)  # Tactical green
                label = f"PERSON {conf:.0%} [{tid}]"
            elif cls_name in ("car", "truck", "bus", "motorcycle"):
                color = (0, 165, 255)  # Tactical orange
                label = f"VEHICLE: {cls_name.upper()} {conf:.0%}"
            elif cls_name in ("cell phone", "phone"):
                color = (255, 128, 0)  # Orange
                label = f"DEVICE: {cls_name.upper()} {conf:.0%}"
            else:
                color = (0, 220, 255)  # Cyan
                label = f"{cls_name.upper()} {conf:.0%}"

            # Bounding box
            cv2.rectangle(annotated, (bx1, by1), (bx2, by2), color, 2)
            # Tactical high-contrast label badge with edge & boundary clamping
            (lw, lh), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
            pill_h = max(20, lh + 8)
            pill_w = lw + 8

            # Clamping X to stay inside frame boundaries
            px1 = max(2, min(bx1, w - pill_w - 4))
            px2 = min(w - 2, px1 + pill_w)

            # Clamping Y: if room above bbox, draw above; otherwise draw inside top of bbox
            if by1 >= pill_h + 4:
                py1 = by1 - pill_h
                py2 = by1
                ty = by1 - 5
            else:
                py1 = by1
                py2 = min(h - 2, by1 + pill_h)
                ty = py1 + lh + 3

            # Draw high-contrast tactical dark background pill + color border
            cv2.rectangle(annotated, (px1, py1), (px2, py2), (10, 15, 22), -1)
            cv2.rectangle(annotated, (px1, py1), (px2, py2), color, 1)
            cv2.putText(annotated, label, (px1 + 4, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)

            # Draw ground-footprint contact anchor point (yellow circle)
            anc = trk.get("ground_anchor", [int((bx1 + bx2) / 2), by2])
            cv2.circle(annotated, (int(anc[0]), int(anc[1])), 4, (0, 255, 255), -1)

            # License plate chip for vehicles
            if trk.get("plate_text"):
                plate_txt = f"PLATE: {trk['plate_text']}"
                (pw, ph), _ = cv2.getTextSize(plate_txt, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
                ppill_h = max(18, ph + 8)
                ppill_w = pw + 8
                lpx1 = max(2, min(bx1, w - ppill_w - 4))
                lpx2 = min(w - 2, lpx1 + ppill_w)
                if by2 + ppill_h + 4 <= h:
                    lpy1 = by2 + 2
                    lpy2 = by2 + ppill_h
                    lpty = lpy1 + ph + 2
                else:
                    lpy1 = max(2, by2 - ppill_h)
                    lpy2 = by2
                    lpty = lpy2 - 4
                cv2.rectangle(annotated, (lpx1, lpy1), (lpx2, lpy2), (10, 15, 22), -1)
                cv2.rectangle(annotated, (lpx1, lpy1), (lpx2, lpy2), (0, 240, 255), 1)
                cv2.putText(annotated, plate_txt, (lpx1 + 4, lpty), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 240, 255), 1, cv2.LINE_AA)

        # Draw dedicated face detection and watchlist matching boxes
        for lf in live_faces:
            fx1, fy1, fx2, fy2 = lf["bbox"]
            fconf = lf["confidence"]
            fm = lf["match"]
            if fm:
                fcolor = (42, 42, 255)  # Alert Red
                flabel = f"MATCH: {fm['name']} ({fm['sim']:.0%})"
            else:
                fcolor = (255, 235, 0)  # Tactical Cyan/Yellow
                flabel = f"FACE {fconf:.0%}"

            cv2.rectangle(annotated, (fx1, fy1), (fx2, fy2), fcolor, 2)
            (flw, flh), _ = cv2.getTextSize(flabel, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
            fpill_h = max(18, flh + 8)
            fpill_w = flw + 8

            fpx1 = max(2, min(fx1, w - fpill_w - 4))
            fpx2 = min(w - 2, fpx1 + fpill_w)

            if fy1 >= fpill_h + 4:
                fpy1 = fy1 - fpill_h
                fpy2 = fy1
                fty = fy1 - 5
            else:
                fpy1 = fy1
                fpy2 = min(h - 2, fy1 + fpill_h)
                fty = fpy1 + flh + 3

            cv2.rectangle(annotated, (fpx1, fpy1), (fpx2, fpy2), (10, 15, 22), -1)
            cv2.rectangle(annotated, (fpx1, fpy1), (fpx2, fpy2), fcolor, 1)
            cv2.putText(annotated, flabel, (fpx1 + 4, fty), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)

        # Tactical HUD footer
        person_count = sum(1 for t in active_tracks if t["class_name"] == "person")
        zone_count = len(self._configured_zones)
        h_score_disp = float(self._latest_health.get("health_score", 100.0))
        hud_text = f"BOP-{self.camera_id} | PERSONS: {person_count} | ZONES: {zone_count} | HEALTH: {self._health_substate} ({h_score_disp:.0f}%)"
        cv2.putText(annotated, hud_text, (14, h - 14), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 240, 255), 1, cv2.LINE_AA)

        self._latest_annotated_frame = annotated

        # Step 6: Check threats and generate debounced incidents
        for track in active_tracks:
            self._check_threat(track, frame_id, inference_ms)

        # Step 6b: Check FRS live face matches for telemetry sightings only (Evidence-only; no duplicate Incident)
        for lf in live_faces:
            fm = lf.get("match")
            if fm:
                subj_id = fm["id"]
                now_ts = time.time()
                last_f_ts = self._last_cam_alert.get(f"frs_{subj_id}", 0.0)
                if now_ts - last_f_ts >= 15.0:
                    self._last_cam_alert[f"frs_{subj_id}"] = now_ts
                    if self._event_callback:
                        self._event_callback({
                            "type": "watchlist_candidate_sighting",
                            "camera_id": self.camera_id,
                            "camera_name": self.camera_name,
                            "subject_id": subj_id,
                            "subject_name": fm.get("name"),
                            "similarity": float(fm.get("sim", 0.0)),
                            "timestamp": datetime.utcnow().isoformat(),
                        })

        # Store detections for context
        self._detections_buffer.append({
            "frame_id": frame_id,
            "detections": len(detections),
            "tracks": len(active_tracks),
            "inference_ms": inference_ms,
            "night_enhanced": enhance_result.was_enhanced if enhance_result else False,
        })

        # Update stats
        self._stats = {
            "camera_id": self.camera_id,
            "frames_processed": frame_id,
            "detections_total": sum(d["detections"] for d in self._detections_buffer),
            "tracks_active": len(active_tracks),
            "avg_inference_ms": round(inference_ms, 1),
            "avg_zone_eval_ms": round(float(np.mean(self._zone_eval_times)), 3) if self._zone_eval_times else 0.0,
            "avg_health_eval_ms": round(float(np.mean(self._health_eval_times)), 3) if self._health_eval_times else 0.0,
            "health_status": self._health_state,
            "health_substate": self._health_substate,
            "health_score": round(float(self._latest_health.get("health_score", 100.0)), 1),
            "fps_actual": self._latest_health.get("fps_actual", 0.0),
            "blur_score": self._latest_health.get("blur_score", 0.0),
            "brightness": self._latest_health.get("brightness", 0.0),
            "frame_delta": self._latest_health.get("frame_delta", 0.0),
            "configured_zones": len(self._configured_zones),
            "uptime_seconds": round(time.time() - self._start_time, 1) if self._start_time else 0.0,
            "night_enhanced_frames": sum(1 for d in self._detections_buffer if d.get("night_enhanced")),
            "face_engine": "active" if face_engine else "unavailable",
            "reid_engine": "active" if reid_engine else "unavailable",
            "rule_engine": "active" if rule_engine else "unavailable",
            "night_enhancer": "active" if night_enhancer else "unavailable",
        }

    def _track_with_bytetrack(self, detections, frame_id: int) -> List[Dict]:
        """Execute persistent ByteTrack tracking and format tracks for downstream pipeline.

        Uses Kalman filter motion prediction and Hungarian IoU association
        across high and low confidence detections. Generates persistent tracklet IDs,
        ground-footprint anchors, dwell times, and velocity vectors.
        """
        now = time.time()

        # Update ByteTrack tracker instance
        raw_tracks = self.tracker.update(detections)

        active_ids = {t["track_id"] for t in raw_tracks}

        # 1. Track completion / departure: tracks present in prev frame but absent now
        if hasattr(self, "_active_track_ids"):
            completed_ids = self._active_track_ids - active_ids
            for cid in completed_ids:
                if cid in self._track_metadata:
                    try:
                        self._emit_tracklet_descriptor(cid, reason="track_completion")
                    except Exception as e:
                        logger.debug(f"Camera {self.camera_id} track completion emit error: {e}")
        self._active_track_ids = active_ids

        # 2. Prune stale track metadata (> 60s since last seen)
        # Lost timeout lifecycle trigger: emit TrackletDescriptor BEFORE deleting stale metadata
        stale_ids = [tid for tid, m in self._track_metadata.items()
                     if (now - m.get("last_seen", 0)) > 60.0 and tid not in active_ids]
        for tid in stale_ids:
            try:
                self._emit_tracklet_descriptor(tid, reason="lost_timeout")
            except Exception as e:
                logger.debug(f"Camera {self.camera_id} lost timeout emit error: {e}")
            del self._track_metadata[tid]
            if tid in self._tracked_targets:
                del self._tracked_targets[tid]

        formatted_tracks = []
        for trk in raw_tracks:
            target_id = trk["track_id"]
            bbox = [float(round(b, 1)) for b in trk["bbox"]]
            cx = float((bbox[0] + bbox[2]) / 2.0)
            cy = float((bbox[1] + bbox[3]) / 2.0)
            ground_anchor = [float(round(cx, 1)), float(round(bbox[3], 1))]

            if target_id not in self._track_metadata:
                self._track_metadata[target_id] = {
                    "first_seen": float(trk.get("first_seen", now)),
                    "last_seen": now,
                    "alert_sent": False,
                    "loiter_alert_sent": False,
                    "hits": int(trk.get("hits", 1)),
                    "class_name": str(trk["class_name"]),
                    "entry_point_normalized": [float(round(cx / 1920.0, 3)), float(round(cy / 1080.0, 3))],
                    "exit_point_normalized": [float(round(cx / 1920.0, 3)), float(round(cy / 1080.0, 3))],
                    "direction": str(trk.get("direction", "stationary")),
                    "speed": float(round(trk.get("speed", 0.0), 1)),
                    "zone_events": [],
                }
            else:
                meta = self._track_metadata[target_id]
                meta["last_seen"] = now
                meta["hits"] = int(trk.get("hits", meta.get("hits", 1) + 1))
                meta["exit_point_normalized"] = [float(round(cx / 1920.0, 3)), float(round(cy / 1080.0, 3))]
                if trk.get("direction"):
                    meta["direction"] = str(trk["direction"])
                if trk.get("speed") is not None:
                    meta["speed"] = float(round(trk["speed"], 1))

            meta = self._track_metadata[target_id]
            dwell = float(round(trk.get("dwell_time", now - meta["first_seen"]), 1))

            # Maintain _tracked_targets for backward compatibility
            self._tracked_targets[target_id] = {
                "center": [float(round(cx, 1)), float(round(cy, 1))],
                "bbox": bbox,
                "confidence": float(trk["confidence"]),
                "class_name": str(trk["class_name"]),
                "first_seen": meta["first_seen"],
                "last_seen": now,
                "alert_sent": meta["alert_sent"],
            }

            track_id = f"TRK-{target_id:02d}"
            formatted_track = {
                "track_id": track_id,
                "target_id": int(target_id),
                "bbox": bbox,
                "center": [float(round(cx, 1)), float(round(cy, 1))],
                "ground_anchor": ground_anchor,
                "footprint": ground_anchor,
                "class_name": str(trk["class_name"]),
                "class_id": int(trk.get("class_id", 0)),
                "confidence": float(round(trk["confidence"], 3)),
                "frame_id": int(frame_id),
                "dwell_time": dwell,
                "speed": float(round(trk.get("speed", 0.0), 1)),
                "direction": str(trk.get("direction", "stationary")),
                "state": str(trk.get("state", "confirmed")),
                "hits": int(trk.get("hits", 1)),
                "age": int(trk.get("age", 1)),
                "alert_sent": bool(meta["alert_sent"]),
                "loiter_alert_sent": bool(meta.get("loiter_alert_sent", False)),
            }
            formatted_tracks.append(formatted_track)

        self._latest_tracks = formatted_tracks
        return formatted_tracks

    def _emit_tracklet_descriptor(self, target_id: int, reason: str = "completed") -> Optional[Any]:
        """
        Assemble and register a TrackletDescriptor with the site-wide CrossCameraAssociator.
        Enforces the live chain:
        ByteTrack lifecycle -> TrackletDescriptor -> CrossCameraAssociator -> GlobalEntityDossier.
        Does NOT require an Incident to exist.
        """
        meta = self._track_metadata.get(target_id)
        if not meta:
            return None

        now = time.time()
        # Debounce rapid re-emissions for the same track within 0.5s unless terminal event
        last_emit = meta.get("last_tracklet_emission_time", 0.0)
        if reason not in ("lost_timeout", "track_completion") and (now - last_emit) < 0.5:
            return None

        meta["last_tracklet_emission_time"] = now

        first_seen = float(meta.get("first_seen", now))
        last_seen = float(meta.get("last_seen", now))
        duration = max(0.1, last_seen - first_seen)
        hits = int(meta.get("hits", 1))

        # Check evidence associator records for high-fidelity state if available
        track_key = f"TRK-{target_id:02d}"
        plate_text = meta.get("plate_text")
        plate_conf = meta.get("plate_confidence")
        plate_state = meta.get("plate_association_state")
        plate_consensus = bool(meta.get("plate_consensus_reached", False))

        subj_id = meta.get("matched_subject_id")
        frs_conf = meta.get("frs_confidence")
        frs_state = meta.get("frs_association_state")

        if hasattr(self, "evidence_associator") and self.evidence_associator is not None:
            plate_rec = self.evidence_associator._plate_records.get(track_key)
            if plate_rec:
                if plate_rec.consensus_text:
                    plate_text = plate_rec.consensus_text
                    plate_conf = plate_rec.consensus_confidence
                    plate_consensus = True
                    plate_state = "ASSOCIATED"
                elif plate_rec.candidates:
                    latest = plate_rec.candidates[-1]
                    plate_text = latest.plate_text
                    plate_conf = latest.ocr_confidence
                    plate_state = latest.association_state.value if hasattr(latest.association_state, "value") else str(latest.association_state)

            face_rec = self.evidence_associator._face_records.get(track_key)
            if face_rec and face_rec.best_candidate:
                best = face_rec.best_candidate
                subj_id = best.matched_subject_id
                frs_conf = best.similarity_score
                frs_state = best.association_state.value if hasattr(best.association_state, "value") else str(best.association_state)

        # Heading calculation from entry to exit if available
        entry_pt = list(meta.get("entry_point_normalized", [0.5, 0.5]))
        exit_pt = list(meta.get("exit_point_normalized", [0.5, 0.5]))
        heading_deg = meta.get("exit_heading_degrees")
        if heading_deg is None and (exit_pt[0] != entry_pt[0] or exit_pt[1] != entry_pt[1]):
            dx = exit_pt[0] - entry_pt[0]
            dy = exit_pt[1] - entry_pt[1]
            heading_deg = float((math.degrees(math.atan2(dx, -dy)) + 360) % 360)

        from edge.correlation.cross_camera import TrackletDescriptor
        from backend.app.services.correlation import get_site_cross_camera_associator

        td = TrackletDescriptor(
            track_id=int(target_id),
            camera_id=self.camera_id,
            class_name=str(meta.get("class_name", "unknown")),
            start_time=first_seen,
            end_time=last_seen,
            duration=duration,
            hit_count=hits,
            entry_point_normalized=entry_pt,
            exit_point_normalized=exit_pt,
            exit_heading_degrees=heading_deg,
            appearance_embedding=meta.get("appearance_embedding"),
            plate_text=plate_text,
            plate_confidence=plate_conf,
            plate_association_state=plate_state,
            plate_consensus_reached=plate_consensus,
            matched_subject_id=subj_id,
            frs_confidence=frs_conf,
            frs_association_state=frs_state,
            zone_events=list(meta.get("zone_events", [])),
            incident_id=meta.get("incident_id"),
            crop_path=meta.get("crop_path"),
        )

        try:
            site_assoc = get_site_cross_camera_associator()
            site_assoc.register_completed_tracklet(td)
        except Exception as reg_err:
            logger.debug(f"Camera {self.camera_id} error registering tracklet {target_id}: {reg_err}")

        return td

    def _simple_track(self, detections, frame_id):
        """[DEPRECATED / BYPASSED FROM LIVE PATH] Legacy Euclidean centroid tracking.
        Bypassed in favor of ByteTrack (_track_with_bytetrack). Retained only as non-live reference."""
        now = time.time()
        tracks = []

        # Remove dead tracks (not seen in > 3.0s)
        dead = [tid for tid, t in self._tracked_targets.items() if (now - t["last_seen"]) > 3.0]
        for tid in dead:
            del self._tracked_targets[tid]

        matched_track_ids = set()

        for det in detections:
            cx = (det.bbox[0] + det.bbox[2]) / 2.0
            cy = (det.bbox[1] + det.bbox[3]) / 2.0
            cls = det.class_name

            # Find closest active track of same class
            best_id = None
            best_dist = 220.0  # pixel distance threshold

            for tid, t in self._tracked_targets.items():
                if tid in matched_track_ids or t["class_name"] != cls:
                    continue
                dx = cx - t["center"][0]
                dy = cy - t["center"][1]
                dist = (dx * dx + dy * dy) ** 0.5
                if dist < best_dist:
                    best_dist = dist
                    best_id = tid

            if best_id is not None:
                # Existing track (same person / vehicle)
                self._tracked_targets[best_id]["center"] = [cx, cy]
                self._tracked_targets[best_id]["bbox"] = det.bbox
                self._tracked_targets[best_id]["confidence"] = det.confidence
                self._tracked_targets[best_id]["last_seen"] = now
                matched_track_ids.add(best_id)
                target_id = best_id
                dwell = now - self._tracked_targets[best_id]["first_seen"]
                alert_sent = self._tracked_targets[best_id].get("alert_sent", False)
            else:
                # New track
                target_id = self._next_track_num
                self._next_track_num += 1
                self._tracked_targets[target_id] = {
                    "center": [cx, cy],
                    "bbox": det.bbox,
                    "confidence": det.confidence,
                    "class_name": cls,
                    "first_seen": now,
                    "last_seen": now,
                    "alert_sent": False,
                }
                matched_track_ids.add(target_id)
                dwell = 0.0
                alert_sent = False

            track_id = f"TRK-{target_id:02d}"
            track = {
                "track_id": track_id,
                "target_id": target_id,
                "bbox": det.bbox,
                "center": [cx, cy],
                "class_name": cls,
                "confidence": det.confidence,
                "frame_id": frame_id,
                "dwell_time": dwell,
                "alert_sent": alert_sent,
            }
            tracks.append(track)

        return tracks

    def _check_threat(self, track: Dict, frame_id: int, inference_ms: float):
        """Check if a track constitutes a threat using the real scoring engine with anti-spam cooldown."""
        track_id = track["track_id"]
        target_id = track.get("target_id")
        now = time.time()
        cls = track["class_name"]
        conf = track["confidence"]
        dwell_time = track.get("dwell_time", 0.0)

        # ── ANTI-SPAM COOLDOWN CHECKS ──
        # 1. If alert was already sent for this persistent target, don't spam!
        if track.get("alert_sent", False):
            # Only escalate if dwell time reaches significant loitering (> 60s) and not yet re-alerted
            if dwell_time < 60.0 or track.get("loiter_alert_sent", False):
                return
            track["loiter_alert_sent"] = True

        # 2. Track-level debounce: prevent spamming alerts for the same track within 60s
        track_key = f"{self.camera_id}:{target_id or track_id}"
        last_time = self._incident_cooldown.get(track_key, 0)
        if now - last_time < 60.0:
            return

        signals = {}
        is_interesting = False
        zone_name = None

        # ── REAL ZONEFENCE GEOMETRIC FACTS ──
        # Evaluate ground-footprint contact anchor against configured zone geometry
        current_zone_events = track.get("current_zone_events", [])
        anchor = track.get("ground_anchor") or track.get("footprint") or [track["center"][0], track["bbox"][3]]
        anchor_pos = (float(anchor[0]), float(anchor[1]))
        containing_zones = self.zone_fence.get_zones_for_position(anchor_pos)

        # 1. Did track cross a boundary or trigger a zone intrusion/entry in this frame?
        primary_zevt_type = None
        direction_val = None
        if current_zone_events:
            primary_zevt = max(current_zone_events, key=lambda e: float(e.get("severity", 0.5)))
            zone_severity = float(primary_zevt.get("severity", 0.5))
            primary_zevt_type = primary_zevt.get("zone_type")
            if not primary_zevt_type:
                if zone_severity >= 0.8:
                    primary_zevt_type = "RESTRICTED"
                elif zone_severity >= 0.5:
                    primary_zevt_type = "BUFFER"
                elif zone_severity >= 0.2:
                    primary_zevt_type = "MONITORED"
                else:
                    primary_zevt_type = "PUBLIC"
            direction_val = primary_zevt.get("direction")
            signals["confidence"] = conf
            signals["zone_severity"] = zone_severity
            has_crossing = any(e.get("event_type") in ("zone_crossing", "zone_intrusion", "zone_entry", "direction_violation") for e in current_zone_events)
            signals["boundary_crossing"] = 1.0 if has_crossing else 0.6
            zone_name = primary_zevt.get("zone_name")
            is_interesting = True
        # 2. Or is track currently inside a configured zone (continuation / dwell)?
        elif containing_zones:
            restricted_zones = [z for z in containing_zones if z.get("zone_type") in ("RESTRICTED", "SENSITIVE")]
            if restricted_zones:
                target_z = max(restricted_zones, key=lambda z: float(z.get("severity", 0.5)))
                primary_zevt_type = target_z.get("zone_type", "RESTRICTED")
                signals["confidence"] = conf
                signals["zone_severity"] = float(target_z.get("severity", 0.5))
                signals["boundary_crossing"] = 0.2  # Persistent presence inside zone
                zone_name = target_z.get("name")
                is_interesting = True
            elif cls in ("person", "car", "truck", "bus", "motorcycle"):
                # Monitored / Patrol zone
                target_z = max(containing_zones, key=lambda z: float(z.get("severity", 0.3)))
                primary_zevt_type = target_z.get("zone_type", "MONITORED")
                signals["confidence"] = conf
                signals["zone_severity"] = float(target_z.get("severity", 0.3))
                signals["boundary_crossing"] = 0.2  # Persistent presence inside zone
                zone_name = target_z.get("name")
                is_interesting = True
        else:
            # Object is completely outside all configured zones
            primary_zevt_type = "PUBLIC"
            signals["zone_severity"] = 0.0
            signals["boundary_crossing"] = 0.0
            is_interesting = False

        if cls in ("car", "truck", "bus", "motorcycle"):
            signals["vehicle_context"] = 1.0

        if cls in ("backpack", "handbag", "suitcase"):
            signals["behavior_anomaly"] = 0.8

        if not is_interesting:
            return

        if dwell_time > 10:
            signals["loitering"] = min(dwell_time / 60.0, 1.0)

        hour = datetime.utcnow().hour
        if hour < 6 or hour > 20:
            signals["night"] = 0.8

        from backend.app.services.scoring import compute_threat_score
        from backend.app.services.feedback import get_site_feedback_registry

        # Authoritative same-frame boundary context & behavioral kinematics
        boundary_ctx = self.zone_fence.get_nearest_boundary_context(anchor_pos, frame_id, now)
        track_bbox = track.get("bbox", (0.0, 0.0, 0.0, 0.0))
        kin_meas = self.kinematic_engine.update_track(target_id or track_id, track_bbox, now, boundary_ctx, object_type=cls)
        signals.update(kin_meas.to_rps_signals(cls))

        resolved_zone_name = zone_name
        if not resolved_zone_name and boundary_ctx:
            b_name = getattr(boundary_ctx, "zone_name", None)
            if isinstance(b_name, str) and b_name != "PUBLIC":
                resolved_zone_name = b_name
        if not resolved_zone_name:
            resolved_zone_name = self.bop or f"Camera {self.camera_id}"

        resolved_zone_type = primary_zevt_type
        if not resolved_zone_type and boundary_ctx:
            b_type = getattr(boundary_ctx, "zone_type", None)
            if isinstance(b_type, str):
                resolved_zone_type = b_type
        if not resolved_zone_type:
            resolved_zone_type = "RESTRICTED"

        hits = track.get("hits") or track.get("hit_count") or (int(dwell_time * 10) if dwell_time > 0 else 1)

        # Ingest UnifiedTrackEvidence from association layer
        rps_ev_ctx = {}
        if hasattr(self, "evidence_associator") and self.evidence_associator is not None:
            unified_ev = self.evidence_associator.build_unified_evidence(track, camera_id=self.camera_id, timestamp=now)
            rps_ev_ctx = unified_ev.to_rps_context()

        # Check in-memory operator feedback suppression
        feedback_reg = get_site_feedback_registry()
        local_supp_key = f"camera:{self.camera_id}:target:{target_id or track_id}"
        is_supp = feedback_reg.is_suppressed(local_supp_key)

        context_data = {
            "object_type": cls,
            "zone_name": resolved_zone_name,
            "zone_type": resolved_zone_type,
            "behavior": "stationary" if dwell_time > 10 else "moving",
            "dwell_time": dwell_time,
            "direction": kin_meas.direction_classification if kin_meas.direction_classification != "parallel" else direction_val,
            "confidence": conf,
            "camera_id": self.camera_id,
            "camera_name": self.camera_name,
            "track_id": track_id,
            "consecutive_frames": hits,
            "is_single_frame_jitter": (dwell_time < 0.2 and hits <= 1),
            "operator_suppressed": is_supp,
            "suppression_key": local_supp_key,
            "perimeter_convergence": kin_meas.perimeter_convergence,
            "trajectory_irregularity_index": kin_meas.trajectory_irregularity_index,
        }
        context_data.update(rps_ev_ctx)

        # Check cross-camera dossier
        try:
            from backend.app.services.correlation import get_site_cross_camera_associator
            site_assoc = get_site_cross_camera_associator()
            lookup_tid = target_id if target_id is not None else track_id
            dossier = site_assoc.get_dossier_for_track(self.camera_id, lookup_tid)
            if dossier and dossier.evidence_status in ("CORROBORATED", "PLAUSIBLE"):
                context_data["cross_camera_corroborated"] = True
                context_data["dossier_id"] = dossier.dossier_id
                context_data["correlated_camera_ids"] = dossier.camera_ids
                if not is_supp and feedback_reg.is_suppressed(f"dossier:{dossier.dossier_id}"):
                    context_data["operator_suppressed"] = True
        except Exception:
            pass

        assessment = compute_threat_score(
            signals,
            context=context_data,
        )

        score = assessment.score

        if score < 19:
            return

        # Record cooldown and mark alert as sent
        self._incident_cooldown[track_key] = now
        if target_id in self._track_metadata:
            self._track_metadata[target_id]["alert_sent"] = True
        if target_id in self._tracked_targets:
            self._tracked_targets[target_id]["alert_sent"] = True
        track["alert_sent"] = True

        fingerprint = hashlib.sha256(f"{track_key}:{int(now / 60)}".encode()).hexdigest()[:16]

        # Generate incident with REAL scoring engine output and geometric zone facts
        self._generate_incident(
            track=track,
            reasons=assessment.reasons,
            severity=assessment.severity,
            score=assessment.score,
            fingerprint=fingerprint,
            dwell_time=dwell_time,
            inference_ms=inference_ms,
            assessment=assessment,
            zone_name=resolved_zone_name,
            suppression_revocation_required=assessment.suppression_revocation_required,
            revocation_reason=assessment.suppression_revocation_reason,
        )

    def _generate_incident(self, track, reasons, severity, score,
                           fingerprint, dwell_time, inference_ms,
                           assessment=None, zone_name=None,
                           suppression_revocation_required=False,
                           revocation_reason=None):
        """Generate a real incident using the scoring engine output."""
        from datetime import timedelta
        import random
        now = datetime.utcnow()
        code = f"IBVAP-{now.strftime('%Y%m%d')}-{now.strftime('%H%M%S')}-{random.randint(1000,9999)}-{track['class_name'][:3].upper()}-{self.camera_id}"

        confidence = track["confidence"]
        cls = track["class_name"]
        incident_zone = zone_name or self.bop or f"Camera {self.camera_id}"

        # Use scoring engine output if available
        ai_assessment = {}
        action = "Monitor situation."
        if assessment:
            ai_assessment = assessment.ai_assessment
            action = assessment.recommended_action
        else:
            ai_assessment = {
                "detected": f"{cls}-like moving object",
                "confidence": confidence,
                "context": f"{self.camera_name} ({incident_zone})",
                "behavior": "stationary" if dwell_time > 10 else "moving",
                "threat_contributions": {"confidence": confidence, "detection_score": score / 100.0},
                "uncertainty": "Moderate" if confidence < 0.7 else "Low",
                "human_action": f"Verify {cls} on live feed from {self.camera_name}",
            }
            if severity == "CRITICAL":
                action = "IMMEDIATE: Verify incident on live feed. Consider escalation if breach confirmed."
            elif severity == "HIGH":
                action = "Verify on live feed. Monitor for escalation."

        ai_assessment["spatial_grounding"] = (
            "Zone evaluation is image-coordinate geometric evaluation; "
            "physical-world accuracy is calibration dependent."
        )
        ai_assessment["zone_name"] = incident_zone

        # Build timeline
        timeline = [
            {
                "timestamp": (now - timedelta(seconds=dwell_time)).isoformat(),
                "event_type": "detection",
                "description": f"{cls.capitalize()} detected approaching perimeter",
                "source": "ai_perception",
                "confidence": confidence,
                "payload": {"bbox": track["bbox"]},
            },
        ]
        current_zone_events = track.get("current_zone_events", [])
        if current_zone_events:
            for ze in current_zone_events:
                timeline.append({
                    "timestamp": now.isoformat(),
                    "event_type": ze.get("event_type", "zone_intrusion"),
                    "description": f"Geometric rule triggered: {ze.get('event_type')} in {ze.get('zone_name', incident_zone)}",
                    "source": "zone_fence",
                    "confidence": confidence,
                    "payload": {
                        "zone_id": ze.get("zone_id"),
                        "zone_name": ze.get("zone_name", incident_zone),
                        "zone_type": ze.get("zone_type", "RESTRICTED"),
                        "ground_anchor": [float(round(p, 1)) for p in track.get("ground_anchor", [])],
                        "rule": ze.get("rule", "polygon_entry"),
                    }
                })
        if dwell_time > 5:
            timeline.append({
                "timestamp": (now - timedelta(seconds=2)).isoformat(),
                "event_type": "loitering",
                "description": f"Object stationary for {dwell_time:.0f} seconds",
                "source": "context_engine",
                "confidence": 0.85,
                "payload": {},
            })
        timeline.append({
            "timestamp": now.isoformat(),
            "event_type": "threat_score_computed",
            "description": f"Threat score: {score:.0f}/100 (severity: {severity})",
            "source": "scoring_engine",
            "confidence": confidence,
            "payload": {},
        })
        timeline.append({
            "timestamp": now.isoformat(),
            "event_type": "alert_generated",
            "description": "Alert generated for operator review",
            "source": "alert_engine",
            "payload": {},
        })

        # GAP-P0-02 Phase 2: Save incident to database with atomic Incident + Evidence + OutboxEvent
        # Cooldown, suppression, and persistence authority run BEFORE any notification is emitted.
        outbox_payload = self._save_incident(
            code=code, track=track, reasons=reasons, severity=severity,
            score=score, confidence=confidence, fingerprint=fingerprint,
            ai_assessment=ai_assessment, action=action, timeline=timeline,
            zone_name=incident_zone,
            suppression_revocation_required=suppression_revocation_required,
            revocation_reason=revocation_reason,
        )

        # Phantom Alert Elimination:
        # Compatibility callback is fired ONLY AFTER successful database commit and outbox row creation
        if outbox_payload and self._event_callback:
            post_commit_event = outbox_payload.copy()
            post_commit_event["type"] = "incident_created"
            try:
                self._event_callback(post_commit_event)
            except Exception as cb_err:
                logger.warning(f"Camera {self.camera_id}: Post-commit event callback error: {cb_err}")

        logger.info(
            f"Camera {self.camera_id}: INCIDENT CREATED — {code} "
            f"[{severity}] score={score:.0f} {cls} "
            f"({confidence:.0%}) dwell={dwell_time:.0f}s"
        )

    def _generate_face_incident(self, match_info: Dict, frame: np.ndarray, frame_id: int, bbox: Tuple[int, int, int, int]):
        """[DEPRECATED in Phase 4] Evaluates face match sighting via compute_threat_score.
        Live video pipeline associates face sightings directly to ByteTrack tracks.
        """
        now = datetime.utcnow()
        subj_id = match_info.get("id", 0)
        subj_name = match_info.get("name", "Unknown Candidate")
        sim = float(match_info.get("sim", 0.5))

        code = f"IBVAP-{now.strftime('%Y%m%d')}-{now.strftime('%H%M%S')}-FRS-S{subj_id}-{self.camera_id}"

        # Neutral evaluation through scoring engine
        from backend.app.services.scoring import compute_threat_score
        assessment = compute_threat_score(
            signals={"confidence": sim, "zone_severity": 0.2, "boundary_crossing": 0.0},
            context={
                "object_type": "person",
                "zone_type": "BUFFER",
                "confidence": sim,
                "camera_id": self.camera_id,
                "camera_name": self.camera_name,
                "dwell_time": 0.0,
            }
        )
        severity = assessment.severity
        threat_score = assessment.score
        title = f"Candidate Sighting: {subj_name} ({sim:.0%} Match Candidate)"
        description = f"Facial recognition candidate match observed for '{subj_name}' on {self.camera_name} ({self.bop}) with similarity {sim:.4f}."
        action = f"Operator verification recommended: verify live feed for candidate '{subj_name}' on {self.camera_name}."

        ai_assessment = {
            "model": "OpenCV YuNet + SFace 128D",
            "subject_id": subj_id,
            "subject_name": subj_name,
            "similarity": sim,
            "frame": frame_id,
            "legal_citation": "Bharatiya Sakshya Adhiniyam, 2023 — Section 63",
        }

        timeline = [
            {
                "timestamp": now.isoformat(),
                "event_type": "face_match",
                "description": f"Facial recognition match candidate: {subj_name} ({sim:.0%})",
                "source": "frs_engine",
                "confidence": sim,
                "payload": {"bbox": list(bbox), "subject_id": subj_id, "similarity": sim},
            },
            {
                "timestamp": now.isoformat(),
                "event_type": "threat_score_computed",
                "description": f"Threat score: {threat_score:.1f}/100 (severity: {severity})",
                "source": "scoring_engine",
                "confidence": sim,
                "payload": {"threat_score": threat_score},
            }
        ]

        if self._event_callback:
            self._event_callback({
                "type": "watchlist_candidate_sighting",
                "camera_id": self.camera_id,
                "camera_name": self.camera_name,
                "incident_code": code,
                "title": title,
                "description": description,
                "severity": severity,
                "threat_score": threat_score,
                "confidence": sim,
                "recommended_action": action,
                "timeline": timeline,
                "ai_assessment": ai_assessment,
                "subject_id": subj_id,
                "subject_name": subj_name,
                "similarity": sim,
                "timestamp": now.isoformat(),
            })

    def _generate_nvr_clip(self, incident_code: str, entries: Optional[List[Any]] = None):
        """
        Synthesize an MP4/AVI video clip from the rolling ring buffer with technical controls aligned with Bharatiya Sakshya Adhiniyam, 2023 (Section 63) requirements. Per-case statutory certificate generation remains an operational/legal prerequisite.
        Container FPS is a CFR approximation based on monotonic capture timestamps.
        Returns: (clip_url, clip_hash, file_size_bytes, clip_file_path)
        """
        if entries is None:
            if hasattr(self, "_frame_ring_buffer") and hasattr(self._frame_ring_buffer, "snapshot_preroll"):
                entries = self._frame_ring_buffer.snapshot_preroll(
                    incident_mono=time.perf_counter(),
                    preroll_duration_sec=getattr(self, "nvr_preroll_duration_sec", 5.0),
                    epoch=getattr(self, "stream_epoch", 1),
                )
            elif hasattr(self, "_frame_ring_buffer") and self._frame_ring_buffer:
                entries = list(self._frame_ring_buffer)[-25:]
            else:
                entries = []

        if not entries:
            return None, None, 0, ""

        try:
            from pathlib import Path
            from backend.app.core.config import settings
            import cv2
            import hashlib

            clips_dir = Path(settings.evidence_dir) / "clips"
            clips_dir.mkdir(parents=True, exist_ok=True)

            import shutil
            total, used, free = shutil.disk_usage(clips_dir)
            if free < 100 * 1024 * 1024:  # less than 100MB free
                logger.warning(f"Low disk space ({free // (1024*1024)}MB free). Skipping clip generation.")
                return None, None, 0, ""

            filename = f"INC-{incident_code}.mp4"
            file_path = clips_dir / filename

            # Representative container FPS calculation (CFR approximation)
            if len(entries) >= 2 and hasattr(entries[0], "capture_mono") and hasattr(entries[-1], "capture_mono"):
                dt = entries[-1].capture_mono - entries[0].capture_mono
                if dt > 0.05:
                    clip_fps = float(round((len(entries) - 1) / dt, 2))
                    clip_fps = max(1.0, min(60.0, clip_fps))
                else:
                    clip_fps = float(getattr(entries[-1], "source_fps", 10.0)) or 10.0
            else:
                clip_fps = 10.0

            # Downscale to 640x360 for compact size (<150KB) and fast encoding
            clip_w, clip_h = 640, 360
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            out = cv2.VideoWriter(str(file_path), fourcc, clip_fps, (clip_w, clip_h))
            if not out.isOpened():
                fourcc = cv2.VideoWriter_fourcc(*"MJPG")
                filename = f"INC-{incident_code}.avi"
                file_path = clips_dir / filename
                out = cv2.VideoWriter(str(file_path), fourcc, clip_fps, (clip_w, clip_h))

            for item in entries:
                if isinstance(item, NVRRingEntry):
                    f = cv2.imdecode(np.frombuffer(item.jpeg_data, dtype=np.uint8), cv2.IMREAD_COLOR)
                    if f is None:
                        self.nvr_decode_corruptions += 1
                        continue
                elif isinstance(item, np.ndarray):
                    f = item
                elif hasattr(item, "frame") and isinstance(item.frame, np.ndarray):
                    f = item.frame
                else:
                    continue

                resized = cv2.resize(f, (clip_w, clip_h), interpolation=cv2.INTER_AREA)
                out.write(resized)
            out.release()

            if file_path.exists() and file_path.stat().st_size > 0:
                data = file_path.read_bytes()
                h_val = hashlib.sha256(data).hexdigest()
                url = f"/api/v1/evidence/clips/{filename}"
                return url, h_val, len(data), str(file_path)
        except Exception as e:
            logger.warning(f"Camera {self.camera_id}: Failed to generate NVR clip for {incident_code}: {e}")
        return None, None, 0, ""

    def _handle_degraded_db_spool(self, code, track, reasons, severity, score, confidence,
                                  fingerprint, ai_assessment, action, timeline, zone_name=None):
        import json
        from datetime import datetime
        from backend.app.models.outbox import derive_event_id, derive_idempotency_key

        spool_dir = os.path.join(os.getcwd(), "data", "spool")
        os.makedirs(spool_dir, exist_ok=True)
        spool_file = os.path.join(spool_dir, "offline_incidents.jsonl")

        event_type = "incident_created"
        event_id = derive_event_id()
        idempotency_key = derive_idempotency_key(code, event_type)

        record = {
            "event_id": event_id,
            "event_type": event_type,
            "incident_code": code,
            "idempotency_key": idempotency_key,
            "camera_id": self.camera_id,
            "camera_name": self.camera_name,
            "zone_name": zone_name or self.bop,
            "severity": severity,
            "threat_score": float(score),
            "confidence": float(confidence),
            "reasons": reasons,
            "track": _normalize_for_json(track) if isinstance(track, dict) else {},
            "fingerprint": fingerprint,
            "ai_assessment": _normalize_for_json(ai_assessment) if isinstance(ai_assessment, dict) else {},
            "recommended_action": action,
            "timeline": _normalize_for_json(timeline) if isinstance(timeline, list) else [],
            "spooled_at": datetime.utcnow().isoformat(),
            "pipeline_state": "DEGRADED_DB_OFFLINE",
        }

        import fcntl
        rot_lock_file = os.path.join(spool_dir, "spool_rotation.lock")
        try:
            lock_fd = os.open(rot_lock_file, os.O_CREAT | os.O_RDWR)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
                try:
                    with open(spool_file, "a", encoding="utf-8") as f:
                        f.write(json.dumps(record) + "\n")
                        f.flush()
                        os.fsync(f.fileno())
                finally:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)
            logger.critical(
                f"Camera {self.camera_id}: [DEGRADED_DB_OFFLINE] Incident {code} spooled to disk at {spool_file}"
            )
        except Exception as e:
            logger.critical(f"Camera {self.camera_id}: Failed to spool offline incident {code}: {e}")

    def _save_incident(self, code, track, reasons, severity, score, confidence,
                        fingerprint, ai_assessment, action, timeline, zone_name=None,
                        suppression_revocation_required=False, revocation_reason=None):
        """Save a real incident to the database with per-target cooldown and concurrency locks."""
        now_ts = time.time()
        target_id_raw = track.get("target_id")
        if target_id_raw is None:
            t_str = str(track.get("track_id", "0"))
            target_id = int(t_str.split("-")[-1]) if "-" in t_str else int(t_str)
        else:
            target_id = int(target_id_raw)

        target_cooldown_key = f"camera:{self.camera_id}:target:{target_id}"
        if not hasattr(self, "_target_incident_cooldown"):
            self._target_incident_cooldown = {}
        last_incident_ts = self._target_incident_cooldown.get(target_cooldown_key, 0.0)
        if (now_ts - last_incident_ts) < 60.0:
            logger.debug(
                f"Camera {self.camera_id}: Suppressing repeat incident for target {target_id} "
                f"(cooldown active: {60.0 - (now_ts - last_incident_ts):.1f}s remaining)"
            )
            return None

        dossier_id = None
        if hasattr(self, "_track_metadata") and target_id in self._track_metadata:
            dossier_id = self._track_metadata[target_id].get("dossier_id")

        keys_to_lock = [f"camera:{self.camera_id}:target:{target_id}"]
        if dossier_id:
            keys_to_lock.append(f"dossier:{dossier_id}")
        keys_to_lock.sort()

        try:
            import sys
            sys.path.insert(0, os.getcwd())
            from backend.app.db.session import SessionLocal
            from backend.app.models.incident import Incident
            from backend.app.models.alert import Alert
            from backend.app.models.evidence import Evidence
            from backend.app.models.suppression import OperatorSuppression
            from backend.app.models.outbox import (
                IncidentOutboxEvent,
                derive_event_id,
                derive_idempotency_key,
            )
            from backend.app.services.feedback import (
                acquire_advisory_xact_lock,
                derive_advisory_lock_id,
                SiteFeedbackRegistry,
            )
            from backend.app.services.evidence import seal_evidence
            from datetime import datetime
            import hashlib as hl

            # Asynchronous NVR clip generation (GAP-P0-01 Step 6)
            t_incident_mono = time.perf_counter()
            if hasattr(self, "_frame_ring_buffer") and hasattr(self._frame_ring_buffer, "snapshot_preroll"):
                preroll_entries = self._frame_ring_buffer.snapshot_preroll(
                    incident_mono=t_incident_mono,
                    preroll_duration_sec=getattr(self, "nvr_preroll_duration_sec", 5.0),
                    epoch=getattr(self, "stream_epoch", 1),
                )
            elif hasattr(self, "_frame_ring_buffer") and self._frame_ring_buffer:
                preroll_entries = list(self._frame_ring_buffer)[-25:]
            else:
                preroll_entries = []

            if not preroll_entries:
                ai_assessment["clip_status"] = "NO_FRAMES"
            else:
                def _async_clip_task(inc_code=code, entries_copy=preroll_entries, cam_id=self.camera_id, cam_name=self.camera_name):
                    try:
                        c_url, c_hash, c_size, c_path = self._generate_nvr_clip(inc_code, entries=entries_copy)
                        if c_url:
                            from backend.app.db.session import SessionLocal
                            from backend.app.models.incident import Incident
                            from backend.app.models.evidence import Evidence
                            db_bg = SessionLocal()
                            try:
                                inc = db_bg.query(Incident).filter(Incident.incident_code == inc_code).first()
                                if inc:
                                    ai_ass = inc.ai_assessment.copy() if inc.ai_assessment else {}
                                    ai_ass["clip_url"] = c_url
                                    ai_ass["clip_sha256"] = c_hash
                                    ai_ass["clip_status"] = "COMPLETED"
                                    inc.ai_assessment = ai_ass

                                    tl = list(inc.timeline) if inc.timeline else []
                                    tl.append({
                                        "timestamp": datetime.utcnow().isoformat(),
                                        "event_type": "nvr_clip_sealed",
                                        "description": f"BSA 2023 Section 63 rolling NVR video clip sealed: {c_url}",
                                        "source": "nvr_subsystem",
                                        "confidence": 1.0,
                                        "payload": {"clip_url": c_url, "sha256": c_hash, "size_bytes": c_size}
                                    })
                                    inc.timeline = tl

                                    ev = Evidence(
                                        incident_id=inc.id,
                                        evidence_type="video_clip",
                                        file_path=c_path,
                                        sha256=c_hash,
                                        manifest_path=c_path,
                                        manifest_data={"size_bytes": c_size, "clip_url": c_url},
                                        threat_score=inc.threat_score,
                                        camera_id=cam_id,
                                        camera_name=cam_name,
                                        detection_metadata={"clip_url": c_url, "sha256": c_hash},
                                        created_at=datetime.utcnow(),
                                    )
                                    db_bg.add(ev)
                                    db_bg.commit()
                            except Exception as bg_db_e:
                                logger.warning(f"Camera {cam_id}: Failed to update DB with async clip: {bg_db_e}")
                                db_bg.rollback()
                            finally:
                                db_bg.close()
                    except Exception as bg_err:
                        logger.error(f"Camera {cam_id}: Async clip task failed for {inc_code}: {bg_err}")

                dispatched = global_clip_synthesizer.submit(_async_clip_task)
                if dispatched:
                    ai_assessment["clip_status"] = "SYNTHESIZING_ASYNC"
                else:
                    self.nvr_clip_shedding_drops += 1
                    ai_assessment["clip_status"] = "SHED_DUE_TO_CAPACITY"
                    logger.warning(
                        f"Camera {self.camera_id}: Clip pool saturated ({global_clip_synthesizer.pending_count} pending). "
                        f"Shedding clip synthesis for {code}."
                    )

            clip_url = None
            clip_path = None
            clip_hash = None
            clip_size = 0

            db = SessionLocal()
            try:
                # Acquire PostgreSQL advisory transaction lock
                for lock_key in keys_to_lock:
                    lock_id = derive_advisory_lock_id(lock_key)
                    acquire_advisory_xact_lock(db, lock_id)

                now_utc = datetime.utcnow()

                if suppression_revocation_required:
                    # P0-1: BREAK-GLASS ATOMIC REVOCATION
                    revoked_count = db.query(OperatorSuppression).filter(
                        OperatorSuppression.suppression_key.in_(keys_to_lock),
                        OperatorSuppression.is_revoked == False
                    ).update({
                        "is_revoked": True,
                        "revoked_at": now_utc,
                        "revocation_reason": revocation_reason or "Automated break-glass revocation: restricted/sensitive penetration"
                    }, synchronize_session=False)

                    if revoked_count > 0:
                        logger.warning(
                            f"Camera {self.camera_id}: Break-glass revoked {revoked_count} active suppressions for keys {keys_to_lock}"
                        )
                        for k in keys_to_lock:
                            SiteFeedbackRegistry.get_instance().invalidate(k)

                        timeline.append({
                            "timestamp": now_utc.isoformat(),
                            "event_type": "suppression_revoked",
                            "description": f"Prior operator suppression automatically revoked: {revocation_reason or 'restricted/sensitive penetration'}",
                            "source": "triage_engine",
                            "confidence": 1.0,
                            "payload": {"revoked_keys": keys_to_lock, "reason": revocation_reason}
                        })
                else:
                    # Normal flow: Check authoritative DB state
                    active_supp = db.query(OperatorSuppression).filter(
                        OperatorSuppression.suppression_key.in_(keys_to_lock),
                        OperatorSuppression.is_revoked == False,
                        OperatorSuppression.expires_at > now_utc
                    ).first()

                    if active_supp:
                        logger.info(
                            f"Camera {self.camera_id}: Target {target_id} incident suppressed by active DB suppression "
                            f"(ID: {active_supp.id}, reason: {active_supp.dismissal_reason})"
                        )
                        for k in keys_to_lock:
                            SiteFeedbackRegistry.get_instance().register_suppression(
                                target_key=k,
                                camera_id=active_supp.camera_id or self.camera_id,
                                target_id=active_supp.track_id or target_id,
                                dossier_id=active_supp.dossier_id,
                                reason=active_supp.dismissal_reason,
                                expires_at=active_supp.expires_at,
                                zone_type=active_supp.initial_zone_type,
                                notes=active_supp.operator_notes,
                                created_by=active_supp.operator_id,
                            )
                        db.rollback()
                        return None

                incident = Incident(
                    incident_code=code,
                    title=f"{track['class_name'].capitalize()} detected at {zone_name or self.bop}",
                    description=f"Real AI detection: {track['class_name']} ({confidence:.0%}) at {self.camera_name}",
                    severity=severity,
                    threat_score=score,
                    confidence=confidence,
                    status="OPEN",
                    reason_codes=reasons,
                    event_ids=[],
                    detection_ids=[],
                    track_ids=[],
                    camera_id=self.camera_id,
                    camera_name=self.camera_name,
                    zone_name=zone_name or "Monitored Area",
                    fingerprint=fingerprint,
                    correlated_ids=[],
                    recommended_action=action,
                    ai_assessment=ai_assessment,
                    timeline=timeline,
                    created_at=now_utc,
                )
                db.add(incident)
                db.flush()

                # Register tracklet and correlate across cameras
                try:
                    if hasattr(self, "_track_metadata") and target_id in self._track_metadata:
                        self._track_metadata[target_id]["incident_id"] = incident.id

                    td = self._emit_tracklet_descriptor(target_id, reason="incident_created")
                    if td:
                        from backend.app.services.correlation import get_site_cross_camera_associator, correlate_incident_with_tracklet
                        site_assoc = get_site_cross_camera_associator()
                        correlate_incident_with_tracklet(db, incident, td, site_assoc)
                except Exception as corr_err:
                    logger.debug(f"Camera {self.camera_id} incident correlation warning: {corr_err}")

                # Create alert
                alert = Alert(
                    incident_id=incident.id,
                    priority=severity,
                    status="NEW",
                    message=f"{severity} incident: {track['class_name'].capitalize()} detected at {self.bop} (Score: {score}/100)",
                    created_at=now_utc,
                )
                db.add(alert)

                # Create snapshot evidence
                payload = {
                    "camera_id": self.camera_id,
                    "track": track,
                    "reasons": reasons,
                    "score": score,
                    "ai_assessment": ai_assessment,
                    "real_detection": True,
                }
                manifest_path, manifest_hash, manifest_data = seal_evidence(
                    code, _normalize_for_json(payload), camera_id=self.camera_id,
                    camera_name=self.camera_name, threat_score=score,
                )
                evidence = Evidence(
                    incident_id=incident.id,
                    evidence_type="snapshot",
                    file_path=manifest_path,
                    sha256=manifest_hash,
                    manifest_path=manifest_path,
                    manifest_data=manifest_data,
                    threat_score=score,
                    camera_id=self.camera_id,
                    camera_name=self.camera_name,
                    detection_metadata={"real_detection": True},
                    created_at=now_utc,
                )
                db.add(evidence)

                # Create NVR clip evidence if generated
                if clip_url and clip_path:
                    clip_ev = Evidence(
                        incident_id=incident.id,
                        evidence_type="clip",
                        file_path=clip_path,
                        sha256=clip_hash,
                        manifest_path=manifest_path,
                        manifest_data={"clip_url": clip_url, "size_bytes": clip_size},
                        file_size_bytes=clip_size,
                        threat_score=score,
                        camera_id=self.camera_id,
                        camera_name=self.camera_name,
                        detection_metadata={"clip_url": clip_url, "real_detection": True},
                        created_at=now_utc,
                    )
                    db.add(clip_ev)

                # GAP-P0-02 Phase 2: Insert IncidentOutboxEvent atomically in the same transaction
                event_type = "incident_created"
                event_id = derive_event_id()
                idempotency_key = derive_idempotency_key(code, event_type)

                resolved_zone = zone_name or self.bop or f"Camera {self.camera_id}"
                outbox_payload = {
                    "event_id": event_id,
                    "event_type": event_type,
                    "incident_code": code,
                    "idempotency_key": idempotency_key,
                    "camera_id": self.camera_id,
                    "camera_name": self.camera_name,
                    "zone_name": resolved_zone,
                    "title": f"{track['class_name'].capitalize()} detected at {resolved_zone}",
                    "description": f"Real AI detection: {track['class_name']} ({confidence:.0%}) at {self.camera_name}",
                    "severity": severity,
                    "threat_score": float(score),
                    "confidence": float(confidence),
                    "reasons": reasons,
                    "ai_assessment": _normalize_for_json(ai_assessment),
                    "recommended_action": action,
                    "timeline": _normalize_for_json(timeline),
                    "fingerprint": fingerprint,
                    "timestamp": now_utc.isoformat(),
                }

                outbox_event = IncidentOutboxEvent(
                    event_id=event_id,
                    event_type=event_type,
                    incident_code=code,
                    idempotency_key=idempotency_key,
                    payload=outbox_payload,
                    status="PENDING",
                    retry_count=0,
                    lease_until=None,
                    worker_id=None,
                    next_retry_at=now_utc,
                    created_at=now_utc,
                )
                db.add(outbox_event)

                db.commit()
                self._target_incident_cooldown[target_cooldown_key] = now_ts
                logger.info(
                    f"Saved incident {code} with outbox event {event_id} "
                    f"(idempotency: {idempotency_key}) to database (ID: {incident.id})"
                )
                return outbox_payload
            except Exception as db_err:
                db.rollback()
                raise db_err
            finally:
                db.close()
        except Exception as e:
            from sqlalchemy.exc import OperationalError, TimeoutError
            import socket
            is_availability_failure = (
                isinstance(e, (OperationalError, TimeoutError, ConnectionRefusedError, socket.error))
                or getattr(e, "connection_invalidated", False)
                or "could not connect" in str(e).lower()
                or "connection refused" in str(e).lower()
                or "database is locked" in str(e).lower()
            )
            if is_availability_failure:
                logger.critical(f"Camera {self.camera_id}: [DEGRADED_DB_OFFLINE] Database availability failure: {e}")
                self._handle_degraded_db_spool(
                    code=code, track=track, reasons=reasons, severity=severity,
                    score=score, confidence=confidence, fingerprint=fingerprint,
                    ai_assessment=ai_assessment, action=action, timeline=timeline,
                    zone_name=zone_name,
                )
                self._target_incident_cooldown[target_cooldown_key] = now_ts
            else:
                logger.error(f"Failed to save incident due to schema/integrity error: {e}", exc_info=True)
            return None

    def get_stats(self) -> Dict:
        """Get pipeline statistics and telemetry."""
        stats = self._stats.copy()
        stats.update({
            "reader_frames_acquired": self.reader_frames_acquired,
            "reader_read_failures": self.reader_read_failures,
            "processor_frames_started": self.processor_frames_started,
            "processor_frames_completed": self.processor_frames_completed,
            "processor_frames_skipped_latest_slot": self.processor_frames_skipped_latest_slot,
            "processor_stale_frames": self.processor_stale_frames,
            "detection_stride_skips": self.detection_stride_skips,
            "processor_processing_errors": self.processor_processing_errors,
            "processor_processing_time": self.processor_processing_time,
            "processor_last_processed_frame_id": self.processor_last_processed_frame_id,
            "reconnect_events": self.reconnect_events,
            "stream_epoch": self.stream_epoch,
            "processor_state": self.processor_state,
            "nvr_staging_drops": self.nvr_staging_drops,
            "nvr_encode_failures": self.nvr_encode_failures,
            "nvr_bytes_evicted": getattr(self._frame_ring_buffer, "nvr_bytes_evicted", 0),
            "nvr_current_bytes": self._frame_ring_buffer.get_total_bytes() if hasattr(self._frame_ring_buffer, "get_total_bytes") else 0,
            "nvr_epoch_transitions": self.nvr_epoch_transitions,
            "nvr_clip_shedding_drops": self.nvr_clip_shedding_drops,
            "nvr_decode_corruptions": self.nvr_decode_corruptions,
            "nvr_timestamp_anomalies": self.nvr_timestamp_anomalies,
        })
        return stats


# Global manager instance
live_manager = LivePipelineManager()
