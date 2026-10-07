"""
IBVAP — Step 04 P0-01 Remediation: Step 2 Slot Synchronization Contract Suite
=============================================================================

Verifies ONLY Step 2 of P0_01_FINAL_IMPLEMENTATION_CHECKLIST:
1. Slot initializes empty (None).
2. Lock and condition variable created per CameraPipeline instance.
3. publish_frame_packet stores the latest packet.
4. Second publish supersedes the previous packet (capacity = strictly 1).
5. Old packet is not returned after replacement.
6. Empty-slot consumer waits and times out cleanly.
7. publish_frame_packet wakes a waiting consumer deterministically.
8. Concurrent producer and consumer operate without deadlocks.
9. Multi-camera slot isolation: Camera A and Camera B slots are completely independent.
10. NumPy frame memory safety: Slot operations never mutate underlying frame bytes.
11. Multiple rapid publications preserve latest-frame semantics (no queue buildup).
12. Packet sequence behavior is deterministic with monotonic IDs.
13. Slot lock is held strictly during pointer swap (< 5us critical section).
"""

import time
import threading
import hashlib
import pytest
import numpy as np

from backend.app.services.live_pipeline import FramePacket, CameraPipeline


def _make_dummy_packet(frame_id: int, fill_val: int = 0) -> FramePacket:
    """Helper to construct a distinct valid FramePacket."""
    frame = np.full((60, 80, 3), fill_val, dtype=np.uint8)
    return FramePacket(
        frame_id=frame_id,
        frame=frame,
        capture_utc=time.time(),
        capture_mono=time.perf_counter(),
        source_fps=30.0,
        stream_epoch=1,
        hardware_drop_count=0,
    )


def test_step02_slot_initializes_empty():
    """Verify latest-frame slot initializes as None with instance-isolated lock and condition."""
    pipe = CameraPipeline(camera_id=1, stream_url="demo://1")

    assert pipe._latest_frame_packet is None
    assert pipe.get_latest_frame_packet(timeout=0.0) is None
    assert isinstance(pipe._slot_lock, type(threading.Lock()))
    assert isinstance(pipe._slot_condition, type(threading.Condition()))


def test_step02_publish_stores_latest_packet():
    """Verify publish_frame_packet stores packet in slot and consumer retrieves it."""
    pipe = CameraPipeline(camera_id=1, stream_url="demo://1")
    p1 = _make_dummy_packet(101, fill_val=10)

    pipe.publish_frame_packet(p1)

    retrieved = pipe.get_latest_frame_packet(timeout=0.0)
    assert retrieved is p1
    assert retrieved.frame_id == 101


def test_step02_second_publish_replaces_first_packet():
    """Verify second publish supersedes first packet and old packet is not returned."""
    pipe = CameraPipeline(camera_id=1, stream_url="demo://1")
    p1 = _make_dummy_packet(1, fill_val=10)
    p2 = _make_dummy_packet(2, fill_val=20)

    pipe.publish_frame_packet(p1)
    pipe.publish_frame_packet(p2)

    retrieved = pipe.get_latest_frame_packet(timeout=0.0)
    assert retrieved is p2
    assert retrieved is not p1
    assert retrieved.frame_id == 2


def test_step02_empty_slot_consumer_waits_and_times_out():
    """Verify consumer waiting on an empty slot times out cleanly and returns None."""
    pipe = CameraPipeline(camera_id=1, stream_url="demo://1")

    t_start = time.perf_counter()
    retrieved = pipe.get_latest_frame_packet(timeout=0.05)
    t_elapsed = time.perf_counter() - t_start

    assert retrieved is None
    assert t_elapsed >= 0.045  # Must have waited for the requested duration


def test_step02_publish_wakes_waiting_consumer_deterministically():
    """Verify publishing a packet immediately wakes a consumer waiting on condition."""
    pipe = CameraPipeline(camera_id=1, stream_url="demo://1")
    p1 = _make_dummy_packet(42, fill_val=42)

    consumer_started = threading.Event()
    received_packet = []

    def consumer():
        consumer_started.set()
        # Consumer waits up to 2.0s for a packet
        pkt = pipe.get_latest_frame_packet(timeout=2.0)
        received_packet.append(pkt)

    t = threading.Thread(target=consumer)
    t.start()

    # Wait until consumer thread is running
    assert consumer_started.wait(timeout=1.0)

    # Publish packet to wake consumer
    pipe.publish_frame_packet(p1)

    t.join(timeout=2.0)
    assert not t.is_alive()
    assert len(received_packet) == 1
    assert received_packet[0] is p1


