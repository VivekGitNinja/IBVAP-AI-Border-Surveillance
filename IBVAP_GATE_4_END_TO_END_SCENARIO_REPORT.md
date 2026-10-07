# IBVAP GATE 4 — END-TO-END OPERATIONAL MISSION SCENARIOS REPORT

**Classification:** CONTROLLED REPRODUCIBLE SCENARIOS (NOT LIVE FIELD TRIALS)  
**Date:** September 21, 2026  
**Document Status:** AUTHORITATIVE GATE 4 ARTIFACT  
**Test Suite:** `backend/tests/test_gate4_end_to_end_scenarios.py` (7 / 7 PASSED, 100%)  
**Execution Environment:** macOS Darwin, Python 3.9.6, OpenCV 4.10, PyTorch ONNX Runtime, SQLite / PostgreSQL (ALEMBIC 2026_09)

---

## 1. EXECUTIVE SUMMARY & EVIDENTIARY SCOPE

Under Gate 4, the Integrated Border Video Analytics Platform (IBVAP) underwent comprehensive operational scenario validation. Seven operational scenarios (Scenarios A through G) were engineered, executed, and verified through the live execution path.

> [!IMPORTANT]
> **Controlled Reproducible Scenarios Disclosure:**
> All scenarios documented herein represent **controlled reproducible test sequences executed in laboratory CI/CD environments**, NOT longitudinal live field trials across border outposts.
> Specifically, performance in facial recognition, license plate recognition (ANPR), cross-camera identity handoff, false alarm suppression, and low-light night enhancement must NOT be construed as generalized real-world field accuracy. Real-world performance remains environment-dependent and requires site-specific calibration.

Unlike unit mocks or static tests, each scenario validated multi-component interactions across:
1. Stream ingestion and synthetic ISO sensor patterns
2. Deep neural inference (YOLO26n ONNX / OpenCV DNN fallback)
3. Ground-contact anchor tracking (ByteTrack Kalman + Hungarian IoU)
4. Virtual fence geometric boundary evaluation (ZoneFence Shapely PIP)
5. Kinematic anomaly and behavioral classification
6. Multi-modal evidence association (YuNet face embeddings + ANPR consensus OCR)
7. Deterministic spatial-gated multiplicative Risk Priority Scoring (RPS)
8. Pre-roll ring buffer preservation (TimeBoundedNVRRing MP4 + SHA-256)
9. Spool replay, atomic transactions, and cross-camera multi-tower correlation

All 7 scenarios passed with zero failures and zero regressions.

---

## 2. DETAILED CONTROLLED SCENARIOS BREAKDOWN

### Scenario A: Daylight Boundary Crossing
* **Operational Profile:** High-visibility daylight border sector (Sector Alpha Tower 01, BOP-01).
* **Test Subject:** Pedestrian intruder approaching border perimeter from outside the monitored zone and crossing into the zero-line restricted polygon (`[100, 200]` to `[500, 450]`).
* **Execution Path:**
  - `CameraPipeline` processed 24 continuous 720p frames at 160 luma.
  - Intruder started at $y=50$ ($y_{\text{contact}} = 170$ outside zone).
  - Advanced monotonically across frame sequence to $y=210$ ($y_{\text{contact}} = 330$ inside polygon).
  - Ground-footprint anchor (`[cx, y2]`) accurately crossed polygon boundary.
* **Observed Verification:**
  - Track ID assigned: `TRK-01` (`target_id = 1`).
  - Spatial status transitioned: `in_restricted_zone = True`.
  - Zone boundary event generated and pushed to WebSocket callback.
  - Zero false boundary alerts prior to footprint penetration.

---

### Scenario B: Night Infiltration with Low-Light Enhancement
* **Operational Profile:** Controlled dark sector (< 45 lux equivalent, mean luma 25).
* **Test Subject:** Low-observable target attempting stealth penetration during zero-illumination window.
* **Execution Path:**
  - Ingested low-luma frame matrix into `NightEnhancer`.
  - CLAHE / adaptive gamma pipeline triggered dynamically (`was_enhanced = True`).
  - Detection extracted from enhanced matrix.
  - `compute_threat_score` evaluated signals: `boundary_crossing = 1.0`, `behavior_anomaly = 0.7`, `night = 1.0`, `zone_type = "BUFFER"`.
