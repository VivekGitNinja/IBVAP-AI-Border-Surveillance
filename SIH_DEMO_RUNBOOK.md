# IBVAP — Deterministic 3–5 Minute SIH Live Demonstration Runbook
## Smart India Hackathon (SIH 2026) — Problem Statement SIH26187

---

## 1. Demonstration Setup & Pre-Flight Verification

This runbook guides evaluators and team presenters through a **deterministic, 3-to-5 minute live demonstration** of the IBVAP platform. The SIH demonstration can operate offline using local models, local storage, and documented test fixtures without requiring external cloud inference.

### Pre-Flight Verification (30 Seconds)
1. **Verify Backend Server is Running:**
   ```bash
   curl -s http://localhost:8001/api/v1/health | grep '"status":"healthy"'
   ```
2. **Verify Frontend UI is Reachable:**  
   Open browser at `http://localhost:5173`.
3. **Login Credentials:**  
   * Username: `operator`  
   * Password: `operator123` (or `commander` / `commander123`)

---

## 2. Step-by-Step Live Demonstration Flow

```text
[Step 1: Camera Ingestion]  ──► [Step 2: Daylight Intrusion]  ──► [Step 3: Night Vision Enhancement]
            │                                                              │
            ▼                                                              ▼
[Step 4: Vehicle & ANPR]    ──► [Step 5: Face & Watchlist]     ──► [Step 6: Cross-Camera ReID Handoff]
            │                                                              │
            ▼                                                              ▼
[Step 7: Offline Spool Mode]──► [Step 8: Evidence integrity & BSA Section 63 technical alignment]
```

---

### Step 1 — Camera Ingestion & Health Telemetry (30 Seconds)

* **Objective:** Demonstrate vendor-neutral camera ingestion and real-time operational health monitoring.
* **Actions in UI:**
  1. Navigate to **Live Monitor** (`/live` or top navigation bar).
  2. Point out the live video feed rendering smoothly on the high-performance HTML5/WebGL canvas player.
  3. Show the **Camera Health Status Badge** displaying `ONLINE`, latency `12 ms`, packet loss `0.0%`, and frame rate `30.0 FPS`.
  4. Demonstrate the fallback to synthetic test pattern or video file playback, proving that outposts can operate and test without live physical cameras connected.
* **Presenter Script:**
  > *"IBVAP retrofits existing border CCTV systems without proprietary camera lock-in. Our ingestion adapter decouples camera network sockets from neural inference using an atomic latest-frame buffer. The system continuously tracks camera health, latency, and frame loss every 5 seconds."*

---

### Step 2 — Daylight Border Intrusion & Footprint Anchoring (30 Seconds)

* **Objective:** Demonstrate person detection, ByteTrack trajectory stability, ground-footprint anchor gating, and automated alarm generation.
* **Actions in UI:**
  1. Select Camera 1 (Border Perimeter North).
  2. Observe a person approaching the international boundary line.
  3. Highlight the bounding box labeled `Person [T-0001]` with confidence `0.88`.
  4. Point out the **ground-footprint contact anchor** $[c_x, y_2]$ at the base of the bounding box.
  5. As the ground anchor crosses from the `BUFFER` corridor into the red `RESTRICTED` zero-line polygon:
     - An audible tactical chime sounds.
     - An instant alert card appears in the right-hand Incident Feed: `CRITICAL: Virtual Fence Breach (Sector 4)`.
     - Threat score instantly computes as `92.5` (`CRITICAL`).
* **Presenter Script:**
  > *"Notice how the system tracks the subject with a stable ByteTrack ID. Crucially, IBVAP evaluates geofences using the ground contact anchor at the subject's feet, not the bounding box center. This eliminates false crossing alarms caused by tall persons or camera perspective angles."*

---

### Step 3 — Low-Light Night Scenario & CLAHE Explainability (30 Seconds)

