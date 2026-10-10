---
status: partial
phase: 02-barrier-state-detector
source: [02-01-SUMMARY.md, 02-02-SUMMARY.md]
started: 2026-10-10T21:15:00Z
updated: 2026-10-10T21:20:00Z
---

## Current Test

number: 5
name: Live opening produces exactly one event per barrier
expected: |
  With the service running (`python -m src.main`, INFO logging), a real car or pedestrian
  opening camera N's barrier logs exactly ONE "camera=cam_N barrier OPEN committed" within
  ~8-16 s of the arm leaving, no second line for the same opening, nothing while the arm is closed.
awaiting: owner at the gate (daylight / next real passage)

## Tests

### 1. Cold start with detection enabled
expected: service boots, both consumers start, no errors, no events while the gate is closed
result: pass
evidence: `python -m src.main` 60 s on 2026-10-10 21:1x — capture threads and "barrier consumer started" for cam_1 and cam_2, no errors (checked by Claude, not owner)

### 2. Replay of all recordings
expected: `python -m scripts.validate_barrier` exits 0 — 0 false openings, 0 missed openings, no duplicates
result: pass
evidence: exit 0 on 14 sessions; staged openings 2+2 events, sunset 3 events for 3 real openings, passive_night 1 for 1 car (Claude, 2026-10-10)

### 3. Test suite and purity gates
expected: full suite green; no `time.sleep` and no capture imports in `src/detect/`
result: pass
evidence: 140 passed; both greps 0 (Claude, 2026-10-10)

### 4. Car waiting at a closed gate is not an opening
expected: closed_car frames (cam_1 #1140-1174, cam_2 #1920-1971 of passive_day__20261010-114411) never commit OPEN
result: pass
evidence: T2 fixtures classify CLOSED and the replay emitted no event at those spans; asserted in tests/test_barrier_fixtures.py

### 5. Live opening produces exactly one event per barrier
expected: see Current Test
result: [pending]

## Summary

total: 5
passed: 4
issues: 0
pending: 1
skipped: 0

## Gaps

[none yet]
