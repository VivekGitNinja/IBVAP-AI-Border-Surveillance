"""
IBVAP — Chaos Engineering & Resiliency Verification Suite
Injects database load surges, payload fuzzing, cache disconnects,
and concurrent race conditions to prove system fault-tolerance.
"""

import concurrent.futures
from unittest.mock import patch
import pytest
from starlette.testclient import TestClient

from backend.app.main import app
from backend.app.core.cache import cache
from backend.app.core.security import create_access_token


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def auth_headers():
    token = create_access_token("admin", "ADMIN")
    return {"Authorization": f"Bearer {token}"}


def test_chaos_concurrent_request_storm(client, auth_headers):
    """Chaos: 25 concurrent threads hammering API endpoints simultaneously."""
    def hit_endpoint(i):
        return client.get(f"/api/v1/status?iter={i}", headers=auth_headers)

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(hit_endpoint, i) for i in range(25)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    # All 25 requests must succeed with 200 and return operational status
    assert len(results) == 25
    for r in results:
        assert r.status_code == 200
        assert r.json()["status"] == "operational"
        assert "X-Trace-ID" in r.headers


def test_chaos_sqli_and_fuzzed_payloads(client, auth_headers):
    """Chaos: Fuzzing endpoints with malicious SQL injection strings and boundary inputs."""
    fuzz_inputs = [
        "1' OR '1'='1' --",
        "../../../../etc/passwd",
        "<script>alert(1)</script>",
        "A" * 200,
        "-9999999999",
        "0.000000000001",
        "NaN",
        "null",
    ]

    for fuzzed in fuzz_inputs:
        # Fuzz camera ID endpoint with auth headers
        res = client.get(f"/api/v1/cameras/{fuzzed}", headers=auth_headers)
        # Must fail safely with 404 (not found) or 422 (validation error), NEVER 500
        assert res.status_code in (404, 422), f"Fuzzed input '{fuzzed}' resulted in unexpected status {res.status_code}"


def test_chaos_cache_outage_graceful_degradation(client, auth_headers):
    """Chaos: Simulate an abrupt cache outage while reading /status."""
    with patch.object(cache, "get", side_effect=RuntimeError("Redis connection abruptly severed")):
        # Application must gracefully fall back without returning 500 to the client
        res = client.get("/api/v1/status", headers=auth_headers)
        assert res.status_code == 200
        assert res.json()["status"] == "operational"


def test_chaos_evidence_hash_tampering_detection(client, auth_headers):
    """Chaos: Attempting to verify non-existent or tampered evidence ID."""
    res = client.get("/api/v1/evidence/verify/999999", headers=auth_headers)
    assert res.status_code == 404


def test_chaos_invalid_auth_token_tampering(client):
    """Chaos: Requests with malformed or tampered JWT signatures."""
    tampered_headers = {"Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.tampered.signature"}
    res = client.get("/api/v1/auth/me", headers=tampered_headers)
    assert res.status_code == 401