* **Objective:** Demonstrate automated low-light enhancement (< 45 lux) and deterministic RPS score calculation.
* **Actions in UI:**
  1. Switch to Camera 2 (Riverine Trench - Night Mode).
  2. Point out the dark, low-illumination feed (< 40 lux).
  3. Show the **Adaptive CLAHE Indicator** turning green (`CLAHE ACTIVE: Luma 38.2 lux`).
  4. Observe how the neural network detects a crawling or low-profile figure that was previously invisible under raw low contrast.
  5. Click on the generated incident to open the **Threat Score Inspector**:
     - Spatial Gate ($G_{\text{spatial}}$): `1.0` (`RESTRICTED` zone)
     - Base Signal ($B_{\text{base}}$): `70.0` (Boundary crossing)
     - Environmental Factor ($\Delta_{\text{env}}$): `+5.0` (Night bonus) + `+7.0` (Behavior anomaly)
     - Multiplier ($T_{\text{persistence}} \times \Omega_{\text{operator}}$): `1.0`
     - Final Score: `82.0` (`CRITICAL`)
* **Presenter Script:**
  > *"When ambient light drops below 45 lux, IBVAP automatically activates Adaptive CLAHE local normalization in under 9 ms. Furthermore, the threat score is not a black box—the operator can inspect the exact mathematical signal breakdown explaining why the incident was escalated."*

---

### Step 4 — Vehicle Checkpoint & ANPR Multi-Frame Consensus (30 Seconds)

* **Objective:** Demonstrate vehicle tracking, license plate detection, and multi-frame voting consensus.
* **Actions in UI:**
  1. Switch to Camera 3 (Border Checkpoint Ingress).
  2. Observe a vehicle traversing the inspection gate.
  3. Point out the vehicle bounding box and the localized high-resolution plate crop.
  4. Show the **ANPR Observation History Table** displaying individual frame readings:
     - Frame 12: `DL 01 AB 123?` (conf: `0.72`)
     - Frame 14: `DL 01 AB 1234` (conf: `0.89`)
     - Frame 16: `DL 01 AB 1234` (conf: `0.94`)
  5. Show the finalized consensus result: `DL 01 AB 1234` (Consensus Confidence: `0.92`).
* **Presenter Script:**
  > *"Optical OCR on moving vehicles is prone to single-frame jitter. IBVAP accumulates plate observations across successive tracking frames and executes a voting consensus algorithm. In this test, the defined consensus scenario was resolved correctly, correctly identifying the vehicle registration and associating it with the incident dossier."*

---

### Step 5 — Watchlist Face Biometrics & Human Adjudication (30 Seconds)

* **Objective:** Demonstrate face feature extraction, watchlist matching, and human-in-the-loop decision support.
* **Actions in UI:**
  1. Navigate to the **Watchlist & Biometrics** panel.
  2. Observe the facial probe crop extracted from the checkpoint camera.
  3. The system displays a watchlist match candidate: `Suspect ID: SUB-4092` with cosine similarity `0.84` (exceeding match threshold `0.75`).
  4. Point out the prominent banner:
     `[INVESTIGATIVE LEAD ONLY — OPERATOR ADJUDICATION REQUIRED]`
  5. The operator clicks **Confirm Identity & Dispatch QRT** or **Dismiss False Match**.
* **Presenter Script:**
  > *"IBVAP treats facial recognition strictly as an investigative lead to assist human sentries. It never makes automated determinations of guilt or autonomous actions. The human commander must verify the candidate match before any tactical response is initiated."*

---

### Step 6 — Cross-Camera Multi-Tower Pursuit (30 Seconds)

* **Objective:** Demonstrate person re-identification (ReID) and spatial-temporal correlation across independent camera feeds.
* **Actions in UI:**
  1. Navigate to the **GIS Tactical Map** (`/map`).
  2. Observe a person track `T-0001` exiting the field-of-view of Tower Camera 1.
  3. Ten seconds later, a new track appears on adjacent Tower Camera 2.
  4. Show the **Cross-Camera Correlation Engine** linking the two tracks into a unified `GlobalEntityDossier [GED-7701]`:
     - Spatial-Temporal Feasibility: `0.95` (distance 45m traversed in 10s = 4.5 m/s)
     - 512D ReID Appearance Cosine Similarity: `0.87`
     - Consolidated Confidence: `0.91`
  5. The map renders a continuous breadcrumb path connecting the subject across both outpost towers.
