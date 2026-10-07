"""
IBVAP — Step 04 P0-01 Remediation: Step 4 Stream Reconnect Loop & Backoff Suite
================================================================================

Verifies ONLY Step 4 of P0_01_FINAL_IMPLEMENTATION_CHECKLIST:
1. isolated read failure does not create reconnect storm
2. consecutive failure threshold triggers reconnect
3. failed reconnect enters bounded backoff
4. successful reconnect increments stream_epoch exactly once
5. multiple failed reconnect attempts do not increment epoch
6. successful reconnect resumes frame publication
7. failed reconnect publishes no invalid packet
8. old capture is released before replacement
9. only one capture is active per CameraPipeline
10. stop during backoff terminates promptly
11. stop during reconnect prevents further publication
12. no post-stop reconnect
13. frame IDs remain monotonic
14. source FPS resets/warmups correctly after reconnect
15. latest-frame slot never becomes an unbounded queue
16. multi-camera reconnect isolation
17. reconnect telemetry is accurate
18. camera health reflects read failure/recovery without fake healthy frames
19. deterministic backoff timing logic
20. no duplicate reader worker is created
21. reconnect validation requires a genuinely readable capture
22. reconnect failure does not increment stream_epoch
23. successful recovery publishes exactly the appropriate frames
24. no stale packet is misclassified as a newly captured frame
"""

import time
import threading
import pytest
import numpy as np

from backend.app.services.live_pipeline import (
    FramePacket,
    CameraPipeline,
    RECONNECT_STATE_CONNECTED,
    RECONNECT_STATE_READ_FAILURE,
    RECONNECT_STATE_BACKOFF,
    RECONNECT_STATE_RECONNECTING,
    RECONNECT_STATE_RECOVERED,
    RECONNECT_STATE_STOPPED,
)


class MockCapture:
    """Configurable mock VideoCapture for deterministic reconnect tests."""

    def __init__(
        self,
        name: str = "cap",
        frames: int = 5,
        fail_after: int = -1,
        can_open: bool = True,
        can_read: bool = True,
        frame_delay: float = 0.001,
        fill_val: int = 1,
    ):
        self.name = name
        self.total_frames = frames
        self.fail_after = fail_after
        self.can_open = can_open
        self.can_read = can_read
        self.frame_delay = frame_delay
        self.fill_val = fill_val

        self.frames_read = 0
        self.read_calls = 0
        self.released = False
        self._lock = threading.Lock()

    def isOpened(self) -> bool:
        with self._lock:
            return self.can_open and not self.released

    def read(self):
        with self._lock:
            self.read_calls += 1
            if not self.can_open or self.released or not self.can_read:
                return False, None
            if self.frame_delay > 0:
                time.sleep(self.frame_delay)
            if self.fail_after >= 0 and self.frames_read >= self.fail_after:
                return False, None
            if self.frames_read >= self.total_frames:
                return False, None

            self.frames_read += 1
            frame = np.full((60, 80, 3), (self.fill_val + self.frames_read) % 255, dtype=np.uint8)
            return True, frame

    def release(self):
        with self._lock:
            self.released = True


def test_step04_01_isolated_read_failure_does_not_create_reconnect_storm():
    """Verify an isolated transient read failure does not trigger reconnect or increment attempts."""
    class IntermittentCapture:
        def __init__(self):
            self.calls = 0
            self.released = False

        def isOpened(self):
            return not self.released

        def read(self):
            self.calls += 1
            if self.calls == 2:  # Call #2 fails intermittently
                return False, None
            time.sleep(0.005)
            return True, np.zeros((10, 10, 3), dtype=np.uint8)

        def release(self):
            self.released = True

    cap = IntermittentCapture()
    pipe = CameraPipeline(
        camera_id=1,
        stream_url="fake://1",
        capture_device=cap,
        consecutive_failure_threshold=3,
        initial_backoff=0.01,
    )
    pipe._running = True

    t = threading.Thread(target=pipe._reader_worker, daemon=True)
    t.start()

    time.sleep(0.04)

    pipe._reader_stop_event.set()
    pipe._running = False
    with pipe._slot_lock:
        pipe._slot_condition.notify_all()
    t.join(timeout=1.0)

    # Reconnect must NOT have been triggered for single isolated failure
    assert pipe.reader_read_failures >= 1
    assert pipe.consecutive_read_failures == 0 or pipe.consecutive_read_failures < 3
    assert pipe.reconnect_attempts == 0
    assert pipe.reconnect_events == 0
    assert pipe.stream_epoch == 1


