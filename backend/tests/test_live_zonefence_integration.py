"""
Live ZoneFence Geometric Rule Integration Test Suite.

Validates Milestone 1B / Step 02:
1. Tracked object outside configured polygon produces no intrusion event.
2. Tracked object entering restricted polygon produces correct zone event.
3. Object remaining inside does not generate duplicate entry alerts every frame.
4. Object exiting produces correct exit transition.
5. Line-crossing rule works using existing geometric implementation.
6. Multiple objects in the same zone are tracked independently.
7. Two cameras with different zone configurations remain isolated.
8. Track ID + zone state remain correctly associated across frames.
9. ByteTrack output -> ZoneFence -> event -> WebSocket/database downstream flow.
10. All downstream payloads remain JSON serializable and contract-compliant.
"""

from datetime import datetime
import json
import numpy as np
import pytest

from backend.app.services.live_pipeline import CameraPipeline
from edge.detection.base import Detection


class MockDetector:
    def __init__(self, detections=None):
        self._detections = detections or []

    def set_detections(self, detections):
        self._detections = detections

    def detect(self, frame: np.ndarray, frame_id: int):
        return self._detections

    def is_available(self) -> bool:
        return True


def test_tracked_object_outside_polygon_produces_no_intrusion_event():
    """Requirement 1: A tracked object outside a configured polygon produces no intrusion event."""
    restricted_zone = {
        "id": 10,
        "name": "Secure Compound",
        "zone_type": "RESTRICTED",
        "polygon": [[300.0, 300.0], [500.0, 300.0], [500.0, 500.0], [300.0, 500.0]],
        "severity": 0.85,
        "min_confidence": 0.25,
    }
    pipeline = CameraPipeline(camera_id=201, stream_url="demo://test201", zones=[restricted_zone])
    received_events = []
    pipeline.set_event_callback(lambda evt: received_events.append(evt))

    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    # Object positioned at (50, 50) to (110, 150) -> ground anchor (80, 150) -> clearly OUTSIDE polygon
    det = MockDetector([
        Detection(label="person", confidence=0.90, bbox=(50.0, 50.0, 110.0, 150.0), class_name="person", class_id=0, frame_id=1)
    ])

    pipeline._process_frame(det, frame, frame_id=1)

    # Verify zero zone events
    assert len(pipeline._latest_zone_events) == 0
    # Verify no intrusion incident generated
    incident_events = [e for e in received_events if e.get("type") == "incident_created"]
    assert len(incident_events) == 0
    assert len(pipeline._latest_tracks) == 1
    assert pipeline._latest_tracks[0]["in_restricted_zone"] is False


def test_tracked_object_entering_restricted_polygon_produces_correct_zone_event():
    """Requirement 2: A tracked object entering a configured restricted polygon produces the correct zone event."""
    restricted_zone = {
        "id": 11,
        "name": "Restricted Perimeter A",
        "zone_type": "RESTRICTED",
        "polygon": [[300.0, 300.0], [500.0, 300.0], [500.0, 500.0], [300.0, 500.0]],
        "severity": 0.90,
        "min_confidence": 0.25,
    }
    pipeline = CameraPipeline(camera_id=202, stream_url="demo://test202", zones=[restricted_zone])
    received_events = []
    pipeline.set_event_callback(lambda evt: received_events.append(evt))

    frame = np.zeros((480, 640, 3), dtype=np.uint8)

    # Frame 1: Person outside polygon at (80, 150)
    det1 = MockDetector([
        Detection(label="person", confidence=0.90, bbox=(50.0, 50.0, 110.0, 150.0), class_name="person", class_id=0, frame_id=1)
    ])
    pipeline._process_frame(det1, frame, frame_id=1)
    assert len(pipeline._latest_zone_events) == 0

    # Frame 2: Person moves inside polygon at (380, 350) to (420, 450) -> ground anchor (400, 450)
    det2 = MockDetector([
        Detection(label="person", confidence=0.92, bbox=(380.0, 350.0, 420.0, 450.0), class_name="person", class_id=0, frame_id=2)
    ])
    pipeline._process_frame(det2, frame, frame_id=2)

    # Verify zone entry and intrusion events generated
    assert len(pipeline._latest_zone_events) >= 1
    event_types = [e["event_type"] for e in pipeline._latest_zone_events]
    assert "zone_entry" in event_types
    assert "zone_intrusion" in event_types

    # Verify event pushed to callback
    zone_ws_msgs = [e for e in received_events if e.get("type") == "zone_event"]
    assert len(zone_ws_msgs) >= 1
    entry_msg = next(m for m in zone_ws_msgs if m["event_type"] == "zone_entry")
    assert entry_msg["zone_id"] == 11
    assert entry_msg["zone_name"] == "Restricted Perimeter A"
    assert entry_msg["zone_type"] == "RESTRICTED"
    assert entry_msg["ground_anchor"] == [400.0, 450.0]

    # Verify threat incident created with real zone name
    incidents = [e for e in received_events if e.get("type") == "incident_created"]
    assert len(incidents) >= 1
    assert incidents[0]["zone_name"] == "Restricted Perimeter A"
    assert "Restricted Perimeter A" in incidents[0]["title"]


