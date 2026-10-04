"""Record real barrier scenarios from the live feed, for Phase 2 validation.

Phase 2's success criterion #4 requires validation against recorded real
footage: a genuine barrier opening, and a car passing with the gate closed.
Neither can be synthesised — a human has to perform the scenario while this
records. The live feed works (snapshot polling, ~1.5 fps per camera), so the
recording can simply be made on site.

Run as a module so the repo-root ``src`` package is importable::

    python -m scripts.record_validation --label car_passes_gate_closed --duration 60
    python -m scripts.record_validation --label opening --duration 20 --cameras cam_1

Output layout (``data/`` is gitignored, so recordings never reach git)::

    data/recordings/<label>__<YYYYmmdd-HHMMSS>/
        cam_1/000000__<HHMMSSmmm>.jpg
        cam_2/000000__<HHMMSSmmm>.jpg
        manifest.json

The manifest carries per-camera frame counts, achieved fps, per-frame
timestamps and file paths, plus any capture errors. Secrets and tokens are
never written to disk.
"""

import argparse
import json
import os
import re
import sys
import threading
import time
from datetime import datetime

import cv2

from src.capture.auth import SessionManager
from src.capture.snapshot import SnapshotPoller
from src.config import load_settings


def _redact(text):
    """Strip any token= value that might sneak into a URL."""
    return re.sub(r"token=[^&\s]+", "token=***", text or "")


def record(settings, label, duration, cameras, target_fps, note=""):
    """Poll every camera for ``duration`` seconds, writing JPEG frames."""
    started = time.time()
    started_dt = datetime.now()
    session_dir = os.path.join(
        "data", "recordings", "%s__%s" % (label, started_dt.strftime("%Y%m%d-%H%M%S"))
    )
    os.makedirs(session_dir, exist_ok=True)

    stop_event = threading.Event()
    lock = threading.Lock()
    records = []
    stats = {cam: {"frames": 0, "errors": []} for cam in cameras}

    def capture(camera_id, cam_url):
        frame_dir = os.path.join(session_dir, camera_id)
        os.makedirs(frame_dir, exist_ok=True)
        poller = SnapshotPoller(settings, camera_id, cam_url, SessionManager(settings))
        index = 0
        try:
            for frame in poller.frames(
                stop_event,
                target_fps=target_fps,
                frame_stale_seconds=settings.get("frame_stale_seconds", 12),
                backoff_initial=settings.get("backoff_initial", 1.0),
                backoff_max=settings.get("backoff_max", 60.0),
            ):
                now = datetime.now()
                name = "%06d__%s.jpg" % (index, now.strftime("%H%M%S%f")[:-3])
                rel = "%s/%s" % (camera_id, name)
                if not cv2.imwrite(os.path.join(session_dir, rel), frame):
                    raise IOError("failed to write %s" % rel)
                with lock:
                    records.append({
                        "camera_id": camera_id,
                        "index": index,
                        "ts": now.isoformat(timespec="milliseconds"),
                        "file": rel,
                    })
                    stats[camera_id]["frames"] += 1
                index += 1
        except Exception as exc:  # pragma: no cover - diagnostic surface
            with lock:
                stats[camera_id]["errors"].append(
                    "%s: %s" % (type(exc).__name__, exc)
                )

    threads = [
        threading.Thread(target=capture, args=(cam, settings["cameras"][cam]),
                         name="rec-%s" % cam, daemon=True)
        for cam in cameras
    ]
    for thread in threads:
        thread.start()

    stop_event.wait(duration)
    stop_event.set()
    for thread in threads:
        thread.join(timeout=8.0)

    elapsed = time.time() - started
    manifest = {
        "label": label,
        "note": note,
        "started": started_dt.isoformat(timespec="seconds"),
        "ended": datetime.now().isoformat(timespec="seconds"),
        "duration_s": round(elapsed, 2),
        "target_fps": target_fps,
        "capture_mode": settings.get("capture_mode", "ffmpeg"),
        "source": "privratnik.net snapshot poll",
        "cameras": {
            cam: {
                "url": _redact(settings["cameras"][cam]),
                "frames": stats[cam]["frames"],
                "fps": round(stats[cam]["frames"] / elapsed, 2) if elapsed else 0.0,
                "errors": stats[cam]["errors"],
            }
            for cam in cameras
        },
        "frames": sorted(records, key=lambda r: (r["camera_id"], r["index"])),
    }
    with open(os.path.join(session_dir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)
    return manifest, session_dir

def main():
    parser = argparse.ArgumentParser(
        description="Record real barrier scenarios for Phase 2 validation."
    )
    parser.add_argument("--label", required=True,
                        help="scenario tag, e.g. opening / car_passes_gate_closed")
    parser.add_argument("--duration", type=float, default=60.0,
                        help="seconds to record (default 60)")
    parser.add_argument("--cameras", default="",
                        help="comma-separated camera ids (default: all)")
    parser.add_argument("--fps", type=float, default=None,
                        help="override snapshot_fps from config")
    parser.add_argument("--note", default="", help="free-text note for the manifest")
    parser.add_argument("--config", default="config.json")
    args = parser.parse_args()

    settings = load_settings(args.config)
    cameras = [c.strip() for c in args.cameras.split(",") if c.strip()] \
        or list(settings["cameras"])
    unknown = [c for c in cameras if c not in settings["cameras"]]
    if unknown:
        print("unknown camera(s): %s" % ", ".join(unknown), file=sys.stderr)
        return 2

    target_fps = args.fps if args.fps else settings.get("snapshot_fps", 2.0)
    manifest, session_dir = record(
        settings, args.label, args.duration, cameras, target_fps, args.note
    )

    print("saved to %s" % session_dir)
    print("camera\tframes\tfps\terrors")
    for cam in cameras:
        info = manifest["cameras"][cam]
        print("%s\t%d\t%.2f\t%d" % (cam, info["frames"], info["fps"], len(info["errors"])))
    total = sum(manifest["cameras"][cam]["frames"] for cam in cameras)
    failed = any(manifest["cameras"][cam]["errors"] for cam in cameras)
    return 0 if total > 0 and not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
