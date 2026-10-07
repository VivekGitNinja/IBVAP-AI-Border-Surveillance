# IBVAP — Authoritative Claims & Evidence Matrix
## Smart India Hackathon (SIH 2026) — Problem Statement SIH26187

---

## 1. Classification Methodology & Verification Standards

To guarantee strict scientific and engineering honesty, every capability claim in the IBVAP platform is classified into one of the following authoritative categories:

* **`IMPLEMENTED`**: Code and data structures are fully written and wired into production execution paths.
* **`TESTED`**: Verified via automated unit, integration, or adversarial test suites in CI/CD.
* **`CONTROLLED SCENARIO VERIFIED`**: Validated against defined, reproducible synthetic/simulated test scenarios.
* **`MEASURED`**: Quantitatively instrumented using independent monotonic wall-clock timing or profiling tools.
* **`MODEL-DOCUMENTED`**: Published specification from upstream model creators (e.g. Ultralytics COCO evaluation); not measured in field deployment.
* **`ENVIRONMENT-DEPENDENT`**: Subject to physical ambient variations (lighting, weather, camera angles, network quality).
* **`NOT FIELD VALIDATED`**: Has not undergone active deployment or formal trials on an international border.
* **`RESIDUAL RISK`**: Acknowledged operational or engineering constraint requiring human oversight or site calibration.

---

## 2. Comprehensive Claims & Evidence Verification Table

