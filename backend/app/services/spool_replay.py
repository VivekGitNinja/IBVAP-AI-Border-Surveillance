"""
Durable Offline Spool Replay Service — At-Least-Once Recovery
============================================================
Provides crash-safe, deterministic replay of incidents spooled to disk during
database availability outages (`data/spool/offline_incidents.jsonl`).

CANONICAL REPLAY PROTOCOL:
READ -> VALIDATE -> DB COMMIT -> CHECKPOINT -> ARCHIVE

CRITICAL INVARIANTS:
1. AT-LEAST-ONCE REPLAY:
   A valid spool record is not considered successfully replayed until its corresponding
   database transaction has committed. Checkpoint advancement occurs ONLY after successful
   database persistence.
2. DATABASE-AUTHORITATIVE IDEMPOTENCY:
   Does NOT use application-level SELECT-then-INSERT. Relies strictly on database UNIQUE
   constraints (`incidents.incident_code` and `incident_outbox.idempotency_key`).
   Duplicate replays are safely absorbed by rollback upon IntegrityError and verified in DB.
3. CRASH-SAFE ATOMIC CHECKPOINT:
   Checkpoint file is updated by writing to a temporary file (`.tmp`), flushing, syncing,
   and atomically renaming via `os.replace`. Checkpoint cannot regress.
4. MALFORMED RECORD ISOLATION (QUARANTINE):
   Malformed records (invalid JSON, missing canonical identity, invalid UUIDs, bad schema)
   are isolated to `data/spool/quarantine/malformed_records.jsonl`.
   Malformed records do NOT advance the committed checkpoint.
5. SINGLE-CONSUMER CONCURRENCY PROTECTION:
   Acquires an exclusive OS-level non-blocking file lock on `data/spool/spool_replay.lock`
   plus in-process thread locking to prevent concurrent checkpoint racing.
6. BOUNDED MEMORY CONSUMPTION:
   Reads spool files incrementally line-by-line (`readline()` with exact `tell()` offsets).
   Never loads full JSONL files into memory.
"""

from __future__ import annotations

import os
import sys
import json
import uuid
import time
import fcntl
import logging
import hashlib
import threading
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple, Callable

import sqlalchemy as sa
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError, OperationalError

from backend.app.db.session import SessionLocal
from backend.app.models.incident import Incident
from backend.app.models.alert import Alert
from backend.app.models.evidence import Evidence
from backend.app.models.outbox import (
    IncidentOutboxEvent,
    derive_event_id,
    derive_idempotency_key,
)

logger = logging.getLogger(__name__)

DEFAULT_SPOOL_DIR = "data/spool"
DEFAULT_SPOOL_FILE = "offline_incidents.jsonl"
DEFAULT_CHECKPOINT_FILE = "offline_incidents.checkpoint"
DEFAULT_QUARANTINE_FILE = "quarantine/malformed_records.jsonl"
DEFAULT_ARCHIVE_DIR = "archive"
DEFAULT_LOCK_FILE = "spool_replay.lock"
DEFAULT_ROTATION_LOCK_FILE = "spool_rotation.lock"
DEFAULT_BATCH_SIZE = 50
DEFAULT_POLL_INTERVAL_SEC = 2.0


@dataclass(frozen=True)
class SpoolRecord:
    """Immutable in-memory representation of a validated spooled incident record."""
    event_id: str
    event_type: str
    incident_code: str
    idempotency_key: str
    camera_id: Optional[int]
    camera_name: str
    zone_name: str
    severity: str
    threat_score: float
    confidence: float
    reasons: List[str]
    fingerprint: str
    ai_assessment: Dict[str, Any]
    recommended_action: str
    timeline: List[Dict[str, Any]]
    track: Dict[str, Any]
    evidence_metadata: Optional[Dict[str, Any]]
    spooled_at: str
    raw_payload: Dict[str, Any]


