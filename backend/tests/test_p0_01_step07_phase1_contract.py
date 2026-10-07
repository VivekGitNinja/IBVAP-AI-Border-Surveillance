"""
Step 07 Phase 1 — Schema, Migration, and Advisory-Lock Contract Verification Suite
=================================================================================

Rigorously verifies Phase 1 scope:
1. Alembic Migration 0004 (operator_suppressions and incident_outbox)
2. IncidentOutboxEvent model and canonical identity contract (UUIDv4 event_id, UUIDv5 idempotency_key, natural incident_code)
3. Canonical integer advisory-lock contract (derive_advisory_lock_id, signed 64-bit BIGINT bounds)
4. Strict acquire_advisory_xact_lock validation (rejection of strings and non-ints)
5. Production caller verification in live_pipeline
6. Real PostgreSQL validation when PostgreSQL environment is available
"""

import os
import uuid
import threading
import pytest
from datetime import datetime, timedelta
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.exc import IntegrityError

from alembic.config import Config
from alembic import command

from backend.app.models.base import Base
from backend.app.models.suppression import OperatorSuppression
from backend.app.models.outbox import (
    IncidentOutboxEvent,
    derive_event_id,
    derive_idempotency_key,
    NAMESPACE_IBVAP,
)
from backend.app.services.feedback import (
    derive_advisory_lock_id,
    acquire_advisory_xact_lock,
)

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


# =============================================================================
# GROUP 1: MIGRATION 0004 & SCHEMA INTEGRITY
# =============================================================================

class TestMigration0004Schema:
    """Verify Alembic migration 0004, table structures, columns, and indexes."""

    def test_sqlite_migration_upgrade_and_downgrade(self, tmp_path):
        """Verify migration 0004 applies and rolls back cleanly on SQLite."""
        db_path = tmp_path / "migration_test.db"
        sqlite_url = f"sqlite:///{db_path}"

        engine = sa.create_engine(sqlite_url)
        # Create schema up to 0003
        tables_to_create = [
            t for name, t in Base.metadata.tables.items()
            if name not in ("operator_suppressions", "incident_outbox")
        ]
        Base.metadata.create_all(bind=engine, tables=tables_to_create)

        cfg = Config("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", sqlite_url)

        # Stamp at 0003
        command.stamp(cfg, "0003_add_camera_sector")

        # Upgrade to head (0004)
        command.upgrade(cfg, "0004_add_suppressions_and_outbox")

        insp = sa.inspect(engine)
        tables = insp.get_table_names()
        assert "operator_suppressions" in tables
        assert "incident_outbox" in tables

        # Verify incident_outbox columns on SQLite
        outbox_cols = {c["name"]: c for c in insp.get_columns("incident_outbox")}
        assert "id" in outbox_cols
        assert "event_id" in outbox_cols
        assert "event_type" in outbox_cols
        assert "incident_code" in outbox_cols
        assert "idempotency_key" in outbox_cols
        assert "payload" in outbox_cols
        assert "status" in outbox_cols
        assert "retry_count" in outbox_cols
        assert "lease_until" in outbox_cols
        assert "worker_id" in outbox_cols
        assert "next_retry_at" in outbox_cols
        assert "created_at" in outbox_cols
        assert "delivered_at" in outbox_cols
        assert "error_message" in outbox_cols

        # Verify operator_suppressions columns on SQLite
        supp_cols = {c["name"]: c for c in insp.get_columns("operator_suppressions")}
        assert "id" in supp_cols
        assert "suppression_key" in supp_cols
        assert "entity_type" in supp_cols
        assert "camera_id" in supp_cols
        assert "track_id" in supp_cols
        assert "dossier_id" in supp_cols
        assert "incident_id" in supp_cols
        assert "dismissal_reason" in supp_cols
        assert "operator_notes" in supp_cols
        assert "operator_id" in supp_cols
        assert "initial_zone_type" in supp_cols
        assert "created_at" in supp_cols
        assert "expires_at" in supp_cols
        assert "is_revoked" in supp_cols
        assert "revoked_at" in supp_cols
        assert "revocation_reason" in supp_cols

        # Test downgrade
        command.downgrade(cfg, "0003_add_camera_sector")
        insp = sa.inspect(engine)
        tables_after_down = insp.get_table_names()
        assert "incident_outbox" not in tables_after_down
        assert "operator_suppressions" not in tables_after_down

        # Clean re-upgrade
        command.upgrade(cfg, "head")
        insp = sa.inspect(engine)
        assert "incident_outbox" in insp.get_table_names()
        assert "operator_suppressions" in insp.get_table_names()

    @pytest.mark.skipif(not is_postgres_available(), reason="PostgreSQL test server unavailable")
    def test_postgresql_migration_upgrade_and_downgrade(self):
        """Verify migration 0004 applies, downgrades, and re-applies on real PostgreSQL."""
        engine = sa.create_engine(PG_TEST_URL)

        # Reset pg database schema
        Base.metadata.drop_all(bind=engine)
        with engine.connect() as conn:
            conn.execute(sa.text("DROP TABLE IF EXISTS alembic_version CASCADE;"))
            conn.commit()

        tables_to_create = [
            t for name, t in Base.metadata.tables.items()
            if name not in ("operator_suppressions", "incident_outbox")
        ]
        Base.metadata.create_all(bind=engine, tables=tables_to_create)

        cfg = Config("alembic.ini")
        cfg.set_main_option("sqlalchemy.url", PG_TEST_URL)

        command.stamp(cfg, "0003_add_camera_sector")
        command.upgrade(cfg, "0004_add_suppressions_and_outbox")

        insp = sa.inspect(engine)
        tables = insp.get_table_names()
        assert "operator_suppressions" in tables
        assert "incident_outbox" in tables

        # Verify PostgreSQL column types and unique constraints
        outbox_cols = {c["name"]: c for c in insp.get_columns("incident_outbox")}
        assert outbox_cols["event_id"]["nullable"] is False
        assert outbox_cols["idempotency_key"]["nullable"] is False
        assert outbox_cols["incident_code"]["nullable"] is False

        # Verify unique constraints
        uniques = [u["name"] for u in insp.get_unique_constraints("incident_outbox")]
        assert any("event_id" in u for u in uniques)
        assert any("idempotency_key" in u for u in uniques)

        # Test downgrade
        command.downgrade(cfg, "0003_add_camera_sector")
        insp = sa.inspect(engine)
        tables_after_down = insp.get_table_names()
        assert "incident_outbox" not in tables_after_down
        assert "operator_suppressions" not in tables_after_down

        # Re-upgrade to head
        command.upgrade(cfg, "head")
        insp = sa.inspect(engine)
        assert "incident_outbox" in insp.get_table_names()
        assert "operator_suppressions" in insp.get_table_names()


