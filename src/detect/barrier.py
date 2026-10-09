"""Reference-frame edge-matching barrier classifier (BARRIER-01, BARRIER-04).

Why reference frames and not background subtraction: the source is a ~1.5 fps
single-frame snapshot feed (``02-CONTEXT.md`` D-01), so a motion-energy detector
is effectively blind — the scene is static almost all the time. ``MOG2`` is
worse than blind here: it learns a stationary closed arm as background, and a
model initialised while the arm is raised never sees an opening again.

So classification is *positional*, not temporal. Each camera keeps one clean
"closed" and one clean "open" reference per lighting bucket; a frame is
classified by which reference its edge map is closer to. Edges are used rather
than raw pixels because they are invariant to overall brightness, which is what
survives day/night and IR switching.

Purity: this module imports only ``cv2``, ``numpy`` and ``src.detect.fsm``. It
must never import the ``capture`` subpackage — no network, no FrameBuffer, no
settings — so it stays unit-testable in isolation. (The phase gate greps this
directory for the capture import path, so even naming it here is avoided.)

Coordinate note: all ROIs are expressed in ``WORK_SIZE`` (320x180) coordinates,
matching the stored reference maps. The bootstrap script converts the
operator's 1280x720 picks into that space.
"""

import json
import logging
import os
from dataclasses import dataclass

import cv2
import numpy as np

from src.detect.fsm import BarrierState, Observation

__all__ = [
    "WORK_SIZE",
    "LIGHT_BUCKETS",
    "mean_brightness",
    "preprocess",
    "bucket_for",
    "ReferenceSet",
    "BarrierDetector",
    "Observation",
    "BarrierState",
]

logger = logging.getLogger(__name__)

#: Single working resolution (width, height). Reference maps are stored here so
#: the repository stays small and the comparison is exact.
WORK_SIZE = (320, 180)

LIGHT_BUCKETS = ("day", "night")

_CANNY_LOW = 60
_CANNY_HIGH = 160
_MISSING_REFERENCE = "no_reference_for_bucket"


def _as_gray(frame):
    if frame is None or getattr(frame, "size", 0) == 0:
        raise ValueError("empty frame")
    if getattr(frame, "ndim", 2) == 3:
        return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return frame


def mean_brightness(frame) -> float:
    """Mean luminance of a downscaled frame — selects the lighting bucket."""
    small = cv2.resize(_as_gray(frame), WORK_SIZE, interpolation=cv2.INTER_AREA)
    return float(np.mean(small))


def preprocess(frame) -> np.ndarray:
    """Raw BGR snapshot -> uint8 edge map at ``WORK_SIZE``."""
    small = cv2.resize(_as_gray(frame), WORK_SIZE, interpolation=cv2.INTER_AREA)
    blurred = cv2.GaussianBlur(small, (3, 3), 0)
    return cv2.Canny(blurred, _CANNY_LOW, _CANNY_HIGH)


def bucket_for(brightness: float, threshold: float = 60.0) -> str:
    """Pick the lighting bucket for a frame's mean brightness."""
    return "day" if brightness >= threshold else "night"


