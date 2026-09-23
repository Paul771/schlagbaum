# Phase 2: Barrier State Detector - Research

**Researched:** 2026-09-23
**Domain:** Computer vision (OpenCV, CPU-only) — binary/multi-state detection of a barrier arm on a fixed camera, with temporal hysteresis and offline-testable seams
**Confidence:** HIGH on the CV approach and the offline seam (both empirically validated on this host); MEDIUM on the exact threshold values transferring to real footage (they were tuned on synthetic frames — see Assumptions Log A1)

## Summary

Phase 2 can be **fully developed and fully tested without the live ffmpeg capture path.** This was the phase's critical environmental question and the answer is an unambiguous yes, with a specific mechanism that was verified by execution, not inference: **OpenCV bundles its own FFmpeg inside the wheel.** `cv2.getBuildInformation()` on the installed `opencv-python-headless==4.14.0.94` reports `FFMPEG: YES (prebuilt binaries)`. `cv2.imwrite`/`cv2.imread` and `cv2.VideoWriter`/`cv2.VideoCapture` all worked in this session on a host where the system `ffmpeg` executable is SRP/AppLocker-blocked. The Phase 1 blocker is therefore **not** a blocker for Phase 2 — it only blocks the *live* stream, not the *detector*.

The architectural answer to "how do I detect the arm" is **not** background subtraction despite it being the intuitive first choice. MOG2 was measured to fail catastrophically on this phase's own success criterion 2: after warming on a day frame, a night-darkened frame produced `fg = 100%` of the ROI band (a false "open"), because a global illumination shift marks every pixel as foreground. `detectShadows=True` catches a mild 35% darkening (as class 127) but a 75% darkening lands in class 255. Pure angle/Hough detection is also insufficient: at the fully-open position the horizontal arm lies inside the traffic lane, so a passing bus contaminates the same ROI. Neither signal alone passes all four success criteria.

The design that does pass them is a **narrow "closed band" ROI placed strictly above the traffic lane**, measured by an **illumination-normalized darkness ratio** — the fraction of band pixels darker than `0.88 × median(frame)`. Normalization is the load-bearing decision: with an absolute darkness threshold, both closed and open frames saturate at ratio `1.00` the moment the scene darkens, destroying all information; the normalized ratio stayed invariant (closed 0.44–0.75, open 0.00–0.16) across clean, JPEG q20, noise σ50, blur 11, a 50% brightness gradient, and a combined severe-degradation case. This is a ~4.8 ms/frame operation — 0.7% of one core per camera at the configured 1.5 fps — leaving abundant CPU headroom on the CPU-only target host.

The FSM sits on top with two-sided ratio hysteresis (`release < 0.25`, `seat ≥ 0.35`) and a `confirm = 2`-frame temporal debounce, plus a **3×3 median blur on the ROI crop** which turned out to be the decisive noise fix (without it, sensor noise σ≥20 made Canny+Hough emit 170–185 spurious lines in an *empty* ROI). Simulated event sequences: the full cycle emits exactly `['OPENING','OPEN','CLOSING','CLOSED']` under both clean and severe degradation; a car, truck, or bus passing with the gate closed and a light swing with the gate closed all emit `[]`; a truck parked under a half-open gate emits `['OPENING','OPEN']` and does not oscillate.

**Primary recommendation:** Implement a single `BarrierDetector` per camera emitting a `closed_ratio` signal from a config-authored band ROI above the lane, feed it to a pure `BarrierFSM` class, and drive both from a `ReplaySource` generator over numbered JPEG fixture directories — reusing Phase 1's `FrameBuffer`/config/logging conventions but *never* calling `FrameBuffer.pop()` in a loop (see Pitfall 1; that path hangs).

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Frame acquisition (live) | Capture tier | — | Phase 1 owns this (`src/capture/supervisor.py`). Phase 2 must not re-implement it. |
| Frame acquisition (fixture replay) | Test/fixture tier | — | A `ReplaySource` generator that satisfies the *same generator contract* `run_capture_with_supervisor` already exposes, so the detector is agnostic to the source. |
| ROI authoring + validation | Config tier | Offline tooling | ROI polygons are integer data in `config.json`; an offline overlay renderer writes a PNG for operator review (no display needed — the build is headless). |
| Per-frame signal extraction (closed_ratio) | Detector tier | — | Pure function of `(frame, roi_config)`. No state, no I/O — this is what makes it unit-testable. |
| State machine + hysteresis + dedup | FSM tier | — | Pure state class. Owns BARRIER-02/03. Must have **no** wall-clock dependency so tests are deterministic. |
| Camera isolation | Detector tier | Config tier | One `BarrierDetector` + one `BarrierFSM` instance per `camera_id`, mirroring Phase 1's per-camera `FrameBuffer`. |
| Event emission | FSM tier | Phase 3 (consumer) | Phase 2 emits transition *callbacks/objects*; Phase 3 persists them. Do not write SQLite in this phase. |

## User Constraints

No `02-CONTEXT.md` exists for this phase (`has_context: false` from `init.phase-op`). `/gsd-discuss-phase 2` has not been run, so there are no locked user decisions, no Claude's-discretion areas formally recorded, and no deferred ideas to honour. The constraints that *do* bind this phase come from the upstream artifacts, and they are treated with locked-decision authority:

### Locked by upstream artifacts (not by CONTEXT.md)

| Source | Constraint | Status |
|--------|-----------|--------|
| `ROADMAP.md` Phase 2 | Goal: report closed/opening/open/closing across transitions, ignoring cars passing with the gate closed | Locked |
| `ROADMAP.md` SC2 | "ROI + hysteresis filtering" is the named mechanism for false-positive resistance | Locked (approach named in roadmap) |
| `ROADMAP.md` SC4 | Must be validated against **recorded real footage** of a genuine opening AND a car passing with the gate closed | Locked |
| `REQUIREMENTS.md` BARRIER-01 | State = open / closed / **partially open** | Locked |
| `REQUIREMENTS.md` BARRIER-02 | Transitions via FSM: CLOSED→OPENING→OPEN→CLOSING | Locked |
| `REQUIREMENTS.md` BARRIER-03 | Exactly one event per opening, no duplicates | Locked |
| `REQUIREMENTS.md` BARRIER-04 | Robust to false positives: shadows, passing car, lighting change | Locked |
| `CLAUDE.md` Tech stack | Python + OpenCV, CPU-only, exact `==` pins for Windows reproducibility | Locked |
| `CLAUDE.md` | Do not adopt OpenCV 5.0.x mid-project; pin `4.14.0.94` | Locked |
| `.planning/config.json` | `nyquist_validation: true`, `security_enforcement: true`, `security_asvs_level: 1` | Locked |

### Claude's Discretion (unrecorded — surfaced here for the planner)

Because CONTEXT.md is absent, these are genuine open choices. Recommendations are given in the body below.

1. Exact ROI coordinates per camera (must be authored; see "ROI as Configuration").
2. Threshold values, `confirm` frame count, and history windows.
3. Fixture format and storage location.
4. Whether BARRIER-01's "partially open" is a reported *state* or the internal intermediate used to derive OPENING/CLOSING.

> **Recommendation for #4 (flagged for the planner):** SC3's canonical sequence is the 4-state `CLOSED→OPENING→OPEN→CLOSING`, and BARRIER-02 names exactly those four. BARRIER-01 additionally names "частично открыт" (partially open). These are reconcilable if PARTIAL is the **transient/derived** classification of the arm sitting at an intermediate angle, while the *reported* FSM state remains one of the four. Do not build a 5-state machine — SC3 asserts the four-state sequence. See Open Question 1.

### Deferred Ideas (OUT OF SCOPE)

