# Phase 1: Stream Client + Frame Buffer - Research

**Researched:** 2026-09-09
**Domain:** Local video stream capture / HTTP proxy auth / producer-consumer frame buffering
**Confidence:** MEDIUM (environment-verified, web-unverifiable)

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** Авто-логин — система сама логинится на `privratnik.net` по логину/паролю, получает `PHPSESSID` + `token`, и при истечении сессии автоматически перелогинивается. Полностью автономно, без ручного вмешательства.
- **D-02:** Логин/пароль `privratnik.net` хранятся в `.env` файле (в `.gitignore`), читаются через `os.getenv()`.
- **D-03:** Переподключение при обрыве потока/истечении сессии — экспоненциальный backoff (1с → 2с → 4с → … до максимума).
- **D-04:** Захват кадров — ffmpeg-подпроцесс (декодирует поток, отдаёт кадры через pipe). Выбран за гибкость с HTTP-заголовками (`Referer`, `Range`, cookie) и надёжный reconnect.
- **D-05:** Формат кадров — JPEG через pipe (`image2pipe -vcodec mjpeg`), Python декодирует через OpenCV.
- **D-06:** Разделение конфига и секретов: не-секретные настройки (URL камер, параметры очереди, таймауты) — в `config.json`; секреты (логин/пароль/token) — в `.env`.
- **D-07:** URL камер — в `config.json` как список (`camera_id` → URL). Токен добавляется динамически при запросе, не хранится вшитым в URL.
- **D-08:** Частота захвата — 1-2 кадра/сек на камеру (достаточно для медленных событий шлагбаума, экономит CPU/память).
- **D-09:** Очередь — ограниченная (10-20 кадров) с политикой drop-oldest: медленный потребитель не блокирует захват.
- **D-10:** Одна очередь на камеру (каждый канал независим), кадры тегируются `camera_id` при помещении в очередь.

### Claude's Discretion
- Точный размер очереди (10 vs 20) и максимум backoff-интервала — на усмотрение Claude при планировании, в рамках выбранных политик.

### Deferred Ideas (OUT OF SCOPE)
None — discussion stayed within phase scope
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| STREAM-01 | Получает видеопоток с двух камер через `privratnik.net` proxy URL | ffmpeg subprocess decode (D-04/D-05) + camera_id→URL config list (D-07). See "Architecture Patterns — Pattern 1" and "Code Examples" |
| STREAM-02 | Поддерживает авторизацию (PHPSESSID cookie + token + Referer) | `requests.Session` auto-login flow (D-01) + headers/cookie on every request. See Pitfalls 1/9 |
| STREAM-03 | Автопереподключение и обновление токена при истечении сессии | Supervisor loop + exponential backoff (D-03) + central `get_session()` re-invocable. See Pattern 2 and Pitfall 1 |
| STREAM-04 | Каждая камера — независимый канал, события тегируются camera_id | Per-camera queue (D-10), frames tagged at enqueue. See Pattern 1 |
| STREAM-05 | Извлекает кадры из потока для последующего анализа | ffmpeg image2pipe mjpeg → cv2.imdecode → push to bounded drop-oldest queue (D-05/D-09). See Pattern 1 |
</phase_requirements>

## Summary

Phase 1 builds the ingestion layer of the linear video pipeline: two independent capture channels that authenticate against the `privratnik.net` proxy, decode an MP4/HTTP preview stream via an ffmpeg subprocess, decode JPEG frames with OpenCV, and push camera-tagged frames into a per-camera bounded drop-oldest queue. Everything downstream (barrier detector, vehicle detector, OCR, event store) consumes frames from these queues, so capture must never block on slow analysis — the bounded-queue pattern is the architectural decoupling point and is locked at this phase.

The single highest-risk item in the whole project is the `privratnik.net` auth/reconnect behavior (D-01/D-03/STREAM-03). The user's locked decision is **ffmpeg subprocess** capture (D-04), not OpenCV `VideoCapture` — the flexibility to send arbitrary HTTP headers (`Referer`, `Range: bytes=0-`, `PHPSESSID` cookie) and the reliable reconnect are why. This decision is **conditional on ffmpeg being installed**, and the environment probe shows ffmpeg is **NOT present on PATH** (verified this session). The plan MUST include an ffmpeg install/discovery step (winget/choco/scoop all available on this machine) and a graceful-error path that tells the user where to get ffmpeg, before any ffmpeg-based capture code runs.

Environment probe (this session, `[VERIFIED: env probe]`): Python **3.14.6** is the system interpreter — newer than the 3.12/3.13 the stack was pinned for, but every Phase 1 dependency publishes a cp314-compatible Windows wheel (verified by downloading wheels). requests 2.34.2 and python-dotenv are already installed at system level; OpenCV is NOT installed (expected — the plan creates a venv). Network access for pip works. No `.gitignore` exists yet (a new `.gitignore` must be created so `.env` and `data/` are never committed per D-02/D-06/D-07).

