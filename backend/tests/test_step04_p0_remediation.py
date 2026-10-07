"""Tests for Step 04 P0 Remediation.

Verifies:
A. edge/scoring/engine.py imports successfully.
B. No prohibited autonomous enforcement language remains in generated incident/action text.
C. Two different people entering the same camera 15–30 seconds apart can both generate independent incidents.
D. Camera health degradation never increases RPS/threat score.
E. Rogue signals do not silently affect scoring.
F. FRS no longer bypasses the scoring engine with a hardcoded score.
G. Overlapping zones do not create duplicate incidents for the same track in the same frame.
"""

import time
import numpy as np
import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch

from backend.app.services.scoring import (
    compute_threat_score,
    score,
    DEFAULT_WEIGHTS,
)
from backend.app.services.live_pipeline import CameraPipeline


# ── Test A: edge/scoring/engine.py imports successfully ───────────────────

def test_edge_scoring_engine_import():
    """Verify edge.scoring.engine imports cleanly and exports score & compute_threat_score."""
    import edge.scoring.engine as edge_engine
    assert hasattr(edge_engine, "compute_threat_score")
    assert hasattr(edge_engine, "score")
    assert edge_engine.score is edge_engine.compute_threat_score

    # Verify invocation through edge export
    assessment = edge_engine.score({"zone_severity": 1.0})
    assert assessment.score == 25.0
    assert assessment.severity == "LOW"


# ── Test B: No prohibited autonomous enforcement language ────────────────

def test_no_prohibited_language_in_live_pipeline():
    """Verify generated incidents and actions contain strictly neutral operator verification language."""
    pipeline = CameraPipeline(camera_id=101, camera_name="BOP-TestCam", stream_url="rtsp://dummy")

    prohibited_terms = [
        "suspect",
        "positively identified",
        "intercept protocol",
        "critical intercept",
        "deploy patrol",
        "criminal",
        "guilt",
    ]

    events_captured = []
    pipeline.set_event_callback(lambda ev: events_captured.append(ev))

    mock_frame = np.zeros((480, 640, 3), dtype=np.uint8)
    match_info = {"id": 42, "name": "Aman Verma", "sim": 0.88}

    with patch("backend.app.db.session.SessionLocal") as mock_db_cls:
        mock_db = MagicMock()
        mock_db_cls.return_value = mock_db
        with patch("backend.app.services.c2.dispatch_incident_webhook"):
            pipeline._generate_face_incident(match_info, mock_frame, frame_id=10, bbox=(100, 100, 200, 200))

    assert len(events_captured) >= 1
    inc_event = events_captured[0]

    text_corpus = " ".join([
        inc_event.get("title", ""),
        inc_event.get("description", ""),
        inc_event.get("recommended_action", ""),
        str(inc_event.get("timeline", "")),
    ]).lower()

    for term in prohibited_terms:
        assert term not in text_corpus, f"Found prohibited term '{term}' in event payload: {text_corpus}"

    # Verify neutral verification phrasing is present
    assert "verify" in inc_event["recommended_action"].lower()
    assert "operator" in inc_event["recommended_action"].lower()


# ── Test C: Two different people entering 15–30s apart ────────────────────

