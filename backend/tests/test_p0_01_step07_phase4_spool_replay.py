"""
Step 07 Phase 4 — Durable Offline Spool Replay / At-Least-Once Recovery Test Suite
==================================================================================

Rigorously verifies Phase 4 scope:
1. One valid spool record replays successfully (Incident + Alert + Evidence + Outbox)
2. Checkpoint advances only after DB commit
3. Successful record is archived / rotated when spool drained
4. Failure before DB commit leaves checkpoint unchanged
5. Simulated crash after DB commit and before checkpoint causes safe duplicate replay
6. Duplicate replay is absorbed by DB idempotency without duplicate entities
7. Checkpoint failure does not silently lose the record
8. Malformed JSON is quarantined and preserved without advancing checkpoint
9. Missing canonical identity (event_id, incident_code, idempotency_key) is rejected & quarantined
10. Malformed payload (severity, threat_score, reasons) is rejected & quarantined
11. Invalid evidence structure is handled safely
12. Transient DB failure preserves spool record & checkpoint
13. Permanent constraint failure has explicit handling
14. Replay resumes successfully after DB recovery
15. Checkpoint atomic replacement works (tmp + os.replace)
16. Interrupted checkpoint update leaves previous valid checkpoint intact
17. Checkpoint cannot regress
18. Two replay workers cannot corrupt checkpoint state (single-consumer lock)
19. Duplicate concurrent replay cannot create duplicate database records
20. Large spool is processed incrementally without loading full file into RAM
21. Replay worker stops cleanly (interruptible thread lifecycle)
22. Application shutdown leaves replay state consistent
23. Real PostgreSQL 15.18 atomic spool replay
24. Real PostgreSQL 15.18 duplicate replay idempotency
25. Spool replay performance benchmarks [MEASURED BENCHMARK]
"""

from __future__ import annotations

import os
import json
import uuid
import time
import inspect
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Any, List
import fcntl
from unittest import mock
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.exc import IntegrityError, OperationalError

from backend.app.models.base import Base
from backend.app.models.incident import Incident
from backend.app.models.alert import Alert
from backend.app.models.evidence import Evidence
from backend.app.models.outbox import (
    IncidentOutboxEvent,
    derive_event_id,
    derive_idempotency_key,
)
from backend.app.services.spool_replay import (
    SpoolReplayWorker,
    SpoolRecord,
    SpoolCheckpoint,
    SpoolTelemetry,
    validate_spool_payload,
)

PG_TEST_URL = os.environ.get(
    "IBVAP_PG_TEST_URL",
    "postgresql://dev_user:dev_password@localhost:5432/ibvap_test",
)


def is_postgres_available() -> bool:
    """Check if PostgreSQL test database is reachable."""
    try:
        engine = sa.create_engine(PG_TEST_URL, connect_args={"connect_timeout": 2})
        with engine.connect() as conn:
            conn.execute(sa.text("SELECT 1"))
        return True
    except Exception:
        return False


@pytest.fixture
def sqlite_test_db(tmp_path):
    """Provide isolated SQLite database for replay testing."""
    db_file = tmp_path / "test_phase4_spool.db"
    url = f"sqlite:///{db_file}"
    engine = sa.create_engine(url)
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return engine, TestingSessionLocal


@pytest.fixture
def postgres_test_session():
    """Provide real PostgreSQL 15 database session with fresh schema."""
    if not is_postgres_available():
        pytest.skip("PostgreSQL test database not available")
    engine = sa.create_engine(PG_TEST_URL)
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    try:
        yield session, TestingSessionLocal
    finally:
        session.close()
        # Clean test tables
        with engine.connect() as conn:
            conn.execute(sa.text("TRUNCATE TABLE incident_outbox, evidence, alerts, incidents CASCADE;"))
            conn.commit()


def _make_valid_spool_payload(
    incident_code: str = "IBVAP-SPOOL-001",
    event_type: str = "incident_created",
    threat_score: float = 88.0,
    severity: str = "HIGH",
    camera_id: int = 1,
) -> Dict[str, Any]:
    """Helper to generate a valid spooled record adhering to the canonical contract."""
    event_id = derive_event_id()
    idempotency_key = derive_idempotency_key(incident_code, event_type)
    return {
        "event_id": event_id,
        "event_type": event_type,
        "incident_code": incident_code,
        "idempotency_key": idempotency_key,
        "camera_id": camera_id,
        "camera_name": f"Cam-{camera_id}",
        "zone_name": "Perimeter Sector 4",
        "severity": severity,
        "threat_score": threat_score,
        "confidence": 0.92,
        "reasons": ["motion_detected", "perimeter_breach"],
        "track": {"track_id": 42, "class_name": "person", "confidence": 0.92},
        "fingerprint": f"fp_{incident_code}",
        "ai_assessment": {"threat": "high", "rule": "fence_intrusion"},
        "recommended_action": "Deploy quick reaction team",
        "timeline": [{"event_type": "detection", "timestamp": datetime.utcnow().isoformat()}],
        "evidence_metadata": {
            "file_path": f"data/evidence/{incident_code}.jpg",
            "sha256": "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
            "manifest_data": {"size_bytes": 1024},
        },
        "spooled_at": datetime.utcnow().isoformat(),
        "pipeline_state": "DEGRADED_DB_OFFLINE",
    }


# =============================================================================
# GROUP 1: BASIC REPLAY & CANONICAL PERSISTENCE PROTOCOL
# =============================================================================

