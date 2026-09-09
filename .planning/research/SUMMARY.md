# Project Research Summary

**Project:** Smart Schlagbaum (barrier-gate video analytics / license plate recognition)
**Domain:** Local video analytics / ANPR-LPR from IP camera streams at a barrier gate
**Researched:** 2026-09-09
**Confidence:** HIGH (stack, core architecture, core pitfalls) / MEDIUM (features, OCR service specifics) / LOW (OCR pricing/maintenance)

## Executive Summary

This is a **local, observation-only audit system** that watches two IP camera streams at a barrier gate, detects when the gate opens, reads the license plate of the vehicle, and persists an event with a photo as evidence. The core value is *reliable event capture with evidence* — every opening must be recorded with a readable plate and a photo. Experts build this as a **linear streaming pipeline** (capture → analyze → recognize → persist) with a bounded queue decoupling fast frame capture from slow analysis/OCR, a finite state machine to emit exactly one event per gate transition, and a pluggable OCR backend (cloud API with a local Tesseract fallback). The recommended stack is Python 3.12+ with OpenCV headless, Ultralytics YOLO11n for vehicle detection, SQLite for event metadata, and photos stored as files on disk (paths in DB, not BLOBs).

The recommended approach is a **dependency-ordered build that produces a working slice at every step**, tackling the riskiest unknown first. The single highest-risk item is the `privratnik.net` proxy stream authentication (token + `PHPSESSID` cookie + `Referer` header) — it must be validated with a reconnect/re-auth loop before anything is built on top of it. The MVP core is "record every opening with a photo and a plate": frame capture, barrier detection, vehicle detection, external OCR, SQLite event logging, photo storage, dedup, and automatic startup. Deliberately out of scope are gate control (safety-critical), real-time alerting, and any cloud/multi-site sync.

The key risks are all reliability and trust issues, not feature gaps: (1) token/session expiry silently killing the stream, (2) blocking the capture loop on slow OCR and dropping the very event you want, (3) event spam flooding the DB, (4) barrier-detection false positives, and (5) LPR misreads being stored as truth. Each is mitigated by a specific architectural pattern (supervisor reconnect loop, bounded queue + OCR on a separate thread, FSM + cooldown, ROI + hysteresis, confidence flagging + best-frame selection). The biggest open question is the external OCR provider — pricing/accuracy/maintenance could not be live-verified this session and must be re-checked before committing in the STACK phase.

## Key Findings

### Recommended Stack

A single-language Python stack is the right call for a CV/ML pipeline — OpenCV, Ultralytics, and pytesseract all have first-class Python bindings, and one language for capture, detection, OCR, and storage keeps the MVP simple. Pin exact versions for reproducibility on the user's Windows PC. Use `opencv-python-headless` (not the GUI variant) to avoid Qt/DLL conflicts, and install it *before* Ultralytics so Ultralytics doesn't pull the non-headless variant.

**Core technologies:**
- **Python 3.12+**: application language — de-facto standard for CV/ML; single language for the whole pipeline
- **OpenCV `opencv-python-headless==4.14.0.94`**: frame capture/decode/preprocessing — `VideoCapture` reads the MP4 proxy stream directly; pin to 4.14.x, avoid OpenCV 5.0 (breaking changes)
- **Ultralytics YOLO11n (8.4.144)**: vehicle detection — COCO-pretrained with `car`/`truck`/`bus`; nano runs on CPU at usable speed
- **SQLite (stdlib `sqlite3`)**: local event + photo-metadata store — zero-install, ACID, single-file; photos as files on disk with paths in DB (not BLOBs)
- **`requests` (2.34.2)**: HTTP proxy auth + token handling, external OCR calls — handles cookies, headers, query params
- **`pytesseract` (0.3.13)**: offline license-plate OCR fallback — works fully offline; needs the Tesseract binary installed separately
- **`python-dotenv`**: load tokens/credentials from `.env` — keep secrets out of source

**Critical version/compat notes:** OpenCV 4.14 works with numpy 2.x; Ultralytics pulls its own torch (fine on CPU Windows); install `opencv-python-headless` *before* `ultralytics` to avoid the GUI-variant conflict. Tesseract binary must be on PATH or `tesseract_cmd` set. Prefer Windows Task Scheduler (native) over a Python scheduler for boot-start.

### Expected Features

