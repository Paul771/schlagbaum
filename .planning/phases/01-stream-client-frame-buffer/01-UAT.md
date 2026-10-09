---
status: complete
phase: 01-stream-client-frame-buffer
source:
  - .planning/phases/01-stream-client-frame-buffer/01-01-SUMMARY.md
  - .planning/phases/01-stream-client-frame-buffer/01-02-SUMMARY.md
started: 2026-10-03T19:33:24Z
updated: 2026-10-03T20:00:00Z
---

## Current Test

[testing complete]

## Tests

### 1. Load configuration and secrets
expected: Starting the app reads both camera URLs from config.json and reads PRIVRATNIK_LOGIN/PRIVRATNIK_PASSWORD from the environment. Credentials are not stored in config.json, and .env is ignored by Git.
result: pass

### 2. Start two camera capture channels
expected: Running the app starts capture-cam_1 and capture-cam_2; each camera receives frames tagged cam_1 or cam_2 in its own buffer.
result: pass

### 3. Authenticate and build the stream request
expected: With valid credentials, login obtains a non-empty token and PHPSESSID cookie; the stream request includes the token, Referer, Range, and Cookie headers, while the token is not stored in the camera URL.
result: pass

### 4. Decode frames and preserve per-camera buffers
expected: ffmpeg decodes the preview stream into frames. When a camera buffer reaches its maximum size, the oldest frame is dropped and the newest frames remain available without blocking capture.
result: pass

### 5. Recover from stream failure
expected: When a stream stops or the session expires, the supervisor detects the failure, kills ffmpeg, re-authenticates, waits with backoff, and resumes frame capture without a manual restart.
result: pass

### 6. Run the live authentication probe
expected: scripts/probe_privratnik_auth.py completes with Overall: PASS after reporting a successful login, a PHPSESSID cookie, a non-empty token, and frame production through ffmpeg.
result: pass

## Summary

total: 6
passed: 6
issues: 0
pending: 0
skipped: 0
blocked: 0

## Gaps

[none yet]

## Evidence

- `scripts/probe_privratnik_auth.py`: login HTTP 200, PHPSESSID set, per-camera token extracted from video-control.php, ffmpeg produced 1 frame, `Overall: PASS`.
- `python -m scripts.probe_capture_2_cameras --duration 20/25`: `cam_1` and `cam_2` each decoded frames and restarted ffmpeg after EOF (`ffmpeg_starts: 2`) with no errors — recovery without manual restart.
- `python -m src.main` (~10s smoke): both `capture-cam_1` and `capture-cam_2` threads started.
- `python -m pytest tests/ -q`: 49 passed.
