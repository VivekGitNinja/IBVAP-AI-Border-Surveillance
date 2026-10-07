"""
IBVAP — Step 04 P0-01 Remediation: Step 1 Focused Contract Verification Suite
=============================================================================

Verifies ONLY Step 1 of P0_01_FINAL_IMPLEMENTATION_CHECKLIST:
1. FramePacket instantiation with all required contract fields.
2. Immutability: Attribute reassignment raises dataclasses.FrozenInstanceError.
3. Timing semantics: capture_mono preserves time.perf_counter() precision; capture_utc preserves wall-clock epoch.
4. Per-camera telemetry counters initialization.
5. Isolation: Telemetry counters initialize and increment independently per CameraPipeline instance.
6. Stream epoch initial value equals 1.
"""

import time
import pytest
from dataclasses import FrozenInstanceError
import numpy as np

from backend.app.services.live_pipeline import FramePacket, CameraPipeline


def test_frame_packet_instantiation_with_all_required_fields():
    """Verify FramePacket can be cleanly instantiated with all required fields."""
    dummy_frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    t_mono = time.perf_counter()
    t_utc = time.time()

    packet = FramePacket(
        frame_id=101,
        frame=dummy_frame,
        capture_utc=t_utc,
        capture_mono=t_mono,
        source_fps=29.97,
        stream_epoch=1,
        hardware_drop_count=0,
    )

    assert packet.frame_id == 101
    assert packet.frame.shape == (720, 1280, 3)
    assert packet.capture_utc == t_utc
    assert packet.capture_mono == t_mono
    assert packet.source_fps == 29.97
    assert packet.stream_epoch == 1
    assert packet.hardware_drop_count == 0


def test_frame_packet_immutability_raises_frozen_instance_error():
    """Verify FramePacket is immutable and attribute re-assignment raises FrozenInstanceError."""
    dummy_frame = np.zeros((480, 640, 3), dtype=np.uint8)
    packet = FramePacket(
        frame_id=1,
        frame=dummy_frame,
        capture_utc=time.time(),
        capture_mono=time.perf_counter(),
        source_fps=30.0,
        stream_epoch=1,
        hardware_drop_count=0,
    )

    with pytest.raises(FrozenInstanceError):
        packet.frame_id = 2  # Reassignment must be strictly blocked

    with pytest.raises(FrozenInstanceError):
        packet.source_fps = 15.0

    with pytest.raises(FrozenInstanceError):
        packet.stream_epoch = 2


def test_frame_packet_timing_semantics_monotonic_and_utc():
    """Verify capture_mono uses monotonic timer and capture_utc uses wall-clock epoch."""
    mono_before = time.perf_counter()
    utc_before = time.time()
    time.sleep(0.005)

    packet = FramePacket(
        frame_id=5,
        frame=np.zeros((10, 10, 3), dtype=np.uint8),
        capture_utc=time.time(),
        capture_mono=time.perf_counter(),
        source_fps=25.0,
        stream_epoch=1,
        hardware_drop_count=0,
    )

    mono_after = time.perf_counter()
    utc_after = time.time()

    # Monotonic timing strictly within bounded window
    assert mono_before <= packet.capture_mono <= mono_after
    # UTC timing strictly within epoch window
    assert utc_before <= packet.capture_utc <= utc_after
    # capture_utc must be a large epoch timestamp (> 1.7e9 for year 2024+)
    assert packet.capture_utc > 1_700_000_000.0


def test_camera_pipeline_telemetry_counters_initialization():
    """Verify CameraPipeline initializes all required telemetry counters with correct defaults."""
    pipe = CameraPipeline(camera_id=42, stream_url="demo://test", camera_name="Test Cam", bop="BOP-42")

    assert pipe.reader_frames_acquired == 0
    assert pipe.reader_read_failures == 0
    assert pipe.processor_frames_started == 0
    assert pipe.processor_frames_skipped_latest_slot == 0
    assert pipe.processor_stale_frames == 0
    assert pipe.detection_stride_skips == 0
    assert pipe.reconnect_events == 0
    assert pipe.stream_epoch == 1


def test_camera_pipeline_telemetry_counters_isolation_across_instances():
    """Verify telemetry counters are strictly instance-isolated across distinct CameraPipeline instances."""
    pipe1 = CameraPipeline(camera_id=1, stream_url="demo://1", camera_name="Cam 1")
    pipe2 = CameraPipeline(camera_id=2, stream_url="demo://2", camera_name="Cam 2")

    # Mutate instance 1 counters
    pipe1.reader_frames_acquired += 50
    pipe1.reader_read_failures += 2
    pipe1.processor_frames_started += 48
    pipe1.processor_frames_skipped_latest_slot += 2
    pipe1.reconnect_events += 1
    pipe1.stream_epoch += 1

    # Instance 2 counters must remain pristine at initial values
    assert pipe2.reader_frames_acquired == 0
    assert pipe2.reader_read_failures == 0
    assert pipe2.processor_frames_started == 0
    assert pipe2.processor_frames_skipped_latest_slot == 0
    assert pipe2.reconnect_events == 0
    assert pipe2.stream_epoch == 1

    # Instance 1 reflects modifications
    assert pipe1.reader_frames_acquired == 50
    assert pipe1.reader_read_failures == 2
    assert pipe1.processor_frames_started == 48
    assert pipe1.processor_frames_skipped_latest_slot == 2
    assert pipe1.reconnect_events == 1
    assert pipe1.stream_epoch == 2


def test_stream_epoch_initial_value():
    """Verify stream_epoch starts at 1 for new pipelines and can be incremented monotonically."""
    pipe = CameraPipeline(camera_id=99, stream_url="demo://99")
    assert pipe.stream_epoch == 1

    pipe.stream_epoch += 1
    assert pipe.stream_epoch == 2