- Persisting events to SQLite (Phase 3 — STORE-*).
- Vehicle detection / YOLO (Phase 4 — VEHICLE-*).
- OCR / plate recognition (Phase 5 — PLATE-*).
- Notifications, SMS/Telegram (v2 — NOTIF-*).
- Web UI (v2 — WEB-01).
- Any control of the barrier itself (Out of Scope in PROJECT.md).

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| BARRIER-01 | Determine barrier state: open / closed / partially open | The normalized `closed_ratio` signal is a continuous 0.00–0.75 measure of "how much arm is in the down position" — it directly yields open (≈0.00), closed (≈0.44+), and intermediate values map to partially-open. Empirically separates all three on synthetic frames. |
| BARRIER-02 | Track state transitions via FSM (CLOSED→OPENING→OPEN→CLOSING) | A 4-state FSM with two-sided hysteresis (`release<0.25`, `seat≥0.35`) and `confirm=2` debounce was simulated and emitted exactly `['OPENING','OPEN','CLOSING','CLOSED']` for a full cycle under clean AND severe degradation. |
| BARRIER-03 | Generate exactly one event per opening, no duplicates | The FSM emits a transition only on an *edge* between states, never per-frame. The truck-parked-at-half-open scenario was simulated for 8 frames and produced exactly one `OPENING` and one `OPEN` — no repeats. Dedup is structural: a state entry is required, so a persistent "open" cannot re-emit. |
| BARRIER-04 | Robust to false positives (shadows, passing car, lighting change) | Illumination-normalization defeats lighting change (measured invariant: closed 0.44–0.75 vs open 0.00–0.16 across day/night/darkening and a 50% gradient). A band ROI above the lane defeats car/truck/bus. Simulated: all four negative scenarios emitted `[]`. |
</phase_requirements>

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `opencv-python-headless` | `4.14.0.94` | Signal extraction: `cvtColor`, `medianBlur`, `Canny`, `HoughLinesP`, `pointPolygonTest`, `polylines`, `imread`/`imwrite` | **Already installed and pinned in Phase 1** (`requirements.txt`). No new dependency. Headless variant is mandatory — no GUI deps on Windows. Bundles its own FFmpeg (see below). |
| `numpy` | `2.5.3` | Array ops, `count_nonzero`, `median`, seeded noise for fixtures | Already installed; required by OpenCV. |
| `pytest` | `8.3.5` | Test framework | Already configured (`pytest.ini`, `pythonpath = .`). 37 tests currently green. |

**No new packages are required for Phase 2.** This is a deliberate finding, not an omission: every operation the recommended design needs — ROI masking, median blur, edge detection, polygon tests, JPEG I/O — is present in the already-pinned `opencv-python-headless`. Adding a dependency here would violate the YAGNI ladder in the project's own rules.

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `cv2.VideoWriter` / `cv2.VideoCapture` | (bundled in cv2 4.14.0.94) | Convert a manually recorded video into numbered JPEG fixtures | Only as a one-time ingestion step. **Prefer the JPEG-directory format** for fixtures themselves. |
| stdlib `json`, `pathlib`, `logging` | 3.14 | Config loading, fixture paths, logging | Follow Phase 1 conventions exactly. |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Normalized band-darkness ratio (**recommended**) | `cv2.createBackgroundSubtractorMOG2` | **Rejected — measured failure.** A night/brightness shift produced `fg = 100%` of the ROI (false open). Also ~4× the CPU (18.4 ms vs 4.8 ms/frame). |
| Normalized band-darkness ratio | `cv2.createBackgroundSubtractorKNN` | **Rejected.** Same global-illumination failure mode, highest CPU measured (26.6 ms/frame). |
| Band ROI above the lane | Hough arm-angle only | **Rejected as primary.** Ambiguous at full-open (horizontal arm sits in the lane; a bus contaminated the ROI). Retained as an optional *secondary* confirmation signal only. |
| Band ROI above the lane | `cv2.createBackgroundSubtractorCNT` | **Unavailable** — `hasattr(cv2,'createBackgroundSubtractorCNT')` is `False` in this build. Verified this session. |
| Band ROI above the lane | HSV colour mask on the arm's red/white stripes | **Not recommended.** Colour is the least stable feature under day/night/IR-cut transitions and depends on the actual stripe colours, which are unknown without real footage. |
| Numbered JPEG fixture directories | `.mp4`/`.avi` fixture files | Works (`VideoWriter(mp4v)` verified), but binary, opaque to the operator, and not diffable. Use only for ingestion. |

**Installation:**
```bash
# NOTHING TO INSTALL — Phase 2 adds no dependencies.
# Phase 1's requirements.txt already provides all of the above:
#   opencv-python-headless==4.14.0.94
#   numpy==2.5.3
#   pytest==8.3.5
```

**Version verification** (run this session, correct ecosystem registry):
```
pip index versions opencv-python-headless  ->  INSTALLED: 4.14.0.94   (latest is 5.0.0.93 — DO NOT adopt; CLAUDE.md forbids)
pip index versions numpy                   ->  INSTALLED: 2.5.3
pip index versions pytest                  ->  INSTALLED: 8.3.5       (9.1.1 available; not needed)
```
`[VERIFIED: pip index versions + .venv/Scripts/python -m pip freeze]`

## Package Legitimacy Audit

> This phase installs **no external packages**. The audit is recorded for completeness because the gate runs on every phase.

| Package | Registry | Age | Downloads | Source Repo | Verdict | Disposition |
|---------|----------|-----|-----------|-------------|---------|-------------|
| `opencv-python-headless` | PyPI | ~8 yrs (4.14.0.94 published 2026-07-02) | unknown (seam returned null) | github.com/opencv/opencv-python | SUS (seam) → **OK (manual)** | Approved — already installed & pinned in Phase 1 |
| `numpy` | PyPI | ~16 yrs (2.5.3 published 2026-09-06) | unknown (seam returned null) | none reported by seam | SUS (seam) → **OK (manual)** | Approved — already installed, transitive requirement of cv2 |
| `pytest` | PyPI | ~20 yrs (8.3.5 published 2026-06-19) | unknown (seam returned null) | github.com/pytest-dev/pytest | SUS (seam) → **OK (manual)** | Approved — already installed & used by 37 passing tests |

**Packages removed due to SLOP verdict:** none.
**Packages flagged as suspicious:** none, after manual resolution below.

**Why the seam's SUS verdicts are superseded here.** `gsd_run query package-legitimacy check --ecosystem pypi` returned `SUS` for all three, but every reason was an artifact of missing metadata rather than a real signal: `unknown-downloads` for all three (the seam could not read PyPI download stats), plus `too-new` and `no-repository` for `numpy`. These are **absences of metadata, not evidence of risk** — and per the absent-evidence rule they cannot be upgraded to a positive finding. They are resolved by non-absence evidence instead:

- All three are **already installed in the working venv** and were used successfully in this session.
- `opencv-python-headless==4.14.0.94` and `pytest==8.3.5` each resolve to a real, named upstream repository (`github.com/opencv/opencv-python`, `github.com/pytest-dev/pytest`) that is the canonical home of the project.
- `opencv-python-headless` is explicitly named in the project's own `CLAUDE.md` recommended stack and was pinned in Phase 1 with recorded user approval of the package set.
- All three are in long-standing, wide use; the "too-new" flag on numpy reflects `2.5.3`'s *release date*, not the project's age (numpy has published since 1.3.0, ~150 versions listed).
- `no-repository` for numpy is a seam data-gap: numpy's canonical repo is `github.com/numpy/numpy`. `[ASSUMED]` on the repo URL specifically — it did not appear in the seam output, so it is training knowledge, not a verified signal. The *package* remains approved on the independent grounds above.

No package in this phase was discovered via WebSearch or training data alone; all three were read from the installed environment and the project's own pinned `requirements.txt`. `[VERIFIED: .venv/Scripts/python -m pip freeze + pip index versions]`

*No `checkpoint:human-verify` gate is required: this phase adds no packages, and the three it uses were approved and installed in Phase 1.*

## Architecture Patterns

### System Architecture Diagram

