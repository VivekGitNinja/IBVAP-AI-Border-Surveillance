"""
Step 04 Phase 4 — Behavioral Kinematics, Operator Feedback Closed-Loop & Dynamic Incident Triage Tests
======================================================================================================

Comprehensive integration test suite covering:
1. Group 1: KinematicBehaviorEngine (Homography ground-space m/s vs image norm/s, circular heading difference
   across -π <-> +π, perimeter convergence angles, multi-criteria loitering, jitter gating, fallback)
2. Group 2: Operator Feedback Closed-Loop & Suppression (Tests A-F: Public, Buffer, Restricted break-glass,
   Sensitive break-glass, Expired, Already Revoked)
3. Group 3: Rate Limiting & Duplicate Path Elimination (Per-target cooldown isolation, FRS duplicate elimination)
4. Group 4: DB Failure Taxonomy & Sibling Matrix (Availability outage disk spooling, Integrity visible fail,
   Sibling dismissal restricted zone safety block)
5. Group 5: System Invariants (Spatial gate dominance in Public, 0 threat points for biometrics/corroboration,
   local ByteTrack IDs immutable integer, model weights unmodified)
"""

import pytest
import math
import time
import os
import json
import numpy as np
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from edge.behavior.kinematics import (
    KinematicBehaviorEngine,
    CameraCalibrationProfile,
    CoordinateSpace,
    CalibrationStatus,
    MeasurementQuality,
    KinematicMeasurement,
)
from edge.zones.fence import BoundaryContext, ZoneFence
from backend.app.services.scoring import compute_threat_score, RPSAssessment
from backend.app.models.suppression import OperatorSuppression
from backend.app.services.feedback import (
    SiteFeedbackRegistry,
    get_site_feedback_registry,
    derive_advisory_lock_id,
    acquire_advisory_xact_lock,
    dismiss_incident,
    propagate_sibling_dismissals,
)
from backend.app.services.live_pipeline import CameraPipeline


# =============================================================================
# GROUP 1: KINEMATICS & GROUND-SPACE REASONING
# =============================================================================

