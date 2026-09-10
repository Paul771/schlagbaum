"""Tests for the bounded drop-oldest frame buffer (STREAM-04/05, D-09/D-10)."""

import queue
import threading

from src.capture.frame_buffer import Frame, FrameBuffer


def test_frame_pops_with_camera_id():
    fb = FrameBuffer("cam_1")
    fb.push(b"frame-data")
    frame = fb.pop()
    assert frame.camera_id == "cam_1"


def test_drop_oldest_keeps_buffer_bounded():
    fb = FrameBuffer("cam_1", maxsize=3)
    for i in range(5):
        fb.push(f"frame-{i}")
    assert fb.q.qsize() == 3
    # The newest frame survives; the oldest were dropped.
    frame = fb.pop()
    assert frame.data == "frame-2"


def test_push_never_blocks_when_full():
    fb = FrameBuffer("cam_1", maxsize=2)
    fb.push("a")
    fb.push("b")
    # Queue is full; push must not block and must not raise.
    fb.push("c")
    assert fb.q.qsize() == 2


def test_drop_oldest_under_race_never_raises_empty(frame_buffer):
    """Two producers racing a full queue must never raise queue.Empty."""
    errors = []

    def producer(prefix):
        try:
            for i in range(50):
                frame_buffer.push(f"{prefix}-{i}")
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=producer, args=(f"p{n}",)) for n in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert frame_buffer.q.qsize() <= frame_buffer.q.maxsize


def test_pop_returns_fifo_order():
    fb = FrameBuffer("cam_1", maxsize=5)
    for i in range(3):
        fb.push(f"frame-{i}")
    popped = [fb.pop().data for _ in range(3)]
    assert popped == ["frame-0", "frame-1", "frame-2"]


def test_frame_carries_all_attributes():
    fb = FrameBuffer("cam_1")
    fb.push(b"payload")
    frame = fb.pop()
    assert frame.camera_id == "cam_1"
    assert isinstance(frame.ts, float)
    assert frame.data == b"payload"


def test_buffers_with_different_camera_ids_are_isolated():
    fb1 = FrameBuffer("cam_1", maxsize=3)
    fb2 = FrameBuffer("cam_2", maxsize=3)
    fb1.push("a")
    fb2.push("b")
    assert fb1.pop().camera_id == "cam_1"
    assert fb2.pop().camera_id == "cam_2"
    # No cross-contamination: each buffer only holds its own frames.
    assert fb1.q.qsize() == 0
    assert fb2.q.qsize() == 0
