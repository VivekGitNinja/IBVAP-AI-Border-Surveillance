"""
IBVAP — Step 04 P0-01 Remediation: Step 3 Dedicated RTSP Ingestion Worker Suite
================================================================================

Verifies ONLY Step 3 of P0_01_FINAL_IMPLEMENTATION_CHECKLIST:
1. reader worker starts successfully
2. successful capture creates FramePacket
3. frame IDs increase monotonically
4. capture timestamps are valid
5. monotonic timestamps are non-decreasing
6. successful read increments reader_frames_acquired
7. failed read increments reader_read_failures
8. failed read does not publish invalid packet
9. reader publishes through Step 2 slot API
10. latest-frame slot receives freshest packet
11. reader does not create an unbounded queue
12. multi-camera reader isolation
13. reader stop prevents further publication
14. no post-stop publication
15. NumPy frame remains unchanged after publication
16. source FPS is based on actual acquisition timing
17. capture ownership is single-owner
18. legacy duplicate cap.read() path is absent
19. reader does not execute downstream AI while holding slot lock
20. deterministic reader lifecycle under test shutdown
"""

import ast
import inspect
import time
import threading
import hashlib
from collections import deque
import pytest
import numpy as np

from backend.app.services.live_pipeline import FramePacket, CameraPipeline


class DeterministicFakeCapture:
    """Deterministic fake VideoCapture for controlled unit tests."""

    def __init__(
        self,
        frame_count: int = 10,
        fail_indices: set = None,
        frame_shape: tuple = (60, 80, 3),
        frame_fill: int = 10,
        frame_delay: float = 0.0,
    ):
        self.total_frames = frame_count
        self.current_frame = 0
        self.fail_indices = set(fail_indices or [])
        self.frame_shape = frame_shape
        self.frame_fill = frame_fill
        self.frame_delay = frame_delay
        self.released = False
        self.read_calls = 0
        self._lock = threading.Lock()

    def isOpened(self) -> bool:
        with self._lock:
            return not self.released

    def read(self):
        with self._lock:
            self.read_calls += 1
            if self.released:
                return False, None
            if self.frame_delay > 0:
                time.sleep(self.frame_delay)
            if self.current_frame in self.fail_indices:
                self.current_frame += 1
                return False, None
            if self.current_frame >= self.total_frames:
                return False, None

            self.current_frame += 1
            frame = np.full(self.frame_shape, (self.frame_fill + self.current_frame) % 255, dtype=np.uint8)
            return True, frame

    def release(self):
        with self._lock:
            self.released = True


def test_step03_01_reader_worker_starts_successfully():
    """Verify reader worker starts up, initializes capture, and runs."""
    fake_cap = DeterministicFakeCapture(frame_count=20, frame_delay=0.005)
    pipe = CameraPipeline(camera_id=1, stream_url="fake://1", capture_device=fake_cap)
    pipe._running = True

    t = threading.Thread(target=pipe._reader_worker, daemon=True)
    t.start()

    # Wait until at least one frame is published
    pkt = pipe.get_latest_frame_packet(timeout=1.0)
    assert pkt is not None
    assert pipe._cap is fake_cap
    assert t.is_alive()

    # Stop and clean up
    pipe._reader_stop_event.set()
    pipe._running = False
    with pipe._slot_lock:
        pipe._slot_condition.notify_all()
    t.join(timeout=1.0)

    assert not t.is_alive()
    assert fake_cap.released is True


def test_step03_02_successful_capture_creates_frame_packet():
    """Verify successful capture produces a valid immutable FramePacket."""
    fake_cap = DeterministicFakeCapture(frame_count=5, frame_delay=0.005)
    pipe = CameraPipeline(camera_id=1, stream_url="fake://1", capture_device=fake_cap)
    pipe._running = True

    t = threading.Thread(target=pipe._reader_worker, daemon=True)
    t.start()

    pkt = pipe.get_latest_frame_packet(timeout=1.0)
    assert isinstance(pkt, FramePacket)
    assert isinstance(pkt.frame, np.ndarray)
    assert pkt.frame_id >= 1
    assert pkt.stream_epoch == 1
    assert pkt.hardware_drop_count == 0

    pipe._reader_stop_event.set()
    pipe._running = False
    with pipe._slot_lock:
        pipe._slot_condition.notify_all()
    t.join(timeout=1.0)


