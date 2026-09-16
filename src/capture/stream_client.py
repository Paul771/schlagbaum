"""ffmpeg subprocess stream client (D-04/D-05/D-08).

Builds and spawns an ffmpeg subprocess that decodes an HTTP/MP4 preview stream
and emits JPEG frames to stdout via ``image2pipe``. Frames are throttled by the
``fps`` filter (D-08). ``read_jpeg_frame`` parses the MJPG-over-pipe byte stream
and decodes each complete JPEG with OpenCV.

The exact MJPG pipe-framing is ``[ASSUMED]`` (A3) — this module implements a
robust byte-buffered parser that accumulates bytes until a complete
``0xFFD8..0xFFD9`` JPEG is found, then decodes it.
"""

import logging
import subprocess
import threading

import cv2
import numpy as np

# JPEG SOI / EOI markers.
_JPEG_SOI = b"\xff\xd8"
_JPEG_EOI = b"\xff\xd9"

logger = logging.getLogger(__name__)


def build_ffmpeg_cmd(cam_url, headers, fps_output=1.5):
    """Return the ffmpeg argv for JPEG frames to stdout (D-04/D-05/D-08).

    ``headers`` is the **pure HTTP header content** (a single string of
    ``Name: value`` lines, e.g. ``"Referer: ...\r\nRange: bytes=0-\r\n..."``),
    as produced by ``SessionManager.stream_headers_and_url``. This function is
    the single place that emits the ffmpeg ``-headers`` flag and the header
    value — the value MUST NOT begin with an ``-headers`` token (CR-01), else
    the HTTP header block is malformed and many servers reject it with a 400.
    The fps filter throttles the output to ``fps_output`` frames per second.
    """
    return [
        "ffmpeg",
        "-headers", headers,
        "-i", cam_url,
        "-vf", f"fps={fps_output}",  # 1-2 fps limit (D-08) — reduces wasteful decode
        "-f", "image2pipe",  # D-05: JPEG via pipe
        "-vcodec", "mjpeg",
        "-",
    ]


class _StderrDrain:
    """Drain an ffmpeg subprocess's stderr pipe on a daemon thread (WR-02).

    Without this, ffmpeg writing warnings/progress to a full stderr pipe
    blocks and silently stops capture (Pitfall 6 / T-01-05). The drained lines
    are logged at debug and discarded; the thread stops when EOF is reached or
    the subprocess exits.
    """

    def __init__(self, proc):
        self._proc = proc
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            while not self._stop.is_set():
                line = self._proc.stderr.readline()
                if not line:  # EOF — process exited or closed stderr
                    return
                text = line.decode("utf-8", errors="replace").strip()
                logger.debug("ffmpeg stderr: %s", text)
        except Exception:  # pragma: no cover - drain is best-effort, never fatal
            pass
        finally:
            try:
                self._proc.stderr.close()
            except Exception:  # pragma: no cover
                pass

    def join(self, timeout=1.0):
        self._stop.set()
        self._thread.join(timeout=timeout)


def spawn_ffmpeg(cmd):
    """Spawn the ffmpeg subprocess with piped stdout/stderr (Pitfall 6).

    On Windows, ``CREATE_NO_WINDOW`` suppresses the console popup for a
    background service. stderr is piped and drained on a daemon thread so it
    never fills and blocks capture (WR-02). Returns the ``Popen`` handle with an
    attached ``.stderr_drain`` that callers tear down before/after reaping the
    process.
    """
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,  # read/drain, else pipe fills and blocks (Pitfall 6)
        creationflags=subprocess.CREATE_NO_WINDOW,  # Windows: no console popup
    )
    proc.stderr_drain = _StderrDrain(proc)
    return proc


def read_jpeg_frame(proc):
    """Read one MJPG frame from ffmpeg stdout.

    Accumulates bytes until a complete ``0xFFD8..0xFFD9`` JPEG is found, then
    decodes it via ``cv2.imdecode`` and returns a numpy BGR array. Returns
    ``None`` at EOF (stream ended / process exited).
    """
    buffer = bytearray()
    while True:
        # Look for a complete JPEG in the current buffer.
        start = buffer.find(_JPEG_SOI)
        if start != -1:
            end = buffer.find(_JPEG_EOI, start + 2)
            if end != -1:
                jpeg = bytes(buffer[start:end + 2])
                # Drop the consumed bytes (keep any trailing partial frame).
                del buffer[:end + 2]
                frame = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
                if frame is not None:
                    return frame
                # Corrupt frame — skip and keep reading.
                continue

        # No complete JPEG yet — read more bytes from the pipe.
        chunk = proc.stdout.read(65536)
        if not chunk:
            # EOF. If we have a partial frame, discard it and signal end.
            return None
        buffer.extend(chunk)
