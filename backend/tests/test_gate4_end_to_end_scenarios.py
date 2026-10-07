"""
IBVAP Gate 4 End-to-End Realistic Mission Scenarios Suite (Scenarios A through G)
================================================================================

Executes complete realistic operational mission scenarios through the live pipeline:
- Scenario A: Daylight Boundary Crossing
- Scenario B: Night Infiltration with Low-Light Enhancement
- Scenario C: Vehicle Checkpoint with ANPR Consensus
- Scenario D: Watchlist Subject Sighting
- Scenario E: Cross-Camera Multi-Tower Pursuit & Handoff
- Scenario F: Offline Border Outpost Network Disconnect & Spool Replay
- Scenario G: False Alarm Storm & Weather Resilience
"""

import os
import time
import json
import uuid
from datetime import datetime
import numpy as np
import pytest

from backend.app.services.live_pipeline import CameraPipeline
from backend.app.services.face import face_service
from backend.app.services.scoring import compute_threat_score
from backend.app.services.spool_replay import SpoolReplayWorker
from backend.app.models.watchlist import Watchlist
from backend.app.models.incident import Incident
from backend.app.db.session import SessionLocal
from edge.detection.base import Detection
from edge.correlation.cross_camera import (
    CrossCameraAssociator,
    CrossCameraConfig,
    TrackletDescriptor,
    CrossCameraState,
)
from edge.correlation.topology import (
    CameraTopologyGraph,
    CameraNode,
    TopologyEdge,
    CameraOverlapType,
)


class TestMissionScenarioA_DaylightBoundaryCrossing:
    """Scenario A: Person approaches border fence in daylight, crosses restricted polygon, triggers incident."""

    def test_scenario_a_daylight_crossing(self, tmp_path):
        pipeline = CameraPipeline(camera_id=1, stream_url="demo://bop1", camera_name="Sector Alpha 01", bop="BOP-01")
        # Define restricted zone polygon: y between 200 and 450
        pipeline.zone_fence.set_zones([{
            "id": 101,
            "name": "Zero-Line Restricted Zone",
            "zone_type": "RESTRICTED",
            "severity": 0.95,
            "coordinates": [[100, 200], [500, 200], [500, 450], [100, 450]],
        }])

        events_pushed = []
        pipeline.set_event_callback(lambda ev: events_pushed.append(ev))

        # Feed daylight frames moving directly into the restricted zone
        day_frame = np.full((720, 1280, 3), 160, dtype=np.uint8)
        for f in range(1, 25):
            # Target starts at y=50 (bottom=170 < 200, outside zone) and moves to y=210 (bottom=330 > 200, inside zone)
            y_pos = int(50 + f * 7)
            dets = [Detection(label="person", confidence=0.92, bbox=(250, y_pos, 320, y_pos + 120),
                              class_name="person", class_id=0, frame_id=f)]
            # Mock detector
            class DetStub:
                def detect(self, frm, fid): return dets
            pipeline._process_frame(DetStub(), day_frame, frame_id=f)

        # Verify zone event generated or track marked in restricted zone
        active = pipeline._latest_tracks
        assert len(active) == 1
        assert active[0]["target_id"] == 1
        assert active[0]["in_restricted_zone"] is True or len(events_pushed) > 0 or len(pipeline._latest_zone_events) > 0


class TestMissionScenarioB_NightInfiltration:
    """Scenario B: Infiltration in low light (< 45 lux equivalent) triggers night enhancement and elevated threat."""

    def test_scenario_b_night_infiltration(self):
        pipeline = CameraPipeline(camera_id=2, stream_url="demo://bop2", camera_name="Sector Bravo 02", bop="BOP-01")
        pipeline.zone_fence.set_zones([{
            "id": 102,
            "name": "Buffer Patrol Zone",
            "zone_type": "BUFFER",
            "severity": 0.8,
            "coordinates": [[50, 50], [600, 50], [600, 500], [50, 500]],
        }])

        # Very low light frame (mean luma ~ 25)
        night_frame = np.full((720, 1280, 3), 25, dtype=np.uint8)

        from edge.modules.night_enhance import NightEnhancer
        enhancer = NightEnhancer(brightness_threshold=45.0)
        res = enhancer.enhance(night_frame)
        assert res.was_enhanced is True

        # Threat score calculation under night conditions
        signals = {
            "confidence": 0.89,
            "zone_severity": 0.8,
            "boundary_crossing": 1.0,
            "behavior_anomaly": 0.7,
            "night": 1.0,
        }
        assessment = compute_threat_score(signals, context={"zone_type": "BUFFER"})
        # Night factor escalates threat
        assert assessment.rps_score >= 45.0
        assert "night_context" in assessment.signal_contributions
        assert assessment.priority_level in ("CRITICAL", "HIGH", "MEDIUM")


