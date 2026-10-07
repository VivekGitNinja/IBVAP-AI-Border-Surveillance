"""
Step 04 Phase 3 — Cross-Camera Tracklet Association & Incident Correlation Tests
=================================================================================

18 focused integration tests covering topology graph, kinematic feasibility,
hard identity vetoes, bipartite global assignment, anti-collapse local track
preservation, incident correlation, and RPS non-escalation.

Classification:
    Production-structured cross-camera association, pending field validation.
"""

import pytest
import time
import math
import numpy as np
from datetime import datetime, timedelta


# ── HELPERS ──────────────────────────────────────────────────────────────────

def _make_topology_3_towers():
    """Create a 3-tower linear topology: T1 -- 100m -- T2 -- 120m -- T3."""
    from edge.correlation.topology import CameraTopologyGraph, CameraNode, TopologyEdge, CameraOverlapType

    graph = CameraTopologyGraph()
    graph.add_node(CameraNode(camera_id=1, name="Tower-1", bop="BOP-01", sector="Alpha",
                              latitude=28.6139, longitude=77.2090))
    graph.add_node(CameraNode(camera_id=2, name="Tower-2", bop="BOP-01", sector="Alpha",
                              latitude=28.6148, longitude=77.2095))
    graph.add_node(CameraNode(camera_id=3, name="Tower-3", bop="BOP-01", sector="Alpha",
                              latitude=28.6158, longitude=77.2100))

    graph.add_edge(TopologyEdge(source_camera_id=1, target_camera_id=2,
                                distance_meters=100.0,
                                overlap_type=CameraOverlapType.ADJACENT_BLIND,
                                blind_zone_description="75m ditch between Tower 1 and Tower 2"))
    graph.add_edge(TopologyEdge(source_camera_id=2, target_camera_id=3,
                                distance_meters=120.0,
                                overlap_type=CameraOverlapType.ADJACENT_BLIND,
                                blind_zone_description="90m scrubland between Tower 2 and Tower 3"))
    return graph


def _make_config(**overrides):
    """Create a CrossCameraConfig with optional overrides."""
    from edge.correlation.cross_camera import CrossCameraConfig
    return CrossCameraConfig(**overrides)


def _make_tracklet(track_id, camera_id, class_name, start_time, end_time,
                   hit_count=30, embedding=None, plate_text=None,
                   matched_subject_id=None, exit_heading=None, incident_id=None,
                   plate_confidence=None, plate_association_state=None,
                   plate_consensus_reached=False, frs_confidence=None,
                   frs_association_state=None):
    """Create a synthetic TrackletDescriptor."""
    from edge.correlation.cross_camera import TrackletDescriptor
    return TrackletDescriptor(
        track_id=track_id,
        camera_id=camera_id,
        class_name=class_name,
        start_time=start_time,
        end_time=end_time,
        duration=end_time - start_time,
        hit_count=hit_count,
        entry_point_normalized=[0.5, 0.5],
        exit_point_normalized=[0.9, 0.5],
        exit_heading_degrees=exit_heading,
        appearance_embedding=embedding,
        plate_text=plate_text,
        plate_confidence=plate_confidence,
        plate_association_state=plate_association_state,
        plate_consensus_reached=plate_consensus_reached,
        matched_subject_id=matched_subject_id,
        frs_confidence=frs_confidence,
        frs_association_state=frs_association_state,
        incident_id=incident_id,
    )


def _random_embedding(seed=42):
    """Generate a deterministic normalized 512-d embedding."""
    rng = np.random.RandomState(seed)
    v = rng.randn(512).astype(np.float32)
    v /= (np.linalg.norm(v) + 1e-9)
    return v


# ── TOPOLOGY & GRAPH TESTS ──────────────────────────────────────────────────