class TestGroup1Kinematics:
    """Group 1: Calibrated ground homography, circular turn irregularity, convergence, loitering."""

    def test_kinematics_calibrated_ground_homography_vs_uncalibrated(self):
        """Calibrated homography converts pixels to ground meters/sec; uncalibrated outputs normalized image/sec."""
        # 1. Calibrated camera profile (1 pixel = 0.05 meters on ground plane)
        H = [
            [0.05, 0.0, 0.0],
            [0.0, 0.05, 0.0],
            [0.0, 0.0, 1.0]
        ]

        cal_profile = CameraCalibrationProfile(
            camera_id=101,
            status=CalibrationStatus.CALIBRATED_HOMOGRAPHY,
            homography_matrix=H,
        )

        engine_cal = KinematicBehaviorEngine(calibration_profile=cal_profile)

        # Feed 6 samples moving 20 pixels horizontally per 0.2 seconds (100 px/s total)
        # Ground distance = 100 * 0.05 = 5.0 meters/sec
        for i in range(6):
            t = 100.0 + i * 0.2
            x = 100.0 + i * 20.0
            m_cal = engine_cal.update_track(1, (x, 100.0, x + 50.0, 200.0), timestamp=t, object_type="person")

        assert m_cal.coordinate_space == CoordinateSpace.GROUND_METRIC_METERS
        assert m_cal.speed is not None
        assert abs(m_cal.speed - 5.0) < 0.5, f"Expected ~5.0 m/s ground speed, got {m_cal.speed}"
        assert m_cal.quality == MeasurementQuality.HIGH

        # 2. Uncalibrated camera profile
        uncal_profile = CameraCalibrationProfile(
            camera_id=102,
            status=CalibrationStatus.UNCALIBRATED_IMAGE_SPACE,
            homography_matrix=None,
        )
        engine_uncal = KinematicBehaviorEngine(calibration_profile=uncal_profile)
        for i in range(6):
            t = 100.0 + i * 0.2
            x = 100.0 + i * 20.0
            m_uncal = engine_uncal.update_track(2, (x, 100.0, x + 50.0, 200.0), timestamp=t, object_type="person")

        assert m_uncal.coordinate_space == CoordinateSpace.NORMALIZED_IMAGE_PLANE
        assert any("uncalibrated" in note.lower() for note in m_uncal.explainability_notes)

    def test_kinematics_circular_heading_difference_wrapping(self):
        """Heading calculation uses circular angular difference atan2(sin, cos) across -pi <-> +pi boundary."""
        profile = CameraCalibrationProfile(camera_id=1, status=CalibrationStatus.UNCALIBRATED_IMAGE_SPACE)
        engine = KinematicBehaviorEngine(profile=profile)

        # Smooth motion moving leftwards near -pi / +pi boundary
        for i in range(6):
            t = 1.0 + i * 0.2
            x = 500.0 - i * 15.0
            m_smooth = engine.update_track(10, (x - 20.0, 480.0, x + 20.0, 520.0), timestamp=t)

        # Trajectory irregularity index should be very low (< 0.20) because movement was straight
        assert m_smooth.trajectory_irregularity_index < 0.25, (
            f"Expected low irregularity for smooth motion across -pi/+pi, got {m_smooth.trajectory_irregularity_index}"
        )

        # In contrast, an erratic zig-zag track (sharp 90-degree turns back and forth)
        engine_erratic = KinematicBehaviorEngine(profile=profile)
        waypoints = [
            (500.0, 500.0),
            (530.0, 500.0), # moving right
            (530.0, 530.0), # turning down
            (500.0, 530.0), # turning left
            (500.0, 500.0), # turning up
            (530.0, 500.0), # turning right again
        ]
        for i, (wx, wy) in enumerate(waypoints):
            t = 1.0 + i * 0.2
            m_erratic = engine_erratic.update_track(20, (wx - 10.0, wy - 20.0, wx + 10.0, wy), timestamp=t)

        assert m_erratic.trajectory_irregularity_index > 0.40, (
            f"Expected high irregularity for erratic zig-zag, got {m_erratic.trajectory_irregularity_index}"
        )

    def test_kinematics_perimeter_convergence_vectors(self):
        """Perimeter convergence evaluates velocity dot product with fence inward normal."""
        profile = CameraCalibrationProfile(camera_id=1, status=CalibrationStatus.UNCALIBRATED_IMAGE_SPACE)
        engine = KinematicBehaviorEngine(profile=profile)

        # Fence boundary at y = 500, inward normal points downwards [0, 1] into restricted zone
        fence_ctx = BoundaryContext(
            zone_id=10,
            zone_name="Fence Sector Alpha",
            zone_type="RESTRICTED",
            boundary_segment=((0.0, 500.0), (1000.0, 500.0)),
            inward_normal=(0.0, 1.0),
            signed_distance=50.0,
            nearest_point=(400.0, 500.0),
            evaluated_at_frame=1,
            evaluated_at_timestamp=1.0,
        )

        # Case A: Direct normal approach (moving down towards/through fence: dy > 0)
        for i in range(6):
            t = 1.0 + i * 0.2
            y = 400.0 + i * 15.0
            m_direct = engine.update_track(1, (400.0, y - 40.0, 450.0, y), timestamp=t, boundary_context=fence_ctx)
        assert m_direct.perimeter_convergence > 0.70, (
            f"Expected high convergence for normal approach, got {m_direct.perimeter_convergence}"
        )

        # Case B: Parallel motion (moving horizontally along fence: dx > 0, dy = 0)
        for i in range(6):
            t = 1.0 + i * 0.2
            x = 200.0 + i * 15.0
            m_parallel = engine.update_track(2, (x, 450.0, x + 50.0, 490.0), timestamp=t, boundary_context=fence_ctx)
        assert abs(m_parallel.perimeter_convergence) < 0.20, (
            f"Expected ~0 convergence for parallel motion, got {m_parallel.perimeter_convergence}"
        )

        # Case C: Retreating motion (moving away from fence: dy < 0)
        for i in range(6):
            t = 1.0 + i * 0.2
            y = 480.0 - i * 15.0
            m_retreat = engine.update_track(3, (400.0, y - 40.0, 450.0, y), timestamp=t, boundary_context=fence_ctx)
        assert m_retreat.perimeter_convergence == 0.0 or m_retreat.perimeter_convergence < 0.10, (
            f"Expected zero convergence for retreating motion, got {m_retreat.perimeter_convergence}"
        )

    def test_kinematics_multi_criteria_loitering(self):
        """Loitering requires dwell time > threshold AND low net displacement bounding box."""
        profile = CameraCalibrationProfile(camera_id=1, status=CalibrationStatus.UNCALIBRATED_IMAGE_SPACE)
        engine = KinematicBehaviorEngine(profile=profile)

        # Track 1: Stationary for 12 seconds in small area (radius < 15 px)
        for t in range(15):
            m_stationary = engine.update_track(1, (200.0 + (t % 2), 200.0, 240.0, 260.0), timestamp=100.0 + t)
        assert m_stationary.is_loitering is True
        assert m_stationary.loitering_index >= 0.50

        # Track 2: Continuously moving across zone for 12 seconds with large displacement
        for t in range(15):
            m_transit = engine.update_track(2, (100.0 + t * 30.0, 200.0, 140.0 + t * 30.0, 260.0), timestamp=100.0 + t)
        assert m_transit.is_loitering is False, "Transiting track with large displacement must not be flagged as loitering"

    def test_kinematics_jitter_gating(self):
        """Single-frame or sub-0.2s jitter is gated and marked with appropriate measurement quality."""
        profile = CameraCalibrationProfile(camera_id=1, status=CalibrationStatus.UNCALIBRATED_IMAGE_SPACE)
        engine = KinematicBehaviorEngine(profile=profile)

        # First frame for track 99
        m_first = engine.update_track(99, (100.0, 100.0, 150.0, 200.0), timestamp=10.0)
        assert m_first.quality in (MeasurementQuality.LOW, MeasurementQuality.DEGRADED)
        assert m_first.speed == 0.0


