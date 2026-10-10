"""Tests for the reference-frame barrier classifier (BARRIER-01, BARRIER-04).

Synthetic ``cv2`` scenes stand in for footage so the classifier's logic is
exercised with no recording, no network and no sleeping. Validation against
REAL frames is a later tier (see ``02-RESEARCH.md`` Validation Architecture) —
these tests prove the logic, not the field calibration.

Fixture geometry (1280x720), matching what the real cameras show (2026-10-10):
a CLOSED arm is a long thin vertical object running most of the arm band, and an
OPEN arm leaves the band empty:

    mast     x 100..120,  y 60..240     (thin, away from the arm band)
    arm      vertical x 600..624, y 40..520 when closed; absent when open
    band     x 560..660,  y 20..520     (the closed ROI, reused as the open ROI)
    car      y 540..690  (lower third, below every ROI)

The car sits below the band so it cannot move either distance — which is the
whole point of the BARRIER-04 regression test. (The first version of this
fixture modelled the opposite, a horizontal closed arm and a raised vertical
open arm; real footage disagreed and the geometry probe was inverted.)

Two properties of the render are measured, not decorative:

``ink_for(brightness)``
    The arm/mast fill is derived from the scene brightness. With a fixed colour
    the arm sank into a 45-luminance night frame, its edges disappeared, and the
    night bucket stopped discriminating at all (night/open gap 0.007 against a
    0.02 margin — the day/night test failed on a fixture bug, not on the
    classifier). Day and night now measure the same gaps.

Horizontal background stripes
    Edge maps without texture are ~3% dense and the two classes land closer
    together than the margin. Stripes must run HORIZONTALLY: vertical ones form
    full-height components inside the arm band, and the arm-presence probe then
    reads an open frame as a closed arm.
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
ARM_CLOSED_BOX = (600, 40, 24, 480)
CAR_BOX = (300, 540, 550, 150)
# One band around the arm serves as both ROIs (the real cameras do the same:
# the raised arm leaves nothing to box). It is also the arm-presence probe.
GATE_BAND = (560, 20, 100, 500)

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
    if state == "closed":
        arm = ARM_CLOSED_BOX
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
            ("cam_1", "open"): to_work(GATE_BAND)}
    return ReferenceSet(maps=maps, rois=rois)


@pytest.fixture
def detector(references):
    # margin is provisional and deliberately small; it is calibrated against
    # real recordings in plan 02-02. On the current synthetic fixture the
    # worst clean gap is 0.078 (3.9x this margin) — see the measured-separation
    # test below for how that is asserted.
    return BarrierDetector(references, margin=0.02, open_extent_ratio=0.64)

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
    assert result.detail in ("ambiguous", "geometric_mismatch", "no_scene_content")


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


def test_geometric_mismatch_is_reported_when_arm_still_in_band(references):
    """Nearer the open reference but a tall arm-like component remains: refuse.

    With a tiny ratio any texture in the band counts as "arm present", so the
    open scene is nearer the open reference yet fails the corroboration.
    """
    strict = BarrierDetector(references, margin=0.02, open_extent_ratio=0.005)
    result = strict.classify("cam_1", make_scene("open"))
    assert result.state is BarrierState.UNKNOWN
    assert result.detail == "geometric_mismatch"


@pytest.mark.parametrize("level", [0, 90, 120, 255])
def test_blank_frame_is_unknown_not_open(detector, level):
    """An open arm leaves the band empty, so a featureless frame must not read open.

    Real-footage finding (2026-10-10): a flat grey frame was nearer the open
    reference than the closed one on cam_2. Blown-out, foggy or dead-feed frames
    must be refused, never turned into an opening.
    """
    frame = np.full((FRAME_H, FRAME_W, 3), level, dtype=np.uint8)
    result = detector.classify("cam_1", frame)
    assert result.state is BarrierState.UNKNOWN, (level, result)
    assert result.detail == "no_scene_content"


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
        day     closed   0.0000   0.1053  0.1053   5.3x margin
        day     open     0.1053   0.0000  0.1053   5.3x margin
        night   closed   0.0000   0.1062  0.1062   5.3x margin
        night   open     0.1062   0.0000  0.1062   5.3x margin

    worst clean gap = 0.1053 = 5.3x the provisional 0.02 margin, and day and
    night agree to within 0.001. (Re-measured 2026-10-10 after the fixture was
    redrawn to the real camera geometry: closed = tall arm in the band, open =
    empty band.)

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


# --- picture dedup and bucket smoothing (calibrated on 2026-10-10 recordings) ---

def test_picture_dedup_flags_only_new_pictures():
    from src.detect.barrier import PictureDedup

    dedup = PictureDedup()
    grey = np.full((90, 160), 100, dtype=np.uint8)
    noisy_copy = grey.copy()
    noisy_copy[::3, ::2] += 1                  # ~0.17 mean grey level of re-encode noise
    other = np.full((90, 160), 160, dtype=np.uint8)

    assert dedup.is_new(grey) is True          # first picture is always new
    assert dedup.is_new(grey) is False         # re-delivered copy
    assert dedup.is_new(noisy_copy) is False   # re-encode noise (~0.2 level)
    assert dedup.is_new(other) is True         # a genuinely new picture


def test_bucket_smoothing_ignores_a_single_flash_and_has_hysteresis():
    detector = BarrierDetector(None, bucket_threshold=110.0, bucket_window=5,
                               bucket_hysteresis=5.0)
    feed = lambda value: detector._bucket("cam_1", value)

    for value in (120, 121, 119, 122):
        assert feed(value) == "day"
    assert feed(89) == "day"          # one headlight flash: median stays ~120
    for value in (106, 105, 104, 103, 102):
        bucket = feed(value)
    assert bucket == "night"          # sustained dusk: below threshold - hysteresis
    assert feed(111) == "night"       # inside the band: no flip back
    for value in (118, 119, 120, 121, 122):
        bucket = feed(value)
    assert bucket == "day"            # clearly day again


def test_bucket_smoothing_is_per_camera():
    detector = BarrierDetector(None, bucket_threshold=110.0, bucket_window=3,
                               bucket_hysteresis=5.0)
    for value in (100, 100, 100):
        detector._bucket("cam_1", value)
    assert detector._bucket("cam_2", 125) == "day"
