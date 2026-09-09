# Pitfalls Research

**Domain:** Barrier-gate video analytics / license plate recognition (ANPR/LPR) from IP camera streams
**Researched:** 2026-09-09
**Confidence:** HIGH (core capture/detection/persistence pitfalls) / MEDIUM (OCR service specifics) / LOW (OCR pricing/maintenance)

> **Confidence note:** Live web/Context7 sources were unreachable in this environment (network blocked, search budget exhausted). This file is synthesized from established domain knowledge of the ANPR/access-control ecosystem (Genetec AutoVu, Plate Recognizer, OpenALPR, Milestone, parking/barrier LPR systems) plus the specific constraints in `PROJECT.md`. The *pitfall categories* and *prevention strategies* are high-confidence domain knowledge; vendor-specific OCR accuracy/pricing figures are directional, not verified. Re-verify OCR provider specifics in the STACK phase.

## Critical Pitfalls

### Pitfall 1: Token / session expiry silently kills the camera stream

**What goes wrong:**
The `privratnik.net` stream requires a token in the query param, a `PHPSESSID` cookie, and a `Referer` header. When the token rotates or the session expires, the stream client gets a 401/403 or an empty/error stream. If the code treats "no frame" as "no event," the system goes dark silently — no openings recorded, no error surfaced. The user only notices days later when the audit log is empty.

**Why it happens:**
Tokens are short-lived by design (they rotate; the user resets them from DevTools). The stream client opens a connection once at startup and never re-authenticates. A single long-lived `cv2.VideoCapture(url)` or ffmpeg subprocess holds a stale token. Because the failure is a *stream* failure (not a crash), there's no exception — just a frozen/empty frame source.

**How to avoid:**
- Treat the stream connection as **ephemeral and reconnectable**, not a persistent handle. Wrap capture in a supervisor loop that detects "no valid frame for N seconds" and tears down + re-authenticates + reconnects.
- Centralize auth (token, cookie, Referer) in `config.py`/env, and make the auth step a **separate, callable function** (`get_session()`) that can be re-invoked on reconnect — not a one-time startup side effect.
- On reconnect, re-fetch the token/session rather than reusing the cached one. If the token is user-supplied and rotated, surface a clear "token expired — please refresh" state instead of silently retrying forever.
- Add a **heartbeat/health check**: if no frame arrives within a threshold (e.g., 10–15s), log a warning and reconnect. This is the single most important reliability guard.

**Warning signs:**
- Stream connects at startup but produces no frames after some hours/days.
- Log shows a single successful connect then silence (no errors, no frames).
- Events stop being recorded but the process is still running (no crash).

**Phase to address:**
Phase 1 (Stream Client + auth). This is the highest-risk unknown in the whole project — validate the reconnect/re-auth loop before building anything on top of it.

---

### Pitfall 2: Blocking the capture loop on slow analysis (OCR) drops the very event you want

**What goes wrong:**
The camera produces frames at its FPS. If the frame loop calls OCR synchronously (OCR takes 0.5–2s per call), the capture thread stalls. The camera buffer overflows, frames are dropped, and the frames containing the barrier opening / the plate are exactly the ones lost. Missed events = the core value is broken.

**Why it happens:**
It's the natural first implementation: "read frame → detect → OCR → save → read next frame." Developers don't realize OCR is orders of magnitude slower than frame capture until the pipeline is live and events are missing.

**How to avoid:**
- Decouple capture from analysis with a **bounded queue** (`queue.Queue(maxsize=N)`, drop-oldest). Capture always keeps up with the camera; analysis consumes at its own pace.
- Run OCR on a **separate worker thread/queue** so frame analysis (barrier + vehicle detection) continues while OCR is in flight. Only event *finalization* waits on OCR.
- Detect at a lower FPS than capture (e.g., analyze 2–5 FPS, capture full FPS) to bound CPU.

**Warning signs:**
- CPU pegged at 100% on the analysis thread.
- Frame timestamps show gaps (frames skipped) during OCR calls.
- Events recorded but photos are blurry/mid-transition (you caught the wrong frame).

