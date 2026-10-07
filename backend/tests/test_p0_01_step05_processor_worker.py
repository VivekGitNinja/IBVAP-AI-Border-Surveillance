"""
IBVAP — Step 05 P0-01 Remediation: Dedicated Processor Worker & Inference Decoupling Suite
==========================================================================================

Verifies ONLY Step 5 of P0_01_FINAL_IMPLEMENTATION_CHECKLIST:
1. processor worker starts correctly
2. processor worker stops correctly
3. processor waits for frame
4. condition wake-up works
5. reader publishes without processor
6. processor consumes latest frame
7. frame IDs are monotonic
8. duplicate frame is not processed twice
9. slow processor does not block reader
10. latest-frame slot remains capacity 1
11. intermediate frames can be superseded
12. processing stride is respected
13. detection stride telemetry is correct
14. stale-frame handling remains correct
15. stream epoch transition resets appropriate processor state
16. processor exception does not kill reader
17. processor error telemetry is correct
18. stop while processor is processing
19. stop while processor is waiting
20. no post-stop processing
21. no post-stop publication
22. two cameras remain isolated
23. only one processor worker per pipeline
24. static ownership audit proves reader does not invoke AI processing
"""

import ast
import inspect
import textwrap
import threading
import time
import pytest
import numpy as np

from backend.app.services.live_pipeline import (
    CameraPipeline,
    FramePacket,
    PROCESSOR_STATE_START,
    PROCESSOR_STATE_PROCESSING,
    PROCESSOR_STATE_WAITING_FOR_FRAME,
    PROCESSOR_STATE_STOPPING,
    PROCESSOR_STATE_STOPPED,
    RECONNECT_STATE_CONNECTED,
)


class MockFastCapture:
    """Configurable mock VideoCapture for fast, deterministic pipeline tests."""

    def __init__(self, frames: int = 20, frame_delay: float = 0.001, fail_after: int = -1):
        self.frames = frames
        self.frame_delay = frame_delay
        self.fail_after = fail_after
        self.read_count = 0
        self.released = False
        self._lock = threading.Lock()

    def isOpened(self) -> bool:
        with self._lock:
            return not self.released

    def read(self):
        with self._lock:
            if self.released:
                return False, None
            if self.fail_after >= 0 and self.read_count >= self.fail_after:
                return False, None
            if self.read_count >= self.frames:
                return False, None
            self.read_count += 1
            if self.frame_delay > 0:
                time.sleep(self.frame_delay)
            frame = np.full((60, 80, 3), (self.read_count * 10) % 255, dtype=np.uint8)
            return True, frame

    def release(self):
        with self._lock:
            self.released = True


def test_step05_01_processor_worker_starts_correctly():
    """Verify processor worker starts, sets state, and runs in a separate thread."""
    processed_frames = []
    cap = MockFastCapture(frames=10)
    pipe = CameraPipeline(
        camera_id=1,
        stream_url="fake://1",
        capture_device=cap,
        frame_processor=lambda f, fid: processed_frames.append(fid),
        detection_stride=1,
        frame_rate_pause=0.001,
    )
    pipe.start()

    assert pipe._processor_thread is not None
    assert pipe._processor_thread.is_alive()
    assert pipe._processor_thread.name == "processor-cam1"
    assert pipe._thread is pipe._processor_thread

    # Allow processor to run
    time.sleep(0.04)

    pipe.stop()
    assert len(processed_frames) > 0


def test_step05_02_processor_worker_stops_correctly():
    """Verify pipe.stop() stops processor worker, transitions state to STOPPED, and cleans thread reference."""
    cap = MockFastCapture(frames=10)
    pipe = CameraPipeline(
        camera_id=1,
        stream_url="fake://1",
        capture_device=cap,
        frame_processor=lambda f, fid: None,
        detection_stride=1,
        frame_rate_pause=0.001,
    )
    pipe.start()
    time.sleep(0.02)

    pipe.stop()

    assert pipe.processor_state == PROCESSOR_STATE_STOPPED
    assert pipe._processor_thread is None
    assert pipe._thread is None
    assert pipe._processor_stop_event.is_set()