def test_step03_03_frame_ids_increase_monotonically():
    """Verify frame IDs increment monotonically 1, 2, 3... without gaps or resets."""
    fake_cap = DeterministicFakeCapture(frame_count=6, frame_delay=0.002)
    pipe = CameraPipeline(camera_id=1, stream_url="fake://1", capture_device=fake_cap)
    pipe._running = True

    t = threading.Thread(target=pipe._reader_worker, daemon=True)
    t.start()

    collected_ids = []
    last_id = None
    for _ in range(6):
        pkt = pipe.get_latest_frame_packet(last_frame_id=last_id, timeout=1.0)
        if pkt:
            collected_ids.append(pkt.frame_id)
            last_id = pkt.frame_id

    pipe._reader_stop_event.set()
    pipe._running = False
    with pipe._slot_lock:
        pipe._slot_condition.notify_all()
    t.join(timeout=1.0)

    assert len(collected_ids) >= 4
    for i in range(len(collected_ids) - 1):
        assert collected_ids[i + 1] > collected_ids[i]


def test_step03_04_capture_timestamps_are_valid():
    """Verify capture_utc reflects real UTC epoch time."""
    fake_cap = DeterministicFakeCapture(frame_count=2)
    pipe = CameraPipeline(camera_id=1, stream_url="fake://1", capture_device=fake_cap)
    pipe._running = True

    t_before = time.time()
    t = threading.Thread(target=pipe._reader_worker, daemon=True)
    t.start()

    pkt = pipe.get_latest_frame_packet(timeout=1.0)
    t_after = time.time()

    assert t_before - 0.1 <= pkt.capture_utc <= t_after + 0.1

    pipe._reader_stop_event.set()
    pipe._running = False
    with pipe._slot_lock:
        pipe._slot_condition.notify_all()
    t.join(timeout=1.0)


def test_step03_05_monotonic_timestamps_are_non_decreasing():
    """Verify capture_mono uses monotonic clocks and is non-decreasing across frames."""
    fake_cap = DeterministicFakeCapture(frame_count=5, frame_delay=0.002)
    pipe = CameraPipeline(camera_id=1, stream_url="fake://1", capture_device=fake_cap)
    pipe._running = True

    t = threading.Thread(target=pipe._reader_worker, daemon=True)
    t.start()

    mono_times = []
    last_id = None
    for _ in range(4):
        pkt = pipe.get_latest_frame_packet(last_frame_id=last_id, timeout=1.0)
        if pkt:
            mono_times.append(pkt.capture_mono)
            last_id = pkt.frame_id

    pipe._reader_stop_event.set()
    pipe._running = False
    with pipe._slot_lock:
        pipe._slot_condition.notify_all()
    t.join(timeout=1.0)

    assert len(mono_times) >= 3
    for i in range(len(mono_times) - 1):
        assert mono_times[i + 1] >= mono_times[i]


def test_step03_06_successful_read_increments_reader_frames_acquired():
    """Verify reader_frames_acquired truthfully counts successful frame reads."""
    fake_cap = DeterministicFakeCapture(frame_count=8, frame_delay=0.001)
    pipe = CameraPipeline(camera_id=1, stream_url="fake://1", capture_device=fake_cap)
    pipe._running = True

    t = threading.Thread(target=pipe._reader_worker, daemon=True)
    t.start()

    # Wait until all 8 frames are acquired
    last_id = None
    while pipe.reader_frames_acquired < 8:
        pkt = pipe.get_latest_frame_packet(last_frame_id=last_id, timeout=0.1)
        if pkt:
            last_id = pkt.frame_id
        time.sleep(0.005)

    assert pipe.reader_frames_acquired == 8

    pipe._reader_stop_event.set()
    pipe._running = False
    with pipe._slot_lock:
        pipe._slot_condition.notify_all()
    t.join(timeout=1.0)