**Phase to address:**
Phase 1 (Frame Buffer) and Phase 5 (Plate Recognizer). The bounded-queue pattern must be in place from the start; the OCR-on-separate-thread rule applies when OCR is added.

---

### Pitfall 3: Event spam — saving an event on every "barrier is open" frame

**What goes wrong:**
A naive `if barrier_open: save_event()` in the frame loop fires dozens of times per single opening. The DB floods with near-identical events, the audit log becomes unusable, and photo storage grows unboundedly. The user can't find the real events in the noise.

**Why it happens:**
The barrier stays "open" for seconds (or minutes), and the detector reports `barrier_open=True` on every frame in that window. Without a state machine or cooldown, each frame is a new event.

**How to avoid:**
- Model the barrier as a **finite state machine** (`CLOSED → OPENING → OPEN → CLOSING → CLOSED`) and emit exactly one event per meaningful transition (e.g., `OPENING` with a vehicle present). See ARCHITECTURE.md Pattern 2.
- Add a **cooldown window** per camera (ignore re-triggers within N seconds) as a second line of defense.
- Apply dedup *before* the DB write, in the event pipeline — not as a post-hoc cleanup.

**Warning signs:**
- DB shows bursts of identical events with timestamps seconds apart.
- Photo directory grows by dozens of files per gate opening.
- Audit queries return hundreds of rows for a single event.

**Phase to address:**
Phase 3 (Event Coordinator / FSM). The state machine must exist before event persistence is wired up, or the DB fills with noise from day one.

---

### Pitfall 4: Barrier state detection false positives (shadows, cars, partial states)

