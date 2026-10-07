# AI Border Surveillance & Perimeter Defense Platform (IBVAP)
## Smart India Hackathon (SIH) 2026 — Problem Statement SIH26187
### Sashastra Seema Bal (SSB) / Ministry of Home Affairs (MHA)

---

## 1. Executive Mission & Problem Statement

**Problem Statement SIH26187:**  
*Development of an AI-based Intelligent Video Analytics Platform for Border Surveillance utilizing existing installed CCTV infrastructure across remote Border Outposts (BOPs).*

Securing international land borders (such as the Indo-Nepal and Indo-Bhutan frontiers under Sashastra Seema Bal jurisdiction) poses severe operational challenges:
1. **Infrastructure Heterogeneity:** Outposts operate legacy, heterogeneous fixed and PTZ cameras from diverse manufacturers without unified modern analytics. Replacing existing cameras is fiscally prohibitive and operationally unfeasible across thousands of border kilometers.
2. **Extreme Environmental Conditions:** Dense riverine fog, low-light night conditions (< 45 lux), blinding dust storms, and monsoon foliage generate severe optical degradation.
3. **Operator Fatigue & False Alarm Storms:** Traditional pixel-motion alarms trigger hundreds of spurious alerts daily on swaying vegetation, shifting cloud shadows, and stray livestock, inducing operator desensitization.
4. **Intermittent Connectivity:** Border Outposts frequently suffer backhaul fiber cuts or satellite link degradation. Video streams must never drop, and surveillance evidence must never be lost during communication outages.
5. **Evidentiary Integrity:** Ad-hoc video clips lack cryptographic integrity and fail to satisfy statutory standards for electronic evidence under Indian legal frameworks.

**IBVAP Solution:**  
IBVAP is an **edge-first, AI-assisted video analytics and tactical decision-support platform** engineered specifically to retrofit existing CCTV networks. It ingests legacy RTSP/ONVIF feeds, performs hardware-efficient local neural perception and tracking, filters benign activity via multi-signal Risk Priority Scoring (RPS), spools incidents locally with guaranteed offline survivability, and implements evidence integrity & BSA Section 63 technical alignment with cryptographic SHA-256 Merkle chaining.

> [!IMPORTANT]
> **Operational Purpose Disclaimer:**  
> IBVAP is an **AI-assisted decision-support platform** designed to augment human border operators by ranking visual priorities. It does **NOT** perform autonomous lethal actions, autonomous weaponized actuation, or automated legal determinations of guilt. All operational responses, dispatch orders, and interdictions remain strictly under human commander control (*Human-In-The-Loop*).

---

## 2. Platform Architecture Overview

IBVAP decouples real-time stream ingestion and inference at the outpost edge from centralized command-center aggregation:

```text
Existing Outpost Cameras (RTSP / ONVIF Profile S/T / USB / File / Synthetic)
  │
  ▼
[Edge Ingestion Adapter] ──(Latest-Frame Slot Decoupling)──► [Edge Ring Buffer]
  │
  ├──► [Adaptive CLAHE Normalization] (< 45 lux Night Enhancement)
  │
  ├──► [YOLO26n / YOLO11n ONNX Inference] (COCO 80-Class Object Detection)
  │
  ├──► [ByteTrack Multi-Object Tracker] (Kalman Filter + Ground-Footprint [cx, y2] Anchor)
  │
  ├──► [ZoneFence Geospatial Engine] (Ray-Casting Polygon Geofencing: Public / Buffer / Restricted)
  │
  ├──► [Kinematic & Behavioral Analysis] (Speed, Direction, Dwell Time, Loitering, Gathering)
  │
  ├──► [ANPR / Face Evidence Extraction] (Plate Voting Consensus & SFace Feature Extraction)
  │
  ├──► [Cross-Camera Spatial-Temporal Correlation] (ReID 512D Descriptor Topology Handoff)
  │
  ├──► [Risk Priority Scoring (RPS) Engine] (Canonical Spatial-Gated Multiplicative Triage)
  │
  ├──► [Forensic Evidence Sealing] (SHA-256 Merkle Chain + BSA 2023 Section 63 Manifest)
  │
  ├──► [Offline Spooler & Replay Worker] (JSONL + Advisory OS Lock + Idempotent Ingestion)
  │
  ▼
[Central FastAPI C2 Backend] ──(Transactional Outbox & Relational Storage)
  │
  ├──► [Role-Based Access Control (RBAC)] (5 Tier Fail-Closed Security Hierarchy)
  ├──► [Asynchronous WebSocket Broadcast] (Real-time Canvas Video & Event Streaming)
  └──► [React 18 / Vite Tactical Dashboard] (GIS Map, Live Player, Incident Triage, Forensic Vault)
```

---

## 3. Core Technical Capabilities

