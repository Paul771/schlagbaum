---
phase: 01-stream-client-frame-buffer
verified: 2026-09-23T20:52:35Z
status: passed
score: 7/7 must-haves verified
covered_files:
  - .planning/REQUIREMENTS.md
  - .planning/ROADMAP.md
  - .planning/phases/01-stream-client-frame-buffer/01-01-PLAN.md
  - .planning/phases/01-stream-client-frame-buffer/01-01-SUMMARY.md
  - .planning/phases/01-stream-client-frame-buffer/01-02-PLAN.md
  - .planning/phases/01-stream-client-frame-buffer/01-02-SUMMARY.md
  - .planning/phases/01-stream-client-frame-buffer/01-CONTEXT.md
  - .planning/phases/01-stream-client-frame-buffer/01-REVIEW.md
  - .planning/phases/01-stream-client-frame-buffer/01-REVIEW-FIX.md
  - .planning/phases/01-stream-client-frame-buffer/01-UAT.md
  - .env.example
  - .gitignore
  - config.json
  - pytest.ini
  - requirements.txt
  - scripts/probe_capture_2_cameras.py
  - scripts/probe_privratnik_auth.py
  - src/capture/auth.py
  - src/capture/frame_buffer.py
  - src/capture/stream_client.py
  - src/capture/supervisor.py
  - src/config.py
  - src/main.py
  - tests/conftest.py
  - tests/test_auth.py
  - tests/test_capture_diagnostic.py
  - tests/test_config.py
  - tests/test_frame_buffer.py
  - tests/test_probe.py
  - tests/test_stream_client.py
  - tests/test_supervisor.py
covered_digest: "v1:sha256:c5fd9e8c8caec2ee5654d879079fe32927469e7aa60d800abf28c4fbea43164c"
behavior_unverified: 0
overrides_applied: 0
re_verification:
  previous_status: "gaps_found"
  previous_score: 4/7
  gaps_closed:
    - "The auth flow is validated against the real stream via a manual probe script before the full pipeline is trusted"
  gaps_remaining: []
  regressions: []
decision_coverage:
  honored: 10
  total: 10
  not_honored: []
---

# Phase 1: Stream Client + Frame Buffer Verification Report

**Phase Goal:** Two cameras capture continuous frames through the privratnik.net proxy with working auth, an auto-reconnect/re-auth loop, a bounded drop-oldest queue that keeps capture non-blocking, and a config/secrets structure feeding the first token.

**Verified:** 2026-09-23T20:52:35Z
**Status:** passed
**Re-verification:** Yes — after live gap-closure evidence
**Mode:** mvp. The ROADMAP goal is not expressed in the canonical User Story format, so the report uses the standard goal-backward observable-truths method against the Roadmap success criteria and requirements.

## Summary

The prior blocker is closed against the current working tree and fresh live evidence. `scripts/probe_privratnik_auth.py` now validates the real `/login.php` contract with `phone`/`pass`, receives HTTP 200 and a PHPSESSID cookie, extracts a per-camera token from `video-control.php`, and produces one decoded frame with ffmpeg; the probe reports `Overall: PASS`. The two-camera diagnostic produced 2 frames and 2 ffmpeg starts for each camera in a 20-second run with no errors, demonstrating that the real preview stream is reopened and frame delivery resumes. `python -m src.main` started both `capture-cam_1` and `capture-cam_2` threads and remained running through an 8-second smoke window.