def test_step04_02_consecutive_failure_threshold_triggers_reconnect():
    """Verify crossing consecutive_failure_threshold triggers RECONNECTING state."""
    # Fails after 2 successful frames
    cap = MockCapture(name="cap1", frames=2, fail_after=2)
    pipe = CameraPipeline(
        camera_id=1,
        stream_url="fake://1",
        capture_device=cap,
        consecutive_failure_threshold=3,
        initial_backoff=0.05,
    )
    pipe._running = True

    t = threading.Thread(target=pipe._reader_worker, daemon=True)
    t.start()

    # Wait until consecutive failures cross threshold and enter reconnecting
    for _ in range(30):
        if pipe.reconnect_state in (RECONNECT_STATE_BACKOFF, RECONNECT_STATE_RECONNECTING):
            break
        time.sleep(0.01)

    assert pipe.consecutive_read_failures >= 3
    assert pipe.reconnect_state in (RECONNECT_STATE_BACKOFF, RECONNECT_STATE_RECONNECTING)
    assert cap.released is True  # Stale capture released

    pipe._reader_stop_event.set()
    pipe._running = False
    with pipe._slot_lock:
        pipe._slot_condition.notify_all()
    t.join(timeout=1.0)


def test_step04_03_failed_reconnect_enters_bounded_backoff():
    """Verify failed reconnect attempts enter BACKOFF and increment reconnect_failures."""
    # Initial capture fails immediately, no new capture can be opened
    cap = MockCapture(name="dead", can_open=False)
    pipe = CameraPipeline(
        camera_id=1,
        stream_url="fake://1",
        capture_device=cap,
        initial_backoff=0.01,
        maximum_backoff=0.03,
        backoff_multiplier=2.0,
    )
    pipe._running = True

    t = threading.Thread(target=pipe._reader_worker, daemon=True)
    t.start()

    # Allow several reconnect attempts to fail
    time.sleep(0.08)

    pipe._reader_stop_event.set()
    pipe._running = False
    with pipe._slot_lock:
        pipe._slot_condition.notify_all()
    t.join(timeout=1.0)

    assert pipe.reconnect_attempts >= 2
    assert pipe.reconnect_failures >= 2
    assert pipe.stream_epoch == 1  # Epoch never advanced on failure


def test_step04_04_successful_reconnect_increments_stream_epoch_exactly_once():
    """Verify successful reconnect increments stream_epoch by exactly 1 and sets timestamp."""
    cap1 = MockCapture(name="cap1", frames=2, fail_after=2)
    cap2 = MockCapture(name="cap2", frames=50, fill_val=50, frame_delay=0.005)

    caps = [cap1, cap2]
    def factory():
        return caps.pop(0) if caps else MockCapture(name="empty", frames=0)

    pipe = CameraPipeline(
        camera_id=1,
        stream_url="fake://1",
        capture_factory=factory,
        consecutive_failure_threshold=2,
        initial_backoff=0.01,
    )
    pipe._running = True

    t = threading.Thread(target=pipe._reader_worker, daemon=True)
    t.start()

    # Wait until reconnect event occurs
    for _ in range(30):
        if pipe.reconnect_events >= 1:
            break
        time.sleep(0.01)

    assert pipe.reconnect_events == 1
    assert pipe.stream_epoch == 2  # Exactly 1 increment: 1 -> 2
    assert pipe.last_reconnect_timestamp is not None
    assert pipe.reconnect_state in (RECONNECT_STATE_CONNECTED, RECONNECT_STATE_RECOVERED)

    pipe._reader_stop_event.set()
    pipe._running = False
    with pipe._slot_lock:
        pipe._slot_condition.notify_all()
    t.join(timeout=1.0)


