"""
Production-structured evidence association layer for ANPR and FRS candidate integration.
(Pending real-world field validation)

Connects raw ANPR license plate detections and FRS face candidates to active ByteTrack
person/vehicle tracks with:
- Configurable anatomical and geometric spatial gating
- Track continuity and temporal proximity validation
- Multi-frame plate consensus & frequency weighting
- Explicit stale observation expiration (TTL)
- Semantic separation of identity evidence from RPS threat scoring
- Full evidence provenance tracking and missing evidence identification
"""

from __future__ import annotations

import enum
import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ── ASSOCIATION ENUMS & STATES ───────────────────────────────────────────────

class AssociationState(str, enum.Enum):
    """Spatial and kinematic association classification."""
    ASSOCIATED = "ASSOCIATED"
    UNCERTAIN = "UNCERTAIN"
    REJECTED = "REJECTED"


class EvidenceStatus(str, enum.Enum):
    """Epistemic evidence lifecycle status."""
    VERIFIED = "VERIFIED"
    CANDIDATE = "CANDIDATE"
    MISSING = "MISSING"


class WatchlistAuthStatus(str, enum.Enum):
    """Watchlist and operational authorization status."""
    UNKNOWN = "UNKNOWN"
    UNCHECKED = "UNCHECKED"
    AUTHORIZED = "AUTHORIZED"
    FLAGGED = "FLAGGED"


# ── EVIDENCE DATA CONTRACTS ──────────────────────────────────────────────────

@dataclass
class PlateEvidenceCandidate:
    """An individual license plate reading candidate associated with a vehicle track."""
    candidate_id: str
    plate_text: str
    ocr_confidence: float
    bbox: list[float]              # [x1, y1, x2, y2] in full-frame coordinates
    relative_bbox: list[float]     # [rx1, ry1, rx2, ry2] relative to vehicle bbox
    frame_id: int
    timestamp: float
    camera_id: int
    crop_path: Optional[str] = None
    is_valid_format: bool = False
    association_state: AssociationState = AssociationState.UNCERTAIN
    association_score: float = 0.0
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass
class PlateTrackRecord:
    """Multi-frame aggregated license plate history for an individual vehicle track."""
    track_id: int
    camera_id: int
    candidates: list[PlateEvidenceCandidate] = field(default_factory=list)
    consensus_text: Optional[str] = None
    consensus_confidence: float = 0.0
    observation_count: int = 0
    first_seen: float = 0.0
    last_updated: float = 0.0
    is_stale: bool = False
    evidence_status: str = EvidenceStatus.MISSING.value

    def get_latest_candidate(self) -> Optional[PlateEvidenceCandidate]:
        return self.candidates[-1] if self.candidates else None


@dataclass
class FaceEvidenceCandidate:
    """An individual facial recognition candidate associated with a person track."""
    candidate_id: str
    bbox: list[float]              # [x1, y1, x2, y2] in full-frame coordinates
    relative_bbox: list[float]     # [rx1, ry1, rx2, ry2] relative to person bbox
    detection_confidence: float
    similarity_score: Optional[float] = None
    matched_subject_id: Optional[int] = None
    matched_subject_name: Optional[str] = None
    frame_id: int = 0
    timestamp: float = 0.0
    camera_id: int = 0
    crop_path: Optional[str] = None
    matching_threshold: float = 0.50
    model_provenance: str = "OpenCV YuNet + SFace 128D"
    is_low_quality: bool = False
    association_state: AssociationState = AssociationState.UNCERTAIN
    association_score: float = 0.0
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass
class FaceTrackRecord:
    """Multi-frame aggregated facial recognition history for an individual person track."""
    track_id: int
    camera_id: int
    candidates: list[FaceEvidenceCandidate] = field(default_factory=list)
    best_candidate: Optional[FaceEvidenceCandidate] = None
    observation_count: int = 0
    first_seen: float = 0.0
    last_updated: float = 0.0
    is_stale: bool = False
    evidence_status: str = EvidenceStatus.MISSING.value
    watchlist_authorization_status: str = WatchlistAuthStatus.UNCHECKED.value

    def get_latest_candidate(self) -> Optional[FaceEvidenceCandidate]:
        return self.candidates[-1] if self.candidates else None


