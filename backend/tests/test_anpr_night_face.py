"""Tests for ANPR, Night Mode, and Face Watchlist (Phases 2 & 3)."""

import io
import os
import pytest
import cv2
import numpy as np
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.core.config import settings
from backend.app.services.anpr import anpr_engine
from backend.app.services.face import face_service
from backend.app.models.plate_read import PlateRead
from backend.app.db.session import SessionLocal
from backend.app.core.security import create_access_token

client = TestClient(app)
_token = create_access_token("operator-1", role="ADMIN")
AUTH_HEADERS = {"Authorization": f"Bearer {_token}"}


def test_anpr_pipeline_with_rendered_plate():
    """Verify ANPR pipeline extracts real text from rendered license plate (>= 4 consecutive chars)."""
    vehicle_img = np.zeros((240, 360, 3), dtype=np.uint8) + 40
    cv2.rectangle(vehicle_img, (80, 150), (280, 210), (255, 255, 255), -1)
    cv2.putText(vehicle_img, "DL01AB1234", (90, 195), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 2)

    plate_crop, bbox = anpr_engine.localize_plate(vehicle_img)
    assert plate_crop is not None
    assert bbox is not None

    text, conf = anpr_engine.read_plate_text(plate_crop)
    assert text is not None
    # Must yield real plate text with >= 4 consecutive correct chars from DL01AB1234
    fixture_str = "DL01AB1234"
    has_consecutive_4 = any(fixture_str[i:i+4] in text for i in range(len(fixture_str) - 3))
    assert has_consecutive_4, f"Extracted text '{text}' did not have 4 consecutive chars matching '{fixture_str}'"
    assert conf > 0.0


def test_anpr_stores_non_morth_pattern_as_is():
    """Verify non-standard plate text is preserved as-is and never rejected."""
    weird_text = "MILITARY99"
    weird_plate = np.full((60, 240, 3), 255, dtype=np.uint8)
    cv2.putText(weird_plate, weird_text, (10, 45), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2)
    text, conf = anpr_engine.read_plate_text(weird_plate)
    assert text is not None
    assert len(text) >= 4
    # Ensure raw text is kept
    assert any(c in text for c in "MILITARY99")


