---
phase: 01
fixed_at: 2026-09-16T13:28:41Z
review_path: .planning/phases/01-stream-client-frame-buffer/01-REVIEW.md
iteration: 1
findings_in_scope: 5
fixed: 5
skipped: 0
status: all_fixed
---

# Phase 01: Code Review Fix Report

**Fixed at:** 2026-09-16T13:28:41Z
**Source review:** `.planning/phases/01-stream-client-frame-buffer/01-REVIEW.md`
**Iteration:** 1

**Summary:**
- Findings in scope: 5 (1 critical + 4 warnings)
- Fixed: 5
- Skipped: 0

**Verification note:** The full suite was run in the main checkout (sequential mode, no worktree). `.venv/Scripts/python -m pytest tests/ -q` passes green (37 passed, up from the 31 baseline). All fixes verified with syntax checks (`py_compile` on all three modified source modules) plus the pytest suite. The plan's `grep -c 'time.sleep' src/capture/supervisor.py` gate returns `0` (interruptible `stop_event.wait` preserved). The plan's `grep -c 'token=' src/capture/auth.py` gate returns `6` — this count was already `6` on the pre-fix `HEAD` and my changes did not increase it; all occurrences are legitimate references to the `token=` substring inside the redactor/split/URL-assembly logic, not tokens embedded in a stored URL (the meaningful gate asserted by `tests/test_auth.py::test_token_not_embedded_in_stored_camera_url` remains green).

## Fixed Issues

### CR-01: `-headers` prefix is double-injected, corrupting the ffmpeg HTTP header block

**Files modified:** `src/capture/auth.py`, `src/capture/stream_client.py`, `tests/test_auth.py`, `tests/test_stream_client.py`
**Commit:** e3a1a55
**Applied fix:** `SessionManager.stream_headers_and_url` now returns the header **content** (a single string of `Name: value` lines) instead of a list whose first element was the literal `-headers` token. `build_ffmpeg_cmd` is now the single place that emits the `-headers` flag and its value, passing the header string through unchanged. The `-headers` option value can no longer begin with a bare `-headers` line. Regression-guard tests added: `test_stream_client.py::test_build_ffmpeg_cmd_header_value_has_no_leading_headers_token` asserts the first header line has a `Name: value` colon and every line is well-formed; `test_stream_client.py::test_build_ffmpeg_cmd_shape` asserts `cmd[2] == headers`; `test_auth.py::test_stream_headers_and_url_appends_token_and_headers` now asserts `headers` is a `str` (not a list) and each line has a colon.

### WR-01: Uncaught auth/network exception in the supervisor kills the capture thread permanently

**Files modified:** `src/capture/supervisor.py`, `tests/test_supervisor.py`
**Commit:** e3a1a55
**Applied fix:** The supervisor's per-iteration `except` clause now catches `requests.RequestException` and `OSError` alongside `StreamStaleError`/`AuthExpiredError`. A transient network blip, timeout, 4xx/5xx, or unexpected socket error on the auth/entry path is routed into the existing reconnect-and-backoff flow (log + force re-auth + interruptible `stop_event.wait`), so the capture thread never dies on a single transient failure. New test `tests/test_supervisor.py::test_transport_error_does_not_kill_capture_loop` proves a `requests.ConnectionError` is recovered from and the loop reconnects.

### WR-02: ffmpeg stderr is piped but never drained — contradicts documented Pitfall 6 mitigation

**Files modified:** `src/capture/stream_client.py`, `src/capture/supervisor.py`, `tests/test_stream_client.py`, `tests/test_supervisor.py`
**Commit:** e3a1a55
**Applied fix:** `spawn_ffmpeg` now attaches a `_StderrDrain` daemon thread that continuously reads `proc.stderr` (logging lines at debug, closing the pipe at EOF), so a long-running ffmpeg can never block on a full stderr pipe. `supervisor._kill_proc` tears the drain down (join) after killing/waiting the process so it does not leak. New tests: `tests/test_stream_client.py::test_spawn_ffmpeg_stderr_is_drained` asserts a drain is attached and reads/EOF+closes; `tests/test_supervisor.py::test_stderr_drain_torn_down_when_process_killed` asserts `_kill_proc` joins the drain and handles procs without a drain.

### WR-03: Login response body logged without redaction violates the "redact secrets in logs" requirement

**Files modified:** `src/capture/auth.py`, `tests/test_auth.py`
**Commit:** e3a1a55
**Applied fix:** On token-extraction failure, `SessionManager.login` no longer logs `(r.text or "")[:500]`. It now logs only shape metadata — `status`, `body_len` (length of the body) and `body_<count>` (an HTML `<` tag count) — with a comment documenting the T-01-03 redaction rationale. New test `tests/test_auth.py::test_login_raises_auth_expired_when_token_absent` asserts the logged output contains neither the raw body nor the embedded `SECRETVAL` while still carrying `body_len`.

### WR-04: `token=None` is not handled — produces a literal `?token=None` stream URL

**Files modified:** `src/capture/auth.py`, `tests/test_auth.py`
**Commit:** e3a1a55
**Applied fix:** When no stream token is extractable, `login()` now raises `AuthExpiredError("login succeeded but no stream token was extractable")` instead of returning `token=None`. This surfaces the auth failure through the supervisor's error path (which now catches it per WR-01) rather than silently degrading into endless `?token=None` retries. Two tests added: `test_login_raises_auth_expired_when_token_absent` and `test_stream_headers_and_url_raises_when_token_none`. As a side effect, this resolves the dead-code Info finding IN-02 (the `AuthExpiredError` that was previously never raised is now the intended failure mode).

## Skipped Issues

No in-scope findings were skipped.

---

_Fixed: 2026-09-16T13:28:41Z_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