The current implementation also uses the per-camera tokens returned by `video-control.php`, redacts probe output while preserving response shape, and applies `fps=...:round=up` to short preview streams. A fresh full-suite run completed with **49 passed in 0.46s**. No remaining phase-goal blocker was found.

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Frames are pushed into per-camera bounded drop-oldest buffers non-blockingly and remain camera-distinguishable | ✓ VERIFIED | `FrameBuffer.push` drops the oldest item before a non-blocking put and tags `camera_id` at enqueue. The full suite exercises bounds, drop-oldest, race handling, FIFO order, isolation, and `feed_frames`; no skipped tests. |
| 2 | Non-secret settings load from `config.json`; secrets load from `.env` via `os.getenv()`; `.env` is ignored and not committed | ✓ VERIFIED | `src/config.py:19-32`; `git check-ignore -v .env` resolves to `.gitignore:2`; `.env` is not tracked; an exact-value scan of tracked files found no current login/password credential leaks; config tests pass. |
| 3 | ffmpeg receives Referer, Range, and PHPSESSID headers, with the live token appended only at request time | ✓ VERIFIED | `auth.py:170-190` builds a pure header block and request-time tokenized URL; `stream_client.py:27-46` is the sole `-headers` emitter. Real probe ffmpeg produced 1 frame with authenticated headers. |
| 4 | The supervisor detects stale/ended streams, tears down ffmpeg, re-authenticates, and reconnects with exponential backoff | ✓ VERIFIED | `supervisor.py:45-71,74-133`; active tests cover stale recovery, transport errors, 1→2→4 backoff, cap, reset, process kill/wait, and stderr-drain teardown. The real 20-second run showed two ffmpeg starts and resumed frames for each camera. |
| 5 | Authentication persists PHPSESSID and obtains per-camera stream tokens through `requests.Session` | ✓ VERIFIED | Fresh live probe: `/login.php` HTTP 200, PHPSESSID `***`, token from `video-control.php` `***`, tokenized URL redacted, ffmpeg frame count 1, `Overall: PASS`. Unit tests additionally cover endpoint/form fields and per-camera token selection. |
| 6 | Both cameras connect and deliver frames without manual intervention | ✓ VERIFIED | Fresh `-m scripts.probe_capture_2_cameras --duration 20`: `cam_1 = 2 frames / 2 starts / no error`; `cam_2 = 2 frames / 2 starts / no error`. Fresh `python -m src.main` smoke remained `Running` after 8 seconds and started both camera threads. |
| 7 | The real-stream auth probe validates the flow before the pipeline is trusted | ✓ VERIFIED | Fresh execution of `scripts/probe_privratnik_auth.py` completed all four steps and reported Login/PHPSESSID/Token/Overall PASS; raw secret values were not printed. |

**Score:** 7/7 truths verified (0 present-but-behavior-unverified)

### Roadmap Success Criteria Coverage

| Roadmap criterion | Status | Evidence |
|------------------|--------|----------|
| Both cameras connect and deliver frames continuously without manual intervention | ✓ VERIFIED | Both cameras produced frames in the live 20-second diagnostic; `src.main` stayed running and started both capture threads. |
| Session/token failure automatically re-authenticates and reconnects, resuming capture | ✓ VERIFIED | Real preview completion exercised two ffmpeg starts per camera with frames and no errors; supervisor unit tests separately exercise stale/auth/transport recovery and backoff transitions. |
| Bounded drop-oldest queue prevents a slow downstream stage from blocking capture | ✓ VERIFIED | `FrameBuffer` never exceeds `maxsize`, drops oldest, and uses non-blocking operations; active tests exercise full-buffer and race paths. |
| Every frame is tagged with the correct `camera_id` | ✓ VERIFIED | Frames are tagged at enqueue; feed-path and per-camera isolation tests pass; the live diagnostic runs independent cam_1/cam_2 channels. |
| Tokens, camera URLs, and secrets are sourced from env/config and secrets are not committed | ✓ VERIFIED | Camera base URLs are in `config.json`; tokens are attached at request time; credentials come from environment; `.env` is ignored and untracked; no exact current credential value appears in tracked non-planning files. |

## Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/config.py` | Config + environment secret loading | ✓ VERIFIED | Exists, substantive, imported by main/probes, and backed by active tests. |
| `src/capture/frame_buffer.py` | Per-camera bounded drop-oldest queue | ✓ VERIFIED | Exists, substantive, wired through `feed_frames`, with behavioral tests. |
| `src/capture/auth.py` | Login/session/per-camera token/header construction | ✓ VERIFIED | Exists, substantive, used by supervisor/probes, and live-probed. |
| `src/capture/stream_client.py` | ffmpeg command, spawn, stderr drain, JPEG decode | ✓ VERIFIED | Exists, substantive, used by main/diagnostic, and live ffmpeg produced frames. |
| `src/capture/supervisor.py` | Stale detection, reconnect/re-auth, backoff, teardown | ✓ VERIFIED | Exists, substantive, wired per camera, with active transition tests and real stream restarts. |
| `src/main.py` | Two-camera entrypoint and buffer wiring | ✓ VERIFIED | Exists, substantive, fresh smoke started both threads and remained running. |
| `scripts/probe_privratnik_auth.py` | Redacted real auth/decode probe | ✓ VERIFIED | Fresh live run completed with `Overall: PASS`. |
| `scripts/probe_capture_2_cameras.py` | Real two-camera capture diagnostic | ✓ VERIFIED | Fresh module invocation produced frames for both cameras. Direct script-path invocation is an Info-level import-path issue described below; use `python -m scripts.probe_capture_2_cameras`. |
| `config.json` | Non-secret camera and capture settings | ✓ VERIFIED | Contains cam_1/cam_2 and tuning; no login/password/token. |
| `.gitignore` / `.env.example` | Secret safety and documented keys | ✓ VERIFIED | `.env` ignored, empty example values, current credentials absent from tracked files. |
| `requirements.txt` / `pytest.ini` | Pinned test environment | ✓ VERIFIED | Exact pins and configured test discovery/import path. |

## Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `video-control.php` response | Per-camera stream URL | `_extract_camera_tokens` keyed by exact base URL | ✓ WIRED | `SessionManager.stream_headers_and_url` selects the token for the requested camera; active test verifies cam_2 receives tok-2. |
| `src/capture/auth.py` | `src/capture/stream_client.py` | URL + pure header string passed to `build_ffmpeg_cmd` | ✓ WIRED | `stream_client.py` alone emits `-headers`; live ffmpeg used authenticated headers and produced a frame. |
| `src/capture/supervisor.py` | `src/capture/auth.py` | Fresh login/token request on every reconnect | ✓ WIRED | `stream_headers_and_url` calls `get_session`; reconnect path also enters a fresh auth request. |
| `src/capture/supervisor.py` | `src/capture/frame_buffer.py` | `main.feed_frames` pushes every yielded frame | ✓ WIRED | Behavioral test verifies all frames land in the camera buffer with `camera_id`. |
| `src/main.py` | Two real camera streams | One supervisor thread and FrameBuffer per `settings["cameras"]` item | ✓ WIRED | Fresh smoke started both camera threads; two-camera diagnostic produced real frames. |

## Data-Flow Trace (Level 4)

| Artifact | Data variable | Source | Produces real data | Status |
|----------|---------------|--------|--------------------|--------|
| FrameBuffer | `frame.data` | Live ffmpeg stdout → `read_jpeg_frame`/`cv2.imdecode` → supervisor → `feed_frames` | Yes | ✓ FLOWING — live diagnostic decoded real frames; feed path is behaviorally tested. |
| FrameBuffer | `frame.camera_id` | Per-camera `FrameBuffer.camera_id` at enqueue | Yes | ✓ FLOWING |
| Stream URL | `token` | Login + per-camera `data-url` values on `video-control.php` | Yes | ✓ FLOWING — token is appended only at request time. |
| Settings | `cameras`, timing, ffmpeg path | `config.json` | Yes | ✓ FLOWING |
| Settings | `login`, `password` | Environment loaded from gitignored `.env` | Yes | ✓ FLOWING — exact-value leak scan found no tracked duplicate. |

No user-visible value terminates in a static return, hardcoded sample, or mock on the production path.

## Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Full suite green | `.venv/Scripts/python.exe -m pytest tests/ -q` | `49 passed in 0.46s` | ✓ PASS |
| Real auth and frame decode | `.venv/Scripts/python.exe scripts/probe_privratnik_auth.py` | HTTP 200; PHPSESSID set; token extracted; 1 frame; `Overall: PASS` | ✓ PASS |
| Real two-camera capture and restart | `.venv/Scripts/python.exe -m scripts.probe_capture_2_cameras --duration 20` | cam_1: 2 frames/2 starts; cam_2: 2 frames/2 starts; no errors | ✓ PASS |
| Entrypoint remains healthy | PowerShell job running `python -m src.main` for 8 seconds | both capture threads started; state `Running` after 8 seconds | ✓ PASS |
| Secret containment | `git check-ignore`, tracked-file check, exact credential-value scan | `.env` ignored and untracked; no current credential leak | ✓ PASS |

## Probe Execution

