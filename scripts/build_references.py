"""Bootstrap barrier reference maps, ROIs and fixtures from real recordings.

Plan 02-02 Task 1. Reads the sessions written by ``scripts.record_validation``
and a hand-labelled span file (``--spans``), then writes:

* ``<references>/<camera_id>/<bucket>/{closed,open}.png`` and ``rois.json`` --
  the runtime references, edge maps at ``WORK_SIZE`` (``data/`` is gitignored);
* the same files into ``<fixtures>/references/`` -- COMMITTED, byte-identical,
  so the T2 fixture test runs on a clean clone;
* ``<fixtures>/<class>/<camera_id>/<n>.jpg`` plus ``<fixtures>/index.json`` --
  a few native 1280x720 frames per class (never downscaled: the fixtures must
  exercise ``preprocess()``'s own resize).

Why a span file: most sessions mix states (an ``opening`` run is closed, open,
closed again; a ``passive_day`` run is hours of traffic), so a session label
cannot say which frames are closed or open. Sessions labelled ``closed_idle`` or
``closed_night`` without a span entry are taken as all-closed.

Reference choice: the cameras only refresh their picture every ~8-9 s, so
consecutive snapshots are duplicates. Frames are de-duplicated first, then the
reference per ``(camera, bucket, kind)`` is the medoid -- the candidate with the
lowest median edge distance to the other candidates in its class -- so a
mid-transition frame or a frame with a vehicle in it is never picked.

ROI: the arm band is the bounding box of the long orange arm in a daylight
closed frame (colour is robust; the edge difference between two references is
swamped by leaf texture on cam_1 and is only the fallback). The same band is
used for both ``closed`` and ``open``: the open arm
leaves almost nothing behind, so a separate open box would be empty and
``BarrierDetector._distance`` would score it 1.0. ``--roi-closed`` /
``--roi-open`` accept ``x,y,w,h`` in 1280x720 pixels, optionally prefixed with
``cam_1:`` to target one camera.

A missing ``(camera, bucket, kind)`` is a hard failure naming what is missing:
a silent gap would present as "the detector never works at night".

Usage::

    python -m scripts.build_references
    python -m scripts.build_references --bucket-threshold 110 --fixtures-per-class 5
"""

import argparse
import json
import os
import shutil
import sys
from collections import defaultdict

import cv2
import numpy as np

from src.config import load_settings
from src.detect.barrier import (
    WORK_SIZE,
    ReferenceSet,
    bucket_for,
    mean_brightness,
    preprocess,
)

#: Frames closer than this (mean gray diff at 160x90) are the same camera moment.
_DUPLICATE_DIFF = 0.8
_DEDUPE_SIZE = (160, 90)

#: States used as references; everything else is fixture-only.
_REFERENCE_KINDS = ("closed", "open")

#: Session labels taken as all-closed when no span entry exists.
_ALL_CLOSED_LABELS = ("closed_idle", "closed_night")

_SRC_SIZE = (1280, 720)


def _parse_roi(values):
    """``['x,y,w,h']`` or ``['cam_1:x,y,w,h']`` -> ``{camera_or_None: (x,y,w,h)}``."""
    parsed = {}
    for raw in values or []:
        camera, _, numbers = raw.rpartition(":")
        parts = [int(p) for p in numbers.split(",")]
        if len(parts) != 4:
            raise SystemExit("ROI %r must be x,y,w,h" % raw)
        parsed[camera or None] = tuple(parts)
    return parsed


def _to_work(roi):
    """1280x720 ``(x, y, w, h)`` -> WORK_SIZE coordinates."""
    fx = WORK_SIZE[0] / float(_SRC_SIZE[0])
    fy = WORK_SIZE[1] / float(_SRC_SIZE[1])
    x, y, w, h = roi
    return (int(round(x * fx)), int(round(y * fy)),
            max(1, int(round(w * fx))), max(1, int(round(h * fy))))


def _in_spans(index, spans):
    return any(low <= index <= high for low, high in spans)


def discover_sessions(root):
    sessions = []
    if not os.path.isdir(root):
        return sessions
    for entry in sorted(os.listdir(root)):
        manifest_path = os.path.join(root, entry, "manifest.json")
        if os.path.isfile(manifest_path):
            with open(manifest_path, encoding="utf-8") as handle:
                sessions.append((entry, os.path.join(root, entry), json.load(handle)))
    return sessions


