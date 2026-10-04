"""Tests for the reference-frame barrier classifier (BARRIER-01, BARRIER-04).

Synthetic ``cv2`` scenes stand in for footage so the classifier's logic is
exercised with no recording, no network and no sleeping. Validation against
REAL frames is a later tier (see ``02-RESEARCH.md`` Validation Architecture) —
these tests prove the logic, not the field calibration.

Fixture geometry (1280x720), chosen so the geometric probe is unambiguous:

    mast     x 100..120,  y 60..240     (thin, deliberately ABOVE the gate band)
    arm      horizontal y 388..412  when closed
             vertical   y 250..410  when open
    car      y 540..690  (lower third, below every ROI)

The mast sits above the band so the tallest connected component inside the band
belongs to the arm alone. The car sits below every ROI so it cannot move either
distance — which is the whole point of the BARRIER-04 regression test.
"""

import os

import cv2
import numpy as np
import pytest

from src.detect.barrier import (
    WORK_SIZE,
    BarrierDetector,
    ReferenceSet,
    bucket_for,
    mean_brightness,
    preprocess,
)
from src.detect.fsm import BarrierState

FRAME_W, FRAME_H = 1280, 720
DAY_BRIGHTNESS = 190
NIGHT_BRIGHTNESS = 45

MAST_BOX = (100, 60, 20, 180)
ARM_CLOSED_BOX = (110, 388, 790, 24)
ARM_OPEN_BOX = (96, 250, 38, 160)
CAR_BOX = (300, 540, 550, 150)
GATE_BAND = (80, 250, 860, 250)       # the "closed" ROI — also the geometry probe
PIVOT_REGION = (60, 200, 260, 280)    # the "open" ROI

MAST_COLOR = (190, 190, 190)
ARM_COLOR = (40, 40, 220)
CAR_COLOR = (70, 70, 70)
WINDOW_COLOR = (150, 150, 150)


def to_work(box):
    """Convert a 1280x720 box into WORK_SIZE coordinates."""
    x, y, w, h = box
    return (int(x * WORK_SIZE[0] / FRAME_W), int(y * WORK_SIZE[1] / FRAME_H),
            max(1, int(w * WORK_SIZE[0] / FRAME_W)),
            max(1, int(h * WORK_SIZE[1] / FRAME_H)))


def make_scene(state="closed", brightness=DAY_BRIGHTNESS, car=False):
    """Render a synthetic gate scene as a 1280x720 BGR frame."""
    gradient = np.linspace(0, 20, FRAME_H, dtype=np.float32)[:, None]
    base = np.full((FRAME_H, FRAME_W), float(brightness), dtype=np.float32)
    frame = np.clip(base + gradient, 0, 255).astype(np.uint8)
    frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)

    cv2.rectangle(frame, (MAST_BOX[0], MAST_BOX[1]),
                  (MAST_BOX[0] + MAST_BOX[2], MAST_BOX[1] + MAST_BOX[3]), MAST_COLOR, -1)
    arm = ARM_CLOSED_BOX if state == "closed" else ARM_OPEN_BOX
    cv2.rectangle(frame, (arm[0], arm[1]), (arm[0] + arm[2], arm[1] + arm[3]), ARM_COLOR, -1)
    if car:
        cv2.rectangle(frame, (CAR_BOX[0], CAR_BOX[1]),
                      (CAR_BOX[0] + CAR_BOX[2], CAR_BOX[1] + CAR_BOX[3]), CAR_COLOR, -1)
        cv2.rectangle(frame, (CAR_BOX[0] + 120, CAR_BOX[1] - 40),
                      (CAR_BOX[0] + 330, CAR_BOX[1]), WINDOW_COLOR, -1)
    return frame


@pytest.fixture(scope="module")
def references():
    """Reference maps for BOTH buckets, built through the real preprocess()."""
    maps = {}
    for bucket, brightness in (("day", DAY_BRIGHTNESS), ("night", NIGHT_BRIGHTNESS)):
        maps[("cam_1", bucket, "closed")] = preprocess(make_scene("closed", brightness))
        maps[("cam_1", bucket, "open")] = preprocess(make_scene("open", brightness))
    rois = {("cam_1", "closed"): to_work(GATE_BAND),
            ("cam_1", "open"): to_work(PIVOT_REGION)}
    return ReferenceSet(maps=maps, rois=rois)


@pytest.fixture
def detector(references):
    # margin is provisional and deliberately small: edge maps are sparse
    # (~3% of pixels), so inter-class distances land around 0.04. It is
    # calibrated against real recordings in plan 02-02.
    return BarrierDetector(references, margin=0.02, open_extent_ratio=0.6)

def test_preprocess_returns_work_size_edge_map():
    edges = preprocess(make_scene())
    assert edges.shape == (WORK_SIZE[1], WORK_SIZE[0])
    assert set(np.unique(edges)).issubset({0, 255})


def test_bucket_for_routes_by_brightness():
    assert bucket_for(190.0) == "day"
    assert bucket_for(45.0) == "night"
    assert bucket_for(45.0, threshold=20.0) == "day"