* **Presenter Script:**
  > *"When an intruder evades one camera, IBVAP uses a 512-dimensional normalized appearance embedding combined with a physical inter-camera transit topology matrix to hand off the track to adjacent outpost cameras seamlessly."*

---

### Step 7 — Offline Resilience & Zero-Data-Loss Spool Replay (30 Seconds)

* **Objective:** Demonstrate uninterrupted local edge surveillance and automatic recovery during backhaul communication cuts.
* **Actions in UI / Console:**
  1. Presenter points to the network status indicator in the top header.
  2. Presenter simulates a network fiber cut by toggling database connectivity offline:
     ```bash
     # Backend terminal simulation or UI offline toggle
     ```
  3. The UI header updates to `BOP OPERATING OFFLINE — LOCAL SPOOL ACTIVE`.
  4. Generate an intrusion incident on the edge camera:
     - Notice that the edge pipeline continues processing at full 30+ FPS without crashing or dropping frames.
     - The incident is appended locally to `data/spool/spool_cam_01.jsonl` under an OS advisory lock (`fcntl.flock`).
  5. Presenter restores network connectivity:
     - The background `spool_replay` worker detects connection restoration.
     - Spooled incidents are replayed into the central database idempotently.
     - The central command dashboard immediately syncs all queued incidents with zero data loss.
* **Presenter Script:**
  > *"Border outposts cannot rely on unbroken internet links. If the WAN fiber is severed, IBVAP continues running full AI inference locally, storing cryptographically sealed incidents to local disk. When connectivity returns, our replay worker reconciles all events idempotently with zero data loss."*

---

### Step 8 — Evidence integrity & BSA Section 63 technical alignment (30 Seconds)

* **Objective:** Demonstrate evidentiary integrity aligned with Indian statutory standards.
* **Actions in UI:**
  1. Navigate to the **Evidence Vault** (`/evidence`).
  2. Select Incident `INC-JOB70-F44-TT-0001`.
  3. Click **Generate BSA Section 63 Certificate**.
  4. The **Legal Electronic Certificate Modal** appears, displaying:
     - Statute: *Bharatiya Sakshya Adhiniyam, 2023 — Section 63 (repealing Section 65B IEA)*
     - Primary Evidence SHA-256 Digest: `7236e66947e8b9b7476b7c83bf65a29992e58ec5f40a6d48a818fdfe616955f1`
     - Device Identifier, Outpost Location, UTC Timestamp, and Certifying Officer signature field.
     - Merkle Hash Tree validation verifying that raw video frames have not been tampered with.
  5. Click **Download Sealed PDF / JSON Certificate**.
* **Presenter Script:**
  > *"Implemented technical controls aligned with Bharatiya Sakshya Adhiniyam, 2023 Section 63 requirements. Per-case statutory certificate generation remains an operational/legal prerequisite. IBVAP seals every evidentiary frame in an immutable SHA-256 Merkle chain, establishing cryptographic integrity and chain-of-custody verification."*

---

## 3. Demonstration Wrap-Up (15 Seconds)

* **Key Takeaway for Judges:**
  1. **Built for Existing Hardware:** Ingests legacy RTSP/ONVIF streams without replacing cameras.
  2. **High-Performance Edge AI:** Sub-29 ms end-to-end latency on standard CPU hardware.
  3. **Zero False Alarm Storms:** Spatial-gated RPS multiplicative scoring with full explainability.
  4. **Mission Survivability:** Autonomous edge spooling and idempotent replay during network cuts.
  5. **Forensic Integrity:** Evidence integrity & BSA Section 63 technical alignment.