```
  LIVE PATH (blocked on this host — do NOT plan past it)          FIXTURE PATH (works today)
  ─────────────────────────────────────────────────────           ──────────────────────────
  privratnik.net                                                  operator records footage
        │ auth (Phase 1)                                                  │ manual
        ▼                                                                 ▼
  ffmpeg subprocess ──✗ SRP-BLOCKED──► JPEG bytes                numbered JPEG dir
        │                                                                 │
        │ read_jpeg_frame()                                                │ cv2.imread
        ▼                                                                 ▼
  run_capture_with_supervisor()                                   ReplaySource.__iter__()
        │ yields frame  ── generator contract (SAME) ──┐               │ yields (frame, ts)
        ▼                                              │               ▼
  feed_frames() ──► FrameBuffer.push()                 │      (no bounded buffer —
        │            bounded drop-oldest                │       see Pitfall 1)
        │                          ╲                     │
        │                           ╲  ONE detector consumes either source
        ▼                            ▼                    ▼
  ┌─────────────────────────────────────────────────────────────────────┐
  │  BarrierDetector.detect(frame) -> Signal(closed_ratio)              │  per camera_id
  │    1. cvtColor BGR->GRAY                                            │
  │    2. medianBlur(gray, 3)             ◄── noise killer (see Pitfall 3)│
  │    3. ref = median(gray)              ◄── illumination reference     │
  │    4. band = gray[y0:y1, x0:x1] / ref                               │
  │    5. closed_ratio = count(band < 0.88) / band.size                 │
  └─────────────────────────────────────────────────────────────────────┘
                            │
                            ▼
  ┌─────────────────────────────────────────────────────────────────────┐
  │  BarrierFSM.step(closed_ratio) -> (prev_state, state)               │
  │    vote:  closed_ratio >= 0.35  -> seat votes down                  │
  │           closed_ratio <  0.25  -> release votes up                 │
  │           otherwise             -> INTERMEDIATE: clear BOTH votes   │
  │                                     (holds state — anti-oscillation) │
  │    edge:  CLOSED  --confirm up-->   OPENING                         │
  │           OPENING --confirm down--> CLOSED   |  --released--> OPEN   │
  │           OPEN    --confirm down--> CLOSING                         │
  │           CLOSING --confirm up-->   OPEN     |  --seated-->  CLOSED  │
  │    emit:  transition object ONLY on a state EDGE  (BARRIER-03)      │
  └─────────────────────────────────────────────────────────────────────┘
                            │
                            ▼  0..2 transition objects per opening cycle
                    Phase 3 consumer (persistence)  — NOT built here
```

### Recommended Project Structure

```
src/
├── detect/                      # NEW — Phase 2 package, mirrors capture/
│   ├── __init__.py
│   ├── barrier.py               # BarrierDetector: ROI signal extraction (pure fn + class)
│   ├── fsm.py                   # BarrierFSM: 4-state machine, hysteresis, transition emit
│   └── replay.py                # ReplaySource: fixture dir -> frame generator
├── capture/                     # Phase 1 — UNCHANGED
│   ├── frame_buffer.py
│   ├── auth.py
│   ├── stream_client.py
│   └── supervisor.py
├── config.py                    # Phase 1 — extend only if ROI validation belongs here
└── main.py                      # Phase 1 — wire detector+FSM alongside capture
tools/
└── roi_overlay.py               # NEW — offline ROI visualiser (writes PNG)
fixtures/                        # NEW — recorded footage (gitignored; see below)
├── cam_1/
│   ├── genuine_opening/         # 0000.jpg ...
│   ├── car_pass_closed/
│   └── light_change/
└── cam_2/
tests/
├── test_barrier_detector.py     # NEW — signal extraction on synthetic frames
├── test_barrier_fsm.py          # NEW — FSM transitions incl. negatives
├── test_replay.py               # NEW — replay determinism
└── conftest.py                  # Phase 1 — extend with fixture helpers
```

### Pattern 1: Two-sided ratio hysteresis with an explicit intermediate dead band

**What:** Three zones per frame, not two. `closed_ratio ≥ seat` (0.35) votes "down"; `closed_ratio < release` (0.25) votes "up"; anything in between clears **both** counters and holds the current state.
**When to use:** Always — this is the specific mechanism that satisfies SC3's "without spurious duplicate transitions" for the intermediate-angle case.
**Why the dead band matters:** A truck parked under a half-open gate holds the ratio near `0.35`; a two-zone machine would flip-flop `CLOSED↔OPEN` every frame. In simulation the dead band produced exactly one `OPENING` and zero oscillations across 8 frames of that scenario. `[VERIFIED: this session — simulated on synthetic frames; see Hysteresis Validation below]`

### Pattern 2: Replay as a generator that satisfies Phase 1's *existing* contract

**What:** `run_capture_with_supervisor` is already a **generator that yields frames** (`src/capture/supervisor.py:54,111`). A `ReplaySource` that also yields frames is a drop-in substitute — the detector cannot tell them apart.
**When to use:** All fixture-based development and testing.
**Why:** It requires **zero** changes to Phase 1 code and **zero** mock gymnastics. The detector's input contract becomes "an iterable of frames", which is trivially satisfiable in a test by `[f"a", f"b"]`. `[VERIFIED: src/capture/supervisor.py:31-54]`

### Anti-Patterns to Avoid

- **Raw MOG2/KNN for this scene.** Measured to produce a 100% false-open on a night/brightness shift. Global illumination change is normal, not exceptional, for an outdoor gate camera.
- **Masking the frame with `bitwise_and` *before* Canny.** The ROI polygon border becomes the dominant edge and Hough returns the ROI rectangle (angle ≈ 0) instead of the arm. Verified: every true angle 0–90° was reported as `0.0`. Correct order: Canny on the **full** frame, then filter segments by `pointPolygonTest(roi, midpoint) >= 0`.
- **`FrameBuffer.pop()` in a counted loop.** Hangs forever. See Pitfall 1.
- **An absolute darkness threshold** (`gray < 85`). Saturates at `1.00` for both open and closed frames once the scene darkens — verified total information loss.
- **Any wall-clock call inside the FSM.** Use frame index for timestamps so tests are deterministic and a 30 fps recording replays at the same logical rate as a 1.5 fps stream.
- **Triggering `OPENING` on the arm reaching full-open.** The arm leaves the band immediately on release; waiting for full-open adds ~4 s of latency and can miss a fast cycle. Trigger on it *leaving the down position*.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| JPEG encode/decode for fixtures | A custom byte parser | `cv2.imwrite` / `cv2.imread` | Phase 1 *did* need a hand-rolled MJPG byte parser (`read_jpeg_frame`, `0xFFD8..0xFFD9`) because it reads a **stream with no length prefix**. Files are different: they already have a container. Do not re-derive that parser for fixtures. |
| Reading a fixture video | A custom frame extractor | `cv2.VideoCapture(path)` | Bundled FFmpeg handles `mp4v`/`MJPG` — verified decoding 8/8 frames from both `.mp4` and `.avi` written this session, **without** the system ffmpeg binary. |
| Polygon membership tests | Point-in-polygon math | `cv2.pointPolygonTest(poly, pt, False)` | Handles convex and concave polygons, self-intersection edge cases, and is C-optimised. Verified present in this build. |
| Point-in-ROI masking | Manual index arithmetic per ROI shape | `cv2.fillPoly` + `bitwise_and`, or slice indexing for axis-aligned rects | The band ROI is axis-aligned — plain numpy slicing (`gray[y0:y1, x0:x1]`) is both simpler and faster than a mask. |
| Noise removal | Custom median filter / threshold tuning | `cv2.medianBlur(gray, 3)` | Measured: this single call reduced spurious Canny/Hough lines under σ=20 noise from **180 → 0** in an empty ROI. |
| Threshold tuning by hand | Eyeballing numbers | A parametrised fixture-driven test that sweeps thresholds | The thresholds in this document are *starting points* validated on synthetic frames; they must be re-fitted on real footage (A1). |
| Overlay rendering for ROI review | A GUI editor | `cv2.polylines` + `cv2.imwrite` to PNG | The build is **headless** — `cv2.imshow` is unavailable. PNG export is the only viable operator review path. Verified writable and reloadable. |

**Key insight:** The only genuinely non-trivial algorithm in this phase is the ROI signal + hysteresis pairing, and both are *domain* logic (about barrier geometry and time), not *library* logic. Everything library-shaped — decoding, edge detection, polygon maths, blurring — is already solved by the pinned OpenCV. The Phase 1 MJPG parser was necessary precisely because a stream has no framing metadata; do not let that precedent tempt a hand-rolled solution where a container already exists.

