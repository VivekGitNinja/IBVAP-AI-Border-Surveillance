"""
Cross-Camera Tracklet Association & Multi-Hypothesis Tracking Engine
====================================================================

Associates single-camera ByteTrack tracklets across adjacent camera towers in a
perimeter security network, enforcing kinematic feasibility, directional continuity,
hard identity non-conflict vetoes, and global bipartite matching.

Classification:
    Production-structured cross-camera association, pending field validation.
"""

from __future__ import annotations
import math
import uuid
import logging
from enum import Enum
from typing import Optional, Any, Dict, List, Tuple
from dataclasses import dataclass, field

import numpy as np

from edge.correlation.topology import CameraTopologyGraph, CameraOverlapType

logger = logging.getLogger(__name__)


# ── CONFIGURATION CONTRACT ───────────────────────────────────────────────────

@dataclass
class CrossCameraConfig:
    """Configurable parameters for cross-camera association and topology gating."""
    # Kinematic speed limits (m/s)
    person_max_speed_mps: float = 10.0       # 36 km/h: Sprinting upper bound
    person_nominal_speed_mps: float = 1.4    # ~5 km/h: Normal walking speed
    person_min_speed_mps: float = 0.3        # ~1 km/h: Slow crawl/creep

    vehicle_max_speed_mps: float = 33.3      # 120 km/h: Maximum patrol/road sprint
    vehicle_nominal_speed_mps: float = 8.3   # 30 km/h: Standard perimeter patrol speed
    vehicle_min_speed_mps: float = 1.0       # ~3.6 km/h: Idle creep

    # Temporal constraints (seconds)
    clock_drift_tolerance_seconds: float = 2.0  # NTP jitter buffer across edge nodes
    max_transit_timeout_seconds: float = 900.0  # 15 minutes: Max gap before dropping hand-off
    min_tracklet_duration_seconds: float = 0.8  # Must be tracked for >= 0.8s before hand-off
    min_tracklet_hits: int = 5                  # Minimum detector confirmations

    # Overlapping camera handoff window (seconds)
    overlapping_min_dt_seconds: float = -2.0    # Allow 2s concurrent detection in overlap
    overlapping_max_dt_seconds: float = 5.0     # Max handoff latency across overlap boundary

    # Affinity score weights (must sum to 1.0)
    weight_kinematic: float = 0.35
    weight_appearance: float = 0.35
    weight_identity: float = 0.20
    weight_heading: float = 0.10

    # Uncertainty classification thresholds
    threshold_corroborated: float = 0.75  # High confidence: Corroborated cross-camera link
    threshold_plausible: float = 0.55     # Moderate confidence: Plausible transit link
    threshold_uncertain: float = 0.40     # Low confidence: Monitored, unlinked

    # Appearance constraints
    reid_min_cosine_similarity: float = 0.45  # Sub-threshold appearance matches discarded
    appearance_embedding_dim: int = 512       # Standard OSNet feature vector dimension


# ── DATA CONTRACTS & ENUMS ───────────────────────────────────────────────────

class CrossCameraState(str, Enum):
    """Lifecycle and certainty states for cross-camera tracklet association."""
    CORROBORATED = "CORROBORATED"  # High-confidence multi-tower link (shared dossier)
    PLAUSIBLE = "PLAUSIBLE"        # Feasible link; linked breadcrumbs for operator
    UNCERTAIN = "UNCERTAIN"        # Ambiguous timing/appearance; maintained independently
    DISCONNECTED = "DISCONNECTED"  # Kinematically or categorically rejected