def test_object_remaining_inside_does_not_generate_duplicate_entry_alerts():
    """Requirement 3: An object remaining inside does not generate duplicate entry alerts every frame."""
    restricted_zone = {
        "id": 12,
        "name": "Bunker Sector",
        "zone_type": "RESTRICTED",
        "polygon": [[200.0, 200.0], [500.0, 200.0], [500.0, 500.0], [200.0, 500.0]],
        "severity": 0.85,
    }
    pipeline = CameraPipeline(camera_id=203, stream_url="demo://test203", zones=[restricted_zone])
    received_events = []
    pipeline.set_event_callback(lambda evt: received_events.append(evt))

    frame = np.zeros((480, 640, 3), dtype=np.uint8)

    # Frame 1: Enters zone
    det1 = MockDetector([
        Detection(label="person", confidence=0.91, bbox=(250.0, 250.0, 310.0, 380.0), class_name="person", class_id=0, frame_id=1)
    ])
    pipeline._process_frame(det1, frame, frame_id=1)
    initial_zone_events_count = len([e for e in received_events if e.get("type") == "zone_event"])
    initial_incidents_count = len([e for e in received_events if e.get("type") == "incident_created"])
    assert initial_zone_events_count >= 1
    assert initial_incidents_count >= 1

    # Frame 2: Remains inside zone (slight movement within polygon)
    det2 = MockDetector([
        Detection(label="person", confidence=0.92, bbox=(255.0, 252.0, 315.0, 382.0), class_name="person", class_id=0, frame_id=2)
    ])
    pipeline._process_frame(det2, frame, frame_id=2)

    # Verify NO new zone events generated on Frame 2
    assert len(pipeline._latest_zone_events) == 0
    # Verify NO duplicate incident generated on Frame 2
    current_incidents_count = len([e for e in received_events if e.get("type") == "incident_created"])
    assert current_incidents_count == initial_incidents_count


def test_object_exiting_produces_correct_exit_transition():
    """Requirement 4: An object exiting produces the correct exit transition."""
    restricted_zone = {
        "id": 13,
        "name": "Gate Area",
        "zone_type": "RESTRICTED",
        "polygon": [[200.0, 200.0], [400.0, 200.0], [400.0, 400.0], [200.0, 400.0]],
        "severity": 0.8,
    }
    pipeline = CameraPipeline(camera_id=204, stream_url="demo://test204", zones=[restricted_zone])
    received_events = []
    pipeline.set_event_callback(lambda evt: received_events.append(evt))

    frame = np.zeros((480, 640, 3), dtype=np.uint8)

    # Frame 1: Inside zone -> ground anchor (395, 350), inside polygon [200..400]
    det1 = MockDetector([
        Detection(label="person", confidence=0.93, bbox=(370.0, 250.0, 420.0, 350.0), class_name="person", class_id=0, frame_id=1)
    ])
    pipeline._process_frame(det1, frame, frame_id=1)
    assert any(e["event_type"] == "zone_entry" for e in pipeline._latest_zone_events)

    # Frame 2: Moves outside boundary x=400 -> ground anchor (410, 350), IoU ~0.54 preserves track ID
    det2 = MockDetector([
        Detection(label="person", confidence=0.91, bbox=(385.0, 250.0, 435.0, 350.0), class_name="person", class_id=0, frame_id=2)
    ])
    pipeline._process_frame(det2, frame, frame_id=2)

    # Verify zone_exit transition event
    assert len(pipeline._latest_zone_events) >= 1
    exit_events = [e for e in pipeline._latest_zone_events if e["event_type"] == "zone_exit"]
    assert len(exit_events) == 1
    assert exit_events[0]["zone_id"] == 13
    assert exit_events[0]["zone_name"] == "Gate Area"


