# Walking Skeleton — Smart Schlagbaum

**Phase:** 1
**Generated:** 2026-09-10

## Capability Proven End-to-End

> The system loads non-secret config from `config.json` and secrets from a gitignored `.env`, then pushes camera-tagged frames into a per-camera bounded drop-oldest queue — the decoupling contract every later phase consumes.

## Architectural Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Language / runtime | Python 3.14 (system 3.14.6) in a venv | All Phase 1 deps publish cp314 Windows wheels (verified); no 3.13 downgrade needed |
| Stream decode | ffmpeg subprocess (`-f image2pipe -vcodec mjpeg -`) | Locked D-04: header injection (`Referer`/`Range`/cookie) + reliable reconnect for the proxy stream |
| Frame decode | `cv2.imdecode` (OpenCV headless) | D-05: JPEG→numpy; `opencv-python-headless==4.14.0.94` avoids Qt/GUI conflicts |
| Auth | `requests.Session` + re-invocable `get_session()` | D-01: persists PHPSESSID cookie; re-login on reconnect (Pitfall 1) |
| Frame buffering | stdlib `queue.Queue(maxsize=15)` drop-oldest, per camera | D-09/D-10: capture never blocks on slow downstream; frames tagged `camera_id` at enqueue |
| Reconnect | Supervisor thread + exponential backoff (1s→2s→…→60s) | D-03: detect "no valid frame for N sec" → re-auth → backoff → respawn |
| Config / secrets | `config.json` (non-secrets) + `.env` (secrets, gitignored) | D-02/D-06/D-07: token appended at request time, never stored in URL |
| Deployment target | Local Windows PC, venv, Windows Task Scheduler (Phase 6) | Native, survives reboots; `subprocess.CREATE_NO_WINDOW` for background service |
| Directory layout | `src/capture/*`, `src/config.py`, `src/main.py`, `tests/`, `scripts/` | RESEARCH.md recommended structure; feature-folder under `src/` |

## Stack Touched in Phase 1

- [x] Project scaffold (venv, `requirements.txt` pinned `==`, `pytest.ini`, `.gitignore`)
- [x] Config + secrets loading (`config.json` + `.env` via `os.getenv()`)
- [x] Frame buffer — bounded drop-oldest queue with `camera_id` tagging (the data-layer contract)
- [x] Auth session manager + ffmpeg subprocess capture (the capture path)
- [x] Supervisor reconnect loop + `main.py` entrypoint wiring 2 cameras
- [ ] Deployment — documented local full-stack run command (`python -m src.main`); Task Scheduler deferred to Phase 6

## Out of Scope (Deferred to Later Slices)

- Barrier state detection (Phase 2)
- Event store / photo store / FSM (Phase 3)
- Vehicle detection (Phase 4)
- Plate recognition / OCR (Phase 5)
- Boot-start / health check / file logging (Phase 6)
- Any live-stream validation beyond the manual auth probe (needs user creds)

## Subsequent Slice Plan

Each later phase adds one vertical slice on top of this skeleton without altering its architectural decisions:

- Phase 2: Barrier State Detector — consumes `FrameBuffer.pop()` to report open/closed/partial
- Phase 3: Event Store + Photo Store + Event Coordinator (FSM) — persists one event per opening
- Phase 4: Vehicle Detector — gates OCR calls, selects best frame
- Phase 5: Plate Recognizer — external ALPR + Tesseract fallback
- Phase 6: Deployment & Startup Hardening — boot-start, restart, health check, file logging