def test_step04_05_multiple_failed_reconnect_attempts_do_not_increment_epoch():
    """Verify multiple failing reconnect attempts do NOT increment stream_epoch before success."""
    cap1 = MockCapture(name="cap1", frames=1, fail_after=1)
    fail_cap1 = MockCapture(name="f1", can_open=False)
    fail_cap2 = MockCapture(name="f2", can_open=False)
    cap_success = MockCapture(name="success", frames=5, fill_val=88)

    caps = [cap1, fail_cap1, fail_cap2, cap_success]
    def factory():
        return caps.pop(0) if caps else None

    pipe = CameraPipeline(
        camera_id=1,
        stream_url="fake://1",
        capture_factory=factory,
        consecutive_failure_threshold=1,
        initial_backoff=0.01,
    )
    pipe._running = True

    t = threading.Thread(target=pipe._reader_worker, daemon=True)
    t.start()

    for _ in range(40):
        if pipe.reconnect_events >= 1:
            break
        time.sleep(0.01)

    assert pipe.reconnect_attempts >= 3
    assert pipe.reconnect_failures >= 2
    assert pipe.reconnect_events == 1
    assert pipe.stream_epoch == 2  # Advanced only once upon final success!

    pipe._reader_stop_event.set()
    pipe._running = False
    with pipe._slot_lock:
        pipe._slot_condition.notify_all()
    t.join(timeout=1.0)


def test_step04_06_successful_reconnect_resumes_frame_publication():
    """Verify consumer receives frames from new stream epoch after reconnect."""
    cap1 = MockCapture(name="cap1", frames=1, fail_after=1, fill_val=10)
    cap2 = MockCapture(name="cap2", frames=4, fill_val=70)

    caps = [cap1, cap2]
    pipe = CameraPipeline(
        camera_id=1,
        stream_url="fake://1",
        capture_factory=lambda: caps.pop(0) if caps else None,
        consecutive_failure_threshold=1,
        initial_backoff=0.01,
    )
    pipe._running = True

    t = threading.Thread(target=pipe._reader_worker, daemon=True)
    t.start()

    # Wait for epoch 2 frame
    pkt_epoch2 = None
    for _ in range(30):
        pkt = pipe.get_latest_frame_packet(timeout=0.05)
        if pkt and pkt.stream_epoch == 2:
            pkt_epoch2 = pkt
            break
        time.sleep(0.01)

    assert pkt_epoch2 is not None
    assert pkt_epoch2.stream_epoch == 2
    assert pkt_epoch2.frame[0, 0, 0] >= 70

    pipe._reader_stop_event.set()
    pipe._running = False
    with pipe._slot_lock:
        pipe._slot_condition.notify_all()
    t.join(timeout=1.0)


def test_step04_07_failed_reconnect_publishes_no_invalid_packet():
    """Verify failed reconnect attempts never publish None or corrupted packets to slot."""
    cap_bad = MockCapture(name="bad", can_open=False)
    pipe = CameraPipeline(
        camera_id=1,
        stream_url="fake://1",
        capture_device=cap_bad,
        consecutive_failure_threshold=1,
        initial_backoff=0.01,
    )
    pipe._running = True

    t = threading.Thread(target=pipe._reader_worker, daemon=True)
    t.start()

    time.sleep(0.05)

    with pipe._slot_lock:
        # If no frame ever succeeded, slot remains None
        assert pipe._latest_frame_packet is None

    pipe._reader_stop_event.set()
    pipe._running = False
    with pipe._slot_lock:
        pipe._slot_condition.notify_all()
    t.join(timeout=1.0)


def test_step04_08_old_capture_is_released_before_replacement():
    """Verify stale capture is released BEFORE new capture is opened."""
    cap1 = MockCapture(name="cap1", frames=1, fail_after=1)
    cap1_released_at_open = []

    class TrackedCap2(MockCapture):
        def __init__(self):
            super().__init__(name="cap2", frames=3)
            # Record if cap1 was already released when cap2 is instantiated/opened
            cap1_released_at_open.append(cap1.released)

    def factory():
        if not cap1.released and cap1.frames_read == 0:
            return cap1
        return TrackedCap2()

    pipe = CameraPipeline(
        camera_id=1,
        stream_url="fake://1",
        capture_factory=factory,
        consecutive_failure_threshold=1,
        initial_backoff=0.01,
    )
    pipe._running = True

    t = threading.Thread(target=pipe._reader_worker, daemon=True)
    t.start()

    for _ in range(30):
        if pipe.reconnect_events >= 1:
            break
        time.sleep(0.01)

    assert len(cap1_released_at_open) >= 1
    assert cap1_released_at_open[0] is True  # cap1 was released before cap2 opened!

    pipe._reader_stop_event.set()
    pipe._running = False
    with pipe._slot_lock:
        pipe._slot_condition.notify_all()
    t.join(timeout=1.0)


