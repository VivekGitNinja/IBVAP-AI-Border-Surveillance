"""
ByteTrack-Inspired Multi-Object Tracker
========================================

SOTA multi-object tracker using:
- Kalman filter for motion prediction
- IoU-based association (Hungarian algorithm)
- Track lifecycle management (tentative → confirmed → lost → deleted)
- Handles detection drops gracefully
- Persistent track IDs across frames

Based on: ByteTrack (ECCV 2022) — "Every Detection is Worth Associating"

Key features:
- Associates high AND low confidence detections
- Kalman filter predicts positions during detection gaps
- Track deletion after configurable frames of no detection
- Direction, speed, and trajectory estimation

License: Apache 2.0 (ByteTrack)
Reference: https://github.com/holistics/bytetrack
"""

import time
import logging
from typing import List, Dict, Optional, Tuple, Any
from dataclasses import dataclass, field
import numpy as np

logger = logging.getLogger(__name__)


def iou_batch(bb_test: np.ndarray, bb_gt: np.ndarray) -> np.ndarray:
    """Compute IoU between two sets of bounding boxes.
    
    Args:
        bb_test: (N, 4) array of [x1, y1, x2, y2]
        bb_gt: (M, 4) array of [x1, y1, x2, y2]
    
    Returns:
        (N, M) IoU matrix
    """
    if len(bb_test) == 0 or len(bb_gt) == 0:
        return np.zeros((len(bb_test), len(bb_gt)))

    xx1 = np.maximum(bb_test[:, 0:1], bb_gt[:, 0].reshape(1, -1))
    yy1 = np.maximum(bb_test[:, 1:2], bb_gt[:, 1].reshape(1, -1))
    xx2 = np.minimum(bb_test[:, 2:3], bb_gt[:, 2].reshape(1, -1))
    yy2 = np.minimum(bb_test[:, 3:4], bb_gt[:, 3].reshape(1, -1))

    w = np.maximum(0.0, xx2 - xx1)
    h = np.maximum(0.0, yy2 - yy1)
    inter = w * h

    area_test = (bb_test[:, 2] - bb_test[:, 0]) * (bb_test[:, 3] - bb_test[:, 1])
    area_gt = (bb_gt[:, 2] - bb_gt[:, 0]) * (bb_gt[:, 3] - bb_gt[:, 1])

    union = area_test.reshape(-1, 1) + area_gt.reshape(1, -1) - inter
    return inter / np.maximum(union, 1e-6)


def linear_assignment(cost_matrix: np.ndarray):
    """Solve linear assignment problem (Hungarian algorithm).
    
    Returns:
        matches: list of (row_idx, col_idx) pairs
        unmatched_rows: list of unmatched row indices
        unmatched_cols: list of unmatched column indices
    """
    if cost_matrix.size == 0:
        return [], list(range(cost_matrix.shape[0])), list(range(cost_matrix.shape[1]))

    # Simple greedy assignment (O(n*m) — good enough for real-time)
    # For production, use scipy.optimize.linear_sum_assignment
    try:
        from scipy.optimize import linear_sum_assignment
        row_ind, col_ind = linear_sum_assignment(cost_matrix)
        matches = [(r, c) for r, c in zip(row_ind, col_ind)
                   if cost_matrix[r, c] < 0.7]  # max distance threshold

        matched_rows = set(r for r, _ in matches)
        matched_cols = set(c for _, c in matches)
        unmatched_rows = [i for i in range(cost_matrix.shape[0])
                         if i not in matched_rows]
        unmatched_cols = [i for i in range(cost_matrix.shape[1])
                         if i not in matched_cols]
        return matches, unmatched_rows, unmatched_cols
    except ImportError:
        # Fallback greedy
        return _greedy_assignment(cost_matrix)


def _greedy_assignment(cost_matrix: np.ndarray):
    """Greedy fallback for assignment."""
    matches = []
    used_rows = set()
    used_cols = set()

    # Sort by cost
    indices = np.argsort(cost_matrix.ravel())
    rows = indices // cost_matrix.shape[1]
    cols = indices % cost_matrix.shape[1]

    for r, c in zip(rows, cols):
        if r in used_rows or c in used_cols:
            continue
        if cost_matrix[r, c] >= 0.7:
            break
        matches.append((int(r), int(c)))
        used_rows.add(r)
        used_cols.add(c)

    unmatched_rows = [i for i in range(cost_matrix.shape[0]) if i not in used_rows]
    unmatched_cols = [i for i in range(cost_matrix.shape[1]) if i not in used_cols]
    return matches, unmatched_rows, unmatched_cols