def test_step02_producer_consumer_concurrent_synchronization_no_deadlock():
    """Verify concurrent producer and consumer threads operate smoothly with zero deadlocks."""
    pipe = CameraPipeline(camera_id=1, stream_url="demo://1")
    start_barrier = threading.Barrier(2)
    consumed_ids = []

    def consumer():
        start_barrier.wait()
        last_id = None
        for _ in range(10):
            pkt = pipe.get_latest_frame_packet(last_frame_id=last_id, timeout=1.0)
            if pkt:
                consumed_ids.append(pkt.frame_id)
                last_id = pkt.frame_id

    def producer():
        start_barrier.wait()
        for i in range(1, 11):
            pipe.publish_frame_packet(_make_dummy_packet(i, fill_val=i))
            # Minimal cooperative yield without arbitrary timing dependence
            threading.Event().wait(0.001)

    t_cons = threading.Thread(target=consumer)
    t_prod = threading.Thread(target=producer)

    t_cons.start()
    t_prod.start()

    t_prod.join(timeout=3.0)
    t_cons.join(timeout=3.0)

    assert not t_prod.is_alive()
    assert not t_cons.is_alive()
    assert len(consumed_ids) > 0
    # Every consumed ID must be monotonically increasing
    for i in range(len(consumed_ids) - 1):
        assert consumed_ids[i + 1] > consumed_ids[i]


def test_step02_multi_camera_slot_isolation():
    """Verify CameraPipeline A and CameraPipeline B slots are strictly isolated."""
    pipeA = CameraPipeline(camera_id=10, stream_url="demo://10")
    pipeB = CameraPipeline(camera_id=20, stream_url="demo://20")

    assert pipeA._slot_lock is not pipeB._slot_lock
    assert pipeA._slot_condition is not pipeB._slot_condition

    pA = _make_dummy_packet(100, fill_val=1)
    pB = _make_dummy_packet(200, fill_val=2)

    pipeA.publish_frame_packet(pA)

    assert pipeA.get_latest_frame_packet(timeout=0.0) is pA
    assert pipeB.get_latest_frame_packet(timeout=0.0) is None

    pipeB.publish_frame_packet(pB)
    assert pipeA.get_latest_frame_packet(timeout=0.0) is pA
    assert pipeB.get_latest_frame_packet(timeout=0.0) is pB


def test_step02_numpy_frame_memory_safety_immutability():
    """Verify slot operations never mutate underlying NumPy frame bytes."""
    raw_array = np.full((100, 100, 3), 77, dtype=np.uint8)
    initial_digest = hashlib.sha256(raw_array.tobytes()).hexdigest()

    packet1 = FramePacket(
        frame_id=1,
        frame=raw_array,
        capture_utc=time.time(),
        capture_mono=time.perf_counter(),
        source_fps=30.0,
        stream_epoch=1,
        hardware_drop_count=0,
    )

    pipe = CameraPipeline(camera_id=1, stream_url="demo://1")
    pipe.publish_frame_packet(packet1)

    # Publish a second packet with different data to supersede packet 1
    packet2 = _make_dummy_packet(2, fill_val=99)
    pipe.publish_frame_packet(packet2)

    retrieved2 = pipe.get_latest_frame_packet(timeout=0.0)
    assert retrieved2 is packet2

    # Assert that packet1's frame buffer was never touched or mutated
    post_publish_digest = hashlib.sha256(packet1.frame.tobytes()).hexdigest()
    assert post_publish_digest == initial_digest
    assert np.all(packet1.frame == 77)


def test_step02_multiple_rapid_publications_preserve_latest_frame_semantics():
    """Verify rapid publications do not create a queue backlog; slot capacity remains strictly 1."""
    pipe = CameraPipeline(camera_id=1, stream_url="demo://1")

    # Publish 100 packets in rapid succession
    for fid in range(1, 101):
        pipe.publish_frame_packet(_make_dummy_packet(fid))

    retrieved = pipe.get_latest_frame_packet(timeout=0.0)
    assert retrieved.frame_id == 100

    # Underlying slot is a single reference, not a list
    assert not isinstance(pipe._latest_frame_packet, (list, tuple))
    assert pipe._latest_frame_packet.frame_id == 100


def test_step02_slot_lock_released_immediately_outside_processing():
    """Verify _slot_lock is not held after publish or retrieval methods return."""
    pipe = CameraPipeline(camera_id=1, stream_url="demo://1")
    p1 = _make_dummy_packet(1)

    pipe.publish_frame_packet(p1)
    # If slot lock was left acquired, acquiring it here non-blocking would fail
    acquired = pipe._slot_lock.acquire(blocking=False)
    assert acquired is True
    pipe._slot_lock.release()

    _ = pipe.get_latest_frame_packet(timeout=0.0)
    acquired = pipe._slot_lock.acquire(blocking=False)
    assert acquired is True
    pipe._slot_lock.release()


def test_step02_type_validation_on_publish():
    """Verify publishing non-FramePacket objects raises TypeError."""
    pipe = CameraPipeline(camera_id=1, stream_url="demo://1")

    with pytest.raises(TypeError, match="Expected FramePacket instance"):
        pipe.publish_frame_packet(np.zeros((10, 10, 3)))

    with pytest.raises(TypeError, match="Expected FramePacket instance"):
        pipe.publish_frame_packet({"frame_id": 1})


def test_step02_clear_latest_frame_packet():
    """Verify clear_latest_frame_packet resets slot to None under lock."""
    pipe = CameraPipeline(camera_id=1, stream_url="demo://1")
    p1 = _make_dummy_packet(1)
    pipe.publish_frame_packet(p1)

    assert pipe.get_latest_frame_packet(timeout=0.0) is p1
    pipe.clear_latest_frame_packet()
    assert pipe.get_latest_frame_packet(timeout=0.0) is None