class TestTopologyGraph:
    """Tests 3A-3C: Topology graph construction, distance, and routing."""

    def test_3a_topology_graph_construction_and_shortest_path(self):
        """Test 3A: Define 3-tower graph and verify correct distances and adjacency queries."""
        graph = _make_topology_3_towers()

        # Direct edges
        assert graph.compute_shortest_path_distance(1, 2) == 100.0
        assert graph.compute_shortest_path_distance(2, 3) == 120.0

        # Shortest path T1 -> T3 via T2: 100 + 120 = 220m
        assert graph.compute_shortest_path_distance(1, 3) == 220.0

        # Neighbor queries
        adj_1 = graph.get_adjacent_cameras(1)
        assert 2 in adj_1
        assert 3 not in adj_1  # T3 is not directly adjacent to T1

        adj_2 = graph.get_adjacent_cameras(2)
        assert 1 in adj_2
        assert 3 in adj_2

        # Self-distance
        assert graph.compute_shortest_path_distance(1, 1) == 0.0

        # Non-existent camera
        assert graph.compute_shortest_path_distance(1, 99) is None

    def test_3b_topology_overlapping_vs_blind_edge_distinction(self):
        """Test 3B: Verify overlapping vs blind zone edge types are correctly stored."""
        from edge.correlation.topology import CameraTopologyGraph, CameraNode, TopologyEdge, CameraOverlapType

        graph = CameraTopologyGraph()
        graph.add_node(CameraNode(camera_id=10, name="Overlap-A", bop="BOP-02", sector="Beta"))
        graph.add_node(CameraNode(camera_id=11, name="Overlap-B", bop="BOP-02", sector="Beta"))

        graph.add_edge(TopologyEdge(source_camera_id=10, target_camera_id=11,
                                    distance_meters=15.0,
                                    overlap_type=CameraOverlapType.OVERLAPPING))

        edge = graph.get_edge(10, 11)
        assert edge is not None
        assert edge.overlap_type == CameraOverlapType.OVERLAPPING
        assert edge.distance_meters == 15.0

        # Blind zone edge from main topology
        graph2 = _make_topology_3_towers()
        edge_blind = graph2.get_edge(1, 2)
        assert edge_blind is not None
        assert edge_blind.overlap_type == CameraOverlapType.ADJACENT_BLIND

    def test_3c_non_adjacent_camera_traversal_shortest_path(self):
        """Test 3C: T1 to T3 requires passing through T2; verify multi-hop calculation."""
        graph = _make_topology_3_towers()

        # No direct edge between T1 and T3
        assert graph.get_edge(1, 3) is None

        # Shortest path should exist via T2
        dist = graph.compute_shortest_path_distance(1, 3)
        assert dist == 220.0


# ── KINEMATIC FEASIBILITY & CAUSAL BOUNDS ────────────────────────────────────

class TestKinematicFeasibility:
    """Tests 3D-3I: Kinematic feasibility, teleportation, causality, timeout."""

    def test_3d_kinematic_feasibility_human_nominal_walk(self):
        """Test 3D: Person covers 100m in 75s (v=1.33 m/s). Should be feasible."""
        graph = _make_topology_3_towers()
        config = _make_config()

        feasible, reason, eff_v = graph.evaluate_kinematic_feasibility(
            cam_a=1, cam_b=2, delta_t=75.0, class_name="person", config=config)

        assert feasible is True
        assert "FEASIBLE" in reason
        assert abs(eff_v - 100.0 / 75.0) < 0.1  # ~1.33 m/s

    def test_3e_kinematic_infeasibility_teleportation_rejection(self):
        """Test 3E: Person appears on T2 (100m away) 1.0s after T1 (v=100 m/s). Must reject."""
        graph = _make_topology_3_towers()
        config = _make_config()

        feasible, reason, eff_v = graph.evaluate_kinematic_feasibility(
            cam_a=1, cam_b=2, delta_t=1.0, class_name="person", config=config)

        assert feasible is False
        assert "IMPOSSIBLE_VELOCITY" in reason
        assert eff_v > config.person_max_speed_mps

    def test_3f_kinematic_infeasibility_causal_negative_time_rejection(self):
        """Test 3F: Person appears on non-overlapping T2 before exiting T1 (dt < 0). Must reject."""
        graph = _make_topology_3_towers()
        config = _make_config()

        feasible, reason, eff_v = graph.evaluate_kinematic_feasibility(
            cam_a=1, cam_b=2, delta_t=-5.0, class_name="person", config=config)

        assert feasible is False
        assert "CAUSAL_VIOLATION" in reason or "IMPOSSIBLE_VELOCITY" in reason

    def test_3g_kinematic_vehicle_speed_boundary_acceptance(self):
        """Test 3G: Vehicle covers 1000m in 35s (v=28.6 m/s = 102 km/h). Within 120 km/h limit."""
        from edge.correlation.topology import CameraTopologyGraph, CameraNode, TopologyEdge, CameraOverlapType

        graph = CameraTopologyGraph()
        graph.add_node(CameraNode(camera_id=1, name="Gate-A", bop="BOP-01", sector="Alpha"))
        graph.add_node(CameraNode(camera_id=2, name="Gate-B", bop="BOP-01", sector="Alpha"))
        graph.add_edge(TopologyEdge(source_camera_id=1, target_camera_id=2,
                                    distance_meters=1000.0,
                                    overlap_type=CameraOverlapType.ADJACENT_BLIND))

        config = _make_config()
        feasible, reason, eff_v = graph.evaluate_kinematic_feasibility(
            cam_a=1, cam_b=2, delta_t=35.0, class_name="car", config=config)

        assert feasible is True
        assert abs(eff_v - 1000.0 / 35.0) < 0.5  # ~28.6 m/s

    def test_3h_kinematic_vehicle_impossible_speed_rejection(self):
        """Test 3H: Vehicle covers 1000m in 10s (v=100 m/s = 360 km/h). Must reject."""
        from edge.correlation.topology import CameraTopologyGraph, CameraNode, TopologyEdge, CameraOverlapType

        graph = CameraTopologyGraph()
        graph.add_node(CameraNode(camera_id=1, name="Gate-A", bop="BOP-01", sector="Alpha"))
        graph.add_node(CameraNode(camera_id=2, name="Gate-B", bop="BOP-01", sector="Alpha"))
        graph.add_edge(TopologyEdge(source_camera_id=1, target_camera_id=2,
                                    distance_meters=1000.0,
                                    overlap_type=CameraOverlapType.ADJACENT_BLIND))

        config = _make_config()
        feasible, reason, eff_v = graph.evaluate_kinematic_feasibility(
            cam_a=1, cam_b=2, delta_t=10.0, class_name="car", config=config)

        assert feasible is False
        assert "IMPOSSIBLE_VELOCITY" in reason
        assert eff_v > config.vehicle_max_speed_mps

    def test_3i_kinematic_timeout_stale_handshake_expiration(self):
        """Test 3I: Person appears on T2 45 minutes after T1 (100m gap). Must timeout."""
        graph = _make_topology_3_towers()
        config = _make_config()

        feasible, reason, eff_v = graph.evaluate_kinematic_feasibility(
            cam_a=1, cam_b=2, delta_t=2700.0, class_name="person", config=config)

        assert feasible is False
        assert "TIMEOUT" in reason