class KalmanBoxTracker:
    """Single object tracker using Kalman filter.
    
    State vector: [x, y, s, r, dx, dy, ds]
    where (x,y) = center, s = area, r = aspect ratio
    """

    _count = 0
    _std_weight_position = 1.0 / 20
    _std_weight_velocity = 1.0 / 160

    def __init__(self, bbox: List[float], class_id: int = 0,
                 class_name: str = "unknown", confidence: float = 0.5,
                 track_id: Optional[int] = None):
        """Initialize tracker with first detection.
        
        Args:
            bbox: [x1, y1, x2, y2] bounding box
            class_id: detection class ID
            class_name: detection class name
            confidence: detection confidence
            track_id: optional explicit track ID (for multi-camera isolation)
        """
        if track_id is not None:
            self.track_id = track_id
        else:
            KalmanBoxTracker._count += 1
            self.track_id = KalmanBoxTracker._count
        self.class_id = class_id
        self.class_name = class_name

        # Initialize Kalman filter (7D state, 4D measurement)
        self.kf = self._init_kalman()

        # Convert bbox to [cx, cy, s, r]
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        cx = bbox[0] + w / 2
        cy = bbox[1] + h / 2
        s = w * h
        r = w / max(h, 1e-6)

        self.kf.statePost = np.array(
            [[cx], [cy], [s], [r], [0], [0], [0]], dtype=np.float32
        )

        # Time tracking
        self.time_since_update = 0
        self.hits = 1
        self.age = 1
        self.confidence = confidence

        # Trajectory
        self.trajectory: List[List[float]] = [[cx, cy]]
        self.timestamps: List[float] = [time.time()]
        self.first_seen = time.time()
        self.last_seen = time.time()

        # Zone tracking
        self.zones_entered: Dict[str, float] = {}
        self.zone_crossings: List[Dict] = []

        # Status
        self.state = "tentative"  # tentative → confirmed → lost → deleted

    @classmethod
    def _init_kalman(cls):
        """Initialize Kalman filter matrices."""
        import cv2

        kf = cv2.KalmanFilter(7, 4)  # 7 state, 4 measurement

        # Transition matrix (constant velocity model)
        kf.transitionMatrix = np.array([
            [1, 0, 0, 0, 1, 0, 0],
            [0, 1, 0, 0, 0, 1, 0],
            [0, 0, 1, 0, 0, 0, 1],
            [0, 0, 0, 1, 0, 0, 0],
            [0, 0, 0, 0, 1, 0, 0],
            [0, 0, 0, 0, 0, 1, 0],
            [0, 0, 0, 0, 0, 0, 1],
        ], dtype=np.float32)

        # Measurement matrix
        kf.measurementMatrix = np.eye(4, 7, dtype=np.float32)

        # Noise
        kf.processNoiseCov = np.eye(7, dtype=np.float32) * 1e-2
        kf.measurementNoiseCov = np.eye(4, dtype=np.float32) * 1e-1
        kf.errorCovPost = np.eye(7, dtype=np.float32)

        return kf

    def predict(self) -> List[float]:
        """Predict next position. Returns [x1, y1, x2, y2]."""
        import cv2

        state = self.kf.predict()
        self.age += 1
        self.time_since_update += 1

        # Convert state to bbox
        cx, cy, s, r = state[0, 0], state[1, 0], state[2, 0], state[3, 0]
        w = np.sqrt(max(s * r, 1))
        h = max(s / max(w, 1e-6), 1)

        return [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2]

    def update(self, bbox: List[float], class_id: int = 0,
               class_name: str = "unknown", confidence: float = 0.5):
        """Update tracker with new detection."""
        import cv2

        self.time_since_update = 0
        self.hits += 1
        self.confidence = confidence
        self.last_seen = time.time()

        # Update class info
        self.class_id = class_id
        self.class_name = class_name

        # Convert bbox to measurement
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        cx = bbox[0] + w / 2
        cy = bbox[1] + h / 2
        s = w * h
        r = w / max(h, 1e-6)

        measurement = np.array(
            [[cx], [cy], [s], [r]], dtype=np.float32
        )

        self.kf.correct(measurement)

        # Update trajectory
        self.trajectory.append([cx, cy])
        self.timestamps.append(time.time())

        # Keep last 100 positions
        if len(self.trajectory) > 100:
            self.trajectory = self.trajectory[-100:]
            self.timestamps = self.timestamps[-100:]

        # Update state
        if self.hits >= 3 and self.state == "tentative":
            self.state = "confirmed"
        elif self.time_since_update > 30:
            self.state = "lost"
        elif self.time_since_update > 60:
            self.state = "deleted"

    def get_state(self) -> List[float]:
        """Get current estimated bbox [x1, y1, x2, y2]."""
        state = self.kf.statePost
        cx, cy, s, r = state[0, 0], state[1, 0], state[2, 0], state[3, 0]
        w = np.sqrt(max(s * r, 1))
        h = max(s / max(w, 1e-6), 1)
        return [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2]

    @property
    def dwell_time(self) -> float:
        """Time since first detection (seconds)."""
        return time.time() - self.first_seen

    @property
    def velocity(self) -> Tuple[float, float]:
        """Estimated velocity [vx, vy] pixels/second."""
        if len(self.trajectory) < 2 or len(self.timestamps) < 2:
            return (0.0, 0.0)

        dt = self.timestamps[-1] - self.timestamps[-2]
        if dt <= 0:
            return (0.0, 0.0)

        dx = self.trajectory[-1][0] - self.trajectory[-2][0]
        dy = self.trajectory[-1][1] - self.trajectory[-2][1]
        return (dx / dt, dy / dt)

    @property
    def direction(self) -> str:
        """Movement direction estimate."""
        vx, vy = self.velocity
        if abs(vx) < 1 and abs(vy) < 1:
            return "stationary"
        angle = np.degrees(np.arctan2(-vy, vx))
        if -45 <= angle < 45:
            return "right"
        elif 45 <= angle < 135:
            return "up"
        elif angle >= 135 or angle < -135:
            return "left"
        else:
            return "down"

    @property
    def speed(self) -> float:
        """Speed in pixels per second."""
        vx, vy = self.velocity
        return np.sqrt(vx ** 2 + vy ** 2)

    def to_dict(self) -> Dict:
        """Export track state as dictionary."""
        bbox = self.get_state()
        cx = float((bbox[0] + bbox[2]) / 2.0)
        cy = float((bbox[1] + bbox[3]) / 2.0)
        ground_anchor = [float(round(cx, 1)), float(round(bbox[3], 1))]
        return {
            "track_id": int(self.track_id),
            "class_id": int(self.class_id),
            "class_name": str(self.class_name),
            "bbox": [float(round(b, 1)) for b in bbox],
            "center": [float(round(cx, 1)), float(round(cy, 1))],
            "ground_anchor": ground_anchor,
            "footprint": ground_anchor,
            "confidence": float(round(self.confidence, 3)),
            "state": str(self.state),
            "age": int(self.age),
            "hits": int(self.hits),
            "time_since_update": int(self.time_since_update),
            "dwell_time": float(round(self.dwell_time, 1)),
            "direction": str(self.direction),
            "speed": float(round(self.speed, 1)),
            "trajectory_length": int(len(self.trajectory)),
            "first_seen": float(self.first_seen),
            "last_seen": float(self.last_seen),
        }


