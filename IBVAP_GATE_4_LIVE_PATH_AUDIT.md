# IBVAP GATE 4 — LIVE PIPELINE REALITY AUDIT REPORT

**Document ID:** `IBVAP-GATE-4-AUDIT-001`  
**Execution Phase:** Phase 4.1 Live Pipeline Reality Audit  
**Date:** 2026-09-21  
**Baseline Test Verification:** 706 passed, 0 failed, 0 skipped, 0 errors (73.48s)  
**Security Baseline:** Gates 1A, 2, 3, and 3.1 Closed & Reconciled  

---

## 1. EXECUTIVE SUMMARY & VERIFICATION SCOPE

As part of **IBVAP Gate 4 (Autonomous Mission Capability + End-to-End Platform Validation)**, this audit rigorously inspects every link in the live execution pipeline from raw camera ingestion down to operator user interface presentation.

The primary mandate is to verify that IBVAP is an authentic edge-native intelligent border surveillance platform operating over existing surveillance infrastructure, without simulated, mocked, or bypassed components in the live operational chain.

```
+------------------+     +-------------------+     +--------------------+
|  CCTV / Camera   | --> | Dedicated Reader  | --> | Latest-Frame Slot  |
| (RTSP/USB/File)  |     | (Single Owner)    |     | (Low-Contention)   |
+------------------+     +-------------------+     +--------------------+
                                                             |
                                                             v
+------------------+     +-------------------+     +--------------------+
| Night Enhance    | <-- | Dedicated Proc    | <-- | Frame Packet       |
| (CLAHE/Zero-DCE) |     | Worker Thread     |     | (Monotonic Epoch)  |
+------------------+     +-------------------+     +--------------------+
         |
         v
+------------------+     +-------------------+     +--------------------+
| YOLO26n ONNX/pt  | --> | ByteTrack Multi-  | --> | ZoneFence Polygon  |
| Object Detection |     | Object Tracker    |     | Spatial Geometry   |
+------------------+     +-------------------+     +--------------------+
                                                             |
                                                             v
+------------------+     +-------------------+     +--------------------+
| Threat Scoring   | <-- | Evidence Engine   | <-- | Kinematic Behavior |
| (Deterministic)  |     | (YuNet + Plate)   |     | (Speed, Dwell, Dir)|
+------------------+     +-------------------+     +--------------------+
         |
         v
+------------------+     +-------------------+     +--------------------+
| Debounce & NVR   | --> | SHA-256 Merkle    | --> | Atomic DB Commit   |
| Rolling Buffer   |     | Evidence Vault    |     | (Incident+Outbox)  |
+------------------+     +-------------------+     +--------------------+
                                                             |
                                                             v
+------------------+     +-------------------+     +--------------------+
| React Frontend   | <-- | FastAPI WebSockets| <-- | Outbox Dispatcher  |
| Live Dashboard   |     | (Token-bound)     |     | Background Worker  |
+------------------+     +-------------------+     +--------------------+
```

---

## 2. DETAILED 16-STAGE LIVE PATH TRACE

### Stage 1: Video Ingestion
- **Module:** `backend/app/services/live_pipeline.py` (`CameraPipeline._reader_worker`)
- **Implementation:** Dedicated background reader thread owning exclusive control of `cv2.VideoCapture` (`self._cap`). Reads RTSP feeds with TCP interleaved transport (`OPENCV_FFMPEG_CAPTURE_OPTIONS="rtsp_transport;tcp|stimeout;3000000"`), USB devices, local files, or phone streaming links.
- **Resilience:** Implements a 6-state Reconnect State Machine (`CONNECTED`, `READ_FAILURE`, `BACKOFF`, `RECONNECTING`, `RECOVERED`, `STOPPED`) with bounded exponential backoff (0.5s to 30.0s) and stream epoch increments.
- **Audit Verdict:** **PASS (REAL)**. Verified in `test_p0_01_step03_reader_worker.py` and `test_p0_01_step04_reconnect.py`.

### Stage 2: Ingestion Decoupling (Latest-Frame Slot)
- **Module:** `backend/app/services/live_pipeline.py` (`publish_frame_packet`, `get_latest_frame_packet`)
- **Implementation:** Thread-safe slot protected by `self._slot_lock`. Reader thread immediately deposits `FramePacket` with monotonic `frame_id`, UTC timestamp, monotonic perf counter, and rolling source FPS.
- **Contention Invariant:** Reader thread never blocks on AI inference. If the processor is slower than the camera, intermediate frames are superseded and tracked in `processor_frames_skipped_latest_slot`.
- **Audit Verdict:** **PASS (REAL)**. Verified in `test_p0_01_step02_slot_contract.py`.