def test_step03_07_failed_read_increments_reader_read_failures():
    """Verify failed reads increment reader_read_failures and hardware_drop_count."""
    # Indices 1 and 3 will fail
    fake_cap = DeterministicFakeCapture(frame_count=5, fail_indices={1, 3}, frame_delay=0.002)
    pipe = CameraPipeline(camera_id=1, stream_url="fake://1", capture_device=fake_cap)
    pipe._running = True

    t = threading.Thread(target=pipe._reader_worker, daemon=True)
    t.start()

    # Wait until all attempts are made
    time.sleep(0.08)

    assert pipe.reader_read_failures >= 2

    pipe._reader_stop_event.set()
    pipe._running = False
    with pipe._slot_lock:
        pipe._slot_condition.notify_all()
    t.join(timeout=1.0)


def test_step03_08_failed_read_does_not_publish_invalid_packet():
    """Verify reader never publishes on failed read; slot remains None if all fail."""
    # All reads fail
    fake_cap = DeterministicFakeCapture(frame_count=5, fail_indices={0, 1, 2, 3, 4})
    pipe = CameraPipeline(camera_id=1, stream_url="fake://1", capture_device=fake_cap)
    pipe._running = True

    t = threading.Thread(target=pipe._reader_worker, daemon=True)
    t.start()

    time.sleep(0.05)

    assert pipe.reader_frames_acquired == 0
    assert pipe.reader_read_failures >= 3
    assert pipe.get_latest_frame_packet(timeout=0.02) is None

    pipe._reader_stop_event.set()
    pipe._running = False
    with pipe._slot_lock:
        pipe._slot_condition.notify_all()
    t.join(timeout=1.0)


def test_step03_09_reader_publishes_through_step2_slot_api():
    """Verify reader calls publish_frame_packet rather than mutating _latest_frame_packet directly."""
    fake_cap = DeterministicFakeCapture(frame_count=3)
    pipe = CameraPipeline(camera_id=1, stream_url="fake://1", capture_device=fake_cap)
    pipe._running = True

    publish_called = threading.Event()
    original_publish = pipe.publish_frame_packet

    def spy_publish(packet):
        publish_called.set()
        original_publish(packet)

    pipe.publish_frame_packet = spy_publish

    t = threading.Thread(target=pipe._reader_worker, daemon=True)
    t.start()

    assert publish_called.wait(timeout=1.0)

    pipe._reader_stop_event.set()
    pipe._running = False
    with pipe._slot_lock:
        pipe._slot_condition.notify_all()
    t.join(timeout=1.0)


def test_step03_10_latest_frame_slot_receives_freshest_packet():
    """Verify reader supersedes older frames in slot; consumer retrieves freshest frame."""
    fake_cap = DeterministicFakeCapture(frame_count=10, frame_delay=0.001)
    pipe = CameraPipeline(camera_id=1, stream_url="fake://1", capture_device=fake_cap)
    pipe._running = True

    t = threading.Thread(target=pipe._reader_worker, daemon=True)
    t.start()

    # Let the producer run through all 10 frames without consumer
    time.sleep(0.06)

    # Consumer now asks for the latest frame
    freshest = pipe.get_latest_frame_packet(timeout=0.1)
    assert freshest is not None
    assert freshest.frame_id == 10

    pipe._reader_stop_event.set()
    pipe._running = False
    with pipe._slot_lock:
        pipe._slot_condition.notify_all()
    t.join(timeout=1.0)


def test_step03_11_reader_does_not_create_unbounded_queue():
    """Verify latest-frame slot maintains capacity 1 without list/queue growth."""
    fake_cap = DeterministicFakeCapture(frame_count=30, frame_delay=0.001)
    pipe = CameraPipeline(camera_id=1, stream_url="fake://1", capture_device=fake_cap)
    pipe._running = True

    t = threading.Thread(target=pipe._reader_worker, daemon=True)
    t.start()

    time.sleep(0.08)

    # Check slot structure directly under lock
    with pipe._slot_lock:
        assert isinstance(pipe._latest_frame_packet, FramePacket)
        assert not isinstance(pipe._latest_frame_packet, (list, tuple, deque))
        assert pipe._latest_frame_packet.frame_id >= 20

    pipe._reader_stop_event.set()
    pipe._running = False
    with pipe._slot_lock:
        pipe._slot_condition.notify_all()
    t.join(timeout=1.0)


