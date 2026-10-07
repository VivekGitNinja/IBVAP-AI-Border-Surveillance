"""
Multi-Spectral Sensor Fusion Module (EO/IR Thermal + Optical)
============================================================

Performs real-time spatial co-registration and feature-level fusion of
Electro-Optical (EO) visible daylight/low-light video and Infrared (IR/LWIR)
thermal camera feeds to unmask camouflaged infiltrators and foliage-hidden threats.
"""

from __future__ import annotations
import time
import cv2
import numpy as np
from dataclasses import dataclass
from typing import Tuple, Optional, Dict, Any


@dataclass
class FusionResult:
    """Telemetry and image payload produced by multi-spectral sensor fusion."""
    fused_frame: np.ndarray           # BGR output ready for YOLO detection / human display
    optical_weight: float             # Alpha blend weight for optical channel (0.0 - 1.0)
    thermal_weight: float             # 1.0 - optical_weight
    hotspots_detected: int            # Count of thermal clusters above threat temperature
    max_intensity: float              # Peak thermal pixel intensity (0 - 255)
    processing_time_ms: float         # Execution latency
    alignment_method: str             # "HOMOGRAPHY", "AFFINE", "RESIZE_DIRECT"
    hotspot_mask: Optional[np.ndarray] = None

    @property
    def hotspot_count(self) -> int:
        return self.hotspots_detected

    @property
    def max_thermal_intensity(self) -> float:
        return self.max_intensity

    @property
    def latency_ms(self) -> float:
        return self.processing_time_ms


class DualSpectralFusionEngine:
    """Co-registers and blends visible optical video with LWIR thermal sensor streams."""

    def __init__(
        self,
        default_alpha: float = 0.65,
        hotspot_threshold: int = 190,
        alignment_matrix: Optional[np.ndarray] = None,
        fusion_alpha: Optional[float] = None,
    ):
        """
        Args:
            default_alpha: Weight given to visible optical channel (0.65 = 65% optical, 35% thermal)
            hotspot_threshold: Pixel intensity (0-255) above which a thermal reading is treated as heat source
            alignment_matrix: Optional 3x3 homography or 2x3 affine matrix for optical/thermal calibration
            fusion_alpha: Optional alias for default_alpha
        """
        chosen_alpha = fusion_alpha if fusion_alpha is not None else default_alpha
        self.default_alpha = max(0.1, min(0.9, float(chosen_alpha)))
        self.hotspot_threshold = int(hotspot_threshold)
        self.alignment_matrix = alignment_matrix

    def align_thermal(self, thermal_frame: np.ndarray, target_shape: Tuple[int, int]) -> np.ndarray:
        """Warp thermal frame to match optical camera frame dimensions and field of view."""
        target_h, target_w = target_shape[:2]
        th_h, th_w = thermal_frame.shape[:2]

        if self.alignment_matrix is not None:
            if self.alignment_matrix.shape == (3, 3):
                aligned = cv2.warpPerspective(thermal_frame, self.alignment_matrix, (target_w, target_h))
            else:
                aligned = cv2.warpAffine(thermal_frame, self.alignment_matrix, (target_w, target_h))
        else:
            # Direct resolution scaling fallback
            aligned = cv2.resize(thermal_frame, (target_w, target_h), interpolation=cv2.INTER_LINEAR)

        return aligned

    def fuse(
        self,
        optical_frame: np.ndarray,
        thermal_frame: np.ndarray,
        alpha: Optional[float] = None,
        apply_colormap: bool = True,
    ) -> FusionResult:
        """
        Fuse visible optical and thermal frames.
        
        Args:
            optical_frame: BGR color image (H, W, 3)
            thermal_frame: Grayscale (H, W) or BGR (H, W, 3) thermal radiometric image
            alpha: Optional override for optical weight (0.0 to 1.0)
            apply_colormap: Whether to colorize thermal grayscale using INFERNO/JET before blending
        """
        if optical_frame is None or len(optical_frame.shape) != 3 or optical_frame.shape[2] != 3:
            raise ValueError("optical_frame must be a 3-channel BGR numpy array")

        t_start = time.perf_counter()
        opt_h, opt_w = optical_frame.shape[:2]

        # 1. Align thermal to optical coordinate frame
        aligned_thermal = self.align_thermal(thermal_frame, (opt_h, opt_w))

        # 2. Extract grayscale thermal intensity
        if len(aligned_thermal.shape) == 3:
            thermal_gray = cv2.cvtColor(aligned_thermal, cv2.COLOR_BGR2GRAY)
        else:
            thermal_gray = aligned_thermal.copy()

        # Ensure thermal_gray is contiguous uint8
        thermal_gray = np.ascontiguousarray(np.clip(thermal_gray, 0, 255), dtype=np.uint8)

        # 3. Detect thermal hotspots (body heat signatures ~ 36-38°C)
        _, hotspot_mask = cv2.threshold(thermal_gray, self.hotspot_threshold, 255, cv2.THRESH_BINARY)
        hotspot_mask = np.ascontiguousarray(hotspot_mask, dtype=np.uint8)
        contours, _ = cv2.findContours(hotspot_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        hotspots_count = sum(1 for cnt in contours if cv2.contourArea(cnt) > 25)

        # 4. Colorize thermal component for visual and neural distinction
        if apply_colormap:
            thermal_bgr = cv2.applyColorMap(thermal_gray, cv2.COLORMAP_INFERNO)
        else:
            thermal_bgr = cv2.cvtColor(thermal_gray, cv2.COLOR_GRAY2BGR)

        # 5. Dynamic Alpha Blending with Hotspot Saliency Boosting
        effective_alpha = self.default_alpha if alpha is None else max(0.0, min(1.0, float(alpha)))
        
        # Base weighted linear combination
        fused = cv2.addWeighted(optical_frame, effective_alpha, thermal_bgr, 1.0 - effective_alpha, 0)

        # Hotspot enhancement: boost brightness on verified thermal signatures
        if hotspots_count > 0:
            hotspot_expanded = cv2.dilate(hotspot_mask, np.ones((5, 5), np.uint8), iterations=1)
            hotspot_indices = np.where(hotspot_expanded > 0)
            # Inject thermal coloration aggressively over hotspots to highlight camouflaged persons
            fused[hotspot_indices] = cv2.addWeighted(
                optical_frame[hotspot_indices], 0.25,
                thermal_bgr[hotspot_indices], 0.75, 0
            )

        elapsed_ms = (time.perf_counter() - t_start) * 1000.0

        return FusionResult(
            fused_frame=fused,
            optical_weight=effective_alpha,
            thermal_weight=1.0 - effective_alpha,
            hotspots_detected=hotspots_count,
            max_intensity=float(np.max(thermal_gray)),
            processing_time_ms=elapsed_ms,
            alignment_method="HOMOGRAPHY" if (self.alignment_matrix is not None and self.alignment_matrix.shape == (3, 3)) else "RESIZE_DIRECT",
            hotspot_mask=hotspot_mask,
        )