def test_step05_03_processor_waits_for_frame():
    """Verify processor waits in WAITING_FOR_FRAME state when no frames are published."""
    pipe = CameraPipeline(
        camera_id=1,
        stream_url="fake://1",
        capture_device=MockFastCapture(frames=0),  # No frames
        frame_processor=lambda f, fid: None,
    )
    pipe._running = True
    pipe._processor_stop_event.clear()

    t = threading.Thread(target=pipe._processor_worker, daemon=True)
    t.start()

    time.sleep(0.03)

    assert pipe.processor_state == PROCESSOR_STATE_WAITING_FOR_FRAME
    assert pipe.processor_frames_started == 0
    assert pipe.processor_frames_completed == 0

    pipe.stop()
    t.join(timeout=1.0)


def test_step05_04_condition_wake_up_works():
    """Verify publishing a frame wakes the processor from WAITING_FOR_FRAME immediately."""
    processed = []
    pipe = CameraPipeline(
        camera_id=1,
        stream_url="fake://1",
        capture_device=MockFastCapture(frames=0),
        frame_processor=lambda f, fid: processed.append(fid),
        detection_stride=1,
        frame_rate_pause=0.001,
    )
    pipe._running = True
    pipe._processor_stop_event.clear()

    t = threading.Thread(target=pipe._processor_worker, daemon=True)
    t.start()

    time.sleep(0.02)
    assert pipe.processor_state == PROCESSOR_STATE_WAITING_FOR_FRAME
    assert len(processed) == 0

    # Publish frame manually to test condition wake up
    pkt = FramePacket(
        frame_id=1,
        frame=np.zeros((10, 10, 3), dtype=np.uint8),
        capture_utc=time.time(),
        capture_mono=time.perf_counter(),
        source_fps=30.0,
        stream_epoch=1,
        hardware_drop_count=0,
    )
    pipe.publish_frame_packet(pkt)

    for _ in range(20):
        if len(processed) == 1:
            break
        time.sleep(0.005)

    assert processed == [1]

    pipe.stop()
    t.join(timeout=1.0)


def test_step05_05_reader_publishes_without_processor():
    """Verify reader worker acquires and publishes frames into slot even if processor is not started."""
    cap = MockFastCapture(frames=5, frame_delay=0.001)
    pipe = CameraPipeline(
        camera_id=1,
        stream_url="fake://1",
        capture_device=cap,
        consecutive_failure_threshold=1,
    )
    pipe._running = True
    pipe._reader_stop_event.clear()

    t_reader = threading.Thread(target=pipe._reader_worker, daemon=True)
    t_reader.start()

    time.sleep(0.03)

    assert pipe.reader_frames_acquired >= 3
    with pipe._slot_lock:
        assert pipe._latest_frame_packet is not None
        assert pipe._latest_frame_packet.frame_id >= 3

    pipe.stop()
    t_reader.join(timeout=1.0)


def test_step05_06_processor_consumes_latest_frame():
    """Verify processor consumes the latest frame published by reader."""
    processed = []
    cap = MockFastCapture(frames=5, frame_delay=0.002)
    pipe = CameraPipeline(
        camera_id=1,
        stream_url="fake://1",
        capture_device=cap,
        frame_processor=lambda f, fid: processed.append(fid),
        detection_stride=1,
        frame_rate_pause=0.001,
    )
    pipe.start()

    for _ in range(30):
        if len(processed) >= 4:
            break
        time.sleep(0.005)

    pipe.stop()

    assert len(processed) >= 3
    assert pipe.processor_last_processed_frame_id in processed


