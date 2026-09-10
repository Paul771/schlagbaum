"""Tests for the ffmpeg stream client (STREAM-01, D-04/D-05/D-08).

``build_ffmpeg_cmd`` and ``read_jpeg_frame`` are unit-tested without spawning
a real ffmpeg process. ``spawn_ffmpeg`` is tested with a mocked
``subprocess.Popen``.
"""

import io
from unittest import mock

import cv2
import numpy as np

from src.capture.stream_client import build_ffmpeg_cmd, read_jpeg_frame, spawn_ffmpeg


def _synthetic_jpeg():
    """Return a tiny valid JPEG byte sequence."""
    img = np.zeros((8, 8, 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


def test_build_ffmpeg_cmd_shape():
    headers = [
        "-headers",
        "Referer: https://privratnik.net/files/video-control.php\r\n"
        "Range: bytes=0-\r\n"
        "Cookie: PHPSESSID=abc\r\n",
    ]
    cmd = build_ffmpeg_cmd("https://cam2.privratnik.net/80146f20_3105/preview.mp4?token=tok", headers, fps_output=1.5)

    assert cmd[0] == "ffmpeg"
    assert cmd[1] == "-headers"
    assert "-f" in cmd and cmd[cmd.index("-f") + 1] == "image2pipe"
    assert "-vcodec" in cmd and cmd[cmd.index("-vcodec") + 1] == "mjpeg"
    assert "-vf" in cmd and cmd[cmd.index("-vf") + 1] == "fps=1.5"
    assert cmd[-1] == "-"


def test_build_ffmpeg_cmd_injects_headers():
    headers = [
        "-headers",
        "Referer: https://privratnik.net/files/video-control.php\r\n"
        "Range: bytes=0-\r\n"
        "Cookie: PHPSESSID=abc\r\n",
    ]
    cmd = build_ffmpeg_cmd("https://cam2.privratnik.net/80146f20_3105/preview.mp4?token=tok", headers)
    header_arg = cmd[cmd.index("-headers") + 1]
    assert "Referer: https://privratnik.net/files/video-control.php" in header_arg
    assert "Range: bytes=0-" in header_arg
    assert "Cookie: PHPSESSID=abc" in header_arg


def test_spawn_ffmpeg_uses_pipes_and_no_window():
    with mock.patch("src.capture.stream_client.subprocess.Popen") as popen:
        spawn_ffmpeg(["ffmpeg", "-i", "url"])
        popen.assert_called_once()
        kwargs = popen.call_args.kwargs
        assert kwargs["stdout"] == mock.ANY  # subprocess.PIPE
        assert kwargs["stderr"] == mock.ANY  # subprocess.PIPE
        assert kwargs["creationflags"] == mock.ANY  # CREATE_NO_WINDOW


def test_read_jpeg_frame_decodes_synthetic_jpeg():
    jpeg = _synthetic_jpeg()
    proc = mock.Mock()
    # First read returns the full JPEG, second returns EOF.
    proc.stdout.read.side_effect = [jpeg, b""]

    frame = read_jpeg_frame(proc)
    assert frame is not None
    assert isinstance(frame, np.ndarray)
    assert frame.shape == (8, 8, 3)


def test_read_jpeg_frame_handles_split_frames():
    """A JPEG split across multiple pipe reads must still decode."""
    jpeg = _synthetic_jpeg()
    mid = len(jpeg) // 2
    proc = mock.Mock()
    proc.stdout.read.side_effect = [jpeg[:mid], jpeg[mid:], b""]

    frame = read_jpeg_frame(proc)
    assert frame is not None
    assert frame.shape == (8, 8, 3)


def test_read_jpeg_frame_returns_none_at_eof():
    proc = mock.Mock()
    proc.stdout.read.side_effect = [b""]
    assert read_jpeg_frame(proc) is None