def test_step04_09_only_one_capture_is_active_per_pipeline():
    """Verify at no point are two VideoCapture instances active simultaneously."""
    active_instances = set()
    concurrency_violation = []

    class AuditedCapture(MockCapture):
        def __init__(self, name):
            super().__init__(name=name, frames=2, fail_after=2)
            if len(active_instances) > 0:
                concurrency_violation.append(f"Multiple active captures: {active_instances}")
            active_instances.add(self.name)

        def release(self):
            super().release()
            active_instances.discard(self.name)

    names = ["c1", "c2"]
    pipe = CameraPipeline(
        camera_id=1,
        stream_url="fake://1",
        capture_factory=lambda: AuditedCapture(names.pop(0)) if names else None,
        consecutive_failure_threshold=1,
        initial_backoff=0.01,
    )
    pipe._running = True

    t = threading.Thread(target=pipe._reader_worker, daemon=True)
    t.start()

    time.sleep(0.06)

    pipe._reader_stop_event.set()
    pipe._running = False
    with pipe._slot_lock:
        pipe._slot_condition.notify_all()
    t.join(timeout=1.0)

    assert len(concurrency_violation) == 0, f"Violations: {concurrency_violation}"


def test_step04_10_stop_during_backoff_terminates_promptly():
    """Verify stop() during a long backoff wakes interruptible sleep and exits immediately."""
    cap = MockCapture(name="dead", can_open=False)
    # Long backoff: 20 seconds!
    pipe = CameraPipeline(
        camera_id=1,
        stream_url="fake://1",
        capture_device=cap,
        consecutive_failure_threshold=1,
        initial_backoff=20.0,
    )
    pipe._running = True

    t = threading.Thread(target=pipe._reader_worker, daemon=True)
    t.start()

    # Wait until thread enters BACKOFF state
    for _ in range(20):
        if pipe.reconnect_state == RECONNECT_STATE_BACKOFF:
            break
        time.sleep(0.01)

    assert pipe.reconnect_state == RECONNECT_STATE_BACKOFF

    # Now stop the pipeline — must interrupt immediately
    t_start = time.perf_counter()
    pipe.stop()
    t_elapsed = time.perf_counter() - t_start

    t.join(timeout=1.0)
    assert not t.is_alive()
    assert t_elapsed < 0.8  # Must terminate in sub-second, not 20 seconds!
    assert pipe.reconnect_state == RECONNECT_STATE_STOPPED


def test_step04_11_stop_during_reconnect_prevents_further_publication():
    """Verify signaling stop while in reconnect prevents publication of any frame."""
    def blocking_factory():
        # Stop pipe right before returning
        pipe.stop()
        return MockCapture(name="late", frames=5)

    pipe = CameraPipeline(
        camera_id=1,
        stream_url="fake://1",
        capture_device=MockCapture(name="init_dead", can_open=False),
        capture_factory=blocking_factory,
        consecutive_failure_threshold=1,
        initial_backoff=0.01,
    )
    pipe._running = True

    t = threading.Thread(target=pipe._reader_worker, daemon=True)
    t.start()

    t.join(timeout=1.0)
    assert not t.is_alive()
    assert pipe.reader_frames_acquired == 0
    assert pipe._latest_frame_packet is None


def test_step04_12_no_post_stop_reconnect():
    """Verify that once stopped, no further reconnect attempts are scheduled."""
    pipe = CameraPipeline(
        camera_id=1,
        stream_url="fake://1",
        capture_device=MockCapture(name="dead", can_open=False),
        consecutive_failure_threshold=1,
        initial_backoff=0.01,
    )
    pipe._running = True
    t = threading.Thread(target=pipe._reader_worker, daemon=True)
    t.start()

    time.sleep(0.03)
    pipe.stop()
    t.join(timeout=1.0)

    last_attempts = pipe.reconnect_attempts
    time.sleep(0.04)

    assert pipe.reconnect_attempts == last_attempts
    assert pipe.reconnect_state == RECONNECT_STATE_STOPPED