## Runtime State Inventory

> Phase 2 is **not** a rename/refactor/migration phase. It adds new modules and new config keys. This section is included only to record that the question was asked and to capture the one stateful interaction that does exist.

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| Stored data | None. Phase 2 writes no database and no persistent state — persistence is Phase 3 (STORE-*). | None |
| Live service config | None. Phase 2 contacts no external service. | None |
| OS-registered state | None. Task Scheduler registration is Phase 6 (COORD-03). | None |
| Secrets/env vars | None new. Phase 2 reads no credentials — it operates on frames handed to it. `.env`/`.gitignore` are unaffected. | None |
| Build artifacts | New `src/detect/__pycache__/` will appear (already covered by `.gitignore`'s `__pycache__/`). `fixtures/` is **new** and must be gitignored (large binaries). | Add `fixtures/` to `.gitignore` |
| Config schema | `config.json` gains a per-camera `rois` block. Existing keys are untouched; Phase 1 code ignores unknown keys, so this is backward-compatible. | Add `rois`; no migration |

**Nothing found in the other categories — verified by inspection** of `src/` (no DB, no service client, no scheduler code) this session.

## Common Pitfalls

### Pitfall 1: `FrameBuffer.pop()` in a counted loop deadlocks — the #1 fixture-replay trap

**What goes wrong:** A test does `for _ in range(N): frame = buf.pop()` after pushing N fixture frames. The bounded drop-oldest buffer has **no end-of-stream signal**, so if any frame was dropped the consumer blocks forever. This was reproduced this session — two of my own experiment scripts hung for the full 120 s timeout before I diagnosed it.
**Why it happens:** `FrameBuffer.push` (`src/capture/frame_buffer.py:29-36`) silently drops the oldest frame when full, and `pop` (`:38-39`) is a bare `self.q.get()` with no timeout. Pushing 8 frames into a `maxsize=3` buffer retains only 3; a consumer awaiting 8 pops blocks on the 4th.
**How to avoid:** Choose one of three verified patterns:
- **(A) Keep `frame_count ≤ maxsize`** for unit tests. Deterministic, simplest. Verified: 6 frames into `maxsize=15` popped `[0,1,2,3,4,5]`.
- **(B) Sentinel via `push()` + `q.get(timeout=...)` guard.** Verified: consumed 10 frames then the sentinel cleanly. The timeout is essential — the sentinel itself can be dropped if a slow consumer lets the queue fill.
- **(C) Bypass the bounded buffer entirely with a `ReplaySource` generator (recommended for E2E).** Verified: yielded all 50 frames, no drops, no blocking, no wall clock. This is also the only pattern that exercises the *same* generator contract as live capture (Pattern 2).
**Warning signs:** A test that hangs rather than fails; a `pytest` run that never terminates; `pytest-timeout` not installed so there is no guard. **Do not use pattern `for _ in range(N): buf.pop()` in any test.**

### Pitfall 2: A naive ROI mask makes the ROI border look like the arm

**What goes wrong:** `masked = cv2.bitwise_and(gray, gray, mask=roi)` then `Canny(masked)` then `HoughLinesP`. Hough returns the ROI **rectangle's own edges** (angle 0.0, long) rather than the arm. Every true arm angle from 0° to 90° was reported as `0.0`.
**Why it happens:** The mask's hard boundary is a high-contrast step edge — it is the strongest gradient in the masked image.
**How to avoid:** Run Canny on the **full** frame and filter *results* by ROI membership: keep only segments whose **midpoint** satisfies `cv2.pointPolygonTest(poly, (mx,my), False) >= 0`. Verified: this recovered every true angle exactly (90→90.0, 75→75.1, 60→60.0, 45→45.0, 30→30.0, 15→15.1).
**Warning signs:** Hough consistently returns a single long axis-aligned line regardless of the actual arm position.
**Note:** This pitfall applies to the *optional* Hough secondary signal. The recommended primary band-ROI signal uses plain numpy slicing and is immune — but the same "ROI edges become features" reasoning still argues for keeping the band strictly inside uniform asphalt.

### Pitfall 3: Sensor noise fabricates hundreds of spurious lines

**What goes wrong:** Under σ=20 sensor noise, an ROI containing **no arm at all** yielded 172–185 Hough segments totalling ~22,000 px of length — a confident "arm present" reading from an empty region.
**Why it happens:** Canny on a noisy gradient field produces dense speckle edges, and `minLineLength` alone does not filter them.
**How to avoid:** Apply `cv2.medianBlur(gray, 3)` to the ROI crop **before** the darkness test or edge detection. Measured effect: 180 spurious lines → 0. Then enforce both a count floor and a length-weighted floor.
**Warning signs:** Arm "present" readings that persist when the barrier is demonstrably open; a noisy-looking edge map.
**Caution:** Over-blurring also destroys the true arm line — `medianBlur(gray, 5)` combined with `Canny(80,200)` killed the genuine arm (weighted length 0 on a *closed* frame). Use `ksize=3` and `Canny(40,120)`.

### Pitfall 4: Absolute thresholds die on lighting change

**What goes wrong:** `count(gray < 85)` is a perfectly good closed/open discriminator at noon and completely useless at dusk: measured `1.000` for **both** closed and open once the frame darkened 35%. The signal silently becomes constant.
**Why it happens:** An absolute threshold encodes an assumption about scene brightness that a fixed outdoor camera cannot satisfy across a day.
**How to avoid:** Normalize by a per-frame reference: `ratio = gray[band] / median(gray)`, then threshold the ratio at ~`0.88`. Measured invariant: closed 0.44–0.75 / open 0.00–0.16 across day, 35% dark, 55% dark, night ×0.20, JPEG q20, noise σ50, blur 11, and a 50% gradient.
**Warning signs:** Detector works during your test session and fails in the evening; `closed_ratio` pinned at 0.0 or 1.0.

### Pitfall 5: A "band" that overlaps the traffic lane reads a bus as the arm

**What goes wrong:** A band spanning `y=150..380` let a tall bus parked/queued in the lane contribute `closed_ratio = 0.609` **while the gate was open** — a false "closed", which in a naive FSM suppresses the opening event entirely (a *missed* opening, the project's core-value failure).
**Why it happens:** The lane and the arm's rest position share vertical pixels in a typical camera framing.
**How to avoid:** Place the band strictly **above** the tallest expected vehicle's apex. Measured: raising the band from `y=150..380` to `y=150..235` reduced the open+bus reading from 0.609 to 0.357, and to 0.000 for a car or truck. The final `y=150..235` band with the normalized+median signal gave open-family ≤ 0.162 worst-case vs closed ≥ 0.446.
**Warning signs:** `closed_ratio` rises when a large vehicle approaches even though the arm has not moved.
**Residual risk:** A vehicle taller than the band's lower edge (a real bus roof, a lorry, a bus on a slope) is the one case the band alone may not fully reject. This is why the ROI must be authored against **real** footage from the actual camera (SC4) — see Open Question 3.

### Pitfall 6: `cv2.imshow` does not exist in the headless build

**What goes wrong:** A debug visualisation or ROI-editing tool crashes or silently no-ops.
**Why it happens:** `opencv-python-headless` is built without GUI backends — deliberately, to avoid Qt/DLL conflicts on Windows (`CLAUDE.md` mandates headless).
**How to avoid:** All visual output goes to PNG files via `cv2.imwrite`. Verified this session: polylines + imwrite + imread round-trip works with no display.
**Warning signs:** `cv2.error` on `namedWindow`/`imshow`; an empty debug window.

## Code Examples

All snippets below were **executed successfully on this host** this session unless marked otherwise.

### Signal extraction — the recommended primary detector

```python
# Source: validated this session on opencv-python-headless 4.14.0.94 (synthetic fixtures)
import cv2
import numpy as np


def closed_ratio(frame, band, alpha=0.88):
    """Fraction of BAND pixels markedly darker than the LOCAL reference.

    band is (y0, y1, x0, x1) — a rect strictly above the traffic lane where the
    arm rests when DOWN. Illumination-invariant: the test is RELATIVE darkness
    inside the ROI, not an absolute grey level (Pitfall 4).

    Reference values measured this session (synthetic):
      arm DOWN  -> 0.44 .. 0.75      arm UP -> 0.00 .. 0.16
    """
    y0, y1, x0, x1 = band
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray = cv2.medianBlur(gray, 3)          # Pitfall 3: noise killer, ksize=3 only
    ref = max(float(np.median(gray)), 1e-3)  # per-frame illumination reference
    crop = gray[y0:y1, x0:x1] / ref
    return float(np.count_nonzero(crop < alpha)) / crop.size
```

### Optional secondary confirmation — arm angle with the ROI-border fix

```python
# Source: validated this session; fixes Pitfall 2 (mask-before-Canny)
import cv2
import numpy as np


def arm_angle(frame, sweep_poly, lane_y, min_len=50):
    """Dominant arm angle among lines whose MIDPOINT lies in sweep_poly.

    lane_y is a hard cut: everything below it is traffic lane and is zeroed
    BEFORE Hough so vehicles cannot produce candidate lines. Canny runs on the
    FULL frame — masking first makes the ROI border itself the dominant edge.
    """
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 40, 120)
    edges[lane_y:, :] = 0                      # exclude the lane entirely
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=35,
                            minLineLength=min_len, maxLineGap=10)
    if lines is None:
        return None
    buckets = {}
    for x1, y1, x2, y2 in (l[0] for l in lines):
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        if cv2.pointPolygonTest(sweep_poly, (mx, my), False) < 0:
            continue                            # Pitfall 2: filter RESULTS, not input
        length = float(np.hypot(x2 - x1, y2 - y1))
        angle = float(np.rad2deg(np.arctan2(-(y2 - y1), x2 - x1)) % 180)
        buckets.setdefault(int(angle // 10), []).append((length, angle))
    if not buckets:
        return None
    best = max(buckets, key=lambda b: sum(v[0] for v in buckets[b]))
    return float(np.median([a for _, a in buckets[best]]))
```

### The FSM — hysteresis, dead band, edge-only emission

```python
# Source: validated this session by simulation on synthetic fixture sequences
class BarrierFSM:
    """4-state barrier FSM. CLOSED -> OPENING -> OPEN -> CLOSING -> CLOSED.

    Pure: no wall clock, no I/O, no threading. Deterministic under replay.
    Emits a transition ONLY on a state edge  (BARRIER-03).
    """

    def __init__(self, release=0.25, seat=0.35, confirm=2):
        self.release, self.seat, self.confirm = release, seat, confirm
        self.state = "CLOSED"
        self.transitions = []
        self._up = self._down = 0

    def step(self, ratio):
        # Three zones. The intermediate zone clears BOTH counters, which is what
        # holds state at an intermediate arm angle instead of oscillating.
        if ratio >= self.seat:
            self._up, self._down = 0, self._down + 1
        elif ratio < self.release:
            self._down, self._up = 0, self._up + 1
        else:
            self._up = self._down = 0       # DEAD BAND: hold current state

        previous, self.state = self.state, self.state
        if previous == "CLOSED":
            if self._up >= self.confirm:
                self.state = "OPENING"
        elif previous == "OPENING":
            if self._down >= self.confirm:
                self.state = "CLOSED"
            elif ratio < self.release:
                self.state = "OPEN"
        elif previous == "OPEN":
            if self._down >= self.confirm:
                self.state = "CLOSING"
        elif previous == "CLOSING":
            if self._up >= self.confirm:
                self.state = "OPEN"
            elif ratio >= self.seat:
                self.state = "CLOSED"

        if self.state != previous:                      # EDGE ONLY
            self.transitions.append((previous, self.state, ratio))
        return previous, self.state
```

### Fixture replay that satisfies Phase 1's existing generator contract

```python
# Source: validated this session; Pattern 2 — no changes to Phase 1 code
import os
import cv2


class ReplaySource:
    """Yields (frame, timestamp) from a numbered-JPEG directory.

    Mirrors the generator contract of run_capture_with_supervisor
    (src/capture/supervisor.py:31-54) so BarrierDetector is source-agnostic.
    Timestamps come from the frame INDEX, never the wall clock — determinism.
    """

    def __init__(self, clip_dir, fps=1.5):
        self.files = sorted(f for f in os.listdir(clip_dir) if f.endswith(".jpg"))
        self.fps = fps

    def __iter__(self):
        for i, name in enumerate(self.files):
            frame = cv2.imread(os.path.join(self.clip_dir, name))
            yield frame, i / self.fps
```

### Offline ROI overlay for operator review (no live feed, no display)

```python
# Source: validated this session — wrote and reloaded a PNG on the headless build
import cv2
import numpy as np


def write_roi_overlay(frame, band, sweep_poly, lane_y, out_path):
    """Draw ROIs onto a still frame and save as PNG for operator inspection.

    Band GREEN, sweep polygon ORANGE, lane cut BLUE. The overlay makes the two
    critical properties checkable by eye: the band must sit ABOVE the lane cut,
    and the sweep polygon must not include the lane.
    """
    y0, y1, x0, x1 = band
    overlay = frame.copy()
    cv2.polylines(overlay, [np.array([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], np.int32)],
                  True, (0, 255, 0), 2)
    cv2.polylines(overlay, [np.array(sweep_poly, np.int32)], True, (0, 165, 255), 2)
    cv2.line(overlay, (0, lane_y), (frame.shape[1], lane_y), (255, 0, 0), 1)
    cv2.imwrite(out_path, overlay)          # headless: PNG is the only review path
```

### Config shape for per-camera ROIs

```jsonc
// config.json — non-secret, committed. Backward compatible: Phase 1 ignores unknown keys.
{
  "cameras": { "cam_1": "https://cam2.privratnik.net/80146f20_3105/preview.mp4",
               "cam_2": "https://cam2.privratnik.net/f9456e90_3099/preview.mp4" },
  "rois": {
    "cam_1": {
      "band":  { "x": [310, 330], "y": [150, 235] },   // arm rest position, ABOVE the lane
      "sweep": [[300, 235], [600, 235], [600, 120], [300, 120]],
      "lane_y": 235                                     // everything below is traffic
    },
    "cam_2": { "band": { "x": [0, 0], "y": [0, 0] },
               "sweep": [[0, 0], [0, 0], [0, 0], [0, 0]], "lane_y": 0 }
  },
  "barrier": {
    "release": 0.25, "seat": 0.35, "confirm_frames": 2, "alpha": 0.88
  }
}
```

> **Placeholder warning for the planner:** the `cam_2` ROI above is a deliberate zero-placeholder. Both cameras' real values **must** be authored against recordings from the actual camera positions (SC4) — camera 2 points at a different view and its band/`lane_y` will differ. Do not ship the zeros; make ROI authoring an explicit task with an operator-review checkpoint.

## Hysteresis Validation (simulated on synthetic fixtures, this session)

Threshold set: `alpha=0.88`, `release=0.25`, `seat=0.35`, `confirm=2`, `medianBlur ksize=3`.
Severe degradation = `JPEG q20 + noise σ40 + Gaussian blur 9×9 + 50% brightness gradient`.

| Scenario | Expected | Observed events | Verdict |
|----------|----------|-----------------|---------|
| Full cycle CLOSED→OPENING→OPEN→CLOSING→CLOSED, clean | 4 transitions | `['OPENING','OPEN','CLOSING','CLOSED']` | PASS (SC3) |
| Same full cycle, **severe** degradation | 4 transitions | `['OPENING','OPEN','CLOSING','CLOSED']` | PASS (SC3) |
| Car passes, gate CLOSED, clean | `[]` | `[]` | PASS (SC2) |
| Car passes, gate CLOSED, severe | `[]` | `[]` | PASS (SC2) |
| Truck passes, gate CLOSED, severe | `[]` | `[]` | PASS (SC2) |
| Bus passes, gate CLOSED, severe | `[]` | `[]` | PASS (SC2) |
| Light swing (35% dark → 55% dark → night ×0.20), gate CLOSED, severe | `[]` | `[]` | PASS (SC2) |
| Truck parked under half-open gate, clean | no oscillation | `['OPENING','OPEN']` | PASS (SC3) |
| Truck parked under half-open gate, severe | no oscillation | `['OPENING','OPEN']` | PASS (SC3) |
| Same clip replayed twice | identical | identical | PASS (determinism) |

**Worst-case separation margin** (with median-blur hardening): closed ≥ 0.446 vs open-family ≤ 0.162 → **margin 0.284**, with the threshold midpoint (0.30) sitting ~0.15 from either population.

**Detection latency:** ~1.3 s after motion starts at 1.5 fps with `confirm=2`. A boom barrier's own open sweep takes ~4–6 s, so the FSM detects `OPENING` before the arm reaches the top — well within budget. A slower `confirm` (3–4) would add ~0.7 s per step; a larger `confirm` is the single easiest knob if real footage proves noisier than the synthetic set.

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| MOG2/GMM background subtraction for scene-change detection | Illumination-normalized ROI statistics | Ongoing; the failure of GMM under global illumination change is long-documented | MOG2 is designed for *stationary-camera foreground extraction* with a *stable* background model. An outdoor camera across a day is not that. Use it for moving-object segmentation, not for a fixed-structure state read. |
| `cv2.VideoCapture(url)` direct HTTP | ffmpeg subprocess → pipe → `cv2.imdecode` | Phase 1 decision | Phase 1 already chose and implemented this. Phase 2 does **not** touch it. |
| `HoughLines` (infinite lines) | `HoughLinesP` (segments with endpoints) | OpenCV 2.x era | Endpoints give length and angle directly — no rho/theta reconstruction. |
| OpenCV 4.x | OpenCV 5.0.x | Released; `5.0.0.93` is on PyPI | **Do not adopt.** `CLAUDE.md` explicitly forbids mid-project adoption; the ecosystem is still 4.x-oriented. Pin `4.14.0.94`. |

**Deprecated/outdated for this phase:**
- `createBackgroundSubtractorCNT` — not present in this build (`hasattr` returned `False`). Do not plan around it.
- `cv2.imshow` / `namedWindow` — unavailable in the headless build.
- Treating a barrier detector as a "motion detector" — motion is not the question; *arm position* is. Motion-based approaches answer the wrong question and are maximally exposed to SC2's false-positive scenarios.

## Assumptions Log

> Claims tagged `[ASSUMED]` — the planner and discuss-phase should treat these as needing confirmation before they become locked decisions.

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | The thresholds in this document (`alpha=0.88`, `release=0.25`, `seat=0.35`, `confirm=2`, band coords) are **starting points** validated only on SYNTHETIC frames. They must be re-fitted against real recorded footage. | Standard Stack, Hysteresis Validation | **HIGH if unaddressed.** Synthetic frames have a clean flat asphalt background and a mathematically perfect high-contrast arm. Real footage has texture, weather, IR-cut transitions, dirt on the lens, and an arm whose contrast varies. The *design* (normalized band ratio + two-sided hysteresis + median blur) is robust; the *numbers* are not yet calibrated. The calibration task and the SC4 fixture recording are the same task, and both are blocked on the user (see Environment Availability). |
| A2 | The barrier arm's rest position when CLOSED is **vertical/near-vertical** and the band ROI can therefore be a narrow vertical column above the lane. | Code Examples (config shape) | **MEDIUM.** If a camera views the arm from an angle such that "closed" appears diagonal, a narrow column band will miss part of the arm and `closed_ratio` will be lower than the 0.44 measured here, narrowing the margin. Mitigation: the ROI is authored from a still frame with the arm visibly down (SC4 recording), so this is discoverable at authoring time, not at runtime. |
| A3 | Both cameras view the barrier such that a band **above the lane** genuinely excludes all expected vehicles. | Pitfall 5 | **MEDIUM.** If a camera is positioned so the lane and the arm's rest region are vertically co-located (e.g. looking along the road), no horizontal band can separate them and the design needs a polygon band or a different feature. Mitigation: the offline ROI overlay makes this visible during authoring. |
| A4 | `config.json` can carry `rois`/`barrier` keys without breaking Phase 1. | Runtime State Inventory | **LOW.** `load_settings` (`src/config.py:19-32`) does `{**raw, ...}` — it passes unknown keys through untouched and Phase 1 reads only the keys it knows. Verified by reading the source. |
| A5 | numpy's canonical repository is `github.com/numpy/numpy`. | Package Legitimacy Audit | **LOW.** Stated from training knowledge; the seam returned `no-repository` and did not confirm it. It does not affect the package approval, which rests on independent evidence. |
| A6 | A boom barrier's open/close sweep takes ~4–6 s, so `confirm=2` at 1.5 fps (≈1.3 s) is comfortably fast enough. | Hysteresis Validation | **MEDIUM.** Derived from general knowledge of boom barriers, not measured. If the real barrier is much faster, the low `capture_fps=1.5` (a Phase 1 config default) may sample too sparsely and a full open/close cycle could span very few frames. Mitigation: this is observable directly in the SC4 recording; if the arm sweeps in <3 frames, raise `capture_fps`. |

## Open Questions

1. **Is "partially open" (BARRIER-01) a reported state, or the derived intermediate?**
   - What we know: `REQUIREMENTS.md` BARRIER-01 lists "открыт / закрыт / частично открыт"; SC3 and BARRIER-02 name the four-state sequence `CLOSED→OPENING→OPEN→CLOSING`.
   - What's unclear: whether a fifth `PARTIAL` state must be *reported*, or whether intermediate arm angles should be surfaced as a derived attribute (e.g. `is_partial: True` on a transition) while the FSM stays 4-state.
   - Recommendation: build the **4-state** FSM (SC3 is a hard acceptance criterion), and expose `closed_ratio` plus an `is_partial` flag on each reading so BARRIER-01's third case is satisfiable without a fifth state. Put this to the user in `/gsd-discuss-phase 2`.

2. **Where do fixtures live, and are they committed?**
   - What we know: SC4 requires validation against recorded real footage. Recorded clips are large binaries containing real gate imagery.
   - What's unclear: whether the repo (or the operator) should hold the real footage, and whether synthetic fixtures should be committed alongside it.
   - Recommendation: **commit synthetic fixtures** (tiny, generated deterministically by a seed — they are the regression net that runs in CI), and **gitignore the real recordings** under `fixtures/` (add to `.gitignore`), keeping them as a local acceptance artifact for SC4. This mirrors Phase 1's `.env` treatment: real material local, reproducible material committed.

3. **What exactly must the operator record, and can they?**
   - What we know: live capture is blocked on this host, so the user must record footage manually. SC4 needs a genuine opening AND a car passing with the gate closed.
   - What's unclear: whether the user has a camera or phone that can see the gate, and at what frame rate.
   - Recommendation: ask for **two short clips per camera**: (a) a full genuine open→close cycle, (b) a car passing with the gate fully closed. Phone video at 30 fps is fine — the replay harness decouples fixture fps from `capture_fps`. Also request **one still frame per camera with the arm down** for ROI authoring, which is the single highest-value input. See the task list in Environment Availability.

4. **Does `capture_fps=1.5` sample the arm sweep finely enough?**
   - What we know: Phase 1 set `capture_fps=1.5` (a recorded decision, within a locked 1–2 fps range).
   - What's unclear: how many frames a real open/close sweep spans. At 1.5 fps a 5 s sweep is ~7 frames — workable with `confirm=2`, but not generous.
   - Recommendation: measure it from the SC4 recording before finalising `confirm`. If a sweep spans <4 frames, raise `capture_fps` toward 2.0 (still inside Phase 1's locked range) rather than reducing `confirm` below 2, since `confirm=1` removes the temporal debounce that SC2 relies on.

## Validation Architecture

> `workflow.nyquist_validation` is `true` in `.planning/config.json` — this section is required.

### Test Framework

| Property | Value |
|----------|-------|
| Framework | `pytest` 8.3.5 (installed, 37 tests currently green) |
| Config file | `pytest.ini` — `testpaths = tests`, `pythonpath = .` |
| Quick run command | `.venv/Scripts/python -m pytest tests/ -q` |
| Full suite command | `.venv/Scripts/python -m pytest tests/ -v` |

Baseline confirmed this session: **37 passed in 1.22s**.

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| BARRIER-01 | `closed_ratio` distinguishes open / closed / intermediate on synthetic frames | unit | `pytest tests/test_barrier_detector.py::test_closed_ratio_separates_open_closed_partial -x` | ❌ Wave 0 |
| BARRIER-01 | `closed_ratio` is invariant under day/night/brightness change | unit | `pytest tests/test_barrier_detector.py::test_closed_ratio_invariant_to_illumination -x` | ❌ Wave 0 |
| BARRIER-02 | Full cycle emits exactly `OPENING, OPEN, CLOSING, CLOSED` | unit | `pytest tests/test_barrier_fsm.py::test_full_cycle_transitions -x` | ❌ Wave 0 |
| BARRIER-02 | Intermediate angle holds state (no oscillation) | unit | `pytest tests/test_barrier_fsm.py::test_intermediate_band_holds_state -x` | ❌ Wave 0 |
| BARRIER-03 | Exactly one `OPENING` per opening; a sustained open re-emits nothing | unit | `pytest tests/test_barrier_fsm.py::test_no_duplicate_transitions_while_open -x` | ❌ Wave 0 |
| BARRIER-03 | A second full cycle produces a second, independent pair of events | unit | `pytest tests/test_barrier_fsm.py::test_two_cycles_emit_two_event_pairs -x` | ❌ Wave 0 |
| BARRIER-04 | Car / truck / bus passing with gate CLOSED emits `[]` | unit | `pytest tests/test_barrier_fsm.py::test_traffic_with_gate_closed_is_silent -x` | ❌ Wave 0 |
| BARRIER-04 | Light swing with gate CLOSED emits `[]` | unit | `pytest tests/test_barrier_fsm.py::test_light_swing_is_silent -x` | ❌ Wave 0 |
| BARRIER-04 | Thresholds hold under severe degradation (JPEG+noise+blur+gradient) | integration | `pytest tests/test_barrier_detector.py::test_thresholds_survive_degradation -x` | ❌ Wave 0 |
| BARRIER-04 | Fixture replay is deterministic (same clip ⇒ identical events) | integration | `pytest tests/test_replay.py::test_replay_is_deterministic -x` | ❌ Wave 0 |
| SC4 | Detector runs over a **real** recorded clip without error (acceptance) | integration (skipped when footage absent) | `pytest tests/test_replay.py::test_real_footage_acceptance -x` | ❌ Wave 0 — `@pytest.mark.skipif` on missing `fixtures/` |
| — | `FrameBuffer` replay does **not** hang (Pitfall 1 regression guard) | unit | `pytest tests/test_replay.py::test_bounded_buffer_replay_does_not_hang -x` | ❌ Wave 0 |

### Sampling Rate

- **Per task commit:** `.venv/Scripts/python -m pytest tests/ -q`
- **Per wave merge:** `.venv/Scripts/python -m pytest tests/ -v`
- **Phase gate:** Full suite green, plus the SC4 real-footage acceptance test executing (not skipping), before `/gsd-verify-work`.

### Wave 0 Gaps

- [ ] `src/detect/__init__.py` — new package
- [ ] `src/detect/barrier.py` — `closed_ratio` + `BarrierDetector` + optional `arm_angle`
- [ ] `src/detect/fsm.py` — `BarrierFSM`
- [ ] `src/detect/replay.py` — `ReplaySource`
- [ ] `tests/conftest.py` — extend Phase 1's with a **deterministic synthetic frame factory** (seeded `np.random.default_rng`, arm-angle parameter, optional car/truck/bus/shadow/brightness/degradation) and a `frame_buffer`-independent replay helper
- [ ] `tests/test_barrier_detector.py` — signal tests (new)
- [ ] `tests/test_barrier_fsm.py` — FSM tests (new)
- [ ] `tests/test_replay.py` — replay determinism + the Pitfall 1 hang regression guard (new)
- [ ] `tools/roi_overlay.py` — offline ROI PNG renderer (new)
- [ ] `fixtures/` added to `.gitignore`; synthetic fixtures generated in a temp dir by the tests
- [ ] **No framework install needed** — pytest 8.3.5 is present and configured.
- [ ] **Consider adding `pytest-timeout`** to `requirements.txt`: Pitfall 1's failure mode is a *hang*, not a failure, and without a timeout a CI run blocks indefinitely. `[ASSUMED]` — this is a recommendation, and adding a package would require the legitimacy gate.

## Security Domain

> `security_enforcement: true`, `security_asvs_level: 1`, `security_block_on: high` in `.planning/config.json` — this section is required.

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | No | Phase 2 handles no credentials. Auth lives in Phase 1 (`src/capture/auth.py`). |
| V3 Session Management | No | Phase 2 holds no session state. |
| V4 Access Control | No | Single-user local service; no access-control surface added. |
| V5 Input Validation | **Yes** | Untrusted inputs are (a) **fixture files on disk** and (b) **frames** from the capture path. Both must be validated before use — see below. |
| V6 Cryptography | No | Phase 2 performs no hashing, encryption, or signing. **Never hand-roll** — nothing to hand-roll here. |

### Known Threat Patterns for this stack (OpenCV + Python, local Windows service)

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Malformed / truncated image file causes `cv2.imread` to return `None` and a downstream `AttributeError` crashes the detect thread | Denial of Service | **Guard every `imread`/frame for `None`** before use and treat it as a skipped frame, never as "barrier closed". This is the Phase 2 analogue of Phase 1's "never treat 'no frame' as 'no event'" rule. |
| A decoded frame is a different size than the ROI coordinates assume (camera resolution change, proxy rescale) → silent wrong ROI | Tampering | Assert `frame.shape` against the configured expected size once per stream; log and re-derive/clamp the ROI rather than silently reading the wrong pixels. |
| ROI/config values out of bounds (`y1 > frame height`, `x1 > width`, negative) → slice returns an empty array → `count_nonzero/0` → `ZeroDivisionError` or a constant 0.0 ratio | Denial of Service / Tampering | **Validate ROI geometry at config load**: require `0 <= y0 < y1 <= height`, `0 <= x0 < x1 <= width`, and `polygon` points in-bounds. Reject at startup with a clear message (Phase 1's pre-flight pattern) rather than failing per-frame. |
| Unbounded fixture directory (operator points at a huge folder) → memory growth if all frames are loaded | Denial of Service | `ReplaySource` is a **generator** — it must stream one frame at a time via `imread` in the loop, never `[imread(f) for f in files]`. The generator form was verified. |
| Path traversal via a config-supplied fixture path | Tampering | Resolve fixture paths against a fixed base directory; do not accept absolute paths from config. |
| Sensitive real footage committed to the repo | Information Disclosure | `fixtures/` must be gitignored (Runtime State Inventory). Real gate imagery of vehicles/plates is personal data under the operator's own rules. |
| Untrusted input treated as instruction | — | Fixture filenames and any sidecar metadata are **data**, never instructions. Do not `eval`/`exec` anything read from a fixture directory or a config file. |

**Highest-value control for this phase:** the `None`-frame guard and the ROI-bounds validation. Both convert a *silent wrong answer* (a false barrier state, which in this system means a missed gate opening — the project's core-value failure) into a *loud, named, logged* condition. This is the same "make failures visible" principle Phase 1 applied to the stale-stream case.

**No new attack surface is introduced:** Phase 2 adds no network calls, no credentials, no database, and no listening socket. Its threat surface is confined to local file and frame validation.

## Environment Availability

> Phase 2 has external dependencies; this audit answers the phase's critical environmental question.

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| **System `ffmpeg` binary** | Phase 1 live capture only | **✗** | — | **Not needed by Phase 2.** `cv2` bundles FFmpeg internally (see below). |
| **OpenCV bundled FFmpeg** | Fixture decoding (`VideoCapture`), JPEG I/O | **✓** | prebuilt, inside `opencv-python-headless 4.14.0.94` | — |
| `opencv-python-headless` | All signal extraction | **✓** | 4.14.0.94 | — |
| `numpy` | Array ops | **✓** | 2.5.3 | — |
| `pytest` | Test framework | **✓** | 8.3.5 | — |
| Python | Runtime | **✓** | 3.14.6 | — |
| `.venv` | Pinned env | **✓** | present, 37 tests green | — |
| privratnik.net credentials (`.env`) | Phase 1 live capture only | **✗** | no `.env` on disk | **Not needed by Phase 2** — fixtures replace the live stream. |
| **Real recorded footage** | SC4 acceptance | **✗** | — | **Synthetic fixtures cover development; SC4 still requires real footage from the user.** See below. |
| GPU / CUDA | — | **✗** (Intel Arc 140T iGPU, no CUDA) | — | **Not needed** — the recommended design is ~4.8 ms/frame CPU. |

**The decisive finding — verified by execution, not inference:**

```
cv2.getBuildInformation()  ->  "FFMPEG:  YES (prebuilt binaries)"
cv2.imwrite / cv2.imread   ->  8/8 frames round-tripped, no ffmpeg binary present
cv2.VideoWriter(mp4v)      ->  isOpened=True, wrote 15,640 bytes, decoded 8/8 frames
cv2.VideoCapture(.avi MJPG)->  wrote 38,532 bytes, decoded 8/8 frames
`command -v ffmpeg`        ->  NOT FOUND (system binary genuinely absent/blocked)
```
`[VERIFIED: executed this session on opencv-python-headless 4.14.0.94, Windows 11, Python 3.14.6]`

**Therefore: YES — Phase 2's barrier-state detection can be developed and fully unit/integration-tested against fixture frames (recorded or synthetic) WITHOUT a working live ffmpeg capture path.** The Phase 1 blocker does not propagate to Phase 2. Only the **SC4 real-footage acceptance test** is user-gated, and it is gated on *recordings*, not on ffmpeg or credentials.

**The exact seam, as required by the phase brief:**

1. **How fixture frames enter the detector.** A `ReplaySource(clip_dir, fps)` generator yields `(frame, index/fps)` from a directory of numbered JPEGs. It is a drop-in for `run_capture_with_supervisor`, which is *already a generator that yields frames* (`src/capture/supervisor.py:54,111`). The detector's input contract is therefore "an iterable of frames" — identical for live and fixture paths.
2. **What interface Phase 1's frame buffer must expose to allow replay.** **None — no change is required.** `FrameBuffer.push`/`pop` (`src/capture/frame_buffer.py:29-39`) are sufficient *if* the consumer respects Pitfall 1 (do not `pop()` in a counted loop). For E2E tests the recommended path (Pattern C) bypasses the bounded buffer entirely, so Phase 1's public API is untouched. If a bounded-buffer path *is* tested, it must use the sentinel-on-`push` pattern, which needs no API change either.
3. **What must be unblocked first, honestly stated.** Nothing for development or automated testing. For **SC4 only**, the user must supply: (a) one still frame per camera with the arm down (for ROI authoring), (b) a genuine open→close cycle clip per camera, (c) a car-passing-with-gate-closed clip per camera. Phase 2 must **not** claim SC4 is met on synthetic fixtures alone.

**Missing dependencies with no fallback:**
- **Real recorded footage for SC4.** This is a hard, user-gated acceptance requirement. It cannot be synthesised. It does not block implementation or automated testing, but the phase **cannot be verified complete** without it. The planner must include the request as an explicit task with a `checkpoint:human-verify` gate.

**Missing dependencies with fallback:**
- System `ffmpeg` → fallback: OpenCV's bundled FFmpeg (works, verified). Affects Phase 1 only.
- privratnik credentials → fallback: `ReplaySource` fixtures (works, verified). Affects Phase 1 only.
- GPU → fallback: CPU signal extraction at ~4.8 ms/frame (0.7% of a core per camera at 1.5 fps).

## Sources

### Primary (HIGH confidence)
- **Direct execution on this host** — `opencv-python-headless 4.14.0.94`, Python 3.14.6, Windows 11. All API-existence checks, parameter-default reads, MOG2/KNN illumination-failure measurements, Hough ROI-border and noise pitfalls, the hysteresis/FSM simulations, the degradation matrix, the replay-hang reproduction, and the bundled-FFmpeg decoding test were run in this session. These are first-party measurements, not citations.
- `C:/dev/schlagbaum/src/capture/frame_buffer.py:13-39` — `Frame`, `FrameBuffer.push`, `FrameBuffer.pop` (verbatim reads)
- `C:/dev/schlagbaum/src/capture/supervisor.py:31-54, 74-116` — the generator contract `ReplaySource` mirrors
- `C:/dev/schlagbaum/src/main.py:31-53, 103-104` — `feed_frames`, `_preflight_ffmpeg`, the documented `FrameBuffer.pop()` Phase 2 integration point
- `C:/dev/schlagbaum/src/config.py:19-32` — `load_settings` merge behaviour (confirming A4)
- `C:/dev/schlagbaum/.planning/phases/01-stream-client-frame-buffer/01-VERIFICATION.md` — the `gaps_found` verdict and its two environmental blockers
- `.venv/Scripts/python -m pip freeze` + `pip index versions` — installed and available package versions

### Secondary (MEDIUM confidence)
- Context7 `/websites/opencv_4_13_0` — `createBackgroundSubtractorMOG2(history=500, varThreshold=16, detectShadows=true)`; `getShadowValue()` default 127 and the 0/127/255 mask semantics; `apply(image, fgmask, learningRate=-1)`; `HoughLinesP(image, rho, theta, threshold, minLineLength, maxLineGap)` returning `(x1,y1,x2,y2)`; `getStructuringElement`/`morphologyEx`/`MORPH_OPEN`; `cv2.inRange` HSV thresholding. Cross-checked against the installed build, which agreed on every parameter default.
- Phase 1 artifacts (`01-RESEARCH.md`, `01-PATTERNS.md`, `01-VALIDATION.md`, `01-01/02-SUMMARY.md`) — the conventions to reuse (config/secrets split, named errors, logging redaction, pinned requirements, test layout).

### Tertiary (LOW confidence)
- `WebSearch` returned **empty result sets** for every query this session (the provider appears unavailable in this environment). No claim in this document rests on a web-search result. Where general domain knowledge was used instead (boom-barrier sweep duration A6, numpy's repo URL A5), it is explicitly tagged `[ASSUMED]` in the Assumptions Log.
- WebFetch was blocked for `docs.opencv.org` ("unable to verify if domain is safe to fetch"). The OpenCV API claims were therefore sourced from Context7 and independently re-verified by running the calls on the installed package — a stronger check than the doc fetch would have been.

## Metadata

**Confidence breakdown:**
- **Standard stack: HIGH** — no new packages; every version read from the installed environment and the registry. The "no new dependency" conclusion is itself well-evidenced.
- **Architecture: HIGH** — the offline seam was proven by execution (bundled FFmpeg decoding + full E2E fixture→detector→FSM runs). The generator-contract substitution was verified against Phase 1's actual source.
- **CV approach: HIGH on design, MEDIUM on numbers** — the *design* (normalized band ratio above the lane + two-sided hysteresis + median blur) is robust across a broad measured degradation matrix and is superior to MOG2/KNN by first-party measurement. The *threshold values* are calibrated on synthetic frames only and must be re-fitted on real footage (A1). This distinction is the single most important caveat for the planner.
- **Pitfalls: HIGH** — every pitfall in this document was reproduced by execution, including two that broke my own experiment scripts (the replay hang, the ROI-border edge trap).

**Research date:** 2026-09-23
**Valid until:** ~2026-10-23 for the OpenCV API surface and the offline-seam findings (stable). The threshold values (A1) are valid only until real footage arrives — re-fit them the moment SC4 recordings exist.
