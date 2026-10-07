# IBVAP MODEL PROVENANCE & LICENSING FORENSIC AUDIT RECORD

> [!IMPORTANT]
> **DISCLAIMER:** This document constitutes an empirical technical and forensic provenance record based strictly on static inspection of repository files, embedded neural network metadata, binary headers, and verifiable upstream source repositories. This document is a legal/provenance record, NOT a formal legal opinion. No commercial rights are claimed without independent contractual documentation.

---

## 1. EXECUTIVE SUMMARY & AUDIT SCOPE

This forensic audit evaluates all neural network models and weights referenced or present in the IBVAP (Intelligent Border & Perimeter Video Analytics Platform) repository (`/Users/vivek/Downloads/ibvap`).

### Audit Methodology
1. **Physical File & Checksum Verification:** SHA-256 cryptographic hashes and byte lengths computed directly over all binary files.
2. **Embedded Metadata Inspection:** ONNX protobuf metadata (`metadata_props`, `producer_name`, `producer_version`, `model_version`, `doc_string`) and PyTorch state dict keys (`date`, `version`, `license`, `docs`, `epoch`, `model`) extracted via `onnx` and `torch` runtimes.
3. **Upstream Source & Lineage Traceability:** Lineage verified back to originating repositories (Ultralytics repository, OpenCV Zoo, or custom training notebooks).
4. **License Classification Hierarchy:**
   - **AGPL-3.0 (Copyleft):** Applies to models generated, fine-tuned, or exported via the Ultralytics framework where copyleft reciprocation applies to network services.
   - **Enterprise / Commercial Licensing Required:** Required for proprietary or commercial closed-source distribution of Ultralytics-derived artifacts.
   - **Independently Proven Permissive Upstream (OpenCV Zoo):** Models distributed under documented permissive licenses (MIT / Apache 2.0) with upstream provenance.
   - **UNKNOWN / REQUIRES LEGAL REVIEW:** Applied to any artifact lacking direct embedded or verifiable upstream legal documentation.

---

## 2. INVENTORY OF AUDITED MODELS

| # | File Path | Exact Bytes | SHA-256 Checksum | Architecture | Source / Lineage | Licensing Basis |
|---|---|---|---|---|---|---|
| 1 | `models/yolo11n.onnx` | 10,741,398 | `b05e57c570a82339816a25ce7ebf3e52cce989818ca42fdcc05589306699a2a4` | YOLO11n | Ultralytics COCO | AGPL-3.0 / Enterprise Commercial Required |
| 2 | `yolo11n.pt` | 5,613,764 | `0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1` | YOLO11n | Ultralytics Base | AGPL-3.0 / Enterprise Commercial Required |
| 3 | `models/yolo26n.onnx` | 9,942,096 | `e9a4f607f1624ffac567eef91148a1bada2c0440bdca1b01508c1bc55718757d` | YOLO26n | Ultralytics Framework | AGPL-3.0 / Enterprise Commercial Required |
| 4 | `models/yolo26n.pt` | 5,544,453 | `9b09cc8bf347f0fc8a5f7657480587f25db09b34bf33b0652110fb03a8ad4fef` | YOLO26n | Ultralytics Framework | AGPL-3.0 / Enterprise Commercial Required |
| 5 | `models/yolo26s.onnx` | 38,291,269 | `995b0854ef955b52d2ccef556db0144ea0b81262270f01d0a23cfcf07971d690` | YOLO26s | Ultralytics Framework | AGPL-3.0 / Enterprise Commercial Required |
| 6 | `models/yolo26s.pt` | 20,422,725 | `646f8bc3fe0a656803d95c294f7852321748cb29d13466a1af8862e2db384a1b` | YOLO26s | Ultralytics Framework | AGPL-3.0 / Enterprise Commercial Required |
| 7 | `models/plate_detect.onnx` | 10,481,682 | `693133a1db97a3ba1e90068986f80afb72c3fcddb681e57181a89a9a3dc351d6` | YOLO11n (LPR) | Ultralytics Fine-Tuned | AGPL-3.0 / Enterprise Commercial Required |
| 8 | `models/plate_detect.pt` | 5,465,235 | `0aec75976c56eb6f26dfb274c430620ec65137915ff1ae47c3a48c7af8afb7b2` | YOLO11n (LPR) | Ultralytics Fine-Tuned | AGPL-3.0 / Enterprise Commercial Required |
| 9 | `models/face_detection_yunet_2023mar.onnx` | 232,589 | `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4` | YuNet | OpenCV Zoo | Permissive Upstream (MIT/Apache 2.0); No Embedded Metadata |
| 10 | `models/face_recognition_sface_2021dec.onnx` | 38,696,353 | `0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79` | SFace | OpenCV Zoo | Permissive Upstream (Apache 2.0); No Embedded Metadata |