### Stage 3: Processor Worker Thread
- **Module:** `backend/app/services/live_pipeline.py` (`_processor_worker`)
- **Implementation:** Dedicated inference thread operating on state machine (`START`, `WAITING_FOR_FRAME`, `PROCESSING`, `STOPPING`, `STOPPED`). Consumes exclusively from the latest-frame slot. Never touches `self._cap`.
- **Audit Verdict:** **PASS (REAL)**. Verified in `test_p0_01_step05_processor_worker.py`.

### Stage 4: Night / Low-Light Enhancement
- **Module:** `edge/modules/night_enhance.py` (`NightEnhancer`)
- **Implementation:** Analyzes frame luminance. If mean illumination drops below 45 lux equivalent, applies CLAHE (Contrast Limited Adaptive Histogram Equalization) or Zero-DCE curve estimation. Detects illuminated frame and passes enhanced tensor to detector.
- **Audit Verdict:** **PASS (REAL)**. Real OpenCV/NumPy matrix transformations; verified in `test_anpr_night_face.py`.

### Stage 5: AI Object Detection
- **Module:** `edge/detection/factory.py`, `edge/detection/yolo26.py`, `edge/detection/yolo11.py`
- **Implementation:** Real ONNX Runtime and PyTorch inference using genuine neural weights (`models/yolo26n.onnx`, `yolo26n.pt`, `yolo11n.onnx`). Runs at 40.9 mAP, detecting persons, vehicles (car, truck, bus, motorcycle), and carry items. Fallback to background subtraction motion detector only if models missing.
- **Audit Verdict:** **PASS (REAL)**. Verified with binary model provenance audit (Gate 3.1).

### Stage 6: Multi-Object Tracking
- **Module:** `edge/tracking/bytetrack.py` (`ByteTracker`), `live_pipeline.py` (`_track_with_bytetrack`)
- **Implementation:** Kalman filter state prediction + two-stage Hungarian association across high-confidence and low-confidence detection pools. Tracks maintain persistent ID, ground-footprint contact anchor point `[cx, y2]`, velocity vector, dwell time, and hit history.
- **Audit Verdict:** **PASS (REAL)**. Verified in `test_live_bytetrack_integration.py`.

### Stage 7: Virtual Fence & Geometric Zone Evaluation
- **Module:** `edge/zones/fence.py` (`ZoneFence`), `live_pipeline.py` (lines 1639–1721)
- **Implementation:** Ray-casting point-in-polygon algorithm evaluating ground-footprint contact anchor against configured convex/concave polygon zones and directional line tripwires. Generates discrete `zone_entry`, `zone_crossing`, `zone_exit`, and `direction_violation` events.
- **Audit Verdict:** **PASS (REAL)**. Verified in `test_live_zonefence_integration.py`.

### Stage 8: Kinematic Behavior Analysis
- **Module:** `edge/behavior/kinematics.py` (`KinematicBehaviorEngine`), `live_pipeline.py` (`_check_threat`)
- **Implementation:** Evaluates track trajectory history to compute Euclidean speed (m/s), acceleration, directional bearing deviation, and spatial dwell time. Detects rapid boundary approach, crawling/stalking, and perimeter loitering.
- **Audit Verdict:** **PASS (REAL)**. Verified in behavioral test suite.

### Stage 9: Evidence Association (Face & ANPR)
- **Module:** `backend/app/services/face.py`, `backend/app/services/anpr.py`, `edge/evidence/association.py` (`EvidenceAssociator`)
- **Implementation:**
  - Face: YuNet ONNX face detection + SFace 128D cosine distance embedding extractor against database watchlist.
  - ANPR: `plate_detect.onnx` license plate detection + OCR with multi-frame string consensus voting.
  - Association: Spatial bounding box containment + Kalman temporal tracking binds face and plate evidence to active ByteTrack tracks.
- **Audit Verdict:** **PASS (REAL)**. Verified in `test_anpr_night_face.py` and `test_step04_rps_phase2.py`.

