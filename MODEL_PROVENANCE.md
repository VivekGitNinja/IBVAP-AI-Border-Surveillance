# IBVAP — Model Provenance & Licensing Register
## Smart India Hackathon (SIH 2026) — Problem Statement SIH26187

---

## 1. Compliance Statement & Legal Disclaimer

This document records the exact provenance, binary checksums, origin lineage, and legal licensing terms for all artificial intelligence and neural network model weights packaged with or utilized by the Intelligent Border Video Analytics Platform (IBVAP).

> [!IMPORTANT]
> **Legal Disclaimer:**  
> This register is an empirical technical and provenance record based on binary file inspection, embedded protobuf metadata, and upstream source repository analysis. It is **NOT** a formal legal opinion. No commercial closed-source distribution rights are claimed for copyleft artifacts without an independent enterprise commercial license agreement.

---

## 2. Complete Model Inventory & Provenance Table

| Model Name | Version / Shipped File | Source / Lineage | Upstream License | Usage Restrictions | SIH 2026 Submission Note |
|---|---|---|---|---|---|
| **YOLO11n (ONNX)** | `models/yolo11n.onnx`<br>(10,741,398 bytes)<br>SHA-256: `b05e57c5...` | Ultralytics official COCO pretrained weights exported to ONNX via Ultralytics 8.4.138. | **AGPL-3.0** (embedded in ONNX protobuf metadata). | Non-commercial / academic use permitted under AGPL-3.0 copyleft. Commercial closed-source redistribution requires Ultralytics Enterprise License. | Shipped as primary lightweight general object detector for commodity CPU edge appliances. |
| **YOLO11n (PyTorch)** | `yolo11n.pt`<br>(5,613,764 bytes)<br>SHA-256: `0ebbc80d...` | Ultralytics release assets (`v8.2.100`). | **AGPL-3.0** (embedded in PyTorch checkpoint dictionary). | Copyleft reciprocation applies to network services. Enterprise commercial license required for proprietary use. | Base PyTorch weights used for offline export, fine-tuning, and testing. |
| **YOLO26n (ONNX)** | `models/yolo26n.onnx`<br>(9,942,096 bytes)<br>SHA-256: `e9a4f607...` | Ultralytics framework export with integrated end-to-end NMS head (`end2end: True`). | **AGPL-3.0** (embedded in ONNX protobuf metadata). | AGPL-3.0 terms apply. Cannot be redistributed as closed-source proprietary software. | Shipped as tactical perimeter detector offering enhanced CPU throughput (~19.4 ms inference). |
| **YOLO26n (PyTorch)** | `models/yolo26n.pt`<br>(5,544,453 bytes)<br>SHA-256: `9b09cc8b...` | Ultralytics framework serialized state dictionary. | **AGPL-3.0** (embedded in checkpoint dictionary). | AGPL-3.0 copyleft terms apply. | PyTorch checkpoint for tactical perimeter model. |
| **YOLO26s (ONNX)** | `models/yolo26s.onnx`<br>(38,291,269 bytes)<br>SHA-256: `995b0854...` | Ultralytics framework small-model variant export. | **AGPL-3.0** (embedded in ONNX protobuf metadata). | AGPL-3.0 copyleft terms apply. Enterprise commercial license required for proprietary deployment. | Higher-accuracy model variant for edge nodes with GPU acceleration (Jetson/CUDA). |
| **YOLO26s (PyTorch)** | `models/yolo26s.pt`<br>(20,422,725 bytes)<br>SHA-256: `646f8bc3...` | Ultralytics framework serialized state dictionary. | **AGPL-3.0** (embedded in checkpoint dictionary). | AGPL-3.0 copyleft terms apply. | PyTorch checkpoint for small-variant model. |
| **Plate Detect (ONNX)** | `models/plate_detect.onnx`<br>(10,481,682 bytes)<br>SHA-256: `693133a1...` | YOLO-derived vehicle license plate detector fine-tuned for vehicle checkpoint lanes. | **AGPL-3.0** (derived from Ultralytics framework). | Inherits AGPL-3.0 copyleft obligations from base architecture. Enterprise license required for commercial packaging. | Shipped for automated license plate localization and bounding box extraction. |
| **Plate Detect (PyTorch)** | `models/plate_detect.pt`<br>(5,465,235 bytes)<br>SHA-256: `0aec7597...` | YOLO-derived serialized state dictionary for plate detection. | **AGPL-3.0** (derived from Ultralytics framework). | Inherits AGPL-3.0 copyleft obligations. | PyTorch source weights for vehicle plate localization. |
| **YuNet Face Detector** | `models/face_detection_yunet_2023mar.onnx`<br>(232,589 bytes)<br>SHA-256: `8f2383e4...` | OpenCV Model Zoo (`opencv_zoo/models/face_detection_yunet`). | **MIT / Apache 2.0 Permissive** (OpenCV upstream distribution). | Permissive academic and commercial use permitted with copyright notice attribution. | Shipped for lightweight, high-speed facial crop detection prior to feature embedding. |
| **SFace Face Recognizer** | `models/face_recognition_sface_2021dec.onnx`<br>(38,696,353 bytes)<br>SHA-256: `0ba9fbfa...` | OpenCV Model Zoo (`opencv_zoo/models/face_recognition_sface`). | **Apache 2.0 Permissive** (OpenCV upstream distribution). | Permissive academic and commercial use permitted with standard Apache 2.0 terms. | Shipped for 128D facial feature embedding extraction against authorized watchlists. |