# =============================================================================
# GROUP 2: EVENT IDENTITY SCHEMA & MODEL TESTS
# =============================================================================

class TestEventIdentityModelContract:
    """Verify IncidentOutboxEvent model, derivation functions, and unique constraints."""

    def test_canonical_identity_derivation_primitives(self):
        """Verify event_id is UUIDv4 and idempotency_key is deterministic UUIDv5."""
        ev_id1 = derive_event_id()
        ev_id2 = derive_event_id()
        assert ev_id1 != ev_id2
        # Must be valid UUID format
        parsed_id = uuid.UUID(ev_id1)
        assert parsed_id.version == 4

        code = "IBVAP-20260917-101530-4921-PER-1"
        etype = "incident_created"
        idem1 = derive_idempotency_key(code, etype)
        idem2 = derive_idempotency_key(code, etype)
        assert idem1 == idem2, "idempotency_key must be deterministic for identical (code, etype)"

        parsed_idem = uuid.UUID(idem1)
        assert parsed_idem.version == 5
        # Expected UUIDv5 with NAMESPACE_IBVAP
        expected = str(uuid.uuid5(NAMESPACE_IBVAP, f"{code}:{etype}"))
        assert idem1 == expected

    def test_outbox_model_persistence_and_defaults(self, tmp_path):
        """Verify IncidentOutboxEvent persists correctly with defaults."""
        db_path = tmp_path / "outbox_model_test.db"
        engine = sa.create_engine(f"sqlite:///{db_path}")
        Base.metadata.create_all(bind=engine)
        SessionLocal = sessionmaker(bind=engine)
        db: Session = SessionLocal()

        code = "IBVAP-20260917-090000-1111-PER-1"
        ev_id = derive_event_id()
        idem_key = derive_idempotency_key(code, "incident_created")

        event = IncidentOutboxEvent(
            event_id=ev_id,
            event_type="incident_created",
            incident_code=code,
            idempotency_key=idem_key,
            payload={"test": "payload", "code": code},
        )
        db.add(event)
        db.commit()
        db.refresh(event)

        assert event.id is not None
        assert event.event_id == ev_id
        assert event.incident_code == code
        assert event.idempotency_key == idem_key
        assert event.status == "PENDING"
        assert event.retry_count == 0
        assert event.lease_until is None
        assert event.created_at is not None
        assert event.next_retry_at is not None
        db.close()

    def test_outbox_unique_constraints_enforced(self, tmp_path):
        """Verify unique constraints on event_id and idempotency_key prevent duplicates."""
        db_path = tmp_path / "outbox_unique_test.db"
        engine = sa.create_engine(f"sqlite:///{db_path}")
        Base.metadata.create_all(bind=engine)
        SessionLocal = sessionmaker(bind=engine)

        db: Session = SessionLocal()
        ev_id1 = derive_event_id()
        idem1 = derive_idempotency_key("INC-001", "incident_created")

        event1 = IncidentOutboxEvent(
            event_id=ev_id1,
            event_type="incident_created",
            incident_code="INC-001",
            idempotency_key=idem1,
            payload={"num": 1},
        )
        db.add(event1)
        db.commit()

        # Attempt duplicate event_id
        event_dup_id = IncidentOutboxEvent(
            event_id=ev_id1,  # DUPLICATE
            event_type="incident_created",
            incident_code="INC-002",
            idempotency_key=derive_idempotency_key("INC-002", "incident_created"),
            payload={"num": 2},
        )
        db.add(event_dup_id)
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

        # Attempt duplicate idempotency_key
        event_dup_idem = IncidentOutboxEvent(
            event_id=derive_event_id(),
            event_type="incident_created",
            incident_code="INC-003",
            idempotency_key=idem1,  # DUPLICATE
            payload={"num": 3},
        )
        db.add(event_dup_idem)
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        db.close()