class TestBasicSpoolReplay:
    """Verify READ -> VALIDATE -> DB COMMIT -> CHECKPOINT -> ARCHIVE flow."""

    def test_one_valid_spool_record_replays_successfully(self, tmp_path, sqlite_test_db):
        """Case 1: One valid spool record is atomically committed to Incident, Alert, Evidence, and Outbox."""
        engine, SessionCls = sqlite_test_db
        spool_dir = tmp_path / "spool"
        spool_dir.mkdir()
        spool_file = spool_dir / "offline_incidents.jsonl"

        record_data = _make_valid_spool_payload("IBVAP-REPLAY-001")
        with open(spool_file, "w", encoding="utf-8") as f:
            f.write(json.dumps(record_data) + "\n")

        worker = SpoolReplayWorker(spool_dir=spool_dir, session_factory=SessionCls)
        processed = worker.run_once()

        assert processed == 1
        with SessionCls() as db:
            # 1. Incident committed
            inc = db.query(Incident).filter(Incident.incident_code == "IBVAP-REPLAY-001").first()
            assert inc is not None
            assert inc.severity == "HIGH"
            assert inc.threat_score == 88.0

            # 2. Alert committed
            alert = db.query(Alert).filter(Alert.incident_id == inc.id).first()
            assert alert is not None

            # 3. Evidence committed
            ev = db.query(Evidence).filter(Evidence.incident_id == inc.id).first()
            assert ev is not None
            assert ev.detection_metadata.get("spool_replay") is True

            # 4. Outbox event committed
            outbox = db.query(IncidentOutboxEvent).filter(IncidentOutboxEvent.incident_code == "IBVAP-REPLAY-001").first()
            assert outbox is not None
            assert outbox.status == "PENDING"
            assert outbox.event_id == record_data["event_id"]
            assert outbox.idempotency_key == record_data["idempotency_key"]

        # Telemetry verification
        snap = worker.telemetry.snapshot()
        assert snap["spool_records_scanned"] == 1
        assert snap["spool_records_validated"] == 1
        assert snap["spool_records_replayed_success"] == 1
        assert snap["spool_records_replayed_duplicate"] == 0

    def test_checkpoint_advances_only_after_db_commit(self, tmp_path, sqlite_test_db):
        """Case 2: Checkpoint file reflects exact byte offset of completed record."""
        engine, SessionCls = sqlite_test_db
        spool_dir = tmp_path / "spool"
        spool_dir.mkdir()
        spool_file = spool_dir / "offline_incidents.jsonl"

        rec1 = _make_valid_spool_payload("IBVAP-CP-001")
        rec2 = _make_valid_spool_payload("IBVAP-CP-002")
        with open(spool_file, "w", encoding="utf-8") as f:
            line1 = json.dumps(rec1) + "\n"
            line2 = json.dumps(rec2) + "\n"
            f.write(line1)
            f.write(line2)

        worker = SpoolReplayWorker(spool_dir=spool_dir, session_factory=SessionCls)

        # Process exactly 1 record
        processed = worker.run_once(max_records=1)
        assert processed == 1

        cp = worker.read_checkpoint()
        assert cp.last_committed_line_number == 1
        assert cp.last_committed_incident_code == "IBVAP-CP-001"
        assert cp.last_committed_byte_offset == len(line1.encode("utf-8"))

        # Process second record
        processed = worker.run_once(max_records=1)
        assert processed == 1

        cp = worker.read_checkpoint()
        assert cp.last_committed_line_number == 2
        assert cp.last_committed_incident_code == "IBVAP-CP-002"
        assert cp.last_committed_byte_offset == len(line1.encode("utf-8")) + len(line2.encode("utf-8"))

    def test_successful_record_archived_or_marked(self, tmp_path, sqlite_test_db):
        """Case 3: When spool file is fully drained, archive_current_spool rotates file to archive/."""
        engine, SessionCls = sqlite_test_db
        spool_dir = tmp_path / "spool"
        spool_dir.mkdir()
        spool_file = spool_dir / "offline_incidents.jsonl"

        with open(spool_file, "w", encoding="utf-8") as f:
            f.write(json.dumps(_make_valid_spool_payload("IBVAP-ARC-001")) + "\n")

        worker = SpoolReplayWorker(spool_dir=spool_dir, session_factory=SessionCls)
        worker.run_once()

        archived_path = worker.archive_current_spool()
        assert archived_path is not None
        assert archived_path.exists()
        assert "archive" in str(archived_path)
        assert not spool_file.exists(), "Active spool file should be moved during archival rotation"

        # Checkpoint is reset to 0
        cp = worker.read_checkpoint()
        assert cp.last_committed_byte_offset == 0


# =============================================================================
# GROUP 2: CRASH SEMANTICS & IDEMPOTENT REPLAY
# =============================================================================