The product is an **audit tool**, not a gate controller. Table stakes are the "reliably capture every opening with a photo and a plate" core; differentiators are what make the audit *trustworthy* (confidence flagging, manual correction) and *low-maintenance* (retention, offline fallback).

**Must have (table stakes):**
- Frame capture from two cameras (privratnik.net session/token handling) — nothing works without this
- Barrier open/close detection — the primary trigger and core value
- Vehicle detection — gates the OCR call and provides the second trigger
- Plate recognition via external OCR — the "whose car" answer
- Event logging to SQLite (type, timestamp, camera, plate, photo path) — the audit record
- Photo storage (full frame + plate crop) — the evidence
- Dedup/coalescing — keeps the log usable (prevents event spam)
- Automatic startup (Task Scheduler) — it must "just run"

**Should have (competitive):**
- Plate confidence score + low-confidence flagging — separates "a log" from "an audit trail"
- Offline OCR fallback (Tesseract) — keeps the system working when the API/network is down
- Manual correction of misreads — lets the user fix a wrong plate
- Retention/cleanup policy — prevents unbounded disk growth
- CSV/JSON export — audit data needs to leave the system

**Defer (v2+):**
- Notification (SMS/Telegram/email) — deferred by PROJECT.md; only after capture is proven reliable
- Local web UI / dashboard — only if the user needs to browse beyond file export
- Multi-site / cloud sync — only if the tool proves worth scaling

**Anti-features (explicitly out of scope):** barrier control (safety-critical liability), continuous full-frame recording, real-time sub-second latency, custom plate-model training.

### Architecture Approach

A **linear streaming pipeline** with a feedback loop for state detection: capture → decode → analyze → recognize → persist, connected by bounded queues so a slow stage (OCR) never blocks a fast stage (frame capture). The canonical shape is a producer–consumer pipeline with a bounded drop-oldest queue, a finite state machine (`CLOSED → OPENING → OPEN → CLOSING → CLOSED`) that emits exactly one event per meaningful transition, and a `PlateRecognizer` strategy interface (cloud client + Tesseract fallback). The project structure mirrors the pipeline (`capture/`, `analysis/`, `events/`, `storage/`), each layer depending only on the one before it, so every stage is independently testable (feed a recorded video into analysis without a live camera).

**Major components:**
1. **Stream Client + Frame Buffer** — connect/auth/decode MP4, emit frames into a bounded drop-oldest queue; capture never blocks on analysis
2. **Barrier State Detector** — ROI pixel-diff / background subtraction (MOG2) with hysteresis to detect open/close transitions
3. **Vehicle Detector** — YOLO or motion blob; gates the OCR call (only OCR when a vehicle is present)
4. **Plate Recognizer** — strategy interface: cloud OCR (Plate Recognizer / Google Vision) with Tesseract fallback; returns plate + confidence
5. **Event Coordinator (FSM)** — combines barrier state + vehicle presence to decide when to emit a persisted event
6. **Event Store + Photo Store** — SQLite event rows referencing photo files on disk (never BLOBs)

### Critical Pitfalls

1. **Token/session expiry silently kills the stream** — treat the stream as ephemeral/reconnectable; wrap capture in a supervisor loop that detects "no valid frame for N seconds" and re-authenticates + reconnects. Highest-risk unknown; validate in Phase 1.
2. **Blocking the capture loop on slow OCR drops the event you want** — decouple capture from analysis with a bounded queue; run OCR on a separate thread so frame analysis continues while OCR is in flight.
3. **Event spam (saving on every "barrier is open" frame)** — use the FSM to emit exactly one event per open/close transition, plus a per-camera cooldown; apply dedup *before* the DB write.
4. **Barrier-detection false positives (shadows, cars, partial states)** — use a tight ROI around the barrier arm, background subtraction, persistence/hysteresis, and calibrate against recorded real footage.
5. **LPR misreads stored as truth** — capture the best frame (not the first), store and use the confidence score (flag < 0.7), multi-frame voting, normalize plate format, and provide manual correction.
6. **External OCR rate-limit/cost blowup** — gate OCR on vehicle detection + finalized event state (one call per event, not per frame); track daily call count; degrade to Tesseract fallback rather than failing.
7. **Unbounded DB/photo growth fills the disk** — photos as files not BLOBs; add a retention/cleanup policy from the start.
8. **Process dies and nothing restarts it** — wrap in a supervisor loop that catches top-level exceptions; run as a service / Task Scheduler task with restart-on-failure; handle sleep/wake; log to a file.
9. **Hardcoded tokens/URLs/OCR keys** — centralize in config/env; treat as secrets; never commit; make token refresh a runtime operation.