**Primary recommendation:** Use Python 3.14 in a venv with `opencv-python-headless==4.14.0.94`, `requests`, `python-dotenv`. Install ffmpeg early via winget/choco/scoop (all present) or discover an existing binary; make ffmpeg path configurable via `config.json` (not hardcoded). Build the capture as a per-camera `StreamClient` wrapping an ffmpeg subprocess + supervisor reconnect loop, feeding a bounded `queue.Queue(maxsize=15)` drop-oldest per camera. Centralize auth in a re-invocable `get_session()` and secrets in `.gitignore`'d `.env`.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| privratnik.net auth (login, PHPSESSID, token, Referer) | API / Backend client | — | Auth is an HTTP concern owned by a session manager (`get_session()`), re-invocable on reconnect (Pitfall 1, Pattern 2) |
| Stream decode (MP4/HTTP → JPEG frames) | API / Backend client | — | ffmpeg subprocess (D-04) decodes; a separate process owned by the client layer |
| Frame decode to numpy (JPEG → cv2 image) | API / Backend client | — | `cv2.imdecode` on piped bytes (D-05) — a client-layer transform |
| Frame rate control (1-2 fps per camera) | API / Backend client | — | FPS throttling belongs in capture loop (D-08) |
| Frame buffering (bounded drop-oldest) | API / Backend client | Database / Storage (secondary) | Queue decouples capture from downstream; per-camera (D-09/D-10). Later phases own consumers |
| camera_id tagging | API / Backend client | — | Tagged at enqueue time (D-10); every frame carries its camera |
| Reconnect / re-auth supervisor | API / Backend client | — | Supervisor loop owns stream lifecycle (D-03, Pitfall 1) |
| Config + secrets loading | Client (config) | OS env | `config.json` (non-secrets) + `.env` (secrets, gitignored) (D-06/D-07, Pitfall 9) |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Python | 3.14 (system) / 3.12+ | Application language | Present on this machine (3.14.6, verified); all Phase 1 deps publish cp314 wheels (verified) |
| `opencv-python-headless` | 4.14.0.94 (pin) | JPEG frame decode (`imdecode`), image preprocessing | The standard CV library; `-headless` avoids Qt/GUI DLL conflicts on Windows. Pin 4.14.x, avoid OpenCV 5.0 (breaking). Verified wheel: `cp37-abi3-win_amd64` installs on 3.14 |
| `requests` | 2.34.2 | HTTP proxy auth, session cookies, token refresh, headers | Standard HTTP client; `requests.Session` persists PHPSESSID cookie (D-01). Already installed (verified) |
| `python-dotenv` | 1.2.3 | Load secrets from `.env` | Keeps login/password/token out of source (D-02, Pitfall 9). Already installed (verified) |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `numpy` | 2.5.3 | Array ops / frame handling | Comes transitively with OpenCV; pin explicitly (CP314 wheel verified) |
| ffmpeg (system binary, NOT pip) | any recent (≥5.x) | MP4/HTTP stream decode → JPEG frames via pipe (D-04) | Required by D-04. **NOT installed on PATH (verified)** — plan must install/discover |
| `imageio-ffmpeg` | 0.6.0 | Bundles a static ffmpeg binary (pip-installable fallback) | Only if standalone ffmpeg install fails AND D-04 must be preserved; it ships an ffmpeg exe on Windows (wheel verified) |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| ffmpeg subprocess (D-04, locked) | OpenCV `VideoCapture(url)` | ffmpeg locked by user for header flexibility (`Referer`/`Range`/cookie) and reliable reconnect. OpenCV `VideoCapture` is simpler but holds a long-lived connection and is harder to re-auth mid-run (Pitfall 1). NOT the D-04 choice |
| `requests.Session` (D-01) | Manual `requests.get` per call | Session persists PHPSESSID cookie automatically — required for auth. Per-call is error-prone and leaks session state |
| `.env` (D-02, locked) | Windows Credential Manager / config-only | Locked: `.env` + `os.getenv()`. Credential Manager would need a native reader lib — extra dependency, rejected in discussion |

**Installation (Phase 1 scope — only what Phase 1 needs):**
```bash
# 1. ffmpeg (system binary — REQUIRED by D-04, NOT installed on PATH)
winget install Gyan.FFmpeg --scope user   # or: choco install ffmpeg | scoop install ffmpeg
# Verify: ffmpeg -version

# 2. venv + Phase 1 deps
python -m venv .venv
.venv\Scripts\activate
pip install opencv-python-headless==4.14.0.94 requests==2.34.2 python-dotenv numpy==2.5.3
```

**Version verification (this session):** `pip index versions` confirmed: opencv-python-headless 4.14.0.94 & 5.0.0.93 exist; requests 2.34.2; python-dotenv 1.2.3; numpy 2.5.3; Pillow 12.3.0; imageio-ffmpeg 0.6.0. Wheel downloads confirmed for **Python 3.14 / Windows**: `opencv_python_headless-4.14.0.94-cp37-abi3-win_amd64.whl`, `numpy-2.5.3-cp314-cp314-win_amd64.whl`, `pillow-12.3.0-cp314-cp314-win_amd64.whl`, `imageio_ffmpeg-0.6.0-py3-none-win_amd64.whl`. requests/dotenv are pure-python (py3-none-any).

**Compatibility note (Python 3.14):** STACK.md/CLAUDE.md recommend 3.12+/3.13. The installed interpreter is 3.14.6. All Phase 1 deps have 3.14 wheels (verified by download), so the venv should use 3.14 rather than requiring a 3.13 downgrade. This is a minor deviation from the doc's wording but does NOT change the pinned package versions.

## Package Legitimacy Audit

Gate ran via `gsd-tools query package-legitimacy check` (`[VERIFIED: package-legitimacy gate]`). All verdicts came back **SUS solely due to `unknown-downloads`** (the gate could not fetch weekly download counts in this offline-capable environment) — NOT due to genuine suspicious signals. Every core package resolves to its official upstream repo and `pip index versions` confirmed current, long-lived releases. This is an environmental limitation of the gate, not a security finding.

| Package | Registry | Registry Age | Downloads | Source Repo | Verdict | Disposition |
|---------|----------|-------------|-----------|-------------|---------|-------------|
| opencv-python-headless | PyPI | multi-year (4.x line mature) | metadata unavailable this session | github.com/opencv/opencv-python (official) | [SUS — unknown-downloads only] | Approved — official OpenCV repo, gate lacked download metadata |
| requests | PyPI | 10+ yrs (2.34.2 is current) | unavailable | github.com/psf/requests (official) | [SUS — unknown-downloads only] | Approved — official Python.org HTTP lib |
| python-dotenv | PyPI | 10+ yrs | unavailable | github.com/theskumar/python-dotenv (official) | [SUS — too-new, unknown-downloads] | Approved — "too-new" spurious (repo is a decade old) |
| numpy | PyPI | 10+ yrs | unavailable | no-repo surfaced in JSON | [SUS — unknown-downloads, no-repo] | Approved — numpy is the canonical array lib; repo field omitted by gate metadata, not a real gap |
| Pillow | PyPI | 10+ yrs | unavailable | github.com/python-pillow/Pillow (official) | [SUS — unknown-downloads only] | Approved — official |
| pytesseract | PyPI | 10+ yrs | unavailable | github.com/madmaze/pytesseract (official) | [SUS — unknown-downloads only] | Approved — official (deferred use; Phase 5) |
| imageio-ffmpeg | PyPI | mature | unavailable | github.com/imageio/imageio-ffmpeg (official) | [SUS — unknown-downloads only] | Approved — fallback only |