### 3.1. Camera Interoperability & Ingestion Resilience
* **Vendor-Neutral Ingestion:** Implements native adapters for TCP interleaved RTSP streams, ONVIF Profile S/T SOAP discovery and PTZ steering, USB/V4L2 local capture devices, test file playback, and synthetic ISO-12233 test patterns.
* **Latest-Frame Slot Decoupling:** Employs a single-frame atomic slot (`threading.Event` + mutex) between reader threads and perception pipelines. If downstream neural inference experiences transient load, intermediate frames are superseded, preventing memory bloat and processor backlog.
* **Camera Health Telemetry:** Independent worker checks socket status, ping latency, packet loss, and frame arrival intervals every 5 seconds, publishing operational states (`ONLINE`, `DEGRADED`, `OFFLINE`).
* **Interoperability Disclosure:**
  > *"Vendor-neutral RTSP/ONVIF adapter architecture implemented. Hardware interoperability requires device-specific validation."*

### 3.2. Low-Light & Adversarial Night Enhancement
* **Adaptive CLAHE:** Real-time luminance estimator evaluates average frame intensity. When ambient illumination falls below 45 lux, Contrast Limited Adaptive Histogram Equalization (CLAHE, clip limit 2.5, $8\times 8$ grid) normalizes local contrast, enabling YOLO detection in dark conditions.
* **Automated Bypass:** Under daylight conditions (> 50 lux with hysteresis), enhancement is bypassed, reducing processing overhead to 0.5 ms.

### 3.3. Deep Neural Perception & Tracking
* **YOLO26n / YOLO11n:** Evaluates frames through optimized ONNX runtimes using OpenCV DNN fallback. Detects persons, vehicles, and backpacks with sub-20 ms latency on commodity CPU hardware.
* **ByteTrack Association:** Associates high-confidence and low-confidence detection boxes using Kalman motion prediction, preventing identity loss during brief object occlusions.
* **Ground-Footprint Anchoring:** Uses the bottom-center coordinate $[c_x, y_2]$ of bounding boxes rather than bounding box centers to determine polygon geofence containment, preventing false crossing alerts caused by person height perspective.

### 3.4. Risk Priority Scoring (RPS) Engine
IBVAP replaces crude boolean thresholds with a deterministic, mathematically validated **multiplicative threat ranking formula**:
$$\text{RPS} = G_{\text{spatial}} \times (B_{\text{base}} + \Delta_{\text{motion}} + \Delta_{\text{env}}) \times T_{\text{persistence}} \times \Omega_{\text{operator}}$$
* **Spatial Gate ($G_{\text{spatial}}$):**
  * `RESTRICTED` (Zero-Line / Border Trench): $G_{\text{spatial}} = 1.0$
  * `BUFFER` (Approach Corridor): $G_{\text{spatial}} = 0.65$
  * `PUBLIC` (Highway / Civilians): $G_{\text{spatial}} = 0.0$
* **Public Zone Invariant:** PUBLIC zone observations receive an RPS spatial gate of 0.0 and therefore contribute no RPS threat points.
* **Explainability:** Every score publishes an itemized signal breakdown explaining exactly why an alert was triaged as `LOW`, `MEDIUM`, `HIGH`, or `CRITICAL`.

### 3.5. Biometrics, ANPR & Cross-Camera ReID
* **ANPR Multi-Frame Consensus:** Evaluates vehicle plates across successive frames; voting aggregation eliminates optical character recognition jitter and assigns consensus confidence.
* **Face Recognition (YuNet / SFace):** Extracts 128D facial embeddings compared against watchlists via cosine similarity. Results are classified strictly as investigative leads for operator review.
* **Cross-Camera Correlation (512D ReID):** Normalized appearance feature vectors combined with kinematic topology matrices determine cross-camera track continuity across outpost towers.

### 3.6. Evidence Integrity & BSA Section 63 Technical Alignment
* **Section 63 Compliance:** Implements cryptographic sealing using SHA-256 hash chains and Merkle root calculation over raw frame snapshots and H.264 video clips.
* **Forensic Metadata:** Automatically compiles camera identifier, UTC timestamp, frame index, operator audit logs, and hardware device signatures into an immutable JSON certificate.
* **Statutory Disclosure:**
  > *"Implemented technical controls aligned with Bharatiya Sakshya Adhiniyam, 2023 Section 63 requirements. Per-case statutory certificate generation remains an operational/legal prerequisite."*

### 3.7. Offline Resilience & Spool Replay
* **Local Spooling:** If network backhaul fails or the central PostgreSQL database becomes unreachable, incidents and evidence records write to local disk in append-only JSONL format.
* **Idempotent Recovery:** A dedicated replay worker locks the spool file using OS advisory locks (`fcntl.flock`), verifies database deduplication keys, pushes events to the central store, and atomically replaces checkpoint files.

---

## 4. Independently Measured Performance

