# Phase 1: Stream Client + Frame Buffer - Pattern Map

**Mapped:** 2026-09-09
**Files analyzed:** 19 (new) / 0 (modified) — all greenfield
**Analogs found:** 0 / 19 in application code; 19/19 mapped to canonical tracked reference patterns in `01-RESEARCH.md`

> **Greenfield note:** This is the project's FIRST phase. The only git-tracked file outside `.planning/` is `.claude/CLAUDE.md` (project instructions). There is **no existing application code** — no `src/`, `tests/`, `scripts/`, or `config.json` on disk (verified this session via `git ls-files` + Glob for `**/*.py`, which returned nothing).
>
> **Consequence for this pattern map:** There are NO existing code analogs to copy from. Every file below is mapped to its **canonical reference pattern** in the git-tracked `C:/dev/schlagbaum/.planning/phases/01-stream-client-frame-buffer/01-RESEARCH.md` (tracked source, lines 188-433), which the researcher wrote explicitly as the pattern to follow. The planner must treat RESEARCH.md's code examples as the sole pattern source and note that the exact `privratnik.net` auth contract (A1) and ffmpeg pipe-framing (A3) are `[ASSUMED]` and require empirical validation — flagged as the phase's highest-risk checkpoint.

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `src/capture/auth.py` | service | request-response | no app analog → RESEARCH.md §Code:Auth (L326-367) | reference |
| `src/capture/stream_client.py` | service | streaming | no app analog → RESEARCH.md §Code:ffmpeg (L370-403) | reference |
| `src/capture/frame_buffer.py` | service+buffer | producer-consumer / event-driven | no app analog → RESEARCH.md §Code:FrameBuffer (L406-433) | reference |
| `src/capture/supervisor.py` | service | streaming / event-driven | no app analog → RESEARCH.md §Pattern 2 (L205-226) | reference |
| `src/config.py` | config | file-I/O | no app analog → RESEARCH.md §Code:config (L290-323) | reference |
| `src/main.py` | controller/entrypoint | orchestration | no app analog → RESEARCH.md §Pattern 1 (L188-203) + structure (L168-186) | reference |
| `config.json` | config | file | no app analog → RESEARCH.md §Code:config (L292-302) | reference |
| `.env` (gitignored) | config/secrets | file | no app analog → RESEARCH.md §Code:config (L304-306) | reference |
| `.env.example` | config/secrets | file | no app analog → RESEARCH.md §Pitfall 4 (L268-272) | reference |
| `.gitignore` | config | file | no app analog → RESEARCH.md §Pitfall 4 (L268-272) | reference |
| `data/` (runtime) | storage | file-I/O | no app analog → RESEARCH.md §structure (L183) | reference |
| `requirements.txt` | config | file | no app analog → RESEARCH.md §Installation (L88-98) | reference |
| `pytest.ini` (or `pyproject.toml` `[tool.pytest.ini_options]`) | config | file | no app analog → RESEARCH.md §Validation Test Framework (L513-519) | reference |
| `tests/test_frame_buffer.py` | test | n/a | no app analog → RESEARCH.md §Validation Req→Test Map STREAM-04/05 (L524-528) | reference |
| `tests/test_auth.py` | test | n/a | no app analog → RESEARCH.md §Validation Req→Test Map STREAM-02 (L525) | reference |
| `tests/test_stream_client.py` | test | n/a | no app analog → RESEARCH.md §Validation Req→Test Map STREAM-01 (L524) | reference |
| `tests/test_supervisor.py` | test | n/a | no app analog → RESEARCH.md §Validation Req→Test Map STREAM-03 (L526) | reference |
| `tests/test_config.py` | test | n/a | no app analog → RESEARCH.md §Validation Req→Test Map D-02/06/07 (L530) | reference |
| `tests/conftest.py` | test fixture | n/a | no app analog → 01-VALIDATION.md §Wave 0 (L51-53) | reference |
| `scripts/probe_privratnik_auth.py` | utility | request-response | no app analog → RESEARCH.md §Open Question 1 (L466-470) + §Manual probe (L546, L529) | reference |

