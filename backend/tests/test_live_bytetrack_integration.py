"""
Integration and validation tests for Step 01: Live ByteTrack Integration.
Validates:
1. Live frame processing through ByteTracker (Kalman + IoU).
2. Proof that _simple_track is completely bypassed in the live path.
3. Persistent tracklet IDs across moving frames.
4. Occlusion and lost-track recovery via Kalman prediction.
5. Multi-camera tracker isolation (independent ByteTracker instances per stream).
6. CameraPipeline reset/stop lifecycle behavior.
7. Ground-footprint anchor calculation.
8. Downstream event, database, and WebSocket contract compatibility.
"""

import time
import numpy as np
import pytest
from unittest.mock import MagicMock

from backend.app.services.live_pipeline import CameraPipeline
from edge.detection.base import Detection
from edge.tracking.bytetrack import ByteTracker


class MockDetector:
    """Mock detector returning configurable detections."""
    def __init__(self, detections=None):
        self.detections = detections or []

    def set_detections(self, detections):
        self.detections = detections

    def detect(self, frame, frame_id):
        return self.detections


def test_live_frames_enter_bytetrack_and_reach_downstream():
    """Verify that live frames enter ByteTrack and active tracks reach downstream pipeline."""
    pipeline = CameraPipeline(camera_id=101, stream_url="demo://test101", camera_name="Tower 1", bop="BOP-A")
    assert isinstance(pipeline.tracker, ByteTracker)

    detector = MockDetector([
        Detection(label="person", confidence=0.91, bbox=(120, 100, 200, 300), class_name="person", class_id=0, frame_id=1)
    ])
    frame = np.zeros((480, 640, 3), dtype=np.uint8)

    pipeline._process_frame(detector, frame, frame_id=1)

    tracks = pipeline._latest_tracks
    assert len(tracks) == 1
    t = tracks[0]

    # Verify track identity format and properties
    assert t["track_id"] == "TRK-01"
    assert t["target_id"] == 1
    assert t["class_name"] == "person"
    assert t["confidence"] >= 0.90
    assert t["frame_id"] == 1
    assert isinstance(t["dwell_time"], float)
    assert t["state"] in ("tentative", "confirmed")

    # Verify ground footprint anchor: pegged to [center_x, bottom_y]
    bbox = t["bbox"]
    expected_cx = float((bbox[0] + bbox[2]) / 2.0)
    expected_footprint_y = float(bbox[3])
    assert abs(t["ground_anchor"][0] - expected_cx) < 0.2
    assert abs(t["ground_anchor"][1] - expected_footprint_y) < 0.2
    assert t["footprint"] == t["ground_anchor"]

    # Verify stats updated
    stats = pipeline.get_stats()
    assert stats["tracks_active"] == 1


def test_simple_track_bypassed_and_not_called():
    """Prove that _simple_track is never called during live frame execution."""
    pipeline = CameraPipeline(camera_id=102, stream_url="demo://test102", camera_name="Tower 2", bop="BOP-A")
    
    # Spy on _simple_track
    simple_track_mock = MagicMock(side_effect=RuntimeError("FAIL: _simple_track was called in live path!"))
    pipeline._simple_track = simple_track_mock

    detector = MockDetector([
        Detection(label="person", confidence=0.88, bbox=(100, 100, 180, 260), class_name="person", class_id=0, frame_id=1)
    ])
    frame = np.zeros((480, 640, 3), dtype=np.uint8)

    # Must execute smoothly without triggering RuntimeError
    pipeline._process_frame(detector, frame, frame_id=1)

    assert simple_track_mock.call_count == 0
    assert len(pipeline._latest_tracks) == 1
    assert pipeline._latest_tracks[0]["track_id"] == "TRK-01"


def test_persistent_track_id_across_moving_frames():
    """Verify track ID persistence and zero ID switches across moving trajectory."""
    pipeline = CameraPipeline(camera_id=103, stream_url="demo://test103", camera_name="Tower 3", bop="BOP-A")
    frame = np.zeros((480, 640, 3), dtype=np.uint8)

    track_ids = []
    x = 100
    for frame_id in range(1, 15):
        x += 8  # Smooth linear motion
        detector = MockDetector([
            Detection(label="car", confidence=0.95, bbox=(x, 200, x + 100, 280), class_name="car", class_id=2, frame_id=frame_id)
        ])
        pipeline._process_frame(detector, frame, frame_id=frame_id)
        assert len(pipeline._latest_tracks) == 1
        track_ids.append(pipeline._latest_tracks[0]["track_id"])

    # All 14 frames must have the exact same track ID
    unique_ids = set(track_ids)
    assert len(unique_ids) == 1, f"Unexpected ID switches detected: {unique_ids}"
    assert track_ids[0] == "TRK-01"
    assert pipeline._latest_tracks[0]["hits"] == 14


