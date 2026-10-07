"""
Live Camera Health Diagnostics Integration Test Suite.

Validates Milestone 1C / Step 03:
1. Healthy live stream remains HEALTHY.
2. Stream read failure produces the appropriate offline state/event.
3. Frozen-frame condition is detected using the actual health mechanism.
4. Blur condition is detected if supported.
5. Brightness degradation is detected if supported.
6. Tamper detection behavior tested and architectural gap documented.
7. Health state transitions are debounced and do not alert every frame.
8. Recovery from unhealthy -> healthy works.
9. Camera A health state does not affect Camera B.
10. Stop/restart clears stale health state.
11. Health events remain separate from intrusion/threat events.
12. Existing WebSocket/database/observability contracts remain compatible.
"""

from datetime import datetime
import json
import numpy as np
import cv2
import pytest

from backend.app.services.live_pipeline import CameraPipeline
from edge.detection.base import Detection
from edge.health.checks import CameraHealthChecker, health_score


class DummyDetector:
    """Mock detector returning configurable detections."""
    def __init__(self, detections=None):
        self._detections = detections or []

    def set_detections(self, detections):
        self._detections = detections

    def detect(self, frame: np.ndarray, frame_id: int):
        return self._detections

    def is_available(self) -> bool:
        return True


def _generate_healthy_frame(seed: int = 42) -> np.ndarray:
    """Generate a realistic textured synthetic frame with sharp edges and normal brightness."""
    rng = np.random.RandomState(seed)
    frame = rng.randint(80, 180, (480, 640, 3), dtype=np.uint8)
    # Add high-contrast geometric patterns to guarantee sharp Laplacian edges (variance > 100)
    for i in range(10, 470, 40):
        cv2.line(frame, (10, i), (630, i), (255, 255, 255), 2)
        cv2.line(frame, (i, 10), (i, 470), (0, 0, 0), 2)
    cv2.putText(frame, f"SURVEILLANCE SEED {seed}", (50, 240),
                cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 3)
    return frame


def test_healthy_live_stream_remains_healthy():
    """Requirement 1: Healthy live stream remains HEALTHY with high health score."""
    pipeline = CameraPipeline(camera_id=301, stream_url="demo://test301", camera_name="Tower 1 Alpha")
    detector = DummyDetector([])

    # Feed 5 consecutive distinct healthy frames
    for f in range(1, 6):
        frame = _generate_healthy_frame(seed=f * 17)
        pipeline._process_frame(detector, frame, frame_id=f)

    health = pipeline.get_health()
    assert health["status"] == "HEALTHY"
    assert health["substate"] == "HEALTHY"
    assert health["health_score"] >= 80.0
    assert health["camera_id"] == 301
    assert health["blur_score"] >= 50.0  # Sharp edges
    assert 50.0 <= health["brightness"] <= 200.0  # Normal lighting


def test_stream_read_failure_produces_offline_state_and_event():
    """Requirement 2: Stream read failure produces the appropriate offline state/event."""
    pipeline = CameraPipeline(camera_id=302, stream_url="rtsp://invalid/offline", camera_name="Tower 2 Bravo")
    received_events = []
    pipeline.set_event_callback(lambda evt: received_events.append(evt))

    # Simulate consecutive stream read failures (frame is None)
    # 1st read failure (debouncing in progress)
    pipeline.evaluate_health(None, frame_id=1)
    assert pipeline._health_state == "HEALTHY"  # Debounce not yet exceeded

    # 2nd read failure
    pipeline.evaluate_health(None, frame_id=2)
    assert pipeline._health_state == "HEALTHY"

    # 3rd read failure: crosses 3-failure threshold -> transitions to OFFLINE
    pipeline.evaluate_health(None, frame_id=3)
    assert pipeline._health_state == "OFFLINE"
    assert pipeline._health_substate == "OFFLINE"
    assert pipeline.get_health()["health_score"] == 0.0

    # Verify camera_health event was dispatched
    health_events = [e for e in received_events if e.get("type") == "camera_health"]
    assert len(health_events) >= 1
    ev = health_events[-1]
    assert ev["camera_id"] == 302
    assert ev["status"] == "OFFLINE"
    assert ev["substate"] == "OFFLINE"
    assert ev["health_score"] == 0.0