## Implications for Roadmap

Based on research, suggested phase structure. This follows the ARCHITECTURE build order (each step produces a working, testable slice; riskiest unknown first) and the PITFALLS phase mapping.

### Phase 1: Stream Client + Frame Buffer + Config/Secrets + Supervisor
**Rationale:** Everything depends on getting frames. The `privratnik.net` auth/token flow is the highest-risk unknown in the whole project — validate the reconnect/re-auth loop before building anything on top of it. Config/secrets structure must exist before the first token is used.
**Delivers:** Two-camera frame capture with auth, bounded drop-oldest queue, supervisor loop that reconnects on stale stream, config/env for tokens.
**Addresses:** Frame capture (P1), automatic startup foundation.
**Avoids:** Pitfall 1 (token expiry), Pitfall 2 (blocked capture loop), Pitfall 8 (process death), Pitfall 9 (hardcoded secrets).

### Phase 2: Barrier State Detector
**Rationale:** Simplest analysis; proves the pipeline works end-to-end with a real event. Must be validated against real footage before the event pipeline is built on top of it.
**Delivers:** Reliable open/close detection with ROI + hysteresis, no false triggers on cars passing with the gate closed.
**Addresses:** Barrier open/close detection (P1).
**Avoids:** Pitfall 4 (barrier false positives).

### Phase 3: Event Store + Photo Store + Event Coordinator (FSM)
**Rationale:** This is the MVP core value — persist the barrier-open event with a photo. The FSM must exist before event persistence is wired up, or the DB fills with noise from day one. The file-not-BLOB decision is architectural and belongs here.
**Delivers:** SQLite event logging, photo files on disk, FSM emitting exactly one event per transition, dedup/cooldown, retention mechanism.
**Addresses:** Event logging (P1), photo storage (P1), dedup/coalescing (P1).
**Avoids:** Pitfall 3 (event spam), Pitfall 7 (disk growth).

### Phase 4: Vehicle Detector
**Rationale:** Refines *when* to capture (only when a car is present) and provides the second event trigger. Independent of barrier detection — a gate opening with no vehicle is still a valid audit event.
**Delivers:** YOLO11n vehicle detection gating the OCR call.
**Addresses:** Vehicle detection (P1).
**Avoids:** Pitfall 6 (OCR cost blowup) — gating foundation.

### Phase 5: Plate Recognizer (cloud + Tesseract fallback)
**Rationale:** Add OCR last — it's the most external-dependency-heavy and can be layered on once events already persist. Confidence flagging and best-frame selection must be part of the OCR integration, not a later bolt-on.
**Delivers:** External OCR with confidence score, best-frame selection, Tesseract offline fallback, rate-limit/cost gating.
**Addresses:** Plate recognition (P1), offline OCR fallback (P2), confidence flagging (P2).
**Avoids:** Pitfall 5 (LPR misreads), Pitfall 6 (OCR cost/rate blowup).

### Phase 6: Deployment / Startup Hardening
**Rationale:** The supervisor must be in place from the start (Phase 1), but the service/restart configuration and sleep/wake handling are a dedicated deployment concern.
**Delivers:** Windows Task Scheduler / service config with restart-on-failure, health check, file logging.
**Addresses:** Automatic startup (P1).
**Avoids:** Pitfall 8 (process death).

### Phase 7 (v1.x): Trust & Maintenance Features
**Rationale:** Add once core capture is proven reliable. These are the differentiators that make the audit trustworthy and low-maintenance.
**Delivers:** Manual correction of misreads, retention/cleanup policy, CSV/JSON export.
**Addresses:** Manual correction (P2), retention (P2), export (P2).

### Phase Ordering Rationale

- **Riskiest unknown first:** the proxy auth/reconnect flow (Phase 1) is validated before anything depends on it — a silent stream death would invalidate every downstream feature.
- **Dependency-driven:** each phase produces a working slice; OCR (Phase 5) is deferred because it's the most failure-prone external dependency and doesn't block the core "record every opening" value.
- **FSM before persistence:** the state machine (Phase 3) must exist before event writes, or the DB floods with duplicate events from day one.
- **Gating before OCR:** vehicle detection (Phase 4) precedes OCR (Phase 5) so the paid API is only called on finalized events — this is the single most important pipeline ordering for cost and accuracy.
- **Architectural decisions locked early:** file-not-BLOB photo storage and the bounded-queue pattern are Phase 1/3 decisions that are painful to retrofit.