---

## 3. DETAILED FORENSIC MODEL PROVENANCE PROFILES

### 3.1. Primary Object Detection Models (Ultralytics Lineage)

#### 1. `models/yolo11n.onnx`
* **Filename:** `models/yolo11n.onnx`
* **Byte Size:** 10,741,398 bytes (~10.24 MB)
* **SHA-256 Checksum:** `b05e57c570a82339816a25ce7ebf3e52cce989818ca42fdcc05589306699a2a4`
* **Architecture:** YOLO11 Nano (`yolo11n`) 80-class detection head.
* **Export Format:** ONNX (IR Version: 8, Producer: `pytorch 2.8.0`).
* **Source / Lineage:** Ultralytics official COCO pretrained weights exported to ONNX via Ultralytics 8.4.138 on 2026-09-02T10:07:07+05:30.
* **Embedded Metadata Evidence:**
  - `description`: "Ultralytics YOLO11n model trained on /usr/src/ultralytics/ultralytics/cfg/datasets/coco.yaml"
  - `author`: "Ultralytics"
  - `license`: "AGPL-3.0 License (https://ultralytics.com/license)"
  - `docs`: "https://docs.ultralytics.com"
  - `version`: "8.4.138"
* **Whether License is Embedded:** **YES, explicitly embedded in ONNX protobuf metadata.**
* **Ultralytics Provenance:** **YES.**
* **Licensing Basis & Legal Classification:**
  - **Open-Source Baseline:** GNU Affero General Public License v3.0 (AGPL-3.0).
  - **Commercial Rights:** Not included under AGPL-3.0 without source disclosure. Commercial closed-source distribution requires an Ultralytics Enterprise License.

#### 2. `yolo11n.pt`
* **Filename:** `yolo11n.pt` (Repository root)
* **Byte Size:** 5,613,764 bytes (~5.35 MB)
* **SHA-256 Checksum:** `0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1`
* **Architecture:** YOLO11 Nano PyTorch serialized state dictionary.
* **Export Format:** PyTorch (`.pt`).
* **Source / Lineage:** Downloaded directly from Ultralytics release assets (`v8.2.100` on 2024-09-25T21:10:26).
* **Embedded Metadata Evidence:**
  - PyTorch Checkpoint Dictionary: `license`: "AGPL-3.0 License (https://ultralytics.com/license)", `version`: "8.2.100".
* **Whether License is Embedded:** **YES, explicitly embedded in PyTorch checkpoint dictionary.**
* **Ultralytics Provenance:** **YES.**
* **Licensing Basis & Legal Classification:** **AGPL-3.0 / Enterprise Commercial Required for proprietary distribution.**

---

### 3.2. Tactical Perimeter Models (YOLO26 Lineage)

#### 3. `models/yolo26n.onnx`
* **Filename:** `models/yolo26n.onnx`
* **Byte Size:** 9,942,096 bytes (~9.48 MB)
* **SHA-256 Checksum:** `e9a4f607f1624ffac567eef91148a1bada2c0440bdca1b01508c1bc55718757d`
* **Architecture:** YOLO26 Nano with end-to-end NMS head (`end2end: True`).
* **Export Format:** ONNX (IR Version: 8, Producer: `pytorch 2.8.0`).
* **Source / Lineage:** Exported via Ultralytics exporter on 2026-09-02T13:13:38+05:30 (`/home/lq/codes/ultralytics/ultralytics/cfg/datasets/coco.yaml`).
* **Embedded Metadata Evidence:**
  - `description`: "Ultralytics YOLO26n model trained on /home/lq/codes/ultralytics/ultralytics/cfg/datasets/coco.yaml"
  - `author`: "Ultralytics"
  - `license`: "AGPL-3.0 License (https://ultralytics.com/license)"
  - `version`: "8.4.138"
* **Whether License is Embedded:** **YES, explicitly embedded in ONNX protobuf metadata.**
* **Ultralytics Provenance:** **YES.**
* **Licensing Basis & Legal Classification:** **AGPL-3.0 / Enterprise Commercial Required.** (Note: Previously erroneously referenced in summary tables as "Apache-2.0 Compatible"; rectified in Gate 3.1 to factual AGPL-3.0).