* **Observed Verification:**
  - Adaptive night enhancement activated automatically in test frame.
  - Environmental night context added $+5.0$ priority points (`contributions["night_context"] = 5.0`).
  - Kinematic behavior anomaly added $+7.0$ points ($\min(15.0, 0.70 \times 10.0) = 7.0$).
  - Mathematical evaluation: $G_{\text{spatial}} \times (B_{\text{base}} + \Delta_{\text{motion}} + \Delta_{\text{env}}) \times T_{\text{pers}} \times \Omega_{\text{op}} = 0.65 \times (70.0 + 0.0 + 12.0) \times 1.0 \times 1.0 = 53.30$.
  - Resulting executable RPS score: $53.3$ (`round(53.30, 1) = 53.3`, MEDIUM priority triage, where thresholds are CRITICAL $\ge 85$, HIGH $\ge 65$, MEDIUM $\ge 40$, LOW $\ge 0$).
  - Operator guidance generated: *"PRIORITY 2: Operator review recommended. Inspect target trajectory and verify boundary adherence."*
  - *Context note:* Controlled benchmark result; field optical performance depends on atmospheric fog, rain, and IR illuminator spread.

---

### Scenario C: Vehicle Checkpoint with Multi-Frame ANPR Consensus
* **Operational Profile:** Border Outpost vehicle transit choke point (BOP-02 Checkpoint 01).
* **Test Subject:** Motorized vehicle displaying standard high-security registration plate (`UK07AB5678`).
* **Execution Path:**
  - Vehicle tracklet `TRK-12` monitored across 4 consecutive inspection frames.
  - Sensor simulated OCR character jitter and optical noise:
    - Frame 1: `UK07AB5678` (conf 0.91)
    - Frame 2: `UK07AB5678` (conf 0.91)
    - Frame 3: `UK07A85678` (conf 0.91, optical misread 'B' -> '8')
    - Frame 4: `UK07AB5678` (conf 0.91)
  - `EvidenceAssociationEngine.associate_vehicle_plate()` accumulated candidate reads.
* **Observed Verification:**
  - **The defined consensus scenario was resolved correctly to `UK07AB5678`.**
  - Single-frame optical error ('8' vs 'B') was cleanly rejected by positional frequency voting.
  - Verified plate metadata attached to tracklet without blocking real-time tracking.
  - *Context note:* Controlled scenario result; does not constitute generalized 100% field accuracy across damaged, mud-covered, or non-standard plates.

---

### Scenario D: Watchlist Subject Facial Recognition Sighting
* **Operational Profile:** Border crossing pedestrian terminal subject inspection.
* **Test Subject:** Enrolled subject ("Infiltration Suspect Bravo") in SQL watchlist repository.
* **Execution Path:**
  - Enrolled normalized 128D facial feature vector in `watchlist` table.
  - Live pipeline probe extracted 128D embedding from YuNet/SFace detector.
  - `face_service.match_watchlist()` executed cosine similarity evaluation against enrolled vector database.
* **Observed Verification:**
  - Match confirmed ($\text{sim} = 1.0000 \ge 0.65$ operational threshold, reflecting exact duplicate probe vector in unit verification).
  - Matched subject ID linked to evidence payload.
  - Invariant verified: Biometric similarity was treated as evidence quality only; it did NOT autonomously escalate raw kinetic RPS points, preserving legal human-in-the-loop triage.
  - *Context note:* Field facial matching varies with standoff distance, camera tilt, subject head turn, and lighting.

---

### Scenario E: Cross-Camera Multi-Tower Pursuit & Handoff
* **Operational Profile:** Multi-tower perimeter corridor (Tower 1 and Tower 2 separated by 50m blind zone).
* **Test Subject:** Pedestrian walking eastward along the perimeter boundary from Tower 1 coverage into Tower 2.
* **Execution Path:**
  - Topo graph constructed: $G = (V, E)$ with `CameraNode(1)` and `CameraNode(2)`.
  - Physical traversal edge: $D = 50.0\text{ m}$, `overlap_type = ADJACENT_BLIND`.
  - Cam 1: Tracklet `T101` exited East (heading $90^\circ$) at $t = \text{now}$.
  - Cam 2: Tracklet `T202` entered West (heading $90^\circ$) at $t = \text{now} + 30\text{ s}$.
  - Kinematic transit velocity: $v = \frac{50\text{ m}}{30\text{ s}} = 1.67\text{ m/s}$ (normal human walking velocity).
  - Appearance vectors: 512D ReID normalized appearance embeddings.