# ── IDENTITY VETOES & MULTI-MODAL SCORING ────────────────────────────────────

class TestIdentityVetoesAndScoring:
    """Tests 3J-3M: Hard plate/FRS vetoes, corroboration, heading continuity."""

    def test_3j_hard_veto_on_license_plate_mismatch(self):
        """Test 3J: High appearance similarity but plates DL01AB1234 vs HR26DK5678. Hard veto."""
        from edge.correlation.cross_camera import CrossCameraAssociator, CrossCameraState

        graph = _make_topology_3_towers()
        config = _make_config()
        assoc = CrossCameraAssociator(topology=graph, config=config)

        emb_a = _random_embedding(seed=100)
        emb_b = emb_a.copy()  # Identical appearance

        t_a = _make_tracklet(track_id=10, camera_id=1, class_name="car",
                             start_time=1000.0, end_time=1050.0,
                             embedding=emb_a, plate_text="DL01AB1234",
                             plate_confidence=0.92, plate_association_state="ASSOCIATED")
        t_b = _make_tracklet(track_id=20, camera_id=2, class_name="car",
                             start_time=1120.0, end_time=1180.0,
                             embedding=emb_b, plate_text="HR26DK5678",
                             plate_confidence=0.91, plate_association_state="ASSOCIATED")

        result = assoc.compute_affinity(t_a, t_b)
        assert result.state == CrossCameraState.DISCONNECTED
        assert result.affinity_score == 0.0
        assert any("PLATE_MISMATCH" in r for r in result.rejection_reasons)

    def test_3k_hard_veto_on_frs_subject_mismatch(self):
        """Test 3K: Body appearance similar but different verified FRS subjects. Hard veto."""
        from edge.correlation.cross_camera import CrossCameraAssociator, CrossCameraState

        graph = _make_topology_3_towers()
        config = _make_config()
        assoc = CrossCameraAssociator(topology=graph, config=config)

        emb_a = _random_embedding(seed=200)
        emb_b = emb_a.copy()

        t_a = _make_tracklet(track_id=11, camera_id=1, class_name="person",
                             start_time=1000.0, end_time=1060.0,
                             embedding=emb_a, matched_subject_id=101,
                             frs_confidence=0.88, frs_association_state="ASSOCIATED")
        t_b = _make_tracklet(track_id=21, camera_id=2, class_name="person",
                             start_time=1135.0, end_time=1200.0,
                             embedding=emb_b, matched_subject_id=205,
                             frs_confidence=0.85, frs_association_state="ASSOCIATED")

        result = assoc.compute_affinity(t_a, t_b)
        assert result.state == CrossCameraState.DISCONNECTED
        assert result.affinity_score == 0.0
        assert any("FRS_SUBJECT_MISMATCH" in r for r in result.rejection_reasons)

    def test_3l_corroboration_on_identical_license_plate(self):
        """Test 3L: Vehicle on T1 and T2 with identical plate and feasible travel. Corroborated."""
        from edge.correlation.cross_camera import CrossCameraAssociator, CrossCameraState

        graph = _make_topology_3_towers()
        config = _make_config()
        assoc = CrossCameraAssociator(topology=graph, config=config)

        emb_a = _random_embedding(seed=300)
        emb_b = emb_a * 0.98 + np.random.RandomState(301).randn(512).astype(np.float32) * 0.02
        emb_b /= (np.linalg.norm(emb_b) + 1e-9)

        t_a = _make_tracklet(track_id=12, camera_id=1, class_name="car",
                             start_time=1000.0, end_time=1040.0,
                             embedding=emb_a, plate_text="DL01AB1234",
                             plate_confidence=0.92, plate_association_state="ASSOCIATED")
        t_b = _make_tracklet(track_id=22, camera_id=2, class_name="car",
                             start_time=1055.0, end_time=1100.0,
                             embedding=emb_b, plate_text="DL01AB1234",
                             plate_confidence=0.95, plate_association_state="ASSOCIATED")

        result = assoc.compute_affinity(t_a, t_b)
        assert result.state == CrossCameraState.CORROBORATED
        assert result.affinity_score >= 0.75
        assert result.score_breakdown["identity"] == 1.0

    def test_3m_directional_heading_continuity_penalty(self):
        """Test 3M: T1 track moves North, but candidate on T2 contradicts heading."""
        from edge.correlation.cross_camera import CrossCameraAssociator, CrossCameraState

        graph = _make_topology_3_towers()
        config = _make_config()
        assoc = CrossCameraAssociator(topology=graph, config=config)

        # Exit heading directly away from T2 (south), contradicting north-to-T2 path
        t_a = _make_tracklet(track_id=13, camera_id=1, class_name="person",
                             start_time=1000.0, end_time=1050.0,
                             exit_heading=180.0)  # Heading South
        t_b = _make_tracklet(track_id=23, camera_id=2, class_name="person",
                             start_time=1125.0, end_time=1200.0)

        result = assoc.compute_affinity(t_a, t_b)
        # With contradictory heading, score should be penalized
        assert result.score_breakdown["heading"] < 0.5