def test_frozen_frame_detected_using_actual_health_mechanism():
    """Requirement 3: Frozen-frame condition is detected using actual frame difference logic."""
    pipeline = CameraPipeline(camera_id=303, stream_url="demo://test303", camera_name="Tower 3 Charlie")
    detector = DummyDetector([])
    received_events = []
    pipeline.set_event_callback(lambda evt: received_events.append(evt))

    # Frame 1: Initial frame
    base_frame = _generate_healthy_frame(seed=99)
    pipeline._process_frame(detector, base_frame, frame_id=1)
    assert pipeline._health_state == "HEALTHY"

    # Feed the EXACT same frame repeatedly (frame_delta == 0.0 < 0.3)
    for f in range(2, 6):
        pipeline._process_frame(detector, base_frame.copy(), frame_id=f)

    # After >= 3 consecutive identical frames, status transitions to DEGRADED / FROZEN
    health = pipeline.get_health()
    assert health["status"] == "DEGRADED"
    assert health["substate"] == "FROZEN"
    assert health["frame_delta"] < 0.3
    assert any("frozen" in w.lower() for w in health["warnings"])

    # Verify frozen event was dispatched
    frozen_events = [e for e in received_events if e.get("substate") == "FROZEN"]
    assert len(frozen_events) >= 1
    assert frozen_events[0]["camera_id"] == 303


def test_blur_condition_detected():
    """Requirement 4: Severe blur / focus degradation is detected via Laplacian variance."""
    pipeline = CameraPipeline(camera_id=304, stream_url="demo://test304", camera_name="Tower 4 Delta")
    detector = DummyDetector([])
    received_events = []
    pipeline.set_event_callback(lambda evt: received_events.append(evt))

    # Generate a heavily blurred frame (Laplacian variance < 20.0)
    raw_frame = _generate_healthy_frame(seed=12)
    blurry_frame = cv2.GaussianBlur(raw_frame, (51, 51), 25.0)

    pipeline._process_frame(detector, blurry_frame, frame_id=1)

    health = pipeline.get_health()
    assert health["status"] == "DEGRADED"
    assert health["substate"] == "BLURRY"
    assert health["blur_score"] < 20.0
    assert any("blurry" in w.lower() for w in health["warnings"])

    # Verify blur event was dispatched
    blur_events = [e for e in received_events if e.get("substate") == "BLURRY"]
    assert len(blur_events) >= 1
    assert blur_events[0]["camera_id"] == 304


def test_brightness_degradation_detected():
    """Requirement 5: Brightness degradation (blackout/overexposure) is detected."""
    # Subtest A: Black screen / extreme darkness (< 5.0)
    pipeline_black = CameraPipeline(camera_id=305, stream_url="demo://test305", camera_name="Tower 5 Echo")
    detector = DummyDetector([])
    black_frame = np.zeros((480, 640, 3), dtype=np.uint8)

    pipeline_black._process_frame(detector, black_frame, frame_id=1)
    health_black = pipeline_black.get_health()
    assert health_black["status"] in ("DEGRADED", "OFFLINE")
    assert health_black["substate"] == "BRIGHTNESS_DEGRADED"
    assert health_black["brightness"] < 5.0
    assert any("black" in w.lower() or "dark" in w.lower() for w in health_black["warnings"])

    # Subtest B: Overexposed / whiteout screen (> 240.0)
    pipeline_white = CameraPipeline(camera_id=306, stream_url="demo://test306", camera_name="Tower 6 Foxtrot")
    white_frame = np.full((480, 640, 3), 255, dtype=np.uint8)

    pipeline_white._process_frame(detector, white_frame, frame_id=1)
    health_white = pipeline_white.get_health()
    assert health_white["status"] in ("DEGRADED", "OFFLINE")
    assert health_white["substate"] == "BRIGHTNESS_DEGRADED"
    assert health_white["brightness"] > 240.0
    assert any("overexposed" in w.lower() or "bright" in w.lower() for w in health_white["warnings"])