Performance benchmarks were established on $1280 \times 720$ video streams using independent monotonic wall-clock timing (`time.perf_counter()`):

| Metric | Night Infiltration (< 45 lux, Adaptive CLAHE) | Daylight Passthrough (> 50 lux, CLAHE Bypassed) | Claim Classification |
| :--- | :--- | :--- | :--- |
| **Inline Frame Processing Latency** | **28.11 ms** (~35.6 FPS) | **19.20 ms** (~52.1 FPS) | `MEASURED` |
| **Total End-to-End Latency** | **28.41 ms** (~35.2 FPS) | **19.53 ms** (~51.2 FPS) | `MEASURED` |
| **YOLO Inference Duration** | 19.43 ms | 18.65 ms | `MEASURED` |
| **Adaptive CLAHE Duration** | 8.64 ms | 0.51 ms | `MEASURED` |
| **ByteTrack Tracking Duration** | 0.011 ms | 0.010 ms | `MEASURED` |
| **ZoneFence Evaluation Duration** | 0.011 ms | 0.011 ms | `MEASURED` |
| **RPS Threat Scoring Duration** | 0.013 ms | 0.013 ms | `MEASURED` |
| **Memory Leakage (500 frames)** | 0.0 MB growth | 0.0 MB growth | `MEASURED` |
| **Pipeline Overlap** | **YES (decoupled reader/processor)** | **YES (decoupled reader/processor)** | `MEASURED` |

---

## 5. Security & Governance Architecture

* **Authentication:** Stateless JSON Web Tokens (JWT) signed via HMAC-SHA256 with 24-hour expiration. Passwords hashed using bcrypt (cost factor 12).
* **Fail-Closed Configuration:** Server startup validates secrets via `validate_security_configuration()`. If `ENVIRONMENT=production` or `staging`, default keys, known insecure placeholders, or keys under 32 characters abort execution.
* **5-Tier Strict RBAC:** Role hierarchy (`ADMIN` > `COMMANDER` > `OPERATOR` > `AUDITOR` > `VIEWER`) strictly enforced across all 117 REST routes and 3 WebSocket channels.
* **SSRF Protection:** Camera URL discovery pins socket connections and strictly rejects RFC 1918 loopback addresses, AWS/GCP cloud metadata endpoints (`169.254.169.254`), and non-whitelisted RTSP/HTTP ports.
* **Secret Scanning:** All 112 repository findings audited and baselined in `.secrets.baseline` (0 unreviewed secrets, 0 true credentials leaked).

---

## 6. Quick Start & Reproduction

### Prerequisites
* Python 3.9+ with virtual environment tools
* Node.js 18+ and npm
* Git

### Installation & Execution
```bash
# 1. Clone repository
git clone https://github.com/VivekGitNinja/IBVAP.git
cd IBVAP

# 2. Setup Python environment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Initialize database
alembic upgrade head

# 4. Verify full test regression (733 tests)
./.venv/bin/pytest backend/tests/

# 5. Build and verify frontend
cd frontend
npm install
npm run build
npm test
cd ..

# 6. Start platform
# Backend API (port 8001):
uvicorn backend.app.main:app --host 0.0.0.0 --port 8001

# Frontend Dashboard (port 5173):
cd frontend && npm run dev
```

---

## 7. Authoritative Submission Document Index

1. [`SIH_ARCHITECTURE.md`](file:///Users/vivek/Downloads/ibvap/SIH_ARCHITECTURE.md): Comprehensive end-to-end edge and control plane architecture.
2. [`SIH_DEMO_RUNBOOK.md`](file:///Users/vivek/Downloads/ibvap/SIH_DEMO_RUNBOOK.md): Deterministic 8-step live demonstration runbook.
3. [`SIH_JUDGE_FAQ.md`](file:///Users/vivek/Downloads/ibvap/SIH_JUDGE_FAQ.md): 20 direct, evidence-based answers to technical and deployment questions.
4. [`SIH_CLAIMS_EVIDENCE_MATRIX.md`](file:///Users/vivek/Downloads/ibvap/SIH_CLAIMS_EVIDENCE_MATRIX.md): Detailed claims classification and evidence audit.
5. [`MODEL_PROVENANCE.md`](file:///Users/vivek/Downloads/ibvap/MODEL_PROVENANCE.md): Neural network origins, checksums, and licensing terms.
6. [`SIH_KNOWN_LIMITATIONS.md`](file:///Users/vivek/Downloads/ibvap/SIH_KNOWN_LIMITATIONS.md): Transparent disclosure of environmental dependencies and remaining deployment requirements.
7. [`SIH_FINAL_SUBMISSION_CHECKLIST.md`](file:///Users/vivek/Downloads/ibvap/SIH_FINAL_SUBMISSION_CHECKLIST.md): Verification sign-off matrix.
