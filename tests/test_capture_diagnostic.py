from scripts.probe_capture_2_cameras import diagnostic_passed


def test_diagnostic_passed_requires_frames_for_every_camera():
    results = {
        "cam_1": {"frames": 1, "starts": 1, "error": None},
        "cam_2": {"frames": 1, "starts": 1, "error": None},
    }

    assert diagnostic_passed(results) is True

    results["cam_2"]["frames"] = 0
    assert diagnostic_passed(results) is False
