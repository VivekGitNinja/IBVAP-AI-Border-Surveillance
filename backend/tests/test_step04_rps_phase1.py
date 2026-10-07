"""
Unit and integration tests for Step 04 — RPS Architecture Implementation (Phase 1).

Covers all mandatory verification requirements:
- Test A: Public / outside-zone object -> RPS 0.0
- Test B: High detection confidence alone -> does not create high RPS
- Test C: Night context alone -> does not create high RPS
- Test D: Camera degradation -> never increases RPS
- Test E: Single-frame zone jitter -> no immediate high-priority incident (suppressed)
- Test F: Persistent restricted-zone breach -> RPS escalates to HIGH/CRITICAL
- Test G: Entry vs exit are semantically differentiated (entry > exit)
- Test H: Overlapping zones -> one consolidated incident per physical track/event
- Test I: RPS explainability fields are complete
- Test J: No prohibited autonomous enforcement language
- Test K: RPS remains strictly bounded in [0.0, 100.0]
- Test L: Missing evidence is explicitly represented in output
"""

from unittest.mock import MagicMock
import pytest

from backend.app.services.scoring import (
    compute_threat_score,
    get_severity_for_score,
    RPSAssessment,
    SPATIAL_GATE_MAP,
    SEVERITY_THRESHOLDS,
    CONFIRMATION_FRAMES_THRESHOLD,
    TEMPORAL_JITTER_FACTOR,
)
from backend.app.services.live_pipeline import CameraPipeline


# ── Test A: Public / outside-zone object -> RPS 0.0 ─────────────────────────

def test_public_outside_zone_object_produces_zero_rps():
    """Objects in public or unmonitored areas must have RPS = 0.0 regardless of signals."""
    # Case 1: Explicit PUBLIC zone type
    assessment_public = compute_threat_score(
        signals={
            "confidence": 0.99,
            "loitering": 1.0,
            "night": 1.0,
            "vehicle_context": 1.0,
            "behavior_anomaly": 1.0,
        },
        context={
            "zone_type": "PUBLIC",
            "zone_name": "Public Highway",
            "object_type": "car",
            "dwell_time": 120.0,
            "consecutive_frames": 50,
        },
    )
    assert assessment_public.rps_score == 0.0
    assert assessment_public.priority_level == "LOW"
    assert assessment_public.spatial_gate == 0.0
    assert assessment_public.evidence_confidence == 0.99
    assert any("public / unmonitored area" in fact for fact in assessment_public.explainability_facts)

    # Case 2: Zero zone severity and no zone
    assessment_no_zone = compute_threat_score(
        signals={"confidence": 0.95, "zone_severity": 0.0},
        context={"object_type": "person"},
    )
    assert assessment_no_zone.rps_score == 0.0
    assert assessment_no_zone.priority_level == "LOW"
    assert assessment_no_zone.spatial_gate == 0.0


# ── Test B: High detection confidence alone -> does not create high RPS ──────

def test_high_detection_confidence_alone_does_not_create_high_rps():
    """High detection confidence (e.g. 0.99) reflects evidence quality, NOT threat points."""
    assessment_low_conf = compute_threat_score(
        signals={"confidence": 0.10},
        context={"zone_type": "MONITORED", "object_type": "person", "dwell_time": 5.0, "consecutive_frames": 10},
    )
    assessment_high_conf = compute_threat_score(
        signals={"confidence": 0.99},
        context={"zone_type": "MONITORED", "object_type": "person", "dwell_time": 5.0, "consecutive_frames": 10},
    )

    # High confidence must not add threat points over low confidence
    assert assessment_high_conf.rps_score == assessment_low_conf.rps_score
    assert assessment_high_conf.priority_level in ("LOW", "MEDIUM")
    assert assessment_high_conf.rps_score < 40.0
    assert assessment_high_conf.evidence_confidence == 0.99
    assert assessment_high_conf.signal_contributions.get("confidence_evidence_only") == 0.0


