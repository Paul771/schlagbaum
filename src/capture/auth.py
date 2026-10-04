"""privratnik.net session manager (D-01, STREAM-02).

Auto-login flow: POST credentials to the auth endpoint, persist the PHPSESSID
cookie via ``requests.Session``, and extract a stream token. ``get_session()``
is re-invocable on reconnect (Pitfall 1) and always performs a fresh login —
a cached session/token is never reused across reconnects.

The exact login/token contract is ``[ASSUMED]`` (A1) and must be validated
against the real stream via ``scripts/probe_privratnik_auth.py``. The token
extraction here is a best-effort parser that handles HTML form / JSON /
redirect shapes and logs the raw response shape for the probe to refine.
"""

import logging
import re
from urllib.parse import parse_qs

import requests

# [ASSUMED] exact endpoint — see Open Question 1 in RESEARCH.md.
AUTH_URL = "https://privratnik.net/login.php"
REFERER = "https://privratnik.net/files/video-control.php"

logger = logging.getLogger(__name__)


class AuthExpiredError(Exception):
    """Raised when the session/token is detected as expired or unextractable.

    Defined here (rather than in ``supervisor``) because it is raised at the
    point of token extraction/log-in where the failure is actually detected.
    ``supervisor`` imports and catches it to drive the reconnect loop.
    """


def _redact_url(url):
    """Redact the ``token=`` query param and PHPSESSID from a URL for logging (V7)."""
    if not url:
        return url
    # Mask the token query param value.
    parts = url.split("token=", 1)
    if len(parts) == 2:
        value = parts[1].split("&", 1)[0]
        url = parts[0] + "token=" + ("***" if value else "") + parts[1][len(value):]
    # Mask any PHPSESSID cookie value embedded in the URL.
    parts = url.split("PHPSESSID=", 1)
    if len(parts) == 2:
        value = parts[1].split("&", 1)[0]
        url = parts[0] + "PHPSESSID=" + ("***" if value else "") + parts[1][len(value):]
    return url


def _extract_token(response):
    """Best-effort token extraction from the login response.

    Handles JSON (``{"token": ...}``), HTML form fields (``name="token"`` /
    ``name="access_token"``), and redirect targets. Returns the token string
    or ``None`` if no token could be found. The exact contract is ``[ASSUMED]``
    (A1) — the probe script refines this against the real stream.
    """
    # JSON body.
    try:
        data = response.json()
        if isinstance(data, dict):
            for key in ("token", "access_token", "auth_token", "data"):
                if key in data:
                    val = data[key]
                    if isinstance(val, dict):
                        val = val.get("token") or val.get("access_token")
                    if isinstance(val, str) and val:
                        return val
    except ValueError:
        pass

    # HTML form field.
    text = response.text or ""
    for name in ("name=\"token\"", "name='token'", "name=\"access_token\"", "name='access_token'"):
        idx = text.find(name)
        if idx != -1:
            # Look for value="..." after the field name.
            val_idx = text.find("value=", idx)
            if val_idx != -1:
                val_start = val_idx + len("value=")
                quote = text[val_start:val_start + 1]
                if quote in ('"', "'"):
                    val_end = text.find(quote, val_start + 1)
                    if val_end != -1:
                        return text[val_start + 1:val_end]

    # Redirect target containing a token.
    if response.history:
        for resp in response.history:
            if "token=" in resp.url:
                return resp.url.split("token=", 1)[1].split("&", 1)[0]

    return None


VIDEO_CONTROL_URL = "https://privratnik.net/files/video-control.php"


def _extract_camera_tokens(response):
    tokens = {}
    for data_url in re.findall(r'data-url\s*=\s*["\']([^"\']+)["\']', response.text or "", re.IGNORECASE):
        base_url, _, query = data_url.partition("?")
        token = (parse_qs(query).get("token") or [None])[0]
        if token:
            tokens[base_url] = token
    return tokens


class SessionManager:
    """Auto-login session manager for privratnik.net (D-01)."""

    def __init__(self, settings):
        self.settings = settings
        self._session = None
        self._token = None
        self._camera_tokens = {}

    def login(self):
        """Log in to privratnik.net, store PHPSESSID cookie + token.

        Returns ``(session, token)``. Raises on HTTP error so an auth failure
        is surfaced, never treated as "no event".
        """
        s = requests.Session()  # persists PHPSESSID cookie (Pitfall 1)
        r = s.post(
            AUTH_URL,
            data={
                "phone": self.settings["login"],
                "pass": self.settings["password"],
            },
            timeout=30,
        )
        r.raise_for_status()
        token = _extract_token(r)
        camera_tokens = {}
        if token is None:
            page = s.get(VIDEO_CONTROL_URL, timeout=30)
            page.raise_for_status()
            camera_tokens = _extract_camera_tokens(page)
            token = next(iter(camera_tokens.values()), None)
        if token is None:
            # Log only shape metadata, never the raw response body (T-01-03 —
            # the body may echo a token/session; redact credential-like tokens
            # before logging). The token extraction itself already reads this
            # body, so exposing it in the log defeats the redaction requirement.
            body = r.text or ""
            logger.warning(
                "No token extracted from login response; status=%s body_len=%d bodies=%d",
                r.status_code,
                len(body),
                body.count("<"),
            )
            raise AuthExpiredError(
                "login succeeded but no stream token was extractable "
                f"(status={r.status_code})"
            )
        self._session = s
        self._token = token
        self._camera_tokens = camera_tokens
        return s, token

    def get_session(self):
        """Re-invocable on reconnect (Pitfall 1). Returns a fresh authed session."""
        self._session, self._token = self.login()  # always fresh — don't reuse on reconnect
        return self._session, self._token

    def _build_headers(self):
        """The pure ``Name: value`` header block (Referer / Range / Cookie)."""
        return (
            f"Referer: {REFERER}\r\n"
            f"Range: bytes=0-\r\n"
            f"Cookie: PHPSESSID={self._session.cookies.get('PHPSESSID', '')}\r\n"
        )

    def stream_headers_and_url(self, camera_id, cam_url):
        """Assemble the ffmpeg header content + full stream URL with live token (D-07).

        The token is appended to the URL at request time and never stored
        embedded in the camera URL. Returns ``(url, headers)`` where ``headers``
        is the **pure header content** (a single string of ``Name: value`` lines)
        that the caller's ``build_ffmpeg_cmd`` feeds as the value of ffmpeg's
        ``-headers`` option. This method does NOT return an argv element —
        emitting the ``-headers`` flag is the single job of ``build_ffmpeg_cmd``,
        so the two sides agree on one contract (no double ``-headers`` injection).
        """
        _, token = self.get_session()
        token = self._camera_tokens.get(cam_url, token)
        url = f"{cam_url}?token={token}"  # token appended at request time, not stored
        headers = self._build_headers()
        logger.debug("stream_headers_and_url camera=%s url=%s", camera_id, _redact_url(url))
        return url, headers

    def auth_context(self, camera_id, cam_url):
        """Single-login ``(session, url, headers)`` for repeated callers.

        Same contract as :meth:`stream_headers_and_url` but built from exactly
        one ``login()``, handing back the authenticated ``requests.Session`` so a
        polling caller can keep HTTP keep-alive (and PHPSESSID) alive across
        requests — logging in per request measured ~3.5x slower per snapshot.
        """
        self.login()
        token = self._camera_tokens.get(cam_url, self._token)
        url = f"{cam_url}?token={token}"  # token appended at request time, not stored
        logger.debug("auth_context camera=%s url=%s", camera_id, _redact_url(url))
        return self._session, url, self._build_headers()
