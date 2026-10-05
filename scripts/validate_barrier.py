"""Replay recorded barrier sessions and turn SC #4 from a claim into numbers.

Plan 02-02 Task 3. Reads the recordings produced by ``scripts.record_validation``
and the references bootstrapped by ``scripts.build_references``, then replays
each session in timestamp order through the REAL ``classify()`` path and that
camera's ``BarrierFSM``, counting emitted open events.

It deliberately mirrors runtime behaviour:

* only the camera named by ``barrier.authoritative_camera`` may emit — the
  sibling camera's opens are counted as diagnostics, never as events, because
  two emitting FSMs would double-count one physical opening (BARRIER-03);
* each session gets a fresh FSM, so state never leaks between sessions that
  were recorded hours apart;
* an ``UNKNOWN`` observation is fed straight through: refusing to classify is
  exactly what the FSM is supposed to do with it.

Exit codes::

    0  every metric passed
    1  a false opening, or a genuine opening that was missed
    2  cannot validate at all (no recordings, no references) — a silent green
       run would be worse than a red one, so "nothing to check" is a failure

Usage::

    python -m scripts.validate_barrier
    python -m scripts.validate_barrier --recordings data/recordings --json
"""

import argparse
import json
import os
import sys
from collections import defaultdict

import cv2

from src.config import load_settings
from src.detect.barrier import BarrierDetector, ReferenceSet, bucket_for, mean_brightness
from src.detect.consumer import authoritative_flags
from src.detect.fsm import BarrierFSM, BarrierState

#: Labels where an open event is a FALSE POSITIVE. The plan gates these at 0.
GATE_CLOSED_LABELS = ("closed_idle", "car_passes_gate_closed")

#: Labels where an open event proves detection. Missing one is a failure.
GATE_OPEN_LABELS = ("opening", "open")

#: Anything else (closing, car_passes_gate_open, night_lighting_change,
#: closed_night, open_night, ...) is replayed and reported but not gated: a
#: transitional single frame has no single correct expectation.


def discover_sessions(root):
    """Return ``[(session_dir, manifest), ...]`` sorted by name."""
    if not os.path.isdir(root):
        return []
    sessions = []
    for entry in sorted(os.listdir(root)):
        session_dir = os.path.join(root, entry)
        manifest_path = os.path.join(session_dir, "manifest.json")
        if not os.path.isfile(manifest_path):
            continue
        with open(manifest_path, encoding="utf-8") as handle:
            sessions.append((session_dir, json.load(handle)))
    return sessions


def load_references(references_dir):
    """Load Task 1's ReferenceSet, or fail loudly with the command to fix it."""
    if not os.path.isdir(references_dir):
        raise SystemExit(
            "no references at %s — record sessions, then run\n"
            "    python -m scripts.build_references" % references_dir
        )
    try:
        return ReferenceSet.load(references_dir)
    except (OSError, ValueError) as exc:
        raise SystemExit("references at %s are unusable (%s)" % (references_dir, exc))


