# IBVAP GATE 3 — FINAL CROSS-GATE TRUTH RECONCILIATION

**Authoritative Cross-Gate Forensic & Truth Reconciliation Document**  
**Classification:** RESTRICTED / LAW ENFORCEMENT & BORDER SURVEILLANCE SENSITIVE  
**Platform:** Intelligent Border Video Analytics Platform (IBVAP) — SIH 2026 Problem Statement 26187  
**Date:** September 20, 2026  
**Final Evaluation Status:** **GATE 3 CROSS-GATE RECONCILIATION — PASSED**  
**Security Reference Frameworks:** OWASP Top 10 API Security (2023), CIS Benchmark Level 1, NIST SP 800-53 Rev. 5, ISO/IEC 27037:2012  

---

## 1. GATE 1A → GATE 2 → GATE 3 CHRONOLOGY

| Gate Phase | Objective & Scope | Entry Baseline | Exit Verified Surface | Test Pass Record | Sign-Off Status |
|---|---|---|---|---|---|
| **Gate 1A** | Surface discovery & P0 vulnerability enumeration | Legacy academic prototype | 118 ASGI surfaces (109 APIRoute, 3 WS, 2 Static, 4 Docs) | 0 tests (manual audit) | **CLOSED** (10 P0 findings confirmed) |
| **Gate 2** | Production security remediation & fail-closed RBAC | 118 ASGI surfaces | 124 ASGI surfaces (116 APIRoute, 3 WS, 1 Static, 4 Docs) | 685 / 685 PASSED | **CLOSED & RECONCILED** |
| **Gate 3** | Pre-production security hardening & deployment assurance | 124 ASGI surfaces, 685 tests | 125 ASGI surfaces (117 APIRoute, 3 WS, 1 Static, 4 Docs) | **705 / 705 PASSED** | **PRE-PRODUCTION ASSURANCE VERIFIED** |

---

## 2. IMMUTABLE GATE 1A P0 FINDINGS RESOLUTION MATRIX

The historical Gate 1A P0 vulnerability identifiers are strictly immutable:

| ID | Historical Vulnerability Title | Gate 2 Remediation | Gate 3 Hardening & Verification | Status |
|---|---|---|---|---|
| **P0-01** | Dead / unenforced server-side RBAC | Enforced 5-tier role hierarchy via `require_permission` across all mutating endpoints. | Object-level authorization on job cancellations; verified across all 5 roles. | **CLOSED** |
| **P0-02** | Unauthenticated physical PTZ & power shutdown | Restricted PTZ to `camera_control` and power-off to `camera_power`. | Formally reconciled: OPERATOR has PTZ steering, but power-off is strictly restricted to COMMANDER/ADMIN. | **CLOSED** |
| **P0-03** | Direct static forensic evidence exposure | Removed static mount `/data/evidence`; created authenticated vault streaming. | Enforced token type `access` immunity (stream tickets strictly rejected on vault). | **CLOSED** |
| **P0-04** | Unauthenticated media upload & deletion | Enforced `write`/`delete` permissions; magic bytes validation; 20MB/500MB caps. | Verified zero script/SVG/executable bypasses across all media ingestion pipelines. | **CLOSED** |
| **P0-05** | Unauthenticated watchlist / biometric operations | Protected watchlist and FRS probe endpoints with `write` and `delete` permissions. | Regression verified against VIEWER/AUDITOR role rejections. | **CLOSED** |
| **P0-06** | Unauthenticated ANPR barrier actuation | Restricted `POST /api/v1/anpr/barrier/toggle` to `barrier_control`. | Reconciled: OPERATOR allowed for vehicle inspections; VIEWER forbidden (403). | **CLOSED** |
| **P0-07** | Unauthenticated QRT tactical operations | Restricted QRT dispatch, status, and radio broadcasts to RBAC permissions. | Reconciled: Dispatch strictly restricted to COMMANDER/ADMIN; Status/Broadcast allowed for OPERATOR. | **CLOSED** |
| **P0-08** | Unauthenticated SSRF smart-probe | Restricted probe endpoints; blocked loopback, cloud metadata, link-local, non-whitelisted ports. | Netloc URL rewritten to IP literal; initial socket destination pinned. | **CLOSED** |
| **P0-09** | Hardcoded JWT / C2 fallback secrets | Fail-closed config validation rejecting default/insecure keys in production. | Reconciled and verified: `iss="ibvap-auth"`, `aud="ibvap-api"` strictly enforced. | **CLOSED** |
| **P0-10** | WebSocket authentication bypass | Centralized `_authenticate_websocket` closing unauthorized handshakes with 4001/4003. | Scoped streaming tickets introduced; resource scope and account active state verified. | **CLOSED** |