def collect_candidates(sessions, spans, threshold):
    """Return ``{(camera_id, state): [candidate, ...]}``.

    A candidate is the first in-span frame of each distinct camera moment, so
    ~17 identical snapshots collapse to one entry.
    """
    candidates = defaultdict(list)
    for name, session_dir, manifest in sessions:
        label = manifest.get("label", "")
        session_spans = spans.get(name, {})
        for camera_id in manifest.get("cameras", {}):
            camera_spans = dict(session_spans.get(camera_id, {}))
            if not camera_spans and label in _ALL_CLOSED_LABELS:
                camera_spans = {"closed": [[0, 10 ** 9]]}
            if not camera_spans:
                continue

            records = sorted(
                (r for r in manifest.get("frames", []) if r.get("camera_id") == camera_id),
                key=lambda r: (r.get("ts", ""), r.get("index", 0)),
            )
            previous = None
            block = -1
            seen = set()
            for record in records:
                path = os.path.join(session_dir, record["file"])
                frame = cv2.imread(path, cv2.IMREAD_COLOR)
                if frame is None:
                    continue
                gray = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY),
                                  _DEDUPE_SIZE).astype(np.int16)
                if previous is None or float(np.abs(gray - previous).mean()) > _DUPLICATE_DIFF:
                    block += 1
                previous = gray
                index = record.get("index", 0)
                for state, ranges in camera_spans.items():
                    if state == "transition" or not _in_spans(index, ranges):
                        continue
                    if (state, block) in seen:
                        continue
                    seen.add((state, block))
                    brightness = mean_brightness(frame)
                    candidates[(camera_id, state)].append({
                        "session": name,
                        "index": index,
                        "path": path,
                        "brightness": brightness,
                        "bucket": bucket_for(brightness, threshold),
                        "edges": preprocess(frame),
                    })
    return candidates


def _distance(a, b):
    return float(np.mean(np.not_equal(a, b)))


def pick_medoid(items):
    """Candidate with the lowest median edge distance to the others."""
    if len(items) <= 2:
        return items[0]
    best, best_score = None, None
    for i, item in enumerate(items):
        distances = [_distance(item["edges"], other["edges"])
                     for j, other in enumerate(items) if j != i]
        score = float(np.median(distances))
        if best_score is None or score < best_score:
            best, best_score = item, score
    return best


def arm_band(closed_edges, open_edges):
    """Bounding box (WORK_SIZE) of edges in the closed map that the open map lacks."""
    closed_mask = (closed_edges > 0).astype(np.uint8)
    open_mask = cv2.dilate((open_edges > 0).astype(np.uint8), np.ones((3, 3), np.uint8))
    diff = cv2.dilate(closed_mask & (1 - open_mask), np.ones((3, 3), np.uint8))
    count, _labels, stats, _ = cv2.connectedComponentsWithStats(diff, connectivity=8)
    if count <= 1:
        return None
    biggest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    x, y, w, h = (int(stats[biggest, k]) for k in (
        cv2.CC_STAT_LEFT, cv2.CC_STAT_TOP, cv2.CC_STAT_WIDTH, cv2.CC_STAT_HEIGHT))
    pad = 2
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(WORK_SIZE[0], x + w + pad), min(WORK_SIZE[1], y + h + pad)
    return (x0, y0, x1 - x0, y1 - y0)


def arm_band_color(frame):
    """Bounding box (WORK_SIZE) of the orange arm in a colour frame, or ``None``.

    The arm is the one long, thin, saturated-orange object; autumn leaves are
    also orange but small, so after a vertical morphological close the largest
    component is the arm. Night/IR frames carry no colour and return ``None``.
    """
    small = cv2.resize(frame, WORK_SIZE, interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    mask = ((hsv[..., 0] >= 3) & (hsv[..., 0] <= 20)
            & (hsv[..., 1] > 110) & (hsv[..., 2] > 120)).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE,
                            cv2.getStructuringElement(cv2.MORPH_RECT, (3, 15)))
    count, _labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if count <= 1:
        return None
    biggest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    x, y, w, h = (int(stats[biggest, k]) for k in (
        cv2.CC_STAT_LEFT, cv2.CC_STAT_TOP, cv2.CC_STAT_WIDTH, cv2.CC_STAT_HEIGHT))
    if h < 0.3 * WORK_SIZE[1] or w > 0.25 * WORK_SIZE[0]:
        return None  # not arm-shaped: a leaf cluster or a vehicle
    pad = 3
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(WORK_SIZE[0], x + w + pad), min(WORK_SIZE[1], y + h + pad)
    return (x0, y0, x1 - x0, y1 - y0)