def test_tamper_detection_behavior_and_gap_documentation():
    """Requirement 6: Test supported tamper signals (sudden blackout) and verify angle tamper gap."""
    pipeline = CameraPipeline(camera_id=307, stream_url="demo://test307", camera_name="Tower 7 Golf")
    detector = DummyDetector([])

    # 1. Normal scene
    normal_frame = _generate_healthy_frame(seed=88)
    pipeline._process_frame(detector, normal_frame, frame_id=1)
    assert pipeline._health_state == "HEALTHY"

    # 2. Sudden camera lens cover / total occlusion (brightness collapse to 0)
    covered_frame = np.zeros((480, 640, 3), dtype=np.uint8)
    pipeline._process_frame(detector, covered_frame, frame_id=2)
    health = pipeline.get_health()

    # Extreme brightness collapse is detected and flagged
    assert health["substate"] == "BRIGHTNESS_DEGRADED"
    assert any("black screen" in w.lower() for w in health["warnings"])

    # ARCHITECTURAL GAP DOCUMENTATION:
    # Camera viewpoint/angle tampering (PTZ bump or manual redirection) requires
    # a static reference frame and keypoint/homography estimation (e.g. SIFT/ORB).
    # The codebase currently lacks reference keypoint comparison, so angle tamper
    # cannot be claimed without inventing a fake rule.


def test_health_state_transitions_debounced_no_spam_alert():
    """Requirement 7: Health state transitions are debounced and do not alert every single frame."""
    pipeline = CameraPipeline(camera_id=308, stream_url="demo://test308", camera_name="Tower 8 Hotel")
    detector = DummyDetector([])
    received_events = []
    pipeline.set_event_callback(lambda evt: received_events.append(evt))

    raw_frame = _generate_healthy_frame(seed=55)
    blurry_frame = cv2.GaussianBlur(raw_frame, (51, 51), 25.0)

    # Frame 1: Transitions HEALTHY -> BLURRY (should dispatch 1 transition event)
    frame1 = cv2.GaussianBlur(_generate_healthy_frame(seed=101), (51, 51), 25.0)
    pipeline._process_frame(detector, frame1, frame_id=1)
    assert len([e for e in received_events if e.get("type") == "camera_health"]) == 1

    # Frames 2 to 10: Continuously blurry with subtle motion, within cooldown window (10.0 seconds)
    for f in range(2, 11):
        blurry_frame = cv2.GaussianBlur(_generate_healthy_frame(seed=101 + f), (51, 51), 25.0)
        pipeline._process_frame(detector, blurry_frame, frame_id=f)

    # Within cooldown period, duplicate alerts are suppressed!
    health_events = [e for e in received_events if e.get("type") == "camera_health"]
    assert len(health_events) == 1, f"Expected 1 debounced event, got {len(health_events)}"


def test_recovery_from_unhealthy_to_healthy():
    """Requirement 8: Recovery from unhealthy (OFFLINE) -> RECOVERING -> HEALTHY works."""
    pipeline = CameraPipeline(camera_id=309, stream_url="demo://test309", camera_name="Tower 9 India")
    detector = DummyDetector([])
    received_events = []
    pipeline.set_event_callback(lambda evt: received_events.append(evt))

    # 1. Drive camera into OFFLINE state via 3 read failures
    for _ in range(3):
        pipeline.evaluate_health(None)
    assert pipeline._health_state == "OFFLINE"

    # 2. Feed recovering frames
    # 1st healthy frame: enters RECOVERING state
    pipeline._process_frame(detector, _generate_healthy_frame(seed=101), frame_id=1)
    assert pipeline._health_state == "RECOVERING"

    # 2nd healthy frame: stays RECOVERING
    pipeline._process_frame(detector, _generate_healthy_frame(seed=102), frame_id=2)
    assert pipeline._health_state == "RECOVERING"

    # 3rd healthy frame: successfully recovers to HEALTHY!
    pipeline._process_frame(detector, _generate_healthy_frame(seed=103), frame_id=3)
    assert pipeline._health_state == "HEALTHY"
    assert pipeline._health_substate == "HEALTHY"

    # Check event sequence in callback
    states_received = [e["status"] for e in received_events if e.get("type") == "camera_health"]
    assert "OFFLINE" in states_received
    assert "RECOVERING" in states_received
    assert "HEALTHY" in states_received


