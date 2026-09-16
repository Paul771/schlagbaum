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
    # headers is the PURE header content string (CR-01) — NOT a list with an
    # "-headers" element. build_ffmpeg_cmd is the single place that emits the
    # "-headers" flag and its value.
    headers = (
        "Referer: https://privratnik.net/files/video-control.php\r\n"
        "Range: bytes=0-\r\n"
        "Cookie: PHPSESSID=abc\r\n"
    )
    cmd = build_ffmpeg_cmd("https://cam2.privratnik.net/80146f20_3105/preview.mp4?token=tok", headers, fps_output=1.5)

    assert cmd[0] == "ffmpeg"
    assert cmd[1] == "-headers"
    assert cmd[2] == headers  # the -headers option value is pure header content
    assert "-f" in cmd and cmd[cmd.index("-f") + 1] == "image2pipe"
    assert "-vcodec" in cmd and cmd[cmd.index("-vcodec") + 1] == "mjpeg"
    assert "-vf" in cmd and cmd[cmd.index("-vf") + 1] == "fps=1.5"
    assert cmd[-1] == "-"


def test_build_ffmpeg_cmd_injects_headers():
    headers = (
        "Referer: https://privratnik.net/files/video-control.php\r\n"
        "Range: bytes=0-\r\n"
        "Cookie: PHPSESSID=abc\r\n"
    )
    cmd = build_ffmpeg_cmd("https://cam2.privratnik.net/80146f20_3105/preview.mp4?token=tok", headers)
    header_arg = cmd[cmd.index("-headers") + 1]
    assert "Referer: https://privratnik.net/files/video-control.php" in header_arg
    assert "Range: bytes=0-" in header_arg
    assert "Cookie: PHPSESSID=abc" in header_arg


def test_build_ffmpeg_cmd_header_value_has_no_leading_headers_token():
    """CR-01 regression guard: the -headers VALUE must not begin with an
    "-headers" line. A value starting with "-headers\\r\\n" is a header with no
    'Name: value' colon, which nginx/reverse proxies reject with a 400.
    """
    headers = (
        "Referer: https://privratnik.net/files/video-control.php\r\n"
        "Range: bytes=0-\r\n"
        "Cookie: PHPSESSID=abc\r\n"
    )
    cmd = build_ffmpeg_cmd("https://cam2.privratnik.net/80146f20_3105/preview.mp4?token=tok", headers)
    header_value = cmd[cmd.index("-headers") + 1]
    # The first line must be a well-formed "Name: value" header.
    first_line = header_value.split("\r\n", 1)[0]
    assert ": " in first_line
    assert first_line.startswith("Referer:")
    assert not header_value.lstrip().startswith("-headers")
    # Every header line is well-formed with a colon.
    for line in header_value.rstrip("\r\n").split("\r\n"):
        assert ": " in line, f"malformed header line {line!r}"


def test_spawn_ffmpeg_uses_pipes_and_no_window():
    with mock.patch("src.capture.stream_client.subprocess.Popen") as popen:
        # stderr is a MagicMock so readline/close are mockable; stderr_drain
        # must be attached so the caller can tear it down (WR-02).
        popen.return_value.stderr = mock.Mock()
        proc = spawn_ffmpeg(["ffmpeg", "-i", "url"])
        popen.assert_called_once()
        kwargs = popen.call_args.kwargs
        assert kwargs["stdout"] == mock.ANY  # subprocess.PIPE
        assert kwargs["stderr"] == mock.ANY  # subprocess.PIPE
        assert kwargs["creationflags"] == mock.ANY  # CREATE_NO_WINDOW
        # WR-02: an attached stderr drain is installed and joinable for teardown.
        assert proc.stderr_drain is not None
        proc.stderr_drain.join(timeout=0.1)


def test_spawn_ffmpeg_stderr_is_drained():
    """WR-02 regression guard: spawn_ffmpeg must attach a stderr drain so a
    long-running ffmpeg never blocks on a full stderr pipe (Pitfall 6).
    """
    with mock.patch("src.capture.stream_client.subprocess.Popen") as popen:
        stderr = mock.Mock()
        # Simulate one stderr line then EOF, then a readline at EOF.
        stderr.readline.side_effect = [b"frame=  1 fps=0.0 q=-1.0 size=N/A\r\n", b"", b""]
        popen.return_value.stderr = stderr
        proc = spawn_ffmpeg(["ffmpeg", "-i", "url"])

        assert proc.stderr_drain is not None
        proc.stderr_drain.join(timeout=1.0)
        # The drain read the line and hit EOF; stderr was closed.
        assert stderr.readline.call_count >= 1
        assert stderr.close.called


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