@dataclass(frozen=True)
class ReferenceSet:
    """Per-camera, per-bucket closed/open reference edge maps plus the ROIs.

    ``maps`` is keyed ``(camera_id, bucket, kind)``; ``rois`` is keyed
    ``(camera_id, kind)`` and holds ``(x, y, w, h)`` in ``WORK_SIZE`` coords.
    """

    maps: dict
    rois: dict

    def has(self, camera_id: str, bucket: str) -> bool:
        return (camera_id, bucket, "closed") in self.maps

    def get(self, camera_id: str, bucket: str, kind: str) -> np.ndarray:
        try:
            return self.maps[(camera_id, bucket, kind)]
        except KeyError:
            raise KeyError(
                "no %s reference for camera=%s bucket=%s" % (kind, camera_id, bucket)
            )

    def roi(self, camera_id: str, kind: str):
        try:
            return self.rois[(camera_id, kind)]
        except KeyError:
            raise KeyError("no %s ROI for camera=%s" % (kind, camera_id))

    def save(self, root: str):
        """Write ``<root>/<camera_id>/<bucket>/{closed,open}.png`` + ``rois.json``."""
        for (camera_id, bucket, kind), edge_map in self.maps.items():
            directory = os.path.join(root, camera_id, bucket)
            os.makedirs(directory, exist_ok=True)
            target = os.path.join(directory, "%s.png" % kind)
            if not cv2.imwrite(target, edge_map):
                raise IOError("failed to write reference %s" % target)
        payload = {"%s|%s" % key: list(value) for key, value in self.rois.items()}
        with open(os.path.join(root, "rois.json"), "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)

    @classmethod
    def load(cls, root: str) -> "ReferenceSet":
        """Inverse of :meth:`save`. Rejects maps that are not ``WORK_SIZE``."""
        maps = {}
        for dirpath, _dirnames, filenames in os.walk(root):
            for filename in filenames:
                kind = os.path.splitext(filename)[0]
                if not filename.endswith(".png") or kind not in ("closed", "open"):
                    continue
                path = os.path.join(dirpath, filename)
                relative = os.path.relpath(path, root).split(os.sep)
                if len(relative) < 3:
                    raise ValueError(
                        "reference %s is not laid out as <camera_id>/<bucket>/<kind>.png"
                        % os.path.relpath(path, root)
                    )
                edge_map = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
                if edge_map is None:
                    raise ValueError("failed to read reference %s" % path)
                if (edge_map.shape[1], edge_map.shape[0]) != WORK_SIZE:
                    raise ValueError(
                        "reference %s is %dx%d, expected %dx%d"
                        % (path, edge_map.shape[1], edge_map.shape[0],
                           WORK_SIZE[0], WORK_SIZE[1])
                    )
                maps[(relative[0], relative[1], kind)] = edge_map
        rois = {}
        with open(os.path.join(root, "rois.json"), encoding="utf-8") as handle:
            for key, value in json.load(handle).items():
                camera_id, kind = key.split("|", 1)
                rois[(camera_id, kind)] = tuple(value)
        return cls(maps=maps, rois=rois)


class BarrierDetector:
    """Classify one snapshot as CLOSED / OPEN / UNKNOWN."""

    def __init__(self, references: ReferenceSet, margin: float = 0.15,
                 open_extent_ratio: float = 0.6, bucket_threshold: float = 60.0):
        self.references = references
        self.margin = float(margin)
        self.open_extent_ratio = float(open_extent_ratio)
        self.bucket_threshold = float(bucket_threshold)

    def _distance(self, edges, camera_id, bucket, kind) -> float:
        x, y, w, h = self.references.roi(camera_id, kind)
        current = edges[y:y + h, x:x + w]
        reference = self.references.get(camera_id, bucket, kind)[y:y + h, x:x + w]
        if current.size == 0 or reference.size == 0:
            return 1.0
        return float(np.mean(np.not_equal(current, reference)))

    def _geometry_looks_open(self, edges, camera_id) -> bool:
        """The arm reads as raised when a tall component fills the gate band.

        Measured inside the closed ROI: when the arm is up, the vertical shaft
        crossing that band is far taller than the flat horizontal bar it
        replaces, so ``max(component height) >= open_extent_ratio * band height``
        separates the two orientations. A vehicle crossing the lower frame
        cannot reach this band, so it cannot spoof the check.
        """
        x, y, w, h = self.references.roi(camera_id, "closed")
        band = (edges[y:y + h, x:x + w] > 0).astype(np.uint8)
        if band.size == 0:
            return False
        count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(
            band, connectivity=8
        )
        if count <= 1:
            return False
        tallest = int(stats[1:, cv2.CC_STAT_HEIGHT].max())
        return tallest >= self.open_extent_ratio * band.shape[0]

    def classify(self, camera_id: str, frame) -> Observation:
        """Classify one raw snapshot. Never guesses: unsure means UNKNOWN."""
        if frame is None or getattr(frame, "size", 0) == 0:
            raise ValueError("empty frame passed to classify(camera=%s)" % camera_id)

        bucket = bucket_for(mean_brightness(frame), self.bucket_threshold)
        if not self.references.has(camera_id, bucket):
            logger.debug("camera=%s has no %s reference; UNKNOWN", camera_id, bucket)
            return Observation(BarrierState.UNKNOWN, 0.0, _MISSING_REFERENCE)

        edges = preprocess(frame)
        d_closed = self._distance(edges, camera_id, bucket, "closed")
        d_open = self._distance(edges, camera_id, bucket, "open")
        gap = abs(d_closed - d_open)
        logger.debug("camera=%s bucket=%s d_closed=%.4f d_open=%.4f gap=%.4f",
                     camera_id, bucket, d_closed, d_open, gap)

        if gap < self.margin:
            return Observation(BarrierState.UNKNOWN, gap, "ambiguous")

        if d_open < d_closed:
            if not self._geometry_looks_open(edges, camera_id):
                return Observation(BarrierState.UNKNOWN, gap, "geometric_mismatch")
            return Observation(BarrierState.OPEN, gap, "")

        return Observation(BarrierState.CLOSED, gap, "")