@dataclass
class UnifiedTrackEvidence:
    """Unified provenance and evidence bundle for an active physical track."""
    camera_id: int
    track_id: int
    target_id: Optional[str]
    class_name: str
    track_bbox: list[float]
    ground_anchor: list[float]
    first_seen_ts: float
    last_seen_ts: float
    frame_count: int
    plate_record: Optional[PlateTrackRecord] = None
    face_record: Optional[FaceTrackRecord] = None
    zone_events: list[dict] = field(default_factory=list)
    identity_evidence_status: str = EvidenceStatus.MISSING.value
    vehicle_evidence_status: str = EvidenceStatus.MISSING.value
    watchlist_authorization_status: str = WatchlistAuthStatus.UNKNOWN.value
    missing_evidence: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_rps_context(self) -> dict[str, Any]:
        """Convert to clean RPS context without leaking raw similarity/OCR into threat scores."""
        # Evidence confidence reflects sensor measurement quality only
        evidence_conf = 0.0
        if self.face_record and self.face_record.best_candidate:
            evidence_conf = max(evidence_conf, self.face_record.best_candidate.detection_confidence)
        if self.plate_record and self.plate_record.consensus_confidence > 0:
            evidence_conf = max(evidence_conf, self.plate_record.consensus_confidence)

        return {
            "object_type": self.class_name,
            "track_id": self.track_id,
            "target_id": self.target_id,
            "camera_id": self.camera_id,
            "consecutive_frames": self.frame_count,
            "identity_evidence_status": self.identity_evidence_status,
            "vehicle_evidence_status": self.vehicle_evidence_status,
            "watchlist_authorization_status": self.watchlist_authorization_status,
            "missing_evidence": list(self.missing_evidence),
            "evidence_confidence": evidence_conf,
            "evidence_provenance": self.provenance,
            # Informational context flags (NOT threat signals)
            "plate_text": self.plate_record.consensus_text if self.plate_record else None,
            "face_candidate": bool(self.face_record and self.face_record.best_candidate),
        }


# ── ANPR ASSOCIATION ENGINE ──────────────────────────────────────────────────