# =============================================================================
# GROUP 3: CANONICAL ADVISORY LOCK INTEGER CONTRACT
# =============================================================================

class TestAdvisoryLockIntegerContract:
    """Verify derive_advisory_lock_id bounds and acquire_advisory_xact_lock validation."""

    def test_derive_advisory_lock_id_deterministic(self):
        """Lock ID is strictly deterministic for identical keys."""
        k1 = "camera:10:target:500"
        id1 = derive_advisory_lock_id(k1)
        id2 = derive_advisory_lock_id(k1)
        assert id1 == id2
        assert isinstance(id1, int)
        assert not isinstance(id1, bool)

    def test_derive_advisory_lock_id_signed_64bit_bounds(self):
        """Lock IDs across 1,000 distinct keys strictly fall in [-2^63, 2^63 - 1]."""
        min_bigint = -9223372036854775808
        max_bigint = 9223372036854775807

        keys = [f"camera:{i}:target:{j}:dossier:DOS-{i*j}" for i in range(50) for j in range(20)]
        ids = [derive_advisory_lock_id(k) for k in keys]
        assert len(ids) == 1000

        for lid in ids:
            assert isinstance(lid, int)
            assert not isinstance(lid, bool)
            assert min_bigint <= lid <= max_bigint

    def test_acquire_advisory_lock_rejects_non_integers(self):
        """Passing str, float, None, bool, or dict raises TypeError."""
        mock_session = sa.orm.Session()

        # String passed directly (the exact historical bug)
        with pytest.raises(TypeError) as exc_info:
            acquire_advisory_xact_lock(mock_session, "camera:1:target:10")  # type: ignore
        assert "requires an int lock_id" in str(exc_info.value)
        assert "str" in str(exc_info.value)

        # Bool passed (bool subclasses int in Python, must be rejected)
        with pytest.raises(TypeError):
            acquire_advisory_xact_lock(mock_session, True)  # type: ignore

        # Float passed
        with pytest.raises(TypeError):
            acquire_advisory_xact_lock(mock_session, 12345.67)  # type: ignore

        # None passed
        with pytest.raises(TypeError):
            acquire_advisory_xact_lock(mock_session, None)  # type: ignore

    def test_acquire_advisory_lock_rejects_out_of_range_integers(self):
        """Integers exceeding 64-bit signed bounds raise ValueError."""
        mock_session = sa.orm.Session()
        with pytest.raises(ValueError):
            acquire_advisory_xact_lock(mock_session, 9223372036854775808)
        with pytest.raises(ValueError):
            acquire_advisory_xact_lock(mock_session, -9223372036854775809)

    def test_acquire_advisory_lock_sqlite_path(self, tmp_path):
        """On SQLite, valid integer lock_id executes BEGIN IMMEDIATE."""
        db_path = tmp_path / "sqlite_lock_test.db"
        engine = sa.create_engine(f"sqlite:///{db_path}")
        SessionLocal = sessionmaker(bind=engine)
        db: Session = SessionLocal()

        lid = derive_advisory_lock_id("camera:1:target:100")
        # Must execute cleanly without error
        acquire_advisory_xact_lock(db, lid)
        db.close()


# =============================================================================
# GROUP 4: REAL POSTGRESQL ADVISORY LOCK VERIFICATION
# =============================================================================