@dataclass
class SpoolCheckpoint:
    """Deterministic, crash-safe checkpoint representing committed replay progress."""
    spool_file: str
    last_committed_byte_offset: int
    last_committed_line_number: int
    last_committed_incident_code: Optional[str]
    records_replayed: int
    records_duplicate: int
    updated_at: str
    file_inode: Optional[int] = None
    file_device: Optional[int] = None
    version: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "spool_file": self.spool_file,
            "last_committed_byte_offset": self.last_committed_byte_offset,
            "last_committed_line_number": self.last_committed_line_number,
            "last_committed_incident_code": self.last_committed_incident_code,
            "records_replayed": self.records_replayed,
            "records_duplicate": self.records_duplicate,
            "updated_at": self.updated_at,
            "file_inode": self.file_inode,
            "file_device": self.file_device,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SpoolCheckpoint:
        return cls(
            version=data.get("version", 1),
            spool_file=data.get("spool_file", DEFAULT_SPOOL_FILE),
            last_committed_byte_offset=int(data.get("last_committed_byte_offset", 0)),
            last_committed_line_number=int(data.get("last_committed_line_number", 0)),
            last_committed_incident_code=data.get("last_committed_incident_code"),
            records_replayed=int(data.get("records_replayed", 0)),
            records_duplicate=int(data.get("records_duplicate", 0)),
            updated_at=data.get("updated_at", datetime.utcnow().isoformat()),
            file_inode=data.get("file_inode"),
            file_device=data.get("file_device"),
        )


class SpoolTelemetry:
    """Thread-safe counters for spool replay and recovery operations."""

    def __init__(self):
        self._lock = threading.Lock()
        self.records_scanned = 0
        self.records_validated = 0
        self.records_replayed_success = 0
        self.records_replayed_duplicate = 0
        self.records_malformed = 0
        self.records_quarantined = 0
        self.database_failures = 0
        self.checkpoint_writes = 0
        self.archive_operations = 0

    def snapshot(self) -> Dict[str, int]:
        with self._lock:
            return {
                "spool_records_scanned": self.records_scanned,
                "spool_records_validated": self.records_validated,
                "spool_records_replayed_success": self.records_replayed_success,
                "spool_records_replayed_duplicate": self.records_replayed_duplicate,
                "spool_records_malformed": self.records_malformed,
                "spool_records_quarantined": self.records_quarantined,
                "spool_database_failures": self.database_failures,
                "spool_checkpoint_writes": self.checkpoint_writes,
                "spool_archive_operations": self.archive_operations,
            }


class SpoolLock:
    """OS-level exclusive non-blocking advisory file lock for single-consumer replay."""

    def __init__(self, lock_path: Path):
        self.lock_path = lock_path
        self._fd: Optional[int] = None

    def acquire(self) -> bool:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._fd = os.open(str(self.lock_path), os.O_CREAT | os.O_RDWR)
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except (BlockingIOError, IOError, OSError):
            if self._fd is not None:
                try:
                    os.close(self._fd)
                except Exception:
                    pass
                self._fd = None
            return False

    def release(self) -> None:
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
                os.close(self._fd)
            except Exception:
                pass
            self._fd = None