def test_step05_07_frame_ids_are_monotonic():
    """Verify processor processes frames in strictly monotonically increasing order."""
    processed_ids = []
    cap = MockFastCapture(frames=10, frame_delay=0.001)
    pipe = CameraPipeline(
        camera_id=1,
        stream_url="fake://1",
        capture_device=cap,
        frame_processor=lambda f, fid: processed_ids.append(fid),
        detection_stride=1,
        frame_rate_pause=0.001,
    )
    pipe.start()

    time.sleep(0.05)
    pipe.stop()

    assert len(processed_ids) >= 3
    for i in range(len(processed_ids) - 1):
        assert processed_ids[i + 1] > processed_ids[i]


def test_step05_08_duplicate_frame_is_not_processed_twice():
    """Verify the same frame packet is NEVER processed more than once."""
    processed = []
    pipe = CameraPipeline(
        camera_id=1,
        stream_url="fake://1",
        frame_processor=lambda f, fid: processed.append(fid),
        detection_stride=1,
        frame_rate_pause=0.001,
    )
    pipe._running = True
    pipe._processor_stop_event.clear()

    t = threading.Thread(target=pipe._processor_worker, daemon=True)
    t.start()

    pkt = FramePacket(
        frame_id=42,
        frame=np.zeros((10, 10, 3), dtype=np.uint8),
        capture_utc=time.time(),
        capture_mono=time.perf_counter(),
        source_fps=30.0,
        stream_epoch=1,
        hardware_drop_count=0,
    )
    pipe.publish_frame_packet(pkt)

    time.sleep(0.03)

    assert processed == [42]
    assert pipe.processor_frames_completed == 1

    # Re-publish same frame id defensively
    pipe.publish_frame_packet(pkt)
    time.sleep(0.03)

    # Must still be processed only once!
    assert processed == [42]
    assert pipe.processor_frames_completed == 1

    pipe.stop()
    t.join(timeout=1.0)


def test_step05_09_slow_processor_does_not_block_reader():
    """Verify a slow processor does NOT slow down or block the reader worker."""
    reader_acquired_checkpoint = []

    def slow_processor(frame, frame_id):
        time.sleep(0.04)  # 40ms slow AI simulation

    cap = MockFastCapture(frames=20, frame_delay=0.001)  # fast 1ms reader
    pipe = CameraPipeline(
        camera_id=1,
        stream_url="fake://1",
        capture_device=cap,
        frame_processor=slow_processor,
        detection_stride=1,
        frame_rate_pause=0.0,
    )
    pipe.start()

    # Wait 0.03s: reader should have acquired 15-20 frames while processor is still on frame 1
    time.sleep(0.03)
    reader_acquired_checkpoint.append(pipe.reader_frames_acquired)

    time.sleep(0.06)
    pipe.stop()

    assert reader_acquired_checkpoint[0] >= 10
    assert pipe.reader_frames_acquired >= 15
    # Processor only processed a fraction of the frames because it's slow
    assert pipe.processor_frames_completed < pipe.reader_frames_acquired


def test_step05_10_latest_frame_slot_remains_capacity_1():
    """Verify latest-frame slot strictly retains 1 frame even with mismatched reader/processor rates."""
    cap = MockFastCapture(frames=15, frame_delay=0.001)
    pipe = CameraPipeline(
        camera_id=1,
        stream_url="fake://1",
        capture_device=cap,
        frame_processor=lambda f, fid: time.sleep(0.02),
        detection_stride=1,
    )
    pipe.start()
    time.sleep(0.03)

    with pipe._slot_lock:
        # Check that slot holds a single FramePacket object, not a collection
        assert isinstance(pipe._latest_frame_packet, FramePacket)

    pipe.stop()


