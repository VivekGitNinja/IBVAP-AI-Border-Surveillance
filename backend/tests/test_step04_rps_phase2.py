"""
Unit and integration tests for Step 04 Phase 2: Identity & Vehicle Evidence Integration.
(Production-structured evidence association, pending field validation)

Covers all required verification requirements:
- Test A: ANPR candidate correctly associates with vehicle track.
- Test B: ANPR candidate cannot incorrectly attach to unrelated vehicle.
- Test C: Multi-frame plate consensus works (frequency/confidence weighting).
- Test D: Stale plate evidence expires after TTL (5.0s).
- Test E: FRS candidate correctly associates with person track.
- Test F: Face candidate cannot incorrectly attach to unrelated person (e.g. lower body/outside).
- Test G: Multiple simultaneous faces/persons remain isolated.
- Test H: Low-quality face evidence remains uncertain.
- Test I: Face similarity alone cannot produce HIGH/CRITICAL RPS.
- Test J: OCR confidence alone cannot produce HIGH/CRITICAL RPS.
- Test K: Missing authorization/watchlist context is explicitly represented.
- Test L: Evidence provenance is complete.
- Test M: Existing ZoneFence + ByteTrack behavior remains intact.
- Test N: No prohibited autonomous enforcement language.
- Test O: Regression compatibility with existing consumers.
- Test P: Additional regression: very high face similarity / OCR confidence cannot increase RPS, but are preserved in provenance.
"""

import time
from unittest.mock import MagicMock
import numpy as np
import pytest

from edge.evidence.association import (
    AssociationState,
    EvidenceStatus,
    WatchlistAuthStatus,
    PlateEvidenceCandidate,
    PlateTrackRecord,
    FaceEvidenceCandidate,
    FaceTrackRecord,
    UnifiedTrackEvidence,
    ANPRAssociator,
    FRSAssociator,
    EvidenceAssociationEngine,
)
from backend.app.services.scoring import compute_threat_score
from backend.app.services.live_pipeline import CameraPipeline


# ── Test A: ANPR candidate correctly associates with vehicle track ──────────

def test_anpr_candidate_correctly_associates_with_vehicle():
    """Valid plate detection within vehicle lower-bumper region associates cleanly."""
    associator = ANPRAssociator()
    vehicle = {
        "track_id": 10,
        "class_name": "car",
        "bbox": [100.0, 100.0, 300.0, 250.0],  # w=200, h=150
    }
    # Plate located at bottom center of vehicle: [160, 200, 240, 225] -> w=80, h=25, ar=3.2
    plate_bbox = [160.0, 200.0, 240.0, 225.0]
    now = time.time()

    state, score, reasons = associator.evaluate_association(
        vehicle_track=vehicle,
        plate_bbox=plate_bbox,
        camera_id=1,
        timestamp=now,
    )

    assert state == AssociationState.ASSOCIATED
    assert score >= 0.75
    assert len(reasons) > 0


# ── Test B: ANPR candidate cannot incorrectly attach to unrelated vehicle ───

def test_anpr_candidate_rejected_for_unrelated_distant_vehicle():
    """Plate located outside the vehicle boundary or exhibiting impossible jumps is rejected."""
    associator = ANPRAssociator()
    vehicle = {
        "track_id": 10,
        "class_name": "car",
        "bbox": [100.0, 100.0, 300.0, 250.0],
    }
    # Plate at completely different coordinates
    distant_plate = [600.0, 500.0, 680.0, 525.0]

    state, score, reasons = associator.evaluate_association(
        vehicle_track=vehicle,
        plate_bbox=distant_plate,
        camera_id=1,
        timestamp=time.time(),
    )

    assert state == AssociationState.REJECTED
    assert score == 0.0
    assert any("outside" in r.lower() for r in reasons)

    # Test impossible spatial jump (e.g. 5000 px/s)
    now = time.time()
    last_cand = PlateEvidenceCandidate(
        candidate_id="P-OLD",
        plate_text="DL01AB1234",
        ocr_confidence=0.90,
        bbox=[160.0, 200.0, 240.0, 225.0],
        relative_bbox=[0.3, 0.6, 0.7, 0.8],
        frame_id=1,
        timestamp=now - 0.05,  # 50ms ago
        camera_id=1,
    )
    # Teleported 800 pixels in 50ms
    teleported_plate = [260.0, 105.0, 340.0, 130.0]
    jump_state, _, jump_reasons = associator.evaluate_association(
        vehicle_track={"bbox": [200.0, 80.0, 400.0, 220.0]},
        plate_bbox=teleported_plate,
        camera_id=1,
        timestamp=now,
        last_plate_candidate=last_cand,
    )
    assert jump_state == AssociationState.REJECTED
    assert any("jump" in r.lower() for r in jump_reasons)