## Pattern Assignments

### `src/capture/auth.py` (service, request-response)

**Analog:** none in codebase → **canonical reference:** RESEARCH.md `Code Examples: Auth session manager` (L326-367)

**Imports pattern** (L327-331):
```python
import requests

AUTH_URL = "https://privratnik.net/login"   # [ASSUMED] exact endpoint — see Open Question
REFERER = "https://privratnik.net/files/video-control.php"
```

**Core pattern — re-invocable `get_session()` (D-01, Pitfall 1)** (L333-366):
```python
class SessionManager:
    def __init__(self, settings):
        self.settings = settings
        self._session = None
        self._token = None

    def login(self):
        """Log in to privratnik.net, store PHPSESSID cookie (via requests.Session) + token."""
        s = requests.Session()                          # persists PHPSESSID cookie (Pitfall 1)
        r = s.post(AUTH_URL, data={
            "login": self.settings["login"],
            "password": self.settings["password"],
        }, timeout=30)
        r.raise_for_status()
        token = extract_token(r)                        # [ASSUMED] — parse response HTML/JSON/redirect
        self._session = s
        self._token = token
        return s, token

    def get_session(self):
        """Re-invocable on reconnect (Pitfall 1). Returns a fresh authed session."""
        self._session, self._token = self.login()      # always fresh — don't reuse on reconnect
        return self._session, self._token

    def stream_headers_and_url(self, camera_id, cam_url):
        """Assemble the ffmpeg header args + full stream URL with live token (D-07)."""
        _, token = self.get_session()
        url = f"{cam_url}?token={token}"               # token appended at request time, not stored
        headers = [
            "-headers", f"Referer: {REFERER}\r\n" +
                        f"Range: bytes=0-\r\n" +
                        f"Cookie: PHPSESSID={self._session.cookies.get('PHPSESSID', '')}\r\n",
        ]
        return url, headers
```

**Error handling / security pattern** (RESEARCH.md L234, L563-566):
- Redact `token=` query param and PHPSESSID in ALL log output before writing (V7).
- `raise_for_status()` on login (L346) — surface auth failure, never treat as "no event".
- `get_session()` must always re-login on reconnect — never reuse a cached session (Pitfall 1).

**Validation note:** A1 (exact login/token contract) is UNKNOWN. Do NOT hardcode AUTH_URL/extraction — build the probe script first (see `scripts/probe_privratnik_auth.py`).

---

### `src/capture/stream_client.py` (service, streaming)

**Analog:** none in codebase → **canonical reference:** RESEARCH.md `Code Examples: ffmpeg subprocess capture` (L370-403)

**Imports pattern** (L372):
```python
import subprocess, cv2, numpy as np
```

**Core pattern — build cmd + spawn + read frame (D-04/D-05/D-08)** (L374-402):
```python
def build_ffmpeg_cmd(cam_url, headers, fps_output=1.5):
    # JPEG frames to stdout pipe; throttle by framerate filter per camera
    return [
        "ffmpeg",
        "-headers", "\r\n".join(headers) + "\r\n",
        "-i", cam_url,
        "-vf", f"fps={fps_output}",          # 1-2 fps limit (D-08) — reduces wasteful decode
        "-f", "image2pipe",                  # D-05: JPEG via pipe
        "-vcodec", "mjpeg",
        "-"
    ]

def spawn_ffmpeg(cmd):
    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,              # read/drain, else pipe fills and blocks (Pitfall 6)
        creationflags=subprocess.CREATE_NO_WINDOW,   # Windows: no console popup
    )
```

**Error handling — Windows subprocess + pipe (Pitfall 6, L280-284):**
- Spawn with `stdout=PIPE, stderr=PIPE`; read frames from `proc.stdout`; drain `stderr` (or redirect to log) so the pipe doesn't fill and block capture.
- `creationflags=subprocess.CREATE_NO_WINDOW` on Windows (background service, no console popup).
- On reconnect: `proc.kill()` + `proc.wait()` in a `finally` — never leave a zombie ffmpeg child (L283, L566).

