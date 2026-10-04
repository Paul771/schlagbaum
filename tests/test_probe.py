import subprocess
from unittest import mock

from scripts.probe_privratnik_auth import (
    _extract_camera_tokens,
    _overall_success,
    _probe_ffmpeg,
    _redact,
    _redact_secret,
)


def test_redact_secret_masks_plain_values():
    assert _redact_secret("token-value") == "***"
    assert _redact_secret("") == "<empty>"


def test_extract_camera_tokens_from_video_control_page():
    response = mock.Mock(
        text=(
            '<img data-url="https://cam2.privratnik.net/cam-1/preview.mp4?token=tok-1">'
            '<img data-url="https://cam2.privratnik.net/cam-2/preview.mp4?token=tok-2">'
        )
    )

    assert _extract_camera_tokens(response) == {
        "https://cam2.privratnik.net/cam-1/preview.mp4": "tok-1",
        "https://cam2.privratnik.net/cam-2/preview.mp4": "tok-2",
    }


def test_redact_preserves_response_shape_and_masks_values():
    body = '<html><input name="token" value="tok-123"><input name="password" value="pw-456"></html>'

    redacted = _redact(body)

    assert "<html>" in redacted
    assert 'name="token"' in redacted
    assert 'name="password"' in redacted
    assert 'value="***"' in redacted
    assert "tok-123" not in redacted
    assert "pw-456" not in redacted


def test_overall_success_requires_live_frame():
    assert _overall_success(200, "session", "token", 1) is True
    assert _overall_success(200, "session", "token", 0) is False
    assert _overall_success(200, "session", "token", None) is False


def test_probe_ffmpeg_limits_process_to_probe_window():
    proc = mock.Mock()
    proc.communicate.return_value = (b"", b"")

    with mock.patch("subprocess.Popen", return_value=proc) as popen:
        _probe_ffmpeg("https://camera.example/stream", seconds=3)

    args = popen.call_args.args[0]
    assert args[args.index("-t") + 1] == "3"
    assert args[args.index("-vf") + 1] == "fps=1:round=up"


def test_probe_ffmpeg_passes_authenticated_headers_to_process():
    proc = mock.Mock()
    proc.communicate.return_value = (b"", b"")

    with mock.patch("subprocess.Popen", return_value=proc) as popen:
        _probe_ffmpeg(
            "https://camera.example/stream",
            headers="Referer: page\r\nCookie: PHPSESSID=session\r\n",
        )

    assert popen.call_args.args[0][2] == "Referer: page\r\nCookie: PHPSESSID=session\r\n"


def test_probe_ffmpeg_stops_after_timeout():
    proc = mock.Mock()
    proc.communicate.side_effect = [
        subprocess.TimeoutExpired(["ffmpeg"], 5),
        (b"\xff\xd8frame-data", b""),
    ]

    with mock.patch("subprocess.Popen", return_value=proc):
        frames = _probe_ffmpeg("https://camera.example/stream?token=redacted", seconds=5)

    assert frames == 1
    proc.kill.assert_called_once_with()
    assert proc.communicate.call_count == 2
