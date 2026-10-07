"""
Edge Modules — IBVAP AI Analytics Components
=============================================

Modules:
- face_recognition: Face detection + watchlist matching
- reid: Person Re-Identification (cross-camera matching)
- activity_rules: Suspicious activity rule engine
- night_enhance: Night-time image enhancement
"""

__all__ = [
    "get_face_engine",
    "get_reid_engine",
    "get_rule_engine",
    "get_night_enhancer",
    "DualSpectralFusionEngine",
    "FastDehazeFilter",
]


def get_face_engine(**kwargs):
    from .face_recognition import get_face_engine as _get
    return _get(**kwargs)


def get_reid_engine(**kwargs):
    from .reid import get_reid_engine as _get
    return _get(**kwargs)


def get_rule_engine(**kwargs):
    from .activity_rules import get_rule_engine as _get
    return _get(**kwargs)


def get_night_enhancer(**kwargs):
    from .night_enhance import get_night_enhancer as _get
    return _get(**kwargs)


def get_sensor_fusion_engine(**kwargs):
    from .sensor_fusion import DualSpectralFusionEngine
    return DualSpectralFusionEngine(**kwargs)


def get_dehaze_filter(**kwargs):
    from .dehaze import FastDehazeFilter
    return FastDehazeFilter(**kwargs)