# ── Test C: Multi-frame plate consensus works ───────────────────────────────

def test_multi_frame_plate_consensus_resolves_correctly():
    """Multiple consistent observations overcome isolated OCR character noise."""
    engine = EvidenceAssociationEngine(camera_id=1)
    vehicle = {"track_id": 7, "class_name": "car", "bbox": [100.0, 100.0, 300.0, 250.0]}
    plate_bbox = [160.0, 200.0, 240.0, 225.0]
    t0 = time.time()

    # Frame 1: Clear read "DL01AB1234"
    engine.associate_vehicle_plate(7, vehicle, "DL01AB1234", 0.92, plate_bbox, 1, t0)
    rec1 = engine._plate_records[7]
    assert rec1.consensus_text == "DL01AB1234"
    assert rec1.evidence_status == EvidenceStatus.CANDIDATE.value  # Single read is candidate

    # Frame 2: Noisy OCR read "DL01AB123A" (conf 0.50)
    engine.associate_vehicle_plate(7, vehicle, "DL01AB123A", 0.50, plate_bbox, 2, t0 + 0.1)
    rec2 = engine._plate_records[7]
    assert rec2.consensus_text == "DL01AB1234"  # Original still dominates by weight

    # Frame 3: Confirmed read "DL01AB1234" (conf 0.95)
    engine.associate_vehicle_plate(7, vehicle, "DL01AB1234", 0.95, plate_bbox, 3, t0 + 0.2)
    rec3 = engine._plate_records[7]
    assert rec3.consensus_text == "DL01AB1234"
    assert rec3.evidence_status == EvidenceStatus.VERIFIED.value  # >= 2 observations achieves verified consensus
    assert rec3.observation_count == 3


# ── Test D: Stale plate evidence expires after TTL ───────────────────────────

def test_stale_plate_evidence_expires():
    """Plate record past TTL (5.0s) is marked stale and excluded from active context."""
    engine = EvidenceAssociationEngine(camera_id=2)
    vehicle = {"track_id": 12, "class_name": "car", "bbox": [100.0, 100.0, 300.0, 250.0], "dwell_time": 10.0}
    plate_bbox = [160.0, 200.0, 240.0, 225.0]
    t0 = time.time()

    # Seen at t0
    engine.associate_vehicle_plate(12, vehicle, "HR26DK8392", 0.90, plate_bbox, 1, t0)
    unified_fresh = engine.build_unified_evidence(vehicle, camera_id=2, timestamp=t0 + 1.0)
    assert unified_fresh.plate_record.is_stale is False
    assert unified_fresh.vehicle_evidence_status == EvidenceStatus.CANDIDATE.value

    # Checked at t0 + 6.0s (past 5.0s TTL)
    unified_stale = engine.build_unified_evidence(vehicle, camera_id=2, timestamp=t0 + 6.0)
    assert unified_stale.plate_record.is_stale is True
    assert unified_stale.vehicle_evidence_status == EvidenceStatus.MISSING.value
    assert any("expired" in m.lower() for m in unified_stale.missing_evidence)


# ── Test E: FRS candidate correctly associates with person track ────────────

def test_frs_candidate_correctly_associates_with_person():
    """Face located in upper body region of person track associates as ASSOCIATED."""
    associator = FRSAssociator(default_max_relative_head_y=0.55)
    person = {
        "track_id": 3,
        "class_name": "person",
        "bbox": [200.0, 100.0, 280.0, 300.0],  # w=80, h=200, top=100, bottom=300
    }
    # Face located in upper head region: [220, 110, 260, 160] -> fc_y=135, rel_y=(135-100)/200=0.175 <= 0.55
    face_bbox = [220.0, 110.0, 260.0, 160.0]

    state, score, reasons, is_low = associator.evaluate_association(
        person_track=person,
        face_bbox=face_bbox,
        detection_confidence=0.92,
        camera_id=1,
        timestamp=time.time(),
    )

    assert state == AssociationState.ASSOCIATED
    assert score >= 0.75
    assert is_low is False
    assert any("upper body" in r.lower() for r in reasons)


# ── Test F: Face candidate rejected for impossible positions ────────────────