---

## 3. ROUTE COUNT & SURFACE DELTA

### Programmatic Surface Inspection (`backend.app.main:app.routes`):
```text
Total Registered Surfaces:  125
  ├── APIRoutes:            117
  ├── WebSocketRoutes:        3 (/ws/events, /ws/analysis/{job_id}, /ws/live/{camera_id})
  ├── StaticFiles Mounts:     1 (/assets mount for React SPA dist)
  └── Framework Routes:       4 (/openapi.json, /docs, /docs/oauth2-redirect, /redoc)
```

### Delta Explanation:
* **Gate 2 Baseline Surface:** 124 routes (116 APIRoutes, 3 WebSocketRoutes, 1 Mount, 4 Framework routes).
* **Gate 3 Added Surface (+1 APIRoute):** `POST /api/v1/auth/stream-ticket` was added in Gate 3.2 to issue short-lived, resource-scoped streaming tickets for browser media players and WebSockets.
* **Current Net Surface:** Exactly **125 surfaces**. Zero unauthorized routes added; zero required routes omitted.

---

## 4. TEST COUNT & EXECUTION DELTA

### Pytest Execution Single Source of Truth:
```text
================= 705 passed, 10 warnings in 71.25s (0:01:11) ==================
```

### Mathematical Grouping Reconciliation:
* **Step 01–07 Milestone Baseline (`test_p0_01_*.py`):** **222 tests**
* **Gate 2 Security Remediation (`test_p0_02_*.py`):** **160 tests**
* **Gate 3 Pre-Production Assurance (`test_p0_03_gate3_preprod_assurance.py`):** **9 tests**
* **Gate 3 Truth Reconciliation (`test_p0_03_truth_reconciliation.py`):** **11 tests**
* **Core Platform, Edge, Vision & Concurrency:** **303 tests**
* **Total Collected & Passed:** **705 tests (0 failed, 0 skipped, 0 errors)**.

---

## 5. JWT CLAIM ACTUAL CONFIGURATION

* **Actual Current Issuer:** `"ibvap-auth"`
* **Actual Current Audience:** `"ibvap-api"`
* **Why:** Both `create_access_token()` and `create_streaming_ticket()` in `backend/app/core/security.py` explicitly embed `"iss": "ibvap-auth"` and `"aud": "ibvap-api"`. Gate 2 documentation was correct; the Gate 3 draft contained a typographical discrepancy (`ibvap-core` / `ibvap-clients`) which has now been corrected across all documentation and tests.
* **Verification Tests:**
  - `test_jwt_actual_issuer_and_audience` (asserts `iss="ibvap-auth"` and `aud="ibvap-api"`).
  - `test_jwt_rejects_wrong_issuer` (asserts `jwt.InvalidIssuerError` on forged issuer).
  - `test_jwt_rejects_wrong_audience` (asserts `jwt.InvalidAudienceError` on forged audience).
  - `test_jwt_legacy_token_compatibility` (asserts backward compatibility for tokens without iss/aud).
* **Compatibility Impact:** None. All internal token generation uses `"ibvap-auth"` and `"ibvap-api"`.

---

## 6. LOGIN LOCKOUT ACTUAL CONFIGURATION