def replay_session(session_dir, manifest, references, cfg, flags):
    """Replay one session; return per-camera results.

    Frames are replayed in timestamp order per camera, exactly as they arrived
    from the feed — the FSM needs temporal order to do its job at 1.5 fps.
    """
    threshold = float(cfg.get("bucket_threshold", 60.0))
    margin = float(cfg.get("margin", 0.15))
    open_extent_ratio = float(cfg.get("open_extent_ratio", 0.6))
    cameras = {}

    for camera_id in manifest.get("cameras", {}):
        detector = BarrierDetector(references, margin=margin,
                                   open_extent_ratio=open_extent_ratio,
                                   bucket_threshold=threshold)
        fsm = BarrierFSM(dwell_open=int(cfg.get("dwell_open", 2)),
                         dwell_closed=int(cfg.get("dwell_closed", 4)))

        records = [r for r in manifest.get("frames", [])
                   if r.get("camera_id") == camera_id]
        records.sort(key=lambda r: (r.get("ts", ""), r.get("index", 0)))

        emitted = []
        diagnostics = 0
        unreadable = 0
        buckets = defaultdict(lambda: {"frames": 0, "unknown": 0, "events": 0})
        authoritative = bool(flags.get(camera_id))

        for position, record in enumerate(records):
            path = os.path.join(session_dir, record["file"])
            frame = cv2.imread(path, cv2.IMREAD_COLOR)
            if frame is None:
                unreadable += 1
                continue

            bucket = bucket_for(mean_brightness(frame), threshold)
            buckets[bucket]["frames"] += 1

            observation = detector.classify(camera_id, frame)
            if observation.state is BarrierState.UNKNOWN:
                buckets[bucket]["unknown"] += 1

            for event in fsm.update(observation):
                if not event.emits_event:
                    continue
                if authoritative:
                    emitted.append({
                        "camera_id": camera_id,
                        "index": record.get("index"),
                        "ts": record.get("ts"),
                        "position": position,
                        "bucket": bucket,
                    })
                    buckets[bucket]["events"] += 1
                else:
                    # Seen, counted, deliberately not emitted (BARRIER-03).
                    diagnostics += 1

        cameras[camera_id] = {
            "authoritative": authoritative,
            "frames": len(records),
            "unreadable": unreadable,
            "unknown": sum(b["unknown"] for b in buckets.values()),
            "emitted": emitted,
            "diagnostics": diagnostics,
            "buckets": {name: dict(vals) for name, vals in sorted(buckets.items())},
            "final_state": fsm.state.value,
        }

    return cameras


def evaluate(results):
    """Turn replay results into the gated metrics.

    ``results`` is a list of ``(label, session_dir, manifest, cameras)``.
    """
    false_openings = []      # (label, camera_id, event)
    missed_openings = []     # (label, camera_id)
    latencies = []           # (label, camera_id, samples_into_session)
    closed_seconds = 0.0
    buckets = defaultdict(lambda: {"frames": 0, "unknown": 0, "events": 0})

    for label, _session_dir, manifest, cameras in results:
        for camera_id, info in cameras.items():
            for bucket_name, values in info["buckets"].items():
                aggregate = buckets[bucket_name]
                aggregate["frames"] += values["frames"]
                aggregate["unknown"] += values["unknown"]
                aggregate["events"] += values["events"]

            if label in GATE_CLOSED_LABELS:
                for event in info["emitted"]:
                    false_openings.append((label, camera_id, event))

            # Detection is judged on the authoritative camera only — that is
            # what actually runs (BARRIER-03).
            if label in GATE_OPEN_LABELS and info["authoritative"]:
                if info["emitted"]:
                    first = min(e["position"] for e in info["emitted"])
                    latencies.append((label, camera_id, first))
                else:
                    missed_openings.append((label, camera_id))

        if label in GATE_CLOSED_LABELS:
            closed_seconds += float(manifest.get("duration_s") or 0.0)

    hours = closed_seconds / 3600.0
    return {
        "false_openings": false_openings,
        "missed_openings": missed_openings,
        "latencies": latencies,
        "closed_hours": hours,
        "false_openings_per_hour": (len(false_openings) / hours) if hours else 0.0,
        "buckets": {name: dict(vals) for name, vals in sorted(buckets.items())},
    }