---

## 3. Detailed Licensing & Distribution Terms

### 3.1. Ultralytics Lineage Models (YOLO11, YOLO26, Plate Detect)
* **Author / Copyright Holder:** Ultralytics Inc.
* **Upstream License:** GNU Affero General Public License v3.0 (AGPL-3.0).
* **Copyleft Trigger:** Distributing or running network services based on AGPL-3.0 code requires making the complete source code available under compatible open-source terms.
* **Commercial Status:** IBVAP distributes all source code openly under the SIH 2026 hackathon terms. If government agencies or defense contractors intend to incorporate these models into closed-source, proprietary weapon systems or proprietary commercial products, an **Ultralytics Enterprise Commercial License** must be acquired directly from Ultralytics Inc.
* **No Unrestricted Proprietary Asset Claim:** The development team does **NOT** claim proprietary ownership of the underlying YOLO architectures or base weights.

### 3.2. OpenCV Zoo Models (YuNet & SFace)
* **Author / Copyright Holder:** OpenCV Development Team & Authors.
* **Upstream License:** Apache License 2.0 / MIT License.
* **Commercial Status:** Permissive open-source license allows commercial redistribution and incorporation into proprietary software provided copyright notices are preserved.
* **Embedded Metadata:** Inspected via ONNX runtime; no contradictory embedded proprietary tags detected.

---

## 4. Verification Checksums

All model files in the `models/` directory were verified on disk on September 21, 2026:

```bash
# Verify integrity of packaged models
sha256sum models/* yolo11n.pt
```

```text
8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4  models/face_detection_yunet_2023mar.onnx
0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79  models/face_recognition_sface_2021dec.onnx
693133a1db97a3ba1e90068986f80afb72c3fcddb681e57181a89a9a3dc351d6  models/plate_detect.onnx
0aec75976c56eb6f26dfb274c430620ec65137915ff1ae47c3a48c7af8afb7b2  models/plate_detect.pt
b05e57c570a82339816a25ce7ebf3e52cce989818ca42fdcc05589306699a2a4  models/yolo11n.onnx
e9a4f607f1624ffac567eef91148a1bada2c0440bdca1b01508c1bc55718757d  models/yolo26n.onnx
9b09cc8bf347f0fc8a5f7657480587f25db09b34bf33b0652110fb03a8ad4fef  models/yolo26n.pt
995b0854ef955b52d2ccef556db0144ea0b81262270f01d0a23cfcf07971d690  models/yolo26s.onnx
646f8bc3fe0a656803d95c294f7852321748cb29d13466a1af8862e2db384a1b  models/yolo26s.pt
0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1  yolo11n.pt
```
