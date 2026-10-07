"""
Adverse Weather De-Hazing Module (Fog, Smog, Dust, and Heavy Rain Penetration)
=============================================================================

Implements fast Dark Channel Prior (DCP) image restoration to penetrate
dense border fog, dust storms, and heavy precipitation before neural detection.
"""

from __future__ import annotations
import time
import cv2
import numpy as np
from dataclasses import dataclass
from typing import Tuple


@dataclass
class DehazeResult:
    """Outcome of adverse weather de-hazing."""
    restored_frame: np.ndarray        # Restored BGR image
    was_processed: bool               # Whether dehazing filter was applied
    fog_density: float                # Estimated atmospheric haze density (0.0 to 1.0)
    atmospheric_light: float          # Estimated ambient atmospheric light
    processing_time_ms: float         # Restoration latency in milliseconds
    transmission_map: Optional[np.ndarray] = None
    contrast_improvement_factor: float = 1.0

    @property
    def dehazed_frame(self) -> np.ndarray:
        return self.restored_frame


class FastDehazeFilter:
    """Fast Dark Channel Prior (DCP) restoration filter optimized for edge video feeds."""

    def __init__(
        self,
        patch_size: int = 15,
        omega: float = 0.85,
        t_min: float = 0.15,
        haze_detection_threshold: float = 0.35,
    ):
        """
        Args:
            patch_size: Neighborhood window size for dark channel minimum filter
            omega: Haze retention factor (0.85 preserves natural distance perspective)
            t_min: Minimum transmission floor to avoid noise amplification in deep haze
            haze_detection_threshold: Mean dark channel level above which dehazing triggers
        """
        self.patch_size = patch_size
        self.omega = omega
        self.t_min = t_min
        self.haze_threshold = haze_detection_threshold

    def get_dark_channel(self, img: np.ndarray) -> np.ndarray:
        """Compute the minimum intensity across color channels and local spatial patch."""
        min_channel = np.min(img, axis=2)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (self.patch_size, self.patch_size))
        dark_channel = cv2.erode(min_channel, kernel)
        return dark_channel

    def estimate_atmospheric_light(self, img: np.ndarray, dark_channel: np.ndarray) -> np.ndarray:
        """Estimate the atmospheric light A from the brightest pixels in the dark channel."""
        h, w = img.shape[:2]
        num_pixels = h * w
        top_k = max(10, int(num_pixels * 0.001))

        flat_dark = dark_channel.reshape(-1)
        flat_img = img.reshape(-1, 3)

        indices = np.argpartition(flat_dark, -top_k)[-top_k:]
        brightest_pixels = flat_img[indices]
        
        # Atmospheric light vector is the maximum intensity among candidate pixels
        A = np.max(brightest_pixels, axis=0)
        return A.astype(np.float64)

    def process(self, frame: np.ndarray, force: bool = False) -> DehazeResult:
        """
        Evaluate frame for atmospheric haze and apply restoration if needed.
        
        Args:
            frame: Raw BGR input frame (uint8)
            force: Force restoration even if estimated haze density is low
        """
        if frame is None or len(frame.shape) != 3 or frame.shape[2] != 3:
            raise ValueError("Input frame must be a 3-channel BGR numpy array")

        t0 = time.perf_counter()
        normalized_img = frame.astype(np.float64) / 255.0
        
        # 1. Compute dark channel
        dark = self.get_dark_channel(normalized_img)
        fog_density = float(np.mean(dark))

        # Check if dehazing is required
        if not force and fog_density < self.haze_threshold:
            return DehazeResult(
                restored_frame=frame,
                was_processed=False,
                fog_density=round(fog_density, 3),
                atmospheric_light=0.0,
                processing_time_ms=(time.perf_counter() - t0) * 1000.0,
                transmission_map=dark,
                contrast_improvement_factor=1.0,
            )

        # 2. Estimate atmospheric light
        A = self.estimate_atmospheric_light(normalized_img, dark)
        A = np.maximum(A, 0.1)

        # 3. Estimate transmission map t(x)
        norm_by_A = normalized_img / A
        transmission = 1.0 - self.omega * self.get_dark_channel(norm_by_A)
        transmission = np.maximum(transmission, self.t_min)

        # Fast guided smoothing using Gaussian blur
        transmission_smooth = cv2.GaussianBlur(transmission, (self.patch_size, self.patch_size), 0)
        transmission_smooth = np.expand_dims(transmission_smooth, axis=2)

        # 4. Recover radiance J(x) = (I(x) - A) / t(x) + A
        restored = (normalized_img - A) / transmission_smooth + A
        restored = np.clip(restored * 255.0, 0, 255).astype(np.uint8)

        # Contrast improvement calculation
        std_orig = float(np.std(frame))
        std_rest = float(np.std(restored))
        contrast_factor = round(std_rest / max(1.0, std_orig), 2)

        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        return DehazeResult(
            restored_frame=restored,
            was_processed=True,
            fog_density=round(fog_density, 3),
            atmospheric_light=float(np.mean(A)),
            processing_time_ms=elapsed_ms,
            transmission_map=transmission,
            contrast_improvement_factor=contrast_factor,
        )
