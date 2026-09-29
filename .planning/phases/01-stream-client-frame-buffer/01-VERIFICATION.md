---
phase: 01-stream-client-frame-buffer
verified: 2026-09-29T00:00:00Z
status: passed
score: 7/7 must-haves verified # 4 VERIFIED + 2 behavior-unverified-carried + 1 PASSED (override)
covered_files:
  - .planning/phases/01-stream-client-frame-buffer/01-01-PLAN.md
  - .planning/phases/01-stream-client-frame-buffer/01-02-PLAN.md
  - .planning/phases/01-stream-client-frame-buffer/01-01-SUMMARY.md
  - .planning/phases/01-stream-client-frame-buffer/01-02-SUMMARY.md
  - .planning/phases/01-stream-client-frame-buffer/01-REVIEW.md
  - .planning/phases/01-stream-client-frame-buffer/01-REVIEW-FIX.md
  - src/config.py
  - src/capture/frame_buffer.py
  - src/capture/auth.py
  - src/capture/stream_client.py
  - src/capture/supervisor.py
  - src/main.py
  - scripts/probe_privratnik_auth.py
  - config.json
  - .gitignore
  - .env.example
  - requirements.txt
  - pytest.ini
covered_digest: "v1:sha256:dfe5f92c2c68d7c05105a4410135a9901b556ef18a096e9322fe094f1770e5d4"
behavior_unverified: 2 # truths present + wired but behavior not exercised against real stream
overrides_applied: 1
overrides:
  - must_have: "The auth flow is validated against the real stream via a manual probe script before the full pipeline is trusted"
    reason: "Accepted so Phase 02 (which is explicitly designed to need no live stream, no credentials and no ffmpeg binary) can proceed. The deviation was NOT descoped — it is externally blocked: the WinGet-installed ffmpeg 9.0.1 is unlaunchable on this host (CreateProcess -> WinError 1260 ERROR_ACCESS_DISABLED_BY_POLICY, reproduced via cmd, PowerShell Start-Process and Python subprocess), so no process on this machine may start it, probe included. No real PRIVRATNIK_LOGIN/PRIVRATNIK_PASSWORD exist yet. The code work itself is complete and green (37/37 tests, all 5 review findings fixed); only the live probe is outstanding. The override covers ROUTING only — the two behavior_unverified items and all three human_verification items remain in force and MUST be discharged before any phase consumes the real capture path."
    accepted_by: "Pavel Vanyushkin"
    accepted_at: "2026-09-29T00:00:00Z"
re_verification: false
gaps:
  - truth: "The auth flow is validated against the real stream via a manual probe script before the full pipeline is trusted"
    status: override
    reason: "scripts/probe_privratnik_auth.py exists and compiles (py_compile passes) but has NEVER RUN. No PRIVRATNIK_LOGIN/PRIVRATNIK_PASSWORD credentials exist and no .env is present, so the A1 (login/token) and A3 (MJPG pipe-framing) contracts remain [ASSUMED]. Re-confirmed 2026-09-29: ffmpeg 9.0.1 IS installed via WinGet at ...\\Gyan.FFmpeg_...\\ffmpeg-9.0.1-full_build\\bin\\ffmpeg.exe and IS on PATH, but CreateProcess on it fails with WinError 1260 ERROR_ACCESS_DISABLED_BY_POLICY, reproduced through cmd, PowerShell Start-Process, and Python subprocess.run — i.e. a host software-restriction policy, not a missing install. The probe therefore cannot run on this machine by any route. Override accepted to unblock Phase 02 routing only."
    artifacts:
      - path: "scripts/probe_privratnik_auth.py"
        issue: "Probe built and compiles (py_compile passes) but was never executed against the real privratnik.net stream"
    missing:
      - "User-supplied PRIVRATNIK_LOGIN/PRIVRATNIK_PASSWORD in a gitignored .env"
      - "Host policy permitting execution of the WinGet ffmpeg binary (WinError 1260 on CreateProcess), or an alternative ffmpeg the policy allows"
      - "Execution of scripts/probe_privratnik_auth.py and confirmation of a PASS summary before any phase consumes the capture path"