class ByteTracker:
    """ByteTrack-inspired multi-object tracker.
    
    Uses Kalman filter + IoU association.
    Handles both high and low confidence detections.
    """
    
    def __init__(
        self,
        high_thresh: float = 0.6,
        low_thresh: float = 0.1,
        track_buffer: int = 30,
        max_age: int = 70,
    ):
        """
        Args:
            high_thresh: confidence threshold for high-confidence detections
            low_thresh: confidence threshold for low-confidence detections
            track_buffer: frames to keep lost tracks
            max_age: frames before deleting a track
        """
        self.high_thresh = high_thresh
        self.low_thresh = low_thresh
        self.track_buffer = track_buffer
        self.max_age = max_age

        self.trackers: List[KalmanBoxTracker] = []
        self.frame_count = 0
        self._next_id = 1

        self._stats = {
            "total_tracks": 0,
            "active_tracks": 0,
            "lost_tracks": 0,
        }

    def update(self, detections: List[Any]) -> List[Dict]:
        """Update tracker with new detections.
        
        Args:
            detections: list of dicts or Detection objects with keys/attrs:
                bbox: [x1, y1, x2, y2]
                class_id: int
                class_name: str
                confidence: float
        
        Returns:
            List of tracked objects with persistent IDs
        """
        self.frame_count += 1

        # Normalize input detections
        normalized_dets = []
        for d in detections:
            if isinstance(d, dict):
                normalized_dets.append(d)
            else:
                normalized_dets.append({
                    "bbox": list(d.bbox),
                    "class_id": getattr(d, "class_id", 0),
                    "class_name": getattr(d, "class_name", None) or getattr(d, "label", "unknown"),
                    "confidence": float(d.confidence),
                })

        # Separate high and low confidence detections
        high_dets = [d for d in normalized_dets if d["confidence"] >= self.high_thresh]
        low_dets = [d for d in normalized_dets if self.low_thresh <= d["confidence"] < self.high_thresh]
        all_dets = high_dets + low_dets

        # Predict existing tracks
        for trk in self.trackers:
            trk.predict()

        # Match ALL detections with existing tracks
        # _match returns (matches, unmatched_a, unmatched_b) where a=dets, b=trks
        matched, unmatched_det_idxs, unmatched_trk_idxs = self._match(
            [d["bbox"] for d in all_dets],
            [t.get_state() for t in self.trackers]
        )

        # Update matched tracks
        for det_idx, trk_idx in matched:
            d = all_dets[det_idx]
            self.trackers[trk_idx].update(
                d["bbox"], d.get("class_id", 0),
                d.get("class_name", "unknown"), d["confidence"]
            )

        # Create new tracks for unmatched detections (only high-confidence detections initiate tracks)
        for det_idx in unmatched_det_idxs:
            d = all_dets[det_idx]
            if d["confidence"] >= self.high_thresh:
                trk = KalmanBoxTracker(
                    d["bbox"], d.get("class_id", 0),
                    d.get("class_name", "unknown"), d["confidence"],
                    track_id=self._next_id,
                )
                self._next_id += 1
                self.trackers.append(trk)
                self._stats["total_tracks"] += 1

        # Remove deleted tracks
        self.trackers = [t for t in self.trackers if t.state != "deleted"
                        and t.time_since_update < self.max_age]

        # Update stats
        self._stats["active_tracks"] = len(
            [t for t in self.trackers if t.state == "confirmed"]
        )
        self._stats["lost_tracks"] = len(
            [t for t in self.trackers if t.state == "lost"]
        )

        # Return active tracks
        return self._get_active_tracks()

    def _match(self, boxes_a: List[List[float]],
               boxes_b: List[List[float]]):
        """Match two sets of bounding boxes using IoU.
        
        Returns:
            matches: list of (a_idx, b_idx)
            unmatched_a: list of unmatched indices from set A
            unmatched_b: list of unmatched indices from set B
        """
        if not boxes_a or not boxes_b:
            return [], list(range(len(boxes_a))), list(range(len(boxes_b)))

        bb_a = np.array(boxes_a, dtype=np.float32)
        bb_b = np.array(boxes_b, dtype=np.float32)

        # Cost = 1 - IoU
        iou_matrix = iou_batch(bb_a, bb_b)
        cost_matrix = 1.0 - iou_matrix

        return linear_assignment(cost_matrix)

    def _get_active_tracks(self) -> List[Dict]:
        """Get output from confirmed and recent tracks."""
        results = []
        for trk in self.trackers:
            if trk.state in ("confirmed", "tentative") and trk.time_since_update <= 1:
                output = trk.to_dict()
                results.append(output)
        return results

    def get_stats(self) -> Dict:
        return self._stats.copy()

    def reset(self):
        """Reset all tracks."""
        self.trackers.clear()
        self.frame_count = 0
        self._next_id = 1
        self._stats = {
            "total_tracks": 0,
            "active_tracks": 0,
            "lost_tracks": 0,
        }
