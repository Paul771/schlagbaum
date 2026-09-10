"""Entrypoint: wire 2 cameras' capture pipeline (STREAM-01..05).

Loads settings, pre-flights ffmpeg, instantiates one FrameBuffer per camera,
and starts a supervisor thread per camera. Each supervisor yields frames that
``feed_frames`` pushes into the camera's bounded drop-oldest FrameBuffer
(D-09/D-10). The FrameBuffer.pop() integration point is exposed for Phase 2
consumers but not consumed here.
"""

import logging
import shutil
import sys
import threading

from src.capture.auth import SessionManager
from src.capture.frame_buffer import FrameBuffer
from src.capture.stream_client import build_ffmpeg_cmd, spawn_ffmpeg
from src.capture.supervisor import run_capture_with_supervisor
from src.config import load_settings

logger = logging.getLogger(__name__)


def _resolve_ffmpeg(ffmpeg_path):
    """Resolve the ffmpeg binary via PATH lookup or an absolute path."""
    if ffmpeg_path and (ffmpeg_path.startswith("/") or ":" in ffmpeg_path[:2]):
        return ffmpeg_path if shutil.which(ffmpeg_path) else None
    return shutil.which(ffmpeg_path)


def _preflight_ffmpeg(settings):
    """Verify ffmpeg resolves; print a clear install hint and exit non-zero if not."""
    ffmpeg_path = settings.get("ffmpeg_path", "ffmpeg")
    resolved = _resolve_ffmpeg(ffmpeg_path)
    if resolved is None:
        print(
            "ffmpeg not found — install via: winget install Gyan.FFmpeg\n"
            "  (or set 'ffmpeg_path' in config.json to an absolute ffmpeg binary path)",
            file=sys.stderr,
        )
        sys.exit(1)
    return resolved


def feed_frames(supervisor, frame_buffer):
    """Push every supervisor-yielded frame into the camera's FrameBuffer (D-10).

    Runs in the supervisor thread. Each frame is tagged with the buffer's
    camera_id at enqueue (D-10). The bounded drop-oldest queue (D-09) means
    capture never blocks on a slow downstream consumer.
    """
    for frame in supervisor:
        frame_buffer.push(frame)


def main():
    logging.basicConfig(level=logging.INFO)
    settings = load_settings()

    _preflight_ffmpeg(settings)

    queue_size = settings.get("queue_size", 15)
    capture_fps = settings.get("capture_fps", 1.5)
    frame_stale_seconds = settings.get("frame_stale_seconds", 12)
    backoff_initial = settings.get("backoff_initial", 1.0)
    backoff_max = settings.get("backoff_max", 60.0)

    session_mgr = SessionManager(settings)

    stop_event = threading.Event()
    threads = []
    buffers = {}

    for camera_id, cam_url in settings["cameras"].items():
        frame_buffer = FrameBuffer(camera_id, maxsize=queue_size)
        buffers[camera_id] = frame_buffer

        def start(url, headers, _fps=capture_fps):
            cmd = build_ffmpeg_cmd(url, headers, fps_output=_fps)
            return spawn_ffmpeg(cmd)

        supervisor = run_capture_with_supervisor(
            start,
            stop_event,
            session_mgr,
            camera_id,
            cam_url,
            frame_stale_seconds=frame_stale_seconds,
            backoff_initial=backoff_initial,
            backoff_max=backoff_max,
        )
        thread = threading.Thread(
            target=feed_frames,
            args=(supervisor, frame_buffer),
            name=f"capture-{camera_id}",
            daemon=True,
        )
        thread.start()
        threads.append(thread)
        logger.info("started capture thread for camera=%s", camera_id)

    try:
        # Block until interrupted. FrameBuffer.pop() is the Phase 2 integration
        # point; it is exposed but not consumed here.
        while not stop_event.is_set():
            stop_event.wait(1.0)
    except KeyboardInterrupt:
        logger.info("interrupt received; shutting down")
    finally:
        stop_event.set()
        for thread in threads:
            thread.join(timeout=5.0)
        logger.info("shutdown complete")


if __name__ == "__main__":
    main()