**What goes wrong:**
The barrier detector uses frame-diff / ROI pixel thresholding. A passing car, a shadow, a person, or a camera exposure change can look like the barrier moving. Result: false "opening" events (a car drove past but the gate never opened) or missed events (the gate opened but the detector didn't fire because the ROI was occluded). The audit trail becomes untrustworthy.

**Why it happens:**
Naive motion detection in a fixed ROI doesn't distinguish "barrier arm moved" from "anything changed in the ROI." Lighting changes (day/night, headlights) and occlusion by the vehicle itself are the classic failure modes. The barrier arm is thin and low-contrast against the background.

**How to avoid:**
- Use a **dedicated ROI** tightly around the barrier arm, not the whole frame.
- Use **background subtraction (MOG2)** or a **persistence/state filter** (require the "open" state to persist for N consecutive frames before declaring a transition) to reject single-frame flicker.
- **Hysteresis:** require a clear open signal to transition closed→open and a clear closed signal to transition open→closed; don't flip on noise.
- Calibrate thresholds against **recorded real footage** of both a real opening and a car passing with the gate closed — tune to reject the false case.
- Tag events with the detector confidence so low-confidence transitions can be reviewed.

**Warning signs:**
- Events recorded when the gate clearly didn't open (car passed, gate stayed closed).
- Events missing when the gate did open (detector didn't fire).
- Detector flips open/closed rapidly on consecutive frames (noise, no hysteresis).

**Phase to address:**
Phase 2 (Barrier State Detector). Validate against real footage before building the event pipeline on top of it.

---

### Pitfall 5: LPR accuracy collapse on blur, angle, and lighting — and trusting a single bad read

**What goes wrong:**
The external OCR returns a wrong plate (misread digit, wrong region format) and the system records it as truth. A moving car at an angle, at night, or with a dirty/obscured plate produces a confident-but-wrong read. The audit trail contains plausible-looking wrong plates that the user trusts.

**Why it happens:**
LPR accuracy depends heavily on plate size in frame, angle, motion blur, and lighting. A single frame captured mid-motion or at a bad angle yields a low-quality plate image. External APIs return a *confidence score*, but if the code ignores it and stores the raw string, wrong reads look identical to right ones.

**How to avoid:**
- **Capture the best frame, not the first frame:** when a vehicle is detected, grab the frame where the plate is largest/clearest (e.g., the frame just before the vehicle exits the ROI), not the trigger frame.
- **Store and use the confidence score.** Flag events below a threshold (e.g., < 0.7) as "low confidence — needs review." This is what separates a log from an audit trail.
- **Multi-frame voting:** if the vehicle is in frame for multiple frames, OCR 2–3 frames and take the majority/consensus plate. This dramatically cuts single-frame misreads.
- **Normalize plate format** (strip spaces, uppercase, validate against the expected region format) before storing — catches obvious OCR garbage.
- Provide **manual correction** of misreads (edit plate on event) so the audit stays accurate.

**Warning signs:**
- Plates stored with obviously wrong characters (e.g., `O` vs `0`, `I` vs `1` confusion).
- Low-confidence reads stored without any flag.
- Night-time events have systematically worse accuracy than daytime.

**Phase to address:**
Phase 5 (Plate Recognizer). Confidence flagging and best-frame selection must be part of the OCR integration, not a later bolt-on.

---

### Pitfall 6: External OCR rate limits / cost blowup from un-gated API calls

**What goes wrong:**
The OCR API is called on every frame (or every detected vehicle, without dedup). At a busy gate, that's thousands of calls/day. The user hits the API's rate limit (calls fail, events lose their plate) or the per-call cost balloons (surprise bill). The system was designed to be cheap but isn't.

**Why it happens:**
The natural implementation calls OCR whenever a vehicle is detected, without gating on "is this a *new* event?" or "is the plate actually readable?" Every frame with a car triggers a paid call. Rate limits are hit during peak traffic exactly when you need the reads most.

**How to avoid:**
- **Gate OCR on vehicle detection + event state:** only OCR when a vehicle is present *and* a new event is being finalized — not on every frame.
- **Dedup/cooldown:** one OCR call per event, not per frame. The FSM (Pitfall 3) naturally provides this.
- **Best-frame selection (Pitfall 5)** also reduces calls: OCR the single best frame, not every frame.
- **Budget/rate-limit awareness:** track daily call count; if approaching the plan's limit, degrade to the offline Tesseract fallback or skip OCR (still record the event + photo, just no plate) rather than failing.
- **Cache/retry with backoff** on 429/5xx; don't hammer the API on failure.

**Warning signs:**
- API dashboard shows call counts far above expected events.
- 429/rate-limit errors in logs during peak hours.
- Monthly bill much higher than the event count would suggest.

**Phase to address:**
Phase 5 (Plate Recognizer). The gating logic (OCR only on finalized events) must be designed in, and the cost/rate model chosen in the STACK phase.

---

### Pitfall 7: Unbounded local database and photo growth silently fills the disk

**What goes wrong:**
Every event writes a SQLite row and saves one or more JPEGs to disk. Over months, the DB and photo directory grow without limit. The disk fills, the system starts failing (SQLite can't write, photos fail to save), and the user discovers it only when the service breaks. If photos are stored as BLOBs in SQLite, the DB itself bloats and backups become slow.

**Why it happens:**
Local systems have no built-in retention. Developers build the "save event + photo" path and never add a cleanup policy, because it's invisible until the disk is full. Event spam (Pitfall 3) accelerates the growth.

**How to avoid:**
- **Store photos as files on disk, not BLOBs in SQLite.** The DB holds the path; the file lives in `data/photos/`. Keeps the DB small and fast (see ARCHITECTURE.md Anti-Pattern 3).
- Add a **retention/cleanup policy** from the start: configurable retention (days or max events) that auto-purges old events and their photo files. Even if the default is generous, the mechanism must exist.
- **Dedup (Pitfall 3)** prevents the biggest growth driver (event spam).
- Monitor disk usage and log a warning before it's critical.

**Warning signs:**
- `data/photos/` grows by many files per day.
- SQLite file size grows steadily even after dedup.
- Disk usage on the local PC climbs toward 100%.

**Phase to address:**
Phase 3 (Event Store + Photo Store). The file-not-BLOB decision is architectural (Phase 1/3); the retention policy can be a Phase 3 or v1.x addition but the *mechanism* should be designed in early.

---

### Pitfall 8: Windows Task Scheduler / service reliability — the process dies and nothing restarts it

**What goes wrong:**
The system is launched via Windows Task Scheduler. The process crashes (unhandled exception, memory leak, camera reconnect loop), or the PC sleeps/wakes and the scheduled task doesn't resume, or the task runs once and exits instead of staying alive. The user assumes it's running; it isn't. Events stop being recorded with no notification.

**Why it happens:**
Task Scheduler is designed for "run a task and exit," not "keep a long-running service alive." A Python script that crashes on an unhandled exception just dies. Sleep/wake can leave the process in a broken state. Developers assume "scheduled to run" = "always running."

**How to avoid:**
- **Wrap the pipeline in a supervisor loop** that catches top-level exceptions, logs them, and restarts the capture/analysis threads — the process itself should never exit on a recoverable error.
- **Run as a Windows Service** (e.g., via `pywin32`/`nssm`) or a Task Scheduler task configured to **restart on failure** and **run at startup**, rather than a one-shot scheduled run.
- **Handle sleep/wake:** on resume, detect the stream is stale (Pitfall 1) and reconnect; don't assume the pre-sleep connection is valid.
- **Health check + watchdog:** a separate lightweight check (or a heartbeat file/DB row) that confirms the process is alive and producing frames; alert if not.
- **Log to a file** (not just console) so a crash is diagnosable after the fact.

**Warning signs:**
- Process not running after a reboot or sleep/wake.
- Task Scheduler shows "Last Run Result" as a failure code.
- No log output for long periods (process died silently).

**Phase to address:**
Phase 1 (supervisor loop) and a dedicated "deployment/startup" phase. The supervisor must be in place from the start; the service/restart configuration is part of the deployment phase.

---

### Pitfall 9: Hardcoding camera tokens / URLs / OCR keys in source

**What goes wrong:**
The `privratnik.net` token, camera URLs, and OCR API key are embedded in the code. Tokens rotate (the user resets them from DevTools), so updating means editing source. Worse, the OCR API key and tokens can be committed to git, leaking credentials.

**Why it happens:**
It's the fastest way to get a prototype running. The token is pasted into the URL string, the API key into the request. Nobody plans for rotation or secret management in an MVP.

**How to avoid:**
- Centralize all config (cameras, tokens, thresholds, OCR keys) in `config.py` / a config file / env vars — never in source.
- Treat tokens and API keys as **secrets**: load from env or a gitignored config file; never commit.
- Make token refresh a **runtime operation** (re-read config / re-fetch session) so rotation doesn't require a code change or restart.

**Warning signs:**
- Token or API key appears in a git diff or committed file.
- Updating a token requires editing and redeploying code.
- `.gitignore` doesn't exclude the config/credentials file.

**Phase to address:**
Phase 1 (config + auth). The config/secrets structure must be set up before the first token is used.

---

## Technical Debt Patterns

Shortcuts that seem reasonable but create long-term problems.

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| Single long-lived camera connection, no reconnect | Simple, one-time setup | Silent stream death on token expiry (Pitfall 1) | Never — reconnect loop is core reliability |
| Synchronous OCR in the frame loop | Simplest code | Drops the event frames you need (Pitfall 2) | Never — bounded queue is required |
| `if barrier_open: save_event()` | Trivial trigger | Event spam floods DB (Pitfall 3) | Never — FSM/cooldown required |
| Store photos as BLOBs in SQLite | One table, no file management | DB bloat, slow backups (Pitfall 7) | Never — file-on-disk is the standard |
| Ignore OCR confidence score | Store raw string | Wrong plates look like truth (Pitfall 5) | Only in throwaway demo, not audit |
| OCR on every vehicle frame | Guarantee a read | Rate-limit/cost blowup (Pitfall 6) | Never — gate on finalized events |
| No retention/cleanup policy | Nothing to build | Disk fills silently (Pitfall 7) | Only in a short-lived demo |
| Hardcoded tokens/keys | Fastest prototype | Rotation pain + secret leak (Pitfall 9) | Never — config/secrets from day one |
| Task Scheduler one-shot run | Easy setup | Process dies, nothing restarts (Pitfall 8) | Never — supervisor/service required |

## Integration Gotchas

Common mistakes when connecting to external services.

| Integration | Common Mistake | Correct Approach |
|-------------|----------------|------------------|
| privratnik.net stream | Open one connection at startup, never re-auth | Supervisor loop: detect stale stream, re-fetch session/token, reconnect (Pitfall 1) |
| privratnik.net stream | Forget `Referer` / `Range: bytes=0-` / `PHPSESSID` cookie | Send all required headers/cookies on every request; token in query param |
| External OCR API | Call on every frame / every vehicle | Gate on finalized event + best-frame selection (Pitfall 6) |
| External OCR API | No retry/backoff on 429/5xx | Retry with exponential backoff; degrade to Tesseract fallback on persistent failure |
| External OCR API | Ignore confidence score | Store confidence; flag low-confidence reads for review (Pitfall 5) |
| Tesseract (offline fallback) | Assume it matches cloud accuracy | It's a degraded mode — lower accuracy on angled/blurred plates; use only as fallback |
| Windows Task Scheduler | One-shot scheduled run | Run as service / restart-on-failure / supervisor loop (Pitfall 8) |

## Performance Traps

Patterns that work at small scale but fail as usage grows.

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| Synchronous OCR in frame loop | Frame gaps, missed events | Bounded queue + OCR on separate thread (Pitfall 2) | As soon as OCR is added (0.5–2s/call) |
| Heavy YOLO detection at full FPS | CPU pegged, frames dropped | Downscale frames / detect at 2–5 FPS | 1 camera at full FPS with a heavy model |
| Unbounded photo storage | Disk fills over months | Retention/cleanup policy (Pitfall 7) | Weeks–months depending on traffic |
| Event spam without dedup | DB floods, disk grows | FSM + cooldown (Pitfall 3) | First busy hour at the gate |
| OCR on every frame | Rate-limit/cost blowup | Gate on finalized events (Pitfall 6) | First busy period at the gate |
| Single-threaded analysis for 2 cameras | One camera starves the other | One analysis thread per camera, or round-robin | When the 2nd camera is added |

## Security Mistakes

Domain-specific security issues beyond general web security.

| Mistake | Risk | Prevention |
|---------|------|------------|
| Committing OCR API key / camera tokens to git | Credential leak; attacker uses your paid OCR quota or views cameras | Load from env/gitignored config; never commit (Pitfall 9) |
| Logging full URLs with tokens | Tokens leak into logs | Redact query-param tokens in log output |
| Storing photos with no access control | Anyone with PC access views all captured vehicles | Restrict `data/photos/` permissions; consider encryption if sensitive |
| Trusting OCR output as validated data | Wrong/malicious plate strings stored | Validate/normalize plate format before storing (Pitfall 5) |
| Exposing a local web UI without auth (future) | Unauthorized access to audit data | Add auth before any remote/network exposure |

## UX Pitfalls

Common user experience mistakes in this domain.

| Pitfall | User Impact | Better Approach |
|---------|-------------|-----------------|
| Silent stream failure | User thinks system works; no events recorded | Surface "stream down / token expired" state clearly (Pitfall 1) |
| No confidence flagging | User trusts wrong plates | Flag low-confidence reads for review (Pitfall 5) |
| No manual correction | Wrong plates stay wrong forever | Allow editing a plate on an event |
| No retention control | Disk fills, user must manually clean | Configurable retention policy (Pitfall 7) |
| No health/status visibility | User can't tell if it's running | Heartbeat/health check + log file (Pitfall 8) |
| Event spam | Audit log unusable | Dedup/coalescing (Pitfall 3) |

## "Looks Done But Isn't" Checklist

Things that appear complete but are missing critical pieces.

- [ ] **Frame capture:** Often missing reconnect/re-auth on token expiry — verify the stream recovers after a token rotation without a restart.
- [ ] **Barrier detection:** Often missing hysteresis/persistence — verify it doesn't false-trigger on a car passing with the gate closed.
- [ ] **Plate recognition:** Often missing confidence flagging — verify low-confidence reads are flagged, not stored as truth.
- [ ] **Event logging:** Often missing dedup/cooldown — verify one opening produces exactly one event, not dozens.
- [ ] **Photo storage:** Often missing retention/cleanup — verify old photos are purged per policy, not accumulated forever.
- [ ] **OCR integration:** Often missing rate-limit/cost gating — verify OCR is called once per event, not per frame.
- [ ] **Startup:** Often missing supervisor/restart — verify the process survives a crash and a sleep/wake cycle.
- [ ] **Config:** Often missing secrets handling — verify tokens/keys are not in source or git history.

## Recovery Strategies

When pitfalls occur despite prevention, how to recover.

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|----------------|
| Token/session expiry (Pitfall 1) | LOW | Re-fetch token/session, reconnect; log the gap so missed events are known |
| Blocked capture loop (Pitfall 2) | MEDIUM | Refactor to bounded queue + OCR thread; re-validate against recorded footage |
| Event spam (Pitfall 3) | MEDIUM | Add FSM/cooldown; dedupe existing DB rows; purge duplicate photos |
| Barrier false positives (Pitfall 4) | MEDIUM | Re-tune ROI/thresholds against real footage; add hysteresis |
| LPR misreads (Pitfall 5) | MEDIUM | Add confidence flagging + manual correction; re-OCR stored photos if possible |
| OCR cost/rate blowup (Pitfall 6) | MEDIUM | Add gating/dedup; switch to cheaper plan or Tesseract fallback |
| Disk full (Pitfall 7) | HIGH | Purge old photos/events; add retention policy; free disk |
| Process died (Pitfall 8) | MEDIUM | Add supervisor/service; restart; diagnose from log file |
| Secret leak (Pitfall 9) | HIGH | Rotate keys/tokens immediately; scrub git history; move to env/config |

## Pitfall-to-Phase Mapping

How roadmap phases should address these pitfalls.

| Pitfall | Prevention Phase | Verification |
|---------|------------------|--------------|
| Token/session expiry (Pitfall 1) | Phase 1 (Stream Client + auth) | Rotate the token mid-run; confirm the stream reconnects and resumes without restart |
| Blocked capture loop (Pitfall 2) | Phase 1 (Frame Buffer) + Phase 5 (OCR) | Feed a recorded video; confirm no frame gaps during slow OCR |
| Event spam (Pitfall 3) | Phase 3 (Event Coordinator / FSM) | One gate opening produces exactly one event |
| Barrier false positives (Pitfall 4) | Phase 2 (Barrier State Detector) | Real footage: car passes with gate closed → no event; gate opens → one event |
| LPR misreads (Pitfall 5) | Phase 5 (Plate Recognizer) | Night/angled/blurred plates are flagged low-confidence, not stored as truth |
| OCR cost/rate blowup (Pitfall 6) | Phase 5 (Plate Recognizer) | API call count ≈ event count, not frame count |
| Disk growth (Pitfall 7) | Phase 3 (Event Store + Photo Store) | Photos are files not BLOBs; retention policy purges old data |
| Process death (Pitfall 8) | Phase 1 (supervisor) + deployment phase | Kill the process; confirm it restarts; sleep/wake → stream recovers |
| Secret leak (Pitfall 9) | Phase 1 (config/secrets) | No tokens/keys in source or git history |

## Sources

- **Project context:** `C:/dev/schlagbaum/.planning/PROJECT.md` (constraints, camera/stream details, scope)
- **Domain knowledge (HIGH confidence, not live-verified this session):**
  - ANPR/ALPR pipeline architecture and failure modes (capture → plate localization → OCR → database) — Wikipedia "Automatic number-plate recognition"
  - Commercial LPR systems (Genetec AutoVu, Milestone, Nedap) — event-triggered capture, confidence scoring, retention
  - Cloud OCR APIs (Plate Recognizer, OpenALPR Cloud, Google Cloud Vision) — per-call pricing, rate limits, confidence scores
  - OpenCV/ffmpeg stream capture — reconnect patterns, bounded-queue producer/consumer, frame-drop behavior
  - Windows Task Scheduler / service reliability for long-running Python processes
  - SQLite best practices — file-not-BLOB storage, retention, WAL mode
- **Research gap:** Live vendor docs (Plate Recognizer pricing/accuracy, OpenALPR maintenance status, privratnik.net token behavior) could not be fetched this session. Re-verify before committing to a specific OCR provider in the STACK phase, and validate the privratnik.net auth/reconnect flow empirically in Phase 1.

---
*Pitfalls research for: barrier-gate video analytics / license plate recognition*
*Researched: 2026-09-09*
