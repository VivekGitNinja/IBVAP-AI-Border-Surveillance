"""
Step 07 Phase 2 — Transactional Outbox Persistence in live_pipeline.py Verification Suite
=======================================================================================

Rigorously verifies Phase 2 scope:
A. Successful Incident + Evidence + Outbox atomic commit
B. Incident failure rolls everything back
C. Evidence failure rolls everything back
D. Outbox failure rolls everything back
E. Cooldown rejection creates no outbox and fires no callback
F. Suppression rejection creates no outbox and fires no callback
G. Successful commit creates exactly one outbox row
H. Initial outbox status == PENDING
I. retry_count == 0
J. Canonical event_id stored correctly
K. Canonical idempotency_key stored correctly
L. Payload contains the same canonical identity values
M. Duplicate idempotency constraint is enforced
N. No pre-commit WebSocket delivery / callback
O. Process/transaction failure before commit leaves no committed outbox row
P. Real PostgreSQL transaction behavior (where PostgreSQL is available)
Q. Existing incident/evidence persistence remains intact
"""

import os
import json
import uuid
import time
import inspect
from datetime import datetime, timedelta
from typing import Dict, Any, List

import pytest
import numpy as np
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.exc import IntegrityError

from backend.app.models.base import Base
from backend.app.models.incident import Incident
from backend.app.models.alert import Alert
from backend.app.models.evidence import Evidence
from backend.app.models.suppression import OperatorSuppression
from backend.app.models.outbox import (
    IncidentOutboxEvent,
    derive_event_id,
    derive_idempotency_key,
    NAMESPACE_IBVAP,
)
from backend.app.services.live_pipeline import (
    CameraPipeline,
    _normalize_for_json,
)
from backend.app.services.feedback import SiteFeedbackRegistry