def test_face_candidate_rejected_for_impossible_positions():
    """Face detected in lower body (legs/feet) or outside person boundary is REJECTED."""
    associator = FRSAssociator(default_max_relative_head_y=0.55, uncertain_relative_head_y=0.70)
    person = {
        "track_id": 3,
        "class_name": "person",
        "bbox": [200.0, 100.0, 280.0, 300.0],  # h=200, top=100, bottom=300
    }

    # 1. Face in lower legs/feet: y from 260 to 295 -> rel_y = (277.5 - 100)/200 = 0.8875 > 0.70
    feet_face = [225.0, 260.0, 255.0, 295.0]
    state_feet, _, reasons_feet, _ = associator.evaluate_association(
        person_track=person,
        face_bbox=feet_face,
        detection_confidence=0.85,
        camera_id=1,
        timestamp=time.time(),
    )
    assert state_feet == AssociationState.REJECTED
    assert any("lower body" in r.lower() or "impossible" in r.lower() for r in reasons_feet)

    # 2. Face completely outside person box
    outside_face = [450.0, 120.0, 490.0, 170.0]
    state_out, _, reasons_out, _ = associator.evaluate_association(
        person_track=person,
        face_bbox=outside_face,
        detection_confidence=0.85,
        camera_id=1,
        timestamp=time.time(),
    )
    assert state_out == AssociationState.REJECTED
    assert any("outside" in r.lower() for r in reasons_out)


# ── Test G: Multiple simultaneous faces/persons remain isolated ──────────────

def test_multiple_simultaneous_faces_and_persons_remain_isolated():
    """Two concurrent persons in frame correctly associate with their respective faces."""
    engine = EvidenceAssociationEngine(camera_id=1)
    p1 = {"track_id": 1, "class_name": "person", "bbox": [100.0, 100.0, 180.0, 300.0]}
    p2 = {"track_id": 2, "class_name": "person", "bbox": [400.0, 100.0, 480.0, 300.0]}

    face1 = [120.0, 110.0, 160.0, 160.0]
    face2 = [420.0, 110.0, 460.0, 160.0]
    now = time.time()

    # Person 1 evaluates face1 (should associate) and face2 (should reject)
    c1_f1 = engine.associate_person_face(1, p1, face1, 0.90, 1, now, similarity_score=0.88, matched_subject_name="Alice")
    c1_f2 = engine.associate_person_face(1, p1, face2, 0.90, 1, now, similarity_score=0.75, matched_subject_name="Bob")
    assert c1_f1 is not None
    assert c1_f2 is None, "Face 2 must be rejected for Person 1"

    # Person 2 evaluates face2 (should associate) and face1 (should reject)
    c2_f2 = engine.associate_person_face(2, p2, face2, 0.90, 1, now, similarity_score=0.75, matched_subject_name="Bob")
    c2_f1 = engine.associate_person_face(2, p2, face1, 0.90, 1, now, similarity_score=0.88, matched_subject_name="Alice")
    assert c2_f2 is not None
    assert c2_f1 is None, "Face 1 must be rejected for Person 2"

    # Verify isolated track records
    assert engine._face_records[1].best_candidate.matched_subject_name == "Alice"
    assert engine._face_records[2].best_candidate.matched_subject_name == "Bob"


# ── Test H: Low-quality face evidence remains uncertain ──────────────────────

def test_low_quality_face_evidence_is_flagged_uncertain():
    """Tiny face bounding box or low detection confidence is classified as UNCERTAIN."""
    associator = FRSAssociator(min_face_size=24, min_detection_confidence=0.60)
    person = {"track_id": 5, "class_name": "person", "bbox": [200.0, 100.0, 280.0, 300.0]}

    # Face is tiny: 16x16 pixels
    tiny_face = [230.0, 110.0, 246.0, 126.0]
    state, score, reasons, is_low = associator.evaluate_association(
        person_track=person,
        face_bbox=tiny_face,
        detection_confidence=0.90,
        camera_id=1,
        timestamp=time.time(),
    )

    assert is_low is True
    assert state == AssociationState.UNCERTAIN
    assert any("low quality" in r.lower() for r in reasons)


# ── Test I: Face similarity alone cannot produce HIGH/CRITICAL RPS ───────────