behavior_unverified_items:
  - truth: "The system authenticates to privratnik.net via requests.Session, persisting the PHPSESSID cookie and obtaining a token"
    test: "Run scripts/probe_privratnik_auth.py against the real stream with a valid .env; observe Step 1 login status, Step 2 token extraction, Step 4 ffmpeg frame production"
    expected: "Login returns <400; a PHPSESSID cookie is set; a non-empty stream token is extracted; ffmpeg produces >=1 frame in the probe window; Overall: PASS"
    why_human: "login()/get_session() are exercised only against mocked requests in tests. The real A1 login/token contract is [ASSUMED]; presence checks cannot see whether the actual privratnik.net server accepts the POST credentials or returns a parseable token. Only a live credential+network run proves it."
  - truth: "System connects to both cameras and delivers a continuous frame stream without manual intervention (SC1)"
    test: "Install ffmpeg on the host, populate .env with real creds, run python -m src.main, and confirm both capture threads produce frames into their per-camera FrameBuffers over an extended window"
    expected: "Both capture-cam_1 and capture-cam_2 threads log started; FrameBuffer.pop() returns camera-tagged frames for cam_1 and cam_2; no silent death and frames keep arriving"
    why_human: "spawn_ffmpeg/read_jpeg_frame run only against mocked subprocess.Popen and synthetic JPEG bytes. The real MJPG-over-pipe framing (A3) and OpenCV decode of an actual stream are never exercised here because ffmpeg is not installed and no stream is reachable. Presence checks cannot prove real decode works."
human_verification:
  - test: "Run scripts/probe_privratnik_auth.py with real credentials and ffmpeg; confirm Overall: PASS (login, PHPSESSID, token, and frame production all PASS)"
    expected: "Auth flow against the real privratnik.net stream is validated end-to-end before Phase 2 trusts the capture path"
    why_human: "Requires user credentials, network access, and ffmpeg on the host — none available in this environment"
  - test: "Run python -m src.main with real creds + ffmpeg; confirm both cameras deliver continuous camera-tagged frames and that the supervisor reconnects/re-auths after a manual stream/token expiry"
    expected: "Both cameras produce tagged frames; on expiry the supervisor detects staleness, re-auths, and resumes within the backoff window"
    why_human: "Real decode + real session-expiry behavior requires live streams, credentials, and ffmpeg; tests mock subprocess and requests"
---

# Phase 1: Stream Client + Frame Buffer Verification Report

**Phase Goal:** Two cameras capture continuous frames through the privratnik.net proxy with working auth, an auto-reconnect/re-auth loop, a bounded drop-oldest queue that keeps capture non-blocking, and a config/secrets structure feeding the first token.

**Verified:** 2026-09-16
**Status:** passed (1 override applied — see `overrides:` in frontmatter)
**Re-verification:** No — initial verification
**Mode:** mvp. The ROADMAP phase goal is not expressed in the canonical User Story format (`As a …, I want to …, so that …`), so the MVP user-flow coverage table is not applicable; verification uses the standard goal-backward observable-truths method, which the non-formatted capability goal supports directly.

## Summary

The codebase implements the full intended architecture: a config/secrets split (`load_settings`), a bounded drop-oldest per-camera frame buffer, a privratnik auth session manager, an ffmpeg subprocess stream client, a reconnect/re-auth supervisor, and `main.py` wiring 2 cameras. All **5 review findings (1 critical CR-01 + 4 warnings WR-01..04) were confirmed genuinely fixed in the source**, and all **37 tests pass** in my own run.

However, the phase's **own declared must-have** — that the privratnik auth flow be **validated against the real stream via the manual probe before the pipeline is trusted** — is **NOT satisfied**. The probe script was never run (no credentials, no `.env`, and ffmpeg is not installed on this host). Two `[ASSUMED]` contracts (A1 login/token, A3 MJPG pipe-framing) and the entire real ffmpeg decode path therefore remain behaviorally unverified. The honest verdict is that the capture path is **wired and logically sound but not proven against a real stream**, which directly blocks the phase goal's "two cameras capture continuous frames … with working auth" clause.

## Goal Achievement

### Observable Truths