@dataclass
class TrackletDescriptor:
    """Summarized spatio-temporal and evidentiary descriptor of a single-camera track."""
    track_id: int
    camera_id: int
    class_name: str                          # "person" or vehicle type
    start_time: float
    end_time: float
    duration: float
    hit_count: int
    entry_point_normalized: list[float] = field(default_factory=lambda: [0.0, 0.0])
    exit_point_normalized: list[float] = field(default_factory=lambda: [0.0, 0.0])
    exit_heading_degrees: Optional[float] = None
    appearance_embedding: Optional[np.ndarray] = None  # Normalized 512-d vector
    plate_text: Optional[str] = None
    matched_subject_id: Optional[int] = None
    zone_events: list[str] = field(default_factory=list)
    incident_id: Optional[int] = None
    crop_path: Optional[str] = None

    # Phase 2 Evidence Quality & Verification Contracts
    plate_confidence: Optional[float] = None
    plate_association_state: Optional[str] = None  # "ASSOCIATED", "UNCERTAIN", "REJECTED"
    plate_consensus_reached: bool = False
    frs_confidence: Optional[float] = None
    frs_association_state: Optional[str] = None    # "ASSOCIATED", "UNCERTAIN", "REJECTED"

    def is_plate_verified(self) -> bool:
        """Check if license plate evidence satisfies Phase 2 verified/high-quality contract."""
        if not self.plate_text:
            return False
        # If explicit association state is present, must be ASSOCIATED
        if self.plate_association_state is not None:
            if self.plate_association_state in ("UNCERTAIN", "REJECTED"):
                return False
            if self.plate_association_state == "ASSOCIATED":
                return True
        # If multi-frame consensus reached
        if self.plate_consensus_reached:
            return True
        # If OCR confidence is high (>= 0.70)
        if self.plate_confidence is not None:
            return self.plate_confidence >= 0.70
        # Merely non-None plate_text without verification metadata is NOT verified
        return False

    def is_frs_verified(self) -> bool:
        """Check if facial recognition evidence satisfies Phase 2 verified/high-quality contract."""
        if self.matched_subject_id is None:
            return False
        # If explicit association state is present, must be ASSOCIATED
        if self.frs_association_state is not None:
            if self.frs_association_state in ("UNCERTAIN", "REJECTED"):
                return False
            if self.frs_association_state == "ASSOCIATED":
                return True
        # If FRS confidence/similarity is high (>= 0.60)
        if self.frs_confidence is not None:
            return self.frs_confidence >= 0.60
        # Merely non-None matched_subject_id without verification metadata is NOT verified
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "track_id": self.track_id,
            "camera_id": self.camera_id,
            "class_name": self.class_name,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration": self.duration,
            "hit_count": self.hit_count,
            "entry_point_normalized": self.entry_point_normalized,
            "exit_point_normalized": self.exit_point_normalized,
            "exit_heading_degrees": self.exit_heading_degrees,
            "has_embedding": self.appearance_embedding is not None,
            "plate_text": self.plate_text,
            "plate_confidence": self.plate_confidence,
            "plate_association_state": self.plate_association_state,
            "plate_consensus_reached": self.plate_consensus_reached,
            "is_plate_verified": self.is_plate_verified(),
            "matched_subject_id": self.matched_subject_id,
            "frs_confidence": self.frs_confidence,
            "frs_association_state": self.frs_association_state,
            "is_frs_verified": self.is_frs_verified(),
            "zone_events": self.zone_events,
            "incident_id": self.incident_id,
            "crop_path": self.crop_path,
        }


@dataclass
class CrossCameraAssociation:
    """A mathematically evaluated association between two tracklets on different cameras."""
    association_id: str
    source_tracklet: TrackletDescriptor
    target_tracklet: TrackletDescriptor
    state: CrossCameraState
    affinity_score: float
    effective_velocity_mps: float
    transit_distance_meters: float
    transit_delta_t_seconds: float
    score_breakdown: dict[str, float]        # kinematic, appearance, identity, heading
    rejection_reasons: list[str] = field(default_factory=list)
    timestamp: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "association_id": self.association_id,
            "source_camera_id": self.source_tracklet.camera_id,
            "source_track_id": self.source_tracklet.track_id,
            "target_camera_id": self.target_tracklet.camera_id,
            "target_track_id": self.target_tracklet.track_id,
            "state": self.state.value if isinstance(self.state, CrossCameraState) else str(self.state),
            "affinity_score": round(self.affinity_score, 4),
            "effective_velocity_mps": round(self.effective_velocity_mps, 2),
            "transit_distance_meters": round(self.transit_distance_meters, 1),
            "transit_delta_t_seconds": round(self.transit_delta_t_seconds, 2),
            "score_breakdown": {k: round(v, 4) for k, v in self.score_breakdown.items()},
            "rejection_reasons": self.rejection_reasons,
            "timestamp": self.timestamp,
        }