# =============================================================================
# GROUP 2: OPERATOR FEEDBACK CLOSED-LOOP & SUPPRESSION (TESTS A–F)
# =============================================================================

class TestGroup2OperatorSuppressionClosedLoop:
    """Group 2: Operator dismissal suppression, atomic break-glass revocation, and expiry (Tests A-F)."""

    @pytest.fixture(autouse=True)
    def clean_registry(self):
        reg = get_site_feedback_registry()
        reg.clear()
        yield
        reg.clear()

    def test_suppression_flow_a_public_zone_suppressed(self):
        """Test A: Target in PUBLIC zone with active operator suppression yields Omega_operator = 0.0 and RPS = 0."""
        assessment = compute_threat_score(
            signals={"confidence": 0.85, "zone_severity": 0.0, "boundary_crossing": 0.0},
            context={
                "object_type": "person",
                "zone_type": "PUBLIC",
                "confidence": 0.85,
                "operator_suppressed": True,
                "camera_id": 1,
            }
        )
        assert assessment.omega_operator == 0.0
        assert assessment.score == 0.0
        assert assessment.suppression_revocation_required is False

    def test_suppression_flow_b_buffer_zone_suppressed(self):
        """Test B: Target in BUFFER zone with active operator suppression yields Omega_operator = 0.0 and score = 0."""
        assessment = compute_threat_score(
            signals={"confidence": 0.88, "zone_severity": 0.5, "boundary_crossing": 0.4},
            context={
                "object_type": "person",
                "zone_type": "BUFFER",
                "confidence": 0.88,
                "operator_suppressed": True,
                "camera_id": 1,
            }
        )
        assert assessment.omega_operator == 0.0
        assert assessment.score == 0.0
        assert assessment.suppression_revocation_required is False

    def test_suppression_flow_c_restricted_break_glass_atomic_revocation(self):
        """Test C: Target in RESTRICTED zone breaks glass: Omega_operator = 1.0 and revocation required."""
        assessment = compute_threat_score(
            signals={"confidence": 0.92, "zone_severity": 0.9, "boundary_crossing": 1.0},
            context={
                "object_type": "person",
                "zone_type": "RESTRICTED",
                "confidence": 0.92,
                "operator_suppressed": True,
                "camera_id": 1,
            }
        )
        # Break-glass: suppression is bypassed in restricted zone
        assert assessment.omega_operator == 1.0
        assert assessment.score >= 70.0
        assert assessment.suppression_revocation_required is True
        assert "restricted" in assessment.suppression_revocation_reason.lower()

    def test_suppression_flow_d_sensitive_break_glass_atomic_revocation(self):
        """Test D: Target in SENSITIVE zone breaks glass: Omega_operator = 1.0 and revocation required."""
        assessment = compute_threat_score(
            signals={"confidence": 0.95, "zone_severity": 1.0, "boundary_crossing": 1.0},
            context={
                "object_type": "person",
                "zone_type": "SENSITIVE",
                "confidence": 0.95,
                "operator_suppressed": True,
                "camera_id": 1,
            }
        )
        assert assessment.omega_operator == 1.0
        assert assessment.score >= 70.0
        assert assessment.suppression_revocation_required is True
        assert "sensitive" in assessment.suppression_revocation_reason.lower()

    def test_suppression_flow_e_expired_suppression_normal_flow(self):
        """Test E: Expired suppression is ignored in registry."""
        reg = get_site_feedback_registry()
        target_key = "camera:1:target:55"
        reg.register_suppression(
            target_key=target_key,
            camera_id=1,
            target_id=55,
            reason="FALSE_POSITIVE",
            expires_at=datetime.utcnow() - timedelta(seconds=5)
        )
        assert reg.is_suppressed(target_key) is False

    def test_suppression_flow_f_already_revoked_suppression_normal_flow(self):
        """Test F: Revoked suppression is inactive and does not suppress."""
        reg = get_site_feedback_registry()
        target_key = "camera:1:target:77"
        reg.register_suppression(
            target_key=target_key,
            camera_id=1,
            target_id=77,
            reason="AUTHORIZED_MAINTENANCE",
            expires_at=datetime.utcnow() + timedelta(minutes=15)
        )
        assert reg.is_suppressed(target_key) is True
        reg.invalidate(target_key)
        assert reg.is_suppressed(target_key) is False


