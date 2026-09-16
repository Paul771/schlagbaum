---
phase: 01-stream-client-frame-buffer
reviewed: 2026-09-16T00:00:00Z
depth: standard
files_reviewed: 18
files_reviewed_list:
  - .env.example
  - .gitignore
  - config.json
  - pytest.ini
  - requirements.txt
  - scripts/probe_privratnik_auth.py
  - src/capture/auth.py
  - src/capture/frame_buffer.py
  - src/capture/stream_client.py
  - src/capture/supervisor.py
  - src/config.py
  - src/main.py
  - tests/conftest.py
  - tests/test_auth.py
  - tests/test_config.py
  - tests/test_frame_buffer.py
  - tests/test_stream_client.py
  - tests/test_supervisor.py
findings:
  critical: 1
  warning: 4
  info: 3
  total: 8
status: issues_found
---

# Phase 01: Code Review Report

**Reviewed:** 2026-09-16T00:00:00Z
**Depth:** standard
**Files Reviewed:** 18
**Status:** issues_found

## Summary

Reviewed the Phase 01 capture path: config/secrets loading, bounded drop-oldest frame buffer, privratnik auth session manager, ffmpeg subprocess stream client, and reconnect/re-auth supervisor plus tests.

Positive findings: secrets correctly sourced from `.env` via `os.getenv()` with no hardcoded credentials; `config.json` contains no login/password; `.env` is gitignored; stream URL construction uses an argv list (no shell — no command injection). The frame buffer drop-oldest logic and the supervisor's stale-detection/backoff loop are sound in isolation, and the tests largely cover the happy path well.

However, one **BLOCKER** was found in the auth → stream_client header contract that will corrupt the HTTP headers ffmpeg sends for the authed stream, and several warnings around resilience/security logging that undermine the "reconnect without misses" core value and the explicit redaction requirement.

## Critical Issues

### CR-01: `-headers` prefix is double-injected, corrupting the ffmpeg HTTP header block

**File:** `src/capture/stream_client.py:32` (and contract source `src/capture/auth.py:138-143`)
**Issue:** `SessionManager.stream_headers_and_url` returns a list whose first element is the literal string `"-headers"`:
```python
headers = [
    "-headers",
    f"Referer: {REFERER}\r\n"
    f"Range: bytes=0-\r\n"
    f"Cookie: PHPSESSID={...}\r\n",
]
```
`build_ffmpeg_cmd` then treats that list as a list of content to be joined and *also* emits its own `-headers` argv element:
```python
return [
    "ffmpeg",
    "-headers", "\r\n".join(headers) + "\r\n",   # -> "-headers\r\n<content>\r\n"
    ...
]
```
The resulting ffmpeg argv is `["ffmpeg", "-headers", "-headers\r\nReferer: ...\r\nRange: bytes=0-\r\nCookie: PHPSESSID=...\r\n\r\n", "-i", url, ...]`. The `-headers` option value therefore begins with the literal token `-headers` instead of a valid HTTP header line. The first line fed to the HTTP server is a header with no `Name: value` colon, which many servers (notably nginx) reject with a 400 Bad Request. Because the whole pipeline depends on passing `Referer`, `Range`, and the `PHPSESSID` cookie to authenticate the stream, this can prevent frames from ever loading. The single-header reference implementation in `scripts/probe_privratnik_auth.py:91-98` (`["ffmpeg", "-headers", "Referer: ...\r\nRange: bytes=0-\r\n", ...]`) shows the correct form — the value must be pure header content with no `-headers` prefix.

The unit test masks the bug: `tests/test_stream_client.py:26-39` asserts only `cmd[1] == "-headers"` and that substrings are present, so the leading `-headers\r\n` pollution goes undetected.

**Fix:** Make `stream_headers_and_url` return only the header content (a single string, not a list prefixed with `-headers`), and have `build_ffmpeg_cmd` emit the `-headers` option itself:
```python
# auth.py
return {
    "url": f"{cam_url}?token={token}",
    "headers": (f"Referer: {REFERER}\r\n"
                f"Range: bytes=0-\r\n"
                f"Cookie: PHPSESSID={self._session.cookies.get('PHPSESSID', '')}\r\n"),
}
```
```python
# stream_client.py
return [
    "ffmpeg",
    "-headers", headers,
    "-i", cam_url,
    "-vf", f"fps={fps_output}",
    "-f", "image2pipe",
    "-vcodec", "mjpeg",
    "-",
]
```
Also strengthen `test_build_ffmpeg_cmd_*` to assert the header value has no leading `-headers` line.

## Warnings

### WR-01: Uncaught auth/network exception in the supervisor kills the capture thread permanently

**File:** `src/capture/supervisor.py:46-67` (except at line 55), `src/capture/auth.py:103-111`
**Issue:** `stream_headers_and_url` → `get_session()` → `login()` performs a real `requests.post` and `r.raise_for_status()`, which raises `requests.ConnectionError` (network down), `Timeout`, or `HTTPError` (4xx/5xx). The supervisor's `except` only catches `StreamStaleError, AuthExpiredError`. Any of these exceptions therefore propagate out of the generator, out of `feed_frames`' `for frame in supervisor`, and kill the capture thread (threading prints a traceback and stops). A single transient network blip or a 401/500 at startup permanently disables that camera until manual restart — directly contradicting the reconnect/re-auth resilience and "without misses" core value. The `# pragma: no cover` `except Exception` at line 59 only guards the explicit re-auth call, not the entry path.