def test_occlusion_and_lost_track_recovery():
    """Verify that ByteTrack recovers a track after momentary occlusion / missing detection frames."""
    pipeline = CameraPipeline(camera_id=104, stream_url="demo://test104", camera_name="Tower 4", bop="BOP-B")
    frame = np.zeros((480, 640, 3), dtype=np.uint8)

    # Step 1: Detect object for 4 frames (x = 100, 115, 130, 145)
    for f in range(1, 5):
        x = 100 + (f - 1) * 15
        det = MockDetector([
            Detection(label="person", confidence=0.92, bbox=(x, 150, x + 60, 270), class_name="person", class_id=0, frame_id=f)
        ])
        pipeline._process_frame(det, frame, frame_id=f)

    initial_track_id = pipeline._latest_tracks[0]["track_id"]
    assert initial_track_id == "TRK-01"

    # Step 2: Object occluded for 3 frames (zero detections)
    empty_det = MockDetector([])
    for f in range(5, 8):
        pipeline._process_frame(empty_det, frame, frame_id=f)

    # Step 3: Object reappears at x = 205 (consistent with constant velocity: 145 + 4*15 = 205)
    reappear_det = MockDetector([
        Detection(label="person", confidence=0.89, bbox=(205, 150, 265, 270), class_name="person", class_id=0, frame_id=8)
    ])
    pipeline._process_frame(reappear_det, frame, frame_id=8)

    assert len(pipeline._latest_tracks) == 1
    recovered_track_id = pipeline._latest_tracks[0]["track_id"]
    # ByteTrack Kalman association must recover the exact same track ID!
    assert recovered_track_id == initial_track_id, f"Track ID changed after occlusion: {initial_track_id} -> {recovered_track_id}"


def test_multi_camera_isolation():
    """Verify that multiple CameraPipeline instances maintain completely isolated tracker state."""
    pipeline_cam1 = CameraPipeline(camera_id=1, stream_url="demo://cam1", camera_name="Cam 1")
    pipeline_cam2 = CameraPipeline(camera_id=2, stream_url="demo://cam2", camera_name="Cam 2")

    # Distinct ByteTracker instances
    assert pipeline_cam1.tracker is not pipeline_cam2.tracker
    assert pipeline_cam1._track_metadata is not pipeline_cam2._track_metadata

    frame = np.zeros((480, 640, 3), dtype=np.uint8)

    # Feed detections ONLY to Cam 1
    det_cam1 = MockDetector([
        Detection(label="person", confidence=0.90, bbox=(100, 100, 160, 220), class_name="person", class_id=0, frame_id=1)
    ])
    pipeline_cam1._process_frame(det_cam1, frame, frame_id=1)

    assert len(pipeline_cam1._latest_tracks) == 1
    assert pipeline_cam1._latest_tracks[0]["track_id"] == "TRK-01"

    # Cam 2 must have 0 tracks
    assert len(pipeline_cam2._latest_tracks) == 0
    assert pipeline_cam2.tracker.get_stats()["total_tracks"] == 0

    # Feed detections to Cam 2
    det_cam2 = MockDetector([
        Detection(label="truck", confidence=0.94, bbox=(300, 200, 450, 350), class_name="truck", class_id=7, frame_id=1)
    ])
    pipeline_cam2._process_frame(det_cam2, frame, frame_id=1)

    assert len(pipeline_cam2._latest_tracks) == 1
    # Cam 2 starts at its own isolated TRK-01
    assert pipeline_cam2._latest_tracks[0]["track_id"] == "TRK-01"
    assert pipeline_cam2._latest_tracks[0]["class_name"] == "truck"

    # Reset Cam 1; Cam 2 state must remain intact
    pipeline_cam1.stop()
    assert len(pipeline_cam1._track_metadata) == 0
    assert pipeline_cam2._latest_tracks[0]["track_id"] == "TRK-01"
    assert len(pipeline_cam2._track_metadata) == 1


def test_pipeline_stop_reset_lifecycle():
    """Verify that CameraPipeline.stop() cleans up and resets tracker state."""
    pipeline = CameraPipeline(camera_id=105, stream_url="demo://test105", camera_name="Tower 5")
    frame = np.zeros((480, 640, 3), dtype=np.uint8)

    det = MockDetector([
        Detection(label="person", confidence=0.91, bbox=(50, 50, 120, 180), class_name="person", class_id=0, frame_id=1)
    ])
    pipeline._process_frame(det, frame, frame_id=1)
    assert len(pipeline._latest_tracks) == 1

    pipeline.stop()
    stats = pipeline.tracker.get_stats()
    assert stats["total_tracks"] == 0
    assert stats["active_tracks"] == 0
    assert len(pipeline._track_metadata) == 0
    assert len(pipeline._tracked_targets) == 0


def test_downstream_event_and_websocket_contract_compatibility():
    """Verify that ByteTrack output meets the exact contract expected by downstream event emitters."""
    pipeline = CameraPipeline(camera_id=106, stream_url="demo://test106", camera_name="Tower 6", bop="BOP-C")
    received_events = []
    pipeline.set_event_callback(lambda evt: received_events.append(evt))

    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    det = MockDetector([
        Detection(label="person", confidence=0.95, bbox=(200, 200, 280, 400), class_name="person", class_id=0, frame_id=1)
    ])

    # Run frame to trigger threat check and incident generation
    pipeline._process_frame(det, frame, frame_id=1)

    # Verify event was dispatched via callback
    assert len(received_events) >= 1
    incident_events = [e for e in received_events if e.get("type") == "incident_created"]
    assert len(incident_events) >= 1
    evt = incident_events[0]
    assert evt["type"] == "incident_created"
    assert evt["camera_id"] == 106
    assert "IBVAP-" in evt["incident_code"]
    assert evt["confidence"] >= 0.90
    assert "timeline" in evt
    assert len(evt["timeline"]) > 0

    # Verify timeline detection payload matches track bbox
    det_entry = [entry for entry in evt["timeline"] if entry["event_type"] == "detection"][0]
    assert "bbox" in det_entry["payload"]
    assert isinstance(det_entry["payload"]["bbox"], list)
    assert len(det_entry["payload"]["bbox"]) == 4