class ANPRAssociator:
    """Configurable geometric and temporal associator for ANPR candidates to vehicle tracks."""

    def __init__(
        self,
        plate_ttl_seconds: float = 5.0,
        min_consensus_observations: int = 2,
        max_spatial_jump_pixels_per_second: float = 600.0,
        min_plate_aspect_ratio: float = 1.3,
        max_plate_aspect_ratio: float = 6.5,
        min_relative_vehicle_area: float = 0.003,
        max_relative_vehicle_area: float = 0.40,
    ):
        self.plate_ttl_seconds = plate_ttl_seconds
        self.min_consensus_observations = min_consensus_observations
        self.max_spatial_jump_pixels_per_second = max_spatial_jump_pixels_per_second
        self.min_plate_aspect_ratio = min_plate_aspect_ratio
        self.max_plate_aspect_ratio = max_plate_aspect_ratio
        self.min_relative_vehicle_area = min_relative_vehicle_area
        self.max_relative_vehicle_area = max_relative_vehicle_area

    def evaluate_association(
        self,
        vehicle_track: dict[str, Any],
        plate_bbox: list[float],
        camera_id: int,
        timestamp: float,
        last_plate_candidate: Optional[PlateEvidenceCandidate] = None,
    ) -> tuple[AssociationState, float, list[str]]:
        """Evaluate whether a plate bounding box belongs to a vehicle track."""
        reasons: list[str] = []
        score = 1.0

        vx1, vy1, vx2, vy2 = vehicle_track["bbox"]
        px1, py1, px2, py2 = plate_bbox

        vw = max(1.0, vx2 - vx1)
        vh = max(1.0, vy2 - vy1)
        pw = max(1.0, px2 - px1)
        ph = max(1.0, py2 - py1)

        v_area = vw * vh
        p_area = pw * ph

        # 1. Spatial Containment & Overlap
        pcx = (px1 + px2) / 2.0
        pcy = (py1 + py2) / 2.0

        # Margin tolerance of 15% vehicle dimensions
        margin_x = vw * 0.15
        margin_y = vh * 0.15

        if not (vx1 - margin_x <= pcx <= vx2 + margin_x and vy1 - margin_y <= pcy <= vy2 + margin_y):
            return AssociationState.REJECTED, 0.0, ["Plate center is outside vehicle bounding box boundary"]

        # 2. Aspect Ratio Plausibility
        ar = pw / ph
        if not (self.min_plate_aspect_ratio <= ar <= self.max_plate_aspect_ratio):
            score *= 0.6
            reasons.append(f"Plate aspect ratio ({ar:.2f}) atypical for license plate")

        # 3. Scale / Area Plausibility
        rel_area = p_area / v_area
        if not (self.min_relative_vehicle_area <= rel_area <= self.max_relative_vehicle_area):
            score *= 0.5
            reasons.append(f"Plate relative area ({rel_area:.4f}) outside normal vehicle proportion")

        # 4. Vertical Placement Plausibility (Plates are typically in lower/middle 80% of vehicle)
        rel_y = (pcy - vy1) / vh
        if rel_y < 0.15:
            # Roof-mounted plates are unusual
            score *= 0.7
            reasons.append("Plate positioned near top vehicle roof line")

        # 5. Spatial Jump & Temporal Continuity Check
        if last_plate_candidate is not None:
            dt = max(0.001, timestamp - last_plate_candidate.timestamp)
            opx1, opy1, opx2, opy2 = last_plate_candidate.bbox
            opcx = (opx1 + opx2) / 2.0
            opcy = (opy1 + opy2) / 2.0
            dist = math.hypot(pcx - opcx, pcy - opcy)
            speed = dist / dt

            if speed > self.max_spatial_jump_pixels_per_second:
                return AssociationState.REJECTED, 0.0, [f"Impossible spatial jump ({speed:.1f} px/s > {self.max_spatial_jump_pixels_per_second}) between plate detections"]

        if score >= 0.75:
            return AssociationState.ASSOCIATED, score, ["Spatially and temporally consistent with vehicle geometry"]
        elif score >= 0.45:
            return AssociationState.UNCERTAIN, score, reasons or ["Plausible but sub-optimal vehicle-plate alignment"]
        else:
            return AssociationState.REJECTED, score, reasons or ["Plate geometry incompatible with vehicle track"]

    def update_plate_consensus(self, record: PlateTrackRecord) -> None:
        """Compute multi-frame weighted consensus across observation history."""
        if not record.candidates:
            record.consensus_text = None
            record.consensus_confidence = 0.0
            record.evidence_status = EvidenceStatus.MISSING.value
            return

        # Count weighted frequencies of normalized texts
        weighted_counts: dict[str, float] = {}
        raw_counts: dict[str, int] = {}
        total_conf_by_text: dict[str, list[float]] = {}

        for c in record.candidates:
            if not c.plate_text or c.association_state == AssociationState.REJECTED:
                continue
            txt = c.plate_text
            weight = max(0.1, c.ocr_confidence) * (1.0 if c.association_state == AssociationState.ASSOCIATED else 0.5)
            weighted_counts[txt] = weighted_counts.get(txt, 0.0) + weight
            raw_counts[txt] = raw_counts.get(txt, 0) + 1
            total_conf_by_text.setdefault(txt, []).append(c.ocr_confidence)

        if not weighted_counts:
            record.consensus_text = None
            record.consensus_confidence = 0.0
            record.evidence_status = EvidenceStatus.MISSING.value
            return

        best_text = max(weighted_counts, key=weighted_counts.get)
        count = raw_counts[best_text]
        avg_conf = sum(total_conf_by_text[best_text]) / len(total_conf_by_text[best_text])

        record.consensus_text = best_text
        record.consensus_confidence = round(avg_conf, 3)

        if count >= self.min_consensus_observations:
            record.evidence_status = EvidenceStatus.VERIFIED.value
        else:
            record.evidence_status = EvidenceStatus.CANDIDATE.value


# ── FRS ASSOCIATION ENGINE ───────────────────────────────────────────────────