def test_face_similarity_alone_cannot_produce_high_critical_rps():
    """Even a 0.999 face similarity match cannot elevate an entity in a public area or without breach to HIGH/CRITICAL."""
    # Public zone: G_spatial = 0.0
    assessment_public = compute_threat_score(
        signals={},
        context={
            "zone_type": "PUBLIC",
            "object_type": "person",
            "evidence_confidence": 0.999,
            "identity_evidence_status": "VERIFIED",
            "watchlist_authorization_status": "FLAGGED",
            "face_candidate": True,
        },
    )
    assert assessment_public.rps_score == 0.0
    assert assessment_public.priority_level == "LOW"

    # Monitored zone with passive presence: bounded low priority
    assessment_monitored = compute_threat_score(
        signals={},
        context={
            "zone_type": "MONITORED",
            "object_type": "person",
            "evidence_confidence": 0.999,
            "identity_evidence_status": "VERIFIED",
            "watchlist_authorization_status": "FLAGGED",
            "face_candidate": True,
            "consecutive_frames": 5,
            "dwell_time": 2.0,
        },
    )
    assert assessment_monitored.rps_score < 40.0
    assert assessment_monitored.priority_level in ("LOW", "MEDIUM")
    assert assessment_monitored.priority_level != "HIGH"
    assert assessment_monitored.priority_level != "CRITICAL"


# ── Test J: OCR confidence alone cannot produce HIGH/CRITICAL RPS ───────────

def test_ocr_confidence_alone_cannot_produce_high_critical_rps():
    """A 100% OCR confidence read in a public or unbreached zone does not create threat priority."""
    assessment = compute_threat_score(
        signals={"vehicle_context": 1.0},
        context={
            "zone_type": "MONITORED",
            "object_type": "car",
            "evidence_confidence": 1.0,
            "vehicle_evidence_status": "VERIFIED",
            "plate_text": "DL01AB1234",
            "consecutive_frames": 10,
            "dwell_time": 5.0,
        },
    )
    # 0.30 * (25 base presence + 5 vehicle) = 9.0
    assert assessment.rps_score < 20.0
    assert assessment.priority_level == "LOW"


# ── Test K: Missing authorization/watchlist context is explicitly represented

def test_missing_evidence_explicitly_represented():
    """When facial or vehicle context is absent, output explicitly identifies the gap."""
    engine = EvidenceAssociationEngine(camera_id=1)
    person = {"track_id": 9, "class_name": "person", "bbox": [100.0, 100.0, 180.0, 300.0], "dwell_time": 1.0}

    unified = engine.build_unified_evidence(person, camera_id=1, timestamp=time.time())
    rps_ctx = unified.to_rps_context()

    assert unified.identity_evidence_status == EvidenceStatus.MISSING.value
    assert len(unified.missing_evidence) > 0
    assert any("facial recognition" in m.lower() for m in unified.missing_evidence)

    assessment = compute_threat_score(signals={}, context=rps_ctx)
    assert any("facial recognition" in m.lower() for m in assessment.missing_evidence)


# ── Test L: Evidence provenance is complete ──────────────────────────────────

def test_evidence_provenance_completeness():
    """Verify that generated candidate evidence preserves full technical provenance."""
    engine = EvidenceAssociationEngine(camera_id=3)
    vehicle = {"track_id": 4, "class_name": "truck", "bbox": [100.0, 100.0, 350.0, 300.0]}
    plate_bbox = [180.0, 240.0, 270.0, 275.0]
    now = time.time()

    cand = engine.associate_vehicle_plate(
        track_id=4,
        vehicle_track=vehicle,
        raw_plate_text="DL01AB1234",
        ocr_confidence=0.94,
        plate_bbox=plate_bbox,
        frame_id=42,
        timestamp=now,
    )

    assert cand is not None
    assert cand.candidate_id == "PLATE-3-4-42"
    assert cand.camera_id == 3
    assert cand.frame_id == 42
    assert cand.timestamp == now
    assert cand.ocr_confidence == 0.94
    assert len(cand.relative_bbox) == 4
    assert cand.is_valid_format is True
    assert "raw_text" in cand.provenance
    assert "association_reasons" in cand.provenance


# ── Test M: Existing ZoneFence + ByteTrack behavior remains intact ───────────

def test_existing_zonefence_bytetrack_behavior_intact():
    """Confirm live pipeline integration preserves ByteTrack tracklets and ZoneFence events."""
    zone = {
        "id": 50,
        "name": "Buffer Alpha",
        "zone_type": "BUFFER",
        "polygon": [[100.0, 100.0], [400.0, 100.0], [400.0, 400.0], [100.0, 400.0]],
        "severity": 0.65,
    }
    pipeline = CameraPipeline(camera_id=301, stream_url="demo://test301", zones=[zone])
    assert hasattr(pipeline, "evidence_associator")
    assert pipeline.evidence_associator.camera_id == 301

    track = {
        "track_id": 1,
        "target_id": "T-001",
        "class_name": "car",
        "confidence": 0.90,
        "bbox": [150.0, 150.0, 350.0, 350.0],
        "center": [250.0, 250.0],
        "ground_anchor": [250.0, 350.0],
        "dwell_time": 1.0,
        "current_zone_events": [],
    }

    generated_incidents = []
    pipeline._generate_incident = MagicMock(side_effect=lambda **kwargs: generated_incidents.append(kwargs))
    pipeline._check_threat(track, frame_id=10, inference_ms=5.0)

    # In buffer zone with vehicle context, score is >= 20, incident generated
    assert len(generated_incidents) == 1
    inc = generated_incidents[0]
    assert inc["zone_name"] == "Buffer Alpha"