| #   | Truth   | Status     | Evidence       |
| --- | ------- | ---------- | -------------- |
| 1   | Frames carrying `camera_id` are pushed into a per-camera bounded drop-oldest buffer, non-blocking, camera-distinguishable | ✓ VERIFIED | `frame_buffer.py` `Frame(camera_id=…)` at enqueue; `if q.full(): get_nowait()`. Tests pass: `test_frame_pops_with_camera_id`, `test_drop_oldest_keeps_buffer_bounded`, `test_push_never_blocks_when_full`, `test_drop_oldest_under_race_never_raises_empty`, `test_buffers_with_different_camera_ids_are_isolated`, `test_feed_frames_pushes_frames_into_buffer_tagged_with_camera_id`. Behavior exercised by named tests |
| 2   | Non-secret settings load from `config.json`; secrets load from `.env` via `os.getenv()`, never source/config; `.env` gitignored | ✓ VERIFIED | `config.py` uses `os.getenv("PRIVRATNIK_LOGIN"/"PASSWORD")` (grep count 3). `git check-ignore .env` → `.env`; `grep -c password config.json` → 0. Tests: `test_login_password_come_from_env`, `test_config_json_has_no_password_key` |
| 3   | ffmpeg argv builds with Referer/Range/PHPSESSID headers + request-time token; single `-headers` emitter; token never embedded in stored URL | ✓ VERIFIED | `build_ffmpeg_cmd` is the sole `-headers` emitter passing pure header string; `stream_headers_and_url` returns a `str`, not a list with a leading `-headers` (CR-01 fix real, source-verified). Tests: `test_build_ffmpeg_cmd_header_value_has_no_leading_headers_token`, `test_build_ffmpeg_cmd_shape`, `test_stream_headers_and_url_appends_token_and_headers`, `test_token_not_embedded_in_stored_camera_url` |
| 4   | Supervisor detects "no valid frame for N sec", tears down ffmpeg, re-auths, reconnects with exponential backoff; not killed by transient transport/auth errors; interruptible wait | ✓ VERIFIED | `supervisor.py` stale-detect via reader thread + `clock()`; `except (StreamStaleError, AuthExpiredError, requests.RequestException, OSError)` (WR-01 fix real); `stop_event.wait(backoff)` (no `time.sleep`, grep count 0); backoff 1→2→…→max, resets on healthy frame; `_kill_proc` + stderr drain teardown (WR-02). Tests: `test_supervisor_reauths_and_reconnects_on_stale_stream`, `test_backoff_doubles_1_2_4`, `test_backoff_resets_on_healthy_frame`, `test_backoff_caps_at_max`, `test_transport_error_does_not_kill_capture_loop`, `test_uses_stop_event_wait_not_time_sleep`, `test_ffmpeg_process_killed_on_reconnect`, `test_stderr_drain_torn_down_when_process_killed`. State transitions exercised via injected fake clock/read_frame |
| 5   | System authenticates to privratnik.net via requests.Session, persisting PHPSESSID cookie and obtaining a token | ⚠️ PRESENT_BEHAVIOR_UNVERIFIED | `auth.py` login() does a real `requests.Session().post` + best-effort `_extract_token` + raises `AuthExpiredError` on absent token (WR-04 fix real); shape-only logging on failure (WR-03 fix real). Tests mock `requests` entirely — the real A1 login/token contract is `[ASSUMED]` and never exercised. Mocked test passes: `test_login_sets_phpsessid_cookie_and_returns_token`. See Human Verification item 1 |
| 6   | Both cameras deliver a continuous frame stream without manual intervention (SC1) | ⚠️ PRESENT_BEHAVIOR_UNVERIFIED | `main.py` wires 2 threads + `feed_frames` + ffmpeg pre-flight; `spawn_ffmpeg`/`read_jpeg_frame` implement the A3 MJPG byte-buffer framing. But ffmpeg is **not on PATH** on this host and decode runs only against mocked `subprocess.Popen` + synthetic JPEGs. Real decode of an actual stream is never proven. See Human Verification item 2 |
| 7   | The auth flow is validated against the real stream via a manual probe script before the full pipeline is trusted | ⚡ PASSED (override) | Override accepted 2026-09-29 by Pavel Vanyushkin — live probe is externally blocked (WinError 1260 on the ffmpeg binary; no credentials), not descoped. Probe exists and compiles; never run. See `overrides:` in frontmatter. **Routing override only — see Human Verification below.** |

**Score:** 4/7 truths verified (2 present, behavior-unverified; 1 failed)

### Required Artifacts