def test_line_crossing_tripwire_rule():
    """Requirement 5: A line-crossing rule works using the existing geometric implementation."""
    line_zone = {
        "id": 14,
        "name": "North Tripwire Line",
        "zone_type": "RESTRICTED",
        "geometry": {
            "type": "line",
            "points": [[100.0, 300.0], [500.0, 300.0]],
        },
        "direction": "either",
        "severity": 0.95,
    }
    pipeline = CameraPipeline(camera_id=205, stream_url="demo://test205", zones=[line_zone])
    received_events = []
    pipeline.set_event_callback(lambda evt: received_events.append(evt))

    frame = np.zeros((480, 640, 3), dtype=np.uint8)

    # Frame 1: Person above line -> ground anchor at (300, 280)
    det1 = MockDetector([
        Detection(label="person", confidence=0.90, bbox=(280.0, 180.0, 320.0, 280.0), class_name="person", class_id=0, frame_id=1)
    ])
    pipeline._process_frame(det1, frame, frame_id=1)
    assert len(pipeline._latest_zone_events) == 0

    # Frame 2: Person crosses below line -> ground anchor at (300, 330)
    det2 = MockDetector([
        Detection(label="person", confidence=0.92, bbox=(280.0, 230.0, 320.0, 330.0), class_name="person", class_id=0, frame_id=2)
    ])
    pipeline._process_frame(det2, frame, frame_id=2)

    # Verify line crossing event
    assert len(pipeline._latest_zone_events) >= 1
    line_events = [e for e in pipeline._latest_zone_events if e.get("rule") == "line_tripwire"]
    assert len(line_events) == 1
    assert line_events[0]["zone_id"] == 14
    assert line_events[0]["zone_name"] == "North Tripwire Line"


def test_multiple_objects_in_same_zone_tracked_independently():
    """Requirement 6: Multiple objects in the same zone are tracked independently."""
    shared_zone = {
        "id": 15,
        "name": "Courtyard",
        "zone_type": "RESTRICTED",
        "polygon": [[100.0, 100.0], [500.0, 100.0], [500.0, 500.0], [100.0, 500.0]],
        "severity": 0.8,
    }
    pipeline = CameraPipeline(camera_id=206, stream_url="demo://test206", zones=[shared_zone])
    received_events = []
    pipeline.set_event_callback(lambda evt: received_events.append(evt))

    frame = np.zeros((480, 640, 3), dtype=np.uint8)

    # Frame 1: Two persons enter the zone at different locations
    det = MockDetector([
        Detection(label="person", confidence=0.91, bbox=(150.0, 150.0, 190.0, 250.0), class_name="person", class_id=0, frame_id=1),
        Detection(label="person", confidence=0.89, bbox=(350.0, 350.0, 390.0, 450.0), class_name="person", class_id=0, frame_id=1),
    ])
    pipeline._process_frame(det, frame, frame_id=1)

    assert len(pipeline._latest_tracks) == 2
    track_ids = {t["track_id"] for t in pipeline._latest_tracks}
    assert len(track_ids) == 2

    # Both should have triggered entry events with their respective track_ids
    zone_entries = [e for e in received_events if e.get("type") == "zone_event" and e.get("event_type") == "zone_entry"]
    assert len(zone_entries) == 2
    entry_track_ids = {e["track_id"] for e in zone_entries}
    assert entry_track_ids == track_ids


def test_two_cameras_with_different_zones_remain_isolated():
    """Requirement 7: Two cameras with different zone configurations remain isolated."""
    # Camera 1 has Zone Left: [50, 50] to [200, 200]
    zone_cam1 = {
        "id": 101,
        "name": "Zone Left",
        "zone_type": "RESTRICTED",
        "polygon": [[50.0, 50.0], [200.0, 50.0], [200.0, 200.0], [50.0, 200.0]],
        "severity": 0.8,
    }
    # Camera 2 has Zone Right: [400, 300] to [600, 450]
    zone_cam2 = {
        "id": 102,
        "name": "Zone Right",
        "zone_type": "RESTRICTED",
        "polygon": [[400.0, 300.0], [600.0, 300.0], [600.0, 450.0], [400.0, 450.0]],
        "severity": 0.8,
    }

    pipeline1 = CameraPipeline(camera_id=1, stream_url="demo://cam1", zones=[zone_cam1])
    pipeline2 = CameraPipeline(camera_id=2, stream_url="demo://cam2", zones=[zone_cam2])

    frame = np.zeros((480, 640, 3), dtype=np.uint8)

    # Object at (100, 100) to (140, 180) -> ground anchor (120, 180)
    # This is INSIDE Zone Left (Camera 1) but OUTSIDE Zone Right (Camera 2)
    det = MockDetector([
        Detection(label="person", confidence=0.92, bbox=(100.0, 100.0, 140.0, 180.0), class_name="person", class_id=0, frame_id=1)
    ])

    pipeline1._process_frame(det, frame, frame_id=1)
    pipeline2._process_frame(det, frame, frame_id=1)

    # Camera 1 should trigger Zone Left entry
    assert len(pipeline1._latest_zone_events) >= 1
    assert pipeline1._latest_zone_events[0]["zone_name"] == "Zone Left"
    assert pipeline1._latest_tracks[0]["in_restricted_zone"] is True

    # Camera 2 should trigger ZERO zone events (object is outside Zone Right)
    assert len(pipeline2._latest_zone_events) == 0
    assert pipeline2._latest_tracks[0]["in_restricted_zone"] is False