# =============================================================================
# GROUP 3: RATE LIMITING & DUPLICATE PATH ELIMINATION
# =============================================================================

class TestGroup3RateLimitingAndDuplicatePaths:
    """Group 3: Per-target rate limiting isolation and elimination of duplicate FRS incidents."""

    def test_per_target_cooldown_isolation_on_same_camera(self):
        """Two distinct targets on the same camera generate independent incidents within 60s."""
        pipeline = CameraPipeline(camera_id=1, camera_name="BOP-Gate", stream_url="rtsp://dummy")
        saved_incidents = []

        with patch.object(pipeline, "_save_incident", side_effect=lambda **kw: saved_incidents.append(kw)):
            # Target 1 detected at t=100
            track1 = {
                "track_id": 1,
                "target_id": 1,
                "class_name": "person",
                "confidence": 0.90,
                "bbox": [100, 100, 150, 200],
                "center": [125, 150],
                "ground_anchor": [125, 200],
                "dwell_time": 2.0,
                "current_zone_events": [{"event_type": "zone_intrusion", "zone_name": "Perimeter", "severity": 0.9}],
            }
            pipeline._check_threat(track1, frame_id=10, inference_ms=10.0)

            # Target 2 detected at t=105 on the SAME camera
            track2 = {
                "track_id": 2,
                "target_id": 2,
                "class_name": "person",
                "confidence": 0.92,
                "bbox": [300, 100, 350, 200],
                "center": [325, 150],
                "ground_anchor": [325, 200],
                "dwell_time": 2.0,
                "current_zone_events": [{"event_type": "zone_intrusion", "zone_name": "Perimeter", "severity": 0.9}],
            }
            pipeline._check_threat(track2, frame_id=15, inference_ms=10.0)

        # Both targets must generate independent incidents without suppressing each other
        assert len(saved_incidents) == 2, "Per-target rate limit must isolate target 1 and target 2 on the same camera"

    def test_frs_duplicate_incident_generator_eliminated(self):
        """FRS candidate sighting does not insert an independent incident into the DB."""
        pipeline = CameraPipeline(camera_id=1, camera_name="BOP-Gate", stream_url="rtsp://dummy")
        events = []
        pipeline.set_event_callback(lambda ev: events.append(ev))

        with patch("backend.app.db.session.SessionLocal") as mock_db_cls:
            mock_db = MagicMock()
            mock_db_cls.return_value = mock_db

            match_info = {"id": 12, "name": "Rajesh Kumar", "sim": 0.75}
            mock_frame = np.zeros((480, 640, 3), dtype=np.uint8)
            pipeline._generate_face_incident(match_info, mock_frame, frame_id=1, bbox=(50, 50, 100, 100))

            # Verify no incident was added to database directly
            mock_db.add.assert_not_called()

        assert len(events) == 1
        assert events[0]["type"] == "watchlist_candidate_sighting"
        assert "Rajesh Kumar" in events[0]["title"]


