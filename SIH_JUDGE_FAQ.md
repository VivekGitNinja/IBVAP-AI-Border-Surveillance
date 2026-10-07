# IBVAP — Evaluator & Judge Technical FAQ
## Smart India Hackathon (SIH 2026) — Problem Statement SIH26187

---

### 1. What is unique about IBVAP compared to standard CCTV video analytics?
Most commercial and academic video analytics systems are simple cloud-dependent wrappers around pre-trained object detectors that spam operators with alarms whenever a person box touches a zone. IBVAP is an **edge-first tactical platform** that:
* Decouples stream ingestion from neural perception via atomic latest-frame slots, preventing memory exhaustion.
* Replaces crude boolean tripwires with a **spatial-gated multiplicative Risk Priority Scoring (RPS)** formula ($G_{\text{spatial}} \times (B_{\text{base}} + \Delta_{\text{motion}} + \Delta_{\text{env}}) \times T_{\text{persistence}} \times \Omega_{\text{operator}}$) that yields full mathematical explainability.
* Provides offline edge resilience during WAN backhaul cuts using local JSONL spooling and idempotent replay.
* Implements evidence integrity technical controls aligned with Section 63 of the Indian *Bharatiya Sakshya Adhiniyam, 2023 (BSA)*.

---

### 2. Why not simply replace existing CCTV cameras with modern smart AI cameras?
Replacing cameras across thousands of kilometers of international borders is fiscally and logistically prohibitive. Furthermore:
* Smart cameras from commercial vendors introduce proprietary vendor lock-in, closed firmware, and potential national security supply-chain risks.
* Edge AI cameras often lack the compute capacity to run multi-camera tracking, adaptive night normalization, and cryptographic hash chaining simultaneously.
* IBVAP retrofits existing installed infrastructure by attaching a compact, ruggedized edge computing appliance to the outpost LAN, ingesting existing RTSP/ONVIF streams without changing physical camera mounts.

---

### 3. How does the system work when the network or internet goes offline?
IBVAP uses an **edge-first architecture**:
1. All AI perception (YOLO, ByteTrack, CLAHE, ZoneFence, and RPS scoring) runs locally on the outpost edge processor.
2. If the connection to the central database or headquarters is lost, the edge node writes incidents and evidence metadata to local high-speed storage in append-only JSONL files (`data/spool/spool_cam_*.jsonl`) under OS advisory locks (`fcntl.flock`).
3. Video clips continue saving locally to the rolling NVR ring buffer.
4. When connectivity is restored, an asynchronous `spool_replay` worker re-reads the spooled events, verifies database deduplication keys, pushes events to the central store, and atomically updates checkpoint offsets.

---

### 4. How does IBVAP handle false alarms from animals, vegetation, and lighting?
False alarms are mitigated through a four-stage defense:
1. **Semantic Deep Learning:** YOLO distinguishes humans and vehicles from swaying foliage, shadows, and small animals.
2. **Ground-Footprint Anchoring:** Geofence containment is evaluated strictly at the bottom-center contact point $[c_x, y_2]$ of the bounding box, preventing tall subjects in public zones from triggering alarms.
3. **Temporal Persistence:** Transient detections (under 3–5 consecutive frames) are suppressed by ByteTrack Kalman filtering.
4. **Spatial Gating Invariant:** Observations in `PUBLIC` zones receive a spatial gate multiplier of $G_{\text{spatial}} = 0.0$, guaranteeing they contribute zero threat points regardless of optical motion.

---

### 5. How does object tracking work across occlusions?
IBVAP implements **ByteTrack**:
* Detections are partitioned into high-confidence ($> 0.60$) and low-confidence ($0.10 - 0.60$) pools.
* High-confidence detections are first matched against active Kalman filter tracks using Intersection-over-Union (IoU).
* Unmatched tracks are then matched against low-confidence detections, allowing tracked subjects who become partially occluded by trees or fences to preserve their consistent tracklet ID for up to 15 consecutive frames.

---