### Research Flags

Phases likely needing deeper research during planning:
- **Phase 1:** The `privratnik.net` proxy auth/reconnect behavior (token rotation, session expiry, `Referer`/`Range`/`PHPSESSID` requirements) is unverified — needs empirical validation against the real stream, not just docs.
- **Phase 5:** The external OCR provider (Plate Recognizer vs OpenALPR Cloud vs Google Vision) — pricing, RU-plate accuracy, rate limits, and maintenance status could not be live-verified this session. **Re-verify before committing to a specific provider in the STACK phase.**

Phases with standard patterns (skip research-phase):
- **Phase 2 (Barrier Detector):** well-documented OpenCV background-subtraction / ROI-diff patterns.
- **Phase 3 (Event Store + Photo Store):** standard SQLite + filesystem persistence; established patterns.
- **Phase 4 (Vehicle Detector):** Ultralytics YOLO11n is well-documented with a one-line inference API.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | Versions verified via PyPI (`pip index versions`); OpenCV/Ultralytics/pytesseract APIs from Context7. OpenCV 5.0 and YOLO11 are current. |
| Features | MEDIUM | Feature *categorization* is high-confidence domain knowledge, but live vendor docs (Plate Recognizer pricing/accuracy, OpenALPR maintenance) were unreachable this session — treat vendor figures as directional. |
| Architecture | HIGH | Core pipeline patterns (bounded queue, FSM, strategy fallback) are established ANPR/ALPR domain knowledge; OCR service specifics are MEDIUM. |
| Pitfalls | HIGH | Pitfall categories and prevention strategies are high-confidence domain knowledge; OCR provider specifics are MEDIUM/LOW. |

**Overall confidence:** HIGH for the core stack/architecture/pitfalls; MEDIUM for feature specifics and OCR provider choice.

### Gaps to Address

- **External OCR provider selection:** pricing, RU-plate accuracy, rate limits, and maintenance status unverified. Handle: re-verify in the STACK phase before committing; design the `PlateRecognizer` interface so the provider is swappable.
- **privratnik.net auth/reconnect behavior:** token rotation and session-expiry semantics unverified. Handle: validate empirically in Phase 1 with a reconnect/re-auth loop before building downstream.
- **Barrier-detection thresholds:** cannot be tuned without real footage. Handle: calibrate in Phase 2 against recorded footage of both a real opening and a car passing with the gate closed.
- **Tesseract RU-plate accuracy:** known to be poor on angled/blurred plates. Handle: treat as degraded fallback only, never the primary; rely on the external API for accuracy.

## Sources

### Primary (HIGH confidence)
- Context7 `/opencv/opencv-python` — VideoCapture API, version access, resource release
- Context7 `/websites/ultralytics` — YOLO11 model sizes, CPU inference, COCO classes
- Context7 `/madmaze/pytesseract` — image_to_string, PSM/OEM config, Windows tesseract_cmd
- Context7 `/parkpow/deep-license-plate-recognition` — ALPR pipeline stages, stream service, camera status monitoring
- PyPI (via `pip index versions`) — verified current versions: opencv-python 5.0.0.93 / 4.14.0.94, ultralytics 8.4.144, pytesseract 0.3.13, pillow 12.3.0, requests 2.34.2, numpy 2.5.3
- Project context `C:/dev/schlagbaum/.planning/PROJECT.md` — privratnik.net proxy, token auth, MP4 preview streams, SQLite constraint, scope

### Secondary (MEDIUM confidence)
- ANPR/ALPR pipeline architecture and failure modes (capture → plate localization → OCR → database) — Wikipedia "Automatic number-plate recognition"
- Commercial LPR systems (Genetec AutoVu, Milestone XProtect, Nedap ANPR, Axis) — event-triggered capture, confidence scoring, retention
- OpenCV/ffmpeg stream capture — reconnect patterns, bounded-queue producer/consumer, frame-drop behavior
- Windows Task Scheduler / service reliability for long-running Python processes
- SQLite best practices — file-not-BLOB storage, retention, WAL mode

### Tertiary (LOW confidence)
- Plate Recognizer / OpenALPR Cloud / Google Cloud Vision — per-call pricing, RU-plate accuracy, rate limits, maintenance status (unverified this session; needs validation)

---
*Research completed: 2026-09-09*
*Ready for roadmap: yes*