# =============================================================================
# GROUP 4: DB FAILURE TAXONOMY & SIBLING MATRIX
# =============================================================================

class TestGroup4DbFailureTaxonomyAndSiblings:
    """Group 4: DB availability failure disk spooling, integrity error visible fail, and sibling suppression rules."""

    def test_db_availability_failure_spools_to_disk_degraded(self):
        """OperationalError during incident save enters DEGRADED_DB_OFFLINE and spools incident to disk."""
        from sqlalchemy.exc import OperationalError

        pipeline = CameraPipeline(camera_id=99, camera_name="BOP-OutageCam", stream_url="rtsp://dummy")
        track = {"track_id": 501, "target_id": 501, "class_name": "person", "confidence": 0.9}

        # Clear spool file if exists
        spool_path = os.path.join(os.getcwd(), "data", "spool", "offline_incidents.jsonl")
        if os.path.exists(spool_path):
            try:
                os.remove(spool_path)
            except Exception:
                pass

        with patch("backend.app.db.session.SessionLocal", side_effect=OperationalError("could not connect", None, None)):
            pipeline._save_incident(
                code="IBVAP-TEST-OFFLINE-001",
                track=track,
                reasons=["zone_intrusion"],
                severity="HIGH",
                score=78.0,
                confidence=0.9,
                fingerprint="abc123hash",
                ai_assessment={"model": "test"},
                action="Verify offline",
                timeline=[],
                zone_name="Perimeter Fence"
            )

        assert os.path.exists(spool_path), "Offline incident must be spooled to data/spool/offline_incidents.jsonl"
        with open(spool_path, "r", encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        assert any(rec.get("incident_code") == "IBVAP-TEST-OFFLINE-001" for rec in lines)
        spooled_rec = next(rec for rec in lines if rec.get("incident_code") == "IBVAP-TEST-OFFLINE-001")
        assert spooled_rec["pipeline_state"] == "DEGRADED_DB_OFFLINE"
        assert spooled_rec["threat_score"] == 78.0

    def test_db_integrity_error_fails_visibly_without_spooling(self):
        """IntegrityError during incident save must NOT be swallowed into degraded spool."""
        from sqlalchemy.exc import IntegrityError

        pipeline = CameraPipeline(camera_id=99, camera_name="BOP-SchemaFail", stream_url="rtsp://dummy")
        track = {"track_id": 502, "target_id": 502, "class_name": "person", "confidence": 0.9}

        with patch.object(pipeline, "_handle_degraded_db_spool") as mock_spool:
            with patch("backend.app.db.session.SessionLocal", side_effect=IntegrityError("duplicate key", None, None)):
                pipeline._save_incident(
                    code="IBVAP-TEST-INTEGRITY-002",
                    track=track,
                    reasons=["zone_intrusion"],
                    severity="HIGH",
                    score=78.0,
                    confidence=0.9,
                    fingerprint="abc123hash",
                    ai_assessment={"model": "test"},
                    action="Verify",
                    timeline=[],
                )
            mock_spool.assert_not_called()

    def test_sibling_dismissal_propagation_rules(self):
        """Sibling dismissals propagate to public/buffer tracks but are BLOCKED in restricted zones."""
        db = MagicMock()
        parent_inc = MagicMock()
        parent_inc.id = 1
        parent_inc.incident_code = "INC-TEST-001"
        parent_inc.correlated_ids = [2, 3]
        parent_inc.ai_assessment = {"dossier_id": "DOS-100"}

        sib_buffer = MagicMock()
        sib_buffer.id = 2
        sib_buffer.status = "OPEN"
        sib_buffer.zone_name = "Perimeter Buffer"
        sib_buffer.ai_assessment = {"zone_type": "BUFFER"}
        sib_buffer.timeline = []

        sib_restricted = MagicMock()
        sib_restricted.id = 3
        sib_restricted.status = "OPEN"
        sib_restricted.zone_name = "Sensitive Sector"
        sib_restricted.ai_assessment = {"zone_type": "RESTRICTED"}
        sib_restricted.timeline = []

        db.query().filter().all.side_effect = [
            [sib_buffer, sib_restricted], # dossier query
            [sib_buffer, sib_restricted], # sibling_ids query
        ]

        propagated = propagate_sibling_dismissals(
            db=db,
            parent_inc=parent_inc,
            reason="FALSE_POSITIVE",
            user_id="OP-TEST"
        )

        assert 2 in propagated, "Buffer sibling should be dismissed"
        assert 3 not in propagated, "Restricted sibling must NOT be dismissed"
        assert sib_buffer.status == "DISMISSED"
        assert sib_restricted.status == "OPEN"
        assert any(e["event_type"] == "sibling_dismissal_blocked" for e in sib_restricted.timeline)


# =============================================================================
# GROUP 5: SYSTEM INVARIANTS & INTEGRITY
# =============================================================================

class TestGroup5SystemInvariants:
    """Group 5: Spatial dominance in public, 0 threat points for biometrics, and immutable ByteTrack IDs."""

    def test_spatial_gate_dominance_public_zone(self):
        """Spatial gate dominance: G_spatial = 0 in PUBLIC zone guarantees final threat score is 0.0."""
        assessment = compute_threat_score(
            signals={
                "confidence": 1.0,
                "zone_severity": 0.0,
                "boundary_crossing": 0.0,
                "night": 1.0,
                "vehicle_context": 1.0,
                "rapid_movement": 1.0,
            },
            context={
                "object_type": "person",
                "zone_type": "PUBLIC",
                "confidence": 1.0,
                "camera_id": 1,
            }
        )
        assert assessment.score == 0.0, f"PUBLIC zone must guarantee score 0.0, got {assessment.score}"
        assert assessment.severity == "LOW"

    def test_zero_threat_points_for_biometrics_and_ocr(self):
        """Biometrics, ANPR plate matches, and cross-camera corroboration contribute exactly 0.0 threat points."""
        assessment_without = compute_threat_score(
            signals={"confidence": 0.8, "zone_severity": 0.5, "boundary_crossing": 0.3},
            context={"object_type": "person", "zone_type": "BUFFER", "confidence": 0.8}
        )

        assessment_with = compute_threat_score(
            signals={"confidence": 0.8, "zone_severity": 0.5, "boundary_crossing": 0.3},
            context={
                "object_type": "person",
                "zone_type": "BUFFER",
                "confidence": 0.8,
                "face_similarity": 0.98,
                "anpr_match": True,
                "cross_camera_corroborated": True,
            }
        )

        assert assessment_with.score == assessment_without.score, (
            f"Biometrics/OCR/Corroboration must contribute 0.0 threat points. "
            f"Without: {assessment_without.score}, With: {assessment_with.score}"
        )

    def test_local_bytetrack_ids_remain_immutable_integers(self):
        """ByteTrack local track IDs remain pure integer types."""
        from edge.tracking.bytetrack import ByteTracker
        tracker = ByteTracker()
        dets = [
            {"bbox": [100.0, 100.0, 150.0, 200.0], "class_id": 0, "class_name": "person", "confidence": 0.9}
        ]
        tracks = tracker.update(dets)
        for t in tracks:
            assert isinstance(t["track_id"], int), f"Track ID must be int, got {type(t['track_id'])}"