**Packages removed due to [SLOP] verdict:** none
**Packages flagged as suspicious [SUS] — genuine concern requiring `checkpoint:human-verify`:** none. All SUS flags are environmental (`unknown-downloads` — the gate couldn't fetch counts here), not legitimacy failures.

> Note: The [SUS] tags are reported faithfully rather than silently converted to OK, per the protocol's honesty requirement. The disposition is "Approved" because the SUS cause is a metadata gap in this environment, not a suspicious signal — but the planner may optionally add a lightweight `checkpoint:human-verify` as belt-and-suspenders since the gate could not produce a download-count signal.

## Architecture Patterns

### System Architecture Diagram

```
                 ┌─────────────────────────────── privratnik.net ───────────────┐
                 │  login (POST /login) → PHPSESSID cookie + token             │
                 │  proxy.php?link=<cam_url>&token=<TOKEN> (GET, MP4 preview)  │
                 │  Headers: Referer: https://privratnik.net/files/video-...  │
                 │           Range: bytes=0-   Cookie: PHPSESSID=...           │
                 └──────┬──────────────────────────────┬───────────────────────┘
                        │                              │
              stream URL (cam1 +live_token)    stream URL (cam2 +live_token)
                        ▼                              ▼
        ┌──────────────────────────┐     ┌──────────────────────────┐
        │  StreamClient(camera_1)  │     │  StreamClient(camera_2)  │   [API/Backend tier]
        │  get_session() → session │     │  get_session() → session │
        │  spawn ffmpeg subprocess │     │  spawn ffmpeg subprocess │
        │  ffmpeg -headers -i <url>│     │  ffmpeg -headers -i <url>│
        │    -f image2pipe         │     │    -f image2pipe         │
        │    -vcodec mjpeg -       │     │    -vcodec mjpeg -       │
        └───────────┬──────────────┘     └───────────┬──────────────┘
                    │  JPEG bytes (stdout pipe)       │  JPEG bytes
                    ▼                                 ▼
        ┌──────────────────────────┐     ┌──────────────────────────┐
        │  cv2.imdecode → frame    │     │  cv2.imdecode → frame    │   decode (client tier)
        │  FPS throttle 1.5 fps    │     │  FPS throttle 1.5 fps    │
        │  tag(camera_id="cam_1")  │     │  tag(camera_id="cam_2")  │
        └───────────┬──────────────┘     └───────────┬──────────────┘
                    ▼                                 ▼
        ┌──────────────────────┐        ┌──────────────────────┐
        │  FrameBuffer(cam_1)  │        │  FrameBuffer(cam_2)  │   bounded queue
        │  Queue(maxsize=15)   │        │  Queue(maxsize=15)   │   drop-oldest (D-09)
        │  drop-oldest         │        │  drop-oldest         │
        └───────────┬──────────┘        └───────────┬──────────┘
                    │                               │
                    │  frame(camera_id, ts, ndarray)│  ← Phase 2+ consumers (NOT built here)
                    └───────────────►►► └───────────►►►
                                        (integration point: barrier detector, Phase 2)

  Reconnect path (on stale stream / token expiry):
    supervisor thread per camera:
      no valid frame for N sec?  → kill ffmpeg proc → get_session() re-auth → backoff(1s→2s→4s→max) → respawn
```

### Recommended Project Structure
```
schlagbaum/
├── src/
│   ├── capture/
│   │   ├── auth.py            # get_session(): login → PHPSESSID+token; re-invocable (D-01, Pitfall 1)
│   │   ├── stream_client.py   # per-camera ffmpeg subprocess decode + FPS throttle (D-04/D-05/D-08)
│   │   ├── frame_buffer.py    # bounded drop-oldest queue, camera_id tagging (D-09/D-10)
│   │   └── supervisor.py      # reconnect/re-auth loop with exponential backoff (D-03, Pitfall 1)
│   ├── config.py              # load config.json + .env → Settings dataclass (D-06/D-07)
│   └── main.py                # entrypoint: build 2 StreamClients + 2 FrameBuffers, wire supervisor
├── config.json                # non-secret: cameras[], queue_size, fps, timeouts, ffmpeg_path (D-06)
├── .env                       # SECRETS, gitignored: PRIVRATNIK_LOGIN, PRIVRATNIK_PASSWORD (D-02)
├── .env.example               # documented keys, committed (no real values)
├── .gitignore                 # MUST be created: exclude .env, .venv/, data/, __pycache__/
├── data/                      # runtime, gitignored (photos/events in later phases)
├── tests/                     # see Validation Architecture
└── requirements.txt           # pinned == versions
```

### Pattern 1: Producer–Consumer Pipeline with Bounded Drop-Oldest Queue (per camera)
**What:** Capture thread decodes a stream and pushes frames into a bounded `queue.Queue(maxsize=N)`; a downstream consumer pops at its own pace. When the queue is full, the oldest frame is dropped first (drop-oldest) so the capture thread never blocks.
**When to use:** Always in video pipelines — capture is fast, analysis/OCR is slow (Pitfall 2). Drop-oldest means you may skip frames under load, which is acceptable because you only need one good frame per event, not every frame.
**Example** (drop-oldest put, per the locked D-09 policy):
```python
import queue

def push_or_drop(frame_q, frame, camera_id):
    """Non-blocking bounded put: drop the oldest when full (D-09)."""
    if frame_q.full():
        try:
            frame_q.get_nowait()  # drop oldest
        except queue.Empty:
            pass
    frame_q.put(frame)  # frame is a tagged Frame(camera_id=camera_id, ts=..., data=...)
```

### Pattern 2: Reconnect / Re-auth Supervisor with Exponential Backoff (STREAM-03 / D-03)
**What:** Treat the stream connection as ephemeral and reconnectable, not a persistent handle. A supervisor thread wraps capture: if no valid frame arrives within a threshold, it tears down the ffmpeg process, re-invokes `get_session()` to refresh token/session (Pitfall 1), waits with exponential backoff (1s → 2s → 4s → max), then respawns the ffmpeg subprocess.
**When to use:** Always for this proxy stream — tokens are short-lived and rotate (PROJECT.md). A single long-lived connection holds a stale token and silently goes dark (Pitfall 1).
**Example** (backoff skeleton; exact max interval is Claude's discretion, recommend 60s):
```python
import time

def run_capture_with_supervisor(start, stop_event, session_mgr, camera):
    backoff = 1                       # starts at 1s (D-03)
    max_backoff = 60                  # discretion: cap at 60s
    while not stop_event.is_set():
        try:
            proc = start(camera, session_mgr.live_headers(camera))
            yield_from_frames_or_raise(proc)   # raises StreamStale if no frame in N sec
            backoff = 1  # healthy; reset
        except (StreamStaleError, AuthExpiredError) as e:
            log(e)
            session_mgr.invalidate(camera)     # force get_session() re-auth (Pitfall 1)
            kill_proc(proc)
            stop_event.wait(backoff)           # sleep, interruptible
            backoff = min(backoff * 2, max_backoff)
```

### Anti-Patterns to Avoid
- **Single long-lived ffmpeg subprocess / `cv2.VideoCapture(url)` with no reconnect:** holds a stale token; stream silently stops producing frames (Pitfall 1). Supervisors always re-auth + reconnect.
- **`if no frame: pass` (treating "no frame" as "no event"):** hides stream death — the system goes dark with no error. The supervisor must detect "no valid frame for N seconds" and surface it (Pitfall 1).
- **Blocking the capture thread on slow downstream analysis:** defeats the whole bounded-queue purpose (Pitfall 2). Capture must only ever `put` to a bounded queue and never wait on a consumer.
- **Hardcoding camera URLs / building the token into the URL string:** tokens rotate; embedding them in source means editing source to update and risks committing secrets (Pitfall 9). Keep URLs in `config.json` and append token at request time (D-07).
- **Logging full stream URLs that contain the token:** tokens leak into log files (security). Redact the `token=` query param in any logged URL.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| HTTP auth / session / cookie persistence (PHPSESSID) | A hand-rolled cookie jar / socket HTTP client | `requests.Session` | Battle-tested cookie persistence, redirects, headers. Hand-rolling cookies is bug-prone (Pitfall 1). |
| MP4/HTTP stream decode → frames | Writing your own demuxer/decoder | ffmpeg subprocess (D-04, locked) or OpenCV decode | ffmpeg handles exotic codecs, HTTP reconnects, and header injection far better than any hand-built decoder (STACK.md). |
| Frame decode (JPEG → numpy) | Writing a JPEG decoder | `cv2.imdecode` | OpenCV's bundled JPEG decoder; not worth hand-rolling (D-05). |
| Loading secrets from env | Parsing `.env` by hand | `python-dotenv` | Standard for env-file loading; trivial to use (D-02). |
| Bounded drop-oldest queue | A hand-rolled lock + list queue | `queue.Queue` (stdlib) | Thread-safe, maxsize-bounded, with `get_nowait`/task_done. Hand-rolling a thread-safe queue is a concurrency bug factory (D-09). |
| Exponential reconnect backoff | A fixed sleep or no delay | Small loop with `min(backoff*2, max)` + interruptible wait | Fixed delays hammer the proxy; no delay spins hot. A backoff loop is trivial to write correctly (avoid `time.sleep` non-interruptible). |

**Key insight:** The genuinely custom parts of this phase are the *proxy auth flow* (login → session → header-injection into ffmpeg) and the *supervisor lifecycle* — there is no library for "authenticate against privratnik.net and feed headers to ffmpeg." Everything else (HTTP, decode, JPEG, queue, env loading) is best left to the standard libraries. Don't hand-roll HTTP, decode, or threading primitives.

## Common Pitfalls

### Pitfall 1: Token / session expiry silently kills the camera stream
**What goes wrong:** The stream requires token in query param + PHPSESSID cookie + Referer. When the token rotates or the session expires, the client gets a 401/403 or an empty/error stream. If code treats "no frame" as "no event," the system goes dark silently — no openings recorded, no error surfaced (PITFALLS.md Pitfall 1).
**Why it happens:** Tokens are short-lived and rotate. The client opens one connection at startup and never re-authenticates; a single long-lived ffmpeg/OpenCV handle holds a stale token.
**How to avoid:** Wrap capture in a supervisor that detects "no valid frame for N seconds" (recommend 10-15s) and tears down + re-auths + reconnects. Centralize auth in `get_session()` as a re-invocable function, not a startup side effect. Re-fetch token/session on reconnect, don't reuse the cached one. Redact token in logs.
**Warning signs:** Stream connects at startup but produces no frames after some hours/days; a single successful connect then silence; events stop but the process runs.

### Pitfall 2: Not installing ffmpeg means D-04 capture code cannot run
**What goes wrong:** D-04 locks ffmpeg-subprocess capture, but the plan must not assume ffmpeg is present. On this machine ffmpeg is **NOT on PATH** (`[VERIFIED: env probe]`). The capture module will fail with `FileNotFoundError` / `No such file or directory` at first spawn.
**Why it happens:** ffmpeg is a system binary, not a pip dependency. It is installed separately.
**How to avoid:** Make ffmpeg path configurable in `config.json` (default `ffmpeg` → PATH lookup). Add an install/discovery task early (winget/choco/scoop all present). Add a graceful pre-flight check: verify `ffmpeg -version` or the configured path resolves before starting capture, and surface a clear "ffmpeg not found — install via: winget install Gyan.FFmpeg" message. Fallback option: `imageio-ffmpeg` bundles a static ffmpeg exe (wheel verified) if standalone install is undesirable — but keep native ffmpeg first per D-04.
**Warning signs:** Immediate `FileNotFoundError` on first capture spawn; log shows no ffmpeg path resolved.

### Pitfall 3: Blocking the capture loop on a slow consumer drops the target event
**What goes wrong:** If capture awaits a downstream consumer (analysis/OCR is 0.5-2s per call), the capture thread stalls and drops frames — including the very event you want (Pitfall 2).
**Why it happens:** It is the natural first implementation to process synchronously in the same loop.
**How to avoid:** Per D-09, capture only ever `put` into a bounded drop-oldest queue and never waits on the consumer. The queue is the decoupling contract; downstream phases consume at their own pace. Verify capture keeps up when the queue is full (frames are dropped oldest-first, never blocked).
**Warning signs:** Frame timestamps show gaps; CPU pegged on the analysis thread; capture stalls when the consumer sleeps.

### Pitfall 4: Committing secrets / not having a `.gitignore`
**What goes wrong:** Login/password/token end up committed to git (Pitfall 9). This project has **NO `.gitignore` yet** (`[VERIFIED: env probe]`), so nothing currently stops `.env` from being tracked.
**Why it happens:** Secrets are often pasted into source or un-guarded `.env` in an MVP. The repo has no ignore rules.
**How to avoid:** Create `.gitignore` as an explicit early task — at minimum `.env`, `.venv/`, `data/`, `__pycache__/`, `*.pyc`. Provide a committed `.env.example` with documented keys but no real values. Never put token/credentials in source. Add a check that `.env` is ignored (`git check-ignore .env` returns it).
**Warning signs:** A token or key appears in a git diff or committed file; `.gitignore` does not exclude `.env`.

### Pitfall 5: Hardcoding camera URLs with tokens baked in
**What goes wrong:** Token rotation forces a source edit; full URLs with tokens risk leaking credentials (D-07/Pitfall 9).
**Why it happens:** Fastest way to a prototype — paste the token into the URL string.
**How to avoid:** Store camera URLs in `config.json` as a `camera_id` → base-URL list; append `?token=<live>` via `get_session()` at request time, never store the token in the URL config (D-07). Treat token as a runtime value.
**Warning signs:** Camera URL in a config or code file contains `token=…`.

### Pitfall 6: Windows process/spawn details for an ffmpeg subprocess
**What goes wrong:** On Windows, spawn flags matter. Default `subprocess.Popen` may flash a console window or inherit the wrong fds; closing the pipe without draining it blocks the parent.
**Why it happens:** The decode is a long-running child process whose stdout you drain. If you don't read its stdout (or set `stdout=PIPE` and ignore it), the child blocks once the OS pipe buffer fills, and capture stalls.
**How to avoid:** Spawn with `subprocess.Popen([...], stdout=subprocess.PIPE, stderr=subprocess.PIPE)` and read frames from `proc.stdout` in the capture loop. Set `creationflags=subprocess.CREATE_NO_WINDOW` on Windows to suppress a console window (recommended for a background service). Read/drain `stderr` (or redirect to a log) so it doesn't fill and block. Kill the child robustly on reconnect (`proc.kill()` + `proc.wait()`), never leave a zombie.
**Warning signs:** A console window pops up on each capture; capture stalls after a burst of frames (pipe backpressure); orphaned ffmpeg processes accumulate after reconnects.

## Code Examples

> Source note: WebSearch/WebFetch are non-functional in this environment (`[VERIFIED: env probe]` — empty/meta results and blocked fetches), consistent with PITFALLS.md's note that live web sources were unreachable. The patterns below are standard practice verified from the existing HIGH-confidence project research (ARCHITECTURE.md/STACK.md) plus domain knowledge; the library-usage specifics are tagged `[ASSUMED]` where they are training-canonical rather than doc-cited this session.

### Config / secrets loading (`config.py`)
```python
# config.json (non-secrets, committed)
# {
#   "cameras": { "cam_1": "https://cam2.privratnik.net/80146f20_3105/preview.mp4",
#               "cam_2": "https://cam2.privratnik.net/f9456e90_3099/preview.mp4" },
#   "queue_size": 15,           # Claude's discretion: 10-20 (D-09)
#   "capture_fps": 1.5,         # 1-2 fps per camera (D-08)
#   "frame_stale_seconds": 12,  # no-valid-frame threshold for supervisor
#   "backoff_initial": 1.0, "backoff_max": 60.0,   # 1s → 2s → … → 60s (D-03, max=discretion)
#   "ffmpeg_path": "ffmpeg",    # PATH lookup; or absolute path
#   "referer": "https://privratnik.net/files/video-control.php"
# }

# .env (gitignored, secrets — D-02)
# PRIVRATNIK_LOGIN=...
# PRIVRATNIK_PASSWORD=...

import json, os
try:
    from dotenv import load_dotenv
    load_dotenv()                      # loads .env into os.environ
except ImportError:
    pass

def load_settings(config_path="config.json"):
    with open(config_path, encoding="utf-8") as f:
        raw = json.load(f)
    return {
        **raw,
        "login": os.getenv("PRIVRATNIK_LOGIN"),          # secrets from env, never config
        "password": os.getenv("PRIVRATNIK_PASSWORD"),
    }
```
`[ASSUMED: stdlib json + python-dotenv standard usage; mirrors STACK.md recommendation]`

### Auth session manager (`auth.py`) — auto-login + re-invocable `get_session()` (D-01, STREAM-02)
```python
import requests

AUTH_URL = "https://privratnik.net/login"   # [ASSUMED] exact endpoint — see Open Question
REFERER = "https://privratnik.net/files/video-control.php"

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
`[ASSUMED: requests.Session cookie persistence + custom-header injection into ffmpeg; the exact login/`AUTH_URL`/token-extraction contract is UNKNOWN (see Open Questions) and must be validated against the real stream — a Phase 1 empirical checkpoint, per PROJECT.md and SUMMARY.md]`

### ffmpeg subprocess capture (`stream_client.py`) — D-04/D-05/D-08
```python
import subprocess, cv2, numpy as np

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

def read_jpeg_frame(proc):
    """Read one MJPG frame from ffmpeg stdout. JPEG frames are delimited; parse via cv2.imdecode.
       Returns numpy frame or None at EOF. [ASSUMED: mjpeg-over-pipe framing pattern]"""
    raise NotImplementedError
    # Practical approach: mjpeg frames are not length-prefixed in piped output.
    # A robust pattern reads bytes until a fresh 0xFFD8 (SOI) then decodes the preceding
    # [0xFFD8..0xFFD9] JPEG; or use OpenCV's imdecode on a buffered chunk with jpeg parser.
    # Simplest reliable option: use the 'image2pipe' mjpeg with a length-delimited container,
    # OR drain chunks and cv2.imdecode each complete JPEG. Implement + verify empirically.
```
`[ASSUMED: ffmpeg `-f image2pipe -vcodec mjpeg -` stdout framing; STACK.md documents the `ffmpeg -i <url> -f image2pipe -vcodec mjpeg -` pattern at CITED level, but the exact byte-length/frame-delimiting details must be validated empirically in Phase 1]`

### Bounded drop-oldest frame buffer (`frame_buffer.py`) — D-09/D-10
```python
import queue

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
`[ASSUMED: stdlib queue.Queue bounded semantics; pattern verified in ARCHITECTURE.md (queue.Queue(maxsize=N) drop-oldest) at CITED level]`

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| OpenCV `VideoCapture(url)` as the only capture path | ffmpeg subprocess piped decode (D-04, locked) | Locked this phase | Better header injection (Referer/Range/cookie) and reliable reconnect for the proxy stream |
| `opencv-python` (GUI) | `opencv-python-headless` | Stack decision | No Qt/DLL conflicts on Windows; smaller service install |
| Python 3.12/3.13 target | Python 3.14 (system 3.14.6) | This session (env) | All Phase 1 deps publish cp314 wheels (verified); no code change, but venv uses 3.14 |
| No `.gitignore` in repo | `.gitignore` must be created this phase | This phase (D-02/D-06) | Prevents secret commit (Pitfall 4/9) |

**Deprecated/outdated:**
- **OpenCV 5.0.x:** major-version breaking change; ecosystem still 4.x-oriented (STACK.md). Keep pinned 4.14.0.94.
- **`schedule`/`APScheduler` for startup scheduling:** superseded by Windows Task Scheduler (native); only relevant in Phase 6.
- **`opencv-python` (non-headless) in a service:** pulls GUI deps; use `-headless`.

## Assumptions Log

> Claims not tool-verified this session (WebSearch/WebFetch non-functional). All flagged for planner/executor attention.

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `privratnik.net` login endpoint, request params, and token-extraction contract (HTML/JSON/redirect target) | Code Examples / auth.py | HIGH — D-01/STREAM-02/STREAM-03 all hinge on the true auth flow. Must be validated against the real stream (empirical checkpoint). PROJECT.md/SUMMARY.md flag this as the highest-risk unknown |
| A2 | Token/session expiry semantics (how the server signals it: 401 vs 403 vs empty stream vs redirect) | Pitfall 1 | MEDIUM — the supervisor's "no valid frame" heuristic must match the real failure signature; needs real-stream validation |
| A3 | ffmpeg `-f image2pipe -vcodec mjpeg` stdout frame-delimiting method (byte parsing vs imdecode buffering) | Pattern 1 / stream_client.py | MEDIUM — an incorrect framing read yields corrupt/split frames; verify empirically in Phase 1 |
| A4 | The exact stream URL token query param name and placement (`?token=` vs other) | auth.py | MEDIUM — if the param name differs, capture gets 401. From PROJECT.md sample `?token=<TOKEN>` [CITED] |
| A5 | `subprocess.CREATE_NO_WINDOW` suppresses the console on all Windows Python builds | Pitfall 6 | LOW — cosmetic/UX; if unsupported, service still runs with a console window |
| A6 | Python 3.14 (system) is acceptable for the venv rather than requiring 3.12/3.13 | Summary / Stack | LOW — wheels verified present, so no functional risk; only a doc deviation |
| A7 | FPS throttle via `-vf fps=1.5` is a suitable rate-control mechanism for the mjpeg pipe | Pattern 1 | LOW — alternative is time-based throttle in Python; both fine, verify output rate |

## Open Questions (RESOLVED)

> All four questions below are resolved by Phase 1 plans (01-01 / 01-02). Each carries its resolution route.

1. **What is the exact `privratnik.net` login/token flow?** — (RESOLVED via 01-02 Task 3 probe script)
   - What we know: PROJECT.md says login via phone/password → page `video-control.php`; stream at `proxy.php?link=<cam_url>?token=<TOKEN>`; headers `Referer` + `Range: bytes=0-` + `PHPSESSID` cookie. User can reset token/lines from DevTools Network log.
   - What's unclear: the POST endpoint, the request fields, and how the token is returned (HTML form, JSON, redirect). This is **the highest-risk unknown in the whole project** (SUMMARY.md).
   - Resolution: Phase 1 (01-02 Task 3) ships `scripts/probe_privratnik_auth.py`, a standalone empirical probe that validates A1/A2 against the real stream before the full pipeline is trusted. `auth.py` implements a best-effort token parser (HTML/JSON/redirect) that the probe refines. If the user provides a Network-tab export, extract the exact request shape.

2. **Is the stream an endless preview or a finite MP4 clip that needs periodic re-opening?** — (RESOLVED via 01-02 supervisor design)
   - What we know: PROJECT.md/STACK.md say `proxy.php` returns MP4 previews, not persistent RTSP; CONTEXT.md says "поток отдаётся как MP4/превью … нужно периодически переоткрывать поток".
   - What's unclear: the clip length and whether the proxy loops/fades. This affects whether the supervisor re-opens on a schedule vs only on error.
   - Resolution: the supervisor (01-02 Task 2) treats each ffmpeg subprocess as short-lived and re-opens on EOF/stale-stream (no valid frame within `frame_stale_seconds`), rather than assuming an endless pipe. `read_jpeg_frame()` returns `None` at EOF, which triggers the reconnect path.

3. **FPS/queue sizing exact values (Claude's discretion)** — (RESOLVED via 01-01 config defaults)
   - What we know: D-08 1-2 fps; D-09 10-20 queue; D-03 1s→max.
   - What's unclear: concrete values.
   - Resolution: 01-01 Task 2 sets `config.json` defaults `queue_size=15`, `capture_fps=1.5`, `frame_stale_seconds=12`, `backoff_max=60.0`. These satisfy the locked ranges; recorded as config defaults, easy to tune.

4. **Is ffmpeg install acceptable before capture runs?** — (RESOLVED via 01-02 pre-flight + configurable path)
   - What we know: ffmpeg is NOT installed (`[VERIFIED: env probe]`); winget/choco/scoop all present.
   - What's unclear: user preference for install method, or whether an existing binary exists elsewhere.
   - Resolution: `ffmpeg_path` is configurable in `config.json`; 01-02 Task 2 adds a graceful missing-ffmpeg pre-flight check in `main.py` that prints a clear "install via: winget install Gyan.FFmpeg" message and exits non-zero. Flagged to user for confirmation at planning.

## Environment Availability

> Step 2.6 ran — this phase has real external dependencies (ffmpeg binary, network, venv).

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python | Application runtime | ✓ | 3.14.6 (system) | — (use 3.14; deps have cp314 wheels, verified) |
| pip (network) | Dependency install | ✓ | 26.2.1 | — |
| venv + ensurepip | Isolated env | ✓ | bundled | — |
| ffmpeg (system binary) | **D-04 capture — REQUIRED, NOT on PATH** | ✗ | — | winget/choco/scoop install all available; `imageio-ffmpeg` bundles a static exe (wheel verified); set `ffmpeg_path` in config |
| `opencv-python-headless` | JPEG frame decode | pip-installable (wheel verified) | 4.14.0.94 | — |
| `requests` | HTTP auth/session | ✓ (installed 2.34.2) | 2.34.2 | — |
| `python-dotenv` | secrets loading | ✓ (installed 1.2.2) | 1.2.3 pin | — |
| Network to privratnik.net | Real-stream auth/capture | unverified (no test creds present) | — | Must be validated empirically in Phase 1 with user creds |
| privratnik.net credentials | STREAM-02 login | ✗ (no `.env`, no creds in repo) | — | User supplies via `.env` (gitignored) at execution |

**Missing dependencies with no fallback:**
- **ffmpeg binary** for D-04 ffmpeg-subprocess capture. Not present on PATH (verified). Must be installed (winget/choco/scoop) or a binary path configured before capture runs. This is a hard prerequisite — the locked D-04 approach cannot run without it.
- **privratnik.net credentials + network access to the proxy** for real-stream validation (D-01/STREAM-02/STREAM-03). Not in repo; the user must provide them in a `.env` at execution and the auth flow must be validated against the live stream (the phase's highest-risk empirical checkpoint).

**Missing dependencies with fallback:**
- ffmpeg → `imageio-ffmpeg` (pip, bundles a static ffmpeg binary, wheel verified) or OpenCV decode (not the D-04 choice, but a debug fallback).

## Validation Architecture

> `workflow.nyquist_validation` is `true` (enabled) in `.planning/config.json` `[VERIFIED: config.json]` — the section is required.

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.x (stdlib `pytest`; add to requirements-dev) — no framework currently installed/declared |
| Config file | none yet — Wave 0 must add `pytest.ini` or `pyproject.toml` `[tool.pytest.ini_options]` |
| Quick run command | `python -m pytest tests/test_frame_buffer.py tests/test_config.py -x` |
| Full suite command | `python -m pytest` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| STREAM-01 | Builds ffmpeg subprocess from a camera URL + headers | unit (no network) | `pytest tests/test_stream_client.py::test_build_cmd -x` | ❌ Wave 0 |
| STREAM-02 | `SessionManager.login()` sets PHPSESSID cookie + token (against a mock, not live) | unit (mocked requests) | `pytest tests/test_auth.py::test_login_sets_cookie -x` | ❌ Wave 0 |
| STREAM-03 | Supervisor re-auths + backoffs on stale-stream/auth failure | unit (fake clock/fake capture) | `pytest tests/test_supervisor.py -x` | ❌ Wave 0 |
| STREAM-04 | Per-camera tagging: frame pushed to cam_1 buffer carries camera_id="cam_1" | unit | `pytest tests/test_frame_buffer.py -x` | ❌ Wave 0 |
| STREAM-05 | `FrameBuffer` decodes/accepts frames and drop-oldest keeps size bounded | unit | `pytest tests/test_frame_buffer.py::test_drop_oldest -x` | ❌ Wave 0 |
| STREAM-03 (reality) | Auto-reconnect after token rotation against the REAL stream | **manual / integration** (needs live creds) | manual — cannot be automated without credentials | ❌ Wave 0 (manual script) |
| D-02/D-06/D-07 | `.env` is gitignored; secrets not in config; token appended at request time | static check | `git check-ignore .env` + `grep -r PRIVRATNIK src config.json` | ❌ Wave 0 |

> **Empirical caveat:** STREAM-02/STREAM-03's core guarantee (auto-login + reconnect survives a real token rotation) CANNOT be proven by mocked unit tests alone. The plan must include a lightweight manual probe against the live stream (per PROJECT.md/SUMMARY.md "validate empirically in Phase 1"). This is a known, accepted limitation, not a test gap.

### Sampling Rate
- **Per task commit:** `python -m pytest tests/test_frame_buffer.py tests/test_config.py tests/test_auth.py -x`
- **Per wave merge:** `python -m pytest` (full suite)
- **Phase gate:** Full suite green + manual live-stream auth probe passing before `/gsd-verify-work`

### Wave 0 Gaps
- [ ] `tests/test_frame_buffer.py` — covers STREAM-04/STREAM-05 (bounded, drop-oldest, camera_id tagging)
- [ ] `tests/test_auth.py` — covers STREAM-02 (login sets cookie/token, mocked `requests`)
- [ ] `tests/test_stream_client.py` — covers STREAM-01 (ffmpeg cmd assembly, header injection)
- [ ] `tests/test_supervisor.py` — covers STREAM-03 (reconnect + backoff, fake clock)
- [ ] `tests/test_config.py` — covers D-02/D-06/D-07 (config.json + .env merge, token not embedded)
- [ ] `pytest.ini` / `[tool.pytest.ini_options]` — framework config; add `pytest` to dev requirements
- [ ] Manual probe script `scripts/probe_privratnik_auth.py` — live-stream auth validation (STREAM-02/03 empirical check)

## Security Domain

> `security_enforcement` is `true` in `.planning/config.json` `[VERIFIED: config.json]` — the section is required.

### Applicable ASVS Categories
| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | yes | Secrets from `.env` via `os.getenv()`; `requests.Session` handles credentials; credentials never in source/config (D-02/Pitfall 9) |
| V3 Session Management | yes | PHPSESSID held by `requests.Session`; token refreshed via re-invocable `get_session()` on reconnect (Pitfall 1); don't log tokens |
| V7 Error/Log Handling | yes | Redact `token=` query param in any logged URL (PITFALLS.md security section); surface clear "ffmpeg not found" / "auth expired" states |
| V11 Business Logic | partial | Auto-reconnect must not loop hot; exponential backoff caps the retry rate (reduces proxy load, D-03) |

### Known Threat Patterns for the Phase 1 stack
| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Credential leak to git (login/password in tracked `.env`) | Information Disclosure | Create `.gitignore` (`.env`, `.venv/`, `data/`); commit only `.env.example` with no values; static check `git check-ignore .env` (Pitfall 9) |
| Token leakage into logs (full stream URLs) | Information Disclosure | Redact `token=` query param and PHPSESSID in all log output before writing |
| Reconnect hot-loop hammering the proxy (no backoff / too-fast retry) | Denial of Service (self) | Exponential backoff 1s→…→max with a cap (D-03); interruptible sleep so shutdown isn't delayed |
| Orphaned ffmpeg child processes after reconnect/crash | Resource Exhaustion | `proc.kill()` + `proc.wait()` in a `finally`; track the child on the supervisor; terminate on shutdown |
| Unauthorized access to captured data (future) | — | Scope: data/ is local and gitignored for now; restrict `data/` perms if sensitive. Not opened to the network this phase |

## Project Constraints (from CLAUDE.md)

Actionable directives extracted from `.claude/CLAUDE.md` `[VERIFIED: .claude/CLAUDE.md]` that constrain Phase 1 planning:

- **Tech stack:** Python (OpenCV, ffmpeg) for capture; the phase IS the capture layer. SQLite/external OCR are later phases — do not build them here.
- **Pin exact versions (`==`) in `requirements.txt`** for reproducibility on the user's Windows PC — do not use `>=` ranges.
- **Use `opencv-python-headless`** (not the GUI variant) in a venv; install headless *before* anything that might pull the GUI variant. Phase 1 needs only headless.
- **Pin `opencv-python-headless==4.14.0.94`**; explicitly do NOT adopt OpenCV 5.0.x.
- **ffmpeg is a system binary** (not pip) — needs a standalone install; this is a Phase 1 prerequisite (D-04).
- **Token/session/camera URLs must come from env/config, not hardcoded source** — this phase's core config/secrets work.
- **Deployment is a Windows local PC; auto-start via Task Scheduler** — relevant to Phase 6, not Phase 1, but avoid anything that breaks a background Windows service (e.g. `subprocess.CREATE_NO_WINDOW` instead of console windows).
- **GSD workflow enforcement:** any repo edits from the plan go through `/gsd-execute-phase` — planning artifacts (this RESEARCH.md) are written as docs, not repo code edits.
- **Do not build gate control / notifications / vehicle detection / OCR in this phase** — strictly the capture + frame buffer slice (STREAM-01..05) + config/secrets feeding the first token.

## Sources

### Primary (verified this session via tooling)
- **Environment probe (Bash)** — `[VERIFIED: env probe]`: Python 3.14.6, pip 26.2.1, venv+ensurepip OK, ffmpeg NOT on PATH, all of winget/choco/scoop present, git 2.55, requests 2.34.2 + python-dotenv 1.2.2 installed, no `.gitignore`, no `.env`/creds/data/tests/requirements files, no project skills dirs
- **PyPI (pip index versions + pip download)** — `[VERIFIED: npm registry-analog]`: confirmed current versions and **cp314 Windows wheel availability** for opencv-python-headless 4.14.0.94, numpy 2.5.3, Pillow 12.3.0, requests 2.34.2, python-dotenv 1.2.3, pytesseract 0.3.13, imageio-ffmpeg 0.6.0
- **package-legitimacy gate** — `[VERIFIED: package-legitimacy gate]`: all core packages resolve to official upstream repos; SUS verdicts are `unknown-downloads` metadata gaps only
- **`.planning/config.json`** — `[VERIFIED: .planning/config.json]`: `workflow.nyquist_validation: true`, `security_enforcement: true`
- **`.claude/CLAUDE.md`** — `[VERIFIED: .claude/CLAUDE.md]`: pinned stack, headless requirement, ffmpeg-as-system-binary, exact-pin rule, GSD workflow enforcement

### Secondary (project's own canonical research — CITED, HIGH confidence)
- `.planning/PROJECT.md` — `[CITED: .planning/PROJECT.md]` — proxy auth details (PHPSESSID + token + Referer + Range), camera URL samples, MP4-preview context
- `.planning/research/STACK.md` — `[CITED: .planning/research/STACK.md]` — pinned stack, ffmpeg/image2pipe pattern, headless-vs-gui, Python 3.12/3.13 note
- `.planning/research/ARCHITECTURE.md` — `[CITED: .planning/research/ARCHITECTURE.md]` — bounded drop-oldest producer-consumer, component responsibilities, project structure, build order
- `.planning/research/PITFALLS.md` — `[CITED: .planning/research/PITFALLS.md]` — Pitfall 1 (token/session expiry), Pitfall 2 (blocked capture loop), Pitfall 8 (process death supervisor), Pitfall 9 (hardcoded secrets), security section (redact tokens), pitfall-to-phase mapping
- `.planning/research/SUMMARY.md` — `[CITED: .planning/research/SUMMARY.md]` — Phase 1 rationale, empirical auth-validation flag, bounded-queue-as-locked-decision
- `.planning/research/FEATURES.md` — `[CITED: .planning/research/FEATURES.md]` — session/token handling as table-stakes, feature dependencies

### Tertiary (training knowledge — ASSUMED, flagged for empirical validation)
- ffmpeg `-headers` injection, `-f image2pipe -vcodec mjpeg` stdout framing, `CREATE_NO_WINDOW`, pipe backpressure — `[ASSUMED]` (web/Context7 unreachable this session; must be validated empirically in Phase 1)
- requests.Session cookie persistence, python-dotenv usage — `[ASSUMED]` (standard practice)

## Metadata

**Confidence breakdown:**
- Standard stack: **HIGH** — all versions + cp314 wheels verified via PyPI tooling this session; only the exact auth flow is unverified (A1)
- Architecture: **MEDIUM** — bounded-queue / supervisor patterns are CITED-high-confidence from project research; the privratnik.net auth specifics and ffmpeg pipe-framing are ASSUMED (A1/A2/A3) and need empirical validation
- Pitfalls: **MEDIUM** — token-expiry / blocked-loop / secrets pitfalls are HIGH-confidence domain knowledge (CITED); the environment-specific ffmpeg-not-installed pitfall is VERIFIED; Windows subprocess details are ASSUMED (A5)

**Research date:** 2026-09-09
**Valid until:** 2026-10-09 (30 days — stack is stable-pinned; the privratnik.net auth contract may drift faster, so re-validate the auth probe if >30 days pass)