def validate_spool_payload(raw: Dict[str, Any]) -> Tuple[bool, Optional[str], Optional[SpoolRecord]]:
    """Validate raw spooled record against the canonical identity and persistence schema.

    Returns:
        (is_valid: bool, error_message: Optional[str], record: Optional[SpoolRecord])
    """
    if not isinstance(raw, dict):
        return False, "Record must be a JSON object dictionary", None

    # 1. Canonical Identity Validation
    event_id = raw.get("event_id")
    incident_code = raw.get("incident_code")
    event_type = raw.get("event_type", "incident_created")
    idempotency_key = raw.get("idempotency_key")

    if not event_id or not isinstance(event_id, str):
        return False, "Missing or non-string required canonical identity 'event_id'", None
    try:
        uuid_obj = uuid.UUID(event_id)
        if uuid_obj.version != 4:
            return False, f"Canonical 'event_id' must be UUIDv4, got version {uuid_obj.version}", None
    except Exception:
        return False, f"Invalid UUID format for 'event_id': {event_id}", None

    if not incident_code or not isinstance(incident_code, str) or not incident_code.strip():
        return False, "Missing or empty required canonical identity 'incident_code'", None

    if not event_type or not isinstance(event_type, str) or not event_type.strip():
        return False, "Missing or empty required canonical identity 'event_type'", None

    if not idempotency_key or not isinstance(idempotency_key, str):
        return False, "Missing or non-string required canonical identity 'idempotency_key'", None
    try:
        uuid_key = uuid.UUID(idempotency_key)
        if uuid_key.version != 5:
            return False, f"Canonical 'idempotency_key' must be UUIDv5, got version {uuid_key.version}", None
    except Exception:
        return False, f"Invalid UUID format for 'idempotency_key': {idempotency_key}", None

    # 2. Incident Payload Validation
    severity = raw.get("severity")
    if not severity or not isinstance(severity, str):
        return False, "Missing or non-string required payload 'severity'", None

    threat_score = raw.get("threat_score")
    if threat_score is None or not isinstance(threat_score, (int, float)):
        return False, "Missing or non-numeric required payload 'threat_score'", None

    confidence = raw.get("confidence", 0.8)
    if not isinstance(confidence, (int, float)):
        return False, "Invalid non-numeric 'confidence'", None

    reasons = raw.get("reasons")
    if reasons is None or not isinstance(reasons, list):
        return False, "Missing or non-list required payload 'reasons'", None

    # 3. Evidence Metadata Validation (when present)
    ev_meta = raw.get("evidence_metadata")
    if ev_meta is not None and not isinstance(ev_meta, dict):
        return False, "Invalid 'evidence_metadata'; must be a dictionary when present", None

    # Construct validated SpoolRecord
    record = SpoolRecord(
        event_id=str(event_id),
        event_type=str(event_type),
        incident_code=str(incident_code),
        idempotency_key=str(idempotency_key),
        camera_id=raw.get("camera_id"),
        camera_name=str(raw.get("camera_name", "Unknown")),
        zone_name=str(raw.get("zone_name", "Monitored Area")),
        severity=str(severity),
        threat_score=float(threat_score),
        confidence=float(confidence),
        reasons=list(reasons),
        fingerprint=str(raw.get("fingerprint", "")),
        ai_assessment=raw.get("ai_assessment", {}) if isinstance(raw.get("ai_assessment"), dict) else {},
        recommended_action=str(raw.get("recommended_action", "Verify incident on live feed.")),
        timeline=raw.get("timeline", []) if isinstance(raw.get("timeline"), list) else [],
        track=raw.get("track", {}) if isinstance(raw.get("track"), dict) else {},
        evidence_metadata=ev_meta,
        spooled_at=str(raw.get("spooled_at", datetime.utcnow().isoformat())),
        raw_payload=raw,
    )
    return True, None, record