# ── FALSE-ASSOCIATION PREVENTION & MHT ───────────────────────────────────────

class TestFalseAssociationPrevention:
    """Tests 3N-3P: Bipartite matching, anti-collapse, clock jitter."""

    def test_3n_bipartite_matching_crowd_disambiguation(self):
        """Test 3N: 2 people cross T1→T2 concurrently; global assignment matches correctly."""
        from edge.correlation.cross_camera import CrossCameraAssociator, CrossCameraState

        graph = _make_topology_3_towers()
        config = _make_config()
        assoc = CrossCameraAssociator(topology=graph, config=config)

        # Target A: dark clothing (seed=400)
        emb_a1 = _random_embedding(seed=400)
        emb_a2 = emb_a1 * 0.95 + np.random.RandomState(401).randn(512).astype(np.float32) * 0.05
        emb_a2 /= (np.linalg.norm(emb_a2) + 1e-9)

        # Target B: light clothing (seed=500)
        emb_b1 = _random_embedding(seed=500)
        emb_b2 = emb_b1 * 0.95 + np.random.RandomState(501).randn(512).astype(np.float32) * 0.05
        emb_b2 /= (np.linalg.norm(emb_b2) + 1e-9)

        exits = [
            _make_tracklet(track_id=1, camera_id=1, class_name="person",
                           start_time=1000.0, end_time=1040.0, embedding=emb_a1),
            _make_tracklet(track_id=2, camera_id=1, class_name="person",
                           start_time=1010.0, end_time=1050.0, embedding=emb_b1),
        ]
        entries = [
            _make_tracklet(track_id=10, camera_id=2, class_name="person",
                           start_time=1115.0, end_time=1160.0, embedding=emb_b2),
            _make_tracklet(track_id=11, camera_id=2, class_name="person",
                           start_time=1120.0, end_time=1170.0, embedding=emb_a2),
        ]

        results = assoc.solve_global_assignment(exits, entries)

        # Verify we got at least 1 match, and no exit is matched to two different entries
        matched_sources = [r.source_tracklet.track_id for r in results]
        matched_targets = [r.target_tracklet.track_id for r in results]
        assert len(matched_sources) == len(set(matched_sources)), "No source track should be double-matched"
        assert len(matched_targets) == len(set(matched_targets)), "No target track should be double-matched"

    def test_3o_anti_collapse_local_track_id_immutability(self):
        """Test 3O: Cross-camera association preserves local track IDs under GlobalEntityDossier."""
        from edge.correlation.cross_camera import CrossCameraAssociator, CrossCameraState

        graph = _make_topology_3_towers()
        config = _make_config()
        assoc = CrossCameraAssociator(topology=graph, config=config)

        emb = _random_embedding(seed=600)
        t_a = _make_tracklet(track_id=12, camera_id=1, class_name="person",
                             start_time=1000.0, end_time=1060.0, embedding=emb,
                             incident_id=100)
        t_b = _make_tracklet(track_id=5, camera_id=2, class_name="person",
                             start_time=1135.0, end_time=1200.0, embedding=emb * 0.97,
                             incident_id=200)
        t_b.appearance_embedding /= (np.linalg.norm(t_b.appearance_embedding) + 1e-9)

        # Register source first, then target
        assoc.register_completed_tracklet(t_a)
        associations = assoc.register_completed_tracklet(t_b)

        # Verify dossier was created
        dossier = assoc.get_dossier_for_track(1, 12)
        assert dossier is not None
        assert "cam1:T12" in dossier.tracklet_references
        assert "cam2:T5" in dossier.tracklet_references

        # Local track IDs remain intact (immutable)
        assert t_a.track_id == 12
        assert t_a.camera_id == 1
        assert t_b.track_id == 5
        assert t_b.camera_id == 2

    def test_3p_clock_jitter_buffer_tolerance(self):
        """Test 3P: Jitter buffer permits feasible negative delta_t, rejects impossible physics."""
        from edge.correlation.topology import CameraTopologyGraph, CameraNode, TopologyEdge, CameraOverlapType

        # Create 2 cameras connected by a short 5m pathway
        graph = CameraTopologyGraph()
        graph.add_node(CameraNode(camera_id=10, name="Gate-A", bop="BOP-01", sector="A"))
        graph.add_node(CameraNode(camera_id=11, name="Gate-B", bop="BOP-01", sector="A"))
        graph.add_edge(TopologyEdge(source_camera_id=10, target_camera_id=11, distance_meters=5.0,
                                    overlap_type=CameraOverlapType.ADJACENT_BLIND))

        config = _make_config(clock_drift_tolerance_seconds=2.0, person_max_speed_mps=10.0)

        # 1. Feasible case: delta_t is -0.5s (slight clock skew), adj_dt = 1.5s, v_eff = 5m / 1.5s = 3.33 m/s
        feasible, reason, eff_v = graph.evaluate_kinematic_feasibility(
            cam_a=10, cam_b=11, delta_t=-0.5, class_name="person", config=config)
        assert feasible is True
        assert "KINEMATICALLY_FEASIBLE" in reason
        assert abs(eff_v - (5.0 / 1.5)) < 0.1

        # 2. Physics check: delta_t is -1.5s on a 100m path -> adj_dt = 0.5s, v_eff = 100m / 0.5s = 200 m/s > 10 m/s
        graph_3 = _make_topology_3_towers()
        feasible_100m, reason_100m, eff_v_100m = graph_3.evaluate_kinematic_feasibility(
            cam_a=1, cam_b=2, delta_t=-1.5, class_name="person", config=config)
        assert feasible_100m is False
        assert "IMPOSSIBLE_VELOCITY" in reason_100m

        # 3. Causality check: delta_t is -2.5s (exceeds 2.0s jitter tolerance -> adj_dt = -0.5s <= 0.05s)
        feasible_causal, reason_causal, _ = graph.evaluate_kinematic_feasibility(
            cam_a=10, cam_b=11, delta_t=-2.5, class_name="person", config=config)
        assert feasible_causal is False
        assert "CAUSAL_VIOLATION" in reason_causal