| Artifact | Expected    | Status | Details |
| -------- | ----------- | ------ | ------- |
| `src/config.py` | `load_settings()` merging config.json + .env | ✓ VERIFIED | Exists, substantive, wired (imported by main.py), secrets from env only |
| `src/capture/frame_buffer.py` | `Frame` + `FrameBuffer` bounded drop-oldest | ✓ VERIFIED | Exists, substantive, wired via main.py feed_frames; data flows |
| `src/capture/auth.py` | `SessionManager` (login/get_session/stream_headers_and_url) | ✓ VERIFIED (behavior vs real stream unverified) | Exists, substantive, wired (supervisor/main); real A1 contract unexercised |
| `src/capture/stream_client.py` | build_ffmpeg_cmd/spawn_ffmpeg/read_jpeg_frame | ✓ VERIFIED (behavior vs real stream unverified) | Exists, substantive, wired; real decode unexercised (no ffmpeg) |
| `src/capture/supervisor.py` | run_capture_with_supervisor + named errors | ✓ VERIFIED | Exists, substantive, wired; transition exercised by tests |
| `src/main.py` | Entrypoint wiring 2 cameras + pre-flight | ✓ VERIFIED | Exists, substantive; pre-flight exits non-zero on missing ffmpeg (correct) |
| `scripts/probe_privratnik_auth.py` | Standalone live-stream probe (A1/A2) | ⚠️ COMPILES, NOT EXECUTED | Compiles clean; never run (no creds/ffmpeg) — this blocks truth #7 |
| `config.json` | Non-secret camera URLs/queue/backoff/ffmpeg | ✓ VERIFIED | No secrets (grep password = 0); cameras cam_1 + cam_2 |
| `.gitignore` | Excludes .env, .venv/, data/, __pycache__/ | ✓ VERIFIED | `.env` listed; `git check-ignore .env` returns `.env` |
| `.env.example` | Documented empty placeholders | ✓ VERIFIED | `PRIVRATNIK_LOGIN=` / `PRIVRATNIK_PASSWORD=` empty |
| `requirements.txt` / `pytest.ini` | Pinned == versions; testpaths+pythonpath | ✓ VERIFIED | opencv-python-headless==4.14.0.94 etc.; `pythonpath = .` |

### Key Link Verification

| From | To  | Via | Status | Details |
| ---- | --- | --- | ------ | ------- |
| auth `stream_headers_and_url` | stream_client `build_ffmpeg_cmd` | pure header `str` → `-headers` value | ✓ WIRED | CR-01: single `-headers` emitter; header content is a string, no leading `-headers`; token appended at request time (`grep token=` = 6 legitimate uses, none a stored URL; D-07 asserted by test) |
| supervisor → auth | `stream_headers_and_url` (re-invokes get_session) on each reconnect | ✓ WIRED | supervisor calls it per iteration (line 49); re-auth forced by get_session + except-path re-auth (WR-01) |
| supervisor → frame_buffer | main.py `feed_frames` pushes into camera's FrameBuffer | ✓ WIRED | `feed_frames` iterates supervisor and `frame_buffer.push(frame)`; tagged camera_id at enqueue; test asserts N frames land tagged |
| main.py → ffmpeg | pre-flight `shutil.which(ffmpeg_path)` | ✓ WIRED | Exits non-zero with install hint if ffmpeg missing (correctly triggered on this host) |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
| -------- | ------------- | ------ | ------------------ | ------ |
| FrameBuffer | frame.data | supervisor-yielded frame → feed_frames | Yes (from read_jpeg_frame/cv2.imdecode) — real decode unproven | ✓ FLOWING (unit-level) / ⚠️ real decode unexercised |
| frame.camera_id | FrameBuffer.camera_id | set at construction | Yes | ✓ FLOWING |
| settings["cameras"] | cam_1/cam_2 | config.json | Yes | ✓ FLOWING |
| login/password | Settings["login"/"password"] | os.getenv(.env) | Yes (env) — .env absent in this env | ✓ FLOWING (wired) / ⚠️ no creds present |