# ── Test N: No prohibited autonomous enforcement language ───────────────────

def test_no_prohibited_language_in_phase2():
    """Verify all evidence models and records exclude prohibited autonomous language."""
    prohibited = [
        "suspect", "guilt", "criminal intent", "arrest", "neutralize",
        "shoot", "eliminate", "lethal", "intercept protocol", "positively identified"
    ]

    engine = EvidenceAssociationEngine(camera_id=1)
    person = {"track_id": 1, "class_name": "person", "bbox": [100.0, 100.0, 180.0, 300.0], "dwell_time": 2.0}
    face_bbox = [120.0, 110.0, 160.0, 160.0]

    engine.associate_person_face(1, person, face_bbox, 0.92, 1, time.time(), similarity_score=0.88, matched_subject_name="Enrolled Subject")
    unified = engine.build_unified_evidence(person, camera_id=1, timestamp=time.time())
    rps_ctx = unified.to_rps_context()
    assessment = compute_threat_score(signals={}, context=rps_ctx)

    corpus = " ".join([
        str(unified.provenance),
        str(unified.missing_evidence),
        assessment.operator_guidance,
        assessment.recommended_action,
        " ".join(assessment.explainability_facts),
    ]).lower()

    for term in prohibited:
        assert term not in corpus, f"Prohibited term '{term}' found in Phase 2 evidence output"


# ── Test O: Regression compatibility with existing consumers ────────────────

def test_regression_compatibility_with_existing_consumers():
    """Verify RPSAssessment fields remain 100% compatible with legacy consumers."""
    assessment = compute_threat_score(
        signals={"confidence": 0.85, "boundary_crossing": 1.0},
        context={"zone_type": "RESTRICTED", "identity_evidence_status": "CANDIDATE"},
    )
    # Modern fields exist
    assert hasattr(assessment, "rps_score")
    assert hasattr(assessment, "identity_evidence_status")
    assert hasattr(assessment, "vehicle_evidence_status")
    assert hasattr(assessment, "watchlist_authorization_status")

    # Legacy fields remain synchronized
    assert assessment.score == assessment.rps_score
    assert assessment.severity == assessment.priority_level
    assert assessment.confidence == assessment.evidence_confidence
    assert isinstance(assessment.reasons, list)
    assert isinstance(assessment.ai_assessment, dict)


# ── Test P: Additional Regression: High Similarity/OCR Cannot Increase RPS ───

def test_very_high_similarity_or_ocr_cannot_directly_increase_rps():
    """Verify that passing 0.999 face similarity or OCR confidence cannot artificially inflate RPS score."""
    base_signals = {"zone_severity": 0.3}  # Monitored zone
    base_context = {
        "zone_type": "MONITORED",
        "object_type": "person",
        "consecutive_frames": 5,
        "dwell_time": 2.0,
    }

    # Evaluation without biometric similarity
    eval_standard = compute_threat_score(
        signals=dict(base_signals),
        context=dict(base_context),
    )

    # Evaluation with 0.999 raw face similarity passed into signals and context
    eval_high_sim = compute_threat_score(
        signals=dict(base_signals, face_similarity=0.999),
        context=dict(base_context, face_similarity=0.999, evidence_confidence=0.999),
    )

    # Evaluation with 0.999 raw OCR confidence passed into signals and context
    eval_high_ocr = compute_threat_score(
        signals=dict(base_signals, ocr_confidence=0.999),
        context=dict(base_context, ocr_confidence=0.999, evidence_confidence=0.999),
    )

    # Score MUST be identical: neither face similarity nor OCR confidence can add threat points!
    assert eval_high_sim.rps_score == eval_standard.rps_score
    assert eval_high_ocr.rps_score == eval_standard.rps_score

    # Threat contribution must be explicitly 0.0
    assert eval_high_sim.signal_contributions.get("face_similarity_evidence_only") == 0.0
    assert eval_high_ocr.signal_contributions.get("ocr_confidence_evidence_only") == 0.0

    # Evidence confidence is preserved for provenance/operator inspection
    assert eval_high_sim.evidence_confidence == 1.0 or eval_high_sim.evidence_confidence == 0.999 or eval_high_sim.evidence_confidence == 1.00
