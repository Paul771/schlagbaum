"""Dwell-gated barrier state machine (BARRIER-01/02/03).

The camera source is a ~1.5 fps single-frame snapshot feed, not a video stream,
so a real barrier opening (2-5 s) yields only ~3-8 frames (see
``02-CONTEXT.md`` D-04). This FSM therefore implements **state confirmation by
dwell**, never velocity tracking.

Shape of the loop:

    CLOSED --(1 OPEN sample)--> OPENING --(dwell_open more)--> OPEN
    OPEN --(1 CLOSED sample)--> CLOSING --(dwell_closed more)--> CLOSED

Two properties matter more than they look:

* An ``UNKNOWN`` observation is **neutral**. It neither commits a state nor
  discards the pending count. With only ~3 frames per opening, one ambiguous
  frame must not throw away accumulated evidence — UNKNOWN means "no new
  information", never "information against".
* ``_armed`` is the real duplicate-event guard (BARRIER-03). ``OPENING ->
  CLOSED`` commits on a single sample and so does NOT satisfy ``dwell_closed``;
  it must not re-arm the emitter, or one misclassified frame mid-opening would
  let a second open event fire for the same physical opening. ``_armed`` clears
  only on a full ``CLOSING -> CLOSED`` commit.
"""

import logging
import time
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class BarrierState(str, Enum):
    """Barrier states. ``UNKNOWN`` is a sentinel the FSM never settles into."""

    CLOSED = "closed"
    OPENING = "opening"
    OPEN = "open"
    CLOSING = "closing"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Observation:
    """What the classifier saw in one frame."""

    state: BarrierState
    confidence: float = 0.0
    detail: str = ""


@dataclass(frozen=True)
class TransitionEvent:
    """One FSM state change. ``emits_event`` is True only for a barrier opening."""

    from_state: BarrierState
    to_state: BarrierState
    at: float
    emits_event: bool = False


class BarrierFSM:
    """Confirm barrier state from per-frame observations, with dwell + re-arm."""

    def __init__(self, dwell_open: int = 2, dwell_closed: int = 4,
                 clock=time.monotonic):
        self.dwell_open = max(1, int(dwell_open))
        self.dwell_closed = max(1, int(dwell_closed))
        self.clock = clock
        self._state = BarrierState.CLOSED
        self._pending = 0
        self._armed = True

    @property
    def state(self) -> BarrierState:
        return self._state

    @property
    def armed(self) -> bool:
        """Whether a committed ``OPEN`` may emit a barrier event."""
        return self._armed

    @property
    def pending(self) -> int:
        return self._pending

    def reset(self):
        self._state = BarrierState.CLOSED
        self._pending = 0
        self._armed = True

    def _transition(self, to_state: BarrierState, emits_event: bool):
        event = TransitionEvent(
            from_state=self._state,
            to_state=to_state,
            at=self.clock(),
            emits_event=emits_event,
        )
        logger.debug(
            "barrier transition %s -> %s emits_event=%s", self._state.value,
            to_state.value, emits_event,
        )
        self._state = to_state
        self._pending = 0
        return event

    def update(self, observation: Observation):
        """Feed one observation; return the transitions it caused (0..1)."""
        seen = observation.state

        if seen is BarrierState.UNKNOWN:
            # Neutral: no commit, no reset. See module docstring.
            return []

        if seen is self._state:
            self._pending = 0
            return []

        if self._state is BarrierState.CLOSED:
            if seen is not BarrierState.OPEN:
                return []
            return [self._transition(BarrierState.OPENING, emits_event=False)]

        if self._state is BarrierState.OPENING:
            if seen is BarrierState.OPEN:
                self._pending += 1
                if self._pending < self.dwell_open:
                    return []
                emits = self._armed
                events = [self._transition(BarrierState.OPEN, emits_event=emits)]
                if emits:
                    self._armed = False
                return events
            # Reversal: affirmative CLOSED evidence re-arms nothing.
            return [self._transition(BarrierState.CLOSED, emits_event=False)]

        if self._state is BarrierState.OPEN:
            if seen is not BarrierState.CLOSED:
                return []
            return [self._transition(BarrierState.CLOSING, emits_event=False)]

        # CLOSING
        if seen is BarrierState.CLOSED:
            self._pending += 1
            if self._pending < self.dwell_closed:
                return []
            events = [self._transition(BarrierState.CLOSED, emits_event=False)]
            self._armed = True  # full close re-arms the emitter
            return events
        # Reversal to OPEN mid-closing: no new event, and the gate stays closed.
        return [self._transition(BarrierState.OPEN, emits_event=False)]