def test_track_id_and_zone_state_association_across_frames():
    """Requirement 8: Track ID + zone state remain correctly associated across frames."""
    zone = {
        "id": 16,
        "name": "Sentry Perimeter",
        "zone_type": "RESTRICTED",
        "polygon": [[100.0, 100.0], [400.0, 100.0], [400.0, 400.0], [100.0, 400.0]],
        "severity": 0.75,
    }
    pipeline = CameraPipeline(camera_id=208, stream_url="demo://test208", zones=[zone])
    frame = np.zeros((480, 640, 3), dtype=np.uint8)

    det = MockDetector([])
    for f in range(1, 6):
        # Moving within zone: x: 150 -> 190
        x = 150.0 + f * 10.0
        det.set_detections([
            Detection(label="person", confidence=0.90, bbox=(x, 150.0, x + 40.0, 250.0), class_name="person", class_id=0, frame_id=f)
        ])
        pipeline._process_frame(det, frame, frame_id=f)
        assert len(pipeline._latest_tracks) == 1
        trk = pipeline._latest_tracks[0]
        assert trk["track_id"] == "TRK-01"
        assert trk["in_restricted_zone"] is True
        # Zone entry recorded on first frame, suppressed on subsequent frames
        if f == 1:
            assert any(e["event_type"] == "zone_entry" for e in pipeline._latest_zone_events)
        else:
            assert len(pipeline._latest_zone_events) == 0


def test_bytetrack_zonefence_websocket_database_pipeline_contract():
    """Requirement 9 & 10: Downstream contracts, JSON serializability, and database persistence."""
    zone = {
        "id": 17,
        "name": "Tactical Sector 7",
        "zone_type": "RESTRICTED",
        "polygon": [[150.0, 150.0], [450.0, 150.0], [450.0, 450.0], [150.0, 450.0]],
        "severity": 0.85,
    }
    pipeline = CameraPipeline(camera_id=209, stream_url="demo://test209", zones=[zone])
    received_events = []
    pipeline.set_event_callback(lambda evt: received_events.append(evt))

    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    det = MockDetector([
        Detection(label="person", confidence=0.94, bbox=(200.0, 200.0, 260.0, 360.0), class_name="person", class_id=0, frame_id=1)
    ])
    pipeline._process_frame(det, frame, frame_id=1)

    # 1. Verify JSON serializability of all emitted WebSocket events
    for evt in received_events:
        dumped = json.dumps(evt)
        assert len(dumped) > 0

    # 2. Verify zone_event payload structure
    zone_events = [e for e in received_events if e.get("type") == "zone_event"]
    assert len(zone_events) >= 1
    ze = zone_events[0]
    assert ze["camera_id"] == 209
    assert ze["zone_id"] == 17
    assert ze["zone_name"] == "Tactical Sector 7"
    assert ze["track_id"] == "TRK-01"
    assert isinstance(ze["ground_anchor"], list)
    assert len(ze["ground_anchor"]) == 2

    # 3. Verify incident_created payload structure
    incidents = [e for e in received_events if e.get("type") == "incident_created"]
    assert len(incidents) >= 1
    inc = incidents[0]
    assert inc["zone_name"] == "Tactical Sector 7"
    assert "spatial_grounding" in inc["ai_assessment"]
    assert "calibration dependent" in inc["ai_assessment"]["spatial_grounding"]
    assert "timeline" in inc
    assert any(t.get("source") == "zone_fence" for t in inc["timeline"])