def test_step04_13_frame_ids_remain_monotonic():
    """Verify frame IDs maintain strict monotonically increasing order across stream reconnects."""
    cap1 = MockCapture(name="cap1", frames=2, fail_after=2)
    cap2 = MockCapture(name="cap2", frames=3)

    caps = [cap1, cap2]
    pipe = CameraPipeline(
        camera_id=1,
        stream_url="fake://1",
        capture_factory=lambda: caps.pop(0) if caps else None,
        consecutive_failure_threshold=1,
        initial_backoff=0.01,
    )
    pipe._running = True

    t = threading.Thread(target=pipe._reader_worker, daemon=True)
    t.start()

    consumed_ids = []
    last_id = None
    for _ in range(10):
        pkt = pipe.get_latest_frame_packet(last_frame_id=last_id, timeout=0.05)
        if pkt:
            consumed_ids.append(pkt.frame_id)
            last_id = pkt.frame_id
        time.sleep(0.01)

    pipe.stop()
    t.join(timeout=1.0)

    assert len(consumed_ids) >= 3
    for i in range(len(consumed_ids) - 1):
        assert consumed_ids[i + 1] > consumed_ids[i]


def test_step04_14_source_fps_resets_warmup_after_reconnect():
    """Verify source FPS window is cleared upon reconnect; first post-reconnect frame has source_fps=0.0."""
    cap1 = MockCapture(name="cap1", frames=5, frame_delay=0.01, fail_after=5)
    cap2 = MockCapture(name="cap2", frames=5, frame_delay=0.01)

    caps = [cap1, cap2]
    pipe = CameraPipeline(
        camera_id=1,
        stream_url="fake://1",
        capture_factory=lambda: caps.pop(0) if caps else None,
        consecutive_failure_threshold=1,
        initial_backoff=0.01,
    )
    pipe._running = True

    t = threading.Thread(target=pipe._reader_worker, daemon=True)
    t.start()

    # Wait for epoch 2 validation frame
    pkt_epoch2_first = None
    for _ in range(30):
        pkt = pipe.get_latest_frame_packet(timeout=0.05)
        if pkt and pkt.stream_epoch == 2:
            pkt_epoch2_first = pkt
            break
        time.sleep(0.01)

    assert pkt_epoch2_first is not None
    assert pkt_epoch2_first.stream_epoch == 2
    assert pkt_epoch2_first.source_fps == 0.0  # Warmup representation!

    pipe.stop()
    t.join(timeout=1.0)


def test_step04_15_latest_frame_slot_never_becomes_unbounded_queue():
    """Verify across reconnects and dropouts that the latest-frame slot maintains capacity 1."""
    cap1 = MockCapture(name="cap1", frames=10, fail_after=10)
    cap2 = MockCapture(name="cap2", frames=10)

    caps = [cap1, cap2]
    pipe = CameraPipeline(
        camera_id=1,
        stream_url="fake://1",
        capture_factory=lambda: caps.pop(0) if caps else None,
        consecutive_failure_threshold=1,
        initial_backoff=0.01,
    )
    pipe._running = True

    t = threading.Thread(target=pipe._reader_worker, daemon=True)
    t.start()

    time.sleep(0.08)

    with pipe._slot_lock:
        assert isinstance(pipe._latest_frame_packet, FramePacket)
        assert not isinstance(pipe._latest_frame_packet, (list, tuple))

    pipe.stop()
    t.join(timeout=1.0)


