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
| 02-01-01 | 01 | 1 | BARRIER-01 | T-02-01, T-02-02 | Path resolved against fixed base; one imread per iteration | integration (tracer) | `.venv/Scripts/python -m pytest tests/test_barrier_detector.py::test_synthetic_clip_drives_detector_end_to_end -x` | ❌ W0 | ⬜ pending |
| 02-01-02 | 01 | 1 | BARRIER-01 | — | N/A | unit | `.venv/Scripts/python -m pytest tests/test_barrier_detector.py::test_closed_ratio_separates_open_closed_partial tests/test_barrier_detector.py::test_closed_ratio_invariant_to_illumination -x` | ❌ W0 | ⬜ pending |
| 02-01-03 | 01 | 1 | BARRIER-01 | T-02-04 | Undecodable frame skipped and named, never treated as a state | unit | `.venv/Scripts/python -m pytest tests/test_barrier_detector.py -x` | ❌ W0 | ⬜ pending |
| 02-02-01 | 02 | 2 | BARRIER-02 | T-02-09 | Confirmation counted in frames; no clock | unit | `.venv/Scripts/python -m pytest tests/test_barrier_fsm.py -x` | ❌ W0 | ⬜ pending |
| 02-02-02 | 02 | 2 | BARRIER-03 | T-02-06 | Edge-only emission; duplicates impossible | unit | `.venv/Scripts/python -m pytest tests/test_barrier_fsm.py::test_no_duplicate_transitions_while_open tests/test_barrier_fsm.py::test_two_cycles_emit_two_event_pairs -x` | ❌ W0 | ⬜ pending |
| 02-02-03 | 02 | 2 | BARRIER-04 | T-02-07, T-02-08 | Bounded excursion and brightness swing stay silent | unit | `.venv/Scripts/python -m pytest tests/test_barrier_fsm.py -x` | ❌ W0 | ⬜ pending |
| 02-03-01 | 03 | 3 | BARRIER-04 | T-02-13 | Bounded-buffer consumer uses a timeout-carrying get | integration | `.venv/Scripts/python -m pytest tests/test_replay.py tests/test_barrier_detector.py::test_thresholds_survive_degradation -x` | ❌ W0 | ⬜ pending |
| 02-03-02 | 03 | 3 | BARRIER-04 | T-02-12 | Out-of-bounds ROI refused at startup | unit | `.venv/Scripts/python -m pytest tests/test_barrier_detector.py -x` | ❌ W0 | ⬜ pending |
| 02-03-03 | 03 | 3 | SC4 | T-02-11, T-02-14 | fixtures/ ignored before recording; absent footage fails, never skips | integration (**runs**, no skip) | `.venv/Scripts/python -m pytest tests/test_replay.py::test_real_footage_acceptance -v` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

*Task IDs match the final PLAN.md assignment: 3 tasks per plan, 9 tasks across the phase. Row 02-03-03 is the SC4 acceptance path — it must be reported PASSED, never SKIPPED; the only sanctioned pre-footage mechanism is an explicit `--deselect` on the command line.*

---

## Wave 0 Requirements

- [ ] `src/detect/barrier.py` — `closed_ratio` + `BarrierDetector` + `validate_roi` + `InvalidFrameError` + `ROIConfigError`
- [ ] `src/detect/fsm.py` — `BarrierFSM`
- [ ] `src/detect/replay.py` — `ReplaySource`
- [ ] ~~`src/detect/__init__.py`~~ — **deliberately NOT created** (planner decision 1): `src/capture/` has no marker file and `pythonpath = .` already makes `src.detect.*` importable. One package convention, not two. The acceptance check is behavioural: the three modules import under `python -m pytest`.
- [ ] `tests/conftest.py` — extend Phase 1's with a deterministic synthetic frame factory (seeded `np.random.default_rng`, arm-angle parameter, optional brightness/degradation) plus a `synthetic_clip` helper that writes real JPEG files to a temp dir, independent of `FrameBuffer`
- [ ] `tests/test_barrier_detector.py` — signal tests + the end-to-end tracer test (new)
- [ ] `tests/test_barrier_fsm.py` — FSM tests (new)
- [ ] `tests/test_replay.py` — replay determinism + the bounded-buffer hang regression guard (new)
- [ ] `tools/roi_overlay.py` — offline ROI PNG renderer (new)
- [ ] `fixtures/` added to `.gitignore`; synthetic fixtures generated into a temp dir by tests
- [ ] `config.json` — `rois` and `barrier` keys; startup ROI-bounds validation in `src/main.py`
- [ ] `pytest.ini` — built-in `faulthandler_timeout = 30` (diagnostic only; **no `pytest-timeout` dependency added** — planner decision 2). The Pitfall 1 hang guard is structural, not timeout-based.
- [ ] **No framework install needed** — pytest 8.3.5 is present and configured

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Detector states match a genuine barrier opening on real footage | SC1, BARRIER-01, SC4 | Live capture is blocked on this host (corporate group policy blocks ffmpeg; no privratnik.net credentials), and thresholds are calibrated on *synthetic* frames only. Real footage must be recorded by the operator and cannot be synthesised. | 1. Operator records two clips per camera on a machine that can reach the cameras: (a) a genuine full opening+closing cycle, (b) a car passing with the gate closed. 2. Place them under `fixtures/real/<camera>/` — **after** confirming `git check-ignore` covers that path. 3. Run `.venv/Scripts/python -m pytest tests/test_replay.py::test_real_footage_acceptance -v` — it must **run**, not skip. 4. Compare the emitted transition sequence against the clip by eye. Carried by plan 02-03 Task 3, a `checkpoint:human-verify` with `gate="blocking-human"`. |
| Operator can author a correct ROI without a live feed | BARRIER-01, BARRIER-04 | The ROI must be drawn over the real camera view; there is no live stream available to the agent. | 1. Capture one still frame per camera. 2. Draw the ROI polygon for the arm sweep region. 3. Render it with `python tools/roi_overlay.py --frame <still> --roi <json>` and inspect the overlay PNG before committing the ROI into `config.json`. |
| `capture_fps=1.5` samples a real arm sweep finely enough | SC1, BARRIER-02 | Depends on the real arm's sweep duration, which is unknown until SC4 footage exists. | After SC4 footage is available, count frames between the arm leaving the seat and reaching the top; if fewer than ~4 samples, raise `capture_fps` and re-fit `confirm`. |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 5s
- [ ] SC4 acceptance test reported PASSED (not SKIPPED) with real footage present
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