def test_multi_camera_health_isolation():
    """Requirement 9: Camera A health degradation does NOT affect Camera B."""
    pipeline_a = CameraPipeline(camera_id=310, stream_url="demo://camA", camera_name="Tower A")
    pipeline_b = CameraPipeline(camera_id=311, stream_url="demo://camB", camera_name="Tower B")
    detector = DummyDetector([])

    # Drive Camera A into OFFLINE
    for _ in range(3):
        pipeline_a.evaluate_health(None)

    # Drive Camera B with pristine frames
    for f in range(1, 4):
        pipeline_b._process_frame(detector, _generate_healthy_frame(seed=f * 33), frame_id=f)

    assert pipeline_a._health_state == "OFFLINE"
    assert pipeline_a.get_health()["health_score"] == 0.0

    assert pipeline_b._health_state == "HEALTHY"
    assert pipeline_b.get_health()["health_score"] >= 80.0


def test_stop_and_restart_clears_health_state():
    """Requirement 10: Stop and restart clears stale health state and diagnostic buffers."""
    pipeline = CameraPipeline(camera_id=312, stream_url="demo://test312", camera_name="Tower 12 Juliet")
    detector = DummyDetector([])

    # Drive into FROZEN state
    frozen_frame = _generate_healthy_frame(seed=77)
    for f in range(1, 6):
        pipeline._process_frame(detector, frozen_frame.copy(), frame_id=f)
    assert pipeline._health_substate == "FROZEN"

    # Stop the pipeline
    pipeline.stop()
    assert pipeline._health_state == "UNKNOWN"
    assert pipeline._health_substate == "UNKNOWN"
    assert len(pipeline._latest_health) == 0
    assert pipeline.health_checker._prev_frame is None

    # Resume with fresh distinct frames
    for f in range(1, 4):
        pipeline._process_frame(detector, _generate_healthy_frame(seed=f * 51), frame_id=f)
    assert pipeline._health_state == "HEALTHY"
    assert pipeline._health_substate == "HEALTHY"


def test_health_events_remain_separate_from_intrusion_threat_events():
    """Requirement 11: Health events do NOT generate false intrusion or threat incidents."""
    pipeline = CameraPipeline(camera_id=313, stream_url="demo://test313", camera_name="Tower 13 Kilo")
    detector = DummyDetector([])  # Zero detections
    received_events = []
    pipeline.set_event_callback(lambda evt: received_events.append(evt))

    # Feed severely degraded frames (blurry + dark)
    dark_blurry = np.full((480, 640, 3), 2, dtype=np.uint8)
    for f in range(1, 6):
        pipeline._process_frame(detector, dark_blurry, frame_id=f)

    assert pipeline._health_state in ("DEGRADED", "OFFLINE")

    # Verify that ONLY camera_health events were emitted
    event_types = {e.get("type") for e in received_events}
    assert "camera_health" in event_types
    assert "incident_created" not in event_types
    assert "zone_event" not in event_types
    assert "zone_intrusion" not in event_types


def test_downstream_websocket_and_database_contract_compatibility():
    """Requirement 12: All health payloads match database schemas and are JSON serializable."""
    pipeline = CameraPipeline(camera_id=314, stream_url="demo://test314", camera_name="Tower 14 Lima")
    detector = DummyDetector([])
    received_events = []
    pipeline.set_event_callback(lambda evt: received_events.append(evt))

    frame = _generate_healthy_frame(seed=999)
    pipeline._process_frame(detector, frame, frame_id=1)

    health_events = [e for e in received_events if e.get("type") == "camera_health"]
    assert len(health_events) >= 1
    payload = health_events[0]

    # 1. Strict JSON serializability (WebSocket contract)
    dumped = json.dumps(payload)
    assert len(dumped) > 0
    parsed = json.loads(dumped)

    # 2. Key contract fields
    assert parsed["camera_id"] == 314
    assert parsed["camera_name"] == "Tower 14 Lima"
    assert parsed["status"] == "HEALTHY"
    assert "health_score" in parsed
    assert "fps_actual" in parsed
    assert "brightness" in parsed
    assert "blur_score" in parsed
    assert "frame_delta" in parsed
    assert "resolution_width" in parsed
    assert "resolution_height" in parsed
    assert "latency_ms" in parsed
    assert "stream_uptime_seconds" in parsed
    assert "reconnect_count" in parsed
    assert "warnings" in parsed
    assert "timestamp" in parsed