# ── Test C: Night context alone -> does not create high RPS ─────────────────

def test_night_context_alone_does_not_create_high_rps():
    """Night context alone in monitored or public area must not escalate to HIGH or CRITICAL."""
    # Night in Monitored area
    assessment_night_monitored = compute_threat_score(
        signals={"night": 1.0, "confidence": 0.85},
        context={"zone_type": "MONITORED", "object_type": "person", "dwell_time": 2.0, "consecutive_frames": 5},
    )
    assert assessment_night_monitored.rps_score < 40.0
    assert assessment_night_monitored.priority_level == "LOW"

    # Night in Public area
    assessment_night_public = compute_threat_score(
        signals={"night": 1.0, "confidence": 0.85},
        context={"zone_type": "PUBLIC", "object_type": "person"},
    )
    assert assessment_night_public.rps_score == 0.0
    assert assessment_night_public.priority_level == "LOW"


# ── Test D: Camera degradation -> never increases RPS ───────────────────────

def test_camera_degradation_never_increases_rps():
    """Camera health degradation must have 0.0 positive contribution to RPS."""
    base_signals = {"confidence": 0.80, "boundary_crossing": 0.5}
    base_ctx = {"zone_type": "BUFFER", "object_type": "person", "consecutive_frames": 5, "dwell_time": 2.0}

    healthy_assessment = compute_threat_score(
        signals=dict(base_signals),
        context=dict(base_ctx, camera_health_degraded=False),
    )

    degraded_assessment = compute_threat_score(
        signals=dict(base_signals, camera_health_degraded=1.0),
        context=dict(base_ctx, camera_health_degraded=True),
    )

    # Degraded health must not increase score
    assert degraded_assessment.rps_score <= healthy_assessment.rps_score
    assert degraded_assessment.signal_contributions.get("camera_health_degraded") == 0.0
    assert any("diagnostic" in fact.lower() for fact in degraded_assessment.explainability_facts)
    assert "Camera health degraded" in degraded_assessment.ai_assessment.get("uncertainty", "")


# ── Test E: Single-frame zone jitter -> no immediate high-priority incident ─

def test_single_frame_zone_jitter_is_suppressed():
    """Single-frame unconfirmed detections must be suppressed and cannot escalate to HIGH/CRITICAL."""
    jitter_assessment = compute_threat_score(
        signals={"confidence": 0.95, "boundary_crossing": 1.0},
        context={
            "zone_type": "RESTRICTED",
            "object_type": "person",
            "is_single_frame_jitter": True,
            "consecutive_frames": 1,
            "dwell_time": 0.1,
        },
    )

    # Score must be suppressed by TEMPORAL_JITTER_FACTOR (0.40)
    assert jitter_assessment.temporal_factor == TEMPORAL_JITTER_FACTOR
    assert jitter_assessment.priority_level in ("LOW", "MEDIUM")
    assert jitter_assessment.priority_level != "HIGH"
    assert jitter_assessment.priority_level != "CRITICAL"
    assert jitter_assessment.rps_score <= 40.0
    assert any("Single-frame" in fact or "unconfirmed" in fact for fact in jitter_assessment.explainability_facts)


# ── Test F: Persistent restricted-zone breach -> escalates to HIGH/CRITICAL ──

def test_persistent_restricted_zone_breach_escalates():
    """Multi-frame verified breach in restricted zone must escalate to HIGH or CRITICAL."""
    breach_assessment = compute_threat_score(
        signals={"confidence": 0.92, "boundary_crossing": 1.0, "night": 1.0},
        context={
            "zone_type": "RESTRICTED",
            "object_type": "person",
            "direction": "inward",
            "consecutive_frames": 10,
            "dwell_time": 3.5,
            "is_single_frame_jitter": False,
        },
    )

    assert breach_assessment.temporal_factor == 1.0
    assert breach_assessment.spatial_gate == 1.00
    assert breach_assessment.rps_score >= 65.0
    assert breach_assessment.priority_level in ("HIGH", "CRITICAL")
    assert breach_assessment.rps_score >= 85.0  # 70 base + 10 entry + 5 night = 85.0 (CRITICAL)
    assert breach_assessment.priority_level == "CRITICAL"