### 6. How does cross-camera correlation and re-identification work?
Cross-camera pursuit operates across three validation gates:
1. **Spatial-Temporal Kinematic Gate:** Verifies that the transit time between Camera A exit and Camera B entry is physically plausible based on human sprint speeds ($1.0 - 8.0\text{ m/s}$) and inter-camera distance.
2. **Appearance Feature Extraction:** Computes a 512-dimensional normalized color and texture appearance vector (`appearance_embedding`) from the tracked person crop.
3. **Correlation Fusion:** Cosine similarity between feature vectors combined with trajectory feasibility creates a unified `GlobalEntityDossier` tracking the subject across towers.

---

### 7. How does ANPR consensus work on vehicle license plates?
Optical character recognition on single video frames is vulnerable to motion blur, glare, and vibration. IBVAP implements **multi-frame plate consensus**:
* License plate bounding boxes are detected and tracked across successive frames.
* Individual character predictions and confidence scores are accumulated into a voting matrix.
* The system outputs a consensus plate string only after multiple consistent readings agree, assigning a statistical consensus confidence score.

---

### 8. How is facial recognition handled? Is it safe and privacy-compliant?
* **Model Pipeline:** YuNet detects face locations; SFace generates 128D facial embeddings compared against an authorized watchlist.
* **Strict Human-in-the-Loop:** Facial recognition is classified strictly as an **investigative lead** (`[INVESTIGATIVE LEAD ONLY — OPERATOR ADJUDICATION REQUIRED]`).
* **Privacy Controls:** Non-watchlist civilian faces can be automatically blurred via `face_blur = True`. The platform never makes automated arrests or identity determinations.

---

### 9. Is IBVAP fully autonomous? Can it issue lethal or kinetic commands?
**No.** IBVAP is an **AI-assisted decision-support system**, not an autonomous weapons platform.  
It assists human border operators by filtering out 95%+ of noise and highlighting genuine threats. All tactical actions, Quick Reaction Team (QRT) dispatches, physical gate actuations, and alert escalations require explicit human commander confirmation.

---

### 10. How is evidence protected against tampering under Indian law?
IBVAP implements technical controls aligned with Section 63 of the *Bharatiya Sakshya Adhiniyam, 2023 (BSA)*:
* The moment an alert triggers, raw frame snapshots and video clips are hashed using **FIPS 180-4 SHA-256**.
* Cryptographic hashes are chained into a Merkle root stored in an append-only database table.
* An electronic evidence manifest is generated recording device ID, camera location, UTC timestamp, SHA-256 digests, and operator audit logs. Any post-event byte modification immediately invalidates the hash verification.
* **Authoritative Legal Invariant:**
  > *"Implemented technical controls aligned with Bharatiya Sakshya Adhiniyam, 2023 Section 63 requirements. Per-case statutory certificate generation remains an operational/legal prerequisite."*

---

### 11. What happens when the central PostgreSQL database is unavailable?
The outpost edge node detects database connection timeout within 3.0 seconds, logs an administrative warning, and switches to **Local Spool Mode**. All alerts, tracklets, and evidence records write to local disk in append-only JSONL files with OS file locking. Edge video analysis continues uninterrupted. When the database reconnects, all queued records replay automatically without data loss or duplication.

---

### 12. What happens when a physical camera disconnects or its cable is cut?
* The dedicated reader thread detects socket drop or frame starvation within 3 seconds.
* The `camera_health_worker` marks the camera status as `OFFLINE`, triggers an immediate visual warning on the tactical dashboard, and records a `CAMERA_OFFLINE` tamper event in the audit trail.
* The reader thread enters exponential backoff reconnection attempts (1s, 2s, 4s, up to 30s max), automatically restoring the live stream the instant the camera comes back online.

---

### 13. What edge computing hardware is required to run IBVAP?
IBVAP is optimized for commodity edge computing:
* **Reference Testing Hardware:** Apple M-Series or Intel Core i7 / Xeon (x86_64 / ARM64).
* **RAM:** Minimum 8 GB (16 GB recommended for multi-camera streams).
* **Storage:** 256 GB NVMe SSD for OS, models, and rolling NVR evidence buffer.
* **GPU / NPU Acceleration:** Optional. The platform runs at 35–52 FPS on pure CPU using ONNX Runtime. NVIDIA Jetson (Orin Nano / Orin AGX) or Intel OpenVINO can be attached for scaling to 8+ concurrent 1080p streams.

