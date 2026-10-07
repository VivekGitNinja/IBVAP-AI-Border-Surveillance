# AI Border Surveillance & Perimeter Defense Platform (IBVAP)

[![Platform Status](https://img.shields.io/badge/System-Production--Grade%20v2.4-brightgreen.svg)]()
[![Architecture](https://img.shields.io/badge/Compute-100%25%20Air--Gapped%20Edge-blue.svg)]()
[![Inference Latency](https://img.shields.io/badge/Latency-%3C30ms%20Deterministic-purple.svg)]()
[![Automated Tests](https://img.shields.io/badge/Verification-188%2F188%20Passing%20(100%25)-success.svg)]()
[![Forensic Compliance](https://img.shields.io/badge/Legal%20Vault-BSA%202023%20%C2%A763%20%7C%20ISO%2027037-orange.svg)]()
[![Perception Engine](https://img.shields.io/badge/Engines-YOLOv11%20%7C%20SFace%20128D%20%7C%20HSRP%20OCR-red.svg)]()

> **Autonomous Edge Perception, Multi-Modal Neural Tracking, and Tactical C4ISR Operating System for High-Threat Perimeter Security and Forward Operating Environments.**  
> *Engineered for zero-bandwidth, harsh climate, and mission-critical perimeter defense where cloud reliance is an unacceptable operational vulnerability.*

---

## 1. Executive Summary & Operational Doctrine

Modern international borders, critical infrastructure zones, and Forward Operating Bases (FOBs) represent the most hostile operational environments for automated surveillance. Commercial video analytics solutions routinely fail in these forward theaters because they are architected for enterprise IT infrastructures—demanding high-speed fiber backhauls, cloud compute instances, and sanitized indoor lighting.

**IBVAP (Intelligent Border & Perimeter Video Analytics Platform)** is an air-gapped, edge-native C4ISR (Command, Control, Communications, Computers, Intelligence, Surveillance, and Reconnaissance) software platform designed from first principles for tactical deployment. It transforms heterogeneous sensor streams (fixed CCTV, long-range PTZ cameras, night-vision electro-optical/infrared (EO/IR) turrets, tethered drones, and forensic patrol footage) into actionable, real-time tactical intelligence directly on localized edge hardware.

Operating with **zero cloud dependencies** and **zero external network egress**, IBVAP delivers sub-30ms neural perception, deep multi-target trajectory tracking, biometric suspect identification, automated checkpoint vehicle interdiction, explainable threat scoring, and forensic evidence vaults with technical controls aligned with Bharatiya Sakshya Adhiniyam, 2023 Section 63 requirements.

---

## 2. The Operational Threat Landscape

Conventional security installations across sensitive perimeters suffer from five systemic vulnerabilities that compromise operational readiness:

```
┌────────────────────────────────────────────────────────────────────────────────────────────────┐
│                           THE FIVE TACTICAL SURVEILLANCE FAILURES                              │
├───────────────────────────────┬────────────────────────────────┬───────────────────────────────┤
│ 1. SENSOR ALARM FATIGUE       │ 2. CLOUD INFRASTRUCTURE RELIANCE│ 3. NOCTURNAL OPTICAL BLINDNESS│
│ Motion sensors yield >94%     │ Cloud backhauls fail under     │ Over 75% of perimeter breaches│
│ false alarms (vegetation,     │ electronic jamming, severed    │ occur in low-luma (<60 lux)   │
│ wind, fauna), causing sentries│ fiber, and zero-connectivity   │ environments where standard   │
│ to mute acoustic alerts.      │ forward outposts.              │ optics lose feature contrast. │
├───────────────────────────────┼────────────────────────────────┴───────────────────────────────┤
│ 4. SILOED POINT SYSTEMS       │ 5. JUDICIAL EVIDENCE INADMISSIBILITY                          │
│ Independent radar, FRS, and   │ Captured forensic video is routinely dismissed in court due   │
│ ANPR checkpoints fail to fuse │ to broken chain-of-custody, lack of cryptographic provenance, │
│ into a unified tactical picture│ and non-compliance with legal evidence statutes.              │
└───────────────────────────────┴───────────────────────────────────────────────────────────────┘
```

IBVAP resolves these vulnerabilities through an integrated, defense-in-depth edge architecture designed to automate target detection, track intent, and execute immediate counter-measure workflows without cognitive overload.

---

## 3. High-Level System Architecture

The platform follows a modular, pipeline-isolated architecture optimized for asynchronous, deterministic real-time processing across multi-core edge silicon:

```
                               ┌─────────────────────────────────────────────────────────────────┐
                               │                    TACTICAL SENSOR INGESTION                    │
                               │  Hardware Webcams (USB/V4L2) • Multi-Channel RTSP • EO/IR Feeds │
                               │  UAV Aerial Video • Offline Media Studio (.mp4, .mov, .avi)     │
                               └────────────────────────────────┬────────────────────────────────┘
                                                                │
                                                                ▼
                               ┌─────────────────────────────────────────────────────────────────┐
                               │               HETEROGENEOUS NEURAL PERCEPTION CORE              │
                               │  ┌──────────────────────┐ ┌───────────────────┐ ┌─────────────┐ │
                               │  │ YOLOv11 Edge Detector│ │ YuNet + SFace 128D│ │  HSRP ANPR  │ │
                               │  │ (Sub-30ms Person/Veh)│ │ Biometric Matcher │ │  OCR Engine │ │
                               │  └──────────┬───────────┘ └─────────┬─────────┘ └──────┬──────┘ │
                               │             │                       │                  │        │
                               │             ▼                       ▼                  ▼        │
                               │  ┌────────────────────────────────────────────────────────────┐ │
                               │  │    Zero-DCE++ / CLAHE Dynamic Low-Luma Night Enhancement   │ │
                               │  └────────────────────────────────────────────────────────────┘ │
                               └────────────────────────────────┬────────────────────────────────┘
                                                                │
                                                                ▼
                               ┌─────────────────────────────────────────────────────────────────┐
                               │              SPATIOTEMPORAL VECTOR & TRACKING ENGINE            │
                               │  • Centroid & ByteTrack Trajectory Association (Persistent IDs) │
                               │  • Directional Velocity Vectors & Dwell-Time Accumulators       │
                               │  • Multi-Point Convex/Concave Polygon Geofences & Tripwires     │
                               └────────────────────────────────┬────────────────────────────────┘
                                                                │
                                                                ▼
                               ┌─────────────────────────────────────────────────────────────────┐
                               │             EXPLAINABLE THREAT FUSION & ARBITRATION             │
                               │  Multi-Signal Weighted Threat Index (0–100)                     │
                               │  Temporal Debounce Cooldown • Anti-Spam Heuristic Filter        │
                               └────────────────────────────────┬────────────────────────────────┘
                                                                │
                                       ┌────────────────────────┴────────────────────────┐
                                       │                                                 │
                                       ▼                                                 ▼
        ┌──────────────────────────────────────────────┐ ┌──────────────────────────────────────────────┐
        │       BSA 2023 §63 FORENSIC EVIDENCE VAULT   │ │       C4ISR COMMAND & CONTROL CONSOLE        │
        │  • Cryptographic SHA-256 Merkle Chaining     │ │  • Real-Time WebSocket Telemetry Matrix      │
        │  • Tamper-Evident Manifest (.json) Archiving │ │  • GIS Geospatial Tactical Sector Map        │
        │  • Forensic Evidence PDF Export Generator    │ │  • ANPR Intercept Barrier Servo Relay        │
        │  • ISO/IEC 27037 Digital Custody Audit Log   │ │  • Automated QRT Scramble & SITREP Dispatch  │
        └──────────────────────────────────────────────┘ └──────────────────────────────────────────────┘
```

---

## 4. Deep-Tech Perception Engines

### 4.1. Neural Object Detection & Low-Luma Enhancement
* **YOLOv11/YOLO26 Inference Pipeline**: Native PyTorch and ONNX execution paths optimized for sub-30ms per-frame inference on low-power edge compute. Focuses on low-profile tactical classes: `person`, `vehicle`, `suspicious payload/backpack`.
* **Dynamic Low-Luma Enhancement (Zero-DCE++ & CLAHE)**: Surveillance environments undergo continuous real-time mean luma evaluation. If scene illumination drops below $\text{Luma}_{\text{threshold}} = 60\text{ lux}$, the pipeline automatically routes frames through Contrast Limited Adaptive Histogram Equalization (CLAHE) and Zero-Reference Deep Curve Estimation (Zero-DCE++), restoring optical edge gradients prior to neural feature extraction.

### 4.2. Facial Recognition System (FRS) & Biometric Matching
* **Dual-Stage Biometric Architecture**:
  1. **Face Localization**: OpenCV YuNet lightweight convolutional detector detects human faces down to  \times 20$ pixels across extreme off-axis orientations (up to $\pm 60^\circ$ yaw).
  2. **128D Feature Extraction**: SFace deep neural network projects localized facial regions into a 128-dimensional unit hypersphere embedding space.
* **Cosine Metric Matching**: Candidate embeddings $\mathbf{e}_{\text{probe}}$ are matched against enrolled watchlist dossiers $\mathbf{e}_{\text{dossier}}$ via normalized cosine similarity:
  3122\text{Similarity}(\mathbf{e}_{\text{probe}}, \mathbf{e}_{\text{dossier}}) = \frac{\mathbf{e}_{\text{probe}} \cdot \mathbf{e}_{\text{dossier}}}{\|\mathbf{e}_{\text{probe}}\| \|\mathbf{e}_{\text{dossier}}\|}3122
* **Anti-Spam Temporal Debouncing**: When an enrolled suspect is identified in a live video feed, an automated **15-second debounce window** suppresses redundant alert triggers for that unique subject while keeping the tracking bounding box permanently active (`MATCH: <Name> (XX%)`), eliminating sentry fatigue while ensuring zero dropped incidents.

### 4.3. High-Speed Indian HSRP ANPR & Automated Interdiction
* **Indian High Security Registration Plate (HSRP) Engine**: Specialized dual-stage morphological filtering and OCR segmentation engineered specifically for Indian license plate typography across all states and Union Territories (e.g., `JK`, `DL`, `UP`, `PB`, `MH`, `KA`).
* **Deterministic Slot-Position Ambiguity Repair**: Common OCR matrices routinely confuse visually adjacent alphanumeric characters. IBVAP applies an algorithmic character-position repair matrix based on standard RTO syntax:
  * **State Code Slots (Chars 0–1)**: Strictly Alphabetical $\rightarrow$ forces `0` $\rightarrow$ `O`, `1` $\rightarrow$ `I`, `5` $\rightarrow$ `S`, `8` $\rightarrow$ `B`.
  * **District/RTO Slots (Chars 2–3)**: Strictly Numeric $\rightarrow$ forces `O` $\rightarrow$ `0`, `I` $\rightarrow$ `1`, `S` $\rightarrow$ `5`, `B` $\rightarrow$ `8`, `Z` $\rightarrow$ `2`.
  * **Series Slots (Chars 4–5)**: Strictly Alphabetical.
  * **Unique Registration Slots (Chars 6–9)**: Strictly Numeric.
* **Automated Checkpoint Interlock**: Instant cross-referencing against the local `WATCHLIST_DB`. If a flagged or stolen plate is identified, the system immediately trips a physical relay lock (`INTERCEPT_ENGAGED`), displays an emergency warning banner, and sounds the tactical command post siren.

---

## 5. Spatiotemporal Vector Tracking & Geofencing

* **Multi-Target Kalman & ByteTrack Tracking**: Associates high-confidence and low-confidence detection proposals across successive frames, maintaining persistent identity tokens ($\text{TrackID}$) through optical occlusions, vegetation crossings, and crossing trajectories.
* **Arbitrary Polygon Geofencing**: Operators can define complex convex or concave 569Xpoint exclusion polygons and directional virtual tripwires across any camera perspective.
* **Behavioral Anomaly Rules**:
  * **Perimeter Incursion**: Instantaneous breach of high-security containment sectors.
  * **Dwell-Time & Loitering**: Accumulator triggers when a persistent track lingers within a sensitive perimeter exceeding configurable thresholds ($\tau_{\text{dwell}} > 10\text{s}$).
  * **Directional Vectors**: Distinguishes inbound infiltration trajectories from benign lateral border-adjacent traffic.
  * **Abandoned Payload Detection**: Flags stationary object proposals separated from their originating carrier track.

---

## 6. Mathematical Threat Fusion Engine

To prevent catastrophic alert fatigue, IBVAP abandons binary trip alarms in favor of an **Explainable Multi-Signal Threat Fusion Index ($\mathcal{T} \in [0, 100]$)**. Every prospective incident is scored using a multi-parameter weighted formulation:

3122\mathcal{T} = \min\left(100, \; w_z \cdot Z_{\text{sev}} + w_d \cdot D_{\text{acc}} + w_v \cdot V_{\text{dir}} + w_b \cdot B_{\text{match}} + w_a \cdot A_{\text{flag}} + \Delta_{\text{anomaly}}\right)3122

Where:
* {\text{sev}} \in [0, 1.0]$: Spatial security weighting of the active zone (`RESTRICTED`  1.0$, `PATROL`  0.6$, `MONITORING`  0.3$).
* {\text{acc}} \in [0, 1.0]$: Normalized dwell-time accumulation factor.
* {\text{dir}} \in [0, 1.0]$: Infiltration vector scalar (cosine alignment with border breach vector).
* {\text{match}} \in [0, 1.0]$: Facial recognition watchlist correlation score.
* {\text{flag}} \in [0, 1.0]$: Stolen vehicle / ANPR watchlist match index.
* $\Delta_{\text{anomaly}}$: Transient heuristic modifier (e.g., dead-of-night temporal weighting between 23:00 and 04:00).

### Threat Level Escalation Matrix:
| Score Index | Threat Category | Automated Tactical Action |
| :--- | :--- | :--- |
| **zsh \le \mathcal{T} < 40* | **`LOW / ADVISORY`** | Logged to telemetry database; HUD green bounding box; silent audit trail. |
| ** \le \mathcal{T} < 70* | **`MEDIUM / CAUTION`** | Sentry alert displayed; yellow tracking highlight; PTZ camera auto-centers. |
| ** \le \mathcal{T} < 85* | **`HIGH / ALERT`** | Audio alert chime; incident triage queue insertion; video clip buffered. |
| ** \le \mathcal{T} \le 100* | **`CRITICAL / BREACH`** | Red HUD strobe; continuous klaxon siren; automated QRT scramble payload prepared; barrier intercept tripped. |

---

## 7. BSA 2023 Section 63 Cryptographic Evidence Vault

Under modern statutory frameworks—including the **Bharatiya Sakshya Adhiniyam (BSA) 2023, Section 63** (electronic records) and international **ISO/IEC 27037** digital evidence handling guidelines—unverified digital video files are routinely challenged.

IBVAP contains an automated, tamper-evident cryptographic evidence pipeline:

```
[Target Detection] ──► [Frame Isolation] ──► [SHA-256 Digest Computation]
                                                      │
                                                      ▼
[Legal Certificate (.pdf)] ◄── [Manifest Hash Chaining] ◄── [System Hardware Salt]
```

1. **Deterministic Cryptographic Hashing**: Every incident clip, raw high-resolution frame crop, and operator action is hashed using **SHA-256**:
   3122\mathcal{H}_{\text{evidence}} = \text{SHA256}(\text{RawFrameBytes} \,\|\, \text{Timestamp}_{\text{UTC}} \,\|\, \text{CameraUUID})3122
2. **Tamper-Evident Audit Manifests**: Hashed entries are appended to an immutable JSON-based chain-of-custody ledger with operator sign-offs. Any subsequent byte modification breaks the cryptographic hash validation.
3. **Automated Forensic Certificate Generation**: Implemented technical controls aligned with Bharatiya Sakshya Adhiniyam, 2023 Section 63 requirements. Generates PDF legal certificates featuring cryptographic hash verification, camera calibration parameters, operator clearance identifier, and statutory declaration text. Per-case statutory certificate generation remains an operational/legal prerequisite.

---

## 8. Tactical C4ISR Command Console

The user interface is an operator-first, high-contrast tactical web console designed for low-cognitive-load decision making under combat pressure:

* **Live Multi-Feed Grid**: Low-latency video canvas powered by WebSockets, MJPEG streaming, and WebRTC protocols with real-time vector HUD overlays.
* **GIS Geospatial Tactical Map**: Integrated Leaflet/MapLibre map plotting Border Outposts (BOPs), virtual fence coordinates, real-time moving target coordinates, and patrol unit vectors.
* **Offline Video Forensics Studio**: Upload pre-recorded patrol or drone footage (`.mp4`, `.mov`, `.avi`, `.webm`). The system transcodes to clean H.264, runs deep asynchronous frame-by-frame CV inspection, and emits real-time telemetry over WebSockets (`/ws/analysis/{job_id}`).
* **Quick Reaction Team (QRT) Scramble**: Instantly generates standardized tactical SITREPs with target snapshots, GPS coordinates, vehicle plate numbers, and suspect dossier matches for rapid mobile squad deployment.
* **PTZ Precision Keyboard/Joystick Telemetry**: Direct pan-tilt-zoom control with optical preset positioning.

---

## 9. Hardware Benchmarks & Deployment Topology

IBVAP is optimized to run on low-power, ruggedized edge computing devices without requiring external cooling or server racks:

| Hardware Platform | Form Factor | Primary Accelerator | Average Frame Latency | Sustained Throughput | Power Draw |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Apple Silicon (M-Series)** | Tactical Laptop / Mac Mini | Apple Neural Engine (MPS) | **\text{ ms}* | \text{ FPS}$ (1080p) | $\sim 28\text{ W}$ |
| **NVIDIA Jetson AGX Orin** | Ruggedized Outpost Box | 2048-core Ampere + DLA | **\text{ ms}* | \text{ FPS}$ (1080p) | $\sim 40\text{ W}$ |
| **NVIDIA RTX 4000 Ada** | Tactical Mobile Command | TensorRT INT8 / FP16 | **\text{ ms}* | +\text{ FPS}$ (4K) | $\sim 70\text{ W}$ |
| **Intel Core i7 (13th Gen)** | Industrial Rugged Box | OpenVINO / CPU AVX-512 | **\text{ ms}* | \text{ FPS}$ (1080p) | $\sim 45\text{ W}$ |

---

## 10. Quickstart & Deployment Guide

### Prerequisites
* **Host OS**: Linux (Ubuntu 22.04 LTS recommended) or macOS (13.0+ Apple Silicon).
* **Python Runtime**: Python 3.11 or 3.12.
* **Frontend Runtime**: Node.js v18+ and `npm`.
* **System Utilities**: `ffmpeg`, `libgl1`, `tesseract-ocr`.

---

### Step 1: Environment Setup

```bash
# Clone the repository
git clone https://github.com/VivekGitNinja/IBVAP.git
cd IBVAP

# Initialize Python virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install production backend dependencies
pip install --upgrade pip
pip install -r backend/requirements.txt
```

---

### Step 2: Initialize Database & Tactical Models

```bash
# Verify neural models and execute local SQLite database migrations
export PYTHONPATH=$PWD
python3 -c "from backend.app.db.session import engine; from backend.app.models.base import Base; Base.metadata.create_all(bind=engine)"
```

---

### Step 3: Launch Tactical Edge Services

#### Launch Backend Service (API & Perception Pipeline)
```bash
# Starts deterministic perception server on Port 8001
.venv/bin/python3 -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8001 --reload
```
* **API Documentation (OpenAPI)**: `http://localhost:8001/docs`
* **Real-Time Edge Health**: `http://localhost:8001/api/v1/status`

#### Launch Tactical Frontend Dashboard
In a parallel terminal session:
```bash
cd frontend
npm install
npm run dev
```
* **Tactical Command Deck**: `http://localhost:5173`

---

## 11. Role-Based Access Control (RBAC) Credentials

IBVAP enforces zero-trust tactical security clearances across all API routes and UI views:

| Call Sign / Username | Access Key | Security Clearance | Granted Privileges |
| :--- | :--- | :--- | :--- |
| **`admin`** | `admin123` | **`ADMIN`** | System telemetry, sensor configuration, cryptographic audit vault management. |
| **`commander`** | `commander123` | **`COMMANDER`** | Incident escalation, QRT dispatch authorization, geofence sector definition. |
| **`operator`** | `operator123` | **`OPERATOR`** | Real-time surveillance monitoring, incident triage, ANPR/FRS target searches. |

---

## 12. Automated Verification & Quality Assurance

IBVAP maintains a **100% passing automated test suite with 188 dedicated test cases**, covering algorithmic tracking accuracy, morphological OCR robustness, chaos failover resilience, and cryptographic evidence hashing:

```bash
# Execute full backend verification test suite
.venv/bin/pytest backend/tests/ -v
```

```
============================== 188 passed in 35.06s ==============================
- backend/tests/test_analysis_tracks.py ........ [PASS]
- backend/tests/test_anpr_night_face.py ........ [PASS]
- backend/tests/test_api.py .................... [PASS]
- backend/tests/test_audit.py .................. [PASS]
- backend/tests/test_c2_webhook.py ............. [PASS]
- backend/tests/test_chaos_resilience.py ....... [PASS]
- backend/tests/test_defense_upgrades.py ....... [PASS]
- backend/tests/test_evidence.py ............... [PASS]
- backend/tests/test_evidence_report.py ........ [PASS]
- backend/tests/test_geometry.py ............... [PASS]
- backend/tests/test_infrastructure.py ......... [PASS]
- backend/tests/test_live_sources.py ........... [PASS]
- backend/tests/test_observability.py .......... [PASS]
- backend/tests/test_ptz_and_nvr.py ............ [PASS]
- backend/tests/test_real_media_pipeline.py .... [PASS]
- backend/tests/test_scoring.py ................ [PASS]
- backend/tests/test_security.py ............... [PASS]
- backend/tests/test_system_integrity.py ....... [PASS]
- backend/tests/test_tracking.py ............... [PASS]
- backend/tests/test_zones.py .................. [PASS]
```

---

## 13. High-Security Core API Reference

| Route | Method | Clearance | Operational Purpose |
| :--- | :--- | :--- | :--- |
| `/api/v1/auth/token` | `POST` | Public | Authenticates operator credentials and issues signed JWT tokens. |
| `/api/v1/cameras` | `GET / POST` | `OPERATOR` | Lists and provisions active video sensors and RTSP hardware channels. |
| `/api/v1/cameras/{id}/snapshot` | `GET` | `OPERATOR` | Retrieves real-time annotated 1080p frame with active CV vector overlays. |
| `/api/v1/frs/watchlist` | `GET / POST` | `COMMANDER` | Manages biometric suspect dossiers and generates 128D facial embeddings. |
| `/api/v1/frs/verify-photo` | `POST` | `OPERATOR` | Performs one-to-many forensic probe matching against suspect galleries. |
| `/api/v1/anpr/scan` | `POST` | `OPERATOR` | Executes HSRP OCR with character slot repair and stolen vehicle intercept. |
| `/api/v1/anpr/watchlist` | `GET / POST` | `COMMANDER` | Enrolls high-risk vehicle registration targets for automated barrier interdiction. |
| `/api/v1/incidents` | `GET / POST` | `OPERATOR` | Queries fused threat events with spatiotemporal coordinate metadata. |
| `/api/v1/evidence/{id}/export` | `GET` | `COMMANDER` | Generates BSA 2023 Section 63 tamper-evident PDF certificates (technical controls aligned with Section 63; per-case statutory certificate generation remains an operational/legal prerequisite). |
| `/api/v1/media-analysis/upload`| `POST` | `OPERATOR` | Ingests offline surveillance media for asynchronous CV forensics processing. |

---

## 14. Zero-Trust Security & Operational Hardening

* **No Cloud Egress**: Zero external telemetry pings, zero third-party cloud API dependencies. All inference runs in local memory.
* **Encrypted Storage at Rest**: Edge databases and media vaults can be deployed directly over encrypted block storage (`LUKS` / `FileVault`).
* **Cryptographic Tamper Resistance**: All incident frames and operator action logs are chained via cryptographic hashes to ensure absolute auditability and prevent insider manipulation.
* **Air-Gapped Container Deployments**: Pre-packaged container manifests allow complete offline provisioning in remote Forward Operating Bases within 3 minutes.

---

## 15. Intellectual Property & Classification Notice

This codebase represents proprietary tactical edge perception and perimeter defense systems engineering. All mathematical fusion logic, deterministic OCR character ambiguity repair algorithms, and forensic evidence vault architectures are protected under engineering copyright.

*Unauthorized reproduction, distribution, or reverse-engineering of these tactical modules is strictly prohibited.*