No value terminates in a hardcoded literal or mock when the app runs for real; the only gap is that the real decode/auth path is not executable in this environment (no ffmpeg, no creds).

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
| -------- | ------- | ------ | ------ |
| Full suite green | `.venv/Scripts/python -m pytest tests/ -q` | 37 passed in 0.70s | ✓ PASS |
| CR-01 header contract regression guard | pytest `test_build_ffmpeg_cmd_header_value_has_no_leading_headers_token` + `..._shape` | 2 passed | ✓ PASS |
| D-07 token-not-embedded + WR-04 raises-on-none | pytest `test_token_not_embedded_in_stored_camera_url`, `test_stream_headers_and_url_raises_when_token_none`, `test_stream_headers_and_url_appends_token_and_headers` | 3 passed | ✓ PASS |
| WR-01 transport resilience + feed path + interruptible wait | pytest `test_transport_error_does_not_kill_capture_loop`, `test_feed_frames_pushes_…`, `test_uses_stop_event_wait_not_time_sleep` | 3 passed | ✓ PASS |
| Probe script syntax | `py_compile scripts/probe_privratnik_auth.py` | compiles OK | ✓ PASS (compile only) |
| Real ffmpeg on PATH | `which ffmpeg` | not found | ✗ SKIP (no ffmpeg) |
| Real auth probe run | `.venv/Scripts/python scripts/probe_privratnik_auth.py` | not run (no creds) | ✗ SKIP → Human Verification |

### Probe Execution

| Probe | Command | Result | Status |
| ----- | ------- | ------ | ------ |
| `scripts/probe_privratnik_auth.py` | `py_compile` | exit 0, no SyntaxError | PASS (compile) |
| `scripts/probe_privratnik_auth.py` | live run with real creds | **NOT RUN** | MISSING — no `.env`, no ffmpeg; this is the phase's blocked must-have (truth #7) |

No conventional `scripts/*/tests/probe-*.sh` probes exist for this phase; the only probe is the auth probe above.

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
| ----------- | ---------- | ----------- | ------ | -------- |
| STREAM-01 | 01-02 | Capture from 2 cameras via proxy | ⚠️ NEEDS HUMAN | Code + wiring present (main.py, spawn_ffmpeg); real decode unvalidated (no ffmpeg) |
| STREAM-02 | 01-02 | Auth (PHPSESSID + token + Referer) | ⚠️ NEEDS HUMAN | SessionManager built + mock-tested; A1 contract [ASSUMED], probe not run |
| STREAM-03 | 01-02 | Auto-reconnect + token refresh on expiry | ⚠️ NEEDS HUMAN | Supervisor reconnect/backoff test-verified; real expiry unvalidated |
| STREAM-04 | 01-01 | Independent per-camera channel, camera_id-tagged | ✓ SATISFIED | Frame camera_id tagging + isolation tested |
| STREAM-05 | 01-01 | Extract frames for later analysis | ⚠️ NEEDS HUMAN | FrameBuffer extraction present; real decode unexercised (A3 [ASSUMED]) |

No orphaned requirements: all 5 STREAM IDs are claimed across the two plans and mapped to Phase 1 in REQUIREMENTS.md. Every ID accounted for.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
| ---- | ---- | ------- | -------- | ------ |
| scripts/probe_privratnik_auth.py | 29-33, 144 | `_redact()` returns `"***"` for any non-empty value (IN-01a, not fixed) | ℹ️ Info | Probe prints only `***` for the response body — defeats its purpose of revealing the response shape it exists to discover |
| scripts/probe_privratnik_auth.py | 113-122 | `proc.stdout.read(65536)` blocks with no read timeout (IN-01b, not fixed) | ℹ️ Info | On a silent stream the probe hangs instead of timing out at `seconds` |
| src/capture/supervisor.py | 94 | reader thread `frame_q.put(frame)` unconditional (IN-03, not fixed) | ℹ️ Info | Queue maxsize=1; a slow downstream consumer could block the reader in put, ignoring reader_stop; currently masked by low frame rate |

No debt markers (`TBD`/`FIXME`/`XXX`), no `placeholder`/`coming soon`/`not yet implemented`, and no hardcoded-empty stub patterns found in any phase source file. The 3 Info findings above are carried from the review and are non-blocking (they do not affect the 5 in-scope findings that were all fixed).

### Review-Fix Verification (commit `e3a1a55`, confirmed in git history)

All 5 in-scope findings were verified fixed directly in the source, not from the fix report:

