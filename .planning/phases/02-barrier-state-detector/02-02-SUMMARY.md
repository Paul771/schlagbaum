---
phase: 02-barrier-state-detector
plan: 02
subsystem: [detection, validation]
tags: [opencv, references, calibration, dwell, picture-dedup, hysteresis, two-barriers]
requires:
  - phase: 02-barrier-state-detector
    provides: "BarrierFSM, BarrierDetector (plan 02-01)"
provides:
  - "scripts/build_references.py — references, ROIs and committed fixtures from recordings"
  - "src/detect/consumer.py — per-camera consumer with picture dedup, wired into main.py"
  - "scripts/validate_barrier.py — replay of recordings with the runtime gate, measured numbers"
  - "tests/test_barrier_fixtures.py — T2 regression on 40 real native frames"
  - "Calibrated barrier config (margin, dwell in pictures, bucket smoothing)"
affects: [03-event-store]
requirements-completed: [BARRIER-01, BARRIER-02, BARRIER-03, BARRIER-04]
completed: 2026-10-10
---

# Phase 02, Plan 02 Summary

**Real-footage bootstrap and calibration of the barrier detector. Two barriers, one camera each; every camera emits. Dwell counts distinct camera pictures, not frames.**

## What was built

| Task | Result | Commit |
|---|---|---|
| 1 Bootstrap references/ROIs/fixtures | `scripts.build_references`; ROI found by arm colour (HSV H 3-20), 5 fixtures per class | `cba1fa9` |
| 2 Consumer + wiring | one daemon thread per camera; picture dedup before classify | `fcf8ea8` + this session |
| 3 Validation harness | replay with the runtime dedup gate; exit 0/1/2 | `046e183` + this session |
| 4 T2 fixture regression | 40 native frames through real `classify()` | `ce74c23` |

## Findings that changed the design

1. **Geometry was inverted** (`ae7c491`). On these cameras the CLOSED arm is a long thin vertical orange bar through the ROI band; an open arm leaves the band nearly empty. `_arm_present` (tallest component >= 0.64 x band height) turns a would-be OPEN into UNKNOWN `geometric_mismatch`. Real separation: open <= 0.59, closed >= 0.68 (headroom 0.04-0.09, recheck on more data).
2. **Empty frame looks OPEN** — guarded by `no_scene_content` (edge density < 25% of reference -> UNKNOWN).
3. **Cameras refresh the picture every ~8-9 s, day and night**, but are polled ~2x/s, so each picture arrives ~16x. With dwell counted in frames, one picture satisfied any dwell: one physical opening produced 2 events 33 s apart (17:58:26 / 17:58:59) and the sunset session 7 events for 3 openings. Fix: `PictureDedup` (160x90 grey thumbnail, mean abs diff >= 0.8 = new); the consumer and the validator skip repeats, dwell is now counted in distinct pictures (~8 s each).
4. **IR switch** happens at dusk (2026-10-10 about 17:58), mean brightness drops from ~120 to ~100; bucket threshold 110 flickers (single pictures 89..134, headlight flash ~90). Fix: per-camera median over the last 5 pictures plus 5-level hysteresis (`bucket_window`, `bucket_hysteresis`); bucket flips over all recordings 32 -> 2.
5. Night IR lit frames are well above the old threshold 60; `bucket_threshold` is now 110.

## Calibrated config (`config.json` `barrier`)

`margin 0.02` (0.04 doubled UNKNOWN and lost real openings), `open_extent_ratio 0.64`, `dwell_open 1` (OPENING + 1 more picture = 2 consecutive OPEN pictures, >= ~8-16 s; 2 missed both staged openings), `dwell_closed 3` (24 s, insensitive 2..4), `bucket_threshold 110`, `bucket_window 5`, `bucket_hysteresis 5`, `enabled true`. Constructor default `margin` aligned to 0.02.

Mapping: `barrier.*` is the single canonical spelling of the research names (`margin`, `dwell_open`, `dwell_closed`; `barrier_rois` -> `references_dir/rois.json`).

## Measured validation (14 sessions, 1907 distinct pictures, 2026-10-10)

- False openings on closed sessions: **0** (only 0.02 h of closed_idle; night closed sessions 2 x 220 frames) — too little data for a rate.
- Missed openings on `opening` / `open`: **0**; staged openings 2+2 events (one per barrier), sunset session 3 events = 3 real openings (car 17:26, pedestrians 17:31/17:39), `passive_night` 1 event = the one car.
- Passive day: 5 + 1 + 0 + 6 events, the first two sessions match the ~6 cars seen by eye; the last session's 6 were not individually audited.
- UNKNOWN: day 2.7%, night 3.2% of pictures.
- Duplicates within 90 s: **0** across the whole grid.
- Latency: >= 2 consecutive OPEN pictures, i.e. 8-16 s after the arm leaves (inherent to the 8 s refresh). `validate_barrier` still prints latency in raw samples, not seconds.

## Open items

- Closed-context data is small (no night closed_car, one closed_idle); false-opening rate not statistically established. Collect longer closed recordings.
- `open_extent_ratio` headroom is thin.
- cam_1 closed_car edge distance is closer to the open reference (d_closed 0.18-0.21 vs d_open 0.28-0.30); still correct but worth watching.
- Recorder `errors` column does not count proxy failures (seen again in `passive_night`, cam_2 lost ~80 frames).
- Fixtures weigh ~15 MB.
- Latency in seconds is not yet printed by `validate_barrier`.