class SpoolReplayWorker:
    """Durable background worker for recovering offline spooled incidents."""

    def __init__(
        self,
        spool_dir: Optional[str | Path] = None,
        spool_filename: str = DEFAULT_SPOOL_FILE,
        checkpoint_filename: str = DEFAULT_CHECKPOINT_FILE,
        session_factory: Optional[Callable[[], Session]] = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
        poll_interval_sec: float = DEFAULT_POLL_INTERVAL_SEC,
        worker_id: Optional[str] = None,
    ):
        self.spool_dir = Path(spool_dir or DEFAULT_SPOOL_DIR).resolve()
        self.spool_path = self.spool_dir / spool_filename
        self.checkpoint_path = self.spool_dir / checkpoint_filename
        self.quarantine_path = self.spool_dir / DEFAULT_QUARANTINE_FILE
        self.archive_dir = self.spool_dir / DEFAULT_ARCHIVE_DIR
        self.lock_path = self.spool_dir / DEFAULT_LOCK_FILE

        self.session_factory = session_factory or SessionLocal
        self.batch_size = batch_size
        self.poll_interval_sec = poll_interval_sec
        self.worker_id = worker_id or f"spool-replay-{os.getpid()}-{uuid.uuid4().hex[:8]}"

        self.telemetry = SpoolTelemetry()
        self._file_lock = SpoolLock(self.lock_path)
        self._in_process_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # Ensure directories exist
        self.spool_dir.mkdir(parents=True, exist_ok=True)
        self.quarantine_path.parent.mkdir(parents=True, exist_ok=True)
        self.archive_dir.mkdir(parents=True, exist_ok=True)

    # ─────────────────────────────────────────────────────────────────────────
    # CHECKPOINT PERSISTENCE (CRASH-SAFE ATOMIC REPLACEMENT)
    # ─────────────────────────────────────────────────────────────────────────

    def read_checkpoint(self) -> SpoolCheckpoint:
        """Read existing checkpoint or return default offset 0 checkpoint."""
        if not self.checkpoint_path.exists():
            return SpoolCheckpoint(
                spool_file=self.spool_path.name,
                last_committed_byte_offset=0,
                last_committed_line_number=0,
                last_committed_incident_code=None,
                records_replayed=0,
                records_duplicate=0,
                updated_at=datetime.utcnow().isoformat(),
            )

        try:
            with open(self.checkpoint_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return SpoolCheckpoint.from_dict(data)
        except Exception as e:
            logger.error(f"SpoolReplayWorker {self.worker_id}: Failed to parse checkpoint file {self.checkpoint_path}: {e}")
            # Fallback safely to initial state
            return SpoolCheckpoint(
                spool_file=self.spool_path.name,
                last_committed_byte_offset=0,
                last_committed_line_number=0,
                last_committed_incident_code=None,
                records_replayed=0,
                records_duplicate=0,
                updated_at=datetime.utcnow().isoformat(),
            )

    def write_checkpoint(self, checkpoint: SpoolCheckpoint, allow_reset: bool = False) -> bool:
        """Crash-safe atomic checkpoint replacement using temporary file and os.replace().

        Invariants:
        1. Never truncates the existing checkpoint file first.
        2. Enforces checkpoint monotonic progression (cannot regress), unless allow_reset=True (e.g. after archiving).
        3. Flushes and syncs to disk before atomic rename.
        """
        # Read current checkpoint to prevent regression
        current = self.read_checkpoint()
        if not allow_reset and checkpoint.last_committed_byte_offset < current.last_committed_byte_offset:
            is_same_file = (
                current.file_inode is not None
                and checkpoint.file_inode is not None
                and current.file_inode == checkpoint.file_inode
            )
            if is_same_file or (current.file_inode is None and checkpoint.file_inode is None):
                logger.error(
                    f"SpoolReplayWorker {self.worker_id}: Checkpoint regression rejected: "
                    f"proposed offset {checkpoint.last_committed_byte_offset} < current offset {current.last_committed_byte_offset}"
                )
                return False

        tmp_path = self.checkpoint_path.with_suffix(".tmp")
        data = checkpoint.to_dict()
        payload = json.dumps(data, indent=2) + "\n"

        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())

            os.replace(tmp_path, self.checkpoint_path)
            with self.telemetry._lock:
                self.telemetry.checkpoint_writes += 1
            return True
        except Exception as e:
            logger.critical(f"SpoolReplayWorker {self.worker_id}: Checkpoint write failure: {e}", exc_info=True)
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass
            return False

    # ─────────────────────────────────────────────────────────────────────────
    # QUARANTINE / MALFORMED HANDLING
    # ─────────────────────────────────────────────────────────────────────────

    def _quarantine_record(self, raw_line: str, reason: str) -> None:
        """Preserve unrecoverable malformed spool line in dedicated quarantine file."""
        self.quarantine_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "quarantined_at": datetime.utcnow().isoformat(),
            "worker_id": self.worker_id,
            "reason": reason,
            "spool_file": self.spool_path.name,
            "raw_line": raw_line.strip(),
        }
        try:
            with open(self.quarantine_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
                f.flush()
                os.fsync(f.fileno())
            logger.warning(
                f"SpoolReplayWorker {self.worker_id}: Record quarantined: {reason} (logged to {self.quarantine_path})"
            )
        except Exception as q_err:
            logger.critical(f"SpoolReplayWorker {self.worker_id}: Failed to write to quarantine file: {q_err}")

    # ─────────────────────────────────────────────────────────────────────────
    # DATABASE PERSISTENCE (DATABASE-AUTHORITATIVE IDEMPOTENCY)
    # ─────────────────────────────────────────────────────────────────────────

    def persist_record(self, db: Session, record: SpoolRecord) -> Tuple[bool, bool]:
        """Atomically persist Incident + Alert + Evidence + IncidentOutboxEvent.

        Invariants:
        1. Single atomic transaction committing all 4 entities together.
        2. Database uniqueness constraints on `incident_code` and `idempotency_key` are authoritative.
        3. NO application-level SELECT-then-INSERT deduplication.
        4. If an IntegrityError is raised, transaction is rolled back, existing record presence is
           verified, and the record is safely recognized as duplicate without overwriting newer state.

        Returns:
            (success: bool, is_duplicate: bool)
        """
        now_utc = datetime.utcnow()
        try:
            # 1. Primary Incident Entity
            incident = Incident(
                incident_code=record.incident_code,
                title=f"{record.track.get('class_name', 'Target').capitalize()} detected at {record.zone_name}",
                description=f"Offline replayed detection: {record.track.get('class_name', 'target')} ({record.confidence:.0%}) at {record.camera_name}",
                severity=record.severity,
                threat_score=record.threat_score,
                confidence=record.confidence,
                status="OPEN",
                reason_codes=record.reasons,
                event_ids=[],
                detection_ids=[],
                track_ids=[record.track.get("track_id")] if record.track.get("track_id") is not None else [],
                camera_id=record.camera_id,
                camera_name=record.camera_name,
                zone_name=record.zone_name,
                fingerprint=record.fingerprint,
                correlated_ids=[],
                recommended_action=record.recommended_action,
                ai_assessment=record.ai_assessment,
                timeline=record.timeline,
                created_at=now_utc,
            )
            db.add(incident)
            db.flush()

            # 2. Alert Notification Entity
            alert = Alert(
                incident_id=incident.id,
                priority=record.severity,
                status="NEW",
                message=f"{record.severity} incident: {record.track.get('class_name', 'Target').capitalize()} detected at {record.zone_name} (Score: {record.threat_score:.0f}/100)",
                created_at=now_utc,
            )
            db.add(alert)

            # 3. Evidence Seal Entity
            ev_meta = record.evidence_metadata or {}
            m_path = ev_meta.get("manifest_path") or ev_meta.get("file_path") or f"data/evidence/manifests/{record.incident_code}.json"
            m_hash = ev_meta.get("sha256") or hashlib.sha256(record.incident_code.encode()).hexdigest()
            m_data = ev_meta.get("manifest_data") or {"source": "spool_replay", "code": record.incident_code}

            evidence = Evidence(
                incident_id=incident.id,
                evidence_type="snapshot",
                file_path=m_path,
                sha256=m_hash,
                manifest_path=m_path,
                manifest_data=m_data,
                threat_score=record.threat_score,
                camera_id=record.camera_id,
                camera_name=record.camera_name,
                detection_metadata={"spool_replay": True, "offline_recovered": True},
                created_at=now_utc,
            )
            db.add(evidence)

            # 4. Transactional Outbox Event Entity
            outbox_payload = {
                "event_id": record.event_id,
                "event_type": record.event_type,
                "incident_code": record.incident_code,
                "idempotency_key": record.idempotency_key,
                "camera_id": record.camera_id,
                "camera_name": record.camera_name,
                "zone_name": record.zone_name,
                "title": incident.title,
                "description": incident.description,
                "severity": record.severity,
                "threat_score": float(record.threat_score),
                "confidence": float(record.confidence),
                "reasons": record.reasons,
                "ai_assessment": record.ai_assessment,
                "recommended_action": record.recommended_action,
                "timeline": record.timeline,
                "fingerprint": record.fingerprint,
                "timestamp": now_utc.isoformat(),
                "source": "spool_replay",
            }
            outbox_event = IncidentOutboxEvent(
                event_id=record.event_id,
                event_type=record.event_type,
                incident_code=record.incident_code,
                idempotency_key=record.idempotency_key,
                payload=outbox_payload,
                status="PENDING",
                retry_count=0,
                lease_until=None,
                worker_id=None,
                next_retry_at=now_utc,
                created_at=now_utc,
            )
            db.add(outbox_event)

            # Atomic single-transaction commit
            db.commit()
            with self.telemetry._lock:
                self.telemetry.records_replayed_success += 1
            return True, False

        except IntegrityError as e:
            db.rollback()
            # Database unique constraint prevented duplicate
            # Verify that record was indeed already persisted previously
            existing_inc = db.query(Incident).filter(Incident.incident_code == record.incident_code).first()
            existing_outbox = db.query(IncidentOutboxEvent).filter(IncidentOutboxEvent.idempotency_key == record.idempotency_key).first()

            if existing_inc or existing_outbox:
                logger.info(
                    f"SpoolReplayWorker {self.worker_id}: Authoritative uniqueness constraint absorbed duplicate "
                    f"replay for incident {record.incident_code} (idempotency: {record.idempotency_key})"
                )
                with self.telemetry._lock:
                    self.telemetry.records_replayed_duplicate += 1
                return True, True
            else:
                # Permanent constraint failure (e.g. foreign key or check violation)
                logger.error(f"SpoolReplayWorker {self.worker_id}: Permanent schema constraint failure for {record.incident_code}: {e}")
                with self.telemetry._lock:
                    self.telemetry.database_failures += 1
                return False, False

        except Exception as e:
            db.rollback()
            logger.warning(f"SpoolReplayWorker {self.worker_id}: Transient database error persisting {record.incident_code}: {e}")
            with self.telemetry._lock:
                self.telemetry.database_failures += 1
            return False, False

    # ─────────────────────────────────────────────────────────────────────────
    # PROTOCOL EXECUTION (READ -> VALIDATE -> DB COMMIT -> CHECKPOINT)
    # ─────────────────────────────────────────────────────────────────────────

    def run_once(self, max_records: Optional[int] = None) -> int:
        """Execute one bounded replay iteration across the spool file.

        Returns:
            int: Number of records successfully committed/advanced in this iteration.
        """
        # Enforce single-consumer concurrency across processes and threads
        if not self._in_process_lock.acquire(blocking=False):
            return 0

        try:
            if not self._file_lock.acquire():
                logger.debug(f"SpoolReplayWorker {self.worker_id}: Another worker holds spool file lock; skipping.")
                return 0

            try:
                if not self.spool_path.exists():
                    return 0

                stat_info = os.stat(self.spool_path)
                current_file_size = stat_info.st_size
                current_inode = stat_info.st_ino
                current_device = stat_info.st_dev

                checkpoint = self.read_checkpoint()

                # Detect if the spool file has been rotated/recreated (inode mismatch) or truncated
                is_inode_mismatch = (
                    checkpoint.file_inode is not None
                    and (checkpoint.file_inode != current_inode or checkpoint.file_device != current_device)
                )
                is_file_truncated = current_file_size < checkpoint.last_committed_byte_offset

                if is_inode_mismatch or is_file_truncated:
                    reason = "inode mismatch (spool rotated/recreated)" if is_inode_mismatch else "file truncated"
                    logger.warning(
                        f"SpoolReplayWorker {self.worker_id}: Spool file {reason} "
                        f"(size {current_file_size}, ino {current_inode} vs checkpoint ino {checkpoint.file_inode}, "
                        f"checkpoint offset {checkpoint.last_committed_byte_offset}). Resetting offset to 0."
                    )
                    checkpoint.last_committed_byte_offset = 0
                    checkpoint.last_committed_line_number = 0
                    checkpoint.file_inode = current_inode
                    checkpoint.file_device = current_device
                    self.write_checkpoint(checkpoint, allow_reset=True)
                elif checkpoint.file_inode is None:
                    checkpoint.file_inode = current_inode
                    checkpoint.file_device = current_device

                if current_file_size == checkpoint.last_committed_byte_offset:
                    return 0

                records_replayed_batch = 0
                target_limit = max_records or self.batch_size

                with open(self.spool_path, "r", encoding="utf-8") as f:
                    f.seek(checkpoint.last_committed_byte_offset)

                    while not self._stop_event.is_set() and records_replayed_batch < target_limit:
                        record_start_offset = f.tell()
                        line = f.readline()
                        if not line:
                            break  # Reached end of current spool file
                        record_end_offset = f.tell()

                        # Skip empty whitespace lines cleanly
                        if not line.strip():
                            checkpoint.last_committed_byte_offset = record_end_offset
                            checkpoint.last_committed_line_number += 1
                            self.write_checkpoint(checkpoint)
                            continue

                        with self.telemetry._lock:
                            self.telemetry.records_scanned += 1

                        # Step 1: Parse JSON
                        try:
                            raw = json.loads(line)
                        except Exception as json_err:
                            with self.telemetry._lock:
                                self.telemetry.records_malformed += 1
                                self.telemetry.records_quarantined += 1
                            self._quarantine_record(line, f"JSON_SYNTAX_ERROR: {json_err}")
                            # Malformed record must NOT advance checkpoint. Halt batch.
                            logger.error(
                                f"SpoolReplayWorker {self.worker_id}: Malformed JSON at byte offset {record_start_offset}. "
                                f"Quarantined; halting replay batch to protect checkpoint."
                            )
                            break

                        # Step 2: Validate Canonical Identity & Payload
                        is_valid, err_msg, record = validate_spool_payload(raw)
                        if not is_valid or record is None:
                            with self.telemetry._lock:
                                self.telemetry.records_malformed += 1
                                self.telemetry.records_quarantined += 1
                            self._quarantine_record(line, f"CANONICAL_VALIDATION_ERROR: {err_msg}")
                            # Malformed record must NOT advance checkpoint. Halt batch.
                            logger.error(
                                f"SpoolReplayWorker {self.worker_id}: Validation error at byte offset {record_start_offset}: {err_msg}. "
                                f"Quarantined; halting replay batch."
                            )
                            break

                        with self.telemetry._lock:
                            self.telemetry.records_validated += 1

                        # Step 3: Database Atomic Persistence (Authoritative Idempotency)
                        with self.session_factory() as db:
                            success, is_duplicate = self.persist_record(db, record)

                        if not success:
                            # DB failure: Do NOT advance checkpoint; stop replay batch to retry later
                            logger.warning(
                                f"SpoolReplayWorker {self.worker_id}: Persistence failed for {record.incident_code}. "
                                f"Preserving checkpoint at byte offset {checkpoint.last_committed_byte_offset}."
                            )
                            break

                        # Step 4: Advance Checkpoint Atomically (Only after DB commit!)
                        checkpoint.last_committed_byte_offset = record_end_offset
                        checkpoint.last_committed_line_number += 1
                        checkpoint.last_committed_incident_code = record.incident_code
                        checkpoint.file_inode = current_inode
                        checkpoint.file_device = current_device
                        checkpoint.updated_at = datetime.utcnow().isoformat()
                        if is_duplicate:
                            checkpoint.records_duplicate += 1
                        else:
                            checkpoint.records_replayed += 1

                        cp_written = self.write_checkpoint(checkpoint)
                        if not cp_written:
                            logger.critical(
                                f"SpoolReplayWorker {self.worker_id}: Failed to write checkpoint after DB commit. "
                                f"Halting to avoid desynchronization."
                            )
                            break

                        records_replayed_batch += 1

                return records_replayed_batch

            finally:
                self._file_lock.release()
        finally:
            self._in_process_lock.release()

    # ─────────────────────────────────────────────────────────────────────────
    # ARCHIVAL / ROTATION
    # ─────────────────────────────────────────────────────────────────────────

    def archive_current_spool(self) -> Optional[Path]:
        """Rotate and archive fully-replayed active spool file to `data/spool/archive/`.

        Invariants:
        1. Only archives if ALL records up to EOF have been committed and checkpointed.
        2. Atomically moves file and resets checkpoint offset to 0.
        3. Synchronizes with concurrent writers using rotation lock.
        """
        rot_lock = SpoolLock(self.spool_dir / DEFAULT_ROTATION_LOCK_FILE)
        if not rot_lock.acquire():
            logger.debug(f"SpoolReplayWorker {self.worker_id}: Cannot acquire rotation lock; skipping archive.")
            return None

        try:
            if not self._file_lock.acquire():
                return None

            try:
                if not self.spool_path.exists():
                    return None

                checkpoint = self.read_checkpoint()
                current_size = os.path.getsize(self.spool_path)

                if current_size == 0:
                    return None

                if checkpoint.last_committed_byte_offset < current_size:
                    logger.warning(
                        f"SpoolReplayWorker {self.worker_id}: Cannot archive spool file {self.spool_path.name}; "
                        f"unreplayed records remain (offset {checkpoint.last_committed_byte_offset} < size {current_size})."
                    )
                    return None

                timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
                archive_target = self.archive_dir / f"offline_incidents_{timestamp}.jsonl"

                # Atomically move spool file to archive directory
                os.replace(self.spool_path, archive_target)

                # Reset checkpoint
                reset_checkpoint = SpoolCheckpoint(
                    spool_file=self.spool_path.name,
                    last_committed_byte_offset=0,
                    last_committed_line_number=0,
                    last_committed_incident_code=None,
                    records_replayed=checkpoint.records_replayed,
                    records_duplicate=checkpoint.records_duplicate,
                    updated_at=datetime.utcnow().isoformat(),
                    file_inode=None,
                    file_device=None,
                )
                cp_written = self.write_checkpoint(reset_checkpoint, allow_reset=True)
                if not cp_written:
                    logger.critical(
                        f"SpoolReplayWorker {self.worker_id}: Checkpoint reset failed during archive rotation!"
                    )

                with self.telemetry._lock:
                    self.telemetry.archive_operations += 1

                logger.info(f"SpoolReplayWorker {self.worker_id}: Fully replayed spool archived to {archive_target}")
                return archive_target

            finally:
                self._file_lock.release()
        finally:
            rot_lock.release()

    # ─────────────────────────────────────────────────────────────────────────
    # BACKGROUND WORKER THREAD LIFECYCLE
    # ─────────────────────────────────────────────────────────────────────────

    def _worker_loop(self) -> None:
        """Continuous background recovery loop with interruptible polling."""
        logger.info(f"SpoolReplayWorker {self.worker_id} background loop started.")
        while not self._stop_event.is_set():
            try:
                processed = self.run_once(max_records=self.batch_size)
                if processed == 0:
                    self._stop_event.wait(timeout=self.poll_interval_sec)
            except Exception as e:
                logger.error(f"SpoolReplayWorker {self.worker_id} unhandled error in worker loop: {e}", exc_info=True)
                self._stop_event.wait(timeout=min(self.poll_interval_sec, 2.0))
        logger.info(f"SpoolReplayWorker {self.worker_id} background loop stopped.")

    def start(self) -> None:
        """Start background recovery thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._worker_loop,
            name=f"spool-replay-{self.worker_id[:16]}",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Signal background worker to stop and join thread."""
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
            self._thread = None

    def is_running(self) -> bool:
        """Check if background worker thread is alive."""
        return self._thread is not None and self._thread.is_alive()


# Global Spool Replay Worker Singleton
global_spool_replay_worker = SpoolReplayWorker()


def get_spool_replay_worker() -> SpoolReplayWorker:
    """Retrieve global spool replay worker instance."""
    return global_spool_replay_worker
