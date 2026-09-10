"""ffmpeg subprocess stream client (D-04/D-05/D-08).

Builds and spawns an ffmpeg subprocess that decodes an HTTP/MP4 preview stream
and emits JPEG frames to stdout via ``image2pipe``. Frames are throttled by the
``fps`` filter (D-08). ``read_jpeg_frame`` parses the MJPG-over-pipe byte stream
and decodes each complete JPEG with OpenCV.

The exact MJPG pipe-framing is ``[ASSUMED]`` (A3) — this module implements a
robust byte-buffered parser that accumulates bytes until a complete
``0xFFD8..0xFFD9`` JPEG is found, then decodes it.
"""

import subprocess

import cv2
import numpy as np

# JPEG SOI / EOI markers.
_JPEG_SOI = b"\xff\xd8"
_JPEG_EOI = b"\xff\xd9"


def build_ffmpeg_cmd(cam_url, headers, fps_output=1.5):
    """Return the ffmpeg argv for JPEG frames to stdout (D-04/D-05/D-08).

    ``headers`` is the list of ffmpeg ``-headers`` argument values (as produced
    by ``SessionManager.stream_headers_and_url``). The fps filter throttles the
    output to ``fps_output`` frames per second.
    """
    return [
        "ffmpeg",
        "-headers", "\r\n".join(headers) + "\r\n",
        "-i", cam_url,
        "-vf", f"fps={fps_output}",  # 1-2 fps limit (D-08) — reduces wasteful decode
        "-f", "image2pipe",  # D-05: JPEG via pipe
        "-vcodec", "mjpeg",
        "-",
    ]


def spawn_ffmpeg(cmd):
    """Spawn the ffmpeg subprocess with piped stdout/stderr (Pitfall 6).

    On Windows, ``CREATE_NO_WINDOW`` suppresses the console popup for a
    background service. stderr is piped so it can be drained (else the pipe
    fills and blocks capture).
    """
    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,  # read/drain, else pipe fills and blocks (Pitfall 6)
        creationflags=subprocess.CREATE_NO_WINDOW,  # Windows: no console popup
    )


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
