"""Edge scoring engine interface."""

from backend.app.services.scoring import compute_threat_score, score

__all__ = ["compute_threat_score", "score"]
