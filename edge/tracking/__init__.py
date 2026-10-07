"""Object tracking module.

Provides:
- CentroidTracker: simple centroid-based tracker
- ByteTracker: Kalman + IoU tracker (ByteTrack-inspired)
"""

from edge.tracking.centroid import CentroidTracker, TrackedObject
from edge.tracking.bytetrack import ByteTracker
from edge.tracking.slew_to_cue import SlewToCueCoordinator, PTZNodeCalibration, SlewCueResult

__all__ = [
    "CentroidTracker",
    "TrackedObject",
    "ByteTracker",
    "SlewToCueCoordinator",
    "PTZNodeCalibration",
    "SlewCueResult",
]