PG_TEST_URL = os.environ.get(
    "IBVAP_PG_TEST_URL",
    "postgresql://dev_user:dev_password@localhost:5432/ibvap_test"
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
    """Provide isolated SQLite database for transactional testing."""
    db_file = tmp_path / "test_phase2_outbox.db"
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
        yield session
    finally:
        session.close()
        # Clean test tables
        with engine.connect() as conn:
            conn.execute(sa.text("TRUNCATE TABLE incident_outbox, evidence, alerts, incidents, operator_suppressions CASCADE;"))
            conn.commit()


# =============================================================================
# GROUP 1: ATOMIC OUTBOX TRANSACTION & IDENTITY CONTRACT
# =============================================================================

class TestAtomicOutboxTransaction:
    """Verify single-transaction atomicity and canonical event identity."""

    def test_atomic_commit_incident_evidence_outbox(self, sqlite_test_db, monkeypatch):
        """Case A & G-L: All persistence succeeds and commits atomically in ONE transaction."""
        engine, SessionCls = sqlite_test_db
        monkeypatch.setattr("backend.app.db.session.SessionLocal", SessionCls)

        pipeline = CameraPipeline(camera_id=1, stream_url="fake://1", camera_name="TestCam-1", bop="BOP-Alpha")
        code = f"IBVAP-{datetime.utcnow().strftime('%Y%m%d')}-0001-PER-1"
        track = {"track_id": 101, "target_id": 101, "class_name": "person", "confidence": 0.92, "bbox": [10, 20, 100, 200]}

        payload = pipeline._save_incident(
            code=code,
            track=track,
            reasons=["zone_intrusion"],
            severity="HIGH",
            score=82.0,
            confidence=0.92,
            fingerprint="fp_hash_001",
            ai_assessment={"threat": "high", "rule": "fence_breach"},
            action="Deploy patrol",
            timeline=[{"event_type": "detection", "timestamp": datetime.utcnow().isoformat()}],
            zone_name="Perimeter Fence",
        )

        assert payload is not None
        assert payload["incident_code"] == code

        # Verify database state
        with SessionCls() as db:
            inc = db.query(Incident).filter(Incident.incident_code == code).first()
            assert inc is not None, "Incident must be committed"
            assert inc.severity == "HIGH"

            alert = db.query(Alert).filter(Alert.incident_id == inc.id).first()
            assert alert is not None, "Alert must be committed"

            ev = db.query(Evidence).filter(Evidence.incident_id == inc.id).first()
            assert ev is not None, "Evidence must be committed"

            # Outbox row verification
            outbox_rows = db.query(IncidentOutboxEvent).filter(IncidentOutboxEvent.incident_code == code).all()
            assert len(outbox_rows) == 1, "Exactly one outbox row must be committed"

            outbox = outbox_rows[0]
            assert outbox.status == "PENDING"
            assert outbox.retry_count == 0
            assert outbox.event_type == "incident_created"
            assert outbox.lease_until is None
            assert outbox.worker_id is None
            assert outbox.delivered_at is None
            assert outbox.created_at is not None
            assert outbox.next_retry_at is not None

            # Canonical identity check
            expected_idempotency = derive_idempotency_key(code, "incident_created")
            assert outbox.idempotency_key == expected_idempotency
            assert uuid.UUID(outbox.event_id).version == 4
            assert uuid.UUID(outbox.idempotency_key).version == 5

            # Payload identity parity
            assert outbox.payload["event_id"] == outbox.event_id
            assert outbox.payload["idempotency_key"] == outbox.idempotency_key
            assert outbox.payload["incident_code"] == code
            assert outbox.payload["severity"] == "HIGH"
            assert outbox.payload["threat_score"] == 82.0

    def test_incident_failure_rolls_everything_back(self, sqlite_test_db, monkeypatch):
        """Case B: If incident persistence fails, rollback leaves no incident, evidence, or outbox."""
        engine, SessionCls = sqlite_test_db

        pipeline = CameraPipeline(camera_id=1, stream_url="fake://1", camera_name="TestCam-1")
        code = "IBVAP-FAIL-INC-001"
        track = {"track_id": 102, "target_id": 102, "class_name": "person", "confidence": 0.85}

        # Mock db.flush() when adding Incident to simulate database constraint failure
        class FaultySession(Session):
            def flush(self, objects=None):
                raise IntegrityError("Simulated incident flush failure", None, None)

        FaultySessionCls = sessionmaker(bind=engine, class_=FaultySession)
        monkeypatch.setattr("backend.app.db.session.SessionLocal", FaultySessionCls)

        res = pipeline._save_incident(
            code=code,
            track=track,
            reasons=["breach"],
            severity="CRITICAL",
            score=95.0,
            confidence=0.85,
            fingerprint="fp_fail_01",
            ai_assessment={},
            action="Alert",
            timeline=[],
        )

        assert res is None, "Failed incident must return None"

        with SessionCls() as db:
            assert db.query(Incident).filter(Incident.incident_code == code).first() is None
            assert db.query(IncidentOutboxEvent).filter(IncidentOutboxEvent.incident_code == code).first() is None
            assert db.query(Evidence).count() == 0

    def test_evidence_failure_rolls_everything_back(self, sqlite_test_db, monkeypatch):
        """Case C: If evidence creation/persistence fails, incident and outbox are rolled back."""
        engine, SessionCls = sqlite_test_db
        monkeypatch.setattr("backend.app.db.session.SessionLocal", SessionCls)

        pipeline = CameraPipeline(camera_id=1, stream_url="fake://1", camera_name="TestCam-1")
        code = "IBVAP-FAIL-EV-001"
        track = {"track_id": 103, "target_id": 103, "class_name": "vehicle", "confidence": 0.88}

        # Fault injection on seal_evidence
        def faulty_seal(*args, **kwargs):
            raise RuntimeError("Disk I/O error during evidence sealing")

        monkeypatch.setattr("backend.app.services.evidence.seal_evidence", faulty_seal)

        res = pipeline._save_incident(
            code=code,
            track=track,
            reasons=["vehicle_entry"],
            severity="HIGH",
            score=80.0,
            confidence=0.88,
            fingerprint="fp_fail_ev",
            ai_assessment={},
            action="Verify",
            timeline=[],
        )

        assert res is None

        with SessionCls() as db:
            assert db.query(Incident).filter(Incident.incident_code == code).first() is None
            assert db.query(IncidentOutboxEvent).filter(IncidentOutboxEvent.incident_code == code).first() is None
            assert db.query(Alert).count() == 0

    def test_outbox_failure_rolls_everything_back(self, sqlite_test_db, monkeypatch):
        """Case D: If outbox insertion fails, incident and evidence are completely rolled back."""
        engine, SessionCls = sqlite_test_db
        monkeypatch.setattr("backend.app.db.session.SessionLocal", SessionCls)

        pipeline = CameraPipeline(camera_id=1, stream_url="fake://1", camera_name="TestCam-1")
        code = "IBVAP-FAIL-OUTBOX-001"
        track = {"track_id": 104, "target_id": 104, "class_name": "person", "confidence": 0.90}

        orig_add = Session.add

        def faulty_add(self_session, instance):
            if isinstance(instance, IncidentOutboxEvent):
                raise IntegrityError("UNIQUE constraint failed: incident_outbox.idempotency_key", None, None)
            return orig_add(self_session, instance)

        monkeypatch.setattr(Session, "add", faulty_add)

        res = pipeline._save_incident(
            code=code,
            track=track,
            reasons=["fence_jump"],
            severity="CRITICAL",
            score=90.0,
            confidence=0.90,
            fingerprint="fp_fail_ob",
            ai_assessment={},
            action="Intercept",
            timeline=[],
        )

        assert res is None

        with SessionCls() as db:
            assert db.query(Incident).filter(Incident.incident_code == code).first() is None
            assert db.query(IncidentOutboxEvent).filter(IncidentOutboxEvent.incident_code == code).first() is None
            assert db.query(Evidence).count() == 0

    def test_duplicate_idempotency_key_enforced_at_db_layer(self, sqlite_test_db):
        """Case M: Database UNIQUE constraint rejects duplicate idempotency_key."""
        engine, SessionCls = sqlite_test_db

        with SessionCls() as db:
            e1 = IncidentOutboxEvent(
                event_id=derive_event_id(),
                event_type="incident_created",
                incident_code="INC-DUP-01",
                idempotency_key="deterministic-dup-key",
                payload={"key": "val1"},
                status="PENDING",
            )
            db.add(e1)
            db.commit()

            e2 = IncidentOutboxEvent(
                event_id=derive_event_id(),
                event_type="incident_created",
                incident_code="INC-DUP-02",
                idempotency_key="deterministic-dup-key",  # Duplicate key!
                payload={"key": "val2"},
                status="PENDING",
            )
            db.add(e2)
            with pytest.raises(IntegrityError):
                db.commit()
            db.rollback()


# =============================================================================
# GROUP 2: PHANTOM ALERT ELIMINATION & AUTHORITY REJECTION
# =============================================================================

class TestPhantomAlertElimination:
    """Verify that alerts are never broadcast before commit or upon rejection."""

    def test_no_precommit_event_callback_on_save_failure(self, sqlite_test_db, monkeypatch):
        """Case N & O: If DB persistence fails, _event_callback NEVER fires."""
        engine, SessionCls = sqlite_test_db
        monkeypatch.setattr("backend.app.db.session.SessionLocal", SessionCls)

        pipeline = CameraPipeline(camera_id=1, stream_url="fake://1", camera_name="TestCam-1")
        callbacks_received = []
        pipeline.set_event_callback(lambda ev: callbacks_received.append(ev))

        # Break DB commit
        def faulty_commit(self_session):
            raise sa.exc.OperationalError("Disk full", None, None)

        monkeypatch.setattr(Session, "commit", faulty_commit)

        track = {"track_id": 201, "target_id": 201, "class_name": "person", "confidence": 0.95, "bbox": [0, 0, 10, 10]}
        pipeline._generate_incident(
            track=track,
            reasons=["breach"],
            severity="CRITICAL",
            score=95.0,
            fingerprint="fp_phantom_01",
            dwell_time=12.0,
            inference_ms=15.0,
        )

        assert len(callbacks_received) == 0, "No event callback must fire when persistence fails (Phantom alert eliminated)"

    def test_cooldown_rejection_creates_no_outbox_and_no_callback(self, sqlite_test_db, monkeypatch):
        """Case E: Cooldown rejection creates NO outbox row and fires NO callback."""
        engine, SessionCls = sqlite_test_db
        monkeypatch.setattr("backend.app.db.session.SessionLocal", SessionCls)

        pipeline = CameraPipeline(camera_id=1, stream_url="fake://1", camera_name="TestCam-1")
        callbacks_received = []
        pipeline.set_event_callback(lambda ev: callbacks_received.append(ev))

        track = {"track_id": 202, "target_id": 202, "class_name": "person", "confidence": 0.95, "bbox": [0, 0, 10, 10]}

        # First call: succeeds
        pipeline._generate_incident(
            track=track,
            reasons=["breach"],
            severity="HIGH",
            score=80.0,
            fingerprint="fp_cd_01",
            dwell_time=5.0,
            inference_ms=10.0,
        )

        assert len(callbacks_received) == 1, "First incident should emit post-commit callback"

        # Second call immediately after for same target: should be rejected by cooldown
        pipeline._generate_incident(
            track=track,
            reasons=["breach"],
            severity="HIGH",
            score=80.0,
            fingerprint="fp_cd_02",
            dwell_time=6.0,
            inference_ms=10.0,
        )

        assert len(callbacks_received) == 1, "Second incident must be rejected by cooldown; NO callback fired"

        with SessionCls() as db:
            outbox_rows = db.query(IncidentOutboxEvent).all()
            assert len(outbox_rows) == 1, "Only the first incident may create an outbox row"

    def test_suppression_rejection_creates_no_outbox_and_no_callback(self, sqlite_test_db, monkeypatch):
        """Case F: Active DB operator suppression creates NO outbox row and fires NO callback."""
        engine, SessionCls = sqlite_test_db
        monkeypatch.setattr("backend.app.db.session.SessionLocal", SessionCls)

        # Pre-seed active DB operator suppression for target 301
        with SessionCls() as db:
            supp = OperatorSuppression(
                suppression_key="camera:1:target:301",
                entity_type="track",
                camera_id=1,
                track_id=301,
                incident_id=999,
                dismissal_reason="ENVIRONMENT_FALSE_ALARM",
                operator_notes="Blowing foliage",
                operator_id="ADMIN",
                initial_zone_type="BUFFER",
                created_at=datetime.utcnow(),
                expires_at=datetime.utcnow() + timedelta(minutes=10),
                is_revoked=False,
            )
            db.add(supp)
            db.commit()

        pipeline = CameraPipeline(camera_id=1, stream_url="fake://1", camera_name="TestCam-1")
        callbacks_received = []
        pipeline.set_event_callback(lambda ev: callbacks_received.append(ev))

        track = {"track_id": 301, "target_id": 301, "class_name": "person", "confidence": 0.88, "bbox": [0, 0, 10, 10]}
        pipeline._generate_incident(
            track=track,
            reasons=["motion"],
            severity="MEDIUM",
            score=65.0,
            fingerprint="fp_supp_01",
            dwell_time=2.0,
            inference_ms=10.0,
            suppression_revocation_required=False,
        )

        assert len(callbacks_received) == 0, "Suppressed incident must NOT emit event callback"

        with SessionCls() as db:
            outbox_rows = db.query(IncidentOutboxEvent).all()
            assert len(outbox_rows) == 0, "Suppressed incident must NOT produce any outbox row"

    def test_postcommit_callback_matches_outbox_canonical_identity(self, sqlite_test_db, monkeypatch):
        """Verification that callback event shares identical canonical identity with persisted outbox."""
        engine, SessionCls = sqlite_test_db
        monkeypatch.setattr("backend.app.db.session.SessionLocal", SessionCls)

        pipeline = CameraPipeline(camera_id=1, stream_url="fake://1", camera_name="TestCam-1")
        callbacks_received = []
        pipeline.set_event_callback(lambda ev: callbacks_received.append(ev))

        track = {"track_id": 401, "target_id": 401, "class_name": "person", "confidence": 0.91, "bbox": [0, 0, 10, 10]}
        pipeline._generate_incident(
            track=track,
            reasons=["line_cross"],
            severity="HIGH",
            score=85.0,
            fingerprint="fp_match_01",
            dwell_time=4.0,
            inference_ms=12.0,
        )

        assert len(callbacks_received) == 1
        cb_event = callbacks_received[0]

        with SessionCls() as db:
            outbox = db.query(IncidentOutboxEvent).first()
            assert outbox is not None
            assert cb_event["event_id"] == outbox.event_id
            assert cb_event["idempotency_key"] == outbox.idempotency_key
            assert cb_event["incident_code"] == outbox.incident_code


# =============================================================================
# GROUP 3: PAYLOAD NORMALIZATION & JSON SERIALIZATION
# =============================================================================

class TestPayloadNormalization:
    """Verify complex objects and NumPy types are cleanly normalized for JSON storage."""

    def test_normalize_for_json_handles_numpy_and_nested_types(self):
        """Verify _normalize_for_json converts NumPy scalars, arrays, datetimes, and nested dicts."""
        data = {
            "float32": np.float32(0.95),
            "float64": np.float64(82.5),
            "int64": np.int64(101),
            "bool_": np.bool_(True),
            "ndarray": np.array([1, 2, 3], dtype=np.int32),
            "nested": {
                "dt": datetime(2026, 9, 17, 12, 0, 0),
                "list": [np.float32(1.1), np.float32(2.2)],
            },
            "standard_str": "valid_string",
            "none_val": None,
        }

        normalized = _normalize_for_json(data)
        serialized = json.dumps(normalized)
        loaded = json.loads(serialized)

        assert isinstance(loaded["float32"], float)
        assert round(loaded["float32"], 2) == 0.95
        assert isinstance(loaded["int64"], int)
        assert loaded["int64"] == 101
        assert loaded["bool_"] is True
        assert loaded["ndarray"] == [1, 2, 3]
        assert loaded["nested"]["dt"] == "2026-09-17T12:00:00"
        assert [round(x, 1) for x in loaded["nested"]["list"]] == [1.1, 2.2]


# =============================================================================
# GROUP 4: REAL POSTGRESQL 15 VALIDATION
# =============================================================================

class TestPostgreSQLPhase2Integration:
    """Real PostgreSQL 15 integration validation for Phase 2 contracts."""

    def test_postgresql_atomic_commit_incident_evidence_outbox(self, postgres_test_session, monkeypatch):
        """Verify atomic commit on real PostgreSQL 15."""
        session = postgres_test_session
        monkeypatch.setattr("backend.app.db.session.SessionLocal", lambda: session)

        pipeline = CameraPipeline(camera_id=1, stream_url="fake://1", camera_name="PG-Cam-1", bop="BOP-PG")
        code = f"IBVAP-PG-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-001"
        track = {"track_id": 501, "target_id": 501, "class_name": "person", "confidence": 0.94, "bbox": [50, 50, 100, 100]}

        payload = pipeline._save_incident(
            code=code,
            track=track,
            reasons=["boundary_penetration"],
            severity="CRITICAL",
            score=91.0,
            confidence=0.94,
            fingerprint="fp_pg_001",
            ai_assessment={"threat": "critical", "source": "yolo26"},
            action="Intercept immediately",
            timeline=[{"event_type": "detection", "timestamp": datetime.utcnow().isoformat()}],
            zone_name="Restricted Boundary",
        )

        assert payload is not None

        # Re-query on PostgreSQL
        inc = session.query(Incident).filter(Incident.incident_code == code).first()
        assert inc is not None
        assert inc.threat_score == 91.0

        ev = session.query(Evidence).filter(Evidence.incident_id == inc.id).first()
        assert ev is not None

        outbox = session.query(IncidentOutboxEvent).filter(IncidentOutboxEvent.incident_code == code).first()
        assert outbox is not None
        assert outbox.status == "PENDING"
        assert outbox.payload["severity"] == "CRITICAL"
        assert outbox.payload["threat_score"] == 91.0

    def test_postgresql_advisory_lock_coexists_with_outbox_transaction(self, postgres_test_session, monkeypatch):
        """Verify that PostgreSQL pg_advisory_xact_lock operates cleanly within the outbox transaction."""
        session = postgres_test_session
        monkeypatch.setattr("backend.app.db.session.SessionLocal", lambda: session)

        pipeline = CameraPipeline(camera_id=1, stream_url="fake://1", camera_name="PG-Cam-1")
        code = f"IBVAP-PG-LOCK-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        track = {"track_id": 502, "target_id": 502, "class_name": "person", "confidence": 0.93}

        payload = pipeline._save_incident(
            code=code,
            track=track,
            reasons=["breach"],
            severity="HIGH",
            score=84.0,
            confidence=0.93,
            fingerprint="fp_pg_lock_01",
            ai_assessment={},
            action="Monitor",
            timeline=[],
        )

        assert payload is not None
        outbox = session.query(IncidentOutboxEvent).filter(IncidentOutboxEvent.incident_code == code).first()
        assert outbox is not None


# =============================================================================
# GROUP 5: STATIC CODE-PATH & TRANSACTION BOUNDARY AUDIT
# =============================================================================

class TestStaticTransactionBoundaryAudit:
    """Statically audit live_pipeline.py to guarantee strict transaction boundaries."""

    def test_single_commit_in_save_incident_transaction_block(self):
        """Verify that _save_incident contains exactly ONE db.commit() in its main transaction block."""
        source = inspect.getsource(CameraPipeline._save_incident)
        
        # Count db.commit() in source (excluding bg clip thread)
        lines = source.split("\n")
        main_commit_lines = [
            line.strip() for line in lines 
            if "db.commit()" in line and "db_bg.commit()" not in line
        ]

        assert len(main_commit_lines) == 1, (
            f"Expected exactly 1 main db.commit() in _save_incident, found {len(main_commit_lines)}: {main_commit_lines}"
        )

    def test_outbox_add_precedes_commit(self):
        """Verify that db.add(outbox_event) is called BEFORE db.commit()."""
        source = inspect.getsource(CameraPipeline._save_incident)
        outbox_pos = source.find("db.add(outbox_event)")
        commit_pos = source.find("db.commit()")

        assert outbox_pos != -1, "db.add(outbox_event) must be present in _save_incident"
        assert commit_pos != -1, "db.commit() must be present in _save_incident"
        assert outbox_pos < commit_pos, "db.add(outbox_event) must precede db.commit()"

    def test_no_websocket_or_network_broadcast_inside_transaction(self):
        """Verify no WebSocket or external HTTP calls exist inside _save_incident."""
        source = inspect.getsource(CameraPipeline._save_incident)
        forbidden_patterns = [
            "broadcast_event",
            "dispatch_incident_webhook",
            "requests.post",
            "httpx.post",
            "aiohttp",
        ]
        for pattern in forbidden_patterns:
            assert pattern not in source, f"Forbidden network dispatch pattern '{pattern}' found inside _save_incident"