class FRSAssociator:
    """Configurable anatomical and geometric associator for FRS candidates to person tracks."""

    def __init__(
        self,
        face_ttl_seconds: float = 5.0,
        default_max_relative_head_y: float = 0.55,
        uncertain_relative_head_y: float = 0.70,
        min_face_size: int = 20,
        min_detection_confidence: float = 0.55,
    ):
        self.face_ttl_seconds = face_ttl_seconds
        self.default_max_relative_head_y = default_max_relative_head_y
        self.uncertain_relative_head_y = uncertain_relative_head_y
        self.min_face_size = min_face_size
        self.min_detection_confidence = min_detection_confidence

    def evaluate_association(
        self,
        person_track: dict[str, Any],
        face_bbox: list[float],
        detection_confidence: float,
        camera_id: int,
        timestamp: float,
    ) -> tuple[AssociationState, float, list[str], bool]:
        """Evaluate whether a face candidate belongs to a person track."""
        reasons: list[str] = []
        score = 1.0
        is_low_quality = False

        px1, py1, px2, py2 = person_track["bbox"]
        fx1, fy1, fx2, fy2 = face_bbox

        pw = max(1.0, px2 - px1)
        ph = max(1.0, py2 - py1)
        fw = max(1.0, fx2 - fx1)
        fh = max(1.0, fy2 - fy1)

        # 1. Quality Check
        if fw < self.min_face_size or fh < self.min_face_size or detection_confidence < self.min_detection_confidence:
            is_low_quality = True
            reasons.append(f"Low quality face detection ({fw:.0f}x{fh:.0f}px, conf {detection_confidence:.0%})")
            score *= 0.65

        # 2. Spatial Containment
        fcx = (fx1 + fx2) / 2.0
        fcy = (fy1 + fy2) / 2.0

        margin_x = pw * 0.15
        margin_y = ph * 0.15

        if not (px1 - margin_x <= fcx <= px2 + margin_x and py1 - margin_y <= fcy <= py2 + margin_y):
            return AssociationState.REJECTED, 0.0, ["Face center falls outside person body bounding box"], is_low_quality

        # 3. Relative Anatomical Position Check (Relative Y in person box)
        rel_y = (fcy - py1) / ph

        if rel_y <= self.default_max_relative_head_y:
            # Anatomically sound: face is in upper head/torso region
            score *= 1.0
        elif rel_y <= self.uncertain_relative_head_y:
            # In mid-body region (could be seated, bending, or detection noise)
            score *= 0.65
            reasons.append(f"Face positioned in lower torso region (rel_y={rel_y:.2f})")
        else:
            # Face in lower body / legs / feet -> anatomical impossibility
            return AssociationState.REJECTED, 0.0, [f"Anatomically impossible face position in lower body/legs (rel_y={rel_y:.2f})"], is_low_quality

        # 4. Scale Plausibility (A face cannot be larger than the person's body)
        if fw > pw * 1.05 or fh > ph * 0.65:
            score *= 0.4
            reasons.append("Face bounding box abnormally large relative to person body")

        # Determine state
        if score >= 0.75 and not is_low_quality:
            return AssociationState.ASSOCIATED, score, ["Anatomically consistent with person upper body"], is_low_quality
        elif score >= 0.40:
            return AssociationState.UNCERTAIN, score, reasons or ["Plausible but uncertain person-face alignment"], is_low_quality
        else:
            return AssociationState.REJECTED, score, reasons or ["Face geometry incompatible with person track"], is_low_quality


# ── UNIFIED EVIDENCE ASSOCIATION ENGINE ──────────────────────────────────────