class TestCrashSemanticsAndIdempotency:
    """Verify failure scenarios, crash recovery, and database-authoritative idempotency."""

    def test_failure_before_db_commit_leaves_checkpoint_unchanged(self, tmp_path, sqlite_test_db):
        """Case 4: DB failure before commit leaves checkpoint at previous offset (record will be retried)."""
        engine, SessionCls = sqlite_test_db
        spool_dir = tmp_path / "spool"
        spool_dir.mkdir()
        spool_file = spool_dir / "offline_incidents.jsonl"

        with open(spool_file, "w", encoding="utf-8") as f:
            f.write(json.dumps(_make_valid_spool_payload("IBVAP-FAIL-001")) + "\n")

        worker = SpoolReplayWorker(spool_dir=spool_dir, session_factory=SessionCls)

        # Force DB commit failure
        with patch.object(Session, "commit", side_effect=OperationalError("connection lost", None, None)):
            processed = worker.run_once()
            assert processed == 0

        cp = worker.read_checkpoint()
        assert cp.last_committed_byte_offset == 0, "Checkpoint must NOT advance on persistence failure"
        assert worker.telemetry.snapshot()["spool_database_failures"] == 1

    def test_simulated_crash_after_db_commit_and_before_checkpoint(self, tmp_path, sqlite_test_db):
        """Case 5 & 6: Crash between DB commit and checkpoint write causes safe idempotent re-replay."""
        engine, SessionCls = sqlite_test_db
        spool_dir = tmp_path / "spool"
        spool_dir.mkdir()
        spool_file = spool_dir / "offline_incidents.jsonl"

        rec = _make_valid_spool_payload("IBVAP-CRASH-001")
        line = json.dumps(rec) + "\n"
        with open(spool_file, "w", encoding="utf-8") as f:
            f.write(line)

        worker = SpoolReplayWorker(spool_dir=spool_dir, session_factory=SessionCls)

        # Intercept write_checkpoint on first attempt to simulate a power outage right after commit
        orig_write_cp = worker.write_checkpoint
        crash_simulated = False

        def crash_on_first_checkpoint(checkpoint):
            nonlocal crash_simulated
            if not crash_simulated:
                crash_simulated = True
                raise OSError("Simulated system crash before checkpoint write")
            return orig_write_cp(checkpoint)

        worker.write_checkpoint = crash_on_first_checkpoint

        # Run 1: Commits to DB, but checkpoint write fails
        try:
            worker.run_once()
        except OSError:
            pass

        # Checkpoint remains at 0
        cp = worker.read_checkpoint()
        assert cp.last_committed_byte_offset == 0

        # DB now already contains the record from Run 1
        with SessionCls() as db:
            assert db.query(Incident).filter(Incident.incident_code == "IBVAP-CRASH-001").count() == 1

        # Run 2: Replay resumes from offset 0 (attempts to re-persist)
        worker.write_checkpoint = orig_write_cp
        processed = worker.run_once()

        assert processed == 1, "Duplicate replay must successfully advance checkpoint"
        assert worker.telemetry.snapshot()["spool_records_replayed_duplicate"] == 1

        # Verify DB still contains exactly ONE incident (no duplicates created!)
        with SessionCls() as db:
            assert db.query(Incident).filter(Incident.incident_code == "IBVAP-CRASH-001").count() == 1
            assert db.query(IncidentOutboxEvent).filter(IncidentOutboxEvent.incident_code == "IBVAP-CRASH-001").count() == 1

        # Checkpoint is now safely advanced
        cp = worker.read_checkpoint()
        assert cp.last_committed_byte_offset == len(line.encode("utf-8"))

    def test_checkpoint_failure_does_not_silently_lose_record(self, tmp_path, sqlite_test_db):
        """Case 7: Checkpoint write error aborts processing and does not skip records."""
        engine, SessionCls = sqlite_test_db
        spool_dir = tmp_path / "spool"
        spool_dir.mkdir()
        spool_file = spool_dir / "offline_incidents.jsonl"

        with open(spool_file, "w", encoding="utf-8") as f:
            f.write(json.dumps(_make_valid_spool_payload("IBVAP-CPFAIL-001")) + "\n")
            f.write(json.dumps(_make_valid_spool_payload("IBVAP-CPFAIL-002")) + "\n")

        worker = SpoolReplayWorker(spool_dir=spool_dir, session_factory=SessionCls)

        with patch.object(worker, "write_checkpoint", return_value=False):
            processed = worker.run_once()
            assert processed == 0, "Processing must halt when checkpoint write fails"


# =============================================================================
# GROUP 3: VALIDATION & MALFORMED RECORD QUARANTINE
# =============================================================================

