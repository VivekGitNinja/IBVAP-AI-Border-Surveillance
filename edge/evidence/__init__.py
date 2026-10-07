"""Evidence management and association layer."""

from edge.evidence.association import (
    AssociationState,
    EvidenceStatus,
    WatchlistAuthStatus,
    PlateEvidenceCandidate,
    PlateTrackRecord,
    FaceEvidenceCandidate,
    FaceTrackRecord,
    UnifiedTrackEvidence,
    ANPRAssociator,
    FRSAssociator,
    EvidenceAssociationEngine,
)

__all__ = [
    "AssociationState",
    "EvidenceStatus",
    "WatchlistAuthStatus",
    "PlateEvidenceCandidate",
    "PlateTrackRecord",
    "FaceEvidenceCandidate",
    "FaceTrackRecord",
    "UnifiedTrackEvidence",
    "ANPRAssociator",
    "FRSAssociator",
    "EvidenceAssociationEngine",
]