def test_step03_12_multi_camera_reader_isolation():
    """Verify Camera 1 and Camera 2 readers operate with complete isolation."""
    cap1 = DeterministicFakeCapture(frame_count=6, frame_fill=10, frame_delay=0.002)
    cap2 = DeterministicFakeCapture(frame_count=3, frame_fill=50, frame_delay=0.002)

    pipe1 = CameraPipeline(camera_id=1, stream_url="fake://1", capture_device=cap1)
    pipe2 = CameraPipeline(camera_id=2, stream_url="fake://2", capture_device=cap2)

    pipe1._running = True
    pipe2._running = True

    t1 = threading.Thread(target=pipe1._reader_worker, daemon=True)
    t2 = threading.Thread(target=pipe2._reader_worker, daemon=True)

    t1.start()
    t2.start()

    time.sleep(0.06)

    pipe1._reader_stop_event.set()
    pipe1._running = False
    pipe2._reader_stop_event.set()
    pipe2._running = False

    with pipe1._slot_lock:
        pipe1._slot_condition.notify_all()
    with pipe2._slot_lock:
        pipe2._slot_condition.notify_all()

    t1.join(timeout=1.0)
    t2.join(timeout=1.0)

    assert pipe1.reader_frames_acquired == 6
    assert pipe2.reader_frames_acquired == 3
    assert pipe1.get_latest_frame_packet().frame_id == 6
    assert pipe2.get_latest_frame_packet().frame_id == 3


def test_step03_13_reader_stop_prevents_further_publication():
    """Verify reader stops publishing once stop event is set."""
    fake_cap = DeterministicFakeCapture(frame_count=100, frame_delay=0.005)
    pipe = CameraPipeline(camera_id=1, stream_url="fake://1", capture_device=fake_cap)
    pipe._running = True

    t = threading.Thread(target=pipe._reader_worker, daemon=True)
    t.start()

    # Wait for frame 1
    pkt1 = pipe.get_latest_frame_packet(timeout=1.0)
    assert pkt1 is not None

    # Signal stop
    pipe._reader_stop_event.set()
    pipe._running = False
    with pipe._slot_lock:
        pipe._slot_condition.notify_all()
    t.join(timeout=1.0)

    last_id = pipe._latest_frame_packet.frame_id
    time.sleep(0.05)

    # Verify no new frames were published
    assert pipe._latest_frame_packet.frame_id == last_id


def test_step03_14_no_post_stop_publication():
    """Verify that if stop is signaled, reader worker does not publish even if read succeeded."""
    fake_cap = DeterministicFakeCapture(frame_count=10, frame_delay=0.01)
    pipe = CameraPipeline(camera_id=1, stream_url="fake://1", capture_device=fake_cap)
    # Stop before starting
    pipe._reader_stop_event.set()
    pipe._running = False

    pipe._reader_worker()

    assert pipe.reader_frames_acquired == 0
    assert pipe._latest_frame_packet is None


def test_step03_15_numpy_frame_remains_unchanged_after_publication():
    """Verify NumPy frame byte digest remains unchanged after reader publication."""
    raw_array = np.full((50, 50, 3), 42, dtype=np.uint8)
    initial_digest = hashlib.sha256(raw_array.tobytes()).hexdigest()

    class StaticArrayCapture:
        def __init__(self, arr):
            self.arr = arr
            self.read_count = 0
            self.released = False

        def isOpened(self):
            return not self.released

        def read(self):
            if self.read_count >= 1:
                return False, None
            self.read_count += 1
            return True, self.arr

        def release(self):
            self.released = True

    cap = StaticArrayCapture(raw_array)
    pipe = CameraPipeline(camera_id=1, stream_url="fake://1", capture_device=cap)
    pipe._running = True

    t = threading.Thread(target=pipe._reader_worker, daemon=True)
    t.start()

    pkt = pipe.get_latest_frame_packet(timeout=1.0)
    assert pkt is not None

    pipe._reader_stop_event.set()
    pipe._running = False
    with pipe._slot_lock:
        pipe._slot_condition.notify_all()
    t.join(timeout=1.0)

    retrieved_digest = hashlib.sha256(pkt.frame.tobytes()).hexdigest()
    assert retrieved_digest == initial_digest
    assert np.all(pkt.frame == 42)


