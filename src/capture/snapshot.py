"""Snapshot poller — in-process capture for single-frame preview endpoints.

privratnik.net's ``preview.mp4`` is NOT a live stream: every HTTP request
returns a ~40 ms, single-frame H.264 snapshot. Measured live 2026-10-04 on
cam_1: ``ffmpeg`` reports ``Duration 00:00:00.04`` and emits exactly one
frame, and ``files/proxy.php?link=...&token=...`` behaves identically. So this
module polls snapshots instead of holding a stream open. The ffmpeg stream
path is untouched and remains selectable via ``capture_mode`` in config.json.

Two measured facts drive the design:

* HTTP keep-alive is the dominant cost. A fresh connection per snapshot costs
  ~1.45 s; reusing one ``requests.Session`` costs ~0.41 s (0.8 -> 2.4 fps).
  ``Connection: close`` measures the same as a new connection, so the session
  must be reused for the poller's whole life — including across reconnects —
  and re-login must NOT happen per snapshot.
* ffmpeg is not the bottleneck. Spawning a subprocess per snapshot and
  re-encoding to MJPEG measured no faster than a plain HTTP GET, so the bytes
  are decoded in-process with OpenCV instead.

The remaining ceiling is server-side (~0.39 s per snapshot), so a barrier
opening of 2-5 s yields roughly 5-12 frames at ``snapshot_fps``.
"""

import logging
import os
import tempfile
import time

import cv2
import requests

from src.capture.auth import AuthExpiredError

logger = logging.getLogger(__name__)


class SnapshotStaleError(Exception):
    """Raised when a snapshot cannot be fetched or yields no frame."""


def parse_headers(header_text):
    """Turn a ``Name: value`` header block into a dict for ``requests``."""
    headers = {}
    for line in (header_text or "").splitlines():
        if ":" in line:
            name, value = line.split(":", 1)
            headers[name.strip()] = value.strip()
    return headers


def decode_snapshot(data, path):
    """Decode the first frame of a snapshot MP4 held in ``data``.

    OpenCV's FFmpeg backend needs a real file, so the bytes are written to
    ``path`` first — the payload is ~10 KB, so this is cheap. Returns the first
    BGR frame, or ``None`` when the container yields nothing.
    """
    with open(path, "wb") as fh:
        fh.write(data)
    cap = cv2.VideoCapture(path)
    try:
        ok, frame = cap.read()
        return frame if ok else None
    finally:
        cap.release()
class SnapshotPoller:
    """Polls single-frame snapshots for one camera and yields BGR frames."""

    def __init__(self, settings, camera_id, cam_url, session_mgr, decode=None,
                 clock=time.monotonic):
        self.settings = settings
        self.camera_id = camera_id
        self.cam_url = cam_url
        self.session_mgr = session_mgr
        self.clock = clock
        self._decode = decode if decode is not None else decode_snapshot
        self._tmp_path = os.path.join(
            tempfile.gettempdir(),
            "schlagbaum_snap_%s_%d.mp4" % (camera_id, os.getpid()),
        )
        self._session = None
        self._url = None
        self._headers = None

    def ensure_auth(self):
        """Log in once, keeping the returned session (keep-alive + PHPSESSID)."""
        session, url, headers = self.session_mgr.auth_context(
            self.camera_id, self.cam_url
        )
        self._session = session
        self._url = url
        self._headers = parse_headers(headers)
        return self._session

    def invalidate(self):
        """Drop the cached session so the next poll re-authenticates."""
        self._session = None
        self._url = None
        self._headers = None

    def poll_once(self):
        """Fetch and decode one snapshot. Returns a frame or ``None``."""
        if self._session is None:
            self.ensure_auth()
        response = self._session.get(self._url, headers=self._headers, timeout=15)
        response.raise_for_status()
        frame = self._decode(response.content, self._tmp_path)
        if frame is None:
            raise SnapshotStaleError("snapshot decoded to no frame")
        return frame

    def frames(self, stop_event, target_fps=2.0, frame_stale_seconds=12,
               backoff_initial=1.0, backoff_max=60.0):
        """Yield frames until ``stop_event`` is set, re-authing on failure."""
        interval = 1.0 / target_fps if target_fps > 0 else 0.0
        backoff = backoff_initial
        dead_since = None
        while not stop_event.is_set():
            try:
                if self._session is None:
                    self.ensure_auth()
                due = self.clock()
                while not stop_event.is_set():
                    frame = self.poll_once()
                    backoff = backoff_initial
                    dead_since = None
                    yield frame
                    if stop_event.wait(max(0.0, due + interval - self.clock())):
                        break
                    due += interval
            except (SnapshotStaleError, AuthExpiredError,
                    requests.RequestException, OSError) as exc:
                now = self.clock()
                if dead_since is None:
                    dead_since = now
                elif now - dead_since > frame_stale_seconds:
                    logger.warning("camera=%s no snapshot for %.0fs (stale)",
                                   self.camera_id, now - dead_since)
                    dead_since = now
                logger.warning("camera=%s snapshot error: %s; re-authing and retrying",
                               self.camera_id, exc)
                self.invalidate()
                try:
                    self.ensure_auth()
                except Exception as auth_exc:  # pragma: no cover - failure path
                    logger.error("camera=%s re-auth failed: %s",
                                 self.camera_id, auth_exc)
            if stop_event.is_set():
                break
            if stop_event.wait(backoff):
                break
            backoff = min(backoff * 2, backoff_max)