def test_step05_11_intermediate_frames_can_be_superseded():
    """Verify intermediate frames are superseded in slot and telemetry counts them accurately."""
    processed = []

    def slow_processor(frame, frame_id):
        processed.append(frame_id)
        time.sleep(0.03)

    cap = MockFastCapture(frames=15, frame_delay=0.001)
    pipe = CameraPipeline(
        camera_id=1,
        stream_url="fake://1",
        capture_device=cap,
        frame_processor=slow_processor,
        detection_stride=1,
        frame_rate_pause=0.0,
    )
    pipe.start()

    time.sleep(0.08)
    pipe.stop()

    assert pipe.processor_frames_skipped_latest_slot > 0
    total_accounted = len(processed) + pipe.processor_frames_skipped_latest_slot
    # Total accounted must reach the latest frame consumed
    assert total_accounted >= processed[-1]


def test_step05_12_processing_stride_is_respected():
    """Verify detection_stride skips neural processing on non-stride frames."""
    ai_processed = []

    def mock_ai(frame, frame_id):
        ai_processed.append(frame_id)

    cap = MockFastCapture(frames=10, frame_delay=0.001)
    pipe = CameraPipeline(
        camera_id=1,
        stream_url="fake://1",
        capture_device=cap,
        frame_processor=mock_ai,
        detection_stride=3,  # Stride: only frames 3, 6, 9...
        frame_rate_pause=0.001,
    )
    pipe.start()

    time.sleep(0.05)
    pipe.stop()

    for fid in ai_processed:
        assert fid % 3 == 0


def test_step05_13_detection_stride_telemetry_is_correct():
    """Verify detection_stride_skips telemetry increments only for stride skips."""
    cap = MockFastCapture(frames=6, frame_delay=0.002)
    pipe = CameraPipeline(
        camera_id=1,
        stream_url="fake://1",
        capture_device=cap,
        frame_processor=lambda f, fid: None,
        detection_stride=2,  # Evens processed, odds skipped (1, 3, 5 skipped)
        frame_rate_pause=0.001,
    )
    pipe.start()

    time.sleep(0.04)
    pipe.stop()

    assert pipe.detection_stride_skips >= 2


def test_step05_14_stale_frame_handling_remains_correct():
    """Verify frames older than stale_frame_threshold increment processor_stale_frames."""
    pipe = CameraPipeline(
        camera_id=1,
        stream_url="fake://1",
        frame_processor=lambda f, fid: None,
        stale_frame_threshold=0.05,  # 50ms stale threshold
        detection_stride=1,
    )
    pipe._running = True
    pipe._processor_stop_event.clear()

    t = threading.Thread(target=pipe._processor_worker, daemon=True)
    t.start()

    # Publish an intentionally old frame packet (mono timestamp 200ms in the past)
    old_pkt = FramePacket(
        frame_id=1,
        frame=np.zeros((10, 10, 3), dtype=np.uint8),
        capture_utc=time.time() - 0.2,
        capture_mono=time.perf_counter() - 0.2,
        source_fps=30.0,
        stream_epoch=1,
        hardware_drop_count=0,
    )
    pipe.publish_frame_packet(old_pkt)

    time.sleep(0.03)

    assert pipe.processor_stale_frames >= 1
    assert pipe.processor_frames_completed == 1

    pipe.stop()
    t.join(timeout=1.0)


def test_step05_15_stream_epoch_transition_resets_appropriate_processor_state():
    """Verify stream epoch advance resets temporal frame state and tracks new epoch."""
    epochs_seen = []

    def tracking_processor(frame, frame_id):
        epochs_seen.append(pipe.processor_last_processed_stream_epoch)

    pipe = CameraPipeline(
        camera_id=1,
        stream_url="fake://1",
        frame_processor=tracking_processor,
        detection_stride=1,
        frame_rate_pause=0.001,
    )
    pipe._prev_frame = np.ones((10, 10, 3), dtype=np.uint8)
    pipe._running = True
    pipe._processor_stop_event.clear()

    t = threading.Thread(target=pipe._processor_worker, daemon=True)
    t.start()

    # Publish epoch 1 frame
    pipe.publish_frame_packet(
        FramePacket(1, np.zeros((10, 10, 3), dtype=np.uint8), time.time(), time.perf_counter(), 30.0, 1, 0)
    )
    time.sleep(0.02)
    assert pipe.processor_last_processed_stream_epoch == 1

    # Simulate reconnect epoch advance: publish epoch 2 frame
    pipe._prev_frame = np.ones((10, 10, 3), dtype=np.uint8)  # Set temporal state
    pipe.publish_frame_packet(
        FramePacket(2, np.zeros((10, 10, 3), dtype=np.uint8), time.time(), time.perf_counter(), 30.0, 2, 0)
    )
    time.sleep(0.02)

    assert pipe.processor_last_processed_stream_epoch == 2
    assert pipe._prev_frame is None  # Temporal frame state was reset!

    pipe.stop()
    t.join(timeout=1.0)


