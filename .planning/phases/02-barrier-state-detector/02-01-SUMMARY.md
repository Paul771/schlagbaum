---
phase: 02-barrier-state-detector
plan: 01
subsystem: [testing, infra]
tags: [opencv, canny, fsm, edge-matching, reference-frames, dwell, synthetic-fixtures]

# Dependency graph
requires:
  - phase: 01-stream-client-frame-buffer
    provides: "FrameBuffer with camera_id-tagged snapshots, SnapshotPoller, measured ~1.5 fps snapshot feed"
provides:
  - "src/detect/fsm.py — BarrierState, Observation, TransitionEvent, dwell-gated BarrierFSM with neutral UNKNOWN and re-arm guard"
  - "src/detect/barrier.py — preprocess(), bucket_for(), ReferenceSet, BarrierDetector.classify() with margin gate + geometric corroboration"
  - "Synthetic-scene test tier (T1): 29 phase tests, no recordings, no network, no sleeps"
  - "Provisional margin=0.02 and dwell_open=2/dwell_closed=4, to be recalibrated on real recordings in plan 02-02"
affects: [02-barrier-state-detector, 03-event-store]

# Tech tracking
tech-stack:
  added: ["opencv (cv2.connectedComponentsWithStats, cv2.Canny)", "numpy"]
  patterns: ["reference-frame edge matching instead of background subtraction", "dwell confirmation instead of velocity tracking", "injected clock for FSM tests"]

key-files:
  created: [src/detect/__init__.py, src/detect/fsm.py, src/detect/barrier.py, tests/test_barrier_fsm.py, tests/test_barrier_detector.py]
  modified: []

key-decisions:
  - "UNKNOWN is neutral evidence: it neither commits nor clears the pending counter. At ~1.5 fps a 2 s opening is only ~3 frames, so one ambiguous frame must not throw away accumulated evidence."
  - "dwell_open is counted BEYOND reaching OPENING, so committing OPEN needs 1 + dwell_open = 3 consecutive OPEN samples (~2.0 s at 1.5 fps). dwell_open stays at 2."
  - "The _armed re-arm flag is the actual BARRIER-03 duplicate guard, not closed_dwell. OPENING -> CLOSED commits on 1 sample and would otherwise re-arm a second event for the same physical opening."
  - "BARRIER-01's third condition 'частично открыт' is represented by the transitional OPENING / CLOSING states, NOT by a distinct PARTIAL observation. At ~1.5 fps a partially raised arm is not reliably separable from either end state, and the classifier cannot support the distinction. Recorded here as plan 02-01 Task 1 step 7 requires."
  - "A missing (camera, bucket) reference returns UNKNOWN / no_reference_for_bucket. Never a silent fallback to the other lighting bucket — an IR switch must degrade to 'no evidence', not to a wrong state."
  - "margin=0.02 is explicitly PROVISIONAL and is calibrated against real recordings in plan 02-02. Raising it to make a synthetic test pass is forbidden (02-RESEARCH.md escalation rule)."
  - "Additive Gaussian noise is not a valid model of a frame 'far from both references' — it raises both distances while preserving their order."

patterns-established:
  - "src/detect purity: only cv2, numpy and src.detect.fsm may be imported; src.capture is grep-gated to 0 occurrences so the detector stays offline-testable"
  - "Injected clock (time.monotonic) used only to stamp TransitionEvent.at, never for control flow — FSM tests never sleep"
  - "Reference maps persist at WORK_SIZE (320x180); ReferenceSet.load() rejects any map that is not exactly that size"
  - "Synthetic fixtures must be MEASURED, not assumed: gap vs margin is recorded in the test docstring"
  - "Test fixture geometry documented in the module docstring (mast above the band, car below every ROI)"

requirements-completed: [BARRIER-01, BARRIER-02, BARRIER-03, BARRIER-04]

# Metrics
duration: "Tasks 1-2: ~18 min (2026-10-04); Task 3 fixture fix: ~35 min (2026-10-05). Wall clock spans two sessions; active time not tracked separately."
completed: 2026-10-05
---

# Phase 02, Plan 01 Summary

**Dwell-gated barrier FSM plus a reference-frame Canny edge-matching classifier, proven on synthetic scenes with a worst-case class separation of 3.9x the provisional margin.**