#### 4. `models/yolo26n.pt`
* **Filename:** `models/yolo26n.pt`
* **Byte Size:** 5,544,453 bytes (~5.29 MB)
* **SHA-256 Checksum:** `9b09cc8bf347f0fc8a5f7657480587f25db09b34bf33b0652110fb03a8ad4fef`
* **Architecture:** YOLO26n PyTorch serialized model.
* **Export Format:** PyTorch (`.pt`).
* **Source / Lineage:** Checkpoint trained within Ultralytics framework (v8.3.222 on 2025-12-15).
* **Embedded Metadata Evidence:** `license`: "AGPL-3.0 (https://ultralytics.com/license)", `version`: "8.3.222".
* **Whether License is Embedded:** **YES, explicitly embedded.**
* **Ultralytics Provenance:** **YES.**
* **Licensing Basis & Legal Classification:** **AGPL-3.0 / Enterprise Commercial Required.**

#### 5. `models/yolo26s.onnx`
* **Filename:** `models/yolo26s.onnx`
* **Byte Size:** 38,291,269 bytes (~36.52 MB)
* **SHA-256 Checksum:** `995b0854ef955b52d2ccef556db0144ea0b81262270f01d0a23cfcf07971d690`
* **Architecture:** YOLO26 Small with end-to-end NMS head (`end2end: True`).
* **Export Format:** ONNX (IR Version: 8, Producer: `pytorch 2.8.0`).
* **Source / Lineage:** Exported via Ultralytics exporter on 2026-09-02T13:13:46+05:30.
* **Embedded Metadata Evidence:**
  - `description`: "Ultralytics YOLO26s model trained on /home/lq/codes/ultralytics/ultralytics/cfg/datasets/coco.yaml"
  - `author`: "Ultralytics"
  - `license`: "AGPL-3.0 License (https://ultralytics.com/license)"
  - `version`: "8.4.138"
* **Whether License is Embedded:** **YES, explicitly embedded.**
* **Ultralytics Provenance:** **YES.**
* **Licensing Basis & Legal Classification:** **AGPL-3.0 / Enterprise Commercial Required.**

#### 6. `models/yolo26s.pt`
* **Filename:** `models/yolo26s.pt`
* **Byte Size:** 20,422,725 bytes (~19.48 MB)
* **SHA-256 Checksum:** `646f8bc3fe0a656803d95c294f7852321748cb29d13466a1af8862e2db384a1b`
* **Architecture:** YOLO26s PyTorch serialized checkpoint.
* **Export Format:** PyTorch (`.pt`).
* **Source / Lineage:** Checkpoint trained within Ultralytics framework (v8.3.222 on 2026-01-05).
* **Embedded Metadata Evidence:** `license`: "AGPL-3.0 (https://ultralytics.com/license)", `version`: "8.3.222".
* **Whether License is Embedded:** **YES, explicitly embedded.**
* **Ultralytics Provenance:** **YES.**
* **Licensing Basis & Legal Classification:** **AGPL-3.0 / Enterprise Commercial Required.**

---

### 3.3. ANPR / HSRP License Plate Recognition Models

#### 7. `models/plate_detect.onnx`
* **Filename:** `models/plate_detect.onnx`
* **Byte Size:** 10,481,682 bytes (~10.00 MB)
* **SHA-256 Checksum:** `693133a1db97a3ba1e90068986f80afb72c3fcddb681e57181a89a9a3dc351d6`
* **Architecture:** YOLO11n fine-tuned single-class (`0: 'License_Plate'`) detector.
* **Export Format:** ONNX (IR Version: 9, Producer: `pytorch 2.6.0`).
* **Source / Lineage:** Fine-tuned in Google Colab (`/content/drive/MyDrive/Colab Notebooks/Computer Vision Workshop/LPR Detection/License Plate Detection...`) using Ultralytics YOLO framework on 2025-05-02T11:36:07.
* **Embedded Metadata Evidence:**
  - `description`: "Ultralytics YOLO11n model trained on /content/drive/MyDrive/Colab Notebooks/Computer Vision Workshop/LPR Detection/License Plate Detection..."
  - `author`: "Ultralytics"
  - `license`: "AGPL-3.0 License (https://ultralytics.com/license)"
  - `version`: "8.3.123"
* **Whether License is Embedded:** **YES, explicitly embedded in ONNX protobuf metadata.**
* **Ultralytics Provenance:** **YES (fine-tuned derivative of Ultralytics YOLO11n).**
* **Licensing Basis & Legal Classification:** **AGPL-3.0 / Enterprise Commercial Required.** (Rectified in Gate 3.1 from prior inaccurate "Apache-2.0 Compatible" label).