**Fix:** Wrap the per-iteration body so the supervisor treats all transient failures as reconnect triggers:
```python
except (StreamStaleError, AuthExpiredError, requests.RequestException, OSError):
    logger.warning("camera=%s capture error: %s; re-authing and reconnecting", camera_id, exc)
    ...
```

### WR-02: ffmpeg stderr is piped but never drained — contradicts documented Pitfall 6 mitigation

**File:** `src/capture/stream_client.py:48-53`
**Issue:** `spawn_ffmpeg` pipes `stderr` with the comment "read/drain, else pipe fills and blocks capture (Pitfall 6)". Nothing ever reads `proc.stderr` — the supervisor's reader thread only drains `stdout` via `read_jpeg_frame`. On a long-running capture, ffmpeg writes warnings/progress to stderr; once the OS pipe buffer (typically ~64KB on Windows) fills, ffmpeg blocks writing stderr and stops producing frames. This manifests as a silent periodic stall that the supervisor "heals" only by reconnect/kill — the documented mitigation is absent. This is a correctness issue because it can stop capture even when the stream is healthy.

**Fix:** Drain stderr. Simplest: `stderr=subprocess.DEVNULL` (the auth failure info is not needed at runtime). Alternatively spawn a daemon thread that reads and discards `proc.stderr`.

### WR-03: Login response body logged without redaction violates the "redact secrets in logs" requirement

**File:** `src/capture/auth.py:115-119`
**Issue:** When token extraction fails, the code logs `(r.text or "")[:500]` and the comment claims it is "redacted", but it is not — it logs up to 500 chars of the raw login response body verbatim. The project requirement explicitly states tokens/PHPSESSID must be redacted in logs. If the server's error/form response echoes a session, token, or other sensitive value (the token extraction itself reads this same body), it is written to the log in plaintext.

**Fix:** Log only shape metadata, never the raw body, or sanitize it first (strip/normalize any credential-like tokens before logging). E.g. log `len(response.text)` and an HTML-tag count, not the body content itself.

### WR-04: `token=None` is not handled — produces a literal `?token=None` stream URL

**File:** `src/capture/auth.py:137`
**Issue:** `login()` returns successfully with `token=None` whenever the login is a form-based 200-response-with-error-page or the token shape is not recognized. `stream_headers_and_url` then builds `url = f"{cam_url}?token={token}"`, producing `?token=None` (the f-string renders `None`). The supervisor treats this as a healthy start, spawns ffmpeg against a garbage URL, waits for `frame_stale_seconds`, then reconnects — instead of surfacing an auth failure (as the "never treat as no event" design intent requires). This quietly degrades the system into a silent retry loop on every auth contract mismatch, which is the exact `[ASSUMED]` (A1) risk the probe was supposed to de-risk.

**Fix:** Raise when the token cannot be extracted rather than returning `None`:
```python
if token is None:
    raise AuthExpiredError("login succeeded but no stream token was extractable")
```
(after importing the exception from `supervisor`, or defining a shared exception). Keep the shape-logging warning but follow WR-03's redaction guidance.

## Info

### IN-01: Probe `_redact()` over-masks and `_probe_ffmpeg` can block forever

**File:** `scripts/probe_privratnik_auth.py:29-33, 113-122`
**Issue:** (a) `_redact(value)` returns `"***"` for any non-empty value, so `print(f"... redacted: {_redact(resp.text[:500])}")` prints only `***` — the probe's stated purpose ("validate the response shape") is defeated; it can never reveal the shape it exists to discover. (b) `_probe_ffmpeg` does `chunk = proc.stdout.read(65536)` which blocks indefinitely on a silent stream; the `while time.monotonic() - start < seconds` time budget is only checked after a read returns, so a silent stream hangs the probe instead of timing out at `seconds`.

**Fix:** (a) Mask only the sensitive fields of the body while still exposing structure (e.g. print it with quotes/escaped, or pass the body through a real redactor). (b) Use `proc.stdout.read1` / `select`/`os.read` with a timeout, or kill the process when the wall-clock window is exceeded even mid-read.

### IN-02: `AuthExpiredError` is dead code; per-reconnect logins are doubled/tripled

**File:** `src/capture/supervisor.py:27-28`, `src/capture/supervisor.py:58`, `src/capture/auth.py:136`
**Issue:** `AuthExpiredError` is defined and caught but never raised anywhere. Additionally each reconnect performs a fresh login in `stream_headers_and_url` (line 50) *and* again in the `except` handler's `get_session()` (line 58) *and* again on the next loop iteration's `stream_headers_and_url` — three logins per reconnect cycle when the explicit re-auth in the handler is redundant. Not a correctness bug but wasted network round-trips on every outage.

**Fix:** Have the token-extraction failure raise `AuthExpiredError` (see WR-04), and delete the explicit `session_mgr.get_session()` in the `except` handler since the next iteration's `stream_headers_and_url` already forces a fresh login.

### IN-03: Reader thread can block on `frame_q.put` and leak until process exit

**File:** `src/capture/supervisor.py:90`
**Issue:** `frame_q` has `maxsize=1`; the reader thread does an unconditional `frame_q.put(frame)`. If the downstream consumer is slow (Phase 2 OCR) so the supervisor generator is not advanced for a while, the queue stays full and the reader blocks in `put`, ignoring `reader_stop`. On stale-detect, `reader.join(timeout=1.0)` times out and the thread leaks (blocked) until the process exits. Currently masked by low frame rates, but fragile once slower consumers land.

**Fix:** Use `frame_q.put(frame, timeout=0.5)` and check `reader_stop` in a loop, or make the reader `put_nowait` and drop the newest frame when full (bounded drop-oldest semantics consistent with the frame buffer).

---

_Reviewed: 2026-09-16T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
