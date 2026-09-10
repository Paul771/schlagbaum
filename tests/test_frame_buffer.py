"""Tests for the bounded drop-oldest frame buffer (STREAM-04/05, D-09/D-10)."""

import queue

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