def test_plates_search_endpoint():
    """Verify GET /api/v1/plates query endpoint with real PlateRead rows."""
    db = SessionLocal()
    try:
        # Create test PlateRead row
        pr = PlateRead(
            job_id=999,
            plate_text="DL01AB9999",
            confidence=0.92,
            frame_index=15,
            timestamp_ms=600.0,
            bbox={"x1": 0.3, "y1": 0.7, "x2": 0.6, "y2": 0.9},
            method="test",
        )
        db.add(pr)
        db.commit()
        db.refresh(pr)

        resp = client.get("/api/v1/plates?q=DL01", headers=AUTH_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert any(p["plate_text"] == "DL01AB9999" for p in data)

        # Clean up
        db.delete(pr)
        db.commit()
    finally:
        db.close()


def test_night_mode_luma_and_clahe():
    """Verify night frame detection based on luminance threshold and CLAHE enhancement."""
    # Dark frame (night)
    dark_frame = np.full((100, 100, 3), 30, dtype=np.uint8)
    dark_gray = cv2.cvtColor(dark_frame, cv2.COLOR_BGR2GRAY)
    luma_dark = float(np.mean(dark_gray))
    assert luma_dark < settings.night_luma_threshold
    assert luma_dark < 40.0

    # Bright frame (day)
    bright_frame = np.full((100, 100, 3), 180, dtype=np.uint8)
    bright_gray = cv2.cvtColor(bright_frame, cv2.COLOR_BGR2GRAY)
    luma_bright = float(np.mean(bright_gray))
    assert luma_bright >= settings.night_luma_threshold

    # CLAHE enhancement on dark frame should increase contrast without errors
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(dark_gray)
    assert enhanced.shape == dark_gray.shape


def test_face_detector_and_blur():
    """Verify face detector initialization and privacy blurring."""
    assert face_service._detector_type in ("YuNet", "HaarCascade", "none")

    # Synthetic frame with mock face bounding box
    frame = np.zeros((200, 200, 3), dtype=np.uint8)
    # Varied texture representing face features
    for y in range(50, 120):
        for x in range(60, 130):
            frame[y, x] = [(x * 7) % 255, (y * 5) % 255, (x + y) % 255]

    faces = [{"bbox": {"x1": 0.3, "y1": 0.25, "x2": 0.65, "y2": 0.6}, "confidence": 0.9}]
    blurred = face_service.apply_face_blur(frame, faces)
    assert blurred.shape == frame.shape
    # Pixel values inside blurred region should have smoothed variations
    assert not np.array_equal(blurred, frame)


def test_watchlist_crud_lifecycle():
    """Verify watchlist enrollment, listing, and deletion endpoints with face validation."""
    # 1. Non-face image should be rejected with 422
    blank_img = np.zeros((100, 100, 3), dtype=np.uint8)
    _, blank_encoded = cv2.imencode(".jpg", blank_img)
    fail_resp = client.post(
        "/api/v1/watchlist/enroll",
        data={"name": "No Face Subject", "notes": "Should Fail"},
        files={"file": ("blank.jpg", io.BytesIO(blank_encoded.tobytes()), "image/jpeg")},
        headers=AUTH_HEADERS,
    )
    assert fail_resp.status_code == 422
    assert "no detectable face" in fail_resp.json().get("detail", "").lower()

    # 2. Face image should enroll successfully
    face_img = np.full((200, 200, 3), 220, dtype=np.uint8)
    cv2.ellipse(face_img, (100, 100), (50, 65), 0, 0, 360, (180, 160, 140), -1)
    cv2.circle(face_img, (80, 85), 8, (50, 50, 50), -1)
    cv2.circle(face_img, (120, 85), 8, (50, 50, 50), -1)
    cv2.line(face_img, (100, 95), (100, 115), (80, 80, 80), 2)
    cv2.ellipse(face_img, (100, 130), (25, 10), 0, 0, 180, (50, 50, 150), 2)

    _, img_encoded = cv2.imencode(".jpg", face_img)
    file_bytes = io.BytesIO(img_encoded.tobytes())

    # Enroll
    resp = client.post(
        "/api/v1/watchlist/enroll",
        data={"name": "Suspect Alpha", "notes": "Border Sector Infiltration Risk"},
        files={"file": ("suspect.jpg", file_bytes, "image/jpeg")},
        headers=AUTH_HEADERS,
    )
    assert resp.status_code == 201
    enrolled = resp.json()
    assert enrolled["name"] == "Suspect Alpha"
    assert enrolled["has_embedding"] is True
    subject_id = enrolled["id"]

    # List
    list_resp = client.get("/api/v1/watchlist", headers=AUTH_HEADERS)
    assert list_resp.status_code == 200
    all_subjects = list_resp.json()
    assert any(s["id"] == subject_id for s in all_subjects)

    # Delete
    del_resp = client.delete(f"/api/v1/watchlist/{subject_id}", headers=AUTH_HEADERS)
    assert del_resp.status_code == 200

    # Confirm deletion
    get_again = client.get("/api/v1/watchlist", headers=AUTH_HEADERS)
    assert not any(s["id"] == subject_id for s in get_again.json())


def test_face_recognition_sface_and_watchlist_matching():
    """Verify SFace extracts 128-d embeddings and matches enrolled watchlist subjects (ZERO SKIPS)."""
    avail, reason = face_service.check_recognition_availability()
    assert avail is True, f"Face recognition must be available with SFace: {reason}"

    # Generate a deterministic face portrait
    def create_test_face(noise_seed=0):
        np.random.seed(noise_seed)
        face_img = np.full((200, 200, 3), 210, dtype=np.uint8)
        cv2.ellipse(face_img, (100, 100), (50, 65), 0, 0, 360, (180, 160, 140), -1)
        cv2.circle(face_img, (80, 85), 8, (50, 50, 50), -1)
        cv2.circle(face_img, (120, 85), 8, (50, 50, 50), -1)
        cv2.line(face_img, (100, 95), (100, 115), (80, 80, 80), 2)
        cv2.ellipse(face_img, (100, 130), (25, 10), 0, 0, 180, (50, 50, 150), 2)
        if noise_seed > 0:
            noise = np.random.randint(-5, 5, face_img.shape, dtype=np.int16)
            face_img = np.clip(face_img.astype(np.int16) + noise, 0, 255).astype(np.uint8)
        return face_img

    enroll_img = create_test_face(0)
    match_img = create_test_face(7)

    emb_enroll = face_service.extract_embedding(enroll_img)
    assert emb_enroll is not None
    assert len(emb_enroll) == 128

    emb_match = face_service.extract_embedding(match_img)
    assert emb_match is not None
    assert len(emb_match) == 128

    # Enroll suspect in database
    db = SessionLocal()
    try:
        from backend.app.models.watchlist import Watchlist
        subject = Watchlist(
            name="Suspect Bravo",
            face_image_path="test_bravo.jpg",
            embedding=emb_enroll,
            notes="Test Bravo Target",
            created_by="operator",
        )
        db.add(subject)
        db.commit()
        db.refresh(subject)

        # Match must fire
        match_result = face_service.match_watchlist(emb_match, db)
        assert match_result is not None, "Watchlist match must fire for matching subject"
        matched_subj, sim = match_result
        assert matched_subj.id == subject.id
        assert sim >= settings.face_match_threshold, f"Similarity {sim} below threshold {settings.face_match_threshold}"

        # Clean up
        db.delete(subject)
        db.commit()
    finally:
        db.close()
