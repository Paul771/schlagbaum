---
phase: 02-barrier-state-detector
slug: barrier-state-detector
status: draft
nyquist_compliant: true
wave_0_complete: false
created: 2026-10-04
---

# Phase 02 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.3.5 |
| **Config file** | `pytest.ini` (testpaths=tests, pythonpath=.) |
| **Quick run command** | `.venv/Scripts/python -m pytest tests/test_barrier_fsm.py tests/test_barrier_detector.py -q` |
| **Full suite command** | `.venv/Scripts/python -m pytest tests/ -q` |
| **Estimated runtime** | ~2 seconds |

---

## Sampling Rate

- **After every task commit:** Run the quick run command
- **After every plan wave:** Run the full suite command
- **Before `/gsd:verify-work`:** Full suite must be green AND `python -m scripts.validate_barrier.py` must exit 0
- **Max feedback latency:** ~3 seconds (T1/T2); the T3 replay is minutes and runs at most once per wave

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 02-01-01 | 01 | 1 | BARRIER-02, BARRIER-03 | unit | `.venv/Scripts/python -m pytest tests/test_barrier_fsm.py -x` | ❌ W0 | ⬜ pending |
| 02-01-02 | 01 | 1 | BARRIER-01, BARRIER-04 | unit | `.venv/Scripts/python -m pytest tests/test_barrier_detector.py -x` | ❌ W0 | ⬜ pending |
| 02-01-03 | 01 | 1 | BARRIER-01, BARRIER-04 | unit (synthetic) | `.venv/Scripts/python -m pytest tests/ -q` | ✅ | ⬜ pending |
| 02-02-01 | 02 | 2 | BARRIER-01 | integration | `python -m scripts.build_references.py` | ❌ W0 (needs recordings) | ⬜ pending |
| 02-02-02 | 02 | 2 | BARRIER-01 | unit + integration | `.venv/Scripts/python -m pytest tests/test_detect_consumer.py -x` | ❌ W0 | ⬜ pending |
| 02-02-03 | 02 | 2 | BARRIER-04 (SC #2, SC #4) | e2e replay | `.venv/Scripts/python -m scripts.validate_barrier.py` | ❌ W0 (needs recordings) | ⬜ pending |
| 02-02-04 | 02 | 2 | BARRIER-01, BARRIER-04 | regression (real fixtures) | `.venv/Scripts/python -m pytest tests/test_barrier_fixtures.py -x` | ❌ W0 (needs fixtures from task 02-02-01) | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `data/recordings/` contains at least `closed_idle`, `opening`, `open`, `closing`, `car_passes_gate_closed`, `car_passes_gate_open` sessions for both cameras — **blocks plan 02-02 entirely**, produced by `python -m scripts.record_validation.py`
- [ ] `tests/test_barrier_fsm.py` — stubs for BARRIER-02/03 (created in task 02-01-01)
- [ ] `tests/test_barrier_detector.py` — stubs for BARRIER-01/04 (created in task 02-01-03)

*Plan 02-01 is deliberately executable with NO recordings — synthetic scenes only. Only plan 02-02 is gated on the recording session.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Genuine barrier opening is detected | BARRIER-01, SC #4 | Requires a human physically operating the gate | Run `python -m scripts.record_validation --label opening --duration 30` while opening the barrier, then `python -m scripts.validate_barrier.py` |
| Car passing with the gate CLOSED raises no event | BARRIER-04, SC #2 | Requires a human driving a car past a closed gate | `python -m scripts.record_validation --label car_passes_gate_closed --duration 60` while a car passes, then replay |
| Day→night / IR switch does not flip the state | BARRIER-04 | Only realisable at dusk | `python -m scripts.record_validation --label night_lighting_change --duration 60` across the transition, then replay |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references
- [x] No watch-mode flags
- [x] Feedback latency < 3s for T1/T2
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