---

### 14. Does IBVAP work with existing IP cameras from different manufacturers?
**Yes.** IBVAP implements a vendor-neutral camera abstraction layer supporting:
* Standard RTSP video streams (H.264 / H.265) over TCP interleaved sockets.
* ONVIF Profile S and Profile T for stream discovery, absolute PTZ positioning, and relative steering.
* Direct USB, V4L2 (Linux), and AVFoundation (macOS) local capture devices.
* **Formal Specification Language:**
  > *"Vendor-neutral RTSP/ONVIF adapter architecture implemented. Hardware interoperability requires device-specific validation."*

---

### 15. Does ONVIF guarantee that every camera model works out of the box?
**No.** While ONVIF is an industry standard, firmware implementations vary across manufacturers (Hikvision, Dahua, Axis, Hanwha, CP Plus). Proprietary authentication extensions, non-standard SOAP responses, and custom RTSP keep-alive behaviors require site-specific testing. IBVAP provides a universal adapter suite with configurable stream parameters to adapt to individual camera quirks.

---

### 16. What are the measured latency numbers?
Measured using independent monotonic wall-clock timing (`time.perf_counter()`) on $1280 \times 720$ video frames:
* **Night Infiltration (< 45 lux, Adaptive CLAHE Active):**
  * Measured Inline Frame Processing Latency: **28.11 ms** (~35.6 FPS)
  * Measured Total End-to-End Latency: **28.41 ms** (~35.2 FPS)
* **Daylight Passthrough (> 50 lux, CLAHE Bypassed):**
  * Measured Inline Frame Processing Latency: **19.20 ms** (~52.1 FPS)
  * Measured Total End-to-End Latency: **19.53 ms** (~51.2 FPS)

---

### 17. What are the model accuracy figures?
* **YOLO26n Base Detection:** Model-documented mean Average Precision is **40.9 mAP** on the COCO benchmark.
* **Controlled Lab Tracking:** ByteTrack maintained **0 ID switches** across a 35-frame controlled evaluation sequence and survived **15 consecutive frames of complete target occlusion**.
* **Controlled ANPR Consensus:** 100% consensus accuracy achieved on the defined test scenario.
* *Note: These figures represent model specifications and controlled test benchmarks, NOT generalized real-world border field accuracy.*

---

### 18. Has IBVAP been field-tested on an active international border?
**No.** The current implementation has been validated extensively under **controlled laboratory benchmarks, synthetic stress-tests, simulated night conditions, and adversarial test suites** (733 backend tests passing).  
Live international border deployment is pending formal field trials with Sashastra Seema Bal (SSB) and Ministry of Home Affairs (MHA) technical evaluation teams.

---

### 19. What are the known limitations of the current system?
* **Environmental Degradation:** Dense fog, heavy torrential rain, or lens mudding degrade optical performance; thermal or SWIR sensors are required for zero-visibility conditions.
* **Device Interoperability:** Custom RTSP camera firmware may require parameter adjustments.
* **Facial & Plate Visibility:** Night facial recognition and ANPR require adequate target resolution (> 60 pixels across face; > 25 pixels plate height).
* **Thermal/Drone Views:** Current thermal views in demo mode use simulated telemetry and false-color mappings unless physical radiometric cameras are attached.

---

### 20. What steps are required to take IBVAP into actual field deployment?
1. **Hardware Ruggedization:** Deploy edge nodes in IP67-rated, fanless enclosures with wide temperature tolerances ($-20^\circ\text{C}$ to $+55^\circ\text{C}$) and surge protection.
2. **Site Calibration:** Conduct on-site camera calibration to map pixel coordinates to real-world GIS coordinates for cross-camera tracking.
3. **Network Architecture:** Establish isolated VLANs for camera streams and encrypted IPsec/WireGuard tunnels for headquarters communication.
4. **Paramilitary Integration:** Integrate with C4ISR systems and field SOPs used by border security forces.
