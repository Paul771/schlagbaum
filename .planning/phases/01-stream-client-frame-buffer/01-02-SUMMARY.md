---
phase: 01-stream-client-frame-buffer
plan: 02
subsystem: capture
tags: [auth, ffmpeg, stream-client, supervisor, reconnect, backoff]
dependency_graph:
  requires:
    - phase: 01-01
      provides: [config.py load_settings, FrameBuffer, config.json, .gitignore, .env.example, requirements.txt, pytest.ini]
  provides: [SessionManager, build_ffmpeg_cmd, spawn_ffmpeg, read_jpeg_frame, run_capture_with_supervisor, feed_frames, main, probe_privratnik_auth.py]
  affects: [phase-2-barrier-detector, phase-3-event-store]
tech-stack:
  added: [requests.Session (auth), ffmpeg subprocess (capture), cv2.imdecode (JPEG decode)]
  patterns: [re-invocable get_session, reconnect/re-auth supervisor with exponential backoff, MJPG-over-pipe frame parsing, feed_frames → FrameBuffer push]
key-files:
  created: [src/capture/auth.py, src/capture/stream_client.py, src/capture/supervisor.py, src/main.py, scripts/probe_privratnik_auth.py, tests/test_auth.py, tests/test_stream_client.py, tests/test_supervisor.py]
  modified: []
decisions:
  - "SessionManager interface is login()/get_session()/stream_headers_and_url(camera_id, cam_url) — no live_headers()/invalidate() (checker iter 2)"
  - "read_jpeg_frame implements MJPG 0xFFD8..0xFFD9 byte-buffered framing (A3) — the reference's NotImplementedError"
  - "Supervisor uses a reader thread + queue so silent stream death (no frames, no EOF) is detected via frame_stale_seconds without blocking on a hung pipe"
  - "Backoff resets to backoff_initial on every healthy frame, not just on clean stream end"
metrics:
  duration: "2026-09-10T15:45:00Z to 2026-09-10T16:05:00Z"
  completed_date: "2026-09-10"
status: complete
actuals:
  tokens: 9581
  tasks: 3
  commits: 3
  plan_head_before: e09ff81b88ac86876a19303f61f559e5be56b239
---

# Phase 01 Plan 02: Capture Path (Auth + Stream Client + Supervisor) Summary

## One-liner

privratnik.net auto-login session manager, ffmpeg subprocess stream client with MJPG pipe framing, and a reconnect/re-auth supervisor with exponential backoff — wired in main.py for two cameras and validated by mocked unit tests.

## What Was Built

The phase's highest-risk slice — the capture path that authenticates, decodes frames via ffmpeg, and self-heals on stream/token expiry:

- **`src/capture/auth.py`** — `SessionManager` (D-01): `login()` POSTs credentials to `AUTH_URL` via `requests.Session`, persists the PHPSESSID cookie, and extracts a token via a best-effort parser (JSON / HTML form / redirect). `get_session()` is re-invocable and always re-logins (Pitfall 1). `stream_headers_and_url(camera_id, cam_url)` appends `?token=` at request time (D-07) and returns the ffmpeg `-headers` list with Referer/Range/PHPSESSID. Logged URLs are redacted (V7).
- **`src/capture/stream_client.py`** — `build_ffmpeg_cmd()` (D-04/D-05/D-08), `spawn_ffmpeg()` (`stdout=PIPE`, `stderr=PIPE`, `CREATE_NO_WINDOW` — Pitfall 6), and `read_jpeg_frame()` which implements the MJPG `0xFFD8..0xFFD9` byte-buffered framing (A3) that was `NotImplementedError` in the reference.
- **`src/capture/supervisor.py`** — `run_capture_with_supervisor()` (STREAM-03/D-03): detects "no valid frame for N seconds" (Pitfall 1), tears down ffmpeg (`proc.kill()` + `proc.wait()` in a `finally`), forces re-auth via `get_session()`, waits with interruptible `stop_event.wait(backoff)` (1s→2s→…→60s), and reconnects. `StreamStaleError`/`AuthExpiredError` are named error conditions. A reader thread + queue detects silent stream death without blocking on a hung pipe.
- **`src/main.py`** — `main()`: loads settings, pre-flights ffmpeg (clear "install via: winget install Gyan.FFmpeg" + exit non-zero if missing — Pitfall 2), instantiates one `FrameBuffer` per camera, starts a supervisor thread per camera, and `feed_frames()` pushes every supervisor-yielded frame into its camera's bounded drop-oldest buffer (D-09/D-10). `FrameBuffer.pop()` is exposed for Phase 2 but not consumed.
- **`scripts/probe_privratnik_auth.py`** — standalone empirical probe validating A1 (login/token) and A2 (expiry) against the real stream. Manual-only; redacts token/password in all output; compiles clean.
- **Tests** — `tests/test_auth.py` (mocked login sets cookie/token, get_session re-invokes login, tokenized URL + headers, token not embedded in stored URL), `tests/test_stream_client.py` (ffmpeg cmd shape, header injection, spawn uses pipes, synthetic JPEG decode incl. split frames, EOF), `tests/test_supervisor.py` (reconnect/re-auth, backoff 1→2→4, reset on healthy frame, cap at max, stop_event.wait not time.sleep, proc killed on reconnect, feed_frames pushes tagged frames).