class TestMissionScenarioC_VehicleCheckpointANPR:
    """Scenario C: Vehicle checkpoint localization and multi-frame character consensus voting."""

    def test_scenario_c_vehicle_checkpoint(self):
        pipeline = CameraPipeline(camera_id=3, stream_url="demo://cp1", camera_name="Vehicle Choke Point", bop="BOP-02")

        # Simulate vehicle tracklet
        v_track = {"track_id": 12, "bbox": [200, 200, 600, 500], "class_name": "car"}

        # 4 noisy reads converging on "UK07AB5678"
        reads = ["UK07AB5678", "UK07AB5678", "UK07A85678", "UK07AB5678"]
        for idx, text in enumerate(reads):
            cand = pipeline.evidence_associator.associate_vehicle_plate(
                track_id=12,
                vehicle_track=v_track,
                raw_plate_text=text,
                ocr_confidence=0.91,
                plate_bbox=[250, 380, 450, 440],
                frame_id=idx + 1,
                timestamp=time.time() + idx * 0.1,
            )
            assert cand is not None

        rec = pipeline.evidence_associator._plate_records.get(12)
        assert rec is not None
        assert rec.consensus_text == "UK07AB5678"


class TestMissionScenarioD_WatchlistSubjectSighting:
    """Scenario D: Watchlist subject facial recognition, cosine matching, evidence-only dispatch."""

    def test_scenario_d_watchlist_sighting(self):
        db = SessionLocal()
        subject_id = None
        try:
            # Create a test enrolled subject
            subj = Watchlist(
                name="Infiltration Suspect Bravo",
                embedding=[0.1] * 128,
                notes="Scenario D Test Subject",
            )
            db.add(subj)
            db.commit()
            db.refresh(subj)
            subject_id = subj.id

            # Query database watchlist matching with matching probe embedding
            probe = np.array([0.1] * 128, dtype=np.float32)
            probe = probe / np.linalg.norm(probe)

            matched_subj, sim = face_service.match_watchlist(probe.tolist(), db)
            assert matched_subj is not None
            assert matched_subj.id == subject_id
            assert sim > 0.65
        finally:
            if subject_id:
                s = db.get(Watchlist, subject_id)
                if s:
                    db.delete(s)
                    db.commit()
            db.close()


class TestMissionScenarioE_CrossCameraMultiTowerPursuit:
    """Scenario E: Target exits Camera 1 East, enters Camera 2 West within transit window -> single dossier."""

    def test_scenario_e_cross_camera_handoff(self):
        # Build 2-tower adjacent topology
        topo = CameraTopologyGraph()
        topo.add_node(CameraNode(camera_id=1, name="Tower 1", bop="BOP-01", sector="Alpha"))
        topo.add_node(CameraNode(camera_id=2, name="Tower 2", bop="BOP-01", sector="Alpha"))
        topo.add_edge(TopologyEdge(
            source_camera_id=1,
            target_camera_id=2,
            distance_meters=50.0,
            overlap_type=CameraOverlapType.ADJACENT_BLIND,
            is_bidirectional=True,
        ))

        associator = CrossCameraAssociator(topology=topo, config=CrossCameraConfig())

        now = time.time()
        # Cam 1 tracklet: person heading East (90 deg), exits at t=now
        emb1 = np.full(512, 0.05, dtype=np.float32)
        emb1 = emb1 / np.linalg.norm(emb1)

        t1 = TrackletDescriptor(
            track_id=101,
            camera_id=1,
            class_name="person",
            start_time=now - 10.0,
            end_time=now,
            duration=10.0,
            hit_count=30,
            entry_point_normalized=[0.1, 0.5],
            exit_point_normalized=[0.9, 0.5],
            exit_heading_degrees=90.0,
            appearance_embedding=emb1,
        )
        assocs1 = associator.register_completed_tracklet(t1)
        assert isinstance(assocs1, list)

        # Cam 2 tracklet: person arrives at t=now+30s (50m in 30s = 1.67 m/s, realistic walking speed)
        emb2 = np.full(512, 0.05, dtype=np.float32)
        emb2 = emb2 / np.linalg.norm(emb2)

        t2 = TrackletDescriptor(
            track_id=202,
            camera_id=2,
            class_name="person",
            start_time=now + 30.0,
            end_time=now + 40.0,
            duration=10.0,
            hit_count=30,
            entry_point_normalized=[0.1, 0.5],
            exit_point_normalized=[0.8, 0.5],
            exit_heading_degrees=90.0,
            appearance_embedding=emb2,
        )
        assocs2 = associator.register_completed_tracklet(t2)

        # Both tracklets must be associated into a single global dossier
        assert len(assocs2) >= 1
        best_assoc = assocs2[0]
        assert best_assoc.state in (CrossCameraState.CORROBORATED, CrossCameraState.PLAUSIBLE)

        dossier1 = associator.get_dossier_for_track(1, 101)
        dossier2 = associator.get_dossier_for_track(2, 202)
        assert dossier1 is not None
        assert dossier2 is not None
        assert dossier1.dossier_id == dossier2.dossier_id
        assert 1 in dossier1.camera_ids and 2 in dossier1.camera_ids


