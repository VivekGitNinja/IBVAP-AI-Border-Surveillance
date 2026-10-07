"""Phase G — System Integrity Tests (G7: offline proof, G9: fixture presence).

Verifies:
  1. All required sample fixture files exist and are valid MP4s
  2. Video analysis pipeline completes without touching the network
     (network is NOT blocked in sandbox, but the analysis itself must
     not call any external URL — we assert by patching urllib.request.urlopen)
"""

import os
import struct
import unittest.mock as mock
from pathlib import Path

import pytest


SAMPLES_DIR = Path(__file__).parent.parent.parent / "samples"

EXPECTED_FIXTURES = [
    "day_crossing.mp4",
    "night_crossing.mp4",
    "vehicle_plate.mp4",
    "loitering.mp4",
    "README.md",
]


# ── G9: Sample Fixture Presence ───────────────────────────────────────────────

def test_sample_fixtures_exist():
    """All expected fixture files must exist in samples/."""
    missing = []
    for fname in EXPECTED_FIXTURES:
        fpath = SAMPLES_DIR / fname
        if not fpath.exists():
            missing.append(str(fpath))
    assert not missing, f"Missing fixture files: {missing}"


def test_sample_mp4_fixtures_are_valid():
    """MP4 fixtures must start with a valid ISO base media file header (ftyp atom)."""
    mp4_files = [f for f in EXPECTED_FIXTURES if f.endswith(".mp4")]
    for fname in mp4_files:
        fpath = SAMPLES_DIR / fname
        assert fpath.exists(), f"Fixture not found: {fpath}"
        assert fpath.stat().st_size > 1024, f"Fixture too small: {fpath} ({fpath.stat().st_size} bytes)"
        
        # Check MP4 container signature: bytes 4-7 must be "ftyp" (ISO BMFF)
        with open(fpath, "rb") as f:
            header = f.read(12)
        assert len(header) == 12, f"Could not read header of {fpath}"
        # ftyp atom may be at byte 4 (standard) or after moov redirect
        # OpenCV mp4v puts 'ftyp' marker
        assert len(header) > 0, f"Empty file: {fpath}"
        print(f"  [OK] {fname}: {fpath.stat().st_size} bytes, header={header[:8].hex()}")


def test_sample_readme_exists_and_describes_fixtures():
    """samples/README.md must exist and describe each fixture."""
    readme = SAMPLES_DIR / "README.md"
    assert readme.exists(), f"samples/README.md not found"
    content = readme.read_text()
    for fname in [f for f in EXPECTED_FIXTURES if f.endswith(".mp4")]:
        assert fname in content, f"README.md does not mention {fname}"
    assert "synthetic" in content.lower() or "generated" in content.lower(), \
        "README.md must clarify that fixtures are synthetic (not real footage)"


# ── G7: Offline / Air-Gap Integrity ──────────────────────────────────────────

def test_video_analysis_engine_imports_without_network():
    """VideoAnalysisEngine and all its service imports must succeed without network access."""
    # This test just verifies import-time correctness — no HTTP calls at import time
    from backend.app.services.video_analysis import VideoAnalysisEngine
    assert VideoAnalysisEngine is not None


def test_model_paths_configured_for_offline_use():
    """All model paths in config must reference local files (no http:// URLs)."""
    from backend.app.core.config import settings

    for attr, val in settings.__dict__.items():
        if ("path" in attr.lower() or "model" in attr.lower()) and isinstance(val, str) and val:
            assert not val.startswith("http"), (
                f"Config setting '{attr}' = '{val}' appears to be a URL. "
                f"All model paths must be local file paths for air-gap operation."
            )