# ── Test G: Entry vs exit are semantically differentiated ───────────────────

def test_entry_vs_exit_directionality_differentiation():
    """Inward breach trajectory must receive a higher RPS than outward retreat."""
    base_signals = {"confidence": 0.85, "boundary_crossing": 1.0}
    common_ctx = {
        "zone_type": "RESTRICTED",
        "object_type": "person",
        "consecutive_frames": 8,
        "dwell_time": 2.0,
    }

    entry_assessment = compute_threat_score(
        signals=dict(base_signals),
        context=dict(common_ctx, direction="entry"),
    )

    exit_assessment = compute_threat_score(
        signals=dict(base_signals),
        context=dict(common_ctx, direction="exit"),
    )

    assert entry_assessment.rps_score > exit_assessment.rps_score
    assert entry_assessment.signal_contributions.get("direction_entry", 0.0) > 0.0
    assert exit_assessment.signal_contributions.get("direction_exit", 0.0) < 0.0
    assert (entry_assessment.rps_score - exit_assessment.rps_score) >= 20.0  # +10 vs -15 = 25 pt diff


# ── Test H: Overlapping zones -> one consolidated incident ──────────────────

def test_overlapping_zones_produce_single_incident():
    """A track triggering multiple overlapping zones produces exactly 1 consolidated incident."""
    pipeline = CameraPipeline(camera_id=5, camera_name="BOP-OverlapCam", stream_url="rtsp://dummy")
    pipeline.zone_fence = MagicMock()

    overlapping_events = [
        {"event_type": "zone_intrusion", "zone_id": 101, "zone_name": "Outer Sector", "severity": 0.4},
        {"event_type": "zone_intrusion", "zone_id": 102, "zone_name": "Inner Sector", "severity": 0.95},
        {"event_type": "direction_violation", "zone_id": 103, "zone_name": "Perimeter Wire", "severity": 0.7},
    ]

    generated_incidents = []
    pipeline._generate_incident = MagicMock(side_effect=lambda **kwargs: generated_incidents.append(kwargs))

    track = {
        "track_id": 42,
        "target_id": "T-0042",
        "class_name": "person",
        "confidence": 0.91,
        "bbox": [100, 100, 150, 200],
        "center": [125, 150],
        "ground_anchor": [125, 200],
        "dwell_time": 2.5,
        "current_zone_events": overlapping_events,
    }

    pipeline._check_threat(track, frame_id=25, inference_ms=8.0)

    assert len(generated_incidents) == 1, "Exactly one consolidated incident must be created"
    inc = generated_incidents[0]
    assert inc["zone_name"] == "Inner Sector", "Highest severity zone must be resolved as primary zone"
    assert inc["score"] >= 65.0


# ── Test I: RPS explainability fields are complete ──────────────────────────

def test_rps_explainability_fields_completeness():
    """Verify RPSAssessment exposes full explainability contracts."""
    assessment = compute_threat_score(
        signals={"confidence": 0.88, "boundary_crossing": 1.0, "vehicle_context": 1.0},
        context={
            "zone_type": "BUFFER",
            "zone_name": "Buffer Zone Bravo",
            "object_type": "truck",
            "direction": "inward",
            "consecutive_frames": 6,
            "dwell_time": 2.0,
        },
    )

    # Core RPS fields
    assert isinstance(assessment.rps_score, float)
    assert assessment.priority_level in ("LOW", "MEDIUM", "HIGH", "CRITICAL")
    assert isinstance(assessment.evidence_confidence, float)
    assert isinstance(assessment.signal_contributions, dict)
    assert len(assessment.signal_contributions) > 0
    assert isinstance(assessment.explainability_facts, list)
    assert len(assessment.explainability_facts) > 0
    assert isinstance(assessment.missing_evidence, list)
    assert len(assessment.missing_evidence) > 0
    assert isinstance(assessment.operator_guidance, str)
    assert len(assessment.operator_guidance) > 0
    assert isinstance(assessment.spatial_gate, float)
    assert isinstance(assessment.temporal_factor, float)
    assert isinstance(assessment.ai_assessment, dict)

    # Dual contract synchronization
    assert assessment.score == assessment.rps_score
    assert assessment.severity == assessment.priority_level
    assert assessment.confidence == assessment.evidence_confidence
    assert assessment.reasons == assessment.explainability_facts
    assert assessment.recommended_action == assessment.operator_guidance