def test_step04_16_multi_camera_reconnect_isolation():
    """Verify Camera 1 reconnect cycle has zero side effects on Camera 2."""
    cap1_dead = MockCapture(name="c1_dead", can_open=False)
    cap2_good = MockCapture(name="c2_good", frames=10, frame_delay=0.002)

    pipe1 = CameraPipeline(
        camera_id=10,
        stream_url="fake://10",
        capture_device=cap1_dead,
        consecutive_failure_threshold=1,
        initial_backoff=0.01,
    )
    pipe2 = CameraPipeline(
        camera_id=20,
        stream_url="fake://20",
        capture_device=cap2_good,
    )

    pipe1._running = True
    pipe2._running = True

    t1 = threading.Thread(target=pipe1._reader_worker, daemon=True)
    t2 = threading.Thread(target=pipe2._reader_worker, daemon=True)

    t1.start()
    t2.start()

    time.sleep(0.05)

    pipe1.stop()
    pipe2.stop()

    t1.join(timeout=1.0)
    t2.join(timeout=1.0)

    # Pipe 1 experienced failures & reconnect attempts
    assert pipe1.reconnect_attempts >= 1
    assert pipe1.stream_epoch == 1  # Never recovered
    assert pipe1.reader_frames_acquired == 0

    # Pipe 2 streamed successfully with zero reconnect events
    assert pipe2.reconnect_attempts == 0
    assert pipe2.reconnect_events == 0
    assert pipe2.stream_epoch == 1
    assert pipe2.reader_frames_acquired >= 5


def test_step04_17_reconnect_telemetry_is_accurate():
    """Verify all reconnect telemetry counters record accurate values."""
    cap1 = MockCapture(name="cap1", frames=2, fail_after=2)
    cap2 = MockCapture(name="cap2", frames=20)

    caps = [cap1, cap2]
    pipe = CameraPipeline(
        camera_id=1,
        stream_url="fake://1",
        capture_factory=lambda: caps.pop(0) if caps else None,
        consecutive_failure_threshold=1,
        initial_backoff=0.01,
    )
    pipe._running = True

    t = threading.Thread(target=pipe._reader_worker, daemon=True)
    t.start()

    for _ in range(30):
        if pipe.reconnect_events == 1:
            break
        time.sleep(0.01)

    pipe.stop()
    t.join(timeout=1.0)

    assert pipe.reconnect_events == 1
    assert pipe.reconnect_attempts >= 1
    assert pipe.last_reconnect_timestamp is not None
    assert pipe.consecutive_read_failures == 0


def test_step04_18_camera_health_reflects_read_failure_without_fake_healthy_frames():
    """Verify read failures trigger offline health transitions without injecting fake frames."""
    cap = MockCapture(name="dead", can_open=False)
    pipe = CameraPipeline(
        camera_id=1,
        stream_url="fake://1",
        capture_device=cap,
        consecutive_failure_threshold=1,
        initial_backoff=0.01,
    )
    pipe._running = True

    t = threading.Thread(target=pipe._reader_worker, daemon=True)
    t.start()

    for _ in range(20):
        if pipe._consecutive_offline_reads >= 3:
            break
        time.sleep(0.01)

    assert pipe._consecutive_offline_reads >= 3
    assert pipe._health_state == "OFFLINE"

    pipe.stop()
    t.join(timeout=1.0)


def test_step04_19_deterministic_backoff_timing_logic():
    """Verify calculate_next_backoff adheres to exponential multiplier and max cap."""
    pipe = CameraPipeline(
        camera_id=1,
        stream_url="fake://1",
        initial_backoff=1.0,
        maximum_backoff=10.0,
        backoff_multiplier=2.0,
        backoff_jitter=0.0,
    )

    b1 = pipe.calculate_next_backoff(1.0)
    assert b1 == 2.0

    b2 = pipe.calculate_next_backoff(2.0)
    assert b2 == 4.0

    b3 = pipe.calculate_next_backoff(4.0)
    assert b3 == 8.0

    b4 = pipe.calculate_next_backoff(8.0)
    assert b4 == 10.0  # Capped at maximum_backoff

    b5 = pipe.calculate_next_backoff(10.0)
    assert b5 == 10.0


def test_step04_20_no_duplicate_reader_worker_is_created():
    """Verify only a single reader thread runs per CameraPipeline across reconnects."""
    pipe = CameraPipeline(
        camera_id=1,
        stream_url="fake://1",
        capture_device=MockCapture(name="dead", can_open=False),
        consecutive_failure_threshold=1,
        initial_backoff=0.01,
    )
    pipe.start()

    assert pipe._reader_thread is not None
    orig_thread = pipe._reader_thread

    time.sleep(0.05)

    # Thread identity must remain identical (same thread executing reconnect loop)
    assert pipe._reader_thread is orig_thread
    assert pipe._reader_thread.is_alive()

    pipe.stop()
    assert pipe._reader_thread is None


