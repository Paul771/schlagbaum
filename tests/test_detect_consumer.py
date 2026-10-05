"""Tests for the per-camera barrier consumer (plan 02-02 Task 2).

No network and no recordings: a fake frame buffer and a scripted stub detector
stand in for capture and for the real classifier. The consumer duck-types the
buffer, which is what keeps ``src/detect/`` free of the capture package — that
boundary is grep-gated by plan 02-01's verification.
"""

import queue
import threading
import time

import numpy as np
import pytest

from src.detect.barrier import ReferenceSet
from src.detect.consumer import (
    BarrierConsumer,
    authoritative_flags,
    build_consumers,
)
from src.detect.fsm import BarrierFSM, BarrierState, Observation

DRAIN_TIMEOUT = 5.0


class FakeFrame:
    def __init__(self, camera_id, data):
        self.camera_id = camera_id
        self.data = data


class FakeFrameBuffer:
    """Mirrors the real buffer's blocking pop(); no capture code involved."""

    def __init__(self, camera_id="cam_1"):
        self.camera_id = camera_id
        self.q = queue.Queue()

    def push(self, data):
        self.q.put(FakeFrame(self.camera_id, data))

    def pop(self):
        # Blocks forever when empty, exactly like the production buffer.
        return self.q.get()


class StubDetector:
    """Returns a scripted sequence of observations, then stays silent.

    ``classify`` mirrors the real signature: the second argument is the raw
    frame payload, not a frame object — the consumer unwraps ``frame.data``.
    """

    def __init__(self, plan=()):
        self.plan = list(plan)
        self.calls = []

    def classify(self, camera_id, data):
        self.calls.append((camera_id, data))
        if self.plan:
            return self.plan.pop(0)
        return Observation(BarrierState.UNKNOWN)


class ExplodingDetector(StubDetector):
    """Raises on selected payloads; used to prove the loop survives them."""

    def __init__(self, boom_on, plan=()):
        super().__init__(plan)
        self.boom_on = set(boom_on)

    def classify(self, camera_id, data):
        if data in self.boom_on:
            self.calls.append((camera_id, data))
            raise RuntimeError("synthetic classify failure")
        return super().classify(camera_id, data)


def open_sequence(count=3):
    """`count` OPEN observations: 1 to reach OPENING, dwell_open(2) more."""
    return [Observation(BarrierState.OPEN) for _ in range(count)]


def make_consumer(detector, camera_id="cam_1", emits_events=True,
                  dwell_open=2, dwell_closed=4):
    return BarrierConsumer(
        camera_id=camera_id,
        frame_buffer=FakeFrameBuffer(camera_id),
        detector=detector,
        fsm=BarrierFSM(dwell_open=dwell_open, dwell_closed=dwell_closed),
        stop_event=threading.Event(),
        emits_events=emits_events,
    )


def drain(consumer, payloads, timeout=DRAIN_TIMEOUT):
    """Push payloads, wait for them to be processed, then stop cleanly.

    The shutdown payload unblocks a consumer parked in pop(), and the loop's
    post-pop ``stop_event`` check means it is never counted as processed.
    """
    for payload in payloads:
        consumer.frame_buffer.push(payload)
    deadline = time.monotonic() + timeout
    while consumer.processed < len(payloads) and time.monotonic() < deadline:
        time.sleep(0.001)
    consumer.stop_event.set()
    consumer.frame_buffer.push(b"__shutdown__")
    consumer.join(timeout)
    assert consumer.processed == len(payloads), (
        "consumer stopped early: %d of %d frames" % (consumer.processed, len(payloads))
    )


def reference_set():
    """Minimal two-camera ReferenceSet, round-tripped through save()/load()."""
    blank = np.zeros((180, 320), dtype=np.uint8)
    maps = {}
    rois = {}
    for camera_id in ("cam_1", "cam_2"):
        maps[(camera_id, "day", "closed")] = blank
        maps[(camera_id, "day", "open")] = blank
        rois[(camera_id, "closed")] = (0, 0, 8, 8)
        rois[(camera_id, "open")] = (0, 0, 8, 8)
    return ReferenceSet(maps=maps, rois=rois)


def test_frames_are_drained_in_order_and_current_state_advances():
    detector = StubDetector(open_sequence(3))
    consumer = make_consumer(detector)
    consumer.start()
    drain(consumer, [b"a", b"b", b"c"])

    assert detector.calls == [("cam_1", b"a"), ("cam_1", b"b"), ("cam_1", b"c")]
    assert consumer.current_state is BarrierState.OPEN
    assert len(consumer.transitions) == 1
    assert consumer.transitions[0].emits_event is True
    assert consumer.errors == 0


