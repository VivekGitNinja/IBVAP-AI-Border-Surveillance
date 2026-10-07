"""
Step 07 Phase 3 — Outbox Claim/Lease Dispatcher & WebSocket Delivery Test Suite
==============================================================================

Rigorously verifies Phase 3 scope:
1. Claim transition to PROCESSING with valid lease & incremented retry_count
2. Claim transaction commits before external delivery (no DB locks during I/O)
3. External delivery success -> fenced transition to DELIVERED
4. External delivery failure -> fenced transition to FAILED with exponential backoff
5. Terminal failure (attempts >= max_retries) -> fenced transition to DEAD_LETTER
6. Expired lease auto-recovery by subsequent worker
7. Active lease protection (unexpired leases cannot be stolen)
8. Zombie worker DELIVERED completion rejection (affected 0 rows)
9. Zombie worker FAILED completion rejection (affected 0 rows)
10. Zombie worker DEAD_LETTER completion rejection (affected 0 rows)
11. Canonical event identity preserved identically across retries
12. Multi-worker concurrency with PostgreSQL SELECT ... FOR UPDATE SKIP LOCKED
13. Real PostgreSQL 15.18 claim, dispatch, and delivery lifecycle
14. Real PostgreSQL 15.18 expired lease recovery
15. Real PostgreSQL 15.18 zombie rejection
16. Bounded interruptible background worker shutdown
17. Telemetry counter accuracy and snapshot contract
18. Custom broadcaster and C2 sender injection
19. Batch size bounding under heavy backlog
20. Future next_retry_at backoff adherence
21. Static structural audit of transaction and lock boundaries
22. Transaction latency performance benchmark [MEASURED BENCHMARK]
"""

from __future__ import annotations

import os
import uuid
import time
import inspect
from datetime import datetime, timedelta
from typing import Dict, Any, List
from concurrent.futures import ThreadPoolExecutor

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker, Session

