"""Tests for the snapshot poller capture path (``src/capture/snapshot.py``).

The poller exists because ``preview.mp4`` is a single-frame snapshot, not a
stream. These tests pin the two properties that matter for that design:

* one login + one ``requests.Session`` serve every snapshot — HTTP keep-alive
  is worth ~3x here (0.8 -> 2.4 fps measured), and a fresh connection per
  snapshot measured ~3.5x slower; and
* a failed poll re-auths and resumes instead of killing the capture loop.
"""

import threading

import pytest

from src.capture.auth import SessionManager
from src.capture.snapshot import SnapshotPoller, SnapshotStaleError, parse_headers


class FakeCookies:
    def __init__(self):
        self._jar = {"PHPSESSID": "sid"}

    def get(self, name, default=""):
        return self._jar.get(name, default)


class FakeResponse:
    def __init__(self, content=b"snapshot-bytes"):
        self.content = content
        self.status_code = 206

    def raise_for_status(self):
        return None


class FakeSession:
    """Stands in for ``requests.Session`` — counts reuses, never reconnects."""

    def __init__(self):
        self.gets = 0
        self.cookies = FakeCookies()

    def get(self, url, headers=None, timeout=None):
        self.gets += 1
        return FakeResponse()


class FakeSessionManager:
    def __init__(self, session):
        self.session = session
        self.auth_calls = 0

    def auth_context(self, camera_id, cam_url):
        self.auth_calls += 1
        return (self.session, "https://cam/preview.mp4?token=tok",
                "Referer: https://privratnik.net/files/video-control.php\r\n"
                "Range: bytes=0-\r\n"
                "Cookie: PHPSESSID=sid\r\n")


def _poller(decode=None, session=None):
    session = session if session is not None else FakeSession()
    mgr = FakeSessionManager(session)
    poller = SnapshotPoller({}, "cam_1", "https://cam/preview.mp4", mgr,
                            decode=decode or (lambda data, path: "frame"))
    return poller, mgr, session


def test_parse_headers_splits_name_and_value():
    parsed = parse_headers("Referer: https://x/\r\nRange: bytes=0-\r\n")
    assert parsed == {"Referer": "https://x/", "Range": "bytes=0-"}


def test_poll_once_authenticates_lazily_then_reuses_one_session():
    poller, mgr, session = _poller()
    assert mgr.auth_calls == 0, "no login before the first poll"
    for _ in range(3):
        assert poller.poll_once() == "frame"
    assert mgr.auth_calls == 1, "must not re-login per snapshot (kills keep-alive)"
    assert session.gets == 3


def test_poll_once_raises_stale_when_decode_yields_nothing():
    poller, _, _ = _poller(decode=lambda data, path: None)
    with pytest.raises(SnapshotStaleError):
        poller.poll_once()


def test_frames_yields_until_stop_event_is_set():
    poller, _, _ = _poller()
    stop_event = threading.Event()
    seen = []
    for frame in poller.frames(stop_event, target_fps=0):
        seen.append(frame)
        if len(seen) == 3:
            stop_event.set()
    assert seen == ["frame", "frame", "frame"]


def test_frames_reauthing_and_resumes_after_a_failed_poll():
    calls = {"n": 0}

    def flaky_decode(data, path):
        calls["n"] += 1
        return None if calls["n"] == 1 else "frame"

    poller, mgr, _ = _poller(decode=flaky_decode)
    stop_event = threading.Event()
    seen = []
    for frame in poller.frames(stop_event, target_fps=0,
                               backoff_initial=0, backoff_max=0):
        seen.append(frame)
        stop_event.set()
    assert seen == ["frame"], "a failed poll must not end the capture loop"
    assert mgr.auth_calls == 2, "a failure must force a fresh re-auth"


def test_auth_context_performs_exactly_one_login(monkeypatch, settings):
    mgr = SessionManager(settings)
    calls = {"n": 0}

    def fake_login():
        calls["n"] += 1
        mgr._session = FakeSession()
        mgr._token = "tok"
        mgr._camera_tokens = {}
        return mgr._session, "tok"

    monkeypatch.setattr(mgr, "login", fake_login)
    session, url, headers = mgr.auth_context("cam_1", settings["cameras"]["cam_1"])
    assert calls["n"] == 1
    assert session is mgr._session
    assert url.endswith("?token=tok")
    assert "Referer:" in headers and "Range: bytes=0-" in headers