## Verification

- Full suite: `.venv/Scripts/python -m pytest tests/ -q` → **31 passed**
- `grep -c 'time.sleep' src/capture/supervisor.py` → 0 (interruptible wait used)
- `scripts/probe_privratnik_auth.py` compiles (py_compile, no SyntaxError)
- `grep -c 'token=' src/capture/auth.py` → 6 (see deviation note below)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Supervisor staleness detection needed a reader thread**
- **Found during:** Task 2
- **Issue:** The plan's reference `_yield_frames_or_raise` skeleton called `read_jpeg_frame(proc)` synchronously. A blocking read on a hung ffmpeg stdout pipe would never detect "no valid frame for N seconds" (Pitfall 1) — the anti-silent-death guard would be defeated exactly when the stream goes dark without EOF.
- **Fix:** The supervisor spawns a daemon reader thread that decodes frames off the pipe into a small queue; the supervisor polls the queue with a timeout and raises `StreamStaleError` when `clock() - last_frame > frame_stale_seconds`. `clock` and `read_frame` are injectable for the fake-clock/fake-capture tests.
- **Files modified:** `src/capture/supervisor.py`
- **Commit:** 34986b8

**2. [Rule 3 - Blocking] Backoff reset semantics**
- **Found during:** Task 2
- **Issue:** The reference skeleton reset `backoff = 1` only after the frame-yield loop completed cleanly, which never happens for a finite MP4 preview (it always ends in EOF → stale). Backoff would never reset on a healthy stream.
- **Fix:** Reset `backoff = backoff_initial` on every healthy frame yielded, so a producing stream keeps backoff at minimum.
- **Files modified:** `src/capture/supervisor.py`
- **Commit:** 34986b8

### Plan-verification note (not a code deviation)

The plan's `<verification>` includes `grep -c 'token=' src/capture/auth.py` returns 0. This check is unsatisfiable as literally written: the reference code (RESEARCH.md L361) and the D-07 requirement both require constructing `f"{cam_url}?token={token}"` at request time, which necessarily contains the substring `token=`. The 6 matches are all legitimate uses of the `token=` query-param name (URL construction, redaction, redirect-token extraction) — none is a hardcoded token value embedded in a stored camera URL. The real D-07 guarantee (token not embedded in stored URL) is asserted by `tests/test_auth.py::test_token_not_embedded_in_stored_camera_url`.

## Auth Gates

None — all tests use mocks; no live credentials or network access were required. The probe script (Task 3) is manual-only and was not run against the live stream (no credentials available), per the plan's note.

## Known Stubs

None — all created files are fully wired and tested. The probe script's `_extract_token` is a best-effort parser that logs the raw response shape for refinement against the real stream (an intentional, documented empirical checkpoint, not a stub).

## Threat Flags

None — no security-relevant surface introduced beyond the plan's threat model. T-01-03 (token/PHPSESSID redaction in logs) is implemented in `auth.py`'s `_redact_url` and the probe script; T-01-04 (backoff caps retry rate) and T-01-05 (proc.kill+wait in finally, stderr drained) are implemented in the supervisor; T-01-06 (token appended at request time, never stored) is implemented in `stream_headers_and_url` and asserted by test.

## Self-Check: PASSED

- [x] `.venv/Scripts/python -m pytest tests/ -q` → 31 passed
- [x] `grep -c 'time.sleep' src/capture/supervisor.py` → 0
- [x] `scripts/probe_privratnik_auth.py` compiles
- [x] Commits exist: c4eac63, 34986b8, ac1aa84

## Self-Check: PASSED

All 8 created files exist on disk; all 3 task commits (c4eac63, 34986b8, ac1aa84) verified in git history.
