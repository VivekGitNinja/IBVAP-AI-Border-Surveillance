"""Tests for the explainable threat scoring engine."""

from backend.app.services.scoring import (
    compute_threat_score, get_severity_for_score,
    DEFAULT_WEIGHTS, SEVERITY_THRESHOLDS,
)


def test_zero_signals_gives_low():
    """No signals should produce LOW severity."""
    result = compute_threat_score({})
    assert result.score == 0.0
    assert result.severity == "LOW"
    assert result.confidence == 0.0


def test_max_signals_gives_critical():
    """All signals at max should produce CRITICAL."""
    signals = {k: 1.0 for k in DEFAULT_WEIGHTS}
    result = compute_threat_score(signals)
    assert result.score >= 85
    assert result.severity == "CRITICAL"
    assert result.confidence > 0


def test_zone_severity_high_impact():
    """Zone severity is the highest-weighted signal."""
    signals_only_zone = {"zone_severity": 1.0}
    signals_only_behavior = {"behavior_anomaly": 1.0}
    r1 = compute_threat_score(signals_only_zone)
    r2 = compute_threat_score(signals_only_behavior)
    assert r1.score > r2.score


def test_reasons_populated():
    """Non-zero signals should produce reason strings."""
    signals = {"confidence": 0.8, "night": 1.0, "loitering": 0.5}
    result = compute_threat_score(signals)
    assert len(result.reasons) >= 2


def test_severity_thresholds():
    """Score-to-severity mapping should match thresholds."""
    assert get_severity_for_score(90) == "CRITICAL"
    assert get_severity_for_score(70) == "HIGH"
    assert get_severity_for_score(50) == "MEDIUM"
    assert get_severity_for_score(10) == "LOW"


def test_ai_assessment_structure():
    """AI assessment should contain required fields."""
    signals = {"confidence": 0.9, "zone_severity": 1.0}
    ctx = {"object_type": "person", "zone_name": "Restricted Zone", "behavior": "loitering"}
    result = compute_threat_score(signals, context=ctx)
    assert "detected" in result.ai_assessment
    assert "confidence" in result.ai_assessment
    assert "context" in result.ai_assessment
    assert "behavior" in result.ai_assessment
    assert "human_action" in result.ai_assessment


def test_recommended_action_scales_with_severity():
    """Critical incidents should have more urgent recommendations."""
    low = compute_threat_score({"confidence": 0.1})
    high = compute_threat_score({
        "confidence": 1.0, "zone_severity": 1.0, "boundary_crossing": 1.0,
        "loitering": 1.0, "night": 1.0, "behavior_anomaly": 1.0,
    })
    assert "IMMEDIATE" in high.recommended_action or "Verify" in high.recommended_action
    assert "monitoring" in low.recommended_action.lower() or "Monitor" in low.recommended_action


def test_canonical_spatial_gated_multiplicative_formula():
    """Assert canonical RPS formula:
    RPS = G_spatial * (B_base + Delta_dir + Delta_dwell + Delta_env) * T_persistence * Omega_operator
    Reconciles executable code with documentation.
    """
    # 1. RESTRICTED zone with breach, inward approach, night bonus:
    # G_spatial = 1.0
    # B_base = 70.0 (boundary crossing)
    # Delta_dir = 10.0 (inward)
    # Delta_env = 5.0 (night)
    # T_persistence = 1.0 (confirmed multi-frame)
    # Omega_operator = 1.0
    # RPS = 1.0 * (70 + 10 + 5) * 1.0 * 1.0 = 85.0
    signals = {"boundary_crossing": 1.0, "night": 1.0}
    ctx = {
        "zone_type": "RESTRICTED",
        "direction": "inward",
        "consecutive_frames": 10,
        "operator_suppressed": False,
    }
    assessment = compute_threat_score(signals, context=ctx)
    assert assessment.rps_score == 85.0
    assert assessment.priority_level == "CRITICAL"

    # 2. BUFFER zone scaling (G_spatial = 0.65)
    # RPS = round(0.65 * 85.0, 1) = round(55.25, 1) = 55.2 (banker's rounding)
    ctx["zone_type"] = "BUFFER"
    assessment_buffer = compute_threat_score(signals, context=ctx)
    assert assessment_buffer.rps_score == 55.2
    assert assessment_buffer.priority_level == "MEDIUM"

    # 3. PUBLIC zone (G_spatial = 0.0) strictly zero
    ctx["zone_type"] = "PUBLIC"
    assessment_public = compute_threat_score(signals, context=ctx)
    assert assessment_public.rps_score == 0.0
    assert assessment_public.priority_level == "LOW"

    # 4. Temporal jitter damping factor (T_persistence = 0.40)
    ctx["zone_type"] = "BUFFER"
    ctx["is_single_frame_jitter"] = True
    assessment_jitter = compute_threat_score(signals, context=ctx)
    # RPS = 0.65 * 85.0 * 0.40 = 22.1
    assert assessment_jitter.rps_score == 22.1
    assert assessment_jitter.priority_level == "LOW"