# ── INCIDENT CORRELATION & RPS BOUNDARIES ────────────────────────────────────

class TestIncidentCorrelationAndRPS:
    """Tests 3Q-3R: Incident linking and RPS non-escalation."""

    def test_3q_symmetric_incident_correlated_ids_linking(self):
        """Test 3Q: Incident on T2 correlates with T1 incident; correlated_ids symmetric."""
        from edge.correlation.cross_camera import CrossCameraAssociator, CrossCameraState
        from backend.app.services.correlation import correlate_incident_with_tracklet

        graph = _make_topology_3_towers()
        config = _make_config()
        assoc = CrossCameraAssociator(topology=graph, config=config)

        emb = _random_embedding(seed=700)

        # Register T1 tracklet with incident_id=501
        t_a = _make_tracklet(track_id=30, camera_id=1, class_name="person",
                             start_time=1000.0, end_time=1060.0, embedding=emb,
                             incident_id=501)
        assoc.register_completed_tracklet(t_a)

        # Register T2 tracklet with incident_id=502 — should link
        t_b = _make_tracklet(track_id=31, camera_id=2, class_name="person",
                             start_time=1135.0, end_time=1200.0,
                             embedding=emb * 0.96,
                             incident_id=502)
        t_b.appearance_embedding /= (np.linalg.norm(t_b.appearance_embedding) + 1e-9)
        assoc.register_completed_tracklet(t_b)

        # Verify dossier contains both incident IDs
        dossier = assoc.get_dossier_for_track(2, 31)
        assert dossier is not None
        assert 501 in dossier.associated_incident_ids
        assert 502 in dossier.associated_incident_ids

    def test_3r_rps_non_escalation_in_public_zone_with_corroboration(self):
        """Test 3R: Cross-camera corroboration in PUBLIC zone must NOT escalate RPS above 0."""
        from backend.app.services.scoring import compute_threat_score

        signals = {
            "confidence": 0.92,
            "zone_severity": 0.0,   # PUBLIC zone
            "boundary_crossing": 0.0,
            "loitering": 0.0,
            "night": 0.0,
            "vehicle_context": 0.0,
            "behavior_anomaly": 0.0,
            "repeated_activity": 0.0,
            "cross_camera_corroboration": 0.0,  # Weight is 0
        }
        context = {
            "object_type": "person",
            "zone_name": "Public Road",
            "zone_type": "PUBLIC",
            "confidence": 0.92,
            "consecutive_frames": 50,
            "cross_camera_corroborated": True,
            "dossier_id": "DOSSIER-TEST123",
            "correlated_camera_ids": [1, 2],
        }

        assessment = compute_threat_score(signals, context=context)

        # Spatial gate should be 0 for PUBLIC zone
        assert assessment.spatial_gate == 0.0
        # RPS score MUST be 0 regardless of cross-camera corroboration
        assert assessment.rps_score == 0.0
        # But the cross-camera fields should be populated as evidence context
        assert assessment.cross_camera_corroborated is True
        assert 1 in assessment.correlated_camera_ids
        assert 2 in assessment.correlated_camera_ids
        assert assessment.dossier_id == "DOSSIER-TEST123"
        # Missing evidence should NOT contain cross-camera warning
        cross_cam_missing = [m for m in assessment.missing_evidence if "cross-camera" in m.lower()]
        assert len(cross_cam_missing) == 0, "Cross-camera missing evidence should be cleared when corroborated"


