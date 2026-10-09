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

Two properties of the render are measured, not decorative:

``ink_for(brightness)``
    The arm/mast fill is derived from the scene brightness. With a fixed colour
    the arm sank into a 45-luminance night frame, its edges disappeared, and the
    night bucket stopped discriminating at all (night/open gap 0.007 against a
    0.02 margin — the day/night test failed on a fixture bug, not on the
    classifier). Day and night now measure the same gaps.

Horizontal background stripes
    Edge maps without texture are ~3% dense and the two classes land closer
    together than the margin. Stripes must run HORIZONTALLY: vertical ones merge
    with the raised arm into a full-height connected component inside the gate
    band, and the geometric probe then reads every closed frame as raised.
    Measured on this fixture: worst clean gap 0.078 = 3.9x the 0.02 margin.
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
# Both ROIs are narrowed onto the band the arm actually sweeps (y 245..420).
# The old, looser boxes left a lot of background that is identical in both
# classes and therefore only diluted the mean that becomes `gap`.
GATE_BAND = (80, 245, 860, 175)       # the "closed" ROI — also the geometry probe
PIVOT_REGION = (60, 245, 260, 175)    # the "open" ROI

TEXTURE_PERIOD = 40

CAR_COLOR = (70, 70, 70)
WINDOW_COLOR = (150, 150, 150)


def to_work(box):
    """Convert a 1280x720 box into WORK_SIZE coordinates."""
    x, y, w, h = box
    return (int(x * WORK_SIZE[0] / FRAME_W), int(y * WORK_SIZE[1] / FRAME_H),
            max(1, int(w * WORK_SIZE[0] / FRAME_W)),
            max(1, int(h * WORK_SIZE[1] / FRAME_H)))


def ink_for(brightness, delta=100):
    """Fill luminance that keeps ~`delta` of contrast against `brightness`.

    ``cv2.Canny(blurred, 60, 160)`` only emits an edge where the gradient
    reaches 60, so a fixed fill reads fine against a day frame (190) and sinks
    into a 45-luminance night frame until the arm disappears entirely. When
    `brightness` is too high to gain `delta`, subtract it instead.
    """
    return int(brightness + delta) if brightness + delta <= 255 else int(brightness - delta)


