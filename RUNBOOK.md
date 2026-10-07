# IBVAP Operations Runbook
## SIH 2026 — PS-26187 | SSB Border Security AI Platform

---

> **Audience:** System integrators, ops team, SIH 2026 evaluators.  
> **Scope:** Local development / evaluation mode on a single-host Ubuntu/macOS deployment.  
> **NO deployment to cloud. NO external network dependency.**

---

## Table of Contents

1. [System Requirements](#1-system-requirements)
2. [First-Time Setup](#2-first-time-setup)
3. [Starting the System](#3-starting-the-system)
4. [Verifying System Health](#4-verifying-system-health)
5. [Running the Test Suites](#5-running-the-test-suites)
6. [Model Pre-Caching](#6-model-pre-caching)
7. [Sample Video Fixtures](#7-sample-video-fixtures)
8. [Offline Operation Proof](#8-offline-operation-proof)
9. [Using the UI](#9-using-the-ui)
10. [API Quick Reference](#10-api-quick-reference)
11. [Evidence & Reports](#11-evidence--reports)
12. [C2 Webhook Integration](#12-c2-webhook-integration)
13. [Tactical Command Presentation Layer](#13-tactical-command-presentation-layer)
14. [Troubleshooting](#14-troubleshooting)
15. [Configuration Reference](#15-configuration-reference)

---

## 1. System Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| Python | 3.9 | 3.11+ |
| Node.js | 18 LTS | 20 LTS |
| RAM | 4 GB | 8 GB |
| Disk | 2 GB | 5 GB |
| OS | Ubuntu 20.04 / macOS 12 | Ubuntu 22.04 / macOS 14 |
| Camera Feed | RTSP/HTTP/file | USB webcam supported via OpenCV |

> **Air-gap ready:** All AI models are pre-cached on first run. No internet access required during analysis.

---

## 2. First-Time Setup

### 2.1 Clone and Install

```bash
# 1. Enter project directory
cd /path/to/ibvap

# 2. Create Python virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install Python dependencies
pip install -r requirements.txt

# 4. Install frontend dependencies
npm --prefix frontend install

# 5. Pre-cache AI models (run once, then works offline)
python scripts/prepare_models.py
```

### 2.2 Database Initialization

```bash
# Run Alembic migrations (first time only)
.venv/bin/alembic upgrade head

# OR: Let pytest auto-create tables
.venv/bin/pytest backend/tests/ -v --tb=short
```

### 2.3 Generate Sample Video Fixtures

```bash
# Creates samples/ directory with synthetic test videos
python scripts/make_demo_fixtures.py
```

---

## 3. Starting the System

### 3.1 Backend API Server

```bash
source .venv/bin/activate
uvicorn backend.app.main:app --host 0.0.0.0 --port 8001 --reload
```

- API docs: `http://localhost:8001/docs`
- Health: `http://localhost:8001/api/v1/readiness`

### 3.2 Frontend Dev Server

```bash
npm run dev --prefix frontend
```

- UI: `http://localhost:5173`

### 3.3 Quick Check (Both Running)

```bash
curl -s http://localhost:8001/api/v1/readiness | python3 -m json.tool
```

Expected:
```json
{
  "status": "ready",
  "checks": { "database": "ok", "models": "ok", "storage": "ok" }
}
```

---

## 4. Verifying System Health

### Full Health Audit

```bash
# AI model availability + pipeline check
python scripts/doctor.py

# End-to-end pipeline verification (37 checks)
python scripts/verify_real_pipeline.py
```

All 37 checks must show `[PASS]`.

---

## 5. Running the Test Suites

### Backend Tests

```bash
.venv/bin/pytest backend/tests/ -v --tb=short
```

Expected: **182 passed, 0 skipped**

### Frontend Tests

```bash
npm test --prefix frontend
```

Expected: **30/30 passed**

### Full Build Check

```bash
npm run build --prefix frontend
```

Expected: Clean build, 70 modules transformed, no TypeScript errors.

---

## 6. Model Pre-Caching

All models are downloaded ONCE via `prepare_models.py` and cached locally. The analysis pipeline will **not download models at runtime**.

```bash
python scripts/prepare_models.py
```

Models cached:
- YOLOv8n detector (`yolov8n.pt`)
- YuNet face detector (`face_detection_yunet_2023mar.onnx`)
- SFace face recognizer (`face_recognition_sface_2021dec.onnx`)
- Tesseract OCR data (`eng.traineddata`)

---

## 7. Sample Video Fixtures

Located in `samples/`. Created by `scripts/make_demo_fixtures.py`.

| File | Description | Use Case |
|------|-------------|----------|
| `day_crossing.mp4` | Person crossing a virtual fence line (daylight) | Zone crossing test |
| `night_crossing.mp4` | Night scene with CLAHE enhancement | Night luminance test |
| `vehicle_plate.mp4` | Vehicle with rendered license plate | ANPR test |
| `loitering.mp4` | Person stationary for 10 seconds | Loitering detection test |

See `samples/README.md` for detailed descriptions.

### Upload for Analysis

```bash
# Upload a sample video and start analysis
curl -X POST http://localhost:8001/api/v1/jobs \
  -F "file=@samples/day_crossing.mp4" \
  -F "camera_id=1"
```

---

## 7.1 Live RTSP Streaming & Loopback Demo

IBVAP integrates live edge video streams using high-performance OpenCV/FFmpeg TCP capture and binary WebSocket streaming (`/ws/live/{camera_id}`) directly to the Tactical HUD.

### A. Real RTSP Server Loop (MediaMTX / ffmpeg)
When an external RTSP server like `mediamtx` (formerly `rtsp-simple-server`) is available:
```bash
# 1. Start mediamtx server on default RTSP port 8554
mediamtx

# 2. In another terminal, stream a continuous sample video loop to the RTSP server:
ffmpeg -re -stream_loop -1 -i samples/vehicle_plate.mp4 -c copy -f rtsp rtsp://localhost:8554/live/bop1

# 3. Register the camera via API or the Deploy Camera Wizard in the Live Monitor:
curl -X POST http://localhost:8001/api/v1/cameras \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <TOKEN>" \
  -d '{
    "name": "BOP-01 Sector Alpha Optical Feed",
    "location": "Sector Alpha Gate",
    "bop": "BOP-01",
    "camera_type": "RTSP",
    "stream_url": "rtsp://localhost:8554/live/bop1",
    "fps": 15
  }'
```

### B. Air-Gapped / Single-Host Evaluation Fallback (Loopback Stream)
If `mediamtx` is not installed on the evaluation machine, IBVAP provides an identical-code-path loopback stream using the `file://` scheme or local HTTP bridge:
```bash
# Register camera pointing to local surveillance fixture (exercises identical OpenCV decode & live pipeline)
curl -X POST http://localhost:8001/api/v1/cameras \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <TOKEN>" \
  -d '{
    "name": "BOP-01 Sector Alpha Tactical RTSP",
    "location": "Sector Alpha Gate 1",
    "bop": "BOP-01",
    "camera_type": "RTSP",
    "stream_url": "file:///path/to/ibvap/samples/vehicle_plate.mp4",
    "fps": 15
  }'
```

### C. Automated RTSP Verification Script
Run the automated verification script to provision the camera, verify status `ONLINE` in UI, stream frames, execute live CV analysis, and generate forensic screenshots:
```bash
node scripts/verify_rtsp_demo.mjs
```
Expected output:
- `data/evidence/53_rtsp_camera_online.png` (Live Monitor grid shows camera status ONLINE)
- `data/evidence/54_rtsp_live_detections.png` (Live stream CV analysis active with detections)


---

## 8. Offline Operation Proof

IBVAP is **fully air-gapped** after model pre-caching:

```bash
# 1. Pre-cache models (one time, needs internet)
python scripts/prepare_models.py

# 2. Disable all network interfaces (or use --env=offline)
# sudo ifconfig en0 down  # macOS example

# 3. Start backend and frontend as normal (no internet used)
uvicorn backend.app.main:app --host 0.0.0.0 --port 8001 &
npm run dev --prefix frontend &

# 4. Verify no network calls during analysis
python scripts/verify_real_pipeline.py
```

The offline integrity test in `backend/tests/test_system_integrity.py::test_full_offline_analysis` blocks network at OS level and verifies a full analysis job completes.

---

## 9. Using the UI

Navigate to `http://localhost:5173` and use the sidebar to access:

| View | Description |
|------|-------------|
| **Dashboard** | Live system status, recent incidents |
| **Upload** | Upload video for analysis |
| **Jobs** | Monitor analysis job progress (WebSocket) |
| **Zones** | Create virtual fences (line/polygon) |
| **Watchlist** | Manage face watchlist for matching |
| **ANPR** | License plate recognition results |
| **Reports** | Generate JSON/PDF evidence reports |
| **Situational Map** | Live camera + incident map (Leaflet/offline grid) |
| **Settings** | System configuration |

---

## 10. API Quick Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/readiness` | GET | System health check |
| `/api/v1/cameras` | GET/POST | Camera management |
| `/api/v1/cameras/{id}` | PATCH | Update camera (GPS, sector) |
| `/api/v1/jobs` | GET/POST | Analysis job management |
| `/api/v1/jobs/{id}/status` | GET | Job status + progress |
| `/api/v1/ws/jobs/{id}` | WS | Real-time job progress |
| `/api/v1/zones` | GET/POST/DELETE | Virtual fence management |
| `/api/v1/incidents` | GET | Incident listing |
| `/api/v1/evidence/{id}/verify` | POST | SHA-256 evidence verification |
| `/api/v1/report/{id}/json` | GET | JSON evidence report |
| `/api/v1/report/{id}/pdf` | GET | PDF evidence report (BSA 2023) |
| `/api/v1/map` | GET | Tactical map data (cameras + incidents) |
| `/api/v1/watchlist` | GET/POST | Face watchlist |
| `/api/v1/doctor` | GET | AI model status |

Full interactive API docs: `http://localhost:8001/docs`

---

## 11. Evidence & Reports

All evidence is SHA-256 sealed with technical controls aligned with **Bharatiya Sakshya Adhiniyam 2023 (Section 63)**. Per-case statutory certificate generation remains an operational/legal prerequisite.

```bash
# Verify evidence seal for evidence ID 1
curl -X POST http://localhost:8001/api/v1/evidence/1/verify

# Export PDF report for job ID 1
curl http://localhost:8001/api/v1/report/1/pdf -o report.pdf

# Export JSON report for job ID 1
curl http://localhost:8001/api/v1/report/1/json | python3 -m json.tool
```

---

## 12. C2 Webhook Integration

IBVAP sends real-time incident alerts to a C2 (Command & Control) system via HMAC-SHA256 signed webhooks.

**Configuration in `backend/app/core/config.py`:**

```python
c2_webhook_url: str = ""             # Set to enable (e.g., "http://c2-system:9000/alerts")
c2_webhook_secret: str = "c2-tactical-secret-key"  # Pre-shared secret
```

**Verification on the C2 receiver:**

```python
import hmac, hashlib
def verify(body_bytes, signature_header, secret):
    expected = "sha256=" + hmac.new(
        secret.encode(), body_bytes, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)
```

- Header: `X-IBVAP-Signature: sha256=<hex>`  
- Retry policy: 3 attempts, exponential backoff (1s, 2s, 4s)  
- Dispatched in background thread (non-blocking)  
- Default: **disabled** (empty URL = no-op)

---

## 13. Tactical Command Presentation Layer

IBVAP features a high-density, situational-awareness tactical presentation layer adhering to strict operational integrity standards.

### 13.1 Operational Design Principles
- **Zero Mock / Zero Synthetic Data:** Every telemetry value, coordinate, DEFCON status, track polyline, and camera orientation originates from verified database models and live API endpoints. Unconfigured fields explicitly display `"-- / --"`.
- **BSA 2023 §63 Evidence Immutability:** Presentation filters (e.g. Tactical HUD brackets, NVG/FLIR preview shaders) are strictly restricted to client-side viewport rendering. Stored evidence video frames and their cryptographic SHA-256 digests remain bit-for-bit pristine.
- **Air-Gap First:** All visualization assets, map fallbacks, and track computation run offline without external runtime API calls.

### 13.2 Tactical HUD Overlay
- **Dual Military Chronometer:** Synchronized UTC (Zulu) and Indian Standard Time (IST, UTC+5:30) 24-hour military clocks.
- **Geodetic Coordinates:** Formatted in high-precision WGS84 decimal degrees (`lat° N, lon° E`). Displays `"-- / --"` if GPS fix is uncalibrated.
- **DEFCON Posture Integration:** Real-time indicator mapped directly to system threat metrics:
  - `DEFCON 1` (Critical threat score ≥ 80)
  - `DEFCON 2` (High threat score ≥ 60)
  - `DEFCON 3` (Medium threat score ≥ 40)
  - `DEFCON 4` (Low threat score ≥ 20)
  - `DEFCON 5` (Normal / Guarded < 20)
- **Telemetry Readouts:** Real video stream metadata (source FPS, native resolution, frame processing latency, video codec).

### 13.3 Multi-Object Track Trails & Active Tracks Roster
- **Track Trajectory API:** `GET /api/v1/analysis/jobs/{job_id}/tracks` extracts contiguous spatial centroids across video frames, downsampling to optimal polyline density.
- **Synchronized Canvas Rendering:** Track polylines render directly over the video canvas up to current playback timestamp:
  - Cyan polyline: Person track
  - Green polyline: Vehicle track
- **Active Tracks Roster:** Interactive tabular panel listing active track IDs, classification badges, calculated dwell times, maximum speeds, and night-vision flags.
- **Click-to-Track:** Selecting any track badge (`TRK-X`) automatically seeks video playback to the target's first detected frame, highlights the path with a glowing polyline, and isolates detection events.

### 13.4 Situational Map & Offline Tile Caching
- **Quad-Tier Map Layer Fallback:**
  1. Local cached tiles (`/tiles/{z}/{x}/{y}.png`)
  2. Esri World Imagery (`https://server.arcgisonline.com/...`)
  3. OpenStreetMap
  4. Tactical Dark Schematic Grid (100% offline canvas fallback)
- **Pre-Caching Sector Tiles:**
  ```bash
  # Pre-download border sector tiles for air-gapped demo
  python scripts/cache_map_tiles.py --min-zoom 6 --max-zoom 14
  ```
- **Directional FOV Cones:** Cameras display directional field-of-view sweep cones rotated by their configured compass bearing.
- **Incident Inspector Drawer:** Clicking an incident marker opens a slide-out drawer presenting BSA 2023 §63 cryptographic seal status, evidence snapshot, and SHA-256 digest.

### 13.5 Tactical Deep Linking & Share Links
- **State Serialization:** Share links encapsulate the complete operational state via URL query parameters:
  `?view=media&job=12&incident=4&track=1&t=14.5`
- **Instant Clipboard Export:** The "SHARE" action in the top navigation bar serializes current view, active incident, and track filters for rapid operational handoff.

### 13.6 Scripted Guided Patrol Mode
- **6-Station Guided Tour:** Automated sweep stepping through Live Monitor, Video Studio, Incident Triage, Evidence Vault, Situational Map, and Forensic Report.
- **Live Platform Execution:** Every stop reflects actual live system data with countdown timers, manual forward/back controls, and instant `ESC` cancellation.

### 13.7 Sensor Skins Safety Policy
- Guarded by `ENABLE_SENSOR_SKINS = false` in `frontend/src/config.ts`.
- When enabled for demonstration, applies CSS-only Night-Vision / FLIR visual styling with a permanent on-screen disclaimer: `"VISUALIZATION ONLY — DETECTION RUNS ON RAW FRAMES"`.
- Video files, frames processed by detectors, and evidence hashes are completely unmodified.

---

## 14. Troubleshooting

### Backend won't start

```bash
# Check port conflict
lsof -i :8001
# Check Python environment
python3 -c "import fastapi, sqlalchemy, cv2; print('OK')"
# Reinstall deps
pip install -r requirements.txt
```

### Frontend won't start

```bash
cd frontend && npm install && npm run dev
```

### Models missing

```bash
python scripts/doctor.py
python scripts/prepare_models.py
```

### Tests failing

```bash
# Clean DB and re-run
rm -f ibvap.db && .venv/bin/pytest backend/tests/ -v --tb=short
```

### Alembic version mismatch

```bash
.venv/bin/alembic stamp head
.venv/bin/alembic upgrade head
```

---

## 15. Configuration Reference

### Backend (`backend/app/core/config.py`)

| Setting | Default | Description |
|---------|---------|-------------|
| `yolo_model_path` | `models/yolov8n.pt` | YOLO detector weight path |
| `enable_face_detection` | `True` | YuNet face detection |
| `enable_face_blur` | `False` | Auto-blur detected faces |
| `enable_anpr` | `True` | License plate recognition |
| `enable_watchlist_matching` | `True` | Face watchlist matching |
| `enable_night_enhancement` | `True` | CLAHE low-light enhancement |
| `enable_virtual_fence` | `True` | Zone crossing detection |
| `c2_webhook_url` | `""` | C2 system webhook URL (disabled if empty) |
| `c2_webhook_secret` | `"c2-tactical..."` | HMAC pre-shared key |
| `max_disappeared` | `10` | Tracker occlusion retention frames |
| `loitering_threshold_seconds` | `10` | Loitering rule trigger threshold |
| `crowd_count_threshold` | `5` | Crowd gathering trigger count |

### Frontend (`frontend/src/config.ts`)

| Setting | Default | Description |
|---------|---------|-------------|
| `ENABLE_HUD_STYLING` | `true` | Tactical HUD corner brackets, military clock, geodetic readouts |
| `ENABLE_SENSOR_SKINS` | `false` | Experimental NVG/FLIR preview shaders (strictly client-side CSS) |

---

## 16. FINAL DEMO RUN-SHEET (Exact Evaluator Script)

This step-by-step walkthrough is designed for SIH 2026 evaluators and SSB operational officers to evaluate the complete end-to-end capabilities of IBVAP in under 10 minutes.

### Pre-requisites & Verification
Ensure backend and frontend are running:
```bash
# Terminal 1: Backend API
.venv/bin/python3 -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8001

# Terminal 2: Frontend Tactical Console
npm run dev -- --host 0.0.0.0 --port 5173
```
Run the automated system doctor:
```bash
.venv/bin/python3 scripts/doctor.py
```
*Expected: All 18 diagnostics pass (Python, OpenCV, Models, Database, Storage, Air-gap).*

---

### Step 1: Tactical Clearance & Role-Based Access Control (RBAC)
1. Open Chrome browser to `http://localhost:5173`.
2. Observe the **IBVAP // C4ISR Clearance** modal.
3. Test failed authorization: enter operator identifier `invalid_user` and security key `bad_key` -> click **AUTHENTICATE**.
   - *Expected:* Visible red alert banner `Invalid credentials`. No unauthorized access granted.
4. Authenticate as authorized operator: click preset **OPERATOR** (or enter `operator` / `Operator@123`) -> click **AUTHENTICATE**.
   - *Expected:* Modal unlocks and transitions into **DEFCON 4 / ROUTINE** Tactical Command Console.

---

### Step 2: Live RTSP Stream Provisioning
1. In the sidebar, navigate to **Tactical Matrix** (`/?view=cameras`).
2. Observe the multi-camera surveillance matrix (Cameras 1-8).
3. Click **Deploy Camera** to test RTSP provisioning:
   - Select brand: **Generic RTSP Stream**.
   - Enter invalid test endpoint: `10.255.255.1`.
   - Click **Test Optical Connection** -> observe honest error `Link failure: RTSP stream unreachable`.
4. To verify live RTSP camera in evaluation mode, run the verified RTSP evaluation harness:
   ```bash
   node scripts/verify_rtsp_demo.mjs
   ```
   - *Expected:* Camera provisions with real RTSP loopback stream, real-time FPS/latency telemetry, and object detections.

---

### Step 3: Tactical Video Studio & Media Ingestion
1. Navigate to **Video Studio** (`/?view=media`).
2. Demonstrate drag-and-drop or file upload:
   - Select `samples/vehicle_plate.mp4`.
3. Observe instant ingestion:
   - Probed metadata: resolution `640x480`, duration `6.0s`, frame rate `10.0 FPS`.
   - Cryptographic SHA-256 digest computed on byte ingestion.
4. Click **Stream** on the uploaded clip:
   - *Expected:* Video preview opens and plays in browser via air-gapped HTML5 video pipeline.

---

### Step 4: Edge Computer Vision Pipeline Execution
1. Click **⚡ Analyze** on `vehicle_plate.mp4`.
2. In the **Configure Edge CV Pipeline** modal:
   - Select Detector Model: `yolo11n` (Ultralytics Edge ONNX).
   - Check **License Plate (ANPR)** and **Multi-Object Tracking (BoT-SORT)**.
   - Click **🚀 Launch Pipeline Job**.
3. Observe real-time telemetry:
   - Progress bar increases deterministically across polls.
   - **REAL FRAME DETECTIONS FEED** populates with vehicle detections, track identifiers (`TRK-T-0`), and plate chips (`🚗 DL01AB1234`).
   - Click any `TRK-` badge to highlight the track path and seek video to first appearance.

---

### Step 5: Virtual Fence Tripwire & Intrusion Escalation
1. On the Video Studio page, click **Virtual Fence Studio**.
2. Draw a **Tripwire Line**:
   - Click two points across the roadway/perimeter.
   - Zone Name: `Perimeter Bravo Line`.
   - Crossing Direction: `Either Direction`.
   - Click **Save Virtual Fence Zone**.
3. Upload and analyze `samples/day_crossing.mp4` with the zone active.
4. Navigate to **Threat Queue** (`/?view=incidents`):
   - *Expected:* Instant intrusion incident escalation with code `INC-...-LINE_BREACH`, threat score >= 85, and legal statutory citation.

---

### Step 6: ANPR License Plate Intelligence
1. Navigate to **ANPR Checkpost** (`/?view=anpr`).
2. Search query: enter `DL01` in the tactical search input.
3. Observe filtered plate hits with confidence scores, vehicle bounding boxes, and timestamp logs.

---

### Step 7: Facial Recognition (FRS) Biometrics
1. Navigate to **FRS Biometrics** (`/?view=frs`).
2. Click **Enroll Suspect Biometrics**:
   - Subject Name: `Suspect Target Alpha`.
   - Upload portrait: `samples/suspect_portrait.jpg`.
   - Click **Enroll Biometric Target**.
   - *Expected:* Suspect enrolled into gallery with 128-d OpenCV SFace biometric embedding.
3. Upload `samples/suspect_crossing.mp4` in Video Studio and run with **Face Watchlist Match** enabled.
   - *Expected:* Real biometric match confirmed (similarity >= 36.3%), triggering a Critical FRS Alert.

---

### Step 8: Night Surveillance & CLAHE Low-Light Intelligence
1. In Video Studio, upload `samples/night_crossing.mp4`.
2. Launch analysis with **Night Luma & CLAHE** active.
3. Observe:
   - Mean luma calculated (<= 60.0).
   - Contrast Limited Adaptive Histogram Equalization applied.
   - Detection card flagged with `🌙 NIGHT` badge.

---

### Step 9: BSA 2023 §63 Forensic Evidence & Electronic Record Report
1. Navigate to **Evidence Locker** (`/?view=evidence`).
2. Click on an evidence snapshot or incident clip.
3. Click **Verify Hash**:
   - Re-reads file from disk, computes SHA-256, and compares with manifest hash.
   - *Expected:* Green validation banner `match: true` confirming cryptographic seal integrity under technical controls aligned with Bharatiya Sakshya Adhiniyam, 2023 §63.
4. On Video Studio, click **Export PDF (BSA §63)**:
   - Downloads forensic evidence certificate (Implemented technical controls aligned with Bharatiya Sakshya Adhiniyam, 2023 Section 63 requirements. Per-case statutory certificate generation remains an operational/legal prerequisite).
   - Verify containing statutory legal citation, digital hash seal, and officer declaration.
5. Click **Export JSON** for automated C2 / Inter-Agency data sharing.

---

### Step 10: Situational Map & Air-Gap Resilience
1. Navigate to **Situational Map** (`/?view=map`).
2. Observe Leaflet tactical map with border BOP pins, camera fields-of-view, and active incident markers.
3. Click an incident marker to open the slide-out inspector drawer with evidence snapshot.
4. Verify air-gap isolation: open Developer Tools Network tab; zero external network requests made outside `localhost`.

---

*IBVAP RUNBOOK — SIH 2026 | MHA SSB PS-26187 | Prepared by IBVAP Engineering Team*