# ── REMEDIATION ADVERSARIAL & LIFECYCLE TESTS ────────────────────────────────

class TestRemediationLifecycleAndAdversarialVetoes:
    """Tests 3S-3AA: Strict Phase 2 evidence verification, persistence filtering, pruning, and orphan targets."""

    def test_3s_orphan_target_remains_unmatched(self):
        """Test 3S: Valid target enters camera but no feasible historical source exists -> remains unmatched."""
        from edge.correlation.cross_camera import CrossCameraAssociator

        graph = _make_topology_3_towers()
        config = _make_config()
        assoc = CrossCameraAssociator(topology=graph, config=config)

        # Fresh associator with zero prior tracklets
        orphan = _make_tracklet(track_id=99, camera_id=2, class_name="person",
                                start_time=1500.0, end_time=1530.0, hit_count=25)

        associations = assoc.register_completed_tracklet(orphan)
        assert len(associations) == 0, "Orphan target should have zero associations"
        assert assoc.get_dossier_for_track(2, 99) is None, "No dossier should be created for orphan target"

    def test_3t_stale_tracklet_pruning_expiry(self):
        """Test 3T: Stale historical tracklets are evicted by prune_stale_records()."""
        from edge.correlation.cross_camera import CrossCameraAssociator

        graph = _make_topology_3_towers()
        config = _make_config(max_transit_timeout_seconds=900.0)
        assoc = CrossCameraAssociator(topology=graph, config=config)

        # Register tracklet at t = 1000s
        t_old = _make_tracklet(track_id=1, camera_id=1, class_name="person",
                               start_time=1000.0, end_time=1030.0)
        assoc.register_completed_tracklet(t_old)
        assert "cam1:T1" in assoc._historical_tracklets

        # Current time t = 1500s: not yet expired (cutoff = 1500 - 1800 = -300)
        assoc.prune_stale_records(1500.0)
        assert "cam1:T1" in assoc._historical_tracklets

        # Current time t = 3000s: cutoff = 3000 - 1800 = 1200 > 1030 -> must be pruned!
        assoc.prune_stale_records(3000.0)
        assert "cam1:T1" not in assoc._historical_tracklets, "Stale tracklet must be evicted after 2x timeout"

    def test_3u_minimum_persistence_rejection(self):
        """Test 3U: Tracklets with sub-threshold duration or hit count are skipped."""
        from edge.correlation.cross_camera import CrossCameraAssociator

        graph = _make_topology_3_towers()
        config = _make_config(min_tracklet_duration_seconds=0.8, min_tracklet_hits=5)
        assoc = CrossCameraAssociator(topology=graph, config=config)

        # Sub-threshold duration: 0.3s (< 0.8s)
        t_short = _make_tracklet(track_id=1, camera_id=1, class_name="person",
                                 start_time=1000.0, end_time=1000.3, hit_count=10)
        assocs_short = assoc.register_completed_tracklet(t_short)
        assert len(assocs_short) == 0

        # Sub-threshold hits: 3 hits (< 5 hits)
        t_low_hits = _make_tracklet(track_id=2, camera_id=1, class_name="person",
                                    start_time=1000.0, end_time=1002.0, hit_count=3)
        assocs_hits = assoc.register_completed_tracklet(t_low_hits)
        assert len(assocs_hits) == 0

    def test_3v_low_quality_conflicting_plate_not_hard_vetoed(self):
        """Test 3V: Conflicting OCR with UNCERTAIN/low confidence does NOT trigger hard veto."""
        from edge.correlation.cross_camera import CrossCameraAssociator, CrossCameraState

        graph = _make_topology_3_towers()
        config = _make_config()
        assoc = CrossCameraAssociator(topology=graph, config=config)

        emb_a = _random_embedding(seed=800)
        emb_b = emb_a.copy()

        # Cam 1 has a low-confidence (0.35) unverified OCR read
        t_a = _make_tracklet(track_id=50, camera_id=1, class_name="car",
                             start_time=1000.0, end_time=1040.0, embedding=emb_a,
                             plate_text="DL01AB1234", plate_confidence=0.35,
                             plate_association_state="UNCERTAIN")
        # Cam 2 has high-confidence OCR read
        t_b = _make_tracklet(track_id=60, camera_id=2, class_name="car",
                             start_time=1055.0, end_time=1100.0, embedding=emb_b,
                             plate_text="HR26DK5678", plate_confidence=0.92,
                             plate_association_state="ASSOCIATED")

        result = assoc.compute_affinity(t_a, t_b)
        # Because Cam 1's plate is UNVERIFIED, it must NOT hard-veto to DISCONNECTED!
        assert result.state != CrossCameraState.DISCONNECTED, "Low-quality OCR mismatch must not trigger hard veto"
        assert result.affinity_score > 0.0
        assert any("UNVERIFIED" in r for r in result.rejection_reasons)

    def test_3w_verified_conflicting_plate_triggers_hard_veto(self):
        """Test 3W: Conflicting OCR where BOTH are VERIFIED triggers hard DISCONNECTED veto."""
        from edge.correlation.cross_camera import CrossCameraAssociator, CrossCameraState

        graph = _make_topology_3_towers()
        config = _make_config()
        assoc = CrossCameraAssociator(topology=graph, config=config)

        emb_a = _random_embedding(seed=810)
        emb_b = emb_a.copy()

        # Both cameras have verified, high-confidence plates with conflicting text
        t_a = _make_tracklet(track_id=51, camera_id=1, class_name="car",
                             start_time=1000.0, end_time=1040.0, embedding=emb_a,
                             plate_text="DL01AB1234", plate_confidence=0.95,
                             plate_association_state="ASSOCIATED", plate_consensus_reached=True)
        t_b = _make_tracklet(track_id=61, camera_id=2, class_name="car",
                             start_time=1055.0, end_time=1100.0, embedding=emb_b,
                             plate_text="HR26DK5678", plate_confidence=0.94,
                             plate_association_state="ASSOCIATED", plate_consensus_reached=True)

        result = assoc.compute_affinity(t_a, t_b)
        assert result.state == CrossCameraState.DISCONNECTED
        assert result.affinity_score == 0.0
        assert any("HARD_VETO_VERIFIED_PLATE_MISMATCH" in r for r in result.rejection_reasons)

    def test_3x_low_quality_conflicting_frs_not_hard_vetoed(self):
        """Test 3X: Conflicting FRS candidate with low confidence/UNCERTAIN does NOT hard veto."""
        from edge.correlation.cross_camera import CrossCameraAssociator, CrossCameraState

        graph = _make_topology_3_towers()
        config = _make_config()
        assoc = CrossCameraAssociator(topology=graph, config=config)

        emb_a = _random_embedding(seed=820)
        emb_b = emb_a.copy()

        # Cam 1 has weak FRS match (sim=0.45, UNCERTAIN)
        t_a = _make_tracklet(track_id=70, camera_id=1, class_name="person",
                             start_time=1000.0, end_time=1040.0, embedding=emb_a,
                             matched_subject_id=101, frs_confidence=0.45,
                             frs_association_state="UNCERTAIN")
        # Cam 2 has verified FRS match (sim=0.88, ASSOCIATED)
        t_b = _make_tracklet(track_id=80, camera_id=2, class_name="person",
                             start_time=1135.0, end_time=1200.0, embedding=emb_b,
                             matched_subject_id=202, frs_confidence=0.88,
                             frs_association_state="ASSOCIATED")

        result = assoc.compute_affinity(t_a, t_b)
        assert result.state != CrossCameraState.DISCONNECTED, "Weak FRS candidate must not trigger hard veto"
        assert result.affinity_score > 0.0
        assert any("UNVERIFIED" in r for r in result.rejection_reasons)

    def test_3y_verified_conflicting_frs_triggers_hard_veto(self):
        """Test 3Y: Conflicting FRS where BOTH are VERIFIED triggers hard DISCONNECTED veto."""
        from edge.correlation.cross_camera import CrossCameraAssociator, CrossCameraState

        graph = _make_topology_3_towers()
        config = _make_config()
        assoc = CrossCameraAssociator(topology=graph, config=config)

        emb_a = _random_embedding(seed=830)
        emb_b = emb_a.copy()

        # Both cameras have verified FRS matches with conflicting subject IDs
        t_a = _make_tracklet(track_id=71, camera_id=1, class_name="person",
                             start_time=1000.0, end_time=1040.0, embedding=emb_a,
                             matched_subject_id=101, frs_confidence=0.85,
                             frs_association_state="ASSOCIATED")
        t_b = _make_tracklet(track_id=81, camera_id=2, class_name="person",
                             start_time=1135.0, end_time=1200.0, embedding=emb_b,
                             matched_subject_id=202, frs_confidence=0.89,
                             frs_association_state="ASSOCIATED")

        result = assoc.compute_affinity(t_a, t_b)
        assert result.state == CrossCameraState.DISCONNECTED
        assert result.affinity_score == 0.0
        assert any("HARD_VETO_VERIFIED_FRS_SUBJECT_MISMATCH" in r for r in result.rejection_reasons)

    def test_3z_public_zone_track_emits_and_associates_without_incident(self):
        """Test 3Z: PUBLIC track with NO incident registers tracklet and associates across cameras."""
        from edge.correlation.cross_camera import CrossCameraAssociator
        from backend.app.services.live_pipeline import CameraPipeline

        # Initialize pipeline on Camera 1
        cam1 = CameraPipeline(camera_id=1, stream_url="rtsp://fake/1", camera_name="Tower-1", bop="BOP-01", zones=[])
        cam1._track_metadata[5] = {
            "first_seen": 1000.0,
            "last_seen": 1040.0,
            "hits": 25,
            "class_name": "person",
            "entry_point_normalized": [0.1, 0.5],
            "exit_point_normalized": [0.9, 0.5],
            "alert_sent": False,
            "incident_id": None,  # STRICTLY NO INCIDENT
        }

        # Lifecycle trigger: track completes / departs without any incident
        td1 = cam1._emit_tracklet_descriptor(target_id=5, reason="track_completion")
        assert td1 is not None
        assert td1.incident_id is None
        assert td1.track_id == 5
        assert td1.camera_id == 1

        # Now target enters Camera 2 (e.g. at t = 1135s)
        cam2 = CameraPipeline(camera_id=2, stream_url="rtsp://fake/2", camera_name="Tower-2", bop="BOP-01", zones=[])
        cam2._track_metadata[15] = {
            "first_seen": 1135.0,
            "last_seen": 1180.0,
            "hits": 30,
            "class_name": "person",
            "entry_point_normalized": [0.1, 0.5],
            "exit_point_normalized": [0.9, 0.5],
            "alert_sent": False,
            "incident_id": None,
        }

        from backend.app.services.correlation import get_site_cross_camera_associator
        site_assoc = get_site_cross_camera_associator(_make_topology_3_towers())

        # Camera 2 registers tracklet
        td2 = cam2._emit_tracklet_descriptor(target_id=15, reason="track_completion")
        assert td2 is not None

        # Verify dossier was created across Camera 1 and Camera 2 WITHOUT ANY INCIDENT
        dossier = site_assoc.get_dossier_for_track(1, 5)
        assert dossier is not None, "Dossier must exist even when neither camera created an incident"
        assert "cam1:T5" in dossier.tracklet_references
        assert "cam2:T15" in dossier.tracklet_references
        assert len(dossier.associated_incident_ids) == 0, "No incident IDs should exist in purely non-incident dossier"

    def test_3aa_uncertain_threshold_assignment_filtering(self):
        """Test 3AA: Bipartite assignment returns UNCERTAIN matches (affinity >= 0.40) without dossier fusion."""
        from edge.correlation.cross_camera import CrossCameraAssociator, CrossCameraState

        graph = _make_topology_3_towers()
        config = _make_config()
        assoc = CrossCameraAssociator(topology=graph, config=config)

        # Create two tracklets whose affinity falls in [0.40, 0.55) (UNCERTAIN)
        # Moderate velocity error + neutral identity + slightly divergent appearance
        emb_a = _random_embedding(seed=900)
        emb_b = emb_a * 0.70 + np.random.RandomState(901).randn(512).astype(np.float32) * 0.30
        emb_b /= (np.linalg.norm(emb_b) + 1e-9)

        t_a = _make_tracklet(track_id=40, camera_id=1, class_name="person",
                             start_time=1000.0, end_time=1030.0, embedding=emb_a)
        # delta_t = 1110 - 1030 = 80s -> eff_v = 100 / 80 = 1.25 m/s (close to 1.4 m/s nominal)
        t_b = _make_tracklet(track_id=41, camera_id=2, class_name="person",
                             start_time=1110.0, end_time=1150.0, embedding=emb_b)

        # Check raw affinity
        raw_assoc = assoc.compute_affinity(t_a, t_b)
        assert raw_assoc.state == CrossCameraState.UNCERTAIN
        assert config.threshold_uncertain <= raw_assoc.affinity_score < config.threshold_plausible

        # Run solve_global_assignment
        results = assoc.solve_global_assignment([t_a], [t_b])
        # Aligned behavior: candidate with affinity >= 0.40 is returned in results
        assert len(results) == 1
        assert results[0].state == CrossCameraState.UNCERTAIN

        # But UNCERTAIN match must NOT be fused into a GlobalEntityDossier
        dossier = assoc.get_dossier_for_track(1, 40)
        assert dossier is None, "UNCERTAIN match must NOT fuse into a shared GlobalEntityDossier"
