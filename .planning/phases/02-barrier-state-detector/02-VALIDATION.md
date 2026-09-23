---
phase: "2"
slug: "barrier-state-detector"
# status lifecycle: draft (seeded by plan-phase) → validated (set by validate-phase §6)
# audit-milestone §5.5 distinguishes NOT-VALIDATED (draft) from PARTIAL (validated + nyquist_compliant: false) (#2117)
status: draft
nyquist_compliant: false
wave_0_complete: false
created: "2026-09-23"
---

# Phase 2 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | `pytest` 8.3.5 (installed; 37 tests currently green) |
| **Config file** | `pytest.ini` — `testpaths = tests`, `pythonpath = .` |
| **Quick run command** | `.venv/Scripts/python -m pytest tests/ -q` |
| **Full suite command** | `.venv/Scripts/python -m pytest tests/ -v` |
| **Estimated runtime** | ~2 seconds (baseline 37 tests in 1.22 s) |

---

## Sampling Rate

- **After every task commit:** Run `.venv/Scripts/python -m pytest tests/ -q`
- **After every plan wave:** Run `.venv/Scripts/python -m pytest tests/ -v`
- **Before `/gsd-verify-work`:** Full suite must be green, **and** the SC4 real-footage acceptance test must execute rather than skip
- **Max feedback latency:** 5 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 02-01-01 | 01 | 1 | BARRIER-01 | — | N/A | unit | `pytest tests/test_barrier_detector.py::test_closed_ratio_separates_open_closed_partial -x` | ❌ W0 | ⬜ pending |
| 02-01-02 | 01 | 1 | BARRIER-01 | — | N/A | unit | `pytest tests/test_barrier_detector.py::test_closed_ratio_invariant_to_illumination -x` | ❌ W0 | ⬜ pending |
| 02-02-01 | 02 | 2 | BARRIER-02 | — | N/A | unit | `pytest tests/test_barrier_fsm.py::test_full_cycle_transitions -x` | ❌ W0 | ⬜ pending |
| 02-02-02 | 02 | 2 | BARRIER-02 | — | N/A | unit | `pytest tests/test_barrier_fsm.py::test_intermediate_band_holds_state -x` | ❌ W0 | ⬜ pending |
| 02-02-03 | 02 | 2 | BARRIER-03 | — | N/A | unit | `pytest tests/test_barrier_fsm.py::test_no_duplicate_transitions_while_open -x` | ❌ W0 | ⬜ pending |
| 02-02-04 | 02 | 2 | BARRIER-03 | — | N/A | unit | `pytest tests/test_barrier_fsm.py::test_two_cycles_emit_two_event_pairs -x` | ❌ W0 | ⬜ pending |
| 02-02-05 | 02 | 2 | BARRIER-04 | — | N/A | unit | `pytest tests/test_barrier_fsm.py::test_traffic_with_gate_closed_is_silent -x` | ❌ W0 | ⬜ pending |
| 02-02-06 | 02 | 2 | BARRIER-04 | — | N/A | unit | `pytest tests/test_barrier_fsm.py::test_light_swing_is_silent -x` | ❌ W0 | ⬜ pending |
| 02-03-01 | 03 | 3 | BARRIER-04 | — | N/A | integration | `pytest tests/test_barrier_detector.py::test_thresholds_survive_degradation -x` | ❌ W0 | ⬜ pending |
| 02-03-02 | 03 | 3 | BARRIER-04 | — | N/A | integration | `pytest tests/test_replay.py::test_replay_is_deterministic -x` | ❌ W0 | ⬜ pending |
| 02-03-03 | 03 | 3 | — | — | N/A | unit | `pytest tests/test_replay.py::test_bounded_buffer_replay_does_not_hang -x` | ❌ W0 | ⬜ pending |
| 02-03-04 | 03 | 3 | SC4 | — | N/A | integration (skips when footage absent) | `pytest tests/test_replay.py::test_real_footage_acceptance -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

*Task IDs are indicative — the planner assigns the final IDs and must keep this map consistent with PLAN.md.*

---

## Wave 0 Requirements

- [ ] `src/detect/__init__.py` — new package
- [ ] `src/detect/barrier.py` — `closed_ratio` + `BarrierDetector` + optional `arm_angle`
- [ ] `src/detect/fsm.py` — `BarrierFSM`
- [ ] `src/detect/replay.py` — `ReplaySource`
- [ ] `tests/conftest.py` — extend Phase 1's with a deterministic synthetic frame factory (seeded `np.random.default_rng`, arm-angle parameter, optional car/truck/bus/shadow/brightness/degradation) plus a replay helper independent of `FrameBuffer`
- [ ] `tests/test_barrier_detector.py` — signal tests (new)
- [ ] `tests/test_barrier_fsm.py` — FSM tests (new)
- [ ] `tests/test_replay.py` — replay determinism + the bounded-buffer hang regression guard (new)
- [ ] `tools/roi_overlay.py` — offline ROI PNG renderer (new)
- [ ] `fixtures/` added to `.gitignore`; synthetic fixtures generated into a temp dir by tests
- [ ] **No framework install needed** — pytest 8.3.5 is present and configured

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Detector states match a genuine barrier opening on real footage | SC1, BARRIER-01 | Live capture is blocked on this host (corporate group policy blocks ffmpeg; no privratnik.net credentials), and thresholds are calibrated on *synthetic* frames only. Real footage must be recorded by the operator and cannot be synthesised. | 1. Operator records two clips per camera with `ffmpeg`/phone on another machine: (a) a genuine full opening+closing cycle, (b) a car passing with the gate closed. 2. Place them under `fixtures/real/<camera>/`. 3. Run `.venv/Scripts/python -m pytest tests/test_replay.py::test_real_footage_acceptance -v` — it must **run**, not skip. 4. Compare the emitted transition sequence against the clip by eye. |
| Operator can author a correct ROI without a live feed | BARRIER-01, BARRIER-04 | The ROI must be drawn over the real camera view; there is no live stream available to the agent. | 1. Capture one still frame per camera. 2. Draw the ROI polygon for the arm sweep region. 3. Render it with `python tools/roi_overlay.py --frame <still> --roi <json>` and inspect the overlay PNG before committing the ROI into `config.json`. |
| `capture_fps=1.5` samples a real arm sweep finely enough | SC1, BARRIER-02 | Depends on the real arm's sweep duration, which is unknown until SC4 footage exists. | After SC4 footage is available, count frames between the arm leaving the seat and reaching the top; if fewer than ~4 samples, raise `capture_fps` and re-fit `confirm`. |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 5s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