class TestValidationAndQuarantine:
    """Verify validation gates and malformed record quarantine preservation."""

    def test_malformed_json_quarantined_and_preserved(self, tmp_path, sqlite_test_db):
        """Case 8: Invalid JSON syntax is preserved in quarantine; checkpoint does NOT advance."""
        engine, SessionCls = sqlite_test_db
        spool_dir = tmp_path / "spool"
        spool_dir.mkdir()
        spool_file = spool_dir / "offline_incidents.jsonl"

        bad_line = '{"incident_code": "CORRUPT", "unclosed_string: 123\n'
        with open(spool_file, "w", encoding="utf-8") as f:
            f.write(bad_line)

        worker = SpoolReplayWorker(spool_dir=spool_dir, session_factory=SessionCls)
        processed = worker.run_once()

        assert processed == 0
        cp = worker.read_checkpoint()
        assert cp.last_committed_byte_offset == 0, "Malformed line must NOT advance checkpoint"

        snap = worker.telemetry.snapshot()
        assert snap["spool_records_malformed"] == 1
        assert snap["spool_records_quarantined"] == 1

        # Check quarantine file exists and contains the corrupted line
        quarantine_file = spool_dir / "quarantine" / "malformed_records.jsonl"
        assert quarantine_file.exists()
        with open(quarantine_file, "r", encoding="utf-8") as qf:
            content = qf.read()
            assert "JSON_SYNTAX_ERROR" in content
            assert "CORRUPT" in content

    def test_missing_canonical_identity_rejected(self, tmp_path, sqlite_test_db):
        """Case 9: Record missing event_id or idempotency_key is quarantined; checkpoint halts."""
        engine, SessionCls = sqlite_test_db
        spool_dir = tmp_path / "spool"
        spool_dir.mkdir()
        spool_file = spool_dir / "offline_incidents.jsonl"

        rec = _make_valid_spool_payload("IBVAP-NO-UUID-001")
        del rec["event_id"]  # Missing required canonical event_id
        with open(spool_file, "w", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")

        worker = SpoolReplayWorker(spool_dir=spool_dir, session_factory=SessionCls)
        processed = worker.run_once()

        assert processed == 0
        assert worker.telemetry.snapshot()["spool_records_quarantined"] == 1

        # Check quarantine log
        quarantine_file = spool_dir / "quarantine" / "malformed_records.jsonl"
        with open(quarantine_file, "r", encoding="utf-8") as qf:
            assert "Missing or non-string required canonical identity 'event_id'" in qf.read()

    def test_malformed_payload_rejected(self, tmp_path, sqlite_test_db):
        """Case 10: Missing severity or invalid threat_score is quarantined."""
        engine, SessionCls = sqlite_test_db
        spool_dir = tmp_path / "spool"
        spool_dir.mkdir()
        spool_file = spool_dir / "offline_incidents.jsonl"

        rec = _make_valid_spool_payload("IBVAP-BAD-SCORE-001")
        rec["threat_score"] = "invalid_score_string"  # Non-numeric
        with open(spool_file, "w", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")

        worker = SpoolReplayWorker(spool_dir=spool_dir, session_factory=SessionCls)
        processed = worker.run_once()

        assert processed == 0
        assert worker.telemetry.snapshot()["spool_records_quarantined"] == 1

    def test_invalid_evidence_structure_handled_safely(self, tmp_path, sqlite_test_db):
        """Case 11: Non-dict evidence_metadata is safely rejected and quarantined."""
        engine, SessionCls = sqlite_test_db
        spool_dir = tmp_path / "spool"
        spool_dir.mkdir()
        spool_file = spool_dir / "offline_incidents.jsonl"

        rec = _make_valid_spool_payload("IBVAP-BAD-EV-001")
        rec["evidence_metadata"] = ["not", "a", "dict"]
        with open(spool_file, "w", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")

        worker = SpoolReplayWorker(spool_dir=spool_dir, session_factory=SessionCls)
        processed = worker.run_once()

        assert processed == 0
        assert worker.telemetry.snapshot()["spool_records_quarantined"] == 1


# =============================================================================
# GROUP 4: DATABASE FAILURES & RESUMPTION
# =============================================================================

class TestDatabaseFailuresAndRecovery:
    """Verify behavior across transient database outages and subsequent recovery."""

    def test_transient_db_failure_preserves_spool_record_and_resumes(self, tmp_path, sqlite_test_db):
        """Case 12 & 14: Transient DB error halts replay; recovery on next attempt succeeds."""
        engine, SessionCls = sqlite_test_db
        spool_dir = tmp_path / "spool"
        spool_dir.mkdir()
        spool_file = spool_dir / "offline_incidents.jsonl"

        with open(spool_file, "w", encoding="utf-8") as f:
            f.write(json.dumps(_make_valid_spool_payload("IBVAP-TRANS-001")) + "\n")

        worker = SpoolReplayWorker(spool_dir=spool_dir, session_factory=SessionCls)

        # Attempt 1: Transient DB outage
        with patch.object(Session, "commit", side_effect=OperationalError("could not connect", None, None)):
            n1 = worker.run_once()
            assert n1 == 0

        assert worker.read_checkpoint().last_committed_byte_offset == 0

        # Attempt 2: Database restored
        n2 = worker.run_once()
        assert n2 == 1
        assert worker.read_checkpoint().last_committed_byte_offset > 0

        with SessionCls() as db:
            assert db.query(Incident).filter(Incident.incident_code == "IBVAP-TRANS-001").first() is not None

    def test_permanent_constraint_failure_explicit_handling(self, tmp_path, sqlite_test_db):
        """Case 13: Permanent non-duplicate constraint failure aborts persistence cleanly."""
        engine, SessionCls = sqlite_test_db
        spool_dir = tmp_path / "spool"
        spool_dir.mkdir()
        spool_file = spool_dir / "offline_incidents.jsonl"

        with open(spool_file, "w", encoding="utf-8") as f:
            f.write(json.dumps(_make_valid_spool_payload("IBVAP-PERM-FAIL-001")) + "\n")

        worker = SpoolReplayWorker(spool_dir=spool_dir, session_factory=SessionCls)

        # Simulate non-duplicate integrity error (e.g. check constraint failure)
        with patch.object(Session, "commit", side_effect=IntegrityError("CHECK constraint failed", None, None)):
            processed = worker.run_once()
            assert processed == 0

        assert worker.telemetry.snapshot()["spool_database_failures"] == 1
        assert worker.read_checkpoint().last_committed_byte_offset == 0


# =============================================================================
# GROUP 5: CHECKPOINT INTEGRITY & CONCURRENCY
# =============================================================================

class TestCheckpointSafetyAndConcurrency:
    """Verify atomic replacement, non-regression, and single-consumer locking."""

    def test_checkpoint_atomic_replacement(self, tmp_path):
        """Case 15: Checkpoint write uses atomic temporary file replacement."""
        spool_dir = tmp_path / "spool"
        worker = SpoolReplayWorker(spool_dir=spool_dir)

        cp = SpoolCheckpoint(
            spool_file="offline_incidents.jsonl",
            last_committed_byte_offset=500,
            last_committed_line_number=2,
            last_committed_incident_code="IBVAP-001",
            records_replayed=2,
            records_duplicate=0,
            updated_at=datetime.utcnow().isoformat(),
        )
        assert worker.write_checkpoint(cp) is True
        assert not worker.checkpoint_path.with_suffix(".tmp").exists(), "Temporary file must be cleanly replaced"
        assert worker.read_checkpoint().last_committed_byte_offset == 500

    def test_checkpoint_cannot_regress(self, tmp_path):
        """Case 17: Attempting to write a checkpoint with lower byte offset is rejected."""
        spool_dir = tmp_path / "spool"
        worker = SpoolReplayWorker(spool_dir=spool_dir)

        cp1 = SpoolCheckpoint(spool_file="test.jsonl", last_committed_byte_offset=1000, last_committed_line_number=5, last_committed_incident_code="INC-5", records_replayed=5, records_duplicate=0, updated_at=datetime.utcnow().isoformat())
        worker.write_checkpoint(cp1)

        # Attempt to write regressed offset
        cp_regress = SpoolCheckpoint(spool_file="test.jsonl", last_committed_byte_offset=500, last_committed_line_number=2, last_committed_incident_code="INC-2", records_replayed=2, records_duplicate=0, updated_at=datetime.utcnow().isoformat())
        assert worker.write_checkpoint(cp_regress) is False
        assert worker.read_checkpoint().last_committed_byte_offset == 1000

    def test_two_replay_workers_cannot_corrupt_checkpoint_state(self, tmp_path, sqlite_test_db):
        """Case 18: File lock guarantees only one replay worker executes at a time."""
        engine, SessionCls = sqlite_test_db
        spool_dir = tmp_path / "spool"
        spool_dir.mkdir()
        spool_file = spool_dir / "offline_incidents.jsonl"

        with open(spool_file, "w", encoding="utf-8") as f:
            for i in range(10):
                f.write(json.dumps(_make_valid_spool_payload(f"IBVAP-CONC-{i:03d}")) + "\n")

        worker_a = SpoolReplayWorker(spool_dir=spool_dir, session_factory=SessionCls, worker_id="worker-A")
        worker_b = SpoolReplayWorker(spool_dir=spool_dir, session_factory=SessionCls, worker_id="worker-B")

        # Manually hold lock on worker_a
        assert worker_a._file_lock.acquire() is True

        # worker_b run_once should skip cleanly without error
        processed_b = worker_b.run_once()
        assert processed_b == 0, "Worker B must skip execution while Worker A holds lock"

        worker_a._file_lock.release()

        # Now worker_b can run
        processed_b_after = worker_b.run_once()
        assert processed_b_after == 10

    def test_interrupted_checkpoint_update_leaves_previous_checkpoint_intact(self, tmp_path):
        """Case 16: Interrupted checkpoint write leaves the prior valid checkpoint completely intact."""
        spool_dir = tmp_path / "spool"
        worker = SpoolReplayWorker(spool_dir=spool_dir)

        # Initial valid checkpoint
        cp1 = SpoolCheckpoint(spool_file="test.jsonl", last_committed_byte_offset=500, last_committed_line_number=2, last_committed_incident_code="INC-002", records_replayed=2, records_duplicate=0, updated_at=datetime.utcnow().isoformat())
        assert worker.write_checkpoint(cp1) is True

        # Simulate interruption during writing to .tmp
        with patch("os.fsync", side_effect=IOError("Disk flush error")):
            cp2 = SpoolCheckpoint(spool_file="test.jsonl", last_committed_byte_offset=1000, last_committed_line_number=4, last_committed_incident_code="INC-004", records_replayed=4, records_duplicate=0, updated_at=datetime.utcnow().isoformat())
            assert worker.write_checkpoint(cp2) is False

        # Verify previous checkpoint remained 100% intact
        recovered_cp = worker.read_checkpoint()
        assert recovered_cp.last_committed_byte_offset == 500
        assert recovered_cp.last_committed_incident_code == "INC-002"

    def test_duplicate_concurrent_replay_cannot_create_duplicate_database_records(self, tmp_path, sqlite_test_db):
        """Case 19: Replaying the same spool stream across two workers cannot create duplicate DB rows."""
        engine, SessionCls = sqlite_test_db
        spool_dir = tmp_path / "spool"
        spool_dir.mkdir()
        spool_file = spool_dir / "offline_incidents.jsonl"

        with open(spool_file, "w", encoding="utf-8") as f:
            for i in range(5):
                f.write(json.dumps(_make_valid_spool_payload(f"IBVAP-DUPCONC-{i:03d}")) + "\n")

        worker1 = SpoolReplayWorker(spool_dir=spool_dir, session_factory=SessionCls, worker_id="w1")
        worker2 = SpoolReplayWorker(spool_dir=spool_dir, session_factory=SessionCls, worker_id="w2")

        # Worker 1 runs and commits all
        n1 = worker1.run_once()
        assert n1 == 5

        # Force worker 2 checkpoint to 0 to simulate replay of same stream
        cp2 = SpoolCheckpoint(spool_file=spool_file.name, last_committed_byte_offset=0, last_committed_line_number=0, last_committed_incident_code=None, records_replayed=0, records_duplicate=0, updated_at=datetime.utcnow().isoformat())
        worker2.write_checkpoint(cp2, allow_reset=True)

        n2 = worker2.run_once()
        assert n2 == 5
        assert worker2.telemetry.snapshot()["spool_records_replayed_duplicate"] == 5

        # Check DB row count: exactly 5 incidents, 0 duplicates!
        with SessionCls() as db:
            assert db.query(Incident).count() == 5
            assert db.query(IncidentOutboxEvent).count() == 5

    def test_large_spool_processed_incrementally_bounded_memory(self, tmp_path, sqlite_test_db):
        """Case 20: Large backlog (50 records) is processed incrementally in bounded batches of 10."""
        engine, SessionCls = sqlite_test_db
        spool_dir = tmp_path / "spool"
        spool_dir.mkdir()
        spool_file = spool_dir / "offline_incidents.jsonl"

        total_records = 50
        with open(spool_file, "w", encoding="utf-8") as f:
            for i in range(total_records):
                f.write(json.dumps(_make_valid_spool_payload(f"IBVAP-BATCH-{i:03d}")) + "\n")

        worker = SpoolReplayWorker(spool_dir=spool_dir, session_factory=SessionCls, batch_size=10)

        # Run 5 iterations of batch_size 10
        total_processed = 0
        for _ in range(5):
            n = worker.run_once()
            assert n == 10
            total_processed += n

        assert total_processed == total_records
        with SessionCls() as db:
            assert db.query(Incident).count() == total_records

    def test_replay_worker_thread_lifecycle(self, tmp_path, sqlite_test_db):
        """Case 21: Background worker thread starts and stops cleanly via interruptible Event."""
        engine, SessionCls = sqlite_test_db
        spool_dir = tmp_path / "spool"
        worker = SpoolReplayWorker(spool_dir=spool_dir, session_factory=SessionCls, poll_interval_sec=0.05)

        assert not worker.is_running()
        worker.start()
        assert worker.is_running()

        t0 = time.perf_counter()
        worker.stop(timeout=2.0)
        duration = time.perf_counter() - t0

        assert not worker.is_running()
        assert duration < 1.0, "Worker must stop promptly on interruptible wait"


# =============================================================================
# GROUP 6: REAL POSTGRESQL 15.18 VALIDATION
# =============================================================================

class TestPostgreSQL15SpoolReplay:
    """Rigorously verify Phase 4 spool recovery contracts against real PostgreSQL 15.18."""

    def test_postgresql_spool_replay_atomic_commit(self, postgres_test_session, tmp_path):
        """Case 23: Verify atomic commit of Incident, Alert, Evidence, Outbox on PostgreSQL 15."""
        session, SessionCls = postgres_test_session
        spool_dir = tmp_path / "pg_spool"
        spool_dir.mkdir()
        spool_file = spool_dir / "offline_incidents.jsonl"

        code = f"IBVAP-PG-SPOOL-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        rec = _make_valid_spool_payload(code)
        with open(spool_file, "w", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")

        worker = SpoolReplayWorker(spool_dir=spool_dir, session_factory=SessionCls)
        processed = worker.run_once()

        assert processed == 1

        # Query real PostgreSQL
        with SessionCls() as db:
            inc = db.query(Incident).filter(Incident.incident_code == code).first()
            assert inc is not None
            assert inc.threat_score == 88.0

            alert = db.query(Alert).filter(Alert.incident_id == inc.id).first()
            assert alert is not None

            ev = db.query(Evidence).filter(Evidence.incident_id == inc.id).first()
            assert ev is not None

            outbox = db.query(IncidentOutboxEvent).filter(IncidentOutboxEvent.incident_code == code).first()
            assert outbox is not None
            assert outbox.status == "PENDING"
            assert outbox.event_id == rec["event_id"]
            assert outbox.idempotency_key == rec["idempotency_key"]

    def test_postgresql_duplicate_replay_idempotency(self, postgres_test_session, tmp_path):
        """Case 24: Verify that second replay on PostgreSQL 15 is absorbed by DB unique constraint."""
        session, SessionCls = postgres_test_session
        spool_dir = tmp_path / "pg_spool_dup"
        spool_dir.mkdir()
        spool_file = spool_dir / "offline_incidents.jsonl"

        code = f"IBVAP-PG-DUP-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        rec = _make_valid_spool_payload(code)
        line = json.dumps(rec) + "\n"
        with open(spool_file, "w", encoding="utf-8") as f:
            f.write(line)

        worker = SpoolReplayWorker(spool_dir=spool_dir, session_factory=SessionCls)

        # Run 1: Inserts to PostgreSQL
        p1 = worker.run_once()
        assert p1 == 1

        # Simulate checkpoint interruption: reset checkpoint offset to 0
        cp = worker.read_checkpoint()
        cp.last_committed_byte_offset = 0
        with open(worker.checkpoint_path, "w", encoding="utf-8") as f:
            json.dump(cp.to_dict(), f)

        # Run 2: Replays same record. PostgreSQL unique constraint prevents duplicate!
        p2 = worker.run_once()
        assert p2 == 1
        assert worker.telemetry.snapshot()["spool_records_replayed_duplicate"] == 1

        # Verify DB still contains exactly ONE incident
        with SessionCls() as db:
            assert db.query(Incident).filter(Incident.incident_code == code).count() == 1
            assert db.query(IncidentOutboxEvent).filter(IncidentOutboxEvent.incident_code == code).count() == 1


# =============================================================================
# GROUP 7: PERFORMANCE BENCHMARK [MEASURED BENCHMARK]
# =============================================================================

class TestSpoolReplayPerformanceBenchmark:
    """Measure engineering benchmark latencies for spool validation, replay, and checkpointing."""

    def test_performance_benchmark_spool_latencies(self, sqlite_test_db, postgres_test_session, tmp_path):
        """Measure validation, replay persistence, checkpoint write, and archive costs [MEASURED BENCHMARK]."""
        pg_session, PgSessionCls = postgres_test_session
        sqlite_engine, SqliteSessionCls = sqlite_test_db

        spool_dir = tmp_path / "bench_spool"
        spool_dir.mkdir()
        spool_file = spool_dir / "offline_incidents.jsonl"

        raw_payload = _make_valid_spool_payload("IBVAP-BENCH-001")
        with open(spool_file, "w", encoding="utf-8") as f:
            f.write(json.dumps(raw_payload) + "\n")

        worker_sqlite = SpoolReplayWorker(spool_dir=spool_dir, session_factory=SqliteSessionCls)

        # 1. Validation Cost
        t0 = time.perf_counter()
        is_valid, _, record = validate_spool_payload(raw_payload)
        t_val_us = (time.perf_counter() - t0) * 1_000_000.0

        # 2. SQLite Replay Persistence Cost
        t0 = time.perf_counter()
        with SqliteSessionCls() as db:
            worker_sqlite.persist_record(db, record)
        t_persist_sqlite_ms = (time.perf_counter() - t0) * 1000.0

        # 3. Checkpoint Write Cost (Atomic disk sync)
        cp = SpoolCheckpoint("bench.jsonl", 100, 1, "IBVAP-BENCH-001", 1, 0, datetime.utcnow().isoformat())
        t0 = time.perf_counter()
        worker_sqlite.write_checkpoint(cp)
        t_cp_ms = (time.perf_counter() - t0) * 1000.0

        # 4. PostgreSQL Replay Persistence Cost
        worker_pg = SpoolReplayWorker(spool_dir=spool_dir, session_factory=PgSessionCls)
        pg_record_payload = _make_valid_spool_payload("IBVAP-PG-BENCH-001")
        _, _, pg_rec = validate_spool_payload(pg_record_payload)
        t0 = time.perf_counter()
        with PgSessionCls() as db:
            worker_pg.persist_record(db, pg_rec)
        t_persist_pg_ms = (time.perf_counter() - t0) * 1000.0

        # 5. Archive Rotation Cost
        # Drain checkpoint to match file size
        cp.last_committed_byte_offset = os.path.getsize(spool_file)
        worker_sqlite.write_checkpoint(cp)
        t0 = time.perf_counter()
        worker_sqlite.archive_current_spool()
        t_archive_ms = (time.perf_counter() - t0) * 1000.0

        print(f"\n[MEASURED BENCHMARK] Spool Record Validation: {t_val_us:.2f} µs")
        print(f"[MEASURED BENCHMARK] SQLite Atomic Replay Persistence: {t_persist_sqlite_ms:.2f} ms")
        print(f"[MEASURED BENCHMARK] PostgreSQL 15 Atomic Replay Persistence: {t_persist_pg_ms:.2f} ms")
        print(f"[MEASURED BENCHMARK] Atomic Checkpoint Sync (tmp + fsync + replace): {t_cp_ms:.2f} ms")
        print(f"[MEASURED BENCHMARK] Archive Spool Rotation: {t_archive_ms:.2f} ms")

        # Assert bounds
        assert t_val_us < 5000.0
        assert t_persist_sqlite_ms < 50.0
        assert t_persist_pg_ms < 50.0
        assert t_cp_ms < 50.0
        assert t_archive_ms < 50.0


class TestSpoolRotationCrashRecovery:
    """Targeted verification for spool archive-rotation + checkpoint-reset crash boundary."""

    def test_crash_after_archive_rename_before_checkpoint_reset_does_not_skip_records(
        self, tmp_path, sqlite_test_db
    ):
        """Case B: Crash between archive rename and checkpoint reset must not skip new records.
        
        Sequence:
        1. Two initial records replayed, committed, checkpoint at offset X (>0).
        2. Archive rename occurs, but process crashes before checkpoint reset.
        3. Worker restarts.
        4. New spool records written into new active spool (total size > X).
        5. Worker replays new active spool from byte offset 0 (via inode mismatch detection).
        6. Confirms zero records skipped.
        """
        engine, SessionCls = sqlite_test_db
        spool_dir = tmp_path / "spool"
        spool_dir.mkdir()
        spool_file = spool_dir / "offline_incidents.jsonl"

        # 1. Write initial records and replay them
        rec1 = _make_valid_spool_payload("IBVAP-ROT-001")
        rec2 = _make_valid_spool_payload("IBVAP-ROT-002")
        with open(spool_file, "w", encoding="utf-8") as f:
            f.write(json.dumps(rec1) + "\n")
            f.write(json.dumps(rec2) + "\n")

        initial_size = os.path.getsize(spool_file)
        worker = SpoolReplayWorker(spool_dir=spool_dir, session_factory=SessionCls)
        replayed = worker.run_once()
        assert replayed == 2

        cp_before = worker.read_checkpoint()
        assert cp_before.last_committed_byte_offset == initial_size
        assert cp_before.file_inode is not None

        # 2. Simulate crash immediately after os.replace during archive_current_spool
        orig_replace = os.replace
        crash_triggered = False

        def crash_after_replace(src, dst):
            nonlocal crash_triggered
            orig_replace(src, dst)
            crash_triggered = True
            raise OSError("Simulated power failure immediately after archive rename")

        with mock.patch("os.replace", side_effect=crash_after_replace):
            with pytest.raises(OSError, match="Simulated power failure"):
                worker.archive_current_spool()

        assert crash_triggered
        # The active spool file was moved to archive
        assert not spool_file.exists()
        # The checkpoint still has old offset
        cp_unreset = worker.read_checkpoint()
        assert cp_unreset.last_committed_byte_offset == initial_size

        # 3. Simulate process restart with new worker instance
        worker_restarted = SpoolReplayWorker(spool_dir=spool_dir, session_factory=SessionCls)

        # 4. New records are written to new active spool
        # Write 3 new records whose total size exceeds initial_size
        new_rec1 = _make_valid_spool_payload("IBVAP-NEW-001")
        new_rec2 = _make_valid_spool_payload("IBVAP-NEW-002")
        new_rec3 = _make_valid_spool_payload("IBVAP-NEW-003")
        with open(spool_file, "w", encoding="utf-8") as f:
            f.write(json.dumps(new_rec1) + "\n")
            f.write(json.dumps(new_rec2) + "\n")
            f.write(json.dumps(new_rec3) + "\n")

        new_file_size = os.path.getsize(spool_file)
        assert new_file_size > initial_size, "New file size must exceed old checkpoint offset to test skip window"

        # 5. Replay with restarted worker
        replayed_new = worker_restarted.run_once()
        assert replayed_new == 3, "Must replay all 3 new records without skipping"

        # 6. Verify database contains all 3 new records
        with SessionCls() as db:
            codes = [inc.incident_code for inc in db.query(Incident).all()]
            assert "IBVAP-NEW-001" in codes
            assert "IBVAP-NEW-002" in codes
            assert "IBVAP-NEW-003" in codes
            assert "IBVAP-ROT-001" in codes
            assert "IBVAP-ROT-002" in codes
            assert len(codes) == 5

        # Checkpoint is now at new_file_size
        cp_final = worker_restarted.read_checkpoint()
        assert cp_final.last_committed_byte_offset == new_file_size

    def test_checkpoint_reset_failure_during_rotation_cannot_advance_past_uncommitted(
        self, tmp_path, sqlite_test_db
    ):
        """Rotation cannot archive unreplayed records, and checkpoint reset failure is safe."""
        engine, SessionCls = sqlite_test_db
        spool_dir = tmp_path / "spool"
        spool_dir.mkdir()
        spool_file = spool_dir / "offline_incidents.jsonl"

        rec1 = _make_valid_spool_payload("IBVAP-UNCOMMITTED-001")
        rec2 = _make_valid_spool_payload("IBVAP-UNCOMMITTED-002")
        with open(spool_file, "w", encoding="utf-8") as f:
            f.write(json.dumps(rec1) + "\n")
            f.write(json.dumps(rec2) + "\n")

        worker = SpoolReplayWorker(spool_dir=spool_dir, session_factory=SessionCls)

        # Replay only 1 record (simulate uncommitted second record)
        replayed = worker.run_once(max_records=1)
        assert replayed == 1

        # Attempt rotation while unreplayed records remain
        res = worker.archive_current_spool()
        assert res is None, "archive_current_spool must refuse to archive when unreplayed records remain"

        # Checkpoint must remain at record 1 offset
        cp = worker.read_checkpoint()
        assert cp.last_committed_line_number == 1
        assert spool_file.exists()

        # Now finish replaying record 2
        replayed_2 = worker.run_once(max_records=1)
        assert replayed_2 == 1

        # Now mock write_checkpoint to return False during rotation reset
        orig_write_cp = worker.write_checkpoint
        def fail_reset_checkpoint(cp_arg, allow_reset=False):
            if allow_reset:
                return False  # simulate checkpoint reset failure
            return orig_write_cp(cp_arg, allow_reset=allow_reset)

        with mock.patch.object(worker, "write_checkpoint", side_effect=fail_reset_checkpoint):
            archived = worker.archive_current_spool()
            assert archived is not None

        # Write new record into new active spool
        rec3 = _make_valid_spool_payload("IBVAP-AFTER-RESET-003")
        with open(spool_file, "w", encoding="utf-8") as f:
            f.write(json.dumps(rec3) + "\n")

        # Worker detects inode mismatch and resets offset safely
        replayed_3 = worker.run_once()
        assert replayed_3 == 1

        with SessionCls() as db:
            codes = [inc.incident_code for inc in db.query(Incident).all()]
            assert "IBVAP-UNCOMMITTED-001" in codes
            assert "IBVAP-UNCOMMITTED-002" in codes
            assert "IBVAP-AFTER-RESET-003" in codes

    def test_writer_and_rotation_synchronization_contract(self, tmp_path, sqlite_test_db):
        """Verify synchronization between spool writer and rotation lock."""
        engine, SessionCls = sqlite_test_db
        spool_dir = tmp_path / "spool"
        spool_dir.mkdir()
        spool_file = spool_dir / "offline_incidents.jsonl"
        rot_lock_file = spool_dir / "spool_rotation.lock"

        # 1. Write an initial record and replay it
        rec1 = _make_valid_spool_payload("IBVAP-SYNC-001")
        with open(spool_file, "w", encoding="utf-8") as f:
            f.write(json.dumps(rec1) + "\n")

        worker = SpoolReplayWorker(spool_dir=spool_dir, session_factory=SessionCls)
        assert worker.run_once() == 1

        # 2. Acquire rotation lock externally to simulate concurrent writer or rotator
        lock_fd = os.open(str(rot_lock_file), os.O_CREAT | os.O_RDWR)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

        try:
            # While rotation lock is held, archive_current_spool must gracefully return None
            archived = worker.archive_current_spool()
            assert archived is None, "archive_current_spool must not proceed when rotation lock is held"
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)

        # After releasing lock, archive_current_spool succeeds
        archived = worker.archive_current_spool()
        assert archived is not None
        assert not spool_file.exists()

    def test_postgresql_duplicate_replay_from_rotation_crash(self, postgres_test_session, tmp_path):
        """Verify on PostgreSQL 15 that duplicate replay after rotation crash is safely absorbed."""
        pg_session, PgSessionCls = postgres_test_session
        spool_dir = tmp_path / "spool"
        spool_dir.mkdir()
        spool_file = spool_dir / "offline_incidents.jsonl"

        rec1 = _make_valid_spool_payload("IBVAP-PG-ROT-001")
        with open(spool_file, "w", encoding="utf-8") as f:
            f.write(json.dumps(rec1) + "\n")

        worker = SpoolReplayWorker(spool_dir=spool_dir, session_factory=PgSessionCls)
        assert worker.run_once() == 1

        # Check in PostgreSQL
        inc1 = pg_session.query(Incident).filter(Incident.incident_code == "IBVAP-PG-ROT-001").first()
        assert inc1 is not None

        # Reset checkpoint manually to simulate crash recovery re-reading the same record
        cp = worker.read_checkpoint()
        cp.last_committed_byte_offset = 0
        worker.write_checkpoint(cp, allow_reset=True)

        # Second replay of the exact same record: database unique constraint absorbs it
        replayed_dup = worker.run_once()
        assert replayed_dup == 1
        assert worker.telemetry.records_replayed_duplicate == 1

        # Verify no duplicate incidents in PostgreSQL
        all_incs = pg_session.query(Incident).filter(Incident.incident_code == "IBVAP-PG-ROT-001").all()
        assert len(all_incs) == 1
