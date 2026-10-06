"""Entrypoint: wire 2 cameras' capture pipeline (STREAM-01..05) + barrier detection (BARRIER-01..04).

Loads settings, instantiates one FrameBuffer per camera, and starts one capture
thread per camera. ``capture_mode`` selects the source: ``"snapshot"`` polls
the single-frame ``preview.mp4`` endpoint in-process (see
``src/capture/snapshot.py`` — no ffmpeg needed), while ``"ffmpeg"`` uses the
original supervisor + ffmpeg-subprocess stream path. Either way each yielded
frame is pushed by ``feed_frames`` into the camera's bounded drop-oldest
FrameBuffer (D-09/D-10).

Phase 2 additionally starts one ``BarrierConsumer`` daemon thread per camera
when ``barrier.enabled`` is set and the references load. Each consumer owns its
thread because ``FrameBuffer.pop()`` blocks; every camera watches its own
barrier and emits for it — two physical barriers, one camera each, so the
duplicate-event guard is the FSM re-arm flag (BARRIER-03). Missing references
degrade to capture-only rather than taking the service down.
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
from src.detect.consumer import build_consumers

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

    capture_mode = settings.get("capture_mode", "ffmpeg")
    if capture_mode != "snapshot":
        _preflight_ffmpeg(settings)  # snapshot mode decodes in-process, no ffmpeg

    queue_size = settings.get("queue_size", 15)
    snapshot_fps = settings.get("snapshot_fps", 2.0)
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

        if capture_mode == "snapshot":
            from src.capture.snapshot import SnapshotPoller

            poller = SnapshotPoller(settings, camera_id, cam_url, session_mgr)
            frames = poller.frames(
                stop_event,
                target_fps=snapshot_fps,
                frame_stale_seconds=frame_stale_seconds,
                backoff_initial=backoff_initial,
                backoff_max=backoff_max,
            )
        else:

            def start(url, headers, _fps=capture_fps):
                cmd = build_ffmpeg_cmd(url, headers, fps_output=_fps)
                return spawn_ffmpeg(cmd)

            frames = run_capture_with_supervisor(
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
            args=(frames, frame_buffer),
            name=f"capture-{camera_id}",
            daemon=True,
        )
        thread.start()
        threads.append(thread)
        logger.info("started capture thread for camera=%s (mode=%s)", camera_id, capture_mode)

    # Phase 2: one barrier consumer thread per camera. Returns [] when detection
    # is disabled or the references are missing, so capture always keeps running.
    consumers = build_consumers(settings, buffers, stop_event)
    for consumer in consumers:
        consumer.start()

    try:
        # Block until interrupted. Capture threads feed the FrameBuffer;
        # BarrierConsumer threads (one per camera) drain it.
        while not stop_event.is_set():
            stop_event.wait(1.0)
    except KeyboardInterrupt:
        logger.info("interrupt received; shutting down")
    finally:
        stop_event.set()
        for consumer in consumers:
            # pop() blocks, so give each consumer a frame-sized window to notice
            # stop_event rather than waiting forever on a quiet camera.
            consumer.join(timeout=1.0)
        for thread in threads:
            thread.join(timeout=5.0)
        logger.info("shutdown complete")


if __name__ == "__main__":
    main()
