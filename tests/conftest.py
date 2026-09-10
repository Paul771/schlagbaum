"""Shared pytest fixtures for the frame-buffer and config tests."""

import pytest

from src.capture.frame_buffer import FrameBuffer


@pytest.fixture
def settings():
    """A minimal Settings dict mirroring config.json defaults."""
    return {
        "cameras": {
            "cam_1": "https://cam2.privratnik.net/80146f20_3105/preview.mp4",
            "cam_2": "https://cam2.privratnik.net/f9456e90_3099/preview.mp4",
        },
        "queue_size": 15,
        "capture_fps": 1.5,
        "frame_stale_seconds": 12,
        "backoff_initial": 1.0,
        "backoff_max": 60.0,
        "ffmpeg_path": "ffmpeg",
        "referer": "https://privratnik.net/files/video-control.php",
    }


@pytest.fixture
def frame_buffer():
    """A small FrameBuffer for edge-case tests."""
    return FrameBuffer("cam_1", maxsize=3)
