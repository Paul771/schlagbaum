"""Bounded drop-oldest frame buffer with per-camera tagging.

Capture must never block on a slow downstream consumer. The bounded
drop-oldest queue is the architectural decoupling point (D-09/D-10): when the
queue is full, the oldest frame is dropped, never the newest. Every frame is
tagged with its ``camera_id`` at enqueue time (D-10).
"""

import queue
import time


class Frame:
    """A single captured frame carrying its camera tag (D-10)."""

    def __init__(self, camera_id, ts, data):
        self.camera_id = camera_id
        self.ts = ts
        self.data = data  # numpy BGR array from cv2.imdecode


class FrameBuffer:
    """A per-camera bounded drop-oldest queue of tagged frames."""

    def __init__(self, camera_id, maxsize=15):
        self.camera_id = camera_id
        self.q = queue.Queue(maxsize=maxsize)

    def push(self, data):
        """Non-blocking drop-oldest put (D-09). Tag with camera_id at enqueue (D-10)."""
        if self.q.full():
            try:
                self.q.get_nowait()  # drop oldest
            except queue.Empty:
                pass
        self.q.put(Frame(camera_id=self.camera_id, ts=time.time(), data=data))

    def pop(self):
        """Return the next frame. Consumer (Phase 2+) — never called by capture."""
        return self.q.get()