def make_scene(state="closed", brightness=DAY_BRIGHTNESS, car=False):
    """Render a synthetic gate scene as a 1280x720 BGR frame."""
    gradient = np.linspace(0, 20, FRAME_H, dtype=np.float32)[:, None]
    base = np.full((FRAME_H, FRAME_W), float(brightness), dtype=np.float32)

    # Horizontal background stripes. Real footage is never edge-free; without
    # texture the edge maps are ~3% dense and the two classes end up closer
    # together than the margin. Rows, never columns — see the module docstring
    # for what vertical stripes do to the geometric probe.
    rows = np.arange(FRAME_H, dtype=np.float32)[:, None]
    amplitude = 0.8 * min(brightness, 255 - brightness)
    stripes = (((rows // TEXTURE_PERIOD) % 2) * 2 - 1) * amplitude

    frame = np.clip(base + gradient + stripes, 0, 255).astype(np.uint8)
    frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)

    ink = (ink_for(brightness),) * 3
    cv2.rectangle(frame, (MAST_BOX[0], MAST_BOX[1]),
                  (MAST_BOX[0] + MAST_BOX[2], MAST_BOX[1] + MAST_BOX[3]), ink, -1)
    arm = ARM_CLOSED_BOX if state == "closed" else ARM_OPEN_BOX
    cv2.rectangle(frame, (arm[0], arm[1]), (arm[0] + arm[2], arm[1] + arm[3]), ink, -1)
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
    # margin is provisional and deliberately small; it is calibrated against
    # real recordings in plan 02-02. On the current synthetic fixture the
    # worst clean gap is 0.078 (3.9x this margin) — see the measured-separation
    # test below for how that is asserted.
    return BarrierDetector(references, margin=0.02, open_extent_ratio=0.6)

def test_preprocess_returns_work_size_edge_map():
    edges = preprocess(make_scene())
    assert edges.shape == (WORK_SIZE[1], WORK_SIZE[0])
    assert set(np.unique(edges)).issubset({0, 255})


def test_bucket_for_routes_by_brightness():
    assert bucket_for(190.0) == "day"
    assert bucket_for(45.0) == "night"
    assert bucket_for(45.0, threshold=20.0) == "day"


def test_day_and_night_scenes_land_in_expected_buckets(references):
    """Guards the day/night comparison against a vacuous pass.

    A night render that drifted over the 60.0 bucket threshold would be
    classified through the *day* reference, and
    ``test_day_and_night_render_same_state`` would still pass — comparing day
    with day. Measured means today are 196.1 (day) and 56.8 (night), so the
    night scene clears the threshold by only ~3 levels; assert the routing and
    the fact that the two buckets really hold different maps.
    """
    day = make_scene(brightness=DAY_BRIGHTNESS)
    night = make_scene(brightness=NIGHT_BRIGHTNESS)
    assert bucket_for(mean_brightness(day)) == "day"
    assert bucket_for(mean_brightness(night)) == "night"
    assert not np.array_equal(references.maps[("cam_1", "day", "closed")],
                              references.maps[("cam_1", "night", "closed")])


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


def test_frame_far_from_both_references_returns_unknown_not_a_guess(detector):
    """Plan assertion 4: a frame FAR from both references must not be a coin flip.

    The corruption has to actually destroy the structure. Additive Gaussian
    noise does not, so this fixture deliberately uses unrecognisable pixels
    instead: measured on the old fixture at sigma=90 the frame stayed far
    closer to the closed reference than to the open one (d_closed 0.000 ->
    0.017 while d_open held at 0.044), i.e. it was NOT far from both references,
    and returning CLOSED for it was correct. See the complementary test below.
    """
    junk = np.random.default_rng(7).integers(0, 256, (FRAME_H, FRAME_W, 3),
                                             dtype=np.uint8)
    result = detector.classify("cam_1", junk)
    assert result.state is BarrierState.UNKNOWN, result
    assert result.detail in ("ambiguous", "geometric_mismatch")


def test_heavy_gaussian_noise_does_not_invent_an_opening(detector):
    """BARRIER-04: a noisy CLOSED frame stays CLOSED — noise is not an opening.

    The other half of the assertion above. Noise perturbs BOTH distances while
    roughly preserving their order, so it must never flip the state; only a
    frame with no recognisable structure at all has to be refused. Measured on
    this fixture: sigma=90 leaves gap at ~0.05, comfortably over the 0.02
    margin, with d_closed still the nearer reference.
    """
    rng = np.random.default_rng(0)
    noisy = make_scene("closed").astype(np.int16)
    noisy = np.clip(noisy + rng.normal(0, 90, noisy.shape), 0, 255).astype(np.uint8)
    result = detector.classify("cam_1", noisy)
    assert result.state is BarrierState.CLOSED, result


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

    Measured on the current fixture (both buckets, both classes):

        bucket  scene   d_closed  d_open   gap
        day     closed   0.0000   0.0984  0.0984   4.9x margin
        day     open     0.0779   0.0000  0.0779   3.9x margin
        night   closed   0.0000   0.0998  0.0998   5.0x margin
        night   open     0.0781   0.0000  0.0781   3.9x margin

    worst clean gap = 0.0779 = 3.9x the provisional 0.02 margin, and day and
    night agree to within 0.0002. The 02-01 handoff asked for at least 3x; the
    fixture before this change managed 1.9x, with night/open at 0.007 — i.e.
    *under* the margin, which is why the day/night test failed.

    The assertion stays ``gap > margin`` rather than ``gap > 3 * margin``:
    `margin` is recalibrated against real recordings in plan 02-02 and a fixed
    multiple would fight that calibration. What must hold is the property the
    classifier actually depends on — the correct class clears the margin.
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