# ── Test J: No prohibited autonomous enforcement language ───────────────────

def test_no_prohibited_language_in_rps_assessment():
    """Verify all RPS outputs avoid prohibited autonomous intercept/intent terminology."""
    prohibited_words = [
        "suspect",
        "positively identified",
        "guilt",
        "criminal intent",
        "arrest",
        "neutralize",
        "shoot",
        "eliminate",
        "lethal",
        "intercept protocol",
        "criminal certainty",
    ]

    for zone_type in ("RESTRICTED", "BUFFER", "MONITORED", "PUBLIC"):
        assessment = compute_threat_score(
            signals={"confidence": 0.99, "boundary_crossing": 1.0, "night": 1.0, "loitering": 1.0},
            context={"zone_type": zone_type, "object_type": "person", "direction": "inward"},
        )

        all_text = " ".join([
            assessment.operator_guidance,
            assessment.recommended_action,
            " ".join(assessment.explainability_facts),
            " ".join(assessment.missing_evidence),
            str(assessment.ai_assessment),
        ]).lower()

        for word in prohibited_words:
            assert word not in all_text, f"Prohibited word '{word}' found in RPS output: {all_text}"


# ── Test K: RPS remains strictly bounded in [0.0, 100.0] ────────────────────

def test_rps_remains_strictly_bounded():
    """RPS score must always be bounded in [0.0, 100.0] regardless of input extremes."""
    # Extreme positive inputs
    huge_signals = {
        "confidence": 10.0,
        "zone_severity": 10.0,
        "boundary_crossing": 10.0,
        "loitering": 10.0,
        "night": 10.0,
        "vehicle_context": 10.0,
        "behavior_anomaly": 10.0,
    }
    assessment_max = compute_threat_score(huge_signals, context={"zone_type": "RESTRICTED", "dwell_time": 999.0})
    assert 0.0 <= assessment_max.rps_score <= 100.0

    # Negative / anomalous inputs
    neg_signals = {
        "confidence": -5.0,
        "zone_severity": -1.0,
        "boundary_crossing": -1.0,
        "direction_exit": -50.0,
    }
    assessment_neg = compute_threat_score(neg_signals, context={"zone_type": "PUBLIC"})
    assert 0.0 <= assessment_neg.rps_score <= 100.0
    assert assessment_neg.rps_score == 0.0


# ── Test L: Missing evidence is explicitly represented in output ─────────────

def test_missing_evidence_is_explicitly_represented():
    """RPS Assessment must explicitly disclose unacquired or absent evidence sources."""
    assessment = compute_threat_score(
        signals={"confidence": 0.85, "boundary_crossing": 1.0},
        context={
            "zone_type": "RESTRICTED",
            "object_type": "person",
            "plate_text": None,
            "face_candidate": None,
            "cross_camera_corroborated": False,
            "thermal_confirmed": False,
        },
    )

    assert len(assessment.missing_evidence) >= 4
    assert any("license plate" in e.lower() or "anpr" in e.lower() for e in assessment.missing_evidence)
    assert any("facial recognition" in e.lower() or "identity" in e.lower() for e in assessment.missing_evidence)
    assert any("cross-camera" in e.lower() for e in assessment.missing_evidence)
    assert any("thermal" in e.lower() for e in assessment.missing_evidence)
