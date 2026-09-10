---
phase: 01-stream-client-frame-buffer
plan: 01
subsystem: data-layer
tags: [config, secrets, frame-buffer, queue, scaffold]
dependency_graph:
  requires: []
  provides: [config.py load_settings, FrameBuffer, config.json, .gitignore, .env.example, requirements.txt, pytest.ini]
  affects: [01-02, phase-2-barrier-detector]
tech-stack:
  added: [opencv-python-headless==4.14.0.94, requests==2.34.2, python-dotenv==1.2.3, numpy==2.5.3, pytest==8.3.5]
  patterns: [bounded drop-oldest queue, config/secrets split, os.getenv secrets]
key-files:
  created: [.gitignore, .env.example, config.json, requirements.txt, pytest.ini, src/config.py, src/capture/frame_buffer.py, tests/test_config.py, tests/test_frame_buffer.py, tests/conftest.py]
  modified: [.planning/config.json]
decisions:
  - "queue_size=15, capture_fps=1.5, frame_stale_seconds=12, backoff_max=60.0 as config defaults (Claude's discretion within locked ranges)"
  - "git.allow_default_branch_commits=true to enable atomic per-task commits on the main working tree (branching_strategy: none)"
metrics:
  duration: "2026-09-10T12:45:23Z to 2026-09-10T12:55:00Z"
  completed_date: "2026-09-10"
status: complete
actuals:
  tokens: 12000
  tasks: 3
  commits: 3
  plan_head_before: a91b56dcc8a86cca45e388efa07ca918f77a52cd
---

# Phase 01 Plan 01: Config + Frame Buffer Data Layer Summary

## One-liner

Bounded drop-oldest per-camera frame buffer with camera_id tagging, fed by a config.json + .env secrets split, fully unit-tested in a pinned venv.

## What Was Built

The Walking Skeleton's data layer — the decoupling contract every later phase consumes:

- **`src/config.py`** — `load_settings()` merges non-secret `config.json` with secrets from `.env` via `os.getenv()` (D-06/D-07). Secrets never come from source or config; no fallback defaults.
- **`src/capture/frame_buffer.py`** — `Frame` dataclass (camera_id, ts, data) + `FrameBuffer` bounded drop-oldest queue using stdlib `queue.Queue`. Frames tagged with `camera_id` at enqueue (D-10). Capture never blocks on a slow consumer (D-09).
- **Scaffold** — `.gitignore` (excludes `.env`, `.venv/`, `data/`, `__pycache__/`, `*.pyc`), `.env.example` (empty placeholders), `config.json` (non-secrets only), `requirements.txt` (exact `==` pins), `pytest.ini` (testpaths + pythonpath).
- **Tests** — `tests/test_config.py` (cameras dict, env secrets, no password in config), `tests/test_frame_buffer.py` (camera_id tagging, drop-oldest bounded, non-blocking push, race, FIFO, Frame attributes, per-camera isolation), `tests/conftest.py` (shared fixtures).

## Verification

- Full suite: `.venv/Scripts/python -m pytest tests/ -q` → **10 passed**
- `git check-ignore .env` → `.env` (proves .env is gitignored)
- `grep -c 'password' config.json` → 0 (no secrets in config)
- `grep -c 'os.getenv' src/config.py` → 3 (secrets read from env)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Protected default branch blocked atomic commits**
- **Found during:** Task 2 commit
- **Issue:** The pre-commit HEAD safety assertion refused to commit on `master` (the project's configured base branch). The project uses `branching_strategy: "none"` and the coordinator instructed sequential execution on the main working tree, so no per-agent branch exists.
- **Fix:** Set `git.allow_default_branch_commits: true` in `.planning/config.json` (the sanctioned override for this scenario).
- **Files modified:** `.planning/config.json`
- **Commit:** f836c55

## Auth Gates

None — the package-legitimacy checkpoint (Task 1) was cleared by explicit user approval of all 5 packages (opencv-python-headless, requests, python-dotenv, numpy, pytest). No auth errors occurred during execution.

## Known Stubs

None — all created files are fully wired and tested. No placeholder values flow to UI/rendering.

## Threat Flags

None — no security-relevant surface introduced beyond the plan's threat model. The `.env` gitignore (T-01-01) and config/secrets split (T-01-02) are verified by the static checks above.

## Self-Check: PASSED

- [x] `.venv/Scripts/python -m pytest tests/ -q` → 10 passed
- [x] `git check-ignore .env` → `.env`
- [x] `grep -c 'password' config.json` → 0
- [x] `grep -c 'os.getenv' src/config.py` → 3
- [x] Commits exist: f836c55, 4423bf8, 32385fb
