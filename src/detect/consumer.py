"""Per-camera barrier detection consumer (BARRIER-01/02/03, SC #1).

Each camera gets its own consumer thread. The loop pops a camera-tagged frame
from the bounded buffer, classifies it, feeds that camera's ``BarrierFSM`` and
exposes the resulting state. Nothing is persisted — Phase 3 reads
``current_state`` and ``transitions``.

Two properties are load-bearing:

``pop()`` blocks, so the consumer owns its thread
    The bounded buffer's ``pop()`` is a blocking ``queue.get()`` with no
    timeout. Calling it inline from ``main()`` would stall the whole service
    behind detection, so the consumer runs as a daemon thread and shuts down
    via a shared ``stop_event``. The loop re-checks ``stop_event`` immediately
    *after* each pop, so a frame arriving during shutdown is dropped rather
    than delaying exit.

Exactly one camera emits
    Two cameras each running an emitting FSM would produce two events for one
    physical opening, which is exactly what BARRIER-03 forbids. The
    authoritative camera is named by ``barrier.authoritative_camera``. Every
    other camera still classifies and runs its own FSM — its open transitions
    are counted and logged as diagnostics, never appended to ``transitions``.

Purity: this module imports only the standard library and ``src.detect.fsm`` /
``src.detect.barrier``. It must never import the capture package, so the
detector stays unit-testable without capture, network or recordings.
"""

import logging
import os
import threading

from src.detect.barrier import BarrierDetector, ReferenceSet
from src.detect.fsm import BarrierFSM, BarrierState

__all__ = ["BarrierConsumer", "authoritative_flags", "build_consumers"]

logger = logging.getLogger(__name__)

#: States the peer camera may legitimately hold when ours commits an OPEN.
_PEER_AGREES = (BarrierState.OPEN, BarrierState.OPENING)


class BarrierConsumer:
    """Drain one camera's frame buffer into that camera's detector + FSM."""

    def __init__(self, camera_id, frame_buffer, detector, fsm, stop_event,
                 emits_events=True):
        self.camera_id = camera_id
        self.frame_buffer = frame_buffer
        self.detector = detector
        self.fsm = fsm
        self.stop_event = stop_event
        self.emits_events = bool(emits_events)

        self.current_state = fsm.state
        #: Emitted open events only. Never populated when ``emits_events`` is
        #: False — that is the BARRIER-03 duplicate-event guarantee.
        self.transitions = []
        #: Open transitions seen but deliberately not emitted (non-authoritative).
        self.diagnostic_opens = 0
        #: Cross-camera consistency checks performed at our own open events.
        self.cross_check = 0
        #: How many of those checks disagreed with the peer camera.
        self.disagreements = 0
        #: Frames popped and handed to the detector.
        self.processed = 0
        #: Frames where ``classify()`` raised; the loop carries on regardless.
        self.errors = 0

        self._peer = None
        self._thread = None

    # ------------------------------------------------------------------ wiring

    def attach_peer(self, other):
        """Link the sibling camera's consumer for the disagreement health check."""
        self._peer = other

    @property
    def is_authoritative(self) -> bool:
        return self.emits_events

    @property
    def peer(self):
        """The sibling camera's consumer, if cross-linked."""
        return self._peer

    def start(self):
        """Start the consumer thread. Returns the thread."""
        if self._thread is not None:
            raise RuntimeError("camera=%s consumer already started" % self.camera_id)
        self._thread = threading.Thread(
            target=self.run,
            name="barrier-%s" % self.camera_id,
            daemon=True,
        )
        self._thread.start()
        logger.info("barrier consumer started for camera=%s (authoritative=%s)",
                    self.camera_id, self.emits_events)
        return self._thread

    def join(self, timeout=None):
        if self._thread is not None:
            self._thread.join(timeout)

    # --------------------------------------------------------------- main loop

    def run(self):
        """Pop frames until ``stop_event``. Never raises out of the thread."""
        while not self.stop_event.is_set():
            frame = self.frame_buffer.pop()
            if self.stop_event.is_set():
                break
            self.processed += 1
            self._handle(frame)

    def _handle(self, frame):
        try:
            observation = self.detector.classify(self.camera_id, frame.data)
        except Exception:
            # One malformed snapshot must not stop detection (T-02-08).
            self.errors += 1
            logger.exception("camera=%s classify failed; frame skipped",
                             self.camera_id)
            return

        for event in self.fsm.update(observation):
            if event.emits_event:
                self._on_open_event(event)
        self.current_state = self.fsm.state

    def _on_open_event(self, event):
        if not self.emits_events:
            self.diagnostic_opens += 1
            logger.info(
                "camera=%s barrier OPEN observed but this camera is not "
                "authoritative; counting it as a diagnostic only (BARRIER-03)",
                self.camera_id,
            )
            return

        self.transitions.append(event)
        peer = self._peer
        if peer is None:
            return
        self.cross_check += 1
        if peer.current_state not in _PEER_AGREES:
            self.disagreements += 1
            logger.warning(
                "barrier camera disagreement: camera=%s committed OPEN but "
                "camera=%s is %s",
                self.camera_id, peer.camera_id, peer.current_state.value,
            )