## Performance

- **Duration:** Tasks 1-2 ~18 min, Task 3 fix ~35 min (two sessions)
- **Started:** 2026-10-04T22:36:17Z
- **Completed:** 2026-10-05T20:42:40Z
- **Tasks:** 3 of 3
- **Files modified:** 5

## Accomplishments

- `BarrierFSM` delivers CLOSED→OPENING→OPEN→CLOSING with dwell confirmation, exactly one `emits_event=True` per opening, and an `_armed` flag that stops a single misclassified frame from manufacturing a second event.
- `BarrierDetector.classify()` returns CLOSED / OPEN / UNKNOWN from a raw 1280×720 snapshot using per-camera, per-lighting-bucket reference edge maps, a margin gate, and a `connectedComponentsWithStats` geometric guard that rejects a vehicle crossing the lower frame.
- The whole plan shipped with **no recordings and no network**: 84 tests pass, of which 29 are the phase's own FSM/detector tests.
- Plan gates green: `time.sleep` in `src/detect/` = 0, `src.capture` in `src/detect/` = 0, `py_compile` clean.

## Measured validation (synthetic tier T1)

| bucket | scene | d_closed | d_open | gap | vs margin 0.02 |
|--------|-------|----------|--------|-----|----------------|
| day | closed | 0.0000 | 0.0984 | 0.0984 | 4.9× |
| day | open | 0.0779 | 0.0000 | 0.0779 | 3.9× |
| night | closed | 0.0000 | 0.0998 | 0.0998 | 5.0× |
| night | open | 0.0781 | 0.0000 | 0.0781 | 3.9× |

Worst clean gap **0.0779 = 3.9× the margin**; day and night agree to within 0.0002. The fixture before this change managed 1.9×, with night/open at 0.007 — *under* the margin.

This is the synthetic tier only. `02-RESEARCH.md` is explicit that T1 cannot prove SC #2 or SC #4: synthetic scenes contain no IR transition, no dirty lens and no real arm geometry.

## Task Commits

Each task was committed atomically:

1. **Task 1: Dwell-gated barrier FSM** - `587cfde` (feat) — 12 tests green
2. **Task 2: Reference-frame edge-matching classifier** - `52d075a` (feat)
3. **Task 3: Synthetic scenes and detector tests** - `85186ac` (wip, 14/16) → `13be452` (fix, 17/17)

**Plan metadata:** `25cd5e1` (docs: wave-1 verify gate), `4fd281d` (chore: reset GSD auto-chain flag)

## Files Created/Modified

- `src/detect/__init__.py` — package marker
- `src/detect/fsm.py` — `BarrierState`, `Observation`, `TransitionEvent`, `BarrierFSM` with the pending counter, neutral UNKNOWN and the `_armed` re-arm flag
- `src/detect/barrier.py` — `preprocess()`, `mean_brightness()`, `bucket_for()`, `ReferenceSet.save/load`, `BarrierDetector.classify()`
- `tests/test_barrier_fsm.py` — 12 FSM tests on an injected clock
- `tests/test_barrier_detector.py` — 17 detector tests on synthetic scenes

## Decisions Made

1. **UNKNOWN is neutral, not negative.** It leaves the pending counter untouched. Alternative considered — clearing on UNKNOWN — rejected because one ambiguous frame in a 3-frame opening would make the opening undetectable.
2. **`dwell_open` counted beyond OPENING ⇒ 3 total OPEN samples to commit OPEN.** Keeps `dwell_open=2` rather than raising it, because 3 samples ≈ 2.0 s is already the whole duration of a fast opening.
3. **`_armed` re-arm flag over relying on `closed_dwell`.** `OPENING → CLOSED` fires on 1 sample, so `closed_dwell` cannot be the duplicate guard on its own.
4. **No `PARTIAL` observation** — see Deviations; this is the plan-mandated interpretation of BARRIER-01.
5. **Missing bucket ⇒ `UNKNOWN / no_reference_for_bucket`**, never a fallback to the other bucket.
6. **Gaussian noise dropped as the "corrupted frame" model** and replaced by unrecognisable pixels, with noise kept as a positive BARRIER-04 assertion instead.

## Deviations from Plan

### Corrections

