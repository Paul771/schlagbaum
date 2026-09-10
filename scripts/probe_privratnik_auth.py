#!/usr/bin/env python
"""Standalone empirical auth probe for privratnik.net (A1/A2).

Validates the login/token contract and stream-URL assembly against the REAL
stream before the full pipeline is trusted (RESEARCH.md Open Question 1).

MANUAL-ONLY: requires real credentials in ``.env`` (PRIVRATNIK_LOGIN /
PRIVRATNIK_PASSWORD). NOT part of the automated test suite.

Never prints the raw token or password — all output is redacted.
"""

import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # pragma: no cover - dotenv is a declared dependency
    pass

import requests

AUTH_URL = "https://privratnik.net/login"  # [ASSUMED] exact endpoint
REFERER = "https://privratnik.net/files/video-control.php"
CAM_URL = "https://cam2.privratnik.net/80146f20_3105/preview.mp4"  # sample camera


def _redact(value):
    """Mask a secret value for safe printing."""
    if not value:
        return "<empty>"
    return "***"


def _redact_url(url):
    """Redact the token= query param and PHPSESSID from a URL."""
    if not url:
        return url
    parts = url.split("token=", 1)
    if len(parts) == 2:
        value = parts[1].split("&", 1)[0]
        url = parts[0] + "token=" + ("***" if value else "") + parts[1][len(value):]
    parts = url.split("PHPSESSID=", 1)
    if len(parts) == 2:
        value = parts[1].split("&", 1)[0]
        url = parts[0] + "PHPSESSID=" + ("***" if value else "") + parts[1][len(value):]
    return url


def _extract_token(response):
    """Best-effort token extraction (JSON / HTML form / redirect)."""
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

    text = response.text or ""
    for name in ("name=\"token\"", "name='token'", "name=\"access_token\"", "name='access_token'"):
        idx = text.find(name)
        if idx != -1:
            val_idx = text.find("value=", idx)
            if val_idx != -1:
                val_start = val_idx + len("value=")
                quote = text[val_start:val_start + 1]
                if quote in ('"', "'"):
                    val_end = text.find(quote, val_start + 1)
                    if val_end != -1:
                        return text[val_start + 1:val_end]

    if response.history:
        for resp in response.history:
            if "token=" in resp.url:
                return resp.url.split("token=", 1)[1].split("&", 1)[0]

    return None


def _probe_ffmpeg(url, seconds=5):
    """Optionally attempt a short ffmpeg decode; report whether frames were produced."""
    import subprocess

    cmd = [
        "ffmpeg",
        "-headers", f"Referer: {REFERER}\r\nRange: bytes=0-\r\n",
        "-i", url,
        "-vf", "fps=1",
        "-f", "image2pipe",
        "-vcodec", "mjpeg",
        "-",
    ]
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except FileNotFoundError:
        print("  [SKIP] ffmpeg not found on PATH — install via: winget install Gyan.FFmpeg")
        return None

    import time

    frames = 0
    start = time.monotonic()
    while time.monotonic() - start < seconds:
        chunk = proc.stdout.read(65536)
        if not chunk:
            break
        frames += chunk.count(b"\xff\xd8")
    proc.kill()
    proc.wait()
    return frames


def main():
    login = os.getenv("PRIVRATNIK_LOGIN")
    password = os.getenv("PRIVRATNIK_PASSWORD")
    if not login or not password:
        print("FAIL: PRIVRATNIK_LOGIN / PRIVRATNIK_PASSWORD not set in .env")
        sys.exit(1)

    print("=== Step 1: Login ===")
    session = requests.Session()
    try:
        resp = session.post(
            AUTH_URL,
            data={"login": login, "password": password},
            timeout=30,
        )
    except requests.RequestException as exc:
        print(f"FAIL: login request error: {exc}")
        sys.exit(1)
    print(f"  HTTP status: {resp.status_code}")
    print(f"  Response body shape (first 500 chars, redacted): {_redact(resp.text[:500])}")
    phpsessid = session.cookies.get("PHPSESSID", "")
    print(f"  PHPSESSID cookie set: {_redact(phpsessid)}")
    if resp.status_code >= 400:
        print("FAIL: login returned an error status")
        sys.exit(1)

    print("=== Step 2: Token extraction ===")
    token = _extract_token(resp)
    if token:
        print(f"  Token extracted: {_redact(token)}")
    else:
        print("  No token extracted — refine _extract_token against the real response")

    print("=== Step 3: Stream URL assembly ===")
    url = f"{CAM_URL}?token={token}" if token else CAM_URL
    print(f"  Stream URL (redacted): {_redact_url(url)}")

    print("=== Step 4: Optional ffmpeg decode ===")
    if token:
        frames = _probe_ffmpeg(url)
        if frames is not None:
            print(f"  Frames produced in probe window: {frames}")
        else:
            print("  ffmpeg decode skipped (no ffmpeg)")
    else:
        print("  Skipped (no token)")

    print("=== Summary ===")
    ok = bool(token) and bool(phpsessid)
    print(f"  Login: {'PASS' if resp.status_code < 400 else 'FAIL'}")
    print(f"  PHPSESSID: {'PASS' if phpsessid else 'FAIL'}")
    print(f"  Token: {'PASS' if token else 'FAIL'}")
    print(f"  Overall: {'PASS' if ok else 'FAIL'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