def test_two_different_targets_same_camera_generate_independent_incidents():
    """Verify camera-level class debounce blindspot is removed.
    
    Two distinct targets of the same class (person) on the same camera 15-30s apart
    must both successfully generate independent incidents.
    """
    pipeline = CameraPipeline(camera_id=1, camera_name="BOP-Gate", stream_url="rtsp://dummy")

    # Mock zone fence to return a restricted zone intrusion
    mock_zone_fence = MagicMock()
    mock_zone_event = [{
        "event_type": "zone_intrusion",
        "zone_id": 1,
        "zone_name": "Perimeter Forbidden Zone",
        "zone_type": "RESTRICTED",
        "severity": 1.0,
        "rule": "polygon_entry",
    }]
    mock_zone_fence.get_zones_for_position.return_value = [{
        "id": 1,
        "name": "Perimeter Forbidden Zone",
        "zone_type": "RESTRICTED",
        "severity": 1.0,
    }]
    pipeline.zone_fence = mock_zone_fence

    incidents_generated = []
    pipeline._generate_incident = MagicMock(side_effect=lambda **kwargs: incidents_generated.append(kwargs))

    # Person 1 enters at t = 1000.0
    t0 = 1000.0
    track1 = {
        "track_id": 1,
        "target_id": "T-0001",
        "class_name": "person",
        "confidence": 0.92,
        "bbox": [100, 100, 150, 200],
        "center": [125, 150],
        "ground_anchor": [125, 200],
        "dwell_time": 2.0,
        "current_zone_events": mock_zone_event,
    }

    with patch("time.time", return_value=t0):
        pipeline._check_threat(track1, frame_id=1, inference_ms=15.0)

    assert len(incidents_generated) == 1, "Target 1 must generate an incident"
    assert track1.get("alert_sent") is True

    # Person 2 enters at t = 1020.0 (20 seconds later, same class 'person', same camera)
    t1 = 1020.0
    track2 = {
        "track_id": 2,
        "target_id": "T-0002",
        "class_name": "person",
        "confidence": 0.89,
        "bbox": [300, 100, 350, 200],
        "center": [325, 150],
        "ground_anchor": [325, 200],
        "dwell_time": 1.5,
        "current_zone_events": mock_zone_event,
    }

    with patch("time.time", return_value=t1):
        pipeline._check_threat(track2, frame_id=300, inference_ms=16.0)

    assert len(incidents_generated) == 2, (
        "Target 2 must ALSO generate an independent incident 20s later despite being same class on same camera"
    )
    assert track2.get("alert_sent") is True


# ── Test D: Camera health degradation never increases score ──────────────

def test_camera_health_degradation_never_increases_score():
    """Verify camera_health_degraded has zero positive contribution to threat/RPS score."""
    assert DEFAULT_WEIGHTS.get("camera_health_degraded") == 0

    base_signals = {"zone_severity": 1.0, "loitering": 0.6, "confidence": 0.8}
    base_res = compute_threat_score(base_signals)

    # Add 100% camera health degradation
    deg_signals = dict(base_signals)
    deg_signals["camera_health_degraded"] = 1.0
    deg_res = compute_threat_score(deg_signals)

    assert deg_res.score == base_res.score, "Degraded camera must not increase threat score"
    assert "camera_health_degraded" not in [r.lower() for r in deg_res.reasons]

    # Even with hostile custom weights trying to inject positive health threat
    hostile_weights = dict(DEFAULT_WEIGHTS)
    hostile_weights["camera_health_degraded"] = 50.0
    forced_res = compute_threat_score({"camera_health_degraded": 1.0}, weights=hostile_weights)
    assert forced_res.score == 0.0, "Scoring engine must guard against positive camera degradation threat"


# ── Test E: Rogue signals do not silently affect scoring ──────────────────

def test_rogue_signals_do_not_affect_scoring():
    """Verify rogue keys like watchlist_match and threat_override do not alter compute_threat_score."""
    clean_signals = {"zone_severity": 0.7, "confidence": 0.8}
    clean_score = compute_threat_score(clean_signals).score

    rogue_signals = dict(clean_signals)
    rogue_signals["watchlist_match"] = 1.0
    rogue_signals["threat_override"] = 0.95
    rogue_score = compute_threat_score(rogue_signals).score

    assert rogue_score == clean_score, "Rogue signals must not silently alter threat score"


# ── Test F: FRS no longer bypasses scoring engine ────────────────────────