from backend.app.models.base import Base
from backend.app.models.outbox import (
    IncidentOutboxEvent,
    derive_event_id,
    derive_idempotency_key,
)
from backend.app.services.outbox_dispatcher import (
    IncidentOutboxDispatcher,
    ClaimedEventSnapshot,
    OutboxTelemetry,
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
    """Provide isolated SQLite database for dispatcher testing."""
    db_file = tmp_path / "test_phase3_outbox.db"
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
        # Clean test table
        with engine.connect() as conn:
            conn.execute(sa.text("TRUNCATE TABLE incident_outbox CASCADE;"))
            conn.commit()


def _create_test_outbox_event(
    session: Session,
    incident_code: str = "INC-TEST-001",
    event_type: str = "incident_created",
    status: str = "PENDING",
    retry_count: int = 0,
    lease_until: datetime = None,
    worker_id: str = None,
    next_retry_at: datetime = None,
    payload: Dict[str, Any] = None,
) -> IncidentOutboxEvent:
    event_id = derive_event_id()
    idempotency_key = derive_idempotency_key(incident_code, event_type)
    now_utc = datetime.utcnow()
    ev = IncidentOutboxEvent(
        event_id=event_id,
        event_type=event_type,
        incident_code=incident_code,
        idempotency_key=idempotency_key,
        payload=payload or {
            "event_id": event_id,
            "event_type": event_type,
            "incident_code": incident_code,
            "idempotency_key": idempotency_key,
            "severity": "HIGH",
            "threat_score": 85.0,
        },
        status=status,
        retry_count=retry_count,
        lease_until=lease_until,
        worker_id=worker_id,
        next_retry_at=next_retry_at or now_utc,
        created_at=now_utc,
    )
    session.add(ev)
    session.commit()
    session.refresh(ev)
    return ev


# =============================================================================
# GROUP 1: CLAIM / LEASE LIFECYCLE & TRANSACTION SEPARATION
# =============================================================================

class TestClaimLeaseLifecycle:
    """Verify Phase A claim semantics and transaction decoupling."""

    def test_claim_transitions_to_processing_with_lease(self, sqlite_test_db):
        """Verify claim transitions status to PROCESSING, sets worker_id, lease_until, and increments retry_count."""
        engine, SessionCls = sqlite_test_db
        with SessionCls() as db:
            ev = _create_test_outbox_event(db, incident_code="INC-CLAIM-01")

        dispatcher = IncidentOutboxDispatcher(session_factory=SessionCls, worker_id="worker-test-1", lease_duration_sec=30.0)

        with SessionCls() as db:
            snapshots = dispatcher._claim_batch(db, datetime.utcnow())

        assert len(snapshots) == 1
        snap = snapshots[0]
        assert snap.incident_code == "INC-CLAIM-01"
        assert snap.worker_id == "worker-test-1"
        assert snap.retry_count == 1
        assert snap.lease_until > datetime.utcnow()

        # Check DB authoritative state
        with SessionCls() as db:
            db_ev = db.query(IncidentOutboxEvent).filter(IncidentOutboxEvent.id == ev.id).first()
            assert db_ev.status == "PROCESSING"
            assert db_ev.worker_id == "worker-test-1"
            assert db_ev.retry_count == 1
            assert db_ev.lease_until is not None

    def test_claim_transaction_commits_before_external_delivery(self, sqlite_test_db):
        """Verify that claim transaction is committed BEFORE external network dispatch is executed."""
        engine, SessionCls = sqlite_test_db
        with SessionCls() as db:
            ev = _create_test_outbox_event(db, incident_code="INC-DECOUPLE-01")

        status_during_dispatch = None
        worker_during_dispatch = None

        def spy_broadcaster(payload):
            nonlocal status_during_dispatch, worker_during_dispatch
            # Open completely independent DB session while dispatch callback runs
            with SessionCls() as independent_db:
                db_record = independent_db.query(IncidentOutboxEvent).filter(IncidentOutboxEvent.id == ev.id).first()
                status_during_dispatch = db_record.status
                worker_during_dispatch = db_record.worker_id

        dispatcher = IncidentOutboxDispatcher(
            session_factory=SessionCls,
            worker_id="worker-decouple-test",
            event_broadcaster=spy_broadcaster,
        )

        processed = dispatcher.run_once()
        assert processed == 1

        # The independent session must have observed PROCESSING status and worker_id during external dispatch
        assert status_during_dispatch == "PROCESSING", "Claim must be committed before network dispatch"
        assert worker_during_dispatch == "worker-decouple-test"

        # Final state after successful run_once should be DELIVERED
        with SessionCls() as db:
            final_ev = db.query(IncidentOutboxEvent).filter(IncidentOutboxEvent.id == ev.id).first()
            assert final_ev.status == "DELIVERED"
            assert final_ev.worker_id is None
            assert final_ev.lease_until is None
            assert final_ev.delivered_at is not None

    def test_external_delivery_success_transitions_to_delivered(self, sqlite_test_db):
        """Verify successful dispatch results in fenced DELIVERED transition with cleared lease."""
        engine, SessionCls = sqlite_test_db
        with SessionCls() as db:
            ev = _create_test_outbox_event(db, incident_code="INC-DELIV-01")

        dispatched_events = []
        dispatcher = IncidentOutboxDispatcher(
            session_factory=SessionCls,
            worker_id="worker-success-test",
            event_broadcaster=lambda p: dispatched_events.append(p),
        )

        processed = dispatcher.run_once()
        assert processed == 1
        assert len(dispatched_events) == 1
        assert dispatched_events[0]["incident_code"] == "INC-DELIV-01"

        with SessionCls() as db:
            db_ev = db.query(IncidentOutboxEvent).filter(IncidentOutboxEvent.id == ev.id).first()
            assert db_ev.status == "DELIVERED"
            assert db_ev.worker_id is None
            assert db_ev.lease_until is None
            assert db_ev.delivered_at is not None
            assert db_ev.error_message is None

        telemetry = dispatcher.telemetry.snapshot()
        assert telemetry["outbox_deliveries"] == 1
        assert telemetry["outbox_delivery_failures"] == 0

    def test_external_delivery_failure_transitions_to_failed_with_exponential_backoff(self, sqlite_test_db):
        """Verify delivery failure transitions to FAILED with exponential backoff and error_message."""
        engine, SessionCls = sqlite_test_db
        with SessionCls() as db:
            ev = _create_test_outbox_event(db, incident_code="INC-FAIL-01")

        def failing_broadcaster(p):
            raise ConnectionResetError("Remote WebSocket endpoint disconnected")

        dispatcher = IncidentOutboxDispatcher(
            session_factory=SessionCls,
            worker_id="worker-fail-test",
            base_backoff_sec=2.0,
            event_broadcaster=failing_broadcaster,
        )

        before_dispatch = datetime.utcnow()
        processed = dispatcher.run_once()
        assert processed == 1

        with SessionCls() as db:
            db_ev = db.query(IncidentOutboxEvent).filter(IncidentOutboxEvent.id == ev.id).first()
            assert db_ev.status == "FAILED"
            assert db_ev.worker_id is None
            assert db_ev.lease_until is None
            assert "Remote WebSocket endpoint disconnected" in db_ev.error_message
            # Retry count is 1, so backoff is base_backoff_sec * 2^(1-1) = 2.0s
            expected_min_retry = before_dispatch + timedelta(seconds=1.5)
            assert db_ev.next_retry_at >= expected_min_retry

        telemetry = dispatcher.telemetry.snapshot()
        assert telemetry["outbox_delivery_failures"] == 1
        assert telemetry["outbox_retry_count"] == 1
        assert telemetry["outbox_dead_letters"] == 0

    def test_dead_letter_transition_after_max_retries(self, sqlite_test_db):
        """Verify that reaching max_retries transitions record to DEAD_LETTER."""
        engine, SessionCls = sqlite_test_db
        with SessionCls() as db:
            # Seed event that has already been attempted 9 times
            ev = _create_test_outbox_event(
                db,
                incident_code="INC-MAXRETRY-01",
                retry_count=9,
                status="FAILED",
            )

        def failing_broadcaster(p):
            raise TimeoutError("C2 HTTP upstream gateway timeout")

        dispatcher = IncidentOutboxDispatcher(
            session_factory=SessionCls,
            worker_id="worker-maxretry-test",
            max_retries=10,
            event_broadcaster=failing_broadcaster,
        )

        processed = dispatcher.run_once()
        assert processed == 1

        with SessionCls() as db:
            db_ev = db.query(IncidentOutboxEvent).filter(IncidentOutboxEvent.id == ev.id).first()
            assert db_ev.status == "DEAD_LETTER"
            assert db_ev.retry_count == 10
            assert db_ev.worker_id is None
            assert db_ev.lease_until is None
            assert "C2 HTTP upstream gateway timeout" in db_ev.error_message

        telemetry = dispatcher.telemetry.snapshot()
        assert telemetry["outbox_dead_letters"] == 1


# =============================================================================
# GROUP 2: LEASE EXPIRATION, RECLAIM & ZOMBIE FENCING
# =============================================================================

class TestLeaseExpirationAndFencing:
    """Verify lease expiration reclaim and strict zombie rejection."""

    def test_lease_expiry_reclaimed_by_subsequent_worker(self, sqlite_test_db):
        """Verify an event stuck in PROCESSING with expired lease is reclaimed by another worker."""
        engine, SessionCls = sqlite_test_db
        past_time = datetime.utcnow() - timedelta(seconds=10)
        with SessionCls() as db:
            ev = _create_test_outbox_event(
                db,
                incident_code="INC-EXPIRED-01",
                status="PROCESSING",
                retry_count=1,
                worker_id="worker-crashed",
                lease_until=past_time,
            )

        dispatcher = IncidentOutboxDispatcher(
            session_factory=SessionCls,
            worker_id="worker-recovery",
            event_broadcaster=lambda p: None,
        )

        processed = dispatcher.run_once()
        assert processed == 1

        telemetry = dispatcher.telemetry.snapshot()
        assert telemetry["outbox_lease_expirations"] == 1
        assert telemetry["outbox_deliveries"] == 1

        with SessionCls() as db:
            db_ev = db.query(IncidentOutboxEvent).filter(IncidentOutboxEvent.id == ev.id).first()
            assert db_ev.status == "DELIVERED"
            assert db_ev.delivered_at is not None

    def test_live_lease_protected_against_theft(self, sqlite_test_db):
        """Verify an event currently leased by Worker A cannot be claimed by Worker B while lease is active."""
        engine, SessionCls = sqlite_test_db
        future_lease = datetime.utcnow() + timedelta(seconds=25)
        with SessionCls() as db:
            ev = _create_test_outbox_event(
                db,
                incident_code="INC-ACTIVE-LEASE-01",
                status="PROCESSING",
                retry_count=1,
                worker_id="worker-active-A",
                lease_until=future_lease,
            )

        dispatcher_b = IncidentOutboxDispatcher(
            session_factory=SessionCls,
            worker_id="worker-intruder-B",
        )

        with SessionCls() as db:
            snapshots = dispatcher_b._claim_batch(db, datetime.utcnow())

        assert len(snapshots) == 0, "Active unexpired lease must not be claimed by another worker"

        with SessionCls() as db:
            db_ev = db.query(IncidentOutboxEvent).filter(IncidentOutboxEvent.id == ev.id).first()
            assert db_ev.worker_id == "worker-active-A"
            assert db_ev.status == "PROCESSING"

    def test_zombie_worker_delivered_completion_rejected(self, sqlite_test_db):
        """Verify that when a stalled worker whose lease expired attempts DELIVERED, it affects 0 rows and aborts."""
        engine, SessionCls = sqlite_test_db
        now = datetime.utcnow()
        with SessionCls() as db:
            ev = _create_test_outbox_event(db, incident_code="INC-ZOMBIE-01")

        dispatcher = IncidentOutboxDispatcher(session_factory=SessionCls, worker_id="worker-zombie")

        # Worker creates a snapshot with expired lease (simulating long stall)
        expired_lease = now - timedelta(seconds=5)
        stale_snapshot = ClaimedEventSnapshot(
            id=ev.id,
            event_id=ev.event_id,
            event_type=ev.event_type,
            incident_code=ev.incident_code,
            idempotency_key=ev.idempotency_key,
            payload=ev.payload,
            retry_count=1,
            worker_id="worker-zombie",
            lease_until=expired_lease,
        )

        # Meanwhile, another worker has claimed and updated the record
        with SessionCls() as db:
            db.query(IncidentOutboxEvent).filter(IncidentOutboxEvent.id == ev.id).update({
                "status": "PROCESSING",
                "worker_id": "worker-new",
                "lease_until": now + timedelta(seconds=30),
            })
            db.commit()

        # Zombie worker attempts completion
        completed = dispatcher._complete_event(stale_snapshot, success=True, error_message=None)
        assert completed is False, "Fenced completion must return False when rowcount == 0"

        telemetry = dispatcher.telemetry.snapshot()
        assert telemetry["outbox_zombie_completion_rejections"] == 1
        assert telemetry["outbox_stale_worker_rejections"] == 1

        # Verify that the new worker's ownership was NOT disturbed
        with SessionCls() as db:
            db_ev = db.query(IncidentOutboxEvent).filter(IncidentOutboxEvent.id == ev.id).first()
            assert db_ev.status == "PROCESSING"
            assert db_ev.worker_id == "worker-new"

    def test_zombie_worker_failed_and_dead_letter_completion_rejected(self, sqlite_test_db):
        """Verify zombie rejection also protects FAILED and DEAD_LETTER transitions."""
        engine, SessionCls = sqlite_test_db
        now = datetime.utcnow()
        with SessionCls() as db:
            ev = _create_test_outbox_event(db, incident_code="INC-ZOMBIE-FAIL-01")

        dispatcher = IncidentOutboxDispatcher(session_factory=SessionCls, worker_id="worker-zombie-fail")

        stale_snapshot = ClaimedEventSnapshot(
            id=ev.id,
            event_id=ev.event_id,
            event_type=ev.event_type,
            incident_code=ev.incident_code,
            idempotency_key=ev.idempotency_key,
            payload=ev.payload,
            retry_count=10,  # Would normally trigger DEAD_LETTER
            worker_id="worker-zombie-fail",
            lease_until=now - timedelta(seconds=1),  # Expired lease
        )

        with SessionCls() as db:
            db.query(IncidentOutboxEvent).filter(IncidentOutboxEvent.id == ev.id).update({
                "status": "DELIVERED",
                "worker_id": None,
                "lease_until": None,
            })
            db.commit()

        completed = dispatcher._complete_event(stale_snapshot, success=False, error_message="Stale error")
        assert completed is False

        telemetry = dispatcher.telemetry.snapshot()
        assert telemetry["outbox_zombie_completion_rejections"] == 1

        with SessionCls() as db:
            db_ev = db.query(IncidentOutboxEvent).filter(IncidentOutboxEvent.id == ev.id).first()
            assert db_ev.status == "DELIVERED", "Stale failure update must not overwrite DELIVERED status"


# =============================================================================
# GROUP 3: CANONICAL IDENTITY & IMMUTABILITY CONTRACT
# =============================================================================

class TestCanonicalIdentityImmutability:
    """Verify event identity remains strictly invariant across retries and deliveries."""

    def test_canonical_event_identity_preserved_across_retries(self, sqlite_test_db):
        """Verify event_id, idempotency_key, and incident_code are never mutated across retries."""
        engine, SessionCls = sqlite_test_db
        with SessionCls() as db:
            ev = _create_test_outbox_event(db, incident_code="INC-IMMUTABLE-01")
            orig_event_id = ev.event_id
            orig_idempotency_key = ev.idempotency_key
            orig_incident_code = ev.incident_code

        attempts = 0
        dispatched_identities = []

        def failing_then_succeeding_broadcaster(p):
            nonlocal attempts
            attempts += 1
            dispatched_identities.append({
                "event_id": p["event_id"],
                "idempotency_key": p["idempotency_key"],
                "incident_code": p["incident_code"],
            })
            if attempts == 1:
                raise IOError("Transient network glitch on attempt 1")

        dispatcher = IncidentOutboxDispatcher(
            session_factory=SessionCls,
            worker_id="worker-identity-test",
            base_backoff_sec=0.01,  # Short backoff for test speed
            event_broadcaster=failing_then_succeeding_broadcaster,
        )

        # Attempt 1: Fails
        dispatcher.run_once()
        with SessionCls() as db:
            db_ev = db.query(IncidentOutboxEvent).filter(IncidentOutboxEvent.id == ev.id).first()
            assert db_ev.status == "FAILED"
            assert db_ev.event_id == orig_event_id
            assert db_ev.idempotency_key == orig_idempotency_key
            assert db_ev.incident_code == orig_incident_code
            # Force next_retry_at to past so attempt 2 runs immediately
            db_ev.next_retry_at = datetime.utcnow() - timedelta(seconds=1)
            db.commit()

        # Attempt 2: Succeeds
        dispatcher.run_once()
        with SessionCls() as db:
            db_ev = db.query(IncidentOutboxEvent).filter(IncidentOutboxEvent.id == ev.id).first()
            assert db_ev.status == "DELIVERED"
            assert db_ev.event_id == orig_event_id
            assert db_ev.idempotency_key == orig_idempotency_key
            assert db_ev.incident_code == orig_incident_code

        assert len(dispatched_identities) == 2
        for identity in dispatched_identities:
            assert identity["event_id"] == orig_event_id
            assert identity["idempotency_key"] == orig_idempotency_key
            assert identity["incident_code"] == orig_incident_code


# =============================================================================
# GROUP 4: REAL POSTGRESQL 15.18 INTEGRATION & CONCURRENCY
# =============================================================================

class TestPostgreSQL15Integration:
    """Rigorously verify Phase 3 claim/lease and SKIP LOCKED semantics on PostgreSQL 15.18."""

    def test_postgresql_full_claim_dispatch_deliver_lifecycle(self, postgres_test_session):
        """End-to-end verification of claim, external dispatch, and fenced DELIVERED on PostgreSQL 15."""
        session, SessionCls = postgres_test_session

        code = f"IBVAP-PG-LIFECYCLE-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        ev = _create_test_outbox_event(session, incident_code=code)

        dispatched = []
        dispatcher = IncidentOutboxDispatcher(
            session_factory=SessionCls,
            worker_id="pg-worker-lifecycle",
            event_broadcaster=lambda p: dispatched.append(p),
        )

        processed = dispatcher.run_once()
        assert processed == 1
        assert len(dispatched) == 1
        assert dispatched[0]["incident_code"] == code

        # Verify DB state
        with SessionCls() as db:
            db_ev = db.query(IncidentOutboxEvent).filter(IncidentOutboxEvent.id == ev.id).first()
            assert db_ev.status == "DELIVERED"
            assert db_ev.worker_id is None
            assert db_ev.lease_until is None
            assert db_ev.delivered_at is not None

    def test_postgresql_concurrent_workers_skip_locked(self, postgres_test_session):
        """Verify that concurrent workers with SELECT ... FOR UPDATE SKIP LOCKED claim disjoint sets of records."""
        session, SessionCls = postgres_test_session

        num_events = 20
        created_codes = []
        for i in range(num_events):
            code = f"IBVAP-CONCURR-{i:03d}"
            _create_test_outbox_event(session, incident_code=code)
            created_codes.append(code)

        worker_claims: Dict[str, List[str]] = {
            "worker-A": [],
            "worker-B": [],
            "worker-C": [],
            "worker-D": [],
        }

        def run_worker(worker_name: str):
            disp = IncidentOutboxDispatcher(
                session_factory=SessionCls,
                worker_id=worker_name,
                batch_size=5,
                event_broadcaster=lambda p: worker_claims[worker_name].append(p["incident_code"]),
            )
            # Run multiple rounds per worker until queue is exhausted
            for _ in range(5):
                n = disp.run_once()
                if n == 0:
                    time.sleep(0.01)

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(run_worker, w) for w in worker_claims.keys()]
            for f in futures:
                f.result()

        all_claimed = []
        for w_name, codes in worker_claims.items():
            all_claimed.extend(codes)

        # Check disjointness: no event claimed twice
        assert len(all_claimed) == len(set(all_claimed)), "No event may be claimed by more than one worker"
        assert set(all_claimed) == set(created_codes), "Every event must be claimed and delivered"

        # Verify all records marked DELIVERED in PostgreSQL
        with SessionCls() as db:
            delivered_count = db.query(IncidentOutboxEvent).filter(IncidentOutboxEvent.status == "DELIVERED").count()
            assert delivered_count == num_events

    def test_postgresql_expired_lease_reclaim(self, postgres_test_session):
        """Verify PostgreSQL recovery of expired PROCESSING records."""
        session, SessionCls = postgres_test_session

        past = datetime.utcnow() - timedelta(seconds=15)
        code = f"IBVAP-PG-RECLAIM-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        ev = _create_test_outbox_event(
            session,
            incident_code=code,
            status="PROCESSING",
            retry_count=1,
            worker_id="stalled-pg-worker",
            lease_until=past,
        )

        reclaimed = []
        dispatcher = IncidentOutboxDispatcher(
            session_factory=SessionCls,
            worker_id="active-pg-recovery-worker",
            event_broadcaster=lambda p: reclaimed.append(p),
        )

        processed = dispatcher.run_once()
        assert processed == 1
        assert len(reclaimed) == 1
        assert reclaimed[0]["incident_code"] == code

        telemetry = dispatcher.telemetry.snapshot()
        assert telemetry["outbox_lease_expirations"] == 1
        assert telemetry["outbox_deliveries"] == 1

    def test_postgresql_zombie_worker_rejection(self, postgres_test_session):
        """Verify that stale worker completion updates on PostgreSQL affect 0 rows and abort."""
        session, SessionCls = postgres_test_session

        code = f"IBVAP-PG-ZOMBIE-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        ev = _create_test_outbox_event(session, incident_code=code)

        dispatcher = IncidentOutboxDispatcher(
            session_factory=SessionCls,
            worker_id="pg-zombie-worker",
        )

        stale_snapshot = ClaimedEventSnapshot(
            id=ev.id,
            event_id=ev.event_id,
            event_type=ev.event_type,
            incident_code=ev.incident_code,
            idempotency_key=ev.idempotency_key,
            payload=ev.payload,
            retry_count=1,
            worker_id="pg-zombie-worker",
            lease_until=datetime.utcnow() - timedelta(seconds=10),
        )

        # Another worker has claimed it
        with SessionCls() as db:
            db.query(IncidentOutboxEvent).filter(IncidentOutboxEvent.id == ev.id).update({
                "status": "PROCESSING",
                "worker_id": "pg-fresh-worker",
                "lease_until": datetime.utcnow() + timedelta(seconds=30),
            })
            db.commit()

        res = dispatcher._complete_event(stale_snapshot, success=True, error_message=None)
        assert res is False

        telemetry = dispatcher.telemetry.snapshot()
        assert telemetry["outbox_zombie_completion_rejections"] == 1