def report(results, evaluation, stream=sys.stdout):
    """Print the per-session table and the gated summary."""
    print("session\tlabel\tframes\temitted\tunknown\tdiagnostic\tauthoritative", file=stream)
    for label, session_dir, _manifest, cameras in results:
        frames = sum(i["frames"] for i in cameras.values())
        emitted = sum(len(i["emitted"]) for i in cameras.values())
        unknown = sum(i["unknown"] for i in cameras.values())
        diagnostics = sum(i["diagnostics"] for i in cameras.values())
        authoritative = ",".join(sorted(
            cid for cid, info in cameras.items() if info["authoritative"]
        )) or "-"
        percent = (100.0 * unknown / frames) if frames else 0.0
        print("%s\t%s\t%d\t%d\t%d (%.1f%%)\t%d\t%s" % (
            os.path.basename(session_dir), label, frames, emitted, unknown,
            percent, diagnostics, authoritative,
        ), file=stream)

    print("", file=stream)
    print("false openings: %d on %s" % (
        len(evaluation["false_openings"]), " + ".join(GATE_CLOSED_LABELS)), file=stream)
    for label, camera_id, event in evaluation["false_openings"]:
        print("    %s camera=%s frame index=%s ts=%s bucket=%s" % (
            label, camera_id, event["index"], event["ts"], event["bucket"]),
            file=stream)
    print("false-openings-per-hour: %.3f (%d over %.2f h of closed-context recordings)" % (
        evaluation["false_openings_per_hour"], len(evaluation["false_openings"]),
        evaluation["closed_hours"]), file=stream)

    print("missed openings: %d on %s" % (
        len(evaluation["missed_openings"]), " + ".join(GATE_OPEN_LABELS)), file=stream)
    for label, camera_id in evaluation["missed_openings"]:
        print("    %s camera=%s committed no OPEN" % (label, camera_id), file=stream)

    print("detection latency (samples from session start to committed OPEN):",
          file=stream)
    if evaluation["latencies"]:
        for label, camera_id, samples in evaluation["latencies"]:
            print("    %s camera=%s %d samples" % (label, camera_id, samples),
                  file=stream)
    else:
        print("    none", file=stream)

    print("per-bucket:", file=stream)
    for bucket_name, values in evaluation["buckets"].items():
        frames = values["frames"]
        unknown = values["unknown"]
        percent = (100.0 * unknown / frames) if frames else 0.0
        print("    %-6s %d frames, %d unknown (%.1f%%), %d open events" % (
            bucket_name, frames, unknown, percent, values["events"]), file=stream)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Replay recorded barrier sessions and report measured numbers."
    )
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--recordings", default=None,
                        help="recording root (default: data/recordings)")
    parser.add_argument("--references", default=None,
                        help="reference root (default: barrier.references_dir)")
    parser.add_argument("--json", action="store_true",
                        help="emit the metrics as JSON instead of the table")
    args = parser.parse_args(argv)

    settings = load_settings(args.config)
    cfg = settings.get("barrier") or {}
    recordings_root = args.recordings or os.path.join("data", "recordings")
    references_dir = args.references or cfg.get("references_dir", "data/references")

    sessions = discover_sessions(recordings_root)
    if not sessions:
        # A silent green run would be worse than a red one: "nothing to check"
        # is not the same as "checked and passed".
        print("no recordings under %s — cannot validate SC #4. Record first:"
              % recordings_root, file=sys.stderr)
        print("    python -m scripts.record_validation --label opening --duration 30",
              file=sys.stderr)
        return 2

    references = load_references(references_dir)
    camera_ids = sorted({camera_id for _path, manifest in sessions
                         for camera_id in manifest.get("cameras", {})})
    try:
        flags = authoritative_flags(camera_ids, cfg.get("authoritative_camera"))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    results = []
    for session_dir, manifest in sessions:
        cameras = replay_session(session_dir, manifest, references, cfg, flags)
        results.append((manifest.get("label", "?"), session_dir, manifest, cameras))

    evaluation = evaluate(results)

    if args.json:
        print(json.dumps({
            "false_openings": [
                {"label": label, "camera_id": camera_id, "event": event}
                for label, camera_id, event in evaluation["false_openings"]
            ],
            "missed_openings": [
                {"label": label, "camera_id": camera_id}
                for label, camera_id in evaluation["missed_openings"]
            ],
            "latencies": [
                {"label": label, "camera_id": camera_id, "samples": samples}
                for label, camera_id, samples in evaluation["latencies"]
            ],
            "closed_hours": evaluation["closed_hours"],
            "false_openings_per_hour": evaluation["false_openings_per_hour"],
            "buckets": evaluation["buckets"],
            "authoritative_flags": flags,
        }, indent=2))
    else:
        report(results, evaluation)

    if evaluation["false_openings"] or evaluation["missed_openings"]:
        # Step 5: report the offending sessions and frame indices first. Do NOT
        # loosen margin/dwell to make this pass — widen the gate only after the
        # offending frames have actually been looked at.
        print("\nFAIL: see the offending sessions/frame indices above before "
              "touching margin or dwell", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