### Stage 10: Deterministic Risk Priority Scoring (RPS)
- **Module:** `backend/app/services/scoring.py` (`compute_threat_score`)
- **Implementation:** Deterministic spatial-gated multiplicative scoring formula:
  $$\text{RPS} = G_{\text{spatial}} \times (B_{\text{base}} + \Delta_{\text{dir}} + \Delta_{\text{dwell}} + \Delta_{\text{env}}) \times T_{\text{persistence}} \times \Omega_{\text{operator}}$$
  - **Spatial Gate ($G_{\text{spatial}}$):** Restricts non-zero scores to monitored zones ($1.00$ RESTRICTED/SENSITIVE, $0.65$ BUFFER, $0.30$ MONITORED, $0.00$ PUBLIC). PUBLIC zone observations receive an RPS spatial gate of 0.0 and therefore contribute no RPS threat points.
  - **Breach Baseline ($B_{\text{base}}$):** $70.0$ for boundary crossing, $25.0$ for persistent presence.
  - **Kinematic & Environment ($\Delta$):** Inward approach ($+10.0$) vs outward retreat ($-15.0$), loiter dwell ($[0, 15.0]$), night context ($+5.0$), vehicle mobility ($+5.0$), anomaly ($[0, 15.0]$).
  - **Temporal Persistence Gate ($T_{\text{persistence}}$):** Multi-frame confirmed ($1.00$) vs single-frame jitter damping ($0.40$).
  - **Operator Feedback Gate ($\Omega_{\text{operator}}$):** $0.0$ if suppressed, overridden ($1.0$) upon restricted zone breach.
  No black-box or non-deterministic LLM hallucination in the threat score. Returns structured explainability facts and missing evidence audit.
- **Audit Verdict:** **PASS (REAL)**. Verified in `test_step04_rps_phase1.py` through `test_step04_rps_phase4.py` and `test_canonical_spatial_gated_multiplicative_formula` in `test_scoring.py`.

### Stage 11: Debouncing & Deduplication
- **Module:** `backend/app/services/live_pipeline.py` (`_check_threat`), `backend/app/models/suppression.py`
- **Implementation:** Track-level 60-second cooldown per target ID; dwell escalation gating (only alerts on loiter after 60s sustained dwell); query against active `IncidentSuppressionRule` database records before triggering.
- **Audit Verdict:** **PASS (REAL)**. Verified in `test_step04_rps_phase3.py`.

### Stage 12: NVR Preroll Ring Buffer & Evidence Sealing
- **Module:** `backend/app/services/live_pipeline.py` (`NVRPrerollBuffer`)
- **Implementation:** Low-latency rolling memory ring buffer storing encoded JPEG frames with timestamps. Upon threat trigger exceeding threshold, asynchronously compiles 5s preroll + 5s postroll into an MP4 video clip via OpenCV `VideoWriter`. Computes SHA-256 cryptographic digest of the generated file.
- **Audit Verdict:** **PASS (REAL)**. Verified in `test_p0_01_step06_nvr_ring.py`.

### Stage 13: Incident & Outbox Atomic Persistence
- **Module:** `backend/app/services/live_pipeline.py`, `backend/app/models/incident.py`, `backend/app/models/outbox.py`
- **Implementation:** Atomic database transaction commits `Incident`, `Evidence`, and `IncidentOutboxEvent`. If the primary database is partitioned or unreachable, payloads are spooled to append-only disk storage (`data/spool/offline_incidents.jsonl`).
- **Audit Verdict:** **PASS (REAL)**. Verified in `test_p0_01_step07_phase2_outbox.py` and `test_p0_01_step07_phase4_spool_replay.py`.

### Stage 14: Outbox Dispatcher
- **Module:** `backend/app/services/outbox_dispatcher.py` (`OutboxDispatcherWorker`)
- **Implementation:** Bounded background daemon polling pending outbox records, executing external HTTP webhooks / QRT dispatch with exponential backoff and dead-letter queues.
- **Audit Verdict:** **PASS (REAL)**. Verified in `test_p0_01_step07_phase3_dispatcher.py`.

### Stage 15: WebSocket Live Push
- **Module:** `backend/app/services/live_pipeline.py` (`_on_event`), `backend/app/api/v1/endpoints/events.py`
- **Implementation:** Dispatches incident alerts, zone crossings, and camera health diagnostics over authenticated WebSocket connections (`/api/v1/events/ws`) using streaming tickets.
- **Audit Verdict:** **PASS (REAL)**. Verified in `test_p0_02_phase2_4_ws_ssrf.py`.

### Stage 16: Frontend React Dashboard
- **Module:** `frontend/src/views/LiveMonitorView.tsx`, `frontend/src/components/WebSocketVideoCanvas.tsx`
- **Implementation:** Renders live MJPEG / WebSocket canvas streams, tactical bounding boxes, virtual zone polygons, threat telemetry, and audible alerts.
- **Audit Verdict:** **PASS (REAL)**.