class EvidenceAssociationEngine:
    """Manages identity and vehicle evidence records per camera stream lifecycle."""

    def __init__(
        self,
        camera_id: int,
        anpr_associator: Optional[ANPRAssociator] = None,
        frs_associator: Optional[FRSAssociator] = None,
    ):
        self.camera_id = camera_id
        self.anpr_associator = anpr_associator or ANPRAssociator()
        self.frs_associator = frs_associator or FRSAssociator()

        self._plate_records: dict[int, PlateTrackRecord] = {}  # track_id -> PlateTrackRecord
        self._face_records: dict[int, FaceTrackRecord] = {}    # track_id -> FaceTrackRecord

    def associate_vehicle_plate(
        self,
        track_id: int,
        vehicle_track: dict[str, Any],
        raw_plate_text: str,
        ocr_confidence: float,
        plate_bbox: list[float],
        frame_id: int,
        timestamp: float,
        crop_path: Optional[str] = None,
        watchlist_match: Optional[dict[str, Any]] = None,
    ) -> Optional[PlateEvidenceCandidate]:
        """Associate a raw plate observation with a vehicle track."""
        record = self._plate_records.get(track_id)
        if record is None:
            record = PlateTrackRecord(
                track_id=track_id,
                camera_id=self.camera_id,
                first_seen=timestamp,
                last_updated=timestamp,
            )
            self._plate_records[track_id] = record

        last_cand = record.get_latest_candidate()
        state, assoc_score, reasons = self.anpr_associator.evaluate_association(
            vehicle_track=vehicle_track,
            plate_bbox=plate_bbox,
            camera_id=self.camera_id,
            timestamp=timestamp,
            last_plate_candidate=last_cand,
        )

        if state == AssociationState.REJECTED:
            logger.debug(f"Cam {self.camera_id}: Plate candidate '{raw_plate_text}' rejected for track {track_id}: {reasons}")
            return None

        # Compute relative bbox
        vx1, vy1, vx2, vy2 = vehicle_track["bbox"]
        vw = max(1.0, vx2 - vx1)
        vh = max(1.0, vy2 - vy1)
        rel_bbox = [
            round((plate_bbox[0] - vx1) / vw, 3),
            round((plate_bbox[1] - vy1) / vh, 3),
            round((plate_bbox[2] - vx1) / vw, 3),
            round((plate_bbox[3] - vy1) / vh, 3),
        ]

        from backend.app.services.anpr import INDIAN_PLATE_PATTERN, normalize_indian_plate
        norm_text = normalize_indian_plate(raw_plate_text)
        is_valid = bool(INDIAN_PLATE_PATTERN.match(norm_text))

        candidate = PlateEvidenceCandidate(
            candidate_id=f"PLATE-{self.camera_id}-{track_id}-{frame_id}",
            plate_text=norm_text or raw_plate_text,
            ocr_confidence=ocr_confidence,
            bbox=plate_bbox,
            relative_bbox=rel_bbox,
            frame_id=frame_id,
            timestamp=timestamp,
            camera_id=self.camera_id,
            crop_path=crop_path,
            is_valid_format=is_valid,
            association_state=state,
            association_score=round(assoc_score, 3),
            provenance={
                "raw_text": raw_plate_text,
                "association_reasons": reasons,
                "watchlist_match": watchlist_match,
                "model": "Contour / Neural ANPR OCR",
            },
        )

        record.candidates.append(candidate)
        record.observation_count += 1
        record.last_updated = timestamp
        record.is_stale = False

        self.anpr_associator.update_plate_consensus(record)
        return candidate

    def associate_person_face(
        self,
        track_id: int,
        person_track: dict[str, Any],
        face_bbox: list[float],
        detection_confidence: float,
        frame_id: int,
        timestamp: float,
        similarity_score: Optional[float] = None,
        matched_subject_id: Optional[int] = None,
        matched_subject_name: Optional[str] = None,
        crop_path: Optional[str] = None,
        matching_threshold: float = 0.50,
        model_provenance: str = "OpenCV YuNet + SFace 128D",
    ) -> Optional[FaceEvidenceCandidate]:
        """Associate a raw face observation with a person track."""
        record = self._face_records.get(track_id)
        if record is None:
            record = FaceTrackRecord(
                track_id=track_id,
                camera_id=self.camera_id,
                first_seen=timestamp,
                last_updated=timestamp,
            )
            self._face_records[track_id] = record

        state, assoc_score, reasons, is_low_qual = self.frs_associator.evaluate_association(
            person_track=person_track,
            face_bbox=face_bbox,
            detection_confidence=detection_confidence,
            camera_id=self.camera_id,
            timestamp=timestamp,
        )

        if state == AssociationState.REJECTED:
            logger.debug(f"Cam {self.camera_id}: Face candidate rejected for person track {track_id}: {reasons}")
            return None

        # Compute relative bbox
        px1, py1, px2, py2 = person_track["bbox"]
        pw = max(1.0, px2 - px1)
        ph = max(1.0, py2 - py1)
        rel_bbox = [
            round((face_bbox[0] - px1) / pw, 3),
            round((face_bbox[1] - py1) / ph, 3),
            round((face_bbox[2] - px1) / pw, 3),
            round((face_bbox[3] - py1) / ph, 3),
        ]

        candidate = FaceEvidenceCandidate(
            candidate_id=f"FACE-{self.camera_id}-{track_id}-{frame_id}",
            bbox=face_bbox,
            relative_bbox=rel_bbox,
            detection_confidence=detection_confidence,
            similarity_score=similarity_score,
            matched_subject_id=matched_subject_id,
            matched_subject_name=matched_subject_name,
            frame_id=frame_id,
            timestamp=timestamp,
            camera_id=self.camera_id,
            crop_path=crop_path,
            matching_threshold=matching_threshold,
            model_provenance=model_provenance,
            is_low_quality=is_low_qual,
            association_state=state,
            association_score=round(assoc_score, 3),
            provenance={
                "association_reasons": reasons,
                "is_low_quality": is_low_qual,
                "matching_threshold": matching_threshold,
            },
        )

        record.candidates.append(candidate)
        record.observation_count += 1
        record.last_updated = timestamp
        record.is_stale = False

        # Update best candidate
        if record.best_candidate is None:
            record.best_candidate = candidate
        else:
            # Prefer higher similarity or higher detection confidence
            cand_sim = candidate.similarity_score or 0.0
            best_sim = record.best_candidate.similarity_score or 0.0
            if cand_sim > best_sim or (cand_sim == best_sim and candidate.detection_confidence > record.best_candidate.detection_confidence):
                record.best_candidate = candidate

        # Update status
        if matched_subject_id is not None:
            record.watchlist_authorization_status = WatchlistAuthStatus.FLAGGED.value
            record.evidence_status = EvidenceStatus.VERIFIED.value if record.observation_count >= 2 else EvidenceStatus.CANDIDATE.value
        else:
            record.watchlist_authorization_status = WatchlistAuthStatus.UNKNOWN.value
            record.evidence_status = EvidenceStatus.CANDIDATE.value

        return candidate

    def build_unified_evidence(
        self,
        track: dict[str, Any],
        camera_id: int,
        timestamp: float,
    ) -> UnifiedTrackEvidence:
        """Assemble a UnifiedTrackEvidence object for an active track."""
        track_id = track["track_id"]
        cls = track.get("class_name", "unknown")

        plate_rec = self._plate_records.get(track_id)
        face_rec = self._face_records.get(track_id)

        # Evaluate staleness
        if plate_rec and (timestamp - plate_rec.last_updated > self.anpr_associator.plate_ttl_seconds):
            plate_rec.is_stale = True

        if face_rec and (timestamp - face_rec.last_updated > self.frs_associator.face_ttl_seconds):
            face_rec.is_stale = True

        # Compile evidence statuses
        missing_evidence: list[str] = []

        if cls in ("car", "truck", "bus", "motorcycle"):
            if plate_rec and plate_rec.consensus_text and not plate_rec.is_stale:
                veh_status = plate_rec.evidence_status
            else:
                veh_status = EvidenceStatus.MISSING.value
                missing_evidence.append("Vehicle license plate read unacquired or expired (ANPR unavailable)")
            id_status = EvidenceStatus.MISSING.value
            watchlist_status = WatchlistAuthStatus.UNKNOWN.value
        elif cls == "person":
            veh_status = EvidenceStatus.MISSING.value
            if face_rec and face_rec.best_candidate and not face_rec.is_stale:
                id_status = face_rec.evidence_status
                watchlist_status = face_rec.watchlist_authorization_status
                if watchlist_status == WatchlistAuthStatus.UNKNOWN.value:
                    missing_evidence.append("Face candidate not enrolled in watchlist database")
            else:
                id_status = EvidenceStatus.MISSING.value
                watchlist_status = WatchlistAuthStatus.UNCHECKED.value
                missing_evidence.append("Facial recognition identity evidence unacquired (sub-threshold or occluded)")
        else:
            veh_status = EvidenceStatus.MISSING.value
            id_status = EvidenceStatus.MISSING.value
            watchlist_status = WatchlistAuthStatus.UNKNOWN.value

        return UnifiedTrackEvidence(
            camera_id=camera_id,
            track_id=track_id,
            target_id=track.get("target_id"),
            class_name=cls,
            track_bbox=list(track.get("bbox", [0, 0, 0, 0])),
            ground_anchor=list(track.get("ground_anchor", [0, 0])),
            first_seen_ts=timestamp - float(track.get("dwell_time", 0.0)),
            last_seen_ts=timestamp,
            frame_count=int(track.get("hits") or track.get("hit_count") or 1),
            plate_record=plate_rec,
            face_record=face_rec,
            zone_events=list(track.get("current_zone_events", [])),
            identity_evidence_status=id_status,
            vehicle_evidence_status=veh_status,
            watchlist_authorization_status=watchlist_status,
            missing_evidence=missing_evidence,
            provenance={
                "camera_id": camera_id,
                "track_id": track_id,
                "dwell_time": float(track.get("dwell_time", 0.0)),
                "timestamp": timestamp,
            },
        )

    def purge_expired(self, current_time: float) -> None:
        """Purge stale records whose tracks have expired or exceed TTL."""
        expired_plates = [
            tid for tid, rec in self._plate_records.items()
            if (current_time - rec.last_updated) > (self.anpr_associator.plate_ttl_seconds * 3)
        ]
        for tid in expired_plates:
            del self._plate_records[tid]

        expired_faces = [
            tid for tid, rec in self._face_records.items()
            if (current_time - rec.last_updated) > (self.frs_associator.face_ttl_seconds * 3)
        ]
        for tid in expired_faces:
            del self._face_records[tid]

    def reset(self) -> None:
        """Reset association engine state on camera pipeline restart."""
        self._plate_records.clear()
        self._face_records.clear()
