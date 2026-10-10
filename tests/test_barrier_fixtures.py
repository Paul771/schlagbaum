"""T2 fixture regression: committed real frames replayed through classify().

Plan 02-02 Task 4 (validation level T2). The frames under
``tests/fixtures/barrier/`` are NATIVE 1280x720 snapshots from the two real
cameras (``scripts.build_references`` wrote them and ``index.json``). Each one
goes through the real ``BarrierDetector.classify`` path, so the
``cv2.resize(..., INTER_AREA)`` + Canny pipeline in ``preprocess()`` runs exactly
as in production -- a pre-downscaled fixture would test nothing.

Offline by design: only committed files are read, never ``data/``. There is no
``skipif`` anywhere in this module on purpose: a silent no-op gate is worse than
a red one, so a missing index or reference set must fail collection, not skip.

Expected labels (deterministic single-frame truth only):

    closed      -> CLOSED
    open        -> OPEN
    closed_car  -> CLOSED   (BARRIER-04 on real footage: a vehicle waiting
                             beside a closed arm is not an opening)

Transitional classes (``opening``, ``closing``, ``car_passes_open``,
``night_lighting_change``) have no single correct label for one frame; they are
judged by the T3 full-session replay (``scripts.validate_barrier``). If the
index ever contains one of them this test fails so the omission is noticed.
"""

import json
import os

import cv2
import pytest

from src.detect.barrier import (
    WORK_SIZE,
    BarrierDetector,
    ReferenceSet,
    bucket_for,
    mean_brightness,
)
from src.detect.fsm import BarrierState

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "barrier")
REFERENCES = os.path.join(FIXTURES, "references")

EXPECTED = {
    "closed": BarrierState.CLOSED,
    "open": BarrierState.OPEN,
    "closed_car": BarrierState.CLOSED,
}

# Provisional values mirroring config.json `barrier.*`; recalibrated in 02-02.
MARGIN = 0.02
OPEN_EXTENT_RATIO = 0.64


def _load_index():
    with open(os.path.join(FIXTURES, "index.json"), encoding="utf-8") as handle:
        return json.load(handle)


INDEX = _load_index()
ENTRIES = INDEX["frames"]


@pytest.fixture(scope="module")
def references():
    # The committed copy is the source of truth; `data/` does not exist on a
    # clean clone, so falling back to it would turn this into a silent no-op.
    return ReferenceSet.load(REFERENCES)


@pytest.fixture(scope="module")
def detector(references):
    return BarrierDetector(references, margin=MARGIN,
                           open_extent_ratio=OPEN_EXTENT_RATIO,
                           bucket_threshold=INDEX["bucket_threshold"])


def test_fixture_set_is_not_empty_and_covers_every_reference():
    assert len(ENTRIES) >= 20, "fixture set suspiciously small: %d" % len(ENTRIES)
    covered = {(e["camera_id"], e["bucket"], e["state"]) for e in ENTRIES}
    references = ReferenceSet.load(REFERENCES)
    for (camera_id, bucket, kind) in references.maps:
        assert (camera_id, bucket, kind) in covered, (
            "no fixture frame for reference %s/%s/%s" % (camera_id, bucket, kind))


def test_index_contains_only_deterministic_classes():
    unexpected = sorted({e["state"] for e in ENTRIES} - set(EXPECTED))
    assert not unexpected, (
        "index.json has classes this test does not assert: %s -- assert them "
        "or move them to the T3 replay" % unexpected)


@pytest.mark.parametrize("entry", ENTRIES, ids=lambda e: e["file"])
def test_real_frame_classifies_as_labelled(detector, entry):
    frame = cv2.imread(os.path.join(FIXTURES, entry["file"]), cv2.IMREAD_COLOR)
    assert frame is not None, "unreadable fixture %s" % entry["file"]
    # Native resolution on purpose: preprocess() must do the downscale itself.
    assert frame.shape[:2] == (720, 1280), frame.shape

    bucket = bucket_for(mean_brightness(frame), detector.bucket_threshold)
    assert bucket == entry["bucket"], (
        "%s: brightness now selects bucket %s, index says %s"
        % (entry["file"], bucket, entry["bucket"]))

    result = detector.classify(entry["camera_id"], frame)
    assert result.state is EXPECTED[entry["state"]], (
        "%s (%s %s): got %s %s"
        % (entry["file"], entry["camera_id"], entry["state"],
           result.state.name, result.detail))


def test_references_are_work_size_and_roi_inside_the_frame(references):
    for key, edge_map in references.maps.items():
        assert (edge_map.shape[1], edge_map.shape[0]) == WORK_SIZE, key
    for (camera_id, kind), (x, y, w, h) in references.rois.items():
        assert w > 0 and h > 0, (camera_id, kind)
        assert 0 <= x and x + w <= WORK_SIZE[0], (camera_id, kind, (x, y, w, h))
        assert 0 <= y and y + h <= WORK_SIZE[1], (camera_id, kind, (x, y, w, h))
