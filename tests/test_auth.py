"""Tests for the privratnik.net session manager (STREAM-02, D-01/D-07).

All tests mock ``requests`` — no live credentials or network access.
"""

from unittest import mock

import pytest

from src.capture.auth import AUTH_URL, AuthExpiredError, SessionManager, _extract_camera_tokens, _extract_token


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


def test_login_uses_authorization_form_endpoint_and_field_names():
    resp = _mock_response(json_data={"token": "tok-123"})
    session = mock.Mock()
    session.post.return_value = resp
    session.cookies = resp.cookies

    with mock.patch("src.capture.auth.requests.Session", return_value=session):
        SessionManager(_settings()).login()

    assert AUTH_URL == "https://privratnik.net/login.php"
    assert session.post.call_args.args[0] == AUTH_URL
    assert session.post.call_args.kwargs["data"] == {
        "phone": "test_login",
        "pass": "test_password",
    }


def test_extract_camera_tokens_from_video_control_page():
    resp = _mock_response(
        text=(
            '<img data-url="https://cam2.privratnik.net/cam-1/preview.mp4?token=tok-1">'
            '<img data-url="https://cam2.privratnik.net/cam-2/preview.mp4?token=tok-2">'
        )
    )

    assert _extract_camera_tokens(resp) == {
        "https://cam2.privratnik.net/cam-1/preview.mp4": "tok-1",
        "https://cam2.privratnik.net/cam-2/preview.mp4": "tok-2",
    }


def test_login_fetches_video_control_page_and_indexes_camera_tokens():
    login_resp = _mock_response(text="<html>redirected</html>")
    video_resp = _mock_response(
        text=(
            '<img data-url="https://cam2.privratnik.net/cam-1/preview.mp4?token=tok-1">'
            '<img data-url="https://cam2.privratnik.net/cam-2/preview.mp4?token=tok-2">'
        )
    )
    session = mock.Mock()
    session.post.return_value = login_resp
    session.get.return_value = video_resp
    session.cookies = login_resp.cookies

    with mock.patch("src.capture.auth.requests.Session", return_value=session):
        mgr = SessionManager(_settings())
        _, token = mgr.login()

    assert token == "tok-1"
    assert mgr._camera_tokens == {
        "https://cam2.privratnik.net/cam-1/preview.mp4": "tok-1",
        "https://cam2.privratnik.net/cam-2/preview.mp4": "tok-2",
    }
    session.get.assert_called_once()


def test_stream_headers_uses_token_for_requested_camera():
    login_resp = _mock_response(text="<html>redirected</html>")
    video_resp = _mock_response(
        text=(
            '<img data-url="https://cam2.privratnik.net/cam-1/preview.mp4?token=tok-1">'
            '<img data-url="https://cam2.privratnik.net/cam-2/preview.mp4?token=tok-2">'
        )
    )
    session = mock.Mock()
    session.post.return_value = login_resp
    session.get.return_value = video_resp
    session.cookies = login_resp.cookies

    with mock.patch("src.capture.auth.requests.Session", return_value=session):
        mgr = SessionManager(_settings())
        url, _ = mgr.stream_headers_and_url(
            "cam_2", "https://cam2.privratnik.net/cam-2/preview.mp4"
        )

    assert url.endswith("?token=tok-2")


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
    # headers is the PURE header content string (CR-01) — a single str,
    # NOT a list prefixed with "-headers". build_ffmpeg_cmd emits the flag.
    assert isinstance(headers, str)
    assert not headers.startswith("-headers")
    header_block = headers
    assert "Referer: https://privratnik.net/files/video-control.php" in header_block
    assert "Range: bytes=0-" in header_block
    assert "Cookie: PHPSESSID=abc123" in header_block
    # Every header line is a well-formed "Name: value" pair (CR-01).
    for line in header_block.rstrip("\r\n").split("\r\n"):
        assert ": " in line, f"malformed header line {line!r}"


def test_login_raises_auth_expired_when_token_absent():
    """WR-04: a login with no extractable token must raise AuthExpiredError,
    not return token=None (which would otherwise produce ?token=None).
    It must log only shape metadata, never the raw response body (WR-03)."""
    body = "<html>bad login form error; token=SECRETVAL embedded in a script</html>"
    resp = _mock_response(text=body)
    session = mock.Mock()
    session.post.return_value = resp
    session.get.return_value = _mock_response(text="<html>no token anywhere</html>")
    session.cookies = resp.cookies

    with mock.patch("src.capture.auth.requests.Session", return_value=session):
        with mock.patch("src.capture.auth.logger.warning") as warn:
            with pytest.raises(AuthExpiredError):
                SessionManager(_settings()).login()

    # The log must carry shape metadata, never the raw body or secret values.
    assert warn.called
    args, kwargs = warn.call_args
    joined = " ".join(str(a) for a in args) + " " + " ".join(str(v) for v in kwargs.values())
    assert "SECRETVAL" not in joined
    assert "token=SECRETVAL" not in joined
    # The secret is redacted; only shape metadata remains.
    assert "body_len" in joined


def test_stream_headers_and_url_raises_when_token_none():
    """WR-04: if no token can be extracted, stream_headers_and_url must surface
    the auth failure rather than produce a literal ?token=None URL."""
    resp = _mock_response(text="<html>no token anywhere</html>")
    session = mock.Mock()
    session.post.return_value = resp
    session.get.return_value = _mock_response(text="<html>no token anywhere</html>")
    session.cookies = resp.cookies

    with mock.patch("src.capture.auth.requests.Session", return_value=session):
        mgr = SessionManager(_settings())
        with pytest.raises(AuthExpiredError):
            mgr.stream_headers_and_url(
                "cam_1", "https://cam2.privratnik.net/80146f20_3105/preview.mp4"
            )


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