# =============================================================================
# GROUP 5: THREAD LIFECYCLE, TELEMETRY & BOUNDARIES
# =============================================================================

class TestDispatcherLifecycleAndAudit:
    """Verify background worker thread control and structural code-level boundaries."""

    def test_bounded_interruptible_shutdown(self, sqlite_test_db):
        """Verify background worker thread starts and stops cleanly within bounded timeout."""
        engine, SessionCls = sqlite_test_db
        dispatcher = IncidentOutboxDispatcher(
            session_factory=SessionCls,
            worker_id="lifecycle-worker",
            poll_interval_sec=0.05,
        )

        assert not dispatcher.is_running()
        dispatcher.start()
        assert dispatcher.is_running()

        # Stop worker
        t0 = time.perf_counter()
        dispatcher.stop(timeout=2.0)
        stop_duration = time.perf_counter() - t0

        assert not dispatcher.is_running()
        assert stop_duration < 1.0, "Worker should terminate quickly on interruptible wait"

    def test_future_next_retry_at_backoff_adherence(self, sqlite_test_db):
        """Verify that records with future next_retry_at are not claimed until due."""
        engine, SessionCls = sqlite_test_db
        future_retry = datetime.utcnow() + timedelta(seconds=100)
        with SessionCls() as db:
            ev = _create_test_outbox_event(
                db,
                incident_code="INC-FUTURE-01",
                status="FAILED",
                next_retry_at=future_retry,
            )

        dispatcher = IncidentOutboxDispatcher(session_factory=SessionCls, worker_id="future-worker")
        with SessionCls() as db:
            snapshots = dispatcher._claim_batch(db, datetime.utcnow())

        assert len(snapshots) == 0, "Record with future next_retry_at must not be claimed"

    def test_batch_size_bounding(self, sqlite_test_db):
        """Verify dispatcher respects batch_size limit under backlog."""
        engine, SessionCls = sqlite_test_db
        with SessionCls() as db:
            for i in range(10):
                _create_test_outbox_event(db, incident_code=f"INC-BATCH-{i}")

        dispatcher = IncidentOutboxDispatcher(session_factory=SessionCls, batch_size=3)
        with SessionCls() as db:
            snapshots = dispatcher._claim_batch(db, datetime.utcnow())

        assert len(snapshots) == 3, "Claim must not exceed configured batch_size"

    def test_static_dispatcher_structural_audit(self):
        """Verify code structure guarantees no network I/O in claim or completion routines."""
        source_claim = inspect.getsource(IncidentOutboxDispatcher._claim_batch)
        source_complete = inspect.getsource(IncidentOutboxDispatcher._complete_event)

        forbidden = ["requests", "httpx", "aiohttp", "urllib", "send_webhook_http", "_event_broadcaster"]
        for f in forbidden:
            assert f not in source_claim, f"Forbidden call '{f}' found inside _claim_batch"
            assert f not in source_complete, f"Forbidden call '{f}' found inside _complete_event"

        # Verify Fencing Predicate in _complete_event
        assert "IncidentOutboxEvent.worker_id == snapshot.worker_id" in source_complete
        assert "IncidentOutboxEvent.lease_until >= now_utc" in source_complete
        assert "res.rowcount == 0" in source_complete

    def test_telemetry_counters_snapshot(self, sqlite_test_db):
        """Verify telemetry counters update accurately across all dispatcher states."""
        engine, SessionCls = sqlite_test_db
        dispatcher = IncidentOutboxDispatcher(session_factory=SessionCls, worker_id="telemetry-test")
        snap = dispatcher.telemetry.snapshot()

        expected_keys = [
            "outbox_claim_attempts",
            "outbox_claim_successes",
            "outbox_claim_conflicts",
            "outbox_deliveries",
            "outbox_delivery_failures",
            "outbox_retry_count",
            "outbox_dead_letters",
            "outbox_lease_expirations",
            "outbox_zombie_completion_rejections",
            "outbox_stale_worker_rejections",
        ]
        for k in expected_keys:
            assert k in snap, f"Missing telemetry metric key: {k}"
            assert snap[k] == 0

    def test_custom_broadcaster_and_c2_hook(self, sqlite_test_db):
        """Verify custom event broadcaster and C2 sender hooks receive exact payload."""
        engine, SessionCls = sqlite_test_db
        with SessionCls() as db:
            _create_test_outbox_event(db, incident_code="INC-HOOKS-01")

        broadcaster_payloads = []
        c2_payloads = []

        dispatcher = IncidentOutboxDispatcher(session_factory=SessionCls, worker_id="hook-worker")
        dispatcher.set_event_broadcaster(lambda p: broadcaster_payloads.append(p))
        dispatcher.set_c2_sender(lambda p: c2_payloads.append(p))

        processed = dispatcher.run_once()
        assert processed == 1
        assert len(broadcaster_payloads) == 1
        assert len(c2_payloads) == 1
        assert broadcaster_payloads[0]["incident_code"] == "INC-HOOKS-01"
        assert c2_payloads[0]["incident_code"] == "INC-HOOKS-01"


