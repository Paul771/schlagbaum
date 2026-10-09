"""Two-camera diagnostic for the snapshot capture path.

Mirrors ``probe_capture_2_cameras.py`` (the ffmpeg path) but exercises
``SnapshotPoller``, so the achieved frame rate of the snapshot path can be
measured against the real privratnik.net service.

Run as a module so the repo-root ``src`` package is importable::

    python -m scripts.probe_snapshot_capture --duration 20
"""

import argparse
import threading
import time

from src.capture.auth import SessionManager
from src.capture.snapshot import SnapshotPoller
from src.config import load_settings


def run_snapshot_capture(settings, duration=12.0):
    stop_event = threading.Event()
    results = {
        camera_id: {"frames": 0, "error": None}
        for camera_id in settings["cameras"]
    }

    def capture(camera_id, cam_url):
        poller = SnapshotPoller(
            settings, camera_id, cam_url, SessionManager(settings)
        )
        try:
            for _ in poller.frames(
                stop_event,
                target_fps=settings.get("snapshot_fps", 2.0),
                frame_stale_seconds=settings.get("frame_stale_seconds", 12),
                backoff_initial=settings.get("backoff_initial", 1.0),
                backoff_max=settings.get("backoff_max", 60.0),
            ):
                results[camera_id]["frames"] += 1
        except Exception as exc:  # pragma: no cover - diagnostic surface
            results[camera_id]["error"] = f"{type(exc).__name__}: {exc}"

    threads = [
        threading.Thread(
            target=capture,
            args=(camera_id, cam_url),
            name=f"snapshot-{camera_id}",
            daemon=True,
        )
        for camera_id, cam_url in settings["cameras"].items()
    ]
    for thread in threads:
        thread.start()

    started = time.time()
    stop_event.wait(duration)
    stop_event.set()
    for thread in threads:
        thread.join(timeout=5.0)
    return results, time.time() - started


def snapshot_capture_passed(results):
    return bool(results) and all(
        item["frames"] > 0 and item["error"] is None
        for item in results.values()
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--duration", type=float, default=12.0)
    args = parser.parse_args()

    settings = load_settings(args.config)
    results, elapsed = run_snapshot_capture(settings, args.duration)
    fps = {cam: r["frames"] / elapsed if elapsed else 0.0 for cam, r in results.items()}

    print(f"elapsed\t{elapsed:.1f}s")
    print("camera\tframes\tfps\terror")
    for camera_id, result in results.items():
        print(
            f"{camera_id}\t{result['frames']}\t{fps[camera_id]:.2f}\t"
            f"{result['error'] or '-'}"
        )
    return 0 if snapshot_capture_passed(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