**Validation note:** A3 (MJPG pipe frame-delimiting: `0xFFD8..0xFFD9` SOI/EOI framing) is UNKNOWN — implement and verify empirically. `read_jpeg_frame` is `NotImplementedError` in the reference (L397) and MUST be completed + tested.

---

### `src/capture/frame_buffer.py` (service+buffer, producer-consumer / event-driven)

**Analog:** none in codebase → **canonical reference:** RESEARCH.md `Code Examples: Bounded drop-oldest frame buffer` (L406-433) + `Pattern 1` (L188-203)

**Imports pattern** (L408):
```python
import queue
```

**Core pattern — tagged Frame + bounded drop-oldest buffer (D-09/D-10)** (L410-433):
```python
# A Frame carries its camera tag (D-10).
class Frame:
    def __init__(self, camera_id, ts, data):
        self.camera_id = camera_id
        self.ts = ts
        self.data = data                  # numpy BGR array from cv2.imdecode

class FrameBuffer:
    def __init__(self, camera_id, maxsize=15):     # maxsize = Claude's discretion (10-20)
        self.camera_id = camera_id
        self.q = queue.Queue(maxsize=maxsize)

    def push(self, data):
        """Non-blocking drop-oldest put (D-09). Tag with camera_id at enqueue (D-10)."""
        if self.q.full():
            try:
                self.q.get_nowait()       # drop oldest
            except queue.Empty:
                pass
        self.q.put(Frame(camera_id=self.camera_id, ts=time.time(), data=data))

    def pop(self):                         # consumer (Phase 2+) — never called by capture
        return self.q.get()
```

**Error handling — drop-oldest under race** (L197-201):
```python
if frame_q.full():
    try:
        frame_q.get_nowait()  # drop oldest
    except queue.Empty:
        pass
```
`queue.Empty` swallow on the `get_nowait` — two producers can race a full queue; the drop must never raise if another thread drained it first.

**Anti-pattern to avoid** (L231): Capture must only ever `put` to a bounded queue and never wait on a consumer — the queue is the decoupling contract with Phase 2+.

**Testing note:** This is the most directly unit-testable module — mock-driven (STREAM-04/05, `tests/test_frame_buffer.py`).

---

### `src/capture/supervisor.py` (service, streaming / event-driven)

**Analog:** none in codebase → **canonical reference:** RESEARCH.md `Pattern 2: Reconnect / Re-auth Supervisor with Exponential Backoff` (L205-226)

**Imports pattern** (L210):
```python
import time
```

**Core pattern — supervisor loop with backoff (STREAM-03 / D-03)** (L212-226):
```python
def run_capture_with_supervisor(start, stop_event, session_mgr, camera_id, cam_url):
    backoff = 1                       # starts at 1s (D-03)
    max_backoff = 60                  # discretion: cap at 60s
    while not stop_event.is_set():
        try:
            url, headers = session_mgr.stream_headers_and_url(camera_id, cam_url)  # re-auths via get_session()
            proc = start(url, headers)   # start is a closure over build_ffmpeg_cmd + spawn_ffmpeg
            yield_from_frames_or_raise(proc)   # raises StreamStale if no frame in N sec
            backoff = 1  # healthy; reset
        except (StreamStaleError, AuthExpiredError) as e:
            log(e)
            session_mgr.get_session()          # force fresh re-auth (Pitfall 1)
            kill_proc(proc)
            stop_event.wait(backoff)           # sleep, interruptible
            backoff = min(backoff * 2, max_backoff)
```

