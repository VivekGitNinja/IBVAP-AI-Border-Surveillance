"""
Unit tests for Multi-Spectral Sensor Fusion and Adverse Weather De-Hazing modules.
"""

import numpy as np
import pytest

from edge.modules.sensor_fusion import DualSpectralFusionEngine, FusionResult
from edge.modules.dehaze import FastDehazeFilter, DehazeResult


class TestDualSpectralFusionEngine:
    def test_fusion_with_matching_dimensions(self):
        engine = DualSpectralFusionEngine(fusion_alpha=0.5, hotspot_threshold=0.75)
        
        # Synthetic optical frame (BGR)
        optical = np.full((480, 640, 3), 100, dtype=np.uint8)
        # Synthetic thermal frame (grayscale / single channel with hot spot)
        thermal = np.full((480, 640), 50, dtype=np.uint8)
        # Hotspot at center
        thermal[200:280, 280:360] = 240

        result = engine.fuse(optical, thermal)
        assert isinstance(result, FusionResult)
        assert result.fused_frame.shape == (480, 640, 3)
        assert result.fused_frame.dtype == np.uint8
        assert result.hotspot_mask.shape == (480, 640)
        assert result.hotspot_count >= 1
        assert result.max_thermal_intensity >= 240
        assert result.latency_ms > 0

    def test_fusion_with_mismatched_dimensions(self):
        engine = DualSpectralFusionEngine()
        optical = np.zeros((720, 1280, 3), dtype=np.uint8)
        # Thermal is 512x640
        thermal = np.zeros((512, 640), dtype=np.uint8)
        thermal[100:150, 100:150] = 220

        result = engine.fuse(optical, thermal)
        assert result.fused_frame.shape == (720, 1280, 3)
        assert result.hotspot_mask.shape == (720, 1280)

    def test_fusion_with_3channel_thermal(self):
        engine = DualSpectralFusionEngine()
        optical = np.zeros((300, 300, 3), dtype=np.uint8)
        thermal_bgr = np.zeros((300, 300, 3), dtype=np.uint8)
        thermal_bgr[50:100, 50:100, :] = 250

        result = engine.fuse(optical, thermal_bgr)
        assert result.fused_frame.shape == (300, 300, 3)
        assert result.hotspot_count >= 1

    def test_fusion_invalid_input_handling(self):
        engine = DualSpectralFusionEngine()
        with pytest.raises(ValueError):
            engine.fuse(np.zeros((10, 10)), np.zeros((10, 10)))  # optical not 3 channels


class TestFastDehazeFilter:
    def test_dehaze_synthetic_hazy_image(self):
        dehazer = FastDehazeFilter(omega=0.85, patch_size=7)
        # Create clear image with high contrast
        clear = np.zeros((240, 320, 3), dtype=np.uint8)
        clear[:120, :] = [200, 50, 50]
        clear[120:, :] = [50, 200, 50]

        # Add synthetic atmospheric veil / haze: I = J * t + A * (1 - t)
        t = 0.5
        a = 220
        hazy = (clear.astype(np.float32) * t + a * (1 - t)).astype(np.uint8)

        result = dehazer.process(hazy)
        assert isinstance(result, DehazeResult)
        assert result.dehazed_frame.shape == (240, 320, 3)
        assert result.transmission_map.shape == (240, 320)
        assert 0.0 <= result.contrast_improvement_factor
        assert result.processing_time_ms > 0

    def test_dehaze_edge_cases(self):
        dehazer = FastDehazeFilter()
        # All black frame
        black = np.zeros((100, 100, 3), dtype=np.uint8)
        res_black = dehazer.process(black)
        assert res_black.dehazed_frame.shape == (100, 100, 3)

        # All white frame
        white = np.full((100, 100, 3), 255, dtype=np.uint8)
        res_white = dehazer.process(white)
        assert res_white.dehazed_frame.shape == (100, 100, 3)

    def test_dehaze_invalid_dimensions(self):
        dehazer = FastDehazeFilter()
        with pytest.raises(ValueError):
            dehazer.process(np.zeros((100, 100), dtype=np.uint8))