#### 8. `models/plate_detect.pt`
* **Filename:** `models/plate_detect.pt`
* **Byte Size:** 5,465,235 bytes (~5.21 MB)
* **SHA-256 Checksum:** `0aec75976c56eb6f26dfb274c430620ec65137915ff1ae47c3a48c7af8afb7b2`
* **Architecture:** YOLO11n fine-tuned PyTorch checkpoint.
* **Export Format:** PyTorch (`.pt`).
* **Source / Lineage:** Checkpoint produced during Colab training run on 2025-05-01T16:00:17 using Ultralytics 8.3.122.
* **Embedded Metadata Evidence:** `license`: "AGPL-3.0 (https://ultralytics.com/license)", `version`: "8.3.122".
* **Whether License is Embedded:** **YES, explicitly embedded.**
* **Ultralytics Provenance:** **YES.**
* **Licensing Basis & Legal Classification:** **AGPL-3.0 / Enterprise Commercial Required.**

---

### 3.4. Biometric & Face Recognition Models (OpenCV Zoo Lineage)

#### 9. `models/face_detection_yunet_2023mar.onnx`
* **Filename:** `models/face_detection_yunet_2023mar.onnx`
* **Byte Size:** 232,589 bytes (~0.22 MB)
* **SHA-256 Checksum:** `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`
* **Architecture:** YuNet lightweight CNN face detector (developed by Shiqi Yu / Shenzhen Key Laboratory of Visual Information Processing).
* **Export Format:** ONNX (IR Version: 6, Producer: `pytorch 1.7`).
* **Source / Lineage:** Sourced from official OpenCV Model Zoo (`opencv/opencv_zoo/models/face_detection_yunet`).
* **Embedded Metadata Evidence:** ONNX `metadata_props` dictionary is **EMPTY**. No license string is embedded inside the ONNX protobuf itself.
* **Whether License is Embedded:** **NO.**
* **Documented Upstream License:** The upstream OpenCV Zoo repository distributes YuNet under the MIT License / Apache 2.0 (based on libfacedetection).
* **Ultralytics Provenance:** **NO (independent open-source project).**
* **Licensing Basis & Legal Classification:**
  - **Documented Upstream:** Permissive (MIT / Apache 2.0 per OpenCV Zoo).
  - **Binary Embedding Status:** Unlicensed at binary container level.
  - **Commercial Classification:** Permissive / Commercial use permitted under upstream terms; NOT an Ultralytics derivative.

#### 10. `models/face_recognition_sface_2021dec.onnx`
* **Filename:** `models/face_recognition_sface_2021dec.onnx`
* **Byte Size:** 38,696,353 bytes (~36.90 MB)
* **SHA-256 Checksum:** `0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79`
* **Architecture:** SFace deep convolutional face recognition network (128D unit hypersphere embedding, developed by Zhong et al.).
* **Export Format:** ONNX (IR Version: 6).
* **Source / Lineage:** Sourced from official OpenCV Model Zoo (`opencv/opencv_zoo/models/face_recognition_sface`).
* **Embedded Metadata Evidence:** ONNX `metadata_props` dictionary is **EMPTY**. No license string is embedded inside the ONNX protobuf.
* **Whether License is Embedded:** **NO.**
* **Documented Upstream License:** OpenCV Zoo repository is released under Apache 2.0.
* **Ultralytics Provenance:** **NO (independent open-source project).**
* **Licensing Basis & Legal Classification:**
  - **Documented Upstream:** Permissive (Apache 2.0 per OpenCV Zoo).
  - **Binary Embedding Status:** Unlicensed at binary container level.
  - **Commercial Classification:** Permissive / Commercial use permitted under upstream terms; NOT an Ultralytics derivative.

---

## 4. CROSS-GATE TRUTH RECONCILIATION FINDINGS

1. **Prior Misattribution Rectified:**
   - In earlier Gate 2/Gate 3 draft reports, `yolo26n.onnx`, `yolo26s.onnx`, and `plate_detect.onnx` were described as "Apache-2.0 Compatible".
   - Forensic binary extraction demonstrated that all three contain embedded metadata explicitly stating:
     `license: AGPL-3.0 License (https://ultralytics.com/license)`
     `author: Ultralytics`
   - All documentation has been updated to reflect the factual AGPL-3.0 licensing status.
2. **Commercial Deployment Requirements:**
   - For educational, prototype, or open-source air-gapped evaluation (such as SIH 2026), AGPL-3.0 permits execution and distribution with reciprocal open-source compliance.
   - For commercial, proprietary closed-source deployment or government defense procurement that prohibits AGPL copyleft reciprocation, an **Ultralytics Enterprise / Commercial License** is a prerequisite.
3. **OpenCV Zoo Models:**
   - YuNet and SFace are confirmed to originate from OpenCV Zoo under permissive terms (MIT / Apache 2.0) and do not impose copyleft reciprocation.
