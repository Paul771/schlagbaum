import argparse
import threading
import time

from src.capture.auth import SessionManager
from src.capture.stream_client import build_ffmpeg_cmd, spawn_ffmpeg
from src.capture.supervisor import run_capture_with_supervisor
from src.config import load_settings


def diagnostic_passed(results):
    return bool(results) and all(
        item.get("frames", 0) > 0 and item.get("error") is None
        for item in results.values()
    )


def run_diagnostic(settings, duration=12.0):
    stop_event = threading.Event()
    results = {
        camera_id: {"frames": 0, "starts": 0, "error": None}
        for camera_id in settings["cameras"]
    }

    def start_factory(camera_id):
        def start(url, headers):
            results[camera_id]["starts"] += 1
            return spawn_ffmpeg(
                build_ffmpeg_cmd(
                    url,
                    headers,
                    fps_output=settings.get("capture_fps", 1.5),
                )
            )

        return start

    def capture(camera_id, cam_url):
        session_mgr = SessionManager(settings)
        try:
            for _ in run_capture_with_supervisor(
                start_factory(camera_id),
                stop_event,
                session_mgr,
                camera_id,
                cam_url,
                frame_stale_seconds=settings.get("frame_stale_seconds", 12),
                backoff_initial=settings.get("backoff_initial", 1.0),
                backoff_max=settings.get("backoff_max", 60.0),
            ):
                results[camera_id]["frames"] += 1
        except Exception as exc:
            results[camera_id]["error"] = f"{type(exc).__name__}: {exc}"

    threads = [
        threading.Thread(
            target=capture,
            args=(camera_id, cam_url),
            name=f"diagnostic-{camera_id}",
            daemon=True,
        )
        for camera_id, cam_url in settings["cameras"].items()
    ]
    for thread in threads:
        thread.start()

    stop_event.wait(duration)
    stop_event.set()
    for thread in threads:
        thread.join(timeout=5.0)
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--duration", type=float, default=12.0)
    args = parser.parse_args()

    settings = load_settings(args.config)
    results = run_diagnostic(settings, args.duration)
    print("camera\tframes\tffmpeg_starts\terror")
    for camera_id, result in results.items():
        print(
            f"{camera_id}\t{result['frames']}\t{result['starts']}\t"
            f"{result['error'] or '-'}"
        )
    return 0 if diagnostic_passed(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