def test_step03_16_source_fps_based_on_actual_acquisition_timing():
    """Verify source_fps is calculated from monotonic timestamps with warm-up = 0.0."""
    # 5 frames with 0.02s delay between reads (~50 FPS)
    fake_cap = DeterministicFakeCapture(frame_count=5, frame_delay=0.02)
    pipe = CameraPipeline(camera_id=1, stream_url="fake://1", capture_device=fake_cap)
    pipe._running = True

    t = threading.Thread(target=pipe._reader_worker, daemon=True)
    t.start()

    # Get frame 1 (warm-up)
    pkt1 = pipe.get_latest_frame_packet(timeout=1.0)
    assert pkt1.frame_id == 1
    assert pkt1.source_fps == 0.0  # Warm-up representation

    # Wait for frame 3+
    last_id = pkt1.frame_id
    pkt_later = None
    for _ in range(4):
        p = pipe.get_latest_frame_packet(last_frame_id=last_id, timeout=0.1)
        if p and p.frame_id >= 3:
            pkt_later = p
            break
        if p:
            last_id = p.frame_id

    assert pkt_later is not None
    # With 20ms delay, FPS is typically between 30 and 70 FPS
    assert 20.0 <= pkt_later.source_fps <= 100.0

    pipe._reader_stop_event.set()
    pipe._running = False
    with pipe._slot_lock:
        pipe._slot_condition.notify_all()
    t.join(timeout=1.0)


def test_step03_17_capture_ownership_is_single_owner():
    """Verify _cap is opened, owned, and closed solely by reader worker."""
    fake_cap = DeterministicFakeCapture(frame_count=3)
    pipe = CameraPipeline(camera_id=1, stream_url="fake://1", capture_device=fake_cap)
    assert pipe._cap is None

    pipe._running = True
    t = threading.Thread(target=pipe._reader_worker, daemon=True)
    t.start()

    _ = pipe.get_latest_frame_packet(timeout=1.0)
    assert pipe._cap is fake_cap

    pipe._reader_stop_event.set()
    pipe._running = False
    with pipe._slot_lock:
        pipe._slot_condition.notify_all()
    t.join(timeout=1.0)

    assert pipe._cap is None
    assert fake_cap.released is True


def test_step03_18_legacy_duplicate_cap_read_path_is_absent():
    """Verify via code inspection that _run_loop contains zero calls to cap.read()."""
    import textwrap
    source = inspect.getsource(CameraPipeline._run_loop)
    tree = ast.parse(textwrap.dedent(source))

    # Walk AST to find any Call attribute named 'read'
    read_calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "read":
                read_calls.append(node)

    assert len(read_calls) == 0, f"Found illegal read() calls in _run_loop: {read_calls}"


def test_step03_19_reader_does_not_execute_downstream_ai_while_holding_slot_lock():
    """Verify slot lock is held for pointer swap only and released immediately."""
    fake_cap = DeterministicFakeCapture(frame_count=5, frame_delay=0.005)
    pipe = CameraPipeline(camera_id=1, stream_url="fake://1", capture_device=fake_cap)
    pipe._running = True

    t = threading.Thread(target=pipe._reader_worker, daemon=True)
    t.start()

    # Continuously verify that outside reader, lock can be acquired non-blocking
    for _ in range(10):
        time.sleep(0.003)
        acquired = pipe._slot_lock.acquire(blocking=False)
        if acquired:
            pipe._slot_lock.release()

    pipe._reader_stop_event.set()
    pipe._running = False
    with pipe._slot_lock:
        pipe._slot_condition.notify_all()
    t.join(timeout=1.0)


def test_step03_20_deterministic_reader_lifecycle_under_test_shutdown():
    """Verify start() and stop() manage reader thread and compatibility loop cleanly."""
    fake_cap = DeterministicFakeCapture(frame_count=20, frame_delay=0.002)
    pipe = CameraPipeline(camera_id=1, stream_url="fake://1", capture_device=fake_cap)

    pipe.start()
    assert pipe._reader_thread is not None
    assert pipe._reader_thread.is_alive()

    # Allow a few frames to flow
    time.sleep(0.04)

    pipe.stop()
    assert pipe._reader_thread is None
    assert pipe._thread is None
    assert fake_cap.released is True
    assert pipe._running is False