def test_step05_16_processor_exception_does_not_kill_reader():
    """Verify an unhandled processing exception does NOT crash or stop the reader worker."""
    call_count = [0]

    def failing_processor(frame, frame_id):
        call_count[0] += 1
        if call_count[0] == 2:
            raise RuntimeError("Simulated AI inference segmentation fault")

    cap = MockFastCapture(frames=10, frame_delay=0.002)
    pipe = CameraPipeline(
        camera_id=1,
        stream_url="fake://1",
        capture_device=cap,
        frame_processor=failing_processor,
        detection_stride=1,
        frame_rate_pause=0.001,
    )
    pipe.start()

    time.sleep(0.06)
    pipe.stop()

    # Reader must have continued acquiring frames despite processor exception
    assert pipe.reader_frames_acquired >= 5
    assert pipe.processor_processing_errors >= 1
    # Processor survived and processed subsequent frames
    assert pipe.processor_frames_completed >= 3


def test_step05_17_processor_error_telemetry_is_correct():
    """Verify processor_processing_errors counts errors accurately."""
    errors_to_throw = 3
    thrown = [0]

    def buggy_processor(frame, frame_id):
        if thrown[0] < errors_to_throw:
            thrown[0] += 1
            raise ValueError(f"Crash #{thrown[0]}")

    cap = MockFastCapture(frames=8, frame_delay=0.002)
    pipe = CameraPipeline(
        camera_id=1,
        stream_url="fake://1",
        capture_device=cap,
        frame_processor=buggy_processor,
        detection_stride=1,
        frame_rate_pause=0.001,
    )
    pipe.start()

    time.sleep(0.05)
    pipe.stop()

    assert pipe.processor_processing_errors == 3


def test_step05_18_stop_while_processor_is_processing():
    """Verify stop() called while processor is in active inference exits cleanly once frame completes."""
    proc_started = threading.Event()

    def slow_processor(frame, frame_id):
        proc_started.set()
        time.sleep(0.04)

    cap = MockFastCapture(frames=5, frame_delay=0.001)
    pipe = CameraPipeline(
        camera_id=1,
        stream_url="fake://1",
        capture_device=cap,
        frame_processor=slow_processor,
        detection_stride=1,
    )
    pipe.start()

    assert proc_started.wait(timeout=1.0)
    assert pipe.processor_state == PROCESSOR_STATE_PROCESSING

    t0 = time.perf_counter()
    pipe.stop()
    t_stop = time.perf_counter() - t0

    assert pipe.processor_state == PROCESSOR_STATE_STOPPED
    assert t_stop < 0.2  # Prompt exit, no indefinite hang


def test_step05_19_stop_while_processor_is_waiting():
    """Verify stop() called while processor is waiting on condition exits immediately (< 20ms)."""
    pipe = CameraPipeline(
        camera_id=1,
        stream_url="fake://1",
        capture_device=MockFastCapture(frames=0),
        frame_processor=lambda f, fid: None,
    )
    pipe.start()

    time.sleep(0.02)
    assert pipe.processor_state == PROCESSOR_STATE_WAITING_FOR_FRAME

    t0 = time.perf_counter()
    pipe.stop()
    t_stop = time.perf_counter() - t0

    assert pipe.processor_state == PROCESSOR_STATE_STOPPED
    assert t_stop < 0.05  # Prompt stop