---

## 3. SPECIFIC AUDIT QUESTIONS & DISCOVERED GAPS

| Audit Question | Code Evidence | Status | Remediation Required |
| :--- | :--- | :--- | :--- |
| **1. Is `_simple_track` still called anywhere?** | `live_pipeline.py:2185` marked `[DEPRECATED / BYPASSED]`. Tested in `test_live_bytetrack_integration.py` (`test_simple_track_bypassed_and_not_called`). | **RESOLVED / BYPASSED** | None. Preserved only as inactive code reference. |
| **2. Is `rule_engine` still called?** | `live_pipeline.py:1391` imports `edge.modules.activity_rules.get_rule_engine()`, passes it into `_process_frame` at line 1470 & 1610, but is never invoked inside `_process_frame`. | **DEAD ARGUMENT** | Clean up uncalled parameter or route rule evaluation explicitly through behavioral engine. |
| **3. Is `reid_engine` called?** | `live_pipeline.py:1382` instantiates `reid_engine`, passes to `_process_frame` line 1469 & 1610. Wired `reid_engine.extract_embedding()` at line 1795 to extract 512D embeddings into `_track_metadata` and `TrackletDescriptor`. | **WIRED & VERIFIED** | Validated via `test_live_reid_pipeline_integration_through_process_frame` asserting genuine 512D appearance vector extraction. |
| **4. Are thermal / drone UI elements backed by real sensors?** | `ThermalDroneView.tsx` uses CSS filter classes (`thermal-shader-ironbow`) over optical snapshots, with static hardcoded UAV telemetry (`ALT: 148m AGL`, `SPD: 44 km/h`, `LAT: 32.7266° N`). | **SIMULATION DEFECT** | Violates Gate 4 hard rule against deceptive fake thermal / drone telemetry. Must add prominent `[SIMULATION & PALETTE FILTER]` disclosure banner and honest labeling. |
| **5. Is PTZ backed by real camera control?** | `backend/app/api/v1/endpoints/cameras.py` mutates in-memory dictionary `_PTZ_STATE` and digitally crops synthetic frames. No physical camera / ONVIF command dispatch. | **MOCK DEFECT** | Violates Gate 4 hard rule against mock PTZ as real PTZ. Must implement unified `CameraAdapter` with genuine ONVIF Profile S capability discovery and honest fallback. |

---

## 4. GATE 4 REMEDIATION PLAN (PHASES 4.2 – 4.4)

1. **Re-ID Wiring (Phase 4.1 Fix):**
   Connect `reid_engine.extract_embedding()` to person track crops in `_process_frame` so `_track_metadata[target_id]["appearance_embedding"]` is populated with genuine 512D vectors for cross-camera handoffs.
2. **Camera Interoperability & ONVIF PTZ (Phase 4.2):**
   Implement `backend/app/services/camera_adapter.py` and `edge/adapters/`:
   - `CameraAdapter` base class with capabilities: `can_ptz`, `can_presets`, `stream_protocol`, `onvif_profile`.
   - `RTSPCameraAdapter`: Native RTSP digest/basic auth, reconnect management.
   - `ONVIFCameraAdapter`: Genuine Profile S/T/M probe, media URI discovery, continuous/absolute PTZ command dispatch with graceful digital fallback.
   - `SyntheticTestPatternAdapter`: Clear, honest synthetic test pattern generator for offline/CI use.
3. **Frontend Thermal & Drone View Rectification (Phase 4.1 Fix):**
   Update `ThermalDroneView.tsx` with unambiguous UI badges:
   - Header badge: `[POST-PROCESS COLORMAP VISUALIZATION — OPTICAL SENSOR]`
   - Telemetry badge: `[SIMULATED FLIGHT TELEMETRY]`
   - Eliminate misleading claims of real FLIR hardware connection.
4. **Authoritative Documentation:**
   Publish `IBVAP_GATE_4_CAMERA_INTEROPERABILITY_REPORT.md` upon completion of Phase 4.2.

---

## 5. TEST BASELINE AT CONCLUSION OF PHASE 4.1 AUDIT

```text
706 passed, 0 failed, 0 skipped, 0 errors in 73.48s
Step 01–07:                   222 passed
Gate 2 Remediation:           160 passed
Gate 3 Pre-Prod Assurance:     10 passed
Gate 3 Truth Reconciliation:   11 passed
Core, Edge, Vision, Platform: 303 passed
```
All existing security and architectural invariants remain intact.