- **CR-01 (critical, `-headers` double-injection):** `stream_headers_and_url` now returns a pure header `str` (`auth.py` L159-166); `build_ffmpeg_cmd` is the single emitter of `-headers` (`stream_client.py` L38-46). Regression guards pass.
- **WR-01 (uncaught transport error kills thread):** `supervisor.py` L55 adds `requests.RequestException, OSError` to the caught set; `test_transport_error_does_not_kill_capture_loop` passes.
- **WR-02 (stderr never drained):** `spawn_ffmpeg` attaches `_StderrDrain`; `_kill_proc` tears it down; drain tests pass.
- **WR-03 (login body logged unredacted):** `auth.py` logs only `status/body_len/bodies_<count>`; redaction test passes.
- **WR-04 (`?token=None`):** `auth.py` raises `AuthExpiredError` when token is `None`; raises-on-none tests pass.

## Human Verification Required

The following require the real environment (user credentials, network access to privratnik.net, and ffmpeg installed on the host) and cannot be resolved by any automated check in this workspace:

### 1. Real-stream auth validation (blocks truth #7 / STREAM-02)
**Test:** Install ffmpeg on the host, create a gitignored `.env` with `PRIVRATNIK_LOGIN` / `PRIVRATNIK_PASSWORD` from the user's privratnik.net account, then run `python scripts/probe_privratnik_auth.py`.
**Expected:** Step 1 login status <400; a PHPSESSID cookie is set; Step 2 extracts a non-empty stream token; Step 4 (if ffmpeg present) produces ≥1 frame; final `Overall: PASS`, exit 0.
**Why human:** Requires real credentials + live network. Until this PASS is observed, the A1 auth contract remains `[ASSUMED]` and truth #7 stays failed.

### 2. Two-camera continuous capture (SC1 / STREAM-01, STREAM-05)
**Test:** With creds + ffmpeg installed, run `python -m src.main` for an extended window; confirm both `capture-cam_1` and `capture-cam_2` threads are started and that `FrameBuffer.pop()` returns camera-tagged frames for both cameras continuously (no silent death).
**Expected:** Both cameras yield frames tagged with `cam_1`/`cam_2`; frames keep arriving; no permanent thread death.
**Why human:** The MJPG decode path (A3) runs only against mocks here; actual OpenCV decode of a real stream requires ffmpeg on the host and a reachable camera stream.

### 3. Reconnect/re-auth against real session expiry (STREAM-03)
**Test:** With capture running, force the stream/token to expire (or simulate session loss) and observe the supervisor detect staleness, tear down ffmpeg, re-auth, and resume within the backoff window.
**Expected:** The supervisor logs the stale/reconnect transitions and resumes producing frames automatically within a few seconds.
**Why human:** Real session-expiry semantics (A2) can only be observed against a live stream; mocked tests prove the mechanics, not the real contract.

## Gaps Summary

The phase's architecture is complete, logically sound, and fully mocked-tested. Its one outstanding verification gate is an **accepted override**, not a defect:

1. **Auth flow not validated against the real stream — OVERRIDE ACCEPTED 2026-09-29.** The plan's `must_haves.truths` explicitly requires: *"The auth flow is validated against the real stream via a manual probe script before the full pipeline is trusted."* `scripts/probe_privratnik_auth.py` has never run: no `.env`, no credentials, and — re-confirmed this session — the WinGet ffmpeg binary is installed and on PATH but unlaunchable (CreateProcess → WinError 1260 `ERROR_ACCESS_DISABLED_BY_POLICY`, reproduced via cmd, PowerShell and Python). No process on this host can start ffmpeg, so the probe is unreachable by any route available to the agent. Pavel Vanyushkin accepted the override to unblock Phase 02, which is explicitly planned to require no live stream, no credentials and no ffmpeg binary. The A1 (login/token) and A3 (MJPG pipe-framing) contracts remain `[ASSUMED]`.

**This override is scoped to ROUTING.** It changes the phase's advancement status; it does **not** certify the capture path. All three human_verification items below remain outstanding and MUST be discharged before any later phase — Phase 3's event store or anything downstream — consumes the real capture path.

This is a **human/user-gated** gap — it cannot be closed by code in this environment. The correct path to close it: install ffmpeg, populate a gitignored `.env` with real credentials, run the probe, and confirm PASS. Only then may Phase 2 build trust on this capture path.

---

_Verified: 2026-09-16_
_Verifier: Claude (gsd-verifier)_