@dataclass
class GlobalEntityDossier:
    """Non-destructive multi-camera entity container linking local tracklets."""
    dossier_id: str
    primary_class: str
    tracklet_references: list[str]           # ["cam1:T12", "cam2:T05"]
    camera_ids: list[int]
    first_seen: float
    last_seen: float
    fused_timeline: list[dict[str, Any]]
    associated_incident_ids: list[int]
    evidence_status: str                     # "CORROBORATED" or "PLAUSIBLE"
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dossier_id": self.dossier_id,
            "primary_class": self.primary_class,
            "tracklet_references": self.tracklet_references,
            "camera_ids": self.camera_ids,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "fused_timeline": self.fused_timeline,
            "associated_incident_ids": self.associated_incident_ids,
            "evidence_status": self.evidence_status,
            "provenance": self.provenance,
        }


# ── BIPARTITE MATCHING HELPER ────────────────────────────────────────────────

def _solve_min_cost_bipartite_assignment(cost_matrix: np.ndarray) -> list[tuple[int, int]]:
    """
    Solve minimum cost bipartite matching on a 2D cost matrix.
    Uses Kuhn-Munkres (Hungarian algorithm) if feasible; falls back to greedy.
    """
    if cost_matrix.size == 0:
        return []

    try:
        from scipy.optimize import linear_sum_assignment
        row_ind, col_ind = linear_sum_assignment(cost_matrix)
        return list(zip(row_ind, col_ind))
    except (ImportError, Exception):
        pass

    # Native Hungarian algorithm implementation for moderate matrix sizes
    m, n = cost_matrix.shape
    assignments = []
    available_cols = set(range(n))

    # Greedy sort on costs as robust fallback
    candidates = []
    for r in range(m):
        for c in range(n):
            if not np.isinf(cost_matrix[r, c]) and not np.isnan(cost_matrix[r, c]):
                candidates.append((cost_matrix[r, c], r, c))

    candidates.sort(key=lambda x: x[0])
    assigned_rows = set()
    assigned_cols = set()

    for cost, r, c in candidates:
        if r not in assigned_rows and c not in assigned_cols:
            assigned_rows.add(r)
            assigned_cols.add(c)
            assignments.append((r, c))

    return assignments


# ── CORE CROSS-CAMERA ASSOCIATION ENGINE ─────────────────────────────────────