* **Actual Threshold:** **5 failed attempts** (`_MAX_FAILED_ATTEMPTS = 5`).
* **Actual Lockout Duration:** **900.0 seconds (15 minutes)** (`_LOCKOUT_WINDOW = 900.0`).
* **Why:** In `backend/app/api/v1/endpoints/auth.py`, lines 29-31 explicitly configure `_LOCKOUT_WINDOW = 900.0` and line 41 returns `"Account locked for 15 minutes."`. Gate 2 was correct; the Gate 3 report had a typo citing 300 seconds (5 minutes) which is now corrected.
* **State Keying:** Keyed strictly on `(client_ip, username)` tuple (`f"{client_ip}:{username}"`).
* **Reset Behavior:** Successful login calls `_reset_attempts(rate_key)` to clear the failure count.
* **Concurrency:** Synchronized via `threading.Lock()` across asynchronous worker threads.
* **Multi-Process Limitation:** Lockout counters are stored in process memory. Deployments using multiple worker processes must utilize sticky routing or a centralized Redis store to aggregate failure attempts across workers.

---

## 7. RBAC ACTUAL CONFIGURATION

### Authoritative Physical & Tactical Control Matrix:

| Action / Capability | Endpoint | Required Permission | OPERATOR | COMMANDER | ADMIN | Operational Rationale |
|---|---|---|---|---|---|---|
| **PTZ Steering** | `POST /api/v1/cameras/{id}/ptz` | `camera_control` | **ALLOWED** | **ALLOWED** | **ALLOWED** | Forward outpost operators must steer PTZ to track live moving threats. |
| **PTZ Goto Preset** | `POST /api/v1/cameras/{id}/ptz/goto` | `camera_control` | **ALLOWED** | **ALLOWED** | **ALLOWED** | Forward outpost operators must rapidly jump cameras to predefined tactical perimeter sectors. |
| **Camera Power Off** | `POST /api/v1/cameras/hardware/power-off-all` | `camera_power` | **FORBIDDEN (403)** | **ALLOWED** | **ALLOWED** | Physical sensor shutdown is an emergency override restricted to senior tactical command. |
| **Barrier Actuation** | `POST /api/v1/anpr/barrier/toggle` | `barrier_control` | **ALLOWED** | **ALLOWED** | **ALLOWED** | Checkpoint operators must raise/lower gates during vehicle inspections. |
| **QRT Scramble/Dispatch** | `POST /api/v1/qrt/dispatch` | `qrt_dispatch` | **FORBIDDEN (403)** | **ALLOWED** | **ALLOWED** | Deploying armed kinetic interceptor teams requires Command authorization. |
| **QRT Status Update** | `POST /api/v1/qrt/status` | `qrt_status` | **ALLOWED** | **ALLOWED** | **ALLOWED** | Radio operators log tactical unit status transitions (e.g. EN_ROUTE, SECURED). |
| **QRT Radio Broadcast** | `POST /api/v1/qrt/radio/broadcast` | `qrt_broadcast` | **ALLOWED** | **ALLOWED** | **ALLOWED** | Outpost watchstanders broadcast tactical SITREPs over tactical VHF channels. |

---

## 8. STREAM-TICKET ACTUAL CONFIGURATION

* **Token Lifetime:** **300 seconds (5 minutes)**.
* **Token Type:** `type="stream_ticket"`.
* **Scope Format:** Resource-bound strings (e.g., `ws:live:1`, `ws:events`, `ws:analysis:10`).
* **Subsystem Enforcement:** Streaming tickets are strictly consumed by `_authenticate_websocket` in `backend/app/main.py`.
* **Rest API Immunity:** General REST endpoints strictly mandate `verify_type="access"`. Streaming tickets passed to REST endpoints (`GET /api/v1/auth/me`), Evidence Vault (`GET /api/v1/evidence/vault/...`), or MJPEG streams (`GET /api/v1/cameras/{id}/stream`) are rejected with `HTTP 401 Unauthorized`.
* **Cross-Resource Rejection:** A ticket scoped to `ws:live:1` is rejected on `/ws/live/2` (`Scope mismatch`).
* **Negative Test Proof:** Fully tested in `test_stream_ticket_negative_scenarios` across 8 distinct attack variations.

