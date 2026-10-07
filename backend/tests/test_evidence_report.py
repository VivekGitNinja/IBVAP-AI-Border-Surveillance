"""Tests for Evidence Verification, System Readiness, and Forensic Reporting (Phase 4)."""

import os
import hashlib
from datetime import datetime
import cv2
import numpy as np
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.models.evidence import Evidence
from backend.app.models.incident import Incident
from backend.app.models.analysis_job import AnalysisJob
from backend.app.models.detection import Detection
from backend.app.services.evidence import transcode_and_seal_clip
from backend.app.core.security import create_access_token
from backend.app.db.session import SessionLocal

client = TestClient(app)


def auth_headers(role="ADMIN"):
    token = create_access_token("test-admin", role)
    return {"Authorization": f"Bearer {token}"}


def test_evidence_verify_endpoint(tmp_path):
    """Verify SHA-256 evidence integrity verification endpoint against disk file."""
    db = SessionLocal()
    try:
        # Create a real test evidence file on disk
        test_file = tmp_path / "test_evidence.jpg"
        test_content = b"EVIDENCE-IMAGE-DATA-FORENSIC-SEAL-2026"
        test_file.write_bytes(test_content)
        expected_sha = hashlib.sha256(test_content).hexdigest()

        # Insert Incident and Evidence row
        inc = Incident(
            incident_code="INC-TEST-VERIFY-01",
            title="Test Perimeter Alert",
            severity="HIGH",
            threat_score=85.0,
            status="OPEN",
        )
        db.add(inc)
        db.commit()
        db.refresh(inc)

        ev = Evidence(
            incident_id=inc.id,
            evidence_type="snapshot",
            file_path=str(test_file),
            sha256=expected_sha,
            manifest_path=str(test_file) + ".json",
            file_size_bytes=len(test_content),
            threat_score=85.0,
        )
        db.add(ev)
        db.commit()
        db.refresh(ev)

        # 1. Verification when file is intact
        resp = client.get(f"/api/v1/evidence/{ev.id}/verify", headers=auth_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert data["match"] is True
        assert data["valid"] is True
        assert data["stored_hash"] == expected_sha
        assert data["computed_hash"] == expected_sha
        assert "Bharatiya Sakshya Adhiniyam, 2023 §63" in data["statutory_compliance"]

        # 2. Verification when file is tampered with
        test_file.write_bytes(b"TAMPERED-UNAUTHORIZED-EDIT")
        resp_tampered = client.get(f"/api/v1/evidence/{ev.id}/verify", headers=auth_headers())
        assert resp_tampered.status_code == 200
        tampered_data = resp_tampered.json()
        assert tampered_data["match"] is False
        assert tampered_data["valid"] is False

        # Clean up
        db.delete(ev)
        db.delete(inc)
        db.commit()
    finally:
        db.close()


def test_system_readiness_endpoint():
    """Verify GET /api/v1/system/readiness reports components without runtime downloads."""
    resp = client.get("/api/v1/system/readiness", headers=auth_headers())
    assert resp.status_code == 200
    data = resp.json()

    assert "status" in data
    assert data["zero_runtime_downloads"] is True
    assert data["offline_ready"] is True

    comps = data["components"]
    for required_comp in ["db", "redis", "ffmpeg", "detector", "face", "anpr", "ocr"]:
        assert required_comp in comps
        assert "status" in comps[required_comp]
        assert comps[required_comp]["status"] in ("CACHED", "MISSING", "UNAVAILABLE", "READY")

    # Detector must be CACHED or FALLBACK
    assert comps["detector"]["status"] in ("CACHED", "MISSING")


def test_analysis_report_export_json_and_pdf():
    """Verify GET /api/v1/analysis/jobs/{id}/report in JSON and PDF formats."""
    db = SessionLocal()
    try:
        # Create completed AnalysisJob
        job = AnalysisJob(
            source_type="media",
            source_id=1,
            status="completed",
            detector_model="yolo11n",
            total_frames=100,
            processed_frames=100,
            fps=25.0,
            detections_count=5,
            incidents_count=1,
            summary={
                "total_frames": 100,
                "duration_seconds": 4.0,
                "is_night": False,
                "night_frames": 0,
                "track_summaries": [{"track_id": "1", "class_name": "person", "max_speed": 45.0}],
            },
            created_by="test_officer",
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        # Add detection and incident
        det = Detection(
            job_id=job.id,
            track_id="1",
            label="person",
            confidence=0.88,
            bbox_x1=10, bbox_y1=10, bbox_x2=50, bbox_y2=100,
            frame_index=10,
            source="yolo11n",
        )
        db.add(det)

        inc = Incident(
            incident_code=f"INC-JOB{job.id}-TEST-01",
            title="Border Sector Intrusion",
            severity="CRITICAL",
            threat_score=92.0,
            confidence=0.88,
            status="OPEN",
            job_id=job.id,
            zone_name="Sector Alpha",
            track_ids=["1"],
        )
        db.add(inc)
        db.commit()
        db.refresh(inc)

        ev = Evidence(
            incident_id=inc.id,
            evidence_type="snapshot",
            file_path="data/evidence/test.jpg",
            sha256="4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
            manifest_path="data/evidence/test.json",
            file_size_bytes=1024,
            threat_score=92.0,
        )
        db.add(ev)
        db.commit()

        # 1. Query JSON format
        resp_json = client.get(f"/api/v1/analysis/jobs/{job.id}/report?format=json", headers=auth_headers())
        assert resp_json.status_code == 200
        rpt = resp_json.json()
        assert rpt["job"]["id"] == job.id
        assert "bsa_section_63_certificate" in rpt
        assert "Bharatiya Sakshya Adhiniyam, 2023 — Section 63" in rpt["bsa_section_63_certificate"]["statute"]
        assert len(rpt["incidents"]) >= 1
        assert len(rpt["evidence"]) >= 1

        # 2. Query PDF format
        resp_pdf = client.get(f"/api/v1/analysis/jobs/{job.id}/report?format=pdf", headers=auth_headers())
        assert resp_pdf.status_code == 200
        assert resp_pdf.headers["content-type"] == "application/pdf"
        pdf_bytes = resp_pdf.content
        assert pdf_bytes.startswith(b"%PDF-")
        assert b"%%EOF" in pdf_bytes

        # Validate with validate_pdf_structure
        from backend.app.services.report import validate_pdf_structure
        assert validate_pdf_structure(pdf_bytes) is True

        # Parse with pypdf to verify text extraction integrity
        import pypdf
        import io
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        assert len(reader.pages) >= 1
        extracted_text = "".join(page.extract_text() or "" for page in reader.pages)
        
        # Verify statutory citation under BSA 2023 §63
        assert "Bharatiya Sakshya Adhiniyam" in extracted_text
        # Verify evidence SHA-256 digest is present in forensic report
        assert "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945" in extracted_text
        # Verify job and incident details
        assert f"#{job.id}" in extracted_text

        # Clean up
        db.delete(ev)
        db.delete(inc)
        db.delete(det)
        db.delete(job)
        db.commit()
    finally:
        db.close()


def test_transcode_and_seal_clip(tmp_path):
    """Verify video clip sealing and SHA-256 hash generation."""
    clip_file = tmp_path / "test_clip.mp4"
    # Create tiny 5-frame video
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(clip_file), fourcc, 10.0, (100, 100))
    for i in range(5):
        frame = np.full((100, 100, 3), i * 40, dtype=np.uint8)
        writer.write(frame)
    writer.release()

    final_path, sha_hash, is_playable = transcode_and_seal_clip(str(clip_file))
    assert os.path.exists(final_path)
    assert len(sha_hash) == 64
    assert isinstance(is_playable, bool)