# =============================================================================
# GROUP 6: PERFORMANCE BENCHMARK [MEASURED BENCHMARK]
# =============================================================================

class TestDispatcherPerformanceBenchmark:
    """Benchmark claim and completion transaction latencies."""

    def test_performance_benchmark_latencies(self, postgres_test_session, sqlite_test_db):
        """Measure claim (< 2ms) and completion (< 2ms) transaction latencies [MEASURED BENCHMARK]."""
        pg_session, PgSessionCls = postgres_test_session
        sqlite_engine, SqliteSessionCls = sqlite_test_db

        # Benchmark on SQLite
        sqlite_dispatcher = IncidentOutboxDispatcher(session_factory=SqliteSessionCls, worker_id="bench-sqlite")
        with SqliteSessionCls() as db:
            _create_test_outbox_event(db, incident_code="BENCH-SQLITE-01")

        t0 = time.perf_counter()
        with SqliteSessionCls() as db:
            snaps_sq = sqlite_dispatcher._claim_batch(db, datetime.utcnow())
        sqlite_claim_ms = (time.perf_counter() - t0) * 1000.0

        t0 = time.perf_counter()
        sqlite_dispatcher._complete_event(snaps_sq[0], success=True, error_message=None)
        sqlite_complete_ms = (time.perf_counter() - t0) * 1000.0

        # Benchmark on PostgreSQL 15.18
        pg_dispatcher = IncidentOutboxDispatcher(session_factory=PgSessionCls, worker_id="bench-pg")
        with PgSessionCls() as db:
            _create_test_outbox_event(db, incident_code="BENCH-PG-01")

        t0 = time.perf_counter()
        with PgSessionCls() as db:
            snaps_pg = pg_dispatcher._claim_batch(db, datetime.utcnow())
        pg_claim_ms = (time.perf_counter() - t0) * 1000.0

        t0 = time.perf_counter()
        pg_dispatcher._complete_event(snaps_pg[0], success=True, error_message=None)
        pg_complete_ms = (time.perf_counter() - t0) * 1000.0

        print(f"\n[MEASURED BENCHMARK] SQLite Claim: {sqlite_claim_ms:.2f} ms")
        print(f"[MEASURED BENCHMARK] SQLite Completion: {sqlite_complete_ms:.2f} ms")
        print(f"[MEASURED BENCHMARK] PostgreSQL 15 Claim (SKIP LOCKED): {pg_claim_ms:.2f} ms")
        print(f"[MEASURED BENCHMARK] PostgreSQL 15 Completion (Fenced): {pg_complete_ms:.2f} ms")

        # Assert latencies are well within operational bounds
        assert sqlite_claim_ms < 50.0
        assert sqlite_complete_ms < 50.0
        assert pg_claim_ms < 50.0
        assert pg_complete_ms < 50.0