| Probe | Command | Result | Status |
|-------|---------|--------|--------|
| Auth probe | `.venv/Scripts/python.exe scripts/probe_privratnik_auth.py` | Login 200, PHPSESSID set, redacted per-camera token, 1 ffmpeg frame, Overall PASS | PASS |
| Two-camera diagnostic | `.venv/Scripts/python.exe -m scripts.probe_capture_2_cameras --duration 20` | 2 frames and 2 starts per camera; no errors | PASS |
| Two-camera direct-file invocation | `.venv/Scripts/python.exe scripts/probe_capture_2_cameras.py --duration 20` | `ModuleNotFoundError: No module named 'src'` because the script directory becomes `sys.path[0]` | INFO — module invocation above is the working form; production entrypoint is unaffected |
| Entrypoint smoke | `python -m src.main` in an 8-second PowerShell job | both capture threads started; process state remained `Running` | PASS |

## Test Quality Audit

| Test scope | Active | Skipped | Circular | Assertion level | Verdict |
|------------|--------|---------|----------|-----------------|---------|
| `tests/` | 49 | 0 | 0 detected | Value + behavioral | PASS |

- Disabled requirement tests: 0.
- Circular expected-value generators: 0 detected.
- Mocked request/ffmpeg tests are backed by independent live probes against the real service.
- The reconnect/re-auth invariant has both named state-transition tests and live preview-restart evidence.

## Requirements Coverage

| Requirement | Source plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| STREAM-01 | 01-02 | Capture video from two cameras through the proxy | ✓ SATISFIED | Live diagnostic produced real frames for both cameras; main started both channels. |
| STREAM-02 | 01-02 | PHPSESSID + token + Referer authorization | ✓ SATISFIED | Live auth probe reports HTTP 200, PHPSESSID, per-camera token, authenticated ffmpeg frame, Overall PASS. |
| STREAM-03 | 01-02 | Automatic reconnect and token refresh on session expiry | ✓ SATISFIED | Supervisor state transitions are tested; real preview completion produced a second ffmpeg start and resumed frames for each camera. |
| STREAM-04 | 01-01 | Independent camera channels with camera_id tagging | ✓ SATISFIED | Separate cam_1/cam_2 diagnostic; tagging/isolation/feed tests pass. |
| STREAM-05 | 01-01 | Extract frames for downstream analysis | ✓ SATISFIED | ffmpeg/JPEG decode produced real frames and data flows into FrameBuffer. |

No orphaned Phase 1 requirements: all five STREAM IDs are claimed by the two plans and covered above.

## Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `scripts/probe_capture_2_cameras.py` | 5-8 | Direct script-path invocation cannot import the repository-root `src` package | ℹ️ Info | Use `python -m scripts.probe_capture_2_cameras`; package/module entrypoints and production `src.main` are unaffected. |
| `src/capture/supervisor.py` | 94 | Internal `frame_q.put(frame)` is unconditional on a size-1 queue | ℹ️ Info | Potential reader-thread stall only if the supervisor generator itself stops consuming; the phase buffer remains bounded/non-blocking, and no live or test evidence shows capture loss. |
| `src/capture/auth.py`, `src/capture/stream_client.py` | module/function docstrings | Comments still label the now live-validated auth/framing contracts `[ASSUMED]` | ℹ️ Info | Documentation drift only; live evidence and active tests establish the contracts. |

Resolved anti-patterns from the prior review:
- Probe over-redaction is fixed: response structure is preserved while token/password/PHPSESSID values are masked (`tests/test_probe.py`).
- Probe ffmpeg timeout/hang risk is fixed with `-t` and `communicate(timeout=...)`; active tests cover timeout termination.
- No unreferenced `TBD`, `FIXME`, or `XXX` debt markers exist in phase source/scripts/tests.
- No current login/password credential value appears in a tracked non-planning file.

## Decision Coverage

All **10/10** trackable `01-CONTEXT.md` decisions are honored by the shipped source, tests, or live probes. This gate is non-blocking and reported for traceability.

## Advisory (New Scope, Unevidenced)

None. The direct probe invocation issue has deterministic evidence but is informational and does not affect a phase must-have or production entrypoint.

## Human Verification

None required. `01-UAT.md` is complete with 6/6 passing checks, and this re-verification independently reproduced the live auth, two-camera capture, and entrypoint smoke evidence.

## Gaps Summary

No remaining gaps. The prior live-probe blocker and both behavior-unverified items are closed; no new blocker, requirement gap, broken key link, stub, or test-quality blocker was found.

---

_Verified: 2026-09-23T20:52:35Z_
_Verifier: space-bunny-free (gsd-verifier)_