def test_day_and_night_scenes_land_in_expected_buckets():
    assert mean_brightness(make_scene(brightness=DAY_BRIGHTNESS)) >= 60.0
    assert mean_brightness(make_scene(brightness=NIGHT_BRIGHTNESS)) < 60.0


def test_closed_scene_classifies_closed(detector):
    result = detector.classify("cam_1", make_scene("closed"))
    assert result.state is BarrierState.CLOSED, result.detail


def test_open_scene_classifies_open(detector):
    result = detector.classify("cam_1", make_scene("open"))
    assert result.state is BarrierState.OPEN, result.detail


def test_car_crossing_lower_frame_does_not_flip_state(detector):
    """BARRIER-04: a vehicle passing with the gate CLOSED is not an opening."""
    quiet = detector.classify("cam_1", make_scene("closed"))
    busy = detector.classify("cam_1", make_scene("closed", car=True))
    assert busy.state is BarrierState.CLOSED, busy.detail
    assert busy.state is quiet.state


def test_day_and_night_render_same_state(detector):
    for state in ("closed", "open"):
        day = detector.classify("cam_1", make_scene(state, DAY_BRIGHTNESS))
        night = detector.classify("cam_1", make_scene(state, NIGHT_BRIGHTNESS))
        assert day.state is night.state is BarrierState(state), (
            state, day.detail, night.detail)


def test_corrupted_frame_returns_unknown_not_a_guess(detector):
    rng = np.random.default_rng(0)
    noisy = make_scene("closed").astype(np.int16)
    noisy = np.clip(noisy + rng.normal(0, 90, noisy.shape), 0, 255).astype(np.uint8)
    result = detector.classify("cam_1", noisy)
    assert result.state is BarrierState.UNKNOWN
    assert result.detail in ("ambiguous", "geometric_mismatch")


def test_missing_reference_bucket_returns_unknown(references):
    day_only = ReferenceSet(
        maps={k: v for k, v in references.maps.items() if k[1] == "day"},
        rois=references.rois,
    )
    detector = BarrierDetector(day_only, margin=0.05)
    result = detector.classify("cam_1", make_scene("closed", NIGHT_BRIGHTNESS))
    assert result.state is BarrierState.UNKNOWN
    assert result.detail == "no_reference_for_bucket"


def test_geometric_mismatch_is_reported_when_arm_still_horizontal(references):
    strict = BarrierDetector(references, margin=0.02, open_extent_ratio=0.99)
    result = strict.classify("cam_1", make_scene("open"))
    assert result.state is BarrierState.UNKNOWN
    assert result.detail == "geometric_mismatch"


def test_reference_set_save_load_round_trip(references, tmp_path):
    references.save(str(tmp_path))
    loaded = ReferenceSet.load(str(tmp_path))
    assert sorted(loaded.maps) == sorted(references.maps)
    for key, original in references.maps.items():
        assert np.array_equal(loaded.maps[key], original)
    assert loaded.rois == references.rois


def test_reference_set_load_rejects_wrong_size(references, tmp_path):
    references.save(str(tmp_path))
    bad = tmp_path / "cam_1" / "day" / "closed.png"
    assert cv2.imwrite(str(bad), np.zeros((720, 1280), dtype=np.uint8))
    with pytest.raises(ValueError, match="expected 320x180"):
        ReferenceSet.load(str(tmp_path))


def test_classify_rejects_empty_frame(detector):
    with pytest.raises(ValueError):
        detector.classify("cam_1", np.zeros((0, 0, 3), dtype=np.uint8))


def test_unknown_camera_returns_unknown_rather_than_guessing(detector):
    """A camera with no references must never be classified, only UNKNOWN."""
    result = detector.classify("cam_9", make_scene("closed"))
    assert result.state is BarrierState.UNKNOWN
    assert result.detail == "no_reference_for_bucket"


def test_class_distance_separation_clears_the_margin(detector):
    """Documents the measured separation on these scenes.

    Edge maps are sparse (~3% of pixels), so inter-class distances are small in
    absolute terms: measured gap is 0.044 for a closed scene and 0.037 for an
    open one, against a provisional margin of 0.02. The headroom over the
    margin is therefore roughly 1.9x — thin, and exactly the weakness recorded
    in the 02-01 handoff. The requirement asserted here is the one the
    classifier actually depends on: the correct class must clear the margin.
    """
    for state in ("closed", "open"):
        frame = make_scene(state)
        bucket = bucket_for(mean_brightness(frame), detector.bucket_threshold)
        edges = preprocess(frame)
        gap = abs(detector._distance(edges, "cam_1", bucket, "closed")
                  - detector._distance(edges, "cam_1", bucket, "open"))
        assert gap > detector.margin, (state, gap, detector.margin)


def test_detector_module_has_no_capture_dependency():
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                        "src", "detect", "barrier.py")
    with open(path, encoding="utf-8") as handle:
        assert "src.capture" not in handle.read()
