# IBVAP — Known Technical Limitations & Deployment Prerequisites
## Smart India Hackathon (SIH 2026) — Problem Statement SIH26187

---

## 1. Purpose & Scope of Disclosure

In strict adherence to scientific and engineering integrity, this document explicitly details all known operational, technical, and environmental limitations of the Intelligent Border Video Analytics Platform (IBVAP).

IBVAP is a high-performance, edge-first prototype engineered for the Smart India Hackathon 2026. While all 733 backend verification tests and frontend components are functional and verified in controlled environments, the platform must not be represented as an off-the-shelf, plug-and-play defense asset ready for unsupervised operational combat deployment.

---

## 2. Inventory of Known Limitations

### 2.1. Lack of Live International Border Field Validation
* **Status:** `NOT FIELD VALIDATED`.
* **Details:** The platform has been validated through rigorous simulated laboratory test suites, synthetic camera streams, offline video files, and controlled edge testbeds. It has **NOT** undergone active field evaluation on an international border (such as the Indo-Nepal or Indo-Bhutan borders) under live tactical conditions with Sashastra Seema Bal (SSB) units.

### 2.2. Camera & Firmware Device Interoperability
* **Status:** `ENVIRONMENT-DEPENDENT`.
* **Details:** IBVAP implements a vendor-neutral RTSP and ONVIF Profile S/T adapter layer. However, individual camera manufacturers (Hikvision, Dahua, Axis, CP Plus, Hanwha, Uniview) frequently implement proprietary authentication extensions, non-standard RTSP keep-alives, non-conformant ONVIF SOAP responses, or non-standard H.264 NAL unit headers.  
* **Rule:** Hardware interoperability requires device-specific validation for every camera model deployed in the field.

### 2.3. Optical & Environmental AI Perception Sensitivity
* **Status:** `ENVIRONMENT-DEPENDENT`.
* **Details:**
  * Optical deep learning detection (YOLO) degrades under extreme weather conditions, including dense zero-visibility fog, blinding sandstorms, torrential monsoon downpours, severe backlighting / lens flare, and direct mud/water splatter on camera lenses.
  * Adaptive CLAHE significantly improves local contrast in low-light environments (< 45 lux), but it cannot recover visual information from absolute darkness (0 lux) without active infrared (IR) illumination or thermal imaging.

### 2.4. Facial Biometrics Environmental Dependencies
* **Status:** `ENVIRONMENT-DEPENDENT` & `RESIDUAL RISK`.
* **Details:**
  * Facial recognition (YuNet / SFace) requires targets to face within $\pm 30^\circ$ of camera yaw and pitch.
  * Minimum inter-pupillary distance must exceed 60 pixels for reliable embedding generation.
  * Severe off-angle faces, low illumination, motion blur, hoods, balaclavas, or deliberate disguise will degrade matching confidence.
  * Facial matching is strictly treated as an investigative lead and requires mandatory human operator adjudication.

### 2.5. ANPR Plate Visibility & Resolution Constraints
* **Status:** `ENVIRONMENT-DEPENDENT`.
* **Details:**
  * Multi-frame voting consensus improves plate recognition reliability, but OCR accuracy remains fundamentally dependent on camera mounting angle ($< 30^\circ$ to vehicle path), vehicle velocity, plate cleanliness, and adequate vertical pixel height ($\ge 25$ pixels).
  * Non-standard, damaged, faded, or heavily mud-splattered license plates will fail automatic OCR extraction.

### 2.6. Cross-Camera Tracking Dependence on Topology Calibration
* **Status:** `ENVIRONMENT-DEPENDENT`.
* **Details:**
  * Multi-camera handoff and re-identification (ReID) rely on a pre-calibrated inter-camera transit time matrix and physical topology distance.
  * If a subject pauses, hides, or takes an unpredictable detour between camera fields of view exceeding the expected temporal transit window, cross-camera correlation confidence will drop.
  * Cross-camera appearance matching across drastic lighting differences (e.g., direct sunlight camera to deep shadow camera) requires on-site color normalization.

### 2.7. Hardware Reference Dependencies & Compute Scaling
* **Status:** `MEASURED` on reference hardware.
* **Details:**
  * Published latency benchmarks (28.41 ms night, 19.53 ms daylight) were measured on a reference Apple Silicon / modern x86_64 workstation.
  * Running on lower-powered edge processors (e.g., quad-core ARM Cortex-A53 or entry-level NVRs) will increase per-frame latency and reduce aggregate frame rates unless hardware accelerators (NVIDIA Jetson, Intel OpenVINO, Hailo-8) are integrated.

### 2.8. Thermal & Drone Sensor Simulation Disclosure
* **Status:** `IMPLEMENTED` as simulation.
* **Details:**
  * In the current prototype demonstration, thermal video feeds and drone aerial views utilize algorithmic colormaps and simulated telemetry overlays.
  * Physical radiometric thermal sensors (FLIR/Boson) and genuine MAVLink drone telemetry streams require physical hardware drivers during deployment.
  * The frontend explicitly displays: `[SIMULATED TELEMETRY & POST-PROCESS COLORMAP - SENSOR SIMULATION]`.

### 2.9. Bharatiya Sakshya Adhiniyam, 2023 (BSA) Technical Alignment vs. Legal Certification
* **Status:** `IMPLEMENTED` technical controls.
* **Details:**
  * IBVAP implements cryptographic SHA-256 hash chaining, Merkle trees, and automated Section 63 electronic record generation.
  * However, software cannot grant automatic legal admissibility by itself. Under Indian law, Section 63 admissibility requires a human certifying officer who had lawful control of the recording computer system to review and sign the certificate for court presentation.

---

## 3. Production Deployment Prerequisites

Before IBVAP can transition from prototype to operational field deployment across Border Outposts, the following engineering steps must be completed:

1. **Physical Site Survey & Camera Calibration:** Measure physical distances, camera mounting heights, tilt angles, and dead zones; calibrate GIS pixel-to-meter matrices.
2. **Industrial Edge Ruggedization:** Package edge nodes in IP67-rated, fanless, surge-protected industrial enclosures capable of operating in temperatures from $-20^\circ\text{C}$ to $+55^\circ\text{C}$.
3. **Isolated Tactical Network Architecture:** Separate camera video streams onto physically isolated, non-routable camera VLANs with 802.1X port security to prevent rogue physical taps.
4. **Hardware-Specific Interoperability Testing:** Test all specific camera makes and firmware builds present at the target BOP against the adapter suite.
5. **Operational SOP Alignment:** Train border sentries and commanders on the Threat Priority Scoring breakdown, human-in-the-loop review protocols, and forensic evidence custody export.