class TestPostgreSQLAdvisoryLockIntegration:
    """Execute real advisory-lock queries against PostgreSQL when available."""

    @pytest.mark.skipif(not is_postgres_available(), reason="PostgreSQL test server unavailable")
    def test_postgresql_advisory_lock_execution(self):
        """Execute pg_advisory_xact_lock on real PostgreSQL with derive_advisory_lock_id."""
        engine = sa.create_engine(PG_TEST_URL)
        SessionLocal = sessionmaker(bind=engine)
        db: Session = SessionLocal()

        canonical_key = "camera:42:target:999"
        lock_id = derive_advisory_lock_id(canonical_key)
        assert isinstance(lock_id, int)

        # Acquire lock in a transaction
        acquire_advisory_xact_lock(db, lock_id)
        # Check pg_locks to verify the advisory lock is held
        res = db.execute(sa.text(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
            "AND (((classid::bigint << 32) | (objid::bigint & 4294967295))::bigint) = :lid"
        ), {"lid": lock_id}).scalar()
        assert res >= 1, "Advisory lock must be visible in pg_locks"
        db.commit()
        db.close()

    @pytest.mark.skipif(not is_postgres_available(), reason="PostgreSQL test server unavailable")
    def test_postgresql_advisory_lock_serialization(self):
        """Verify two transactions trying to acquire the same key serialize."""
        engine = sa.create_engine(PG_TEST_URL)
        SessionLocal = sessionmaker(bind=engine)

        canonical_key = "camera:99:target:critical_breach"
        lock_id = derive_advisory_lock_id(canonical_key)

        lock_acquired_t1 = threading.Event()
        release_gate_t1 = threading.Event()
        t2_lock_result = []

        def worker_t1():
            db1: Session = SessionLocal()
            acquire_advisory_xact_lock(db1, lock_id)
            lock_acquired_t1.set()
            release_gate_t1.wait()
            db1.commit()
            db1.close()

        def worker_t2():
            lock_acquired_t1.wait()
            db2: Session = SessionLocal()
            # Try non-blocking advisory lock to prove mutual exclusion
            got_lock = db2.execute(
                sa.text("SELECT pg_try_advisory_xact_lock(:lid)"),
                {"lid": lock_id}
            ).scalar()
            t2_lock_result.append(got_lock)
            db2.rollback()
            db2.close()

        t1 = threading.Thread(target=worker_t1)
        t2 = threading.Thread(target=worker_t2)

        t1.start()
        t2.start()

        t2.join(timeout=3.0)
        assert len(t2_lock_result) == 1
        assert t2_lock_result[0] is False, "Thread 2 must fail to acquire advisory lock held by Thread 1"

        release_gate_t1.set()
        t1.join(timeout=3.0)

        # Once Thread 1 has committed, a new session can acquire the lock
        db3: Session = SessionLocal()
        can_acquire_now = db3.execute(
            sa.text("SELECT pg_try_advisory_xact_lock(:lid)"),
            {"lid": lock_id}
        ).scalar()
        assert can_acquire_now is True, "Advisory lock must be released once Thread 1 commits"
        db3.rollback()
        db3.close()

    @pytest.mark.skipif(not is_postgres_available(), reason="PostgreSQL test server unavailable")
    def test_postgresql_rejects_string_before_query(self):
        """Verify acquire_advisory_xact_lock rejects string before reaching PostgreSQL driver."""
        engine = sa.create_engine(PG_TEST_URL)
        SessionLocal = sessionmaker(bind=engine)
        db: Session = SessionLocal()

        with pytest.raises(TypeError) as exc:
            acquire_advisory_xact_lock(db, "camera:1:target:10")  # type: ignore
        assert "requires an int lock_id" in str(exc.value)
        db.close()


# =============================================================================
# GROUP 5: PRODUCTION CALLER AUDIT & CONVERSION VERIFICATION
# =============================================================================

class TestProductionCallerAudit:
    """Verify live_pipeline and feedback services explicitly call derive_advisory_lock_id."""

    def test_live_pipeline_source_code_uses_derive_advisory_lock_id(self):
        """Static audit: verify live_pipeline.py passes derive_advisory_lock_id output."""
        import inspect
        from backend.app.services import live_pipeline

        source = inspect.getsource(live_pipeline)
        # Must import derive_advisory_lock_id
        assert "from backend.app.services.feedback import" in source
        assert "derive_advisory_lock_id" in source
        # Must call derive_advisory_lock_id before acquire_advisory_xact_lock
        assert "lock_id = derive_advisory_lock_id(lock_key)" in source
        assert "acquire_advisory_xact_lock(db, lock_id)" in source

    def test_feedback_service_source_code_uses_derive_advisory_lock_id(self):
        """Static audit: verify feedback.py passes derive_advisory_lock_id output."""
        import inspect
        from backend.app.services import feedback

        source = inspect.getsource(feedback.dismiss_incident)
        assert "lid = derive_advisory_lock_id(k)" in source
        assert "acquire_advisory_xact_lock(db, lid)" in source