def test_raising_detector_does_not_kill_the_loop():
    detector = ExplodingDetector(boom_on={b"b"}, plan=open_sequence(3))
    consumer = make_consumer(detector)
    consumer.start()
    drain(consumer, [b"a", b"b", b"c", b"d"])

    assert consumer.errors == 1
    assert consumer.processed == 4
    # frames queued behind the failure were still handed to the detector
    assert [payload for _camera, payload in detector.calls] == [b"a", b"b", b"c", b"d"]


def test_two_authoritative_cameras_are_rejected():
    """BARRIER-03: exactly one consumer may emit open events."""
    flags = authoritative_flags(["cam_1", "cam_2"], "cam_1")
    assert flags == {"cam_1": True, "cam_2": False}
    assert sum(flags.values()) == 1

    with pytest.raises(ValueError, match="authoritative"):
        authoritative_flags(["cam_1", "cam_2"], ["cam_1", "cam_2"])
    with pytest.raises(ValueError, match="authoritative"):
        authoritative_flags(["cam_1", "cam_2"], ["cam_1", "cam_2", "cam_3"])
    with pytest.raises(ValueError, match="authoritative"):
        authoritative_flags(["cam_1", "cam_2"], None)
    with pytest.raises(ValueError, match="not a configured camera"):
        authoritative_flags(["cam_1", "cam_2"], "cam_9")


def test_non_authoritative_camera_counts_but_never_emits():
    detector = StubDetector(open_sequence(3))
    consumer = make_consumer(detector, camera_id="cam_2", emits_events=False)
    consumer.start()
    drain(consumer, [b"a", b"b", b"c"])

    assert consumer.transitions == []          # nothing for Phase 3 to read
    assert consumer.diagnostic_opens == 1      # still visible as a health signal
    assert consumer.current_state is BarrierState.OPEN


def test_peer_disagreement_is_a_warning_not_an_event():
    authoritative = make_consumer(StubDetector(open_sequence(3)), emits_events=True)
    peer = make_consumer(StubDetector([]), camera_id="cam_2", emits_events=False)
    authoritative.attach_peer(peer)
    # peer never saw the opening, so it is still CLOSED when we commit OPEN
    authoritative.start()
    drain(authoritative, [b"a", b"b", b"c"])

    assert len(authoritative.transitions) == 1
    assert authoritative.cross_check == 1
    assert authoritative.disagreements == 1


def test_peer_agreement_is_counted_without_disagreement():
    authoritative = make_consumer(StubDetector(open_sequence(3)), emits_events=True)
    peer = make_consumer(StubDetector([]), camera_id="cam_2", emits_events=False)
    peer.current_state = BarrierState.OPEN    # sibling saw the same opening
    authoritative.attach_peer(peer)
    authoritative.start()
    drain(authoritative, [b"a", b"b", b"c"])

    assert len(authoritative.transitions) == 1
    assert authoritative.cross_check == 1
    assert authoritative.disagreements == 0


def test_build_consumers_reads_the_whole_barrier_block(tmp_path):
    reference_set().save(str(tmp_path))
    settings = {"barrier": {
        "enabled": True,
        "references_dir": str(tmp_path),
        "rois_file": str(tmp_path / "rois.json"),
        "margin": 0.07,
        "open_extent_ratio": 0.42,
        "dwell_open": 3,
        "dwell_closed": 5,
        "bucket_threshold": 55.0,
        "authoritative_camera": "cam_2",
    }}
    buffers = {"cam_1": FakeFrameBuffer("cam_1"), "cam_2": FakeFrameBuffer("cam_2")}
    consumers = build_consumers(settings, buffers, threading.Event())

    assert len(consumers) == 2
    by_id = {consumer.camera_id: consumer for consumer in consumers}
    assert sum(consumer.emits_events for consumer in consumers) == 1
    assert by_id["cam_2"].emits_events is True
    assert by_id["cam_1"].emits_events is False

    detector = by_id["cam_1"].detector
    assert detector.margin == pytest.approx(0.07)
    assert detector.open_extent_ratio == pytest.approx(0.42)
    assert detector.bucket_threshold == pytest.approx(55.0)
    assert by_id["cam_1"].fsm.dwell_open == 3
    assert by_id["cam_1"].fsm.dwell_closed == 5
    assert by_id["cam_1"].peer is by_id["cam_2"]
    assert by_id["cam_2"].peer is by_id["cam_1"]


def test_build_consumers_degrades_gracefully(tmp_path):
    """Detection never takes capture down: disabled or unloadable -> no threads."""
    buffers = {"cam_1": FakeFrameBuffer("cam_1")}
    assert build_consumers({}, buffers, threading.Event()) == []
    assert build_consumers({"barrier": {"enabled": False}}, buffers,
                           threading.Event()) == []

    missing = tmp_path / "never-recorded"
    settings = {"barrier": {
        "enabled": True,
        "references_dir": str(missing),
        "rois_file": str(missing / "rois.json"),
        "authoritative_camera": "cam_1",
    }}
    assert build_consumers(settings, buffers, threading.Event()) == []