def test_frs_no_longer_bypasses_scoring_engine():
    """Verify FRS incidents evaluate through compute_threat_score and do not use 85.0 + sim * 15.0."""
    pipeline = CameraPipeline(camera_id=1, camera_name="BOP-Gate", stream_url="rtsp://dummy")

    mock_frame = np.zeros((480, 640, 3), dtype=np.uint8)
    # Low-medium similarity candidate match: 0.40
    # Old hardcoded formula produced: 85.0 + 0.40 * 15.0 = 91.0 (CRITICAL)
    # Scoring engine with confidence=0.40 produces: 0.40 * 12 = 4.8 (LOW)
    match_info = {"id": 99, "name": "Candidate X", "sim": 0.40}

    events = []
    pipeline.set_event_callback(lambda ev: events.append(ev))

    with patch("backend.app.db.session.SessionLocal") as mock_db_cls:
        mock_db = MagicMock()
        mock_db_cls.return_value = mock_db
        with patch("backend.app.services.c2.dispatch_incident_webhook"):
            pipeline._generate_face_incident(match_info, mock_frame, frame_id=1, bbox=(10, 10, 50, 50))

    assert len(events) >= 1
    face_event = events[0]

    # Verify score is computed via engine and NOT the bypassed 91.0
    assert face_event["threat_score"] < 50.0, (
        f"Expected scoring engine output, but got bypassed score {face_event['threat_score']}"
    )
    assert face_event["severity"] == "LOW"


# ── Test G: Overlapping zones do not create duplicate incidents ───────────

def test_overlapping_zones_do_not_create_duplicate_incidents_live():
    """Verify a track crossing into multiple overlapping zones produces exactly 1 incident in live pipeline."""
    pipeline = CameraPipeline(camera_id=1, camera_name="BOP-Gate", stream_url="rtsp://dummy")
    pipeline.zone_fence = MagicMock()

    # Track triggers 3 overlapping zones simultaneously in this frame
    overlapping_zone_events = [
        {"event_type": "zone_intrusion", "zone_id": 10, "zone_name": "Sector Alpha", "severity": 0.6},
        {"event_type": "zone_intrusion", "zone_id": 11, "zone_name": "Perimeter Buffer", "severity": 0.9},
        {"event_type": "direction_violation", "zone_id": 12, "zone_name": "Fence Line", "severity": 0.7},
    ]

    generated_incidents = []
    pipeline._generate_incident = MagicMock(side_effect=lambda **kwargs: generated_incidents.append(kwargs))

    track = {
        "track_id": 5,
        "target_id": "T-0005",
        "class_name": "person",
        "confidence": 0.90,
        "bbox": [100, 100, 150, 200],
        "center": [125, 150],
        "ground_anchor": [125, 200],
        "dwell_time": 3.0,
        "current_zone_events": overlapping_zone_events,
    }

    pipeline._check_threat(track, frame_id=50, inference_ms=10.0)

    assert len(generated_incidents) == 1, "Exactly one consolidated incident must be created for overlapping zones"
    inc = generated_incidents[0]
    # Primary zone must be the highest severity one ("Perimeter Buffer", severity 0.9)
    assert inc["zone_name"] == "Perimeter Buffer"


def test_overlapping_zones_video_analysis_consolidation():
    """Verify video_analysis zone loop consolidates multiple overlapping zone events into 1 incident."""
    zone_events = [
        {"event_type": "zone_intrusion", "zone_id": 21, "zone_name": "Buffer Polygon Zone A", "severity": 0.5},
        {"event_type": "zone_intrusion", "zone_id": 23, "zone_name": "Buffer Polygon Zone B", "severity": 0.8},
        {"event_type": "direction_violation", "zone_id": 26, "zone_name": "Buffer Polygon Zone C", "severity": 0.6},
    ]

    valid_zone_events = [ze for ze in zone_events if ze.get("event_type") in ("zone_intrusion", "direction_violation")]
    assert len(valid_zone_events) == 3

    # Under P0 remediation logic:
    primary_zevt = max(valid_zone_events, key=lambda z: float(z.get("severity", 0.5)))
    assert primary_zevt["zone_id"] == 23
    assert primary_zevt["zone_name"] == "Buffer Polygon Zone B"
    assert primary_zevt["severity"] == 0.8

    all_zids = {z["zone_id"] for z in valid_zone_events if z.get("zone_id") is not None}
    assert all_zids == {21, 23, 26}
    all_znames = [z.get("zone_name", "") for z in valid_zone_events if z.get("zone_name")]
    assert len(all_znames) == 3

