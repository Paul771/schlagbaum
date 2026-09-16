"""Tests for the reconnect/re-auth supervisor (STREAM-03, D-03).

Uses a fake clock / fake capture — no network, no real ffmpeg.
"""

import threading
from unittest import mock

from src.capture.frame_buffer import FrameBuffer
from src.capture.supervisor import run_capture_with_supervisor
from src.main import feed_frames


class FakeStopEvent:
    """A stop event that records wait() durations and stops after N waits."""

    def __init__(self, waits_before_stop):
        self.waits = []
        self._waits_before_stop = waits_before_stop

    def is_set(self):
        return len(self.waits) >= self._waits_before_stop

    def wait(self, timeout):
        self.waits.append(timeout)


class FakeProc:
    """A fake ffmpeg Popen handle that records kill/wait calls."""

    def __init__(self):
        self.killed = False
        self.waited = False

    def kill(self):
        self.killed = True

    def wait(self):
        self.waited = True


def _run_supervisor(read_frame, session_mgr, stop_event, start=None, **kwargs):
    """Run the supervisor to completion and return (frames, stop_event, procs)."""
    procs = []

    def _start(url, headers):
        proc = FakeProc()
        procs.append((url, headers, proc))
        return proc

    supervisor = run_capture_with_supervisor(
        start or _start,
        stop_event,
        session_mgr,
        "cam_1",
        "https://cam2.privratnik.net/80146f20_3105/preview.mp4",
        read_frame=read_frame,
        **kwargs,
    )
    frames = list(supervisor)
    return frames, stop_event, procs


def _session_mgr():
    mgr = mock.Mock()
    mgr.stream_headers_and_url.return_value = (
        "https://cam2.privratnik.net/80146f20_3105/preview.mp4?token=tok",
        "Referer: x\r\n",  # pure header content string (CR-01)
    )
    return mgr


def test_supervisor_reauths_and_reconnects_on_stale_stream():
    """EOF (None frame) triggers StreamStaleError → re-auth → reconnect."""
    mgr = _session_mgr()
    stop = FakeStopEvent(waits_before_stop=2)
    # read_frame returns None immediately → EOF → stale on first read.
    frames, _, procs = _run_supervisor(lambda proc: None, mgr, stop)

    # Reconnected at least twice (initial + after first stale).
    assert len(procs) >= 2
    # Re-auth was forced on each stale-stream failure.
    assert mgr.get_session.call_count >= 1
    # stream_headers_and_url was called before each start (tokenized URL + headers).
    assert mgr.stream_headers_and_url.call_count == len(procs)
    for url, headers, _ in procs:
        assert url == "https://cam2.privratnik.net/80146f20_3105/preview.mp4?token=tok"
        assert headers == "Referer: x\r\n"


def test_backoff_doubles_1_2_4():
    """Backoff doubles 1→2→4 on consecutive stale-stream failures."""
    mgr = _session_mgr()
    stop = FakeStopEvent(waits_before_stop=3)
    _run_supervisor(lambda proc: None, mgr, stop)
    assert stop.waits == [1.0, 2.0, 4.0]


def test_backoff_resets_on_healthy_frame():
    """A healthy frame resets backoff to initial, so it never grows."""
    mgr = _session_mgr()
    stop = FakeStopEvent(waits_before_stop=3)
    # Yield one healthy frame then EOF (stale) each iteration.
    state = {"n": 0}

    def read_frame(proc):
        state["n"] += 1
        if state["n"] % 2 == 1:
            return "frame-data"  # healthy frame
        return None  # EOF → stale

    _run_supervisor(read_frame, mgr, stop)
    # Every healthy frame resets backoff to 1.0, so all waits are 1.0.
    assert stop.waits == [1.0, 1.0, 1.0]


def test_backoff_caps_at_max():
    """Backoff doubles up to backoff_max and then stays capped."""
    mgr = _session_mgr()
    stop = FakeStopEvent(waits_before_stop=8)
    _run_supervisor(lambda proc: None, mgr, stop, backoff_initial=1.0, backoff_max=4.0)
    # 1,2,4,4,4,4,4,4 — capped at 4.0.
    assert stop.waits == [1.0, 2.0, 4.0, 4.0, 4.0, 4.0, 4.0, 4.0]


def test_uses_stop_event_wait_not_time_sleep():
    """The supervisor must use stop_event.wait (interruptible), not time.sleep."""
    mgr = _session_mgr()
    stop = FakeStopEvent(waits_before_stop=2)
    with mock.patch("src.capture.supervisor.time.sleep", side_effect=AssertionError("time.sleep used")) as sleep:
        _run_supervisor(lambda proc: None, mgr, stop)
    sleep.assert_not_called()
    assert len(stop.waits) >= 1


def test_ffmpeg_process_killed_on_reconnect():
    """The ffmpeg process is killed and waited on each reconnect."""
    mgr = _session_mgr()
    stop = FakeStopEvent(waits_before_stop=2)
    _, _, procs = _run_supervisor(lambda proc: None, mgr, stop)
    for _, _, proc in procs:
        assert proc.killed is True
        assert proc.waited is True


def test_stderr_drain_torn_down_when_process_killed():
    """WR-02: when a spawned process carries an attached stderr_drain, killing
    it also tears the drain down so the drained pipe never leaks."""
    from src.capture.supervisor import _kill_proc

    drain = mock.Mock()
    proc = mock.Mock()
    proc.stderr_drain = drain  # as attached by spawn_ffmpeg
    _kill_proc(proc)
    drain.join.assert_called_once_with(timeout=1.0)
    # A process WITHOUT a drain must not error.
    _kill_proc(mock.Mock())


def test_transport_error_does_not_kill_capture_loop():
    """WR-01: a requests.ConnectionError on the auth/stream entry path must be
    treated as a reconnect trigger, not propagate out and kill the capture
    thread."""
    import requests

    mgr = _session_mgr()

    def raising_mgr():
        """stream_headers_and_url raises ConnectionError on the first call."""
        calls = {"n": 0}
        def _stream(camera_id, cam_url):
            calls["n"] += 1
            if calls["n"] == 1:
                raise requests.ConnectionError("network down (transient)")
            return "u?token=tok", "Referer: x\r\n"
        mgr.stream_headers_and_url.side_effect = _stream
        return mgr

    stop = FakeStopEvent(waits_before_stop=2)
    frames, _, procs = _run_supervisor(lambda proc: None, raising_mgr(), stop)
    # The loop survived the transport error and reconnected.
    assert len(procs) >= 1
    assert mgr.get_session.call_count >= 1


def test_feed_frames_pushes_frames_into_buffer_tagged_with_camera_id():
    """feed_frames pushes every supervisor-yielded frame into the camera's buffer."""
    buffer = FrameBuffer("cam_1", maxsize=10)

    def fake_supervisor():
        for i in range(5):
            yield f"frame-{i}"

    feed_frames(fake_supervisor(), buffer)
    assert buffer.q.qsize() == 5
    popped = [buffer.pop() for _ in range(5)]
    assert [f.data for f in popped] == ["frame-0", "frame-1", "frame-2", "frame-3", "frame-4"]
    # Every frame is tagged with the camera's id (D-10).
    assert all(f.camera_id == "cam_1" for f in popped)
