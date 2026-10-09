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

Every camera emits
    There are two physical barriers and each camera watches its own one
    (owner-confirmed on 2026-10-06). One camera per barrier means one FSM per
    barrier, so a physical opening cannot be double-reported across cameras;
    within a camera the FSM's re-arm flag is the duplicate-event guard
    (BARRIER-03). The earlier "exactly one authoritative camera" design
    assumed both cameras watch the same barrier and was removed as a
    recorded deviation.

Purity: this module imports only the standard library and ``src.detect.fsm`` /
``src.detect.barrier``. It must never import the capture package, so the
detector stays unit-testable without capture, network or recordings.
"""

import logging
import os
import threading

from src.detect.barrier import BarrierDetector, ReferenceSet
from src.detect.fsm import BarrierFSM

__all__ = ["BarrierConsumer", "build_consumers"]

logger = logging.getLogger(__name__)


class BarrierConsumer:
    """Drain one camera's frame buffer into that camera's detector + FSM."""

    def __init__(self, camera_id, frame_buffer, detector, fsm, stop_event):
        self.camera_id = camera_id
        self.frame_buffer = frame_buffer
        self.detector = detector
        self.fsm = fsm
        self.stop_event = stop_event

        self.current_state = fsm.state
        #: Open events emitted for this camera's own barrier, read by Phase 3.
        self.transitions = []
        #: Frames popped and handed to the detector.
        self.processed = 0
        #: Frames where ``classify()`` raised; the loop carries on regardless.
        self.errors = 0

        self._thread = None

    # ------------------------------------------------------------------ wiring

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
        logger.info("barrier consumer started for camera=%s", self.camera_id)
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
        self.transitions.append(event)
        logger.info("camera=%s barrier OPEN committed", self.camera_id)


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
            BarrierConsumer(camera_id, frame_buffer, detector, fsm, stop_event)
        )
    return consumers