**Anti-patterns to avoid** (L229-233):
- Do NOT hold a single long-lived ffmpeg subprocess with no reconnect — supervisor always re-auths + reconnects.
- Do NOT `if no frame: pass` — "no valid frame for N seconds" (recommend 12s, config `frame_stale_seconds`) must be DETECTED and surfaced (Pitfall 1).
- Use interruptible `stop_event.wait(backoff)`, NOT `time.sleep` — shutdown must not be delayed (L244, L566).

**Testing note:** Use a fake clock / fake capture in `tests/test_supervisor.py` (STREAM-03) — no network.

---

### `src/config.py` (config, file-I/O)

**Analog:** none in codebase → **canonical reference:** RESEARCH.md `Code Examples: Config / secrets loading` (L290-323)

**Imports pattern** (L308-313):
```python
import json, os
try:
    from dotenv import load_dotenv
    load_dotenv()                      # loads .env into os.environ
except ImportError:
    pass
```

**Core pattern — merge config.json + .env via os.getenv (D-06/D-07)** (L315-322):
```python
def load_settings(config_path="config.json"):
    with open(config_path, encoding="utf-8") as f:
        raw = json.load(f)
    return {
        **raw,
        "login": os.getenv("PRIVRATNIK_LOGIN"),          # secrets from env, never config
        "password": os.getenv("PRIVRATNIK_PASSWORD"),
    }
```

**Security pattern (Pitfall 4/Security V2):** secrets come from `os.getenv()` exclusively, never from config.json or source (D-02, L268-272, L554). Login/password have no fallback defaults (security.md: `os.getenv()` exclusively, no defaults for secrets). Static check: `git check-ignore .env` + `grep -r PRIVRATNIK src config.json` (L530).

---

### `src/main.py` (controller/entrypoint, orchestration)

**Analog:** none in codebase → **canonical reference:** RESEARCH.md structure (L168-186) + Pattern 1 (L188-203) + supervisor wiring (L205-226)

**Role:** Entrypoint — build 2 `StreamClient`s + 2 `FrameBuffer`s and wire the supervisor per camera. No analog; assemble following RESEARCH.md's responsibility map (L51-63):
- Load `Settings` via `config.py`.
- Pre-flight check: verify `ffmpeg -version` (or configured `ffmpeg_path`) resolves BEFORE capture; surface a clear "ffmpeg not found — install via: winget install Gyan.FFmpeg" if missing (Pitfall 2, L256-262).
- Instantiate per-camera `StreamClient(camera_id, session_mgr, frame_buffer)` + one `FrameBuffer(camera_id, queue_size)` each (D-10).
- Start a supervisor thread per camera.
- The integration point for Phase 2 is the `FrameBuffer.pop()` (L159-160) — exposed but not consumed here.

---

### `config.json` (config, file)

**Analog:** none → **canonical reference:** RESEARCH.md §Code:config JSON shape (L292-302) + Open Question 3 (L476-479)
```jsonc
// Non-secrets only (D-06). Committed. Never put token/credentials here (D-07/Pitfall 5).
{
  "cameras": { "cam_1": "https://cam2.privratnik.net/80146f20_3105/preview.mp4",
              "cam_2": "https://cam2.privratnik.net/f9456e90_3099/preview.mp4" },
  "queue_size": 15,           // Claude's discretion: 10-20 (D-09)
  "capture_fps": 1.5,         // 1-2 fps per camera (D-08)
  "frame_stale_seconds": 12,  // no-valid-frame threshold for supervisor
  "backoff_initial": 1.0, "backoff_max": 60.0,   // 1s → 2s → … → 60s (D-03, max=discretion)
  "ffmpeg_path": "ffmpeg",    // PATH lookup; or absolute path
  "referer": "https://privratnik.net/files/video-control.php"
}
```

---

### `.env`, `.env.example`, `.gitignore` (config/secrets)

**Analog:** none → **canonical reference:** RESEARCH.md §Pitfall 4 (L268-272), §Code:config .env (L304-306), Security V2/V3 (L552-558)

**`.env` (gitignored, secrets — D-02)** (L304-306):
```
PRIVRATNIK_LOGIN=...
PRIVRATNIK_PASSWORD=...
```