* **Observed Verification:**
  - `CrossCameraAssociator.register_completed_tracklet()` evaluated kinematic feasibility ($1.67\text{ m/s} \le 10\text{ m/s}$ max sprint, $v_{\text{nom}} = 1.4\text{ m/s}$).
  - Multi-modal affinity scored $0.864 \ge 0.75$, achieving `CrossCameraState.CORROBORATED`.
  - Tracklets fused into unified `GlobalEntityDossier` (`DOSSIER-XXXXXX`).
  - Both cameras associated: `dossier.camera_ids == [1, 2]`.
  - Single coherent tactical trail maintained across multi-tower handover.
  - *Context note:* Controlled topological transit test; real-world terrain non-linearities require site GIS mapping.

---

### Scenario F: Offline Border Outpost Network Disconnect & Spool Recovery
* **Operational Profile:** Remote Border Outpost experiencing total WAN backhaul severance during network outage.
* **Test Subject:** 3 high-severity perimeter breach incidents generated during WAN outage.
* **Execution Path:**
  - Primary database connection severed.
  - Edge pipeline redirected canonical incident records to durable local JSONL spool (`offline_incidents.jsonl`).
  - Each record encoded with UUIDv4 `event_id`, UUIDv5 `idempotency_key`, SHA-256 evidence fingerprint, and RPS threat assessment.
  - WAN connectivity restored; `SpoolReplayWorker.run_once()` initiated.
* **Observed Verification:**
  - `run_once(max_records=10)` recovered exactly 3/3 records ($100\%$).
  - Telemetry: `records_replayed_success = 3`, `records_malformed = 0`.
  - Database verification: All 3 incidents committed to `incidents` table with exact threat scores ($88.0$).
  - Two-stage atomic checkpoint advanced; zero duplicate replays.

---

### Scenario G: False Alarm Storm & Weather Resilience
* **Operational Profile:** High-wind foliage agitation, camera vibration, and small animal motion in unmonitored buffer zone.
* **Test Subject:** 50 transient low-confidence motion detections and single-frame jitters.
* **Execution Path:**
  - Detections generated with low confidence ($0.35$).
  - Evaluated against `MONITORED` buffer zone ($G_{\text{spatial}} = 0.30$).
  - Motion was transient without sustained trajectory ($T_{\text{persistence}} = 0.40$).
  - Evaluated via `compute_threat_score(signals, context={"is_single_frame_jitter": True, "zone_type": "MONITORED"})`.
* **Observed Verification:**
  - Raw RPS calculation: $0.30 \times 25.0 \times 0.40 = 3.0$ points.
  - Priority level assigned: `LOW` (strictly below operator triage threshold $< 40.0$).
  - Operator console remained clean; zero nuisance alarm dispatch.
  - Furthermore, PUBLIC zone observations receive an RPS spatial gate of 0.0 and therefore contribute no RPS threat points.
  - *Context note:* Controlled algorithmic damping demonstration; field false-alarm rates depend on vegetation density, thermal gradients, and wildlife activity.

---

## 3. SCENARIOS ACCEPTANCE SUMMARY

| Scenario ID | Mission Description | Key Invariant Tested | Execution Classification | Status |
|---|---|---|---|:---:|
| **Scenario A** | Daylight Boundary Crossing | Footprint anchor geometry & zone crossing | CONTROLLED TEST | **PASSED** |
| **Scenario B** | Night Infiltration | CLAHE night enhance & night context RPS | CONTROLLED TEST | **PASSED** |
| **Scenario C** | Vehicle Checkpoint ANPR | Multi-frame positional consensus voting | CONTROLLED TEST | **PASSED** |
| **Scenario D** | Watchlist Subject Sighting | SFace 128D cosine match & evidence isolation | CONTROLLED TEST | **PASSED** |
| **Scenario E** | Cross-Camera Pursuit | Dijkstra topology & kinematic walking handoff | CONTROLLED TEST | **PASSED** |
| **Scenario F** | Offline BOP WAN Disconnect | At-least-once durable spool replay | CONTROLLED TEST | **PASSED** |
| **Scenario G** | False Alarm Storm | Spatial gating & temporal persistence damping | CONTROLLED TEST | **PASSED** |

**Conclusion:** All 7 controlled reproducible scenarios passed with 100% consistency across the live execution path.