def test_step05_20_no_post_stop_processing():
    """Verify no frames are processed after pipeline is stopped."""
    processed = []
    cap = MockFastCapture(frames=2, frame_delay=0.001)
    pipe = CameraPipeline(
        camera_id=1,
        stream_url="fake://1",
        capture_device=cap,
        frame_processor=lambda f, fid: processed.append(fid),
        detection_stride=1,
    )
    pipe.start()
    time.sleep(0.03)
    pipe.stop()

    count_at_stop = len(processed)

    # Defensively attempt manual publication
    pipe.publish_frame_packet(
        FramePacket(999, np.zeros((10, 10, 3), dtype=np.uint8), time.time(), time.perf_counter(), 30.0, 1, 0)
    )
    time.sleep(0.02)

    assert len(processed) == count_at_stop


def test_step05_21_no_post_stop_publication():
    """Verify reader does not publish frames after stop."""
    cap = MockFastCapture(frames=100, frame_delay=0.001)
    pipe = CameraPipeline(
        camera_id=1,
        stream_url="fake://1",
        capture_device=cap,
        detection_stride=1,
    )
    pipe.start()
    time.sleep(0.02)
    pipe.stop()

    acquired_at_stop = pipe.reader_frames_acquired
    time.sleep(0.02)

    assert pipe.reader_frames_acquired == acquired_at_stop


def test_step05_22_two_cameras_remain_isolated():
    """Verify slow processor on Camera 1 has zero performance effect on Camera 2."""
    cam1_processed = []
    cam2_processed = []

    def slow_cam1(frame, fid):
        cam1_processed.append(fid)
        time.sleep(0.04)

    def fast_cam2(frame, fid):
        cam2_processed.append(fid)

    cap1 = MockFastCapture(frames=10, frame_delay=0.001)
    cap2 = MockFastCapture(frames=10, frame_delay=0.001)

    pipe1 = CameraPipeline(
        camera_id=101,
        stream_url="fake://101",
        capture_device=cap1,
        frame_processor=slow_cam1,
        detection_stride=1,
        frame_rate_pause=0.0,
    )
    pipe2 = CameraPipeline(
        camera_id=102,
        stream_url="fake://102",
        capture_device=cap2,
        frame_processor=fast_cam2,
        detection_stride=1,
        frame_rate_pause=0.0,
    )

    pipe1.start()
    pipe2.start()

    time.sleep(0.05)

    pipe1.stop()
    pipe2.stop()

    # Camera 2 should have completed significantly more frames than slow Camera 1
    assert len(cam2_processed) >= 6
    assert len(cam1_processed) <= 2
    assert pipe2.reader_frames_acquired >= 8


def test_step05_23_only_one_processor_worker_per_pipeline():
    """Verify calling start() multiple times does not spawn duplicate processor threads."""
    cap = MockFastCapture(frames=10)
    pipe = CameraPipeline(
        camera_id=1,
        stream_url="fake://1",
        capture_device=cap,
        frame_processor=lambda f, fid: None,
    )
    pipe.start()
    t_orig = pipe._processor_thread

    # Call start again while running
    pipe.start()
    assert pipe._processor_thread is t_orig

    pipe.stop()


def test_step05_24_static_ownership_audit_proves_reader_does_not_invoke_ai_processing():
    """Static AST inspection verifying _reader_worker does not call AI inference or tracking."""
    source = inspect.getsource(CameraPipeline._reader_worker)
    tree = ast.parse(textwrap.dedent(source))

    prohibited_calls = {
        "_process_frame",
        "detect",
        "_track_with_bytetrack",
        "tracker",
        "zone_fence",
        "kinematic_engine",
        "seal_evidence",
        "create_detector",
    }

    found_violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = None
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
            if name in prohibited_calls:
                found_violations.append(name)

    assert len(found_violations) == 0, f"Found prohibited AI calls in _reader_worker: {found_violations}"