class CrossCameraAssociator:
    """
    Cross-camera tracklet association engine.
    Applies topology graph, kinematic gates, hard identity vetoes, and
    global bipartite assignment to link single-camera tracklets across towers.
    """

    def __init__(
        self,
        topology: CameraTopologyGraph,
        config: Optional[CrossCameraConfig] = None,
        reid_engine: Optional[Any] = None,
    ):
        self.topology = topology
        self.config = config or CrossCameraConfig()
        self.reid_engine = reid_engine

        # Historical tracklets available for forward association: {track_ref -> TrackletDescriptor}
        self._historical_tracklets: dict[str, TrackletDescriptor] = {}
        # Active dossiers: {dossier_id -> GlobalEntityDossier}
        self._dossiers: dict[str, GlobalEntityDossier] = {}
        # Map local track ref to dossier ID: {"cam1:T12" -> dossier_id}
        self._track_to_dossier: dict[str, str] = {}

    def register_completed_tracklet(
        self,
        descriptor: TrackletDescriptor,
    ) -> list[CrossCameraAssociation]:
        """
        Register a completed or transitioning tracklet from a single-camera pipeline.
        Searches historical tracklets from adjacent cameras for feasible incoming associations,
        stores the descriptor for future transitions, and updates dossiers.
        """
        track_ref = f"cam{descriptor.camera_id}:T{descriptor.track_id}"
        self._historical_tracklets[track_ref] = descriptor

        # Minimum persistence filter
        if descriptor.duration < self.config.min_tracklet_duration_seconds or descriptor.hit_count < self.config.min_tracklet_hits:
            logger.debug(f"Tracklet {track_ref} skipped cross-camera matching: sub-threshold duration/hits ({descriptor.duration:.2f}s, {descriptor.hit_count} hits)")
            return []

        # Find historical candidates from adjacent/topology-connected cameras that could have transitioned into descriptor
        candidate_sources: list[TrackletDescriptor] = []
        for ref, past in self._historical_tracklets.items():
            if past.camera_id == descriptor.camera_id:
                continue
            # Past must have started or existed before descriptor arrived
            if past.end_time <= descriptor.start_time + self.config.clock_drift_tolerance_seconds:
                candidate_sources.append(past)

        if not candidate_sources:
            return []

        # Solve assignment against incoming candidate descriptor
        associations = []
        for past in candidate_sources:
            assoc = self.compute_affinity(past, descriptor)
            if assoc.state in (CrossCameraState.CORROBORATED, CrossCameraState.PLAUSIBLE):
                associations.append(assoc)

        # Sort by affinity descending
        associations.sort(key=lambda a: a.affinity_score, reverse=True)

        if associations:
            best = associations[0]
            self._update_or_create_dossier(best)

        return associations

    def compute_affinity(
        self,
        track_a: TrackletDescriptor,
        track_b: TrackletDescriptor,
    ) -> CrossCameraAssociation:
        """
        Evaluate full multi-modal affinity between tracklet A (source) and tracklet B (target).
        Enforces topological adjacency, kinematic feasibility, hard identity vetoes,
        and directional continuity.
        """
        cam_a = track_a.camera_id
        cam_b = track_b.camera_id
        delta_t = track_b.start_time - track_a.end_time
        dist = self.topology.compute_shortest_path_distance(cam_a, cam_b) or 0.0

        assoc_id = f"ASSOC-{cam_a}:T{track_a.track_id}->{cam_b}:T{track_b.track_id}"
        reasons: list[str] = []

        # 1. Class Compatibility Check
        is_veh_a = track_a.class_name in ("car", "truck", "bus", "motorcycle", "vehicle")
        is_veh_b = track_b.class_name in ("car", "truck", "bus", "motorcycle", "vehicle")
        if is_veh_a != is_veh_b:
            return CrossCameraAssociation(
                association_id=assoc_id,
                source_tracklet=track_a,
                target_tracklet=track_b,
                state=CrossCameraState.DISCONNECTED,
                affinity_score=0.0,
                effective_velocity_mps=0.0,
                transit_distance_meters=dist,
                transit_delta_t_seconds=delta_t,
                score_breakdown={"class_mismatch": 0.0},
                rejection_reasons=["CLASS_MISMATCH_PERSON_VEHICLE"],
                timestamp=track_b.end_time,
            )

        # 2. Kinematic Feasibility Gate
        is_feasible, reason_code, eff_v = self.topology.evaluate_kinematic_feasibility(
            cam_a=cam_a,
            cam_b=cam_b,
            delta_t=delta_t,
            class_name=track_b.class_name,
            config=self.config,
        )

        if not is_feasible:
            return CrossCameraAssociation(
                association_id=assoc_id,
                source_tracklet=track_a,
                target_tracklet=track_b,
                state=CrossCameraState.DISCONNECTED,
                affinity_score=0.0,
                effective_velocity_mps=eff_v,
                transit_distance_meters=dist,
                transit_delta_t_seconds=delta_t,
                score_breakdown={"kinematic": 0.0},
                rejection_reasons=[reason_code],
                timestamp=track_b.end_time,
            )

        # 3. Hard Identity Non-Conflict Vetoes
        # 3a. License Plate Veto (Vehicles) - Only when BOTH observations satisfy Phase 2 verified contract
        if is_veh_a and is_veh_b and track_a.plate_text and track_b.plate_text:
            from backend.app.services.anpr import normalize_indian_plate
            p_a = normalize_indian_plate(track_a.plate_text)
            p_b = normalize_indian_plate(track_b.plate_text)
            if track_a.is_plate_verified() and track_b.is_plate_verified():
                edit_dist = self._levenshtein_distance(p_a, p_b)
                if edit_dist > 1:
                    return CrossCameraAssociation(
                        association_id=assoc_id,
                        source_tracklet=track_a,
                        target_tracklet=track_b,
                        state=CrossCameraState.DISCONNECTED,
                        affinity_score=0.0,
                        effective_velocity_mps=eff_v,
                        transit_distance_meters=dist,
                        transit_delta_t_seconds=delta_t,
                        score_breakdown={"plate_conflict": 0.0},
                        rejection_reasons=[f"HARD_VETO_VERIFIED_PLATE_MISMATCH_{p_a}_VS_{p_b}"],
                        timestamp=track_b.end_time,
                    )
            elif (track_a.plate_association_state in ("UNCERTAIN", "REJECTED") or
                  track_b.plate_association_state in ("UNCERTAIN", "REJECTED") or
                  (track_a.plate_confidence is not None and track_a.plate_confidence < 0.70) or
                  (track_b.plate_confidence is not None and track_b.plate_confidence < 0.70)):
                reasons.append("UNVERIFIED_OR_LOW_QUALITY_PLATE_EVIDENCE_IGNORED_FOR_HARD_VETO")

        # 3b. FRS Subject ID Veto (Persons) - Only when BOTH observations satisfy Phase 2 verified contract
        if not is_veh_a and track_a.matched_subject_id is not None and track_b.matched_subject_id is not None:
            if track_a.is_frs_verified() and track_b.is_frs_verified():
                if track_a.matched_subject_id != track_b.matched_subject_id:
                    return CrossCameraAssociation(
                        association_id=assoc_id,
                        source_tracklet=track_a,
                        target_tracklet=track_b,
                        state=CrossCameraState.DISCONNECTED,
                        affinity_score=0.0,
                        effective_velocity_mps=eff_v,
                        transit_distance_meters=dist,
                        transit_delta_t_seconds=delta_t,
                        score_breakdown={"subject_conflict": 0.0},
                        rejection_reasons=[f"HARD_VETO_VERIFIED_FRS_SUBJECT_MISMATCH_{track_a.matched_subject_id}_VS_{track_b.matched_subject_id}"],
                        timestamp=track_b.end_time,
                    )
            elif (track_a.frs_association_state in ("UNCERTAIN", "REJECTED") or
                  track_b.frs_association_state in ("UNCERTAIN", "REJECTED") or
                  (track_a.frs_confidence is not None and track_a.frs_confidence < 0.60) or
                  (track_b.frs_confidence is not None and track_b.frs_confidence < 0.60)):
                reasons.append("UNVERIFIED_OR_LOW_QUALITY_FRS_EVIDENCE_IGNORED_FOR_HARD_VETO")

        # 4. Soft Multi-Modal Scoring
        # 4a. Kinematic Score
        v_nom = self.config.vehicle_nominal_speed_mps if is_veh_a else self.config.person_nominal_speed_mps
        sigma_v = 4.0 if is_veh_a else 1.5
        kin_score = math.exp(-((eff_v - v_nom) ** 2) / (2 * (sigma_v ** 2)))

        # 4b. Appearance Score
        app_score = 0.5  # Neutral default if no embeddings
        if track_a.appearance_embedding is not None and track_b.appearance_embedding is not None:
            norm_a = np.linalg.norm(track_a.appearance_embedding)
            norm_b = np.linalg.norm(track_b.appearance_embedding)
            if norm_a > 0 and norm_b > 0:
                cos_sim = float(np.dot(track_a.appearance_embedding, track_b.appearance_embedding) / (norm_a * norm_b))
                app_score = max(0.0, min(1.0, cos_sim))
                if app_score < self.config.reid_min_cosine_similarity:
                    reasons.append("LOW_APPEARANCE_SIMILARITY")

        # 4c. Identity Corroboration Score
        id_score = 0.5  # Neutral default
        if is_veh_a and track_a.plate_text and track_b.plate_text:
            p_a = normalize_indian_plate(track_a.plate_text)
            p_b = normalize_indian_plate(track_b.plate_text)
            if p_a == p_b:
                id_score = 1.0
            elif self._levenshtein_distance(p_a, p_b) <= 1:
                id_score = 0.75
            else:
                # Conflicting plates that were not verified (so no hard veto)
                id_score = 0.3
        elif not is_veh_a and track_a.matched_subject_id is not None and track_b.matched_subject_id is not None:
            if track_a.matched_subject_id == track_b.matched_subject_id:
                id_score = 1.0
            else:
                # Conflicting FRS that was not verified
                id_score = 0.3

        # 4d. Heading / Directional Continuity
        heading_score = 0.7  # Default plausible
        if track_a.exit_heading_degrees is not None:
            node_a = self.topology.get_node(cam_a)
            node_b = self.topology.get_node(cam_b)
            if node_a and node_b and (node_a.latitude != 0 or node_a.longitude != 0):
                # Target bearing from A to B
                bearing_ab = self._compute_bearing_degrees(node_a.latitude, node_a.longitude, node_b.latitude, node_b.longitude)
                angle_diff = abs((track_a.exit_heading_degrees - bearing_ab + 180) % 360 - 180)
                if angle_diff > 120.0:
                    heading_score = 0.1
                    reasons.append(f"DIRECTION_CONTRADICTION_{angle_diff:.0f}_DEG")
                elif angle_diff < 45.0:
                    heading_score = 1.0
                else:
                    heading_score = 0.5

        # 5. Composite Affinity Score
        total_affinity = (
            self.config.weight_kinematic * kin_score +
            self.config.weight_appearance * app_score +
            self.config.weight_identity * id_score +
            self.config.weight_heading * heading_score
        )

        # 6. State Classification
        if total_affinity >= self.config.threshold_corroborated:
            state = CrossCameraState.CORROBORATED
        elif total_affinity >= self.config.threshold_plausible:
            state = CrossCameraState.PLAUSIBLE
        elif total_affinity >= self.config.threshold_uncertain:
            state = CrossCameraState.UNCERTAIN
        else:
            state = CrossCameraState.DISCONNECTED

        return CrossCameraAssociation(
            association_id=assoc_id,
            source_tracklet=track_a,
            target_tracklet=track_b,
            state=state,
            affinity_score=total_affinity,
            effective_velocity_mps=eff_v,
            transit_distance_meters=dist,
            transit_delta_t_seconds=delta_t,
            score_breakdown={
                "kinematic": kin_score,
                "appearance": app_score,
                "identity": id_score,
                "heading": heading_score,
            },
            rejection_reasons=reasons,
            timestamp=track_b.end_time,
        )

    def solve_global_assignment(
        self,
        recent_exits: list[TrackletDescriptor],
        new_entries: list[TrackletDescriptor],
    ) -> list[CrossCameraAssociation]:
        """
        Solve global bipartite assignment across multi-target boundary crossings
        (e.g., crowd or convoy crossing between two towers) to eliminate greedy hijacking.
        """
        if not recent_exits or not new_entries:
            return []

        M = len(recent_exits)
        N = len(new_entries)
        cost_matrix = np.full((M, N), float("inf"), dtype=np.float32)
        associations_matrix: dict[tuple[int, int], CrossCameraAssociation] = {}

        for i, trk_a in enumerate(recent_exits):
            for j, trk_b in enumerate(new_entries):
                assoc = self.compute_affinity(trk_a, trk_b)
                associations_matrix[(i, j)] = assoc
                if assoc.state in (CrossCameraState.CORROBORATED, CrossCameraState.PLAUSIBLE, CrossCameraState.UNCERTAIN):
                    cost_matrix[i, j] = 1.0 - assoc.affinity_score

        # Solve assignment
        matches = _solve_min_cost_bipartite_assignment(cost_matrix)
        results = []

        for r, c in matches:
            assoc = associations_matrix.get((r, c))
            if assoc and assoc.affinity_score >= self.config.threshold_uncertain:
                results.append(assoc)
                # Only high/moderate confidence links fuse into a shared GlobalEntityDossier
                if assoc.state in (CrossCameraState.CORROBORATED, CrossCameraState.PLAUSIBLE):
                    self._update_or_create_dossier(assoc)

        return results

    def _update_or_create_dossier(self, assoc: CrossCameraAssociation) -> GlobalEntityDossier:
        """Link tracklets under a non-destructive GlobalEntityDossier."""
        ref_a = f"cam{assoc.source_tracklet.camera_id}:T{assoc.source_tracklet.track_id}"
        ref_b = f"cam{assoc.target_tracklet.camera_id}:T{assoc.target_tracklet.track_id}"

        dossier_id = self._track_to_dossier.get(ref_a) or self._track_to_dossier.get(ref_b)
        if dossier_id and dossier_id in self._dossiers:
            dossier = self._dossiers[dossier_id]
            if ref_a not in dossier.tracklet_references:
                dossier.tracklet_references.append(ref_a)
            if ref_b not in dossier.tracklet_references:
                dossier.tracklet_references.append(ref_b)
            if assoc.target_tracklet.camera_id not in dossier.camera_ids:
                dossier.camera_ids.append(assoc.target_tracklet.camera_id)
            dossier.last_seen = max(dossier.last_seen, assoc.target_tracklet.end_time)
        else:
            dossier_id = f"DOSSIER-{uuid.uuid4().hex[:8].upper()}"
            dossier = GlobalEntityDossier(
                dossier_id=dossier_id,
                primary_class=assoc.source_tracklet.class_name,
                tracklet_references=[ref_a, ref_b],
                camera_ids=[assoc.source_tracklet.camera_id, assoc.target_tracklet.camera_id],
                first_seen=assoc.source_tracklet.start_time,
                last_seen=assoc.target_tracklet.end_time,
                fused_timeline=[],
                associated_incident_ids=[],
                evidence_status=assoc.state.value,
                provenance={"created_via": assoc.association_id, "score": assoc.affinity_score},
            )
            self._dossiers[dossier_id] = dossier

        self._track_to_dossier[ref_a] = dossier_id
        self._track_to_dossier[ref_b] = dossier_id

        # Update associated incident IDs if available
        if assoc.source_tracklet.incident_id and assoc.source_tracklet.incident_id not in dossier.associated_incident_ids:
            dossier.associated_incident_ids.append(assoc.source_tracklet.incident_id)
        if assoc.target_tracklet.incident_id and assoc.target_tracklet.incident_id not in dossier.associated_incident_ids:
            dossier.associated_incident_ids.append(assoc.target_tracklet.incident_id)

        return dossier

    def get_dossier_for_track(self, camera_id: int, track_id: Any) -> Optional[GlobalEntityDossier]:
        """Retrieve global entity dossier for a single camera track reference."""
        ref = f"cam{camera_id}:T{track_id}"
        dossier_id = self._track_to_dossier.get(ref)
        if not dossier_id and isinstance(track_id, str) and track_id.startswith("TRK-"):
            try:
                numeric_tid = int(track_id.split("-")[-1])
                ref2 = f"cam{camera_id}:T{numeric_tid}"
                dossier_id = self._track_to_dossier.get(ref2)
            except Exception:
                pass
        elif not dossier_id and isinstance(track_id, int):
            ref2 = f"cam{camera_id}:TTRK-{track_id:02d}"
            dossier_id = self._track_to_dossier.get(ref2)
        if dossier_id:
            return self._dossiers.get(dossier_id)
        return None

    def prune_stale_records(self, current_time: float) -> None:
        """Evict tracklet descriptors exceeding max history timeout."""
        cutoff = current_time - (self.config.max_transit_timeout_seconds * 2)
        expired = [ref for ref, trk in self._historical_tracklets.items() if trk.end_time < cutoff]
        for ref in expired:
            del self._historical_tracklets[ref]

    def reset(self) -> None:
        """Reset internal history and dossier tables."""
        self._historical_tracklets.clear()
        self._dossiers.clear()
        self._track_to_dossier.clear()

    @staticmethod
    def _levenshtein_distance(s1: str, s2: str) -> int:
        """Standard Levenshtein edit distance between two strings."""
        if len(s1) < len(s2):
            return CrossCameraAssociator._levenshtein_distance(s2, s1)
        if len(s2) == 0:
            return len(s1)
        prev = range(len(s2) + 1)
        for i, c1 in enumerate(s1):
            curr = [i + 1]
            for j, c2 in enumerate(s2):
                insertions = prev[j + 1] + 1
                deletions = curr[j] + 1
                substitutions = prev[j] + (c1 != c2)
                curr.append(min(insertions, deletions, substitutions))
            prev = curr
        return prev[-1]

    @staticmethod
    def _compute_bearing_degrees(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Calculate forward azimuth / bearing from point 1 to point 2 in degrees [0, 360)."""
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        delta_lambda = math.radians(lon2 - lon1)
        y = math.sin(delta_lambda) * math.cos(phi2)
        x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(delta_lambda)
        bearing = math.degrees(math.atan2(y, x))
        return (bearing + 360) % 360
