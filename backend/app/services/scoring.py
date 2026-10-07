"""
Risk Priority Scoring (RPS) Engine — Phase 1

Computes an explainable Risk Priority Score (RPS) for operator triage:
- RPS = G_spatial * (B_base + Delta_motion + Delta_env) * T_persistence
- Strictly framed as priority/risk ranking for human-in-the-loop review.
- NOT a probability of intent, guilt, or autonomous enforcement directive.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ── SEVERITY & PRIORITY THRESHOLDS ──
SEVERITY_THRESHOLDS = [
    (85, "CRITICAL"),
    (65, "HIGH"),
    (40, "MEDIUM"),
    (0, "LOW"),
]

# ── SPATIAL ADMISSIBILITY GATE ──
# G_spatial defines the spatial boundary constraint:
# Unmonitored / public areas have zero spatial admissibility (RPS = 0).
SPATIAL_GATE_MAP = {
    "RESTRICTED": 1.00,
    "SENSITIVE": 1.00,
    "BUFFER": 0.65,
    "MONITORED": 0.30,
    "PUBLIC": 0.00,
}

# ── UNCALIBRATED BASELINE PARAMETERS ──
# Note: These parameters represent initial operational defense baseline heuristics.
# They are explicitly uncalibrated and subject to BOP empirical field tuning.
CONFIRMATION_FRAMES_THRESHOLD = 3   # Consecutive frames required for full persistence
TEMPORAL_JITTER_FACTOR = 0.40        # Priority suppression factor for single-frame jitter
BREACH_BASE_CROSSING = 70.0          # Base priority points for verified perimeter boundary crossing
BREACH_BASE_PRESENCE = 25.0          # Base priority points for persistent presence within zone
DIRECTION_ENTRY_BONUS = 10.0         # Priority modifier for inward breach trajectory
DIRECTION_EXIT_PENALTY = -15.0       # Priority reduction for outward retreat trajectory
DWELL_MAX_BONUS = 15.0               # Maximum priority accumulation for prolonged dwell
NIGHT_CONTEXT_BONUS = 5.0            # Environmental visibility modifier
VEHICLE_CONTEXT_BONUS = 5.0          # High-mobility tactical context modifier

# Backward compatibility legacy weights mapping
DEFAULT_WEIGHTS = {
    "confidence": 0,                   # Separated: evidence quality only, does not add threat
    "zone_severity": 25,
    "boundary_crossing": 18,
    "loitering": 12,
    "night": 8,
    "vehicle_context": 5,
    "behavior_anomaly": 10,
    "repeated_activity": 5,
    "camera_health_degraded": 0,        # Strictly 0: health never increases threat
    "cross_camera_corroboration": 0,    # Out of scope in Phase 1
}

SIGNAL_DESCRIPTIONS = {
    "confidence": "Detection confidence (evidence quality)",
    "zone_severity": "Spatial zone severity gate",
    "boundary_crossing": "Virtual fence boundary crossing",
    "loitering": "Prolonged dwell in security zone",
    "night": "Low-visibility night context",
    "vehicle_context": "High-mobility vehicle presence",
    "behavior_anomaly": "Kinematic movement anomaly",
    "repeated_activity": "Historical repeated crossing",
    "camera_health_degraded": "Camera health degradation (diagnostic only)",
    "cross_camera_corroboration": "Cross-camera correlation",
}


@dataclass
class RPSAssessment:
    """Explainable Risk Priority Score assessment for operator triage."""
    rps_score: float = 0.0
    priority_level: str = "LOW"
    evidence_confidence: float = 0.0
    signal_contributions: dict[str, float] = field(default_factory=dict)
    explainability_facts: list[str] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
    operator_guidance: str = "Continue routine monitoring."
    spatial_gate: float = 0.0
    temporal_factor: float = 1.0
    identity_evidence_status: str = "MISSING"
    vehicle_evidence_status: str = "MISSING"
    watchlist_authorization_status: str = "UNKNOWN"
    cross_camera_corroborated: bool = False
    correlated_camera_ids: list[int] = field(default_factory=list)
    dossier_id: Optional[str] = None
    evidence_provenance: dict[str, Any] = field(default_factory=dict)
    ai_assessment: dict[str, Any] = field(default_factory=dict)

    # Phase 4 Deterministic Decision Flags (Side-Effect-Free)
    suppression_revocation_required: bool = False
    suppression_revocation_reason: Optional[str] = None
    omega_operator: float = 1.0

    # Legacy attribute compatibility
    score: float = 0.0
    severity: str = "LOW"
    confidence: float = 0.0
    reasons: list[str] = field(default_factory=list)
    supporting_signals: dict[str, Any] = field(default_factory=dict)
    uncertainty: str = ""
    recommended_action: str = "Continue monitoring."

    def __post_init__(self):
        # Synchronize modern and legacy fields
        if self.score == 0.0 and self.rps_score != 0.0:
            self.score = self.rps_score
        elif self.rps_score == 0.0 and self.score != 0.0:
            self.rps_score = self.score

        if self.severity == "LOW" and self.priority_level != "LOW":
            self.severity = self.priority_level
        elif self.priority_level == "LOW" and self.severity != "LOW":
            self.priority_level = self.severity

        if self.confidence == 0.0 and self.evidence_confidence != 0.0:
            self.confidence = self.evidence_confidence
        elif self.evidence_confidence == 0.0 and self.confidence != 0.0:
            self.evidence_confidence = self.confidence

        if not self.reasons and self.explainability_facts:
            self.reasons = list(self.explainability_facts)
        elif not self.explainability_facts and self.reasons:
            self.explainability_facts = list(self.reasons)

        if self.recommended_action == "Continue monitoring." and self.operator_guidance != "Continue routine monitoring.":
            self.recommended_action = self.operator_guidance
        elif self.operator_guidance == "Continue routine monitoring." and self.recommended_action != "Continue monitoring.":
            self.operator_guidance = self.recommended_action


# Compatibility alias
ThreatAssessment = RPSAssessment


def compute_threat_score(
    signals: dict[str, float] | None = None,
    weights: dict[str, float] | None = None,
    context: dict[str, Any] | None = None,
) -> RPSAssessment:
    """Compute an explainable Risk Priority Score (RPS) using spatial gating and temporal persistence."""
    ctx = context or {}
    signals = signals or {}
    contributions: dict[str, float] = {}
    facts: list[str] = []
    missing_evidence: list[str] = []
    if ctx.get("missing_evidence"):
        for item in ctx["missing_evidence"]:
            if item not in missing_evidence:
                missing_evidence.append(item)

    # 1. Perception Confidence (Separated from threat points)
    conf_val = max(0.0, min(1.0, float(signals.get("confidence", ctx.get("confidence", ctx.get("evidence_confidence", 0.0))))))
    evidence_confidence = round(conf_val, 2)
    contributions["confidence_evidence_only"] = 0.0  # Perception confidence does not add threat points
    facts.append(f"Perception confidence: {conf_val:.0%} (+0.0 evidence quality only)")

    # Explicit safeguard: raw biometric similarity or OCR confidence can NEVER add threat points
    if "face_similarity" in signals:
        contributions["face_similarity_evidence_only"] = 0.0
    if "ocr_confidence" in signals:
        contributions["ocr_confidence_evidence_only"] = 0.0

    # 2. Camera Health Factor (Never increases threat)
    health_degraded = bool(signals.get("camera_health_degraded", 0.0) > 0.1 or ctx.get("camera_health_degraded"))
    if health_degraded:
        contributions["camera_health_degraded"] = 0.0
        facts.append("Camera health degraded: Diagnostic metadata only (+0.0 threat impact)")

    # 3. Spatial Gate Evaluation (G_spatial)
    # Check context zone_type first, fallback to signals["zone_severity"]
    zone_type = ctx.get("zone_type")
    if zone_type and zone_type.upper() in SPATIAL_GATE_MAP:
        g_spatial = SPATIAL_GATE_MAP[zone_type.upper()]
    elif "zone_severity" in signals:
        sev = max(0.0, min(1.0, float(signals["zone_severity"])))
        if sev >= 0.8:
            g_spatial = SPATIAL_GATE_MAP["RESTRICTED"]
        elif sev >= 0.5:
            g_spatial = SPATIAL_GATE_MAP["BUFFER"]
        elif sev > 0.0:
            g_spatial = SPATIAL_GATE_MAP["MONITORED"]
        else:
            g_spatial = 0.0
    else:
        g_spatial = 0.0

    contributions["spatial_gate"] = round(g_spatial, 2)

    # 4. If outside security perimeter (G_spatial == 0), RPS is strictly 0.0
    if g_spatial <= 0.001:
        facts.append("Spatial Gate: Entity is in public / unmonitored area (+0.0 spatial priority)")
        if conf_val > 0.7:
            facts.append(f"Observation: High perception confidence ({conf_val:.0%}) (+0.0 threat outside security zones)")

        is_suppressed = bool(ctx.get("operator_suppressed", False))
        omega_op = 0.0 if is_suppressed else 1.0
        if is_suppressed:
            facts.append("Operator feedback suppression active in public area")

        missing_evidence.extend([
            "No perimeter boundary violation",
            "ANPR vehicle authorization not required outside monitored zones",
            "Cross-camera perimeter tracking not applicable",
        ])

        # Preserve cross-camera context even in public zones (RPS stays 0.0)
        early_cross_cam = bool(ctx.get("cross_camera_corroborated", False))
        early_corr_cams = list(ctx.get("correlated_camera_ids", []))
        early_doss_id = ctx.get("dossier_id")
        if early_cross_cam:
            # Remove cross-camera missing evidence since it's corroborated
            missing_evidence = [m for m in missing_evidence if "cross-camera" not in m.lower()]

        return RPSAssessment(
            rps_score=0.0,
            priority_level="LOW",
            evidence_confidence=evidence_confidence,
            signal_contributions=contributions,
            explainability_facts=facts,
            missing_evidence=missing_evidence,
            operator_guidance="Entity outside monitored security zones. Continue routine monitoring.",
            spatial_gate=0.0,
            temporal_factor=1.0,
            suppression_revocation_required=False,
            suppression_revocation_reason=None,
            omega_operator=omega_op,
            cross_camera_corroborated=early_cross_cam,
            correlated_camera_ids=early_corr_cams,
            dossier_id=early_doss_id,
            ai_assessment={
                "detected": ctx.get("object_type", "moving object"),
                "confidence": evidence_confidence,
                "context": ctx.get("zone_name", "public area"),
                "behavior": ctx.get("behavior", "normal movement"),
                "spatial_gate": 0.0,
                "rps_score": 0.0,
                "priority_level": "LOW",
                "omega_operator": omega_op,
                "human_action": "Entity outside monitored security zones. Continue routine monitoring.",
                "missing_evidence": missing_evidence,
                "cross_camera_corroborated": early_cross_cam,
                "correlated_camera_ids": early_corr_cams,
                "dossier_id": early_doss_id,
            },
        )

    facts.append(f"Spatial Gate active: {zone_type or 'monitored zone'} (+{g_spatial:.2f} gate)")

    # 5. Temporal Persistence Gate (T_persistence)
    is_jitter = bool(ctx.get("is_single_frame_jitter"))
    consecutive_frames = int(ctx.get("consecutive_frames", 10))
    dwell_time = float(ctx.get("dwell_time", signals.get("loitering", 0.0) * 60.0))

    if is_jitter or (consecutive_frames < CONFIRMATION_FRAMES_THRESHOLD and dwell_time < 0.5):
        t_persistence = TEMPORAL_JITTER_FACTOR
        facts.append(f"Temporal persistence: Single-frame/unconfirmed observation (+{t_persistence:.2f} factor)")
    else:
        t_persistence = 1.0
        facts.append("Temporal persistence: Confirmed multi-frame trajectory (+1.00 persistence factor)")

    # 6. Physical Breach & Kinematic Evaluation (B_base)
    boundary_crossing = float(signals.get("boundary_crossing", 0.0))
    if boundary_crossing > 0.5:
        b_base = BREACH_BASE_CROSSING
        contributions["boundary_breach"] = round(b_base, 1)
        facts.append(f"Boundary breach: Virtual perimeter crossing detected (+{b_base:.0f})")
    else:
        b_base = BREACH_BASE_PRESENCE
        contributions["zone_presence"] = round(b_base, 1)
        facts.append(f"Zone presence: Active target inside perimeter boundary (+{b_base:.0f})")

    # 7. Directional Trajectory Evaluation (Entry vs Exit / Perimeter Convergence)
    direction = str(ctx.get("direction") or signals.get("direction", "")).lower()
    delta_dir = 0.0
    if direction in ("entry", "inward", "approaching"):
        delta_dir = DIRECTION_ENTRY_BONUS
        contributions["direction_entry"] = round(delta_dir, 1)
        facts.append(f"Directionality: Inward perimeter approach (+{delta_dir:.0f})")
    elif direction in ("exit", "outward", "retreating"):
        delta_dir = DIRECTION_EXIT_PENALTY
        contributions["direction_exit"] = round(delta_dir, 1)
        facts.append(f"Directionality: Outward retreat from perimeter ({delta_dir:.0f})")
    else:
        # Perimeter convergence signal fallback
        perim_conv = float(signals.get("perimeter_convergence", ctx.get("perimeter_convergence", 0.0)))
        if perim_conv > 0.35:
            delta_dir = DIRECTION_ENTRY_BONUS * min(1.0, perim_conv)
            contributions["perimeter_convergence_inward"] = round(delta_dir, 1)
            facts.append(f"Perimeter convergence: Inward boundary approach ({perim_conv:+.2f}) (+{delta_dir:.1f})")
        elif perim_conv < -0.35:
            delta_dir = DIRECTION_EXIT_PENALTY * min(1.0, abs(perim_conv))
            contributions["perimeter_convergence_outward"] = round(delta_dir, 1)
            facts.append(f"Perimeter convergence: Outward boundary departure ({perim_conv:+.2f}) ({delta_dir:.1f})")

    # 8. Dwell & Loitering (No double counting with generic anomaly)
    loitering_signal = float(signals.get("loitering", ctx.get("loitering", 0.0)))
    delta_dwell = 0.0
    if loitering_signal > 0.1:
        delta_dwell = min(DWELL_MAX_BONUS, loitering_signal * DWELL_MAX_BONUS)
        contributions["loitering_dwell"] = round(delta_dwell, 1)
        facts.append(f"Loitering: Prolonged dwell inside zone (+{delta_dwell:.1f})")

    # 9. Environmental & Vehicle Context Modifiers
    delta_env = 0.0
    if float(signals.get("night", 0.0)) > 0.5:
        delta_env += NIGHT_CONTEXT_BONUS
        contributions["night_context"] = round(NIGHT_CONTEXT_BONUS, 1)
        facts.append(f"Environmental context: Low-visibility night window (+{NIGHT_CONTEXT_BONUS:.0f})")

    if float(signals.get("vehicle_context", 0.0)) > 0.5:
        delta_env += VEHICLE_CONTEXT_BONUS
        contributions["vehicle_mobility"] = round(VEHICLE_CONTEXT_BONUS, 1)
        facts.append(f"Tactical context: High-mobility motorized vehicle (+{VEHICLE_CONTEXT_BONUS:.0f})")

    # Behavior anomaly (speed anomaly + trajectory irregularity)
    behavior_anomaly = float(signals.get("behavior_anomaly", ctx.get("behavior_anomaly", 0.0)))
    if loitering_signal <= 0.1 and behavior_anomaly > 0.1:
        anomaly_pts = round(min(15.0, behavior_anomaly * 10.0), 1)
        delta_env += anomaly_pts
        contributions["behavior_anomaly"] = anomaly_pts
        facts.append(f"Behavior anomaly: Observable kinematic irregularity index ({behavior_anomaly:.2f}) (+{anomaly_pts:.1f})")

    # 10. Operator Feedback Suppression Gate (Omega_operator) & Same-Frame Break-Glass
    zone_type_clean = str(zone_type or ctx.get("zone_name", "")).upper()
    is_restricted_breach = zone_type_clean in ("RESTRICTED", "SENSITIVE")

    suppression_revocation_required = False
    suppression_revocation_reason = None
    omega_operator = 1.0

    if is_restricted_breach:
        # Break-glass: Operator suppression is overridden
        omega_operator = 1.0
        if ctx.get("operator_suppressed"):
            suppression_revocation_required = True
            suppression_revocation_reason = f"{zone_type_clean}_ZONE_PENETRATION"
            facts.append(f"SAFETY OVERRIDE: Operator suppression revoked due to active {zone_type_clean} zone penetration")
    else:
        # Non-restricted zones (BUFFER, MONITORED, PUBLIC): check suppression
        if ctx.get("operator_suppressed"):
            omega_operator = 0.0
            facts.append("Operator feedback suppression active (RPS forced to 0.0)")

    # 11. Synthesize Raw RPS & Apply Gates
    raw_subtotal = b_base + delta_dir + delta_dwell + delta_env
    raw_rps = g_spatial * raw_subtotal * t_persistence * omega_operator
    rps_score = round(max(0.0, min(100.0, raw_rps)), 1)

    # 12. Determine Priority Level
    priority_level = "LOW"
    for threshold, level in SEVERITY_THRESHOLDS:
        if rps_score >= threshold:
            priority_level = level
            break

    # 13. Compile Missing Evidence
    if ctx.get("missing_evidence"):
        for item in ctx["missing_evidence"]:
            if item not in missing_evidence:
                missing_evidence.append(item)

    if not ctx.get("plate_text") and not any("plate" in m.lower() for m in missing_evidence):
        missing_evidence.append("No license plate read (ANPR not available or non-vehicle)")
    if not ctx.get("face_candidate") and not any("facial recognition" in m.lower() or "face" in m.lower() for m in missing_evidence):
        missing_evidence.append("Facial recognition identity evidence not available (sub-threshold or unacquired)")
    if not ctx.get("cross_camera_corroborated") and not any("cross-camera" in m.lower() for m in missing_evidence):
        missing_evidence.append("Cross-camera spatial correlation evidence not available (single-camera observation)")
    if not ctx.get("thermal_confirmed") and not any("thermal" in m.lower() for m in missing_evidence):
        missing_evidence.append("Thermal sensor confirmation not available")

    # 14. Neutral Operator Guidance
    if priority_level == "CRITICAL":
        operator_guidance = "PRIORITY 1: IMMEDIATE operator visual verification required on live feed. Verify perimeter boundary status before taking operational action."
    elif priority_level == "HIGH":
        operator_guidance = "PRIORITY 2: Operator review recommended. Inspect target trajectory and verify boundary adherence."
    elif priority_level == "MEDIUM":
        operator_guidance = "PRIORITY 3: Monitored activity. Verify conditions if dwell continues."
    else:
        operator_guidance = "PRIORITY 4: Routine activity. Continue normal monitoring."

    # Uncertainty factor
    uncertainty_notes = []
    if evidence_confidence < 0.5:
        uncertainty_notes.append(f"Detection confidence is low ({evidence_confidence:.0%})")
    if t_persistence < 1.0:
        uncertainty_notes.append("Trajectory unconfirmed (short observation window)")
    if health_degraded:
        uncertainty_notes.append("Camera health degraded (sensor reliability reduced)")
    uncertainty_str = "; ".join(uncertainty_notes) if uncertainty_notes else "Low uncertainty (high sensor reliability)"

    id_status = ctx.get("identity_evidence_status", "MISSING")
    veh_status = ctx.get("vehicle_evidence_status", "MISSING")
    auth_status = ctx.get("watchlist_authorization_status", "UNKNOWN")
    cross_cam_corr = bool(ctx.get("cross_camera_corroborated", False))
    corr_cams = list(ctx.get("correlated_camera_ids", []))
    doss_id = ctx.get("dossier_id")
    ev_prov = ctx.get("evidence_provenance", {})

    ai_assessment = {
        "detected": ctx.get("object_type", "moving object"),
        "confidence": evidence_confidence,
        "context": ctx.get("zone_name", "monitored area"),
        "zone_type": zone_type or "monitored",
        "behavior": ctx.get("behavior", "normal movement"),
        "spatial_gate": g_spatial,
        "temporal_factor": t_persistence,
        "omega_operator": omega_operator,
        "suppression_revocation_required": suppression_revocation_required,
        "suppression_revocation_reason": suppression_revocation_reason,
        "rps_score": rps_score,
        "priority_level": priority_level,
        "signal_contributions": contributions,
        "explainability_facts": facts,
        "missing_evidence": missing_evidence,
        "uncertainty": uncertainty_str,
        "human_action": operator_guidance,
        "identity_evidence_status": id_status,
        "vehicle_evidence_status": veh_status,
        "watchlist_authorization_status": auth_status,
        "cross_camera_corroborated": cross_cam_corr,
        "correlated_camera_ids": corr_cams,
        "dossier_id": doss_id,
        "evidence_provenance": ev_prov,
    }

    return RPSAssessment(
        rps_score=rps_score,
        priority_level=priority_level,
        evidence_confidence=evidence_confidence,
        signal_contributions=contributions,
        explainability_facts=facts,
        missing_evidence=missing_evidence,
        operator_guidance=operator_guidance,
        spatial_gate=g_spatial,
        temporal_factor=t_persistence,
        suppression_revocation_required=suppression_revocation_required,
        suppression_revocation_reason=suppression_revocation_reason,
        omega_operator=omega_operator,
        identity_evidence_status=id_status,
        vehicle_evidence_status=veh_status,
        watchlist_authorization_status=auth_status,
        cross_camera_corroborated=cross_cam_corr,
        correlated_camera_ids=corr_cams,
        dossier_id=doss_id,
        evidence_provenance=ev_prov,
        ai_assessment=ai_assessment,
        score=rps_score,
        severity=priority_level,
        confidence=evidence_confidence,
        reasons=facts,
        supporting_signals=signals,
        uncertainty=uncertainty_str,
        recommended_action=operator_guidance,
    )


def get_severity_for_score(score: float) -> str:
    """Map an RPS/threat score to priority/severity string."""
    for threshold, level in SEVERITY_THRESHOLDS:
        if score >= threshold:
            return level
    return "LOW"


# Export aliases
calculate_rps = compute_threat_score
score = compute_threat_score