---

## 9. SSRF & DNS REBINDING ACTUAL BEHAVIOR

* **Connection Path:** In `test_stream` (`cameras.py`), the URL hostname is resolved via `socket.getaddrinfo`, verified against prohibited ranges (loopback, link-local, cloud metadata), and the URL's `netloc` is reconstructed with the pre-validated IP literal before calling `cv2.VideoCapture(url)`.
* **Socket Destination:** Because the URL contains an IP literal, initial socket establishment connects directly to the validated IP.
* **Residual Risk:** Downstream video capture libraries (OpenCV / FFmpeg) may follow higher-layer HTTP 30x redirects without re-invoking the application-layer security check. Therefore, "DNS rebinding eliminated" is NOT claimed. True elimination requires non-routable camera VLAN segmentation and outbound firewall egress filtering.

---

## 10. PASSWORD HASHING ACTUAL CONFIGURATION

* **Algorithm:** PBKDF2-HMAC-SHA256.
* **Work Factor:** **600,000 iterations** (`TARGET_PBKDF2_ITERATIONS`).
* **Salt:** 16 bytes cryptographically secure random (`os.urandom(16)`).
* **Format:** `pbkdf2$<iterations>$<salt_hex>$<hash_hex>`.
* **Migration:** Legacy 100,000-iteration hashes verify seamlessly; successful login triggers inline upgrade to 600,000 iterations in the database.
* **Standards Assessment:** Argon2id is OWASP's primary general recommendation; PBKDF2-HMAC-SHA256 at 600,000 iterations is selected here because it relies exclusively on standard library OpenSSL primitives with zero external C-extension dependencies, ensuring zero-dependency air-gapped edge deployability.

---

## 11. SECURITY STANDARDS WORDING

All reports strictly use the formulation:
```text
Security Reference / Control-Mapping Frameworks:
- OWASP Top 10 API Security (2023)
- CIS Benchmark Level 1
- NIST SP 800-53 Rev. 5
- ISO/IEC 27037:2012
```
No claims of "certified", "compliant", "conforming", or "approved" are made. Controls are classified as:
`IMPLEMENTED`, `TESTED`, `STATICALLY VERIFIED`, `INTEGRATION VERIFIED`, `ENVIRONMENT-DEPENDENT`, `RESIDUAL RISK`.

---

## 12. BSA / FORENSIC WORDING

All forensic evidence handling is documented strictly as:
```text
Implemented technical controls aligned with Bharatiya Sakshya Adhiniyam, 2023 Section 63 requirements.
```
No claims of "BSA certified", "court admissible", "legally admissible", or "judicially accepted" are permitted.

---

## 13. REMAINING RISKS

1. **Host-Level Memory Exfiltration:** Host root access allows memory dumping of decoded video frames.
2. **FFmpeg HTTP Redirection:** Protocol redirects followed by FFmpeg bypass URL IP pinning.
3. **Multi-Worker Lockout State:** In-memory brute-force counters require sticky reverse proxy sessions.
4. **Hardware Supply Chain:** Compromised IP camera firmware on the physical surveillance network.

---

## 14. DEPLOYMENT PREREQUISITES

1. Mandatory provisioning of cryptographically random `SECRET_KEY` (>= 32 chars).
2. CCTV camera surveillance network isolation on dedicated non-routable VLAN.
3. Reverse proxy terminating TLS 1.3 with strict cipher suites.
4. Hardened PostgreSQL 15+ database instance with encrypted connections.

---

## 15. INDEPENDENT VAPT REQUIREMENT

Prior to operational deployment in any live defense or paramilitary facility, the platform must undergo formal, independent Vulnerability Assessment and Penetration Testing (VAPT) conducted by an accredited third-party auditing agency (e.g., CERT-In empanelled auditor).

---

### FINAL CONCLUSION:
```text
CURRENT CODE = CURRENT TESTS = GATE 2 RECORD = GATE 3 RECORD
```
**GATE 3 CROSS-GATE RECONCILIATION — PASSED**