**1. [Task 3 assertion 4] The corrupted-frame test did not implement the plan's wording**
- **Found during:** Task 3 (synthetic scenes and detector tests), left red at the `85186ac` save-point
- **Issue:** The plan asks for "a frame **far from both references** ... yields UNKNOWN, not a coin flip". The test used `sigma=90` additive Gaussian noise, which is *not* far from both references: measured, `d_closed` only moved 0.000 → 0.017 while `d_open` held at 0.044, so the frame was recognisably the closed scene and returning CLOSED was correct behaviour. The test asserted something its fixture did not produce.
- **Fix:** Split into `test_frame_far_from_both_references_returns_unknown_not_a_guess` (uniform unrecognisable pixels, gap 0.008 → UNKNOWN) and `test_heavy_gaussian_noise_does_not_invent_an_opening` (sigma=90 stays CLOSED — a BARRIER-04 property).
- **Files modified:** tests/test_barrier_detector.py
- **Verification:** 17/17 detector tests, 84/84 full suite
- **Committed in:** `13be452`

**2. [Task 3] Day/night invariance failed on a fixture defect, not a classifier defect**
- **Found during:** Task 3, `test_day_and_night_render_same_state`
- **Issue:** `ARM_COLOR` was a fixed grey ≈60 luminance, indistinguishable from a 45-luminance night background, so the arm's edges disappeared at night and the night bucket stopped discriminating (night/open gap 0.007 < margin 0.02).
- **Fix:** `ink_for(brightness)` derives the arm/mast fill from scene brightness, keeping ~100 levels of contrast in both buckets (`cv2.Canny(60,160)` needs a ≥60 step to emit an edge at all). Added horizontal background stripes so the edge maps are not ~3% sparse, and narrowed both ROIs onto the band the arm actually sweeps.
- **Files modified:** tests/test_barrier_detector.py
- **Verification:** day and night now measure the same gaps to within 0.0002
- **Committed in:** `13be452`

---

**Total deviations:** 2 corrections (both fixture-side; `src/detect/` untouched — no detector logic changed)
**Impact on plan:** Both corrections move the tests *toward* the plan's stated assertions rather than away from them. No scope creep, no threshold loosening.

## Issues Encountered

- **Vertical background stripes silently destroy the geometric probe.** Vertical texture merges with the raised arm into a full-height connected component inside the gate band, and `_geometry_looks_open` then reads a *closed* frame as raised. Measured as a deliberate control during the fixture work (gap still good at 3.4×, `GEO BROKEN`); the fixture uses horizontal stripes only, and the constraint is recorded in the module docstring.
- **The two failing tests pulled in opposite directions.** Making the fixture more discriminative *raises* the noise gap, so "strengthen the fixture" alone could never have fixed both. Resolved by correcting the noise test's premise rather than by touching `margin`.
- **The day/night comparison could have passed vacuously.** Had the night render drifted over the 60.0 bucket threshold it would have been compared through the day reference and still passed. Added an explicit bucket-routing assertion plus a check that the two buckets hold different maps. Night mean brightness is 56.8 against a threshold of 60 — only ~3 levels of headroom, so this is now asserted directly.

## User Setup Required

None for plan 02-01 — no external service configuration was needed. Plan 02-02 carries its own `user_setup` (the on-site recording session).

## Next Phase Readiness

Plan 02-01 is complete; plan 02-02 (wave 2) is **not started and is hard-blocked**:

- **`data/recordings/` is empty.** Required labels: `closed_idle`, `opening`, `open`, `closing`, `car_passes_gate_closed`, `car_passes_gate_open`, `night_lighting_change`, plus `closed_night` and `open_night` — the night reference bucket cannot be bootstrapped without the last two. ROADMAP SC #4 cannot be satisfied until this exists.
- **Open field decision:** which camera is authoritative for emitting events (`barrier.authoritative_camera`). Two event-emitting FSMs would violate BARRIER-03; plan 02-02 Task 2 wires the flag but no value has been chosen.
- **`margin=0.02` is provisional** and must be recalibrated against the recordings (02-RESEARCH.md step 3) — deliberately NOT raised to satisfy synthetic tests.

---
*Phase: 02-barrier-state-detector*
*Plan: 01*
*Completed: 2026-10-05*