**`.gitignore` (MUST be created — none exists, Pitfall 4)** (L271):
- At minimum `.env`, `.venv/`, `data/`, `__pycache__/`, `*.pyc`.
- Add a check that `.env` is ignored (`git check-ignore .env` returns it).

**`.env.example`:** committed, documented keys, NO real values (L182, L271).

---

### `requirements.txt` (config, file)

**Analog:** none → **canonical reference:** RESEARCH.md §Installation + CLAUDE.md exact-pin rule (L88-98, L574)
```
opencv-python-headless==4.14.0.94
requests==2.34.2
python-dotenv==1.2.3
numpy==2.5.3
```
Pin exact `==` versions (never `>=`) for Windows reproducibility (CLAUDE.md). Dev: add `pytest` (RESEARCH.md L516). `imageio-ffmpeg==0.6.0` optional fallback if standalone ffmpeg install fails (D-04 fallback, L79). Python target: 3.14 (system 3.14.6) — all deps have cp314 wheels (verified, L100).

---

### `scripts/probe_privratnik_auth.py` (utility, request-response)

**Analog:** none → **canonical reference:** RESEARCH.md Open Question 1 (L466-470) + Validation manual map (L529, L546)

**Purpose:** empirical auth probe against the REAL stream — validates A1 (login/token contract) and A2 (expiry semantics) BEFORE the full pipeline is wired (L469). This is the phase's highest-risk empirical checkpoint.
- Pattern: minimal `requests.Session` login + token extraction + stream-URL assembly (mirror `auth.py` L333-366, but standalone and disposable).
- Manual-only; needs user creds in `.env` (RESEARCH.md Validation L546).

---

### Test files (`tests/*`, `tests/conftest.py`, `pytest.ini`)

**Analog:** none → **canonical reference:** RESEARCH.md §Validation Architecture (L509-546) + 01-VALIDATION.md Wave 0 (L51-53)

Framework: `pytest` 8.x. Config via `pytest.ini` or `pyproject.toml` `[tool.pytest.ini_options]` (L517). Wave 0 adds `tests/conftest.py` (shared fixtures: mock auth, mock ffmpeg pipe — 01-VALIDATION.md L53).

| Test file | Covers | Reference | Mock strategy |
|-----------|--------|-----------|---------------|
| `tests/test_frame_buffer.py` | STREAM-04/05 (bounded, drop-oldest, camera_id) | L528 | pure stdlib queue, no network |
| `tests/test_auth.py` | STREAM-02 (login sets cookie/token) | L525 | mocked `requests` (no live creds) |
| `tests/test_stream_client.py` | STREAM-01 (ffmpeg cmd assembly, header injection) | L524 | `build_cmd` unit — no spawn |
| `tests/test_supervisor.py` | STREAM-03 (reconnect + backoff) | L526 | fake clock / fake capture |
| `tests/test_config.py` | D-02/D-06/D-07 (json+env merge, token not embedded) | L530 | static-file assertions |
| `tests/conftest.py` | shared fixtures | 01-VALIDATION.md L53 | mock auth + mock ffmpeg pipe |

**Empirical caveat** (L532): STREAM-02/03 core guarantee (auto-login + reconnect survives real token rotation) CANNOT be proven by mocked unit tests — must include the manual probe. Known, accepted limitation, not a test gap.

## Shared Patterns (cross-cutting)

### 1. Secrets & Config Separation (D-02/D-06/D-07)
**Source:** RESEARCH.md §Code:config (L290-323) + §Pitfall 4/5 (L268-277, L232)
**Apply to:** `src/config.py`, `src/capture/auth.py`, all capture modules
- Non-secrets → `config.json`; secrets → `.env` via `os.getenv()`. Never hardcode credentials/tokens in source or URLs. Append token at request time, never stored embedded in URL.