def test_step04_21_reconnect_validation_requires_genuinely_readable_capture():
    """Verify that a capture where isOpened() is True but read() fails is rejected."""
    # Capture reports open, but read() immediately fails
    unreadable_cap = MockCapture(name="unreadable", can_open=True, can_read=False)
    pipe = CameraPipeline(
        camera_id=1,
        stream_url="fake://1",
        capture_device=unreadable_cap,
        consecutive_failure_threshold=1,
        initial_backoff=0.01,
    )
    pipe._running = True

    t = threading.Thread(target=pipe._reader_worker, daemon=True)
    t.start()

    time.sleep(0.05)

    # Must have rejected unreadable capture and incremented reconnect_failures
    assert pipe.reconnect_failures >= 1
    assert pipe.reconnect_events == 0
    assert pipe.stream_epoch == 1
    assert unreadable_cap.released is True

    pipe.stop()
    t.join(timeout=1.0)


def test_step04_22_reconnect_failure_does_not_increment_stream_epoch():
    """Verify stream_epoch is strictly protected against incrementing on failures."""
    pipe = CameraPipeline(
        camera_id=1,
        stream_url="fake://1",
        capture_device=MockCapture(name="dead", can_open=False),
        consecutive_failure_threshold=1,
        initial_backoff=0.01,
    )
    pipe._running = True

    t = threading.Thread(target=pipe._reader_worker, daemon=True)
    t.start()

    time.sleep(0.06)

    assert pipe.reconnect_attempts >= 2
    assert pipe.stream_epoch == 1

    pipe.stop()
    t.join(timeout=1.0)


def test_step04_23_successful_recovery_publishes_validation_frame_without_duplication():
    """Verify validation frame is published as frame #1 of epoch and not re-read or duplicated."""
    cap1 = MockCapture(name="c1", frames=1, fail_after=1)
    cap2 = MockCapture(name="c2", frames=2, fill_val=99)

    caps = [cap1, cap2]
    pipe = CameraPipeline(
        camera_id=1,
        stream_url="fake://1",
        capture_factory=lambda: caps.pop(0) if caps else None,
        consecutive_failure_threshold=1,
        initial_backoff=0.01,
    )
    pipe._running = True

    t = threading.Thread(target=pipe._reader_worker, daemon=True)
    t.start()

    # Wait for recovery
    for _ in range(30):
        if pipe.reconnect_events == 1:
            break
        time.sleep(0.01)

    # Epoch 2 must have acquired exactly 2 frames total (1 validation + 1 normal)
    time.sleep(0.04)
    pipe.stop()
    t.join(timeout=1.0)

    assert pipe.reader_frames_acquired == 3  # 1 from cap1 + 2 from cap2
    assert cap2.read_calls == 3  # 1 validation + 1 second frame + 1 EOF check


def test_step04_24_no_stale_packet_misclassified_as_newly_captured_frame():
    """Verify monotonic last_frame_id and capture_mono timestamps prevent stale frame reuse."""
    cap = MockCapture(name="cap1", frames=2, fail_after=2)
    pipe = CameraPipeline(
        camera_id=1,
        stream_url="fake://1",
        capture_device=cap,
        consecutive_failure_threshold=1,
        initial_backoff=0.02,
    )
    pipe._running = True

    t = threading.Thread(target=pipe._reader_worker, daemon=True)
    t.start()

    # Fetch frame 2
    last_id = None
    for _ in range(10):
        pkt = pipe.get_latest_frame_packet(last_frame_id=last_id, timeout=0.05)
        if pkt:
            last_id = pkt.frame_id
            if pkt.frame_id == 2:
                break
        time.sleep(0.01)

    assert last_id == 2

    # Now camera is in outage. Consumer asking for new frame should NOT receive frame 2 again
    stale_check = pipe.get_latest_frame_packet(last_frame_id=last_id, timeout=0.02)
    assert stale_check is None

    pipe.stop()
    t.join(timeout=1.0)