def authoritative_flags(camera_ids, authoritative_camera):
    """Map ``camera_id -> emits_events``. Exactly one camera may be True.

    Accepts a single name or a list so that a config drift toward two
    authoritative cameras fails loudly instead of silently emitting duplicate
    barrier events (BARRIER-03).
    """
    camera_ids = list(camera_ids)
    if authoritative_camera is None:
        names = []
    elif isinstance(authoritative_camera, str):
        names = [authoritative_camera]
    else:
        names = list(authoritative_camera)
    names = [name for name in names if name]

    if len(names) != 1:
        raise ValueError(
            "exactly one camera must be authoritative for barrier events "
            "(BARRIER-03); barrier.authoritative_camera resolved to %r" % (names,)
        )
    if names[0] not in camera_ids:
        raise ValueError(
            "barrier.authoritative_camera=%r is not a configured camera (%r)"
            % (names[0], camera_ids)
        )
    return {camera_id: camera_id == names[0] for camera_id in camera_ids}


def build_consumers(settings, buffers, stop_event):
    """Read the whole ``barrier`` config block and build one consumer per camera.

    Returns ``[]`` when detection is disabled or the references cannot be
    loaded, so capture always continues — detection must never take capture
    down with it.

    ``barrier`` is the single canonical spelling of these knobs. ``02-RESEARCH``
    writes them unprefixed (``margin``, ``dwell_open``, ``dwell_closed``) and
    additionally uses ``barrier_margin`` / ``barrier_rois``; the mapping onto
    this block is recorded in ``02-02-SUMMARY.md``.
    """
    cfg = settings.get("barrier") or {}
    if not cfg.get("enabled", False):
        logger.debug("barrier detection disabled in config")
        return []

    references_dir = cfg.get("references_dir", "data/references")
    rois_file = cfg.get("rois_file", "")
    if rois_file and os.path.dirname(rois_file) != references_dir:
        # ReferenceSet.load(root) always reads <root>/rois.json, so a rois_file
        # pointing elsewhere cannot be honoured — say so rather than ignore it.
        logger.warning(
            "barrier.rois_file=%s does not live under barrier.references_dir=%s; "
            "the loader reads %s/rois.json",
            rois_file, references_dir, references_dir,
        )

    try:
        references = ReferenceSet.load(references_dir)
    except (OSError, ValueError) as exc:
        logger.warning(
            "barrier references unavailable from %s (%s) — run "
            "'python -m scripts.build_references' after recording sessions "
            "exist under data/recordings/; continuing capture without detection",
            references_dir, exc,
        )
        return []

    flags = authoritative_flags(list(buffers), cfg.get("authoritative_camera"))
    consumers = []
    for camera_id, frame_buffer in buffers.items():
        detector = BarrierDetector(
            references,
            margin=float(cfg.get("margin", 0.15)),
            open_extent_ratio=float(cfg.get("open_extent_ratio", 0.6)),
            bucket_threshold=float(cfg.get("bucket_threshold", 60.0)),
        )
        fsm = BarrierFSM(
            dwell_open=int(cfg.get("dwell_open", 2)),
            dwell_closed=int(cfg.get("dwell_closed", 4)),
        )
        consumers.append(
            BarrierConsumer(camera_id, frame_buffer, detector, fsm, stop_event,
                            emits_events=flags[camera_id])
        )

    # Cross-link so an authoritative OPEN can be sanity-checked against its
    # sibling camera — a health signal, never an event.
    for consumer in consumers:
        for other in consumers:
            if other is not consumer:
                consumer.attach_peer(other)
    return consumers
