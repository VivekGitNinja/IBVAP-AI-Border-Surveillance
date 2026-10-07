"""
IBVAP — Edge Behavioral Kinematics Engine
==========================================
Computes physical and normalized kinematic metrics from ByteTrack Kalman tracking history.
Provides perimeter convergence, circular-difference trajectory irregularity, and multi-criteria loitering.
Guarantees explicit calibration status provenance and neutral, non-autonomous explainability language.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class CalibrationStatus(str, Enum):
    """Camera ground-plane calibration status."""
    UNCALIBRATED_IMAGE_SPACE = "UNCALIBRATED_IMAGE_SPACE"
    CALIBRATED_HOMOGRAPHY = "CALIBRATED_HOMOGRAPHY"


class CoordinateSpace(str, Enum):
    """Measurement coordinate space."""
    NORMALIZED_IMAGE_PLANE = "NORMALIZED_IMAGE_PLANE"  # [0.0, 1.0] x [0.0, 1.0]
    GROUND_METRIC_METERS = "GROUND_METRIC_METERS"      # (X, Y) in meters on ground plane


class MeasurementQuality(str, Enum):
    """Kinematic measurement confidence/quality category."""
    HIGH = "HIGH"          # Calibrated homography, >=5 samples, dt >= 0.5s
    MEDIUM = "MEDIUM"      # Uncalibrated image-space, >=5 samples, dt >= 0.5s
    LOW = "LOW"            # <5 samples, dt < 0.5s (insufficient history)
    DEGRADED = "DEGRADED"  # Jitter / detector flicker detected


@dataclass
class CameraCalibrationProfile:
    """Camera calibration configuration and operating baselines."""
    camera_id: int
    status: CalibrationStatus = CalibrationStatus.UNCALIBRATED_IMAGE_SPACE
    homography_matrix: Optional[List[List[float]]] = None  # 3x3 perspective homography
    elevation_angle_deg: Optional[float] = None
    mounting_height_m: Optional[float] = None

    # Calibrated physical baselines (meters / second)
    person_nominal_speed_mps: float = 1.4
    person_burst_speed_mps: float = 4.2
    vehicle_nominal_speed_mps: float = 8.3
    vehicle_burst_speed_mps: float = 20.0

    # Image-space apparent velocity baselines (normalized units / second)
    person_norm_speed_nominal: float = 0.02
    person_norm_speed_burst: float = 0.08
    vehicle_norm_speed_nominal: float = 0.06
    vehicle_norm_speed_burst: float = 0.20


@dataclass
class KinematicMeasurement:
    """Observable kinematic properties derived from target tracking history."""
    speed: float = 0.0
    velocity: Tuple[float, float] = (0.0, 0.0)
    perimeter_convergence: float = 0.0          # [-1.0, 1.0]
    trajectory_irregularity_index: float = 0.0  # [0.0, 1.0]
    loitering_index: float = 0.0                # [0.0, 1.0]
    direction_classification: str = "parallel"  # "inward", "parallel", "outward"

    # Explicit Provenance & Metadata
    camera_calibration_status: CalibrationStatus = CalibrationStatus.UNCALIBRATED_IMAGE_SPACE
    coordinate_space: CoordinateSpace = CoordinateSpace.NORMALIZED_IMAGE_PLANE
    measurement_window_seconds: float = 0.0
    valid_sample_count: int = 0
    measurement_quality: MeasurementQuality = MeasurementQuality.LOW
    explainability_notes: List[str] = field(default_factory=list)

    def speed_anomaly_factor(self, object_type: str = "person") -> float:
        """Calculate normalized speed anomaly factor in [0.0, 1.0]."""
        if self.measurement_quality == MeasurementQuality.LOW:
            return 0.0

        if self.coordinate_space == CoordinateSpace.GROUND_METRIC_METERS:
            nominal = 8.3 if object_type == "vehicle" else 1.4
            burst = 20.0 if object_type == "vehicle" else 4.2
        else:
            nominal = 0.06 if object_type == "vehicle" else 0.02
            burst = 0.20 if object_type == "vehicle" else 0.08

        if self.speed <= nominal:
            return 0.0
        if self.speed >= burst:
            return 1.0
        return (self.speed - nominal) / max(1e-5, (burst - nominal))

    @property
    def quality(self) -> MeasurementQuality:
        """Alias for measurement_quality."""
        return self.measurement_quality

    @property
    def is_loitering(self) -> bool:
        """True if loitering index indicates confirmed loitering."""
        return self.loitering_index >= 0.5

    def to_rps_signals(self, object_type: str = "person") -> Dict[str, float]:
        """Convert kinematic measurements to bounded RPS scoring signals."""
        anomaly_val = min(1.0, 0.6 * self.speed_anomaly_factor(object_type) + 0.4 * self.trajectory_irregularity_index)
        return {
            "behavior_anomaly": anomaly_val,
            "loitering": self.loitering_index,
            "perimeter_convergence": self.perimeter_convergence,
        }


class KinematicBehaviorEngine:
    """Derives physical and normalized observable kinematic metrics from ByteTrack history."""

    def __init__(
        self,
        calibration_profile: Optional[CameraCalibrationProfile] = None,
        profile: Optional[CameraCalibrationProfile] = None,
        camera_id: int = 0,
    ):
        self.profile = profile or calibration_profile or CameraCalibrationProfile(camera_id=camera_id)
        # History per track_id: list of (x, y, timestamp)
        self._history: Dict[int, List[Tuple[float, float, float]]] = {}
        # Bounding box history per track_id: list of (x1, y1, x2, y2)
        self._bboxes: Dict[int, List[Tuple[float, float, float, float]]] = {}
        self.window_seconds = 3.0
        self.min_samples = 5
        self.min_time_delta = 0.5
        # Subpixel jitter / flicker noise threshold (normalized plane)
        self.jitter_threshold_norm = 0.003
        self.jitter_threshold_meters = 0.15

    def clear_track(self, track_id: int) -> None:
        """Clear tracking history for a completed or lost tracklet."""
        self._history.pop(track_id, None)
        self._bboxes.pop(track_id, None)

    def apply_homography(self, x: float, y: float) -> Tuple[float, float]:
        """Transform normalized image plane point (x, y) to ground metric meters (X, Y)."""
        H = self.profile.homography_matrix
        if not H or len(H) != 3 or len(H[0]) != 3:
            return (x, y)

        denom = H[2][0] * x + H[2][1] * y + H[2][2]
        if abs(denom) < 1e-9:
            return (x, y)

        X = (H[0][0] * x + H[0][1] * y + H[0][2]) / denom
        Y = (H[1][0] * x + H[1][1] * y + H[1][2]) / denom
        return (float(X), float(Y))

    def update_track(
        self,
        track_id: int,
        bbox: Tuple[float, float, float, float],
        timestamp: float,
        boundary_context: Optional[Any] = None,
        object_type: str = "person",
    ) -> KinematicMeasurement:
        """Update tracklet trajectory and derive instantaneous kinematic measurements."""
        x1, y1, x2, y2 = bbox
        # Ground contact anchor: horizontal center, bottom edge
        anchor_x = (x1 + x2) / 2.0
        anchor_y = y2

        if track_id not in self._history:
            self._history[track_id] = []
            self._bboxes[track_id] = []

        self._history[track_id].append((anchor_x, anchor_y, timestamp))
        self._bboxes[track_id].append(bbox)

        # Prune history to sliding window + buffer
        cutoff_time = timestamp - max(self.window_seconds * 2.0, 60.0)
        valid_indices = [i for i, (_, _, t) in enumerate(self._history[track_id]) if t >= cutoff_time]
        if valid_indices:
            start_idx = valid_indices[0]
            self._history[track_id] = self._history[track_id][start_idx:]
            self._bboxes[track_id] = self._bboxes[track_id][start_idx:]

        hist = self._history[track_id]
        total_samples = len(hist)

        # Gating: Insufficient samples or duration
        dt_total = hist[-1][2] - hist[0][2]
        if total_samples < self.min_samples or dt_total < self.min_time_delta:
            return KinematicMeasurement(
                speed=0.0,
                velocity=(0.0, 0.0),
                perimeter_convergence=0.0,
                trajectory_irregularity_index=0.0,
                loitering_index=0.0,
                direction_classification="parallel",
                camera_calibration_status=self.profile.status,
                coordinate_space=(
                    CoordinateSpace.GROUND_METRIC_METERS
                    if self.profile.status == CalibrationStatus.CALIBRATED_HOMOGRAPHY
                    else CoordinateSpace.NORMALIZED_IMAGE_PLANE
                ),
                measurement_window_seconds=dt_total,
                valid_sample_count=total_samples,
                measurement_quality=MeasurementQuality.LOW,
                explainability_notes=["Insufficient trajectory history (<5 samples or <0.5s)"],
            )

        # Consider points within window_seconds for instantaneous velocity & irregularity
        window_cutoff = timestamp - self.window_seconds
        w_hist = [p for p in hist if p[2] >= window_cutoff]
        if len(w_hist) < 2:
            w_hist = hist[-2:]

        is_calibrated = (
            self.profile.status == CalibrationStatus.CALIBRATED_HOMOGRAPHY
            and self.profile.homography_matrix is not None
        )
        coord_space = CoordinateSpace.GROUND_METRIC_METERS if is_calibrated else CoordinateSpace.NORMALIZED_IMAGE_PLANE

        # Transform coordinates according to calibration status
        transformed_pts: List[Tuple[float, float, float]] = []
        for x, y, t in w_hist:
            if is_calibrated:
                gx, gy = self.apply_homography(x, y)
                transformed_pts.append((gx, gy, t))
            else:
                transformed_pts.append((x, y, t))

        # Instantaneous velocity & speed over the measurement window
        p_start = transformed_pts[0]
        p_end = transformed_pts[-1]
        dt_window = max(1e-5, p_end[2] - p_start[2])
        dx = p_end[0] - p_start[0]
        dy = p_end[1] - p_start[1]
        displacement = math.hypot(dx, dy)

        # Noise / subpixel jitter filter
        noise_threshold = self.jitter_threshold_meters if is_calibrated else self.jitter_threshold_norm
        if displacement < noise_threshold:
            vx, vy = 0.0, 0.0
            speed = 0.0
            is_jitter = True
        else:
            vx = dx / dt_window
            vy = dy / dt_window
            speed = displacement / dt_window
            is_jitter = False

        notes: List[str] = []
        if is_calibrated:
            notes.append(f"Calibrated ground metric velocity: {speed:.2f} m/s")
        else:
            notes.append(f"Uncalibrated apparent image-space velocity: {speed:.4f} norm/s (physical m/s uncalibrated)")

        # 1. Circular-Difference Trajectory Irregularity Calculation
        if is_jitter or len(transformed_pts) < 3:
            irregularity_index = 0.0
        else:
            angular_diffs: List[float] = []
            prev_heading: Optional[float] = None

            for i in range(1, len(transformed_pts)):
                seg_dx = transformed_pts[i][0] - transformed_pts[i - 1][0]
                seg_dy = transformed_pts[i][1] - transformed_pts[i - 1][1]
                seg_disp = math.hypot(seg_dx, seg_dy)

                # Ignore segments with negligible displacement
                if seg_disp < (noise_threshold / 2.0):
                    continue

                curr_heading = math.atan2(seg_dy, seg_dx)
                if prev_heading is not None:
                    # Circular difference safe angle subtraction
                    d_theta = math.atan2(
                        math.sin(curr_heading - prev_heading),
                        math.cos(curr_heading - prev_heading),
                    )
                    angular_diffs.append(abs(d_theta))
                prev_heading = curr_heading

            if angular_diffs:
                mean_turn_rad = sum(angular_diffs) / len(angular_diffs)
                irregularity_index = round(min(1.0, mean_turn_rad / (math.pi / 2.0)), 2)
            else:
                irregularity_index = 0.0

        if irregularity_index > 0.4:
            notes.append(f"Observable kinematic irregularity index: {irregularity_index:.2f}")

        # 2. Perimeter Convergence Calculation
        perimeter_convergence = 0.0
        direction_class = "parallel"

        if boundary_context is not None and not is_jitter:
            nx, ny = getattr(boundary_context, "inward_normal", (0.0, 0.0))
            v_norm = math.hypot(vx, vy)
            if v_norm > 1e-6:
                v_hat_x = vx / v_norm
                v_hat_y = vy / v_norm
                # Dot product with inward boundary normal
                dot = (v_hat_x * nx) + (v_hat_y * ny)
                perimeter_convergence = round(max(-1.0, min(1.0, dot)), 2)

                if perimeter_convergence > 0.35:
                    direction_class = "inward"
                    notes.append(f"Inward perimeter convergence toward {getattr(boundary_context, 'zone_name', 'zone')} ({perimeter_convergence:+.2f})")
                elif perimeter_convergence < -0.35:
                    direction_class = "outward"
                    notes.append(f"Outward perimeter departure from {getattr(boundary_context, 'zone_name', 'zone')} ({perimeter_convergence:+.2f})")
                else:
                    direction_class = "parallel"

        # 3. Multi-Criteria Loitering Calculation
        loitering_index = 0.0
        dwell_duration = hist[-1][2] - hist[0][2]

        if dwell_duration >= 10.0 and len(hist) >= 5:
            all_pts = [
                self.apply_homography(p[0], p[1]) if is_calibrated else (p[0], p[1])
                for p in hist
            ]
            cx = sum(p[0] for p in all_pts) / len(all_pts)
            cy = sum(p[1] for p in all_pts) / len(all_pts)
            max_radius = max(math.hypot(p[0] - cx, p[1] - cy) for p in all_pts)

            cum_path = sum(
                math.hypot(all_pts[i][0] - all_pts[i - 1][0], all_pts[i][1] - all_pts[i - 1][1])
                for i in range(1, len(all_pts))
            )
            net_displacement = math.hypot(all_pts[-1][0] - all_pts[0][0], all_pts[-1][1] - all_pts[0][1])
            net_disp_ratio = net_displacement / max(1e-5, cum_path)

            if is_calibrated:
                max_allowed_radius = 12.0
            else:
                max_allowed_radius = 50.0 if cx > 1.0 else 0.10

            if max_radius <= max_allowed_radius and net_disp_ratio <= 0.35:
                containment_factor = 1.0 - (max_radius / max_allowed_radius)
                duration_factor = min(1.0, (dwell_duration - 10.0) / 20.0)
                loitering_index = round(min(1.0, 0.5 + 0.3 * containment_factor + 0.2 * duration_factor), 2)
                notes.append(f"Spatial loitering identified (dwell={dwell_duration:.1f}s, radius={max_radius:.2f}, net_disp_ratio={net_disp_ratio:.2f})")

        quality = MeasurementQuality.HIGH if is_calibrated else MeasurementQuality.MEDIUM
        if is_jitter:
            quality = MeasurementQuality.DEGRADED

        return KinematicMeasurement(
            speed=round(speed, 3),
            velocity=(round(vx, 3), round(vy, 3)),
            perimeter_convergence=perimeter_convergence,
            trajectory_irregularity_index=irregularity_index,
            loitering_index=loitering_index,
            direction_classification=direction_class,
            camera_calibration_status=self.profile.status,
            coordinate_space=coord_space,
            measurement_window_seconds=round(dt_window, 2),
            valid_sample_count=len(transformed_pts),
            measurement_quality=quality,
            explainability_notes=notes,
        )
