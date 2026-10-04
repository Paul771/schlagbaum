"""Tests for the dwell-gated barrier FSM (BARRIER-02, BARRIER-03, ROADMAP SC #3).

No network, no recordings, no sleeping: the clock is injected, so the whole
file runs in milliseconds.

The cases that matter are the ones that break at ~1.5 fps, where a real 2 s
opening is only ~3 frames wide:

* ``UNKNOWN`` must be neutral — it may neither commit nor discard evidence;
* a single misclassified CLOSED frame mid-opening must not re-arm the emitter
  and produce a second open event.
"""

import pytest

from src.detect.fsm import BarrierFSM, BarrierState, Observation


class FakeClock:
    """Monotonic-ish clock that advances only when called."""

    def __init__(self):
        self.t = 0.0

    def __call__(self):
        self.t += 1.0
        return self.t


def obs(state):
    return Observation(state=state, confidence=1.0)


def drive(fsm, states):
    """Feed states; return (all transitions, emitted events)."""
    transitions, emitted = [], []
    for state in states:
        for event in fsm.update(obs(state)):
            transitions.append(event)
            if event.emits_event:
                emitted.append(event)
    return transitions, emitted


@pytest.fixture
def fsm():
    return BarrierFSM(dwell_open=2, dwell_closed=4, clock=FakeClock())


def test_starts_closed_and_armed(fsm):
    assert fsm.state is BarrierState.CLOSED
    assert fsm.armed is True


def test_follows_closed_opening_open_order(fsm):
    transitions, emitted = drive(fsm, [BarrierState.OPEN] * 3)
    order = [(t.from_state, t.to_state) for t in transitions]
    assert order == [
        (BarrierState.CLOSED, BarrierState.OPENING),
        (BarrierState.OPENING, BarrierState.OPEN),
    ]
    assert len(emitted) == 1, "one physical opening must emit exactly one event"
    assert fsm.state is BarrierState.OPEN


def test_dwell_open_counts_samples_beyond_reaching_opening(fsm):
    fsm.update(obs(BarrierState.OPEN))          # -> OPENING
    assert fsm.update(obs(BarrierState.OPEN)) == []
    assert fsm.state is BarrierState.OPENING
    fsm.update(obs(BarrierState.OPEN))          # dwell_open satisfied
    assert fsm.state is BarrierState.OPEN


def test_extra_open_samples_while_open_emit_nothing(fsm):
    drive(fsm, [BarrierState.OPEN] * 3)
    transitions, emitted = drive(fsm, [BarrierState.OPEN] * 5)
    assert transitions == [] and emitted == []


def test_unknown_never_commits_a_state(fsm):
    transitions, _ = drive(fsm, [BarrierState.UNKNOWN] * 10)
    assert transitions == []
    assert fsm.state is BarrierState.CLOSED


def test_unknown_does_not_discard_pending_evidence(fsm):
    """The 1.5 fps survival case: one ambiguous frame must not lose the opening."""
    fsm.update(obs(BarrierState.OPEN))          # -> OPENING
    assert fsm.pending == 0
    fsm.update(obs(BarrierState.OPEN))          # pending 1
    assert fsm.pending == 1
    fsm.update(obs(BarrierState.UNKNOWN))       # neutral: pending survives
    assert fsm.pending == 1, "UNKNOWN must not reset accumulated evidence"
    fsm.update(obs(BarrierState.OPEN))          # pending 2 -> commit
    assert fsm.state is BarrierState.OPEN


def test_closed_dwell_required_before_returning_to_closed(fsm):
    drive(fsm, [BarrierState.OPEN] * 3)
    fsm.update(obs(BarrierState.CLOSED))        # -> CLOSING
    assert fsm.state is BarrierState.CLOSING
    for _ in range(3):
        fsm.update(obs(BarrierState.CLOSED))
        assert fsm.state is BarrierState.CLOSING, "closed_dwell not met yet"
    fsm.update(obs(BarrierState.CLOSED))        # 4th -> CLOSED
    assert fsm.state is BarrierState.CLOSED


def test_open_closing_open_wobble_emits_no_second_event(fsm):
    """Reversal mid-closing returns to OPEN but must not re-emit."""
    drive(fsm, [BarrierState.OPEN] * 3)
    fsm.update(obs(BarrierState.CLOSED))        # -> CLOSING
    transitions, emitted = drive(fsm, [BarrierState.OPEN] * 2)
    assert fsm.state is BarrierState.OPEN
    assert emitted == [], "a wobble must not produce a second open event"
    assert [t.to_state for t in transitions] == [BarrierState.OPEN]


def test_spurious_closed_mid_opening_emits_at_most_one_event(fsm):
    """One misclassified CLOSED frame mid-opening must not re-arm the emitter."""
    fsm.update(obs(BarrierState.OPEN))          # -> OPENING
    fsm.update(obs(BarrierState.CLOSED))        # spurious reversal -> CLOSED
    assert fsm.state is BarrierState.CLOSED
    assert fsm.armed is True
    _, emitted = drive(fsm, [BarrierState.OPEN] * 3)
    assert len(emitted) == 1, "still one event, not two"


def test_full_close_rearms_and_a_new_opening_emits_again(fsm):
    drive(fsm, [BarrierState.OPEN] * 3)
    assert fsm.armed is False
    drive(fsm, [BarrierState.CLOSED] * 5)       # CLOSING + closed_dwell -> CLOSED
    assert fsm.state is BarrierState.CLOSED
    assert fsm.armed is True, "a full close re-arms the emitter"
    _, emitted = drive(fsm, [BarrierState.OPEN] * 3)
    assert len(emitted) == 1, "this is a genuinely new opening"


def test_observation_matches_committed_state_resets_pending(fsm):
    fsm.update(obs(BarrierState.OPEN))          # -> OPENING
    fsm.update(obs(BarrierState.OPEN))          # pending 1
    fsm.update(obs(BarrierState.OPENING))       # equals committed state
    assert fsm.pending == 0


def test_transition_carries_injected_clock_timestamp(fsm):
    events = fsm.update(obs(BarrierState.OPEN))
    assert events[0].at == 1.0
