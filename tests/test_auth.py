"""Tests for the privratnik.net session manager (STREAM-02, D-01/D-07).

All tests mock ``requests`` — no live credentials or network access.
"""

from unittest import mock

import pytest

from src.capture.auth import SessionManager, _extract_token


def _settings():
    return {
        "login": "test_login",
        "password": "test_password",
        "cameras": {
            "cam_1": "https://cam2.privratnik.net/80146f20_3105/preview.mp4",
        },
    }


def _mock_response(status=200, text="", json_data=None, cookies=None, url="https://privratnik.net/login"):
    resp = mock.Mock()
    resp.status_code = status
    resp.text = text
    resp.url = url
    resp.history = []
    if json_data is not None:
        resp.json.return_value = json_data
    else:
        resp.json.side_effect = ValueError("not json")
    resp.raise_for_status = mock.Mock()
    if cookies is None:
        cookies = {"PHPSESSID": "abc123"}
    resp.cookies = mock.Mock()
    resp.cookies.get.side_effect = lambda name, default=None: cookies.get(name, default)
    return resp


def test_login_sets_phpsessid_cookie_and_returns_token():
    resp = _mock_response(json_data={"token": "tok-123"})
    session = mock.Mock()
    session.post.return_value = resp
    session.cookies = resp.cookies

    with mock.patch("src.capture.auth.requests.Session", return_value=session):
        mgr = SessionManager(_settings())
        s, token = mgr.login()

    assert token == "tok-123"
    assert s is session
    # The session's PHPSESSID cookie is readable via the manager's session.
    assert mgr._session.cookies.get("PHPSESSID") == "abc123"


def test_get_session_reinvokes_login():
    resp = _mock_response(json_data={"token": "tok-1"})
    session = mock.Mock()
    session.post.return_value = resp
    session.cookies = resp.cookies

    with mock.patch("src.capture.auth.requests.Session", return_value=session) as session_cls:
        mgr = SessionManager(_settings())
        mgr.get_session()
        mgr.get_session()

    # get_session() always re-invokes login() for a fresh session (Pitfall 1).
    assert session_cls.call_count == 2
    assert session.post.call_count == 2


def test_stream_headers_and_url_appends_token_and_headers():
    resp = _mock_response(json_data={"token": "tok-456"})
    session = mock.Mock()
    session.post.return_value = resp
    session.cookies = resp.cookies

    with mock.patch("src.capture.auth.requests.Session", return_value=session):
        mgr = SessionManager(_settings())
        url, headers = mgr.stream_headers_and_url("cam_1", "https://cam2.privratnik.net/80146f20_3105/preview.mp4")

    assert url == "https://cam2.privratnik.net/80146f20_3105/preview.mp4?token=tok-456"
    # headers is the ffmpeg -headers argument list.
    assert headers[0] == "-headers"
    header_block = headers[1]
    assert "Referer: https://privratnik.net/files/video-control.php" in header_block
    assert "Range: bytes=0-" in header_block
    assert "Cookie: PHPSESSID=abc123" in header_block


def test_token_not_embedded_in_stored_camera_url():
    """The stored camera URL must not contain the token (D-07)."""
    resp = _mock_response(json_data={"token": "tok-789"})
    session = mock.Mock()
    session.post.return_value = resp
    session.cookies = resp.cookies

    with mock.patch("src.capture.auth.requests.Session", return_value=session):
        mgr = SessionManager(_settings())
        cam_url = "https://cam2.privratnik.net/80146f20_3105/preview.mp4"
        mgr.stream_headers_and_url("cam_1", cam_url)

    # The token is appended at request time, never stored in the camera URL.
    assert "token=" not in cam_url
    assert "tok-789" not in cam_url


def test_extract_token_from_json():
    resp = _mock_response(json_data={"token": "json-tok"})
    assert _extract_token(resp) == "json-tok"


def test_extract_token_from_html_form():
    resp = _mock_response(text='<input type="hidden" name="token" value="html-tok">')
    assert _extract_token(resp) == "html-tok"


def test_extract_token_from_redirect():
    resp = _mock_response(text="")
    resp.history = [mock.Mock(url="https://privratnik.net/files/video-control.php?token=redir-tok")]
    assert _extract_token(resp) == "redir-tok"


def test_extract_token_returns_none_when_absent():
    resp = _mock_response(text="<html>no token here</html>")
    assert _extract_token(resp) is None
