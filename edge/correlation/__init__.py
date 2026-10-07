"""
Cross-Camera Correlation & Topology Package.
============================================

Provides physical camera network topology modeling, kinematic feasibility analysis,
appearance and identity cross-camera tracklet association, and incident correlation.
"""

from edge.correlation.topology import (
    CameraOverlapType,
    CameraNode,
    TopologyEdge,
    CameraTopologyGraph,
)
from edge.correlation.cross_camera import (
    CrossCameraConfig,
    CrossCameraState,
    TrackletDescriptor,
    CrossCameraAssociation,
    GlobalEntityDossier,
    CrossCameraAssociator,
)

__all__ = [
    "CameraOverlapType",
    "CameraNode",
    "TopologyEdge",
    "CameraTopologyGraph",
    "CrossCameraConfig",
    "CrossCameraState",
    "TrackletDescriptor",
    "CrossCameraAssociation",
    "GlobalEntityDossier",
    "CrossCameraAssociator",
]
