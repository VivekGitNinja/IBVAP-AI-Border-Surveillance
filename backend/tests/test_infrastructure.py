"""
IBVAP — Data & Infrastructure Verification Suite
Tests Alembic schema integrity, database composite indexes,
and Redis/Memory hybrid caching layer.
"""

import time
import pytest
from starlette.testclient import TestClient

from backend.app.main import app
from backend.app.db.session import engine
from backend.app.db.migrator import DatabaseMigrator
from backend.app.core.cache import cache, cached, invalidate_cache
from backend.app.core.security import create_access_token
from backend.app.models.incident import Incident
from backend.app.models.audit import AuditLog
from backend.app.models.camera import Camera
from backend.app.models.evidence import Evidence


@pytest.fixture
def client():
    return TestClient(app)


def test_database_composite_indexes_declared():
    """Verify high-performance composite indexes are declared on SQLAlchemy models."""
    incident_idx = {idx.name for idx in Incident.__table__.indexes}
    assert "ix_incidents_status_severity" in incident_idx
    assert "ix_incidents_camera_created" in incident_idx
    assert "ix_incidents_threat_score" in incident_idx

    audit_idx = {idx.name for idx in AuditLog.__table__.indexes}
    assert "ix_audit_logs_actor_created" in audit_idx
    assert "ix_audit_logs_target" in audit_idx

    camera_idx = {idx.name for idx in Camera.__table__.indexes}
    assert "ix_cameras_bop_status" in camera_idx
    assert "ix_cameras_status" in camera_idx

    evidence_idx = {idx.name for idx in Evidence.__table__.indexes}
    assert "ix_evidence_incident_type" in evidence_idx


def test_migrator_schema_synchronization_and_audit():
    """Verify autonomous migrator verifies all 13 core tables and active indexes."""
    migrator = DatabaseMigrator(engine)
    res = migrator.run_migrations()
    assert res["status"] == "synchronized"
    assert res["tables_count"] >= 13
    assert res["indexes_verified"] > 20

    integrity = migrator.verify_schema_integrity()
    assert integrity["healthy"] is True
    assert len(integrity["missing_tables"]) == 0
    assert "incidents" in integrity["indexes"]
    assert "cameras" in integrity["indexes"]


def test_hybrid_cache_crud_and_ttl():
    """Verify cache get, set, expiration, and deletion operations."""
    cache.set("test:tactical:key", "payload_alpha", ttl_seconds=1)
    assert cache.get("test:tactical:key") == "payload_alpha"

    cache.delete("test:tactical:key")
    assert cache.get("test:tactical:key") is None

    # Test TTL expiration
    cache.set("test:tactical:expire", "short_lived", ttl_seconds=1)
    assert cache.get("test:tactical:expire") == "short_lived"
    time.sleep(1.1)
    assert cache.get("test:tactical:expire") is None


def test_cached_function_decorator():
    """Verify @cached decorator returns cached results and bypasses computation."""
    call_count = 0

    @cached(ttl_seconds=10, key_prefix="test_counter")
    def expensive_operation(param: str):
        nonlocal call_count
        call_count += 1
        return {"data": param, "computed_at": call_count}

    res1 = expensive_operation("sector_7")
    assert res1["computed_at"] == 1

    res2 = expensive_operation("sector_7")
    assert res2["computed_at"] == 1  # Cache HIT: computation not repeated!
    assert call_count == 1

    # Invalidate cache
    invalidate_cache("test_counter")
    res3 = expensive_operation("sector_7")
    assert res3["computed_at"] == 2  # Cache MISS: recomputed!
    assert call_count == 2


def test_cached_system_status_api(client):
    """Verify /api/v1/status endpoint utilizes caching without breaking response schema."""
    token = create_access_token("operator", "OPERATOR")
    response = client.get("/api/v1/status", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "operational"
    assert "cameras_total" in data
    assert "active_incidents" in data
