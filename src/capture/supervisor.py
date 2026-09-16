"""Reconnect / re-auth supervisor with exponential backoff (STREAM-03, D-03).

Treats the stream connection as ephemeral and reconnectable, not a persistent
handle. If no valid frame arrives within ``frame_stale_seconds``, the supervisor
tears down the ffmpeg process, forces a fresh re-auth via ``get_session()``
(Pitfall 1), waits with exponential backoff (1s → 2s → … → max), then respawns
the ffmpeg subprocess.

The "no valid frame for N seconds" detection is the anti-silent-death guard
(Pitfall 1): a stale stream is surfaced as a named error, never treated as
"no event". A reader thread decodes frames off the ffmpeg stdout pipe so the
supervisor can detect silence without blocking on a hung pipe.
"""

import logging
import queue as _queue
import threading
import time

import requests

from src.capture.auth import AuthExpiredError

logger = logging.getLogger(__name__)


class StreamStaleError(Exception):
    """Raised when no valid frame arrives within the stale threshold."""


def run_capture_with_supervisor(start, stop_event, session_mgr, camera_id, cam_url,
                                frame_stale_seconds=12, backoff_initial=1.0, backoff_max=60.0,
                                clock=time.monotonic, read_frame=None):
    """Run the capture loop for one camera with reconnect + re-auth.

    ``start`` is a callable ``start(url, headers)`` that builds and spawns the
    ffmpeg subprocess and returns the ``Popen`` handle. ``session_mgr`` is a
    ``SessionManager``. Yields each decoded frame. On a stale-stream or auth
    failure, re-auths, kills the ffmpeg process, waits with exponential backoff
    (interruptible via ``stop_event.wait``), and reconnects.

    ``clock`` and ``read_frame`` are injectable for tests (fake clock / fake
    capture). ``read_frame(proc)`` returns a numpy frame or ``None`` at EOF.
    """
    backoff = backoff_initial
    while not stop_event.is_set():
        proc = None
        try:
            url, headers = session_mgr.stream_headers_and_url(camera_id, cam_url)
            proc = start(url, headers)
            for frame in _yield_frames_or_raise(proc, frame_stale_seconds, stop_event,
                                                clock=clock, read_frame=read_frame):
                backoff = backoff_initial  # healthy frame; reset
                yield frame
        except (StreamStaleError, AuthExpiredError, requests.RequestException, OSError) as exc:
            # WR-01: transport/auth failures on the entry path (network down,
            # timeout, 4xx/5xx, unexpected socket error) are transient — treat
            # them as reconnect triggers rather than letting them kill the
            # capture thread ("reconnect without misses" core value).
            logger.warning("camera=%s capture error: %s; re-authing and reconnecting", camera_id, exc)
            try:
                session_mgr.get_session()  # force fresh re-auth (Pitfall 1)
            except Exception as auth_exc:  # pragma: no cover - failure path
                logger.error("camera=%s re-auth failed: %s", camera_id, auth_exc)
        finally:
            if proc is not None:
                _kill_proc(proc)
        if stop_event.is_set():
            break
        stop_event.wait(backoff)  # interruptible sleep (D-03)
        backoff = min(backoff * 2, backoff_max)


def _yield_frames_or_raise(proc, frame_stale_seconds, stop_event, clock=time.monotonic, read_frame=None):
    """Yield frames from ``proc``; raise StreamStaleError if none arrive in time.

    A reader thread decodes frames off the pipe into a small queue so the
    supervisor can poll with a timeout and detect silence (Pitfall 1) without
    blocking on a hung stdout pipe.
    """
    if read_frame is None:
        from src.capture.stream_client import read_jpeg_frame
        read_frame = read_jpeg_frame

    frame_q = _queue.Queue(maxsize=1)
    reader_stop = threading.Event()

    def _reader():
        while not reader_stop.is_set():
            try:
                frame = read_frame(proc)
            except Exception:  # pragma: no cover - decode failure path
                frame = None
            frame_q.put(frame)
            if frame is None:
                return

    reader = threading.Thread(target=_reader, daemon=True)
    reader.start()
    last_frame = clock()
    try:
        while not stop_event.is_set():
            try:
                frame = frame_q.get(timeout=0.5)
            except _queue.Empty:
                if clock() - last_frame > frame_stale_seconds:
                    raise StreamStaleError(f"no valid frame for {frame_stale_seconds}s")
                continue
            if frame is None:
                raise StreamStaleError("stream ended (EOF); no more frames")
            yield frame
            last_frame = clock()
    finally:
        reader_stop.set()
        reader.join(timeout=1.0)


def _kill_proc(proc):
    """Kill the ffmpeg process and wait for it to exit (Pitfall 6).

    If an attached stderr drain (from ``spawn_ffmpeg``) is present, it is shut
    down AFTER the process is killed so the drain thread never blocks reaping
    the child (WR-02 — drained pipes are torn down with the process).
    """
    try:
        proc.kill()
        proc.wait()
    except Exception:  # pragma: no cover - process may already be gone
        pass
    finally:
        drain = getattr(proc, "stderr_drain", None)
        if drain is not None:
            drain.join(timeout=1.0)  # pragma: no cover - only real spawn_ffmpeg has one