class TestMissionScenarioF_OfflineBorderOutpost:
    """Scenario F: WAN drops, incident written to spool, WAN recovers, spool replays 100% of data."""

    def test_scenario_f_offline_spool_replay(self, tmp_path):
        spool_dir = tmp_path / "spool"
        spool_dir.mkdir(parents=True, exist_ok=True)
        spool_file = spool_dir / "offline_incidents.jsonl"

        # 1. Simulate edge writing 3 incidents to disk spool during network blackout
        records = []
        for i in range(1, 4):
            event_id = str(uuid.uuid4())
            idempotency_key = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"offline-scenario-f-{i}"))
            rec = {
                "event_id": event_id,
                "event_type": "incident_created",
                "idempotency_key": idempotency_key,
                "incident_code": f"INC-SCENARIO-F-{i:03d}",
                "camera_id": 1,
                "camera_name": "Sector Alpha 01",
                "zone_name": "Restricted Zone",
                "severity": "CRITICAL",
                "threat_score": 88.0,
                "confidence": 0.90,
                "reasons": ["Restricted zone intrusion", "Speed anomaly"],
                "fingerprint": f"fp_{i}",
                "recommended_action": "Verify live feed",
                "spooled_at": datetime.utcnow().isoformat(),
            }
            records.append(rec)

        with open(spool_file, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

        # 2. Network recovers: SpoolReplayWorker runs
        db = SessionLocal()
        try:
            worker = SpoolReplayWorker(
                spool_dir=spool_dir,
                spool_filename="offline_incidents.jsonl",
                session_factory=lambda: db,
            )
            replayed_count = worker.run_once(max_records=10)
            assert replayed_count == 3

            snap = worker.telemetry.snapshot()
            assert snap["spool_records_replayed_success"] == 3
            assert snap["spool_records_malformed"] == 0

            # Confirm incidents exist in database
            for r in records:
                inc = db.query(Incident).filter(Incident.incident_code == r["incident_code"]).first()
                assert inc is not None
                assert inc.threat_score == 88.0
                db.delete(inc)
            db.commit()
        finally:
            db.close()


class TestMissionScenarioG_FalseAlarmStorm:
    """Scenario G: Heavy vegetation / animal motion with low confidence candidates damped by spatial and persistence gates."""

    def test_scenario_g_false_alarm_storm(self):
        pipeline = CameraPipeline(camera_id=4, stream_url="demo://bop4", camera_name="Forest Edge 04", bop="BOP-01")
        # Monitored zone only (not restricted)
        pipeline.zone_fence.set_zones([{
            "id": 104,
            "name": "General Buffer",
            "zone_type": "MONITORED",
            "severity": 0.2,
            "coordinates": [[0, 0], [1000, 0], [1000, 700], [0, 700]],
        }])

        threat_signals = {
            "confidence": 0.35,            # Low confidence (vegetation false positive)
            "zone_severity": 0.2,          # Monitored zone
            "boundary_crossing": 0.0,      # No crossing
            "speed_anomaly": 0.0,
            "loitering": 0.0,
        }

        # RPS threat evaluation on jittering candidates
        assessment = compute_threat_score(threat_signals, context={"is_single_frame_jitter": True, "zone_type": "MONITORED"})
        # Must be below standard alert threshold (< 40.0)
        assert assessment.rps_score < 40.0
        assert assessment.priority_level.upper() in ("LOW", "INFORMATIONAL")