def _union(a, b):
    if a is None:
        return b
    if b is None:
        return a
    x0, y0 = min(a[0], b[0]), min(a[1], b[1])
    x1 = max(a[0] + a[2], b[0] + b[2])
    y1 = max(a[1] + a[3], b[1] + b[3])
    return (x0, y0, x1 - x0, y1 - y0)


def _spread(items, count):
    """Up to ``count`` items spread evenly over ``items``."""
    if len(items) <= count:
        return list(items)
    positions = np.linspace(0, len(items) - 1, count).round().astype(int)
    return [items[i] for i in sorted(set(positions.tolist()))]


def write_fixtures(candidates, fixtures_dir, per_class, chosen):
    """Copy native frames into ``<fixtures>/<class>/<camera>/<n>.jpg``; return the index."""
    grouped = defaultdict(list)
    for (camera_id, state), items in candidates.items():
        for item in items:
            grouped[(state, item["bucket"], camera_id)].append(item)

    index = []
    for (state, bucket, camera_id), items in sorted(grouped.items()):
        items = sorted(items, key=lambda it: (it["session"], it["index"]))
        picks = _spread(items, per_class)
        reference = chosen.get((camera_id, bucket, state))
        if reference is not None and all(p["path"] != reference["path"] for p in picks):
            picks[-1] = reference
        klass = "%s_%s" % (state, bucket)
        directory = os.path.join(fixtures_dir, klass, camera_id)
        shutil.rmtree(directory, ignore_errors=True)
        os.makedirs(directory, exist_ok=True)
        for number, item in enumerate(picks):
            target = os.path.join(directory, "%d.jpg" % number)
            shutil.copyfile(item["path"], target)
            index.append({
                "file": "%s/%s/%d.jpg" % (klass, camera_id, number),
                "camera_id": camera_id,
                "state": state,
                "bucket": bucket,
                "session": item["session"],
                "frame_index": item["index"],
                "mean_brightness": round(item["brightness"], 1),
            })
    return index


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--recordings", default=os.path.join("data", "recordings"))
    parser.add_argument("--spans", default=None,
                        help="span file (default: <fixtures>/spans.json)")
    parser.add_argument("--references", default=None,
                        help="runtime reference root (default: barrier.references_dir)")
    parser.add_argument("--fixtures", default=os.path.join("tests", "fixtures", "barrier"))
    parser.add_argument("--bucket-threshold", type=float, default=None,
                        help="brightness split (default: barrier.bucket_threshold)")
    parser.add_argument("--fixtures-per-class", type=int, default=5)
    parser.add_argument("--roi-closed", action="append",
                        help="override the closed ROI, x,y,w,h in 1280x720 px, "
                             "optionally cam_1:x,y,w,h")
    parser.add_argument("--roi-open", action="append",
                        help="override the open ROI (default: same band as closed)")
    args = parser.parse_args(argv)

    cfg = (load_settings(args.config).get("barrier") or {})
    threshold = args.bucket_threshold
    if threshold is None:
        threshold = float(cfg.get("bucket_threshold", 60.0))
    references_dir = args.references or cfg.get("references_dir", "data/references")
    spans_path = args.spans or os.path.join(args.fixtures, "spans.json")

    spans = {}
    if os.path.isfile(spans_path):
        with open(spans_path, encoding="utf-8") as handle:
            spans = {k: v for k, v in json.load(handle).items() if not k.startswith("_")}

    sessions = discover_sessions(args.recordings)
    if not sessions:
        print("no recordings under %s — record sessions first:\n"
              "    python -m scripts.record_validation --label closed_idle --duration 60"
              % args.recordings, file=sys.stderr)
        return 2

    candidates = collect_candidates(sessions, spans, threshold)
    cameras = sorted({camera for camera, _ in candidates})
    if not cameras:
        print("no labelled frames: add spans to %s" % spans_path, file=sys.stderr)
        return 2

    chosen = {}
    missing = []
    for camera_id in cameras:
        for bucket in ("day", "night"):
            for kind in _REFERENCE_KINDS:
                pool = [c for c in candidates.get((camera_id, kind), [])
                        if c["bucket"] == bucket]
                if not pool:
                    missing.append((camera_id, bucket, kind))
                    continue
                chosen[(camera_id, bucket, kind)] = pick_medoid(pool)

    if missing:
        print("MISSING references — record or label footage for:", file=sys.stderr)
        for camera_id, bucket, kind in missing:
            pool = candidates.get((camera_id, kind), [])
            seen = sorted({"%.0f" % c["brightness"] for c in pool})
            print("  camera=%s bucket=%s kind=%s (threshold %.0f; %s frames seen at brightness %s)"
                  % (camera_id, bucket, kind, threshold, kind, ", ".join(seen) or "none"),
                  file=sys.stderr)
        return 1

    roi_closed_override = _parse_roi(args.roi_closed)
    roi_open_override = _parse_roi(args.roi_open)
    rois = {}
    for camera_id in cameras:
        # Colour first: the arm is the only long orange object in a daylight
        # closed frame, whereas the edge difference between two references is
        # swamped by leaf texture on cam_1. Edge difference is the fallback.
        band, method = None, "color"
        for bucket in ("day", "night"):
            frame = cv2.imread(chosen[(camera_id, bucket, "closed")]["path"], cv2.IMREAD_COLOR)
            band = arm_band_color(frame)
            if band is not None:
                break
        if band is None:
            method = "edge-diff"
            for bucket in ("day", "night"):
                band = _union(band, arm_band(chosen[(camera_id, bucket, "closed")]["edges"],
                                             chosen[(camera_id, bucket, "open")]["edges"]))
        print("camera=%s arm band by %s: %s" % (camera_id, method, band))
        closed_roi = roi_closed_override.get(camera_id) or roi_closed_override.get(None)
        closed_roi = _to_work(closed_roi) if closed_roi else band
        if closed_roi is None:
            print("camera=%s: closed and open references have no differing edges; "
                  "pass --roi-closed %s:x,y,w,h" % (camera_id, camera_id), file=sys.stderr)
            return 1
        open_roi = roi_open_override.get(camera_id) or roi_open_override.get(None)
        open_roi = _to_work(open_roi) if open_roi else closed_roi
        rois[(camera_id, "closed")] = closed_roi
        rois[(camera_id, "open")] = open_roi

    maps = {key: item["edges"] for key, item in chosen.items()}
    reference_set = ReferenceSet(maps=maps, rois=rois)

    for root in (references_dir, os.path.join(args.fixtures, "references")):
        shutil.rmtree(root, ignore_errors=True)
        reference_set.save(root)

    index = write_fixtures(candidates, args.fixtures, args.fixtures_per_class, chosen)
    with open(os.path.join(args.fixtures, "index.json"), "w", encoding="utf-8") as handle:
        json.dump({"bucket_threshold": threshold, "work_size": list(WORK_SIZE),
                   "frames": index}, handle, indent=2)

    print("bucket_threshold=%.0f  references -> %s and %s/references"
          % (threshold, references_dir, args.fixtures))
    print("camera\tbucket\tkind\tcandidates\tchosen (session #frame)\tbrightness")
    for (camera_id, bucket, kind), item in sorted(chosen.items()):
        count = sum(1 for c in candidates[(camera_id, kind)] if c["bucket"] == bucket)
        print("%s\t%s\t%s\t%d\t%s #%d\t%.1f"
              % (camera_id, bucket, kind, count, item["session"], item["index"],
                 item["brightness"]))
    for (camera_id, kind), roi in sorted(rois.items()):
        print("roi %s %s (WORK_SIZE x,y,w,h) = %s" % (camera_id, kind, roi))
    print("fixtures: %d frames in %s" % (len(index), args.fixtures))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