### 2. Re-invocable Auth / Reconnect (D-01/D-03, Pitfall 1)
**Source:** RESEARCH.md §Code:auth (L352-366) + §Pattern 2 (L212-226)
**Apply to:** `src/capture/auth.py`, `src/capture/supervisor.py`
- Centralize auth in `get_session()` — always re-login on reconnect, never reuse cached session/token. Supervisor detects "no valid frame for N sec" → tear down → re-auth → exponential backoff (1s→2s→…→60s) → respawn.

### 3. Non-blocking Bounded Queue (D-09/D-10, Pitfall 3)
**Source:** RESEARCH.md §Pattern 1 (L188-203) + §Code:FrameBuffer (L406-433)
**Apply to:** `src/capture/frame_buffer.py`, `src/capture/stream_client.py`
- Capture only ever `put`s to a bounded `queue.Queue(maxsize)` drop-oldest; never waits on a consumer. Frames tagged `camera_id` at enqueue.

### 4. Windows subprocess discipline (Pitfall 6)
**Source:** RESEARCH.md §Pitfall 6 (L280-284)
**Apply to:** `src/capture/stream_client.py`, `src/capture/supervisor.py`
- `subprocess.Popen(stdout=PIPE, stderr=PIPE, creationflags=CREATE_NO_WINDOW)`; drain stderr; `proc.kill()` + `proc.wait()` in `finally` on reconnect to avoid zombies.

### 5. Log/error redaction (V7, Pitfall 1/5, RESEARCH.md L233/L564)
**Apply to:** all modules
- Redact `token=` query param and PHPSESSID in every logged URL. Never treat "no frame" as "no event" — surface stale-stream and auth-expiry as named error conditions.

### 6. Config-driven ffmpeg path (Pitfall 2)
**Source:** RESEARCH.md §Pitfall 2 (L256-262)
**Apply to:** `src/main.py` pre-flight
- `ffmpeg_path` in `config.json`; pre-flight check resolves the binary; graceful "ffmpeg not found — install via: winget install Gyan.FFmpeg" message before capture runs. Do not assume ffmpeg is on PATH (verified NOT present this session).

## No Analog Found (in application code)

There are no existing application-code files in the repo (greenfield, verified this session). Every file above is mapped to the canonical tracked reference patterns in `01-RESEARCH.md`. The following are the highest-uncertainty items the planner/executor must treat as empirical checkpoints (they have NO codebase precedent to borrow from):

| File | Role | Data Flow | Reason (must validate empirically) |
|------|------|-----------|-------------------------------------|
| `src/capture/auth.py` | service | request-response | A1: exact privratnik.net login/token contract UNKNOWN — highest-risk project unknown (RESEARCH.md L466-470) |
| `src/capture/stream_client.py` | service | streaming | A3: MJPG pipe frame-delimiting method UNKNOWN (L396-403) |
| `scripts/probe_privratnik_auth.py` | utility | request-response | A2: session-expiry failure signature UNKNOWN — must probe real stream (L469, L532) |

## Metadata

**Analog search scope:**
- `git ls-files` (whole repo) — only `.claude/CLAUDE.md` tracked outside `.planning/`
- `Glob("**/*.py")` — 0 results
- `ls src/, tests/, scripts/` — none exist
- Verified `git ls-files` returns all cited planning docs as tracked source (satisfies tracked-source gate #3645) — every pattern reference points to `C:/dev/schlagbaum/.planning/phases/01-stream-client-frame-buffer/01-RESEARCH.md` (git-tracked), never a mirror path.

**Files scanned:** 0 application files (none exist); 2 doc sources read in full: `01-CONTEXT.md`, `01-RESEARCH.md`; plus `01-VALIDATION.md`, `.planning/config.json` (flags).

**Pattern extraction date:** 2026-09-09

**Config flags honored:** `workflow.nyquist_validation: true` (test framework section required), `security_enforcement: true` / `security_asvs_level: 1` / `security_block_on: high` (security domain required) — both confirmed in `.planning/config.json` L24/L48.