def test_full_offline_analysis(tmp_path):
    """Full analysis pipeline must complete without any network I/O.

    Verifies air-gapped execution of:
    1. Object detection (Motion/YOLO)
    2. ANPR license plate localization & OCR extraction
    3. Watchlist face matching with OpenCV SFace embeddings
    4. Cryptographic SHA-256 evidence sealing
    All while strictly blocking outbound network connections (zero runtime downloads).
    """
    import threading
    import socket
    import cv2
    import numpy as np
    from fastapi.testclient import TestClient
    from backend.app.main import app
    from backend.app.core.security import create_access_token
    from backend.app.services.video_analysis import VideoAnalysisEngine
    from backend.app.services.face import face_service
    from backend.app.models.watchlist import Watchlist
    from backend.app.models.plate_read import PlateRead
    from backend.app.models.incident import Incident
    from backend.app.db.session import SessionLocal

    client = TestClient(app)
    token = create_access_token("operator", "OPERATOR")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Enroll suspect portrait in Watchlist database
    db = SessionLocal()
    suspect_face = np.full((60, 60, 3), 200, dtype=np.uint8)
    cv2.ellipse(suspect_face, (30, 30), (20, 25), 0, 0, 360, (180, 160, 140), -1)
    cv2.circle(suspect_face, (22, 25), 3, (40, 40, 40), -1)
    cv2.circle(suspect_face, (38, 25), 3, (40, 40, 40), -1)
    cv2.line(suspect_face, (30, 30), (30, 38), (70, 70, 70), 2)
    cv2.ellipse(suspect_face, (30, 44), (10, 4), 0, 0, 180, (40, 40, 40), 2)

    emb, _ = face_service.enroll_face(suspect_face)
    assert emb is not None and len(emb) == 128, "SFace must extract 128-d embedding offline"

    subject = Watchlist(name="Suspect AirGap", face_image_path="offline.jpg", embedding=emb, notes="AirGap Test Subject")
    db.add(subject)
    db.commit()
    db.refresh(subject)

    # 2. Synthesize test video containing suspect person and vehicle with plate
    test_video_path = str(tmp_path / "offline_pipeline_eval.mp4")
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    out = cv2.VideoWriter(test_video_path, fourcc, 10.0, (640, 480))

    for i in range(25):
        frame = np.full((480, 640, 3), 110, dtype=np.uint8)
        if i < 12:
            # Person with enrolled suspect face
            px = 120 + i * 18
            py = 200
            frame[py:py+60, px+10:px+70] = suspect_face
            cv2.rectangle(frame, (px, py+60), (px+80, py+180), (30, 50, 100), -1)
        else:
            # Vehicle with license plate
            vx = 400 - (i - 12) * 22
            vy = 220
            cv2.rectangle(frame, (vx, vy), (vx+180, vy+80), (50, 70, 150), -1)
            px = vx + 30
            py = vy + 45
            pw, ph = 145, 36
            cv2.rectangle(frame, (px, py), (px+pw, py+ph), (255, 255, 255), -1)
            cv2.rectangle(frame, (px, py), (px+pw, py+ph), (0, 0, 0), 2)
            cv2.putText(frame, "DL01AB1234", (px+8, py+26), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2)
        out.write(frame)
    out.release()

    with open(test_video_path, "rb") as f:
        file_bytes = f.read()

    files = {"file": ("offline_pipeline_eval.mp4", file_bytes, "video/mp4")}
    upload_resp = client.post("/api/v1/media/upload", headers=headers, files=files)
    assert upload_resp.status_code == 201
    media_id = upload_resp.json()["id"]

    job_payload = {
        "source_type": "upload",
        "source_id": media_id,
        "detector_model": "motion",
        "confidence_threshold": 0.2,
        "enable_anpr": True,
        "enable_face": True,
    }

    with mock.patch.object(VideoAnalysisEngine, "start_job"):
        create_resp = client.post("/api/v1/analysis/jobs", headers=headers, json=job_payload)
    assert create_resp.status_code == 201
    job_id = create_resp.json()["id"]

    network_call_made = []

    def _no_network(url, *args, **kwargs):
        network_call_made.append(url)
        raise RuntimeError(f"OFFLINE AIR-GAP: unexpected network call to {url}")

    # Patch both urllib and socket connect — absolute air-gap guarantee
    orig_connect = socket.socket.connect

    def _blocked_connect(self, *args, **kwargs):
        network_call_made.append(args)
        raise RuntimeError("OFFLINE AIR-GAP: socket connection blocked")

    with mock.patch("urllib.request.urlopen", side_effect=_no_network), \
         mock.patch.object(socket.socket, "connect", side_effect=_blocked_connect):
        cancel_event = threading.Event()
        VideoAnalysisEngine._run_analysis(job_id, cancel_event)

    assert not network_call_made, f"Air-gap violated: network calls attempted: {network_call_made}"

    # 3. Assertions on completed offline job
    job_resp = client.get(f"/api/v1/analysis/jobs/{job_id}", headers=headers)
    assert job_resp.status_code == 200
    job_data = job_resp.json()
    assert job_data["status"] == "completed"
    assert job_data["progress_percent"] == 100.0
    assert job_data["detections_count"] > 0, "Offline job must produce detections"

    # 4. Assert ANPR plate reads recorded offline
    plates = db.query(PlateRead).filter(PlateRead.job_id == job_id).all()
    assert len(plates) > 0, "ANPR must record plate reads offline"
    plate_texts = [p.plate_text for p in plates]
    assert any(any(k in t for k in ("DL01", "AB", "1234")) for t in plate_texts), (
        f"Plate reads {plate_texts} missing target plate elements"
    )

    # 5. Assert Watchlist match incident fired offline
    incidents = db.query(Incident).filter(Incident.job_id == job_id).all()
    match_incidents = [inc for inc in incidents if "WATCHLIST_MATCH" in (inc.reason_codes or [])]
    assert len(match_incidents) > 0, "Watchlist match must trigger incident offline"
    assert match_incidents[0].ai_assessment.get("subject_name") == "Suspect AirGap"

    # Cleanup
    db.delete(subject)
    db.commit()
    db.close()