| # | System Area | Presentation Claim | Authoritative Classification | Empirical Evidence & Test Location | Operational Limitation & Boundary |
|---|---|---|---|---|---|
| 1 | **Object Detection** | YOLO26n achieves 40.9 mAP object detection accuracy. | `MODEL-DOCUMENTED` | Official Ultralytics COCO validation report; model file: `models/yolo26n.onnx`. | Published benchmark on standard COCO dataset. Field detection accuracy varies with camera mounting angle, lens focal length, distance, and environmental conditions. |
| 2 | **Multi-Object Tracking** | ByteTrack maintains target identity with 0 ID switches. | `CONTROLLED SCENARIO VERIFIED` | `backend/tests/test_ai_pipeline_validation.py` (`test_bytetrack_zero_id_switches_controlled`). | Verified on a controlled 35-frame linear pedestrian video sequence. Severe crowded crossings or prolonged full occlusion (> 15 frames) may cause tracklet re-indexing. |
| 3 | **Occlusion Handling** | Target tracks survive up to 15 frames of complete occlusion. | `CONTROLLED SCENARIO VERIFIED` | `backend/tests/test_ai_pipeline_validation.py` (`test_bytetrack_occlusion_survival`). | Kalman filter trajectory projection holds track state for exactly 15 frames in test sequence; targets stationary or changing direction while occluded may suffer ID loss. |
| 4 | **Perception Anchoring** | Zone containment evaluated using ground-footprint anchor $[c_x, y_2]$. | `IMPLEMENTED` & `TESTED` | `edge/zones/fence.py`, `backend/tests/test_live_zonefence_integration.py`. | Ground anchor assumes flat terrain or appropriate camera tilt. Extreme steep topological elevation changes require site-specific GIS calibration. |
| 5 | **Public Zone Gating** | PUBLIC zones contribute zero RPS threat points. | `IMPLEMENTED` & `TESTED` | `edge/scoring/engine.py`, `backend/tests/test_scoring.py` (`test_zero_signals_gives_low`). | Mathematical formula assigns $G_{\text{spatial}} = 0.0$ to public zones. Does not prevent visual clutter in raw stream; strictly prevents threat score escalation. |
| 6 | **Night Vision Enhancement** | Adaptive CLAHE improves detection contrast below 45 lux in 8.64 ms. | `MEASURED` & `TESTED` | `edge/modules/activity_rules.py`, `backend/tests/test_ai_pipeline_validation.py`. | Hardware-dependent CPU execution time. CLAHE improves local contrast but cannot recover details obscured by zero-light absolute blackouts or sensor thermal noise. |
| 7 | **End-to-End Latency** | Full night processing pipeline executes in 28.41 ms (~35.2 FPS). | `MEASURED` | Independent monotonic wall-clock timing (`time.perf_counter()`); `backend/tests/test_ai_pipeline_validation.py`. | Measured on $1280 \times 720$ video input on reference workstation. Multi-stream scaling depends on CPU core count, RAM bus bandwidth, and optional accelerator hardware. |
| 8 | **Daylight Latency** | Full daylight processing pipeline executes in 19.53 ms (~51.2 FPS). | `MEASURED` | Independent monotonic wall-clock timing (`time.perf_counter()`); `backend/tests/test_ai_pipeline_validation.py`. | CLAHE enhancement bypassed above 50 lux. Performance is environment-dependent based on resolution and stream bitrate. |
| 9 | **Multi-Camera Throughput** | Multi-stream pipeline processes 92.1 aggregate FPS across 4 cameras. | `MEASURED` & `ENVIRONMENT-DEPENDENT` | `backend/tests/test_ai_pipeline_validation.py` (`test_multi_camera_concurrent_throughput`). | Measured on reference hardware under simulated camera loads. Requires sufficient network switch bandwidth and decoding threads in physical deployment. |
| 10 | **ANPR Consensus** | Multi-frame license plate consensus scenario resolved correctly. | `CONTROLLED SCENARIO VERIFIED` | `backend/tests/test_gate4_end_to_end_scenarios.py` (`test_scenario_c_vehicle_checkpoint_anpr_consensus`). | Verified on the defined checkpoint test sequence. Plate readability requires proper vehicle illumination, plate angle $< 30^\circ$, and plate height $\ge 25$ pixels. |
| 11 | **Facial Matching** | Watchlist facial probe matched with cosine similarity 1.00. | `CONTROLLED SCENARIO VERIFIED` | `backend/tests/test_gate4_end_to_end_scenarios.py` (`test_scenario_d_watchlist_frs_match_adjudication`). | Verified on identical vector test fixtures. Real-world facial recognition requires adequate face resolution (> 60px between eyes), minimal pitch/yaw, and good lighting. |
| 12 | **ReID Live Integration** | 512D normalized appearance embedding extracted into tracklet metadata. | `IMPLEMENTED` & `TESTED` | `edge/tracking/bytetrack.py`, `backend/tests/test_ai_pipeline_validation.py`. | Appearance embeddings describe color/texture features. ReID across drastically different camera color profiles requires cross-camera color calibration. |
| 13 | **Cross-Camera Pursuit** | Spatial-temporal topology and ReID link intruder across two towers. | `CONTROLLED SCENARIO VERIFIED` | `edge/correlation/cross_camera.py`, `backend/tests/test_gate4_end_to_end_scenarios.py`. | Transit time window and physical distance must be calibrated during outpost commissioning. Out-of-order erratic movement outside topology bounds reduces correlation score. |
| 14 | **Offline Resilience** | Edge node spools incidents to JSONL during network outages and replays them. | `IMPLEMENTED` & `TESTED` | `backend/app/services/spool_replay.py`, `backend/tests/test_gate4_end_to_end_scenarios.py`. | Spool capacity is bounded by local SSD disk space. Long-term offline operation requires sufficient local storage allocation. |
| 15 | **BSA Technical Alignment** | Evidence integrity & BSA Section 63 technical alignment via SHA-256 Merkle chain. | `IMPLEMENTED` & `TESTED` | `backend/app/services/evidence.py`, `backend/tests/test_evidence_report.py`. | Implemented technical controls aligned with Bharatiya Sakshya Adhiniyam, 2023 Section 63 requirements. Per-case statutory certificate generation remains an operational/legal prerequisite. |
| 16 | **Camera Interoperability** | Vendor-neutral RTSP/ONVIF adapter architecture implemented. | `IMPLEMENTED` & `TESTED` | `edge/adapters/`, `backend/tests/test_camera_interoperability.py`. | Hardware interoperability requires device-specific validation. Non-standard proprietary camera firmware may require custom RTSP transport settings. |
| 17 | **Sensor Simulation** | Thermal and drone feeds render sensor simulation disclaimers. | `IMPLEMENTED` & `TESTED` | `frontend/src/views/ThermalDroneView.tsx`. | Sensor views display: `[SIMULATED TELEMETRY & POST-PROCESS COLORMAP]`. Does not replace physical radiometric thermal cores. |
| 18 | **Secret Hygiene** | Repository clean of unreviewed secrets; reviewed fixtures baselined. | `MEASURED` & `TESTED` | Yelp `detect-secrets` 1.5.0, `.secrets.baseline` (112 reviewed fixtures, 0 unreviewed, 0 true credentials). | Baseline must be maintained across future commits using pre-commit hooks. |
| 19 | **Operational Readiness** | 733 / 733 backend tests green, frontend builds without errors. | `TESTED` | Full regression run: `collected=733, passed=733, failed=0, errors=0, warnings=10`. | Laboratory and CI/CD test baseline. Live operational deployment requires on-site military commissioning. |
| 20 | **Human Decision Support** | AI assists human operators; no autonomous lethal actions. | `IMPLEMENTED` | Role hierarchy and confirmation modals in UI. | Final interdiction and lethal authority remains strictly under human commander control (*Human-In-The-Loop*). |

---

## 3. Claim Integrity Affirmation

The engineering team explicitly affirms:
1. No synthetic test result is represented as live border field validation.
2. No model benchmark is described as guaranteed real-world accuracy.
3. No camera manufacturer compatibility is claimed without empirical adapter test verification.
4. No simulated thermal sensor is presented as genuine physical radiometric hardware.
5. All performance timings are based strictly on independent monotonic wall-clock measurements.
