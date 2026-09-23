# Phase 2: Barrier State Detector - Pattern Map

**Mapped:** 2026-09-23
**Files analyzed:** 10 (9 new, 2 modified — `config.json` and `tests/conftest.py` are modifications, not creations)
**Analogs found:** 8 / 10 with a real tracked-source analog in the Phase 1 codebase; 2 mapped to RESEARCH.md reference patterns only

> **Not greenfield, unlike Phase 1.** Phase 1 delivered 8 tracked Python modules under `src/` and `tests/`. This map therefore cites **real, git-tracked source** for most files. Verified this session: every analog path below satisfies `git ls-files -- <path>` (non-empty). No `.gsd/` mirror path is referenced.
>
> **Load-bearing structural finding:** `src/capture/` has **no `__init__.py`** — `find src tests -name "__init__.py"` returns nothing. Imports work because `pytest.ini` sets `pythonpath = .` and Python 3 namespace packages resolve `src.capture.*` from the filesystem. So the new `src/detect/__init__.py` in RESEARCH.md's Wave 0 list would make `src/detect` the project's **first regular package** while `src/capture` stays a namespace package. That is an inconsistency the planner must resolve explicitly: either add the (empty) `src/detect/__init__.py` for a clean package marker, or omit it to match `src/capture/` exactly. Both work today; do not silently mix without noting it.

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `src/detect/__init__.py` | config (package marker) | n/a | `src/capture/` — but it has NO `__init__.py` | no-analog (see note above) |
| `src/detect/barrier.py` | service (CV transform) | transform (frame → scalar signal) | `src/capture/stream_client.py` | role-match (both are cv2/numpy frame-processing modules with a named-error + logger convention) |
| `src/detect/fsm.py` | store (state machine) | event-driven | `src/capture/frame_buffer.py` (**weak**) | role-match — both are small, pure, I/O-free state-holding classes; no true FSM exists yet |
| `src/detect/replay.py` | service (fixture source) | streaming / file-I/O | `src/capture/supervisor.py` (generator contract) + `src/capture/stream_client.py::read_jpeg_frame` (None-at-EOF) | **exact on contract**, role-match on body |
| `tests/test_barrier_detector.py` | test | n/a | `tests/test_stream_client.py` | exact (synthetic-frame construction via cv2/numpy) |
| `tests/test_barrier_fsm.py` | test | n/a | `tests/test_frame_buffer.py` | exact (pure-logic tests, no mocks, no I/O) |
| `tests/test_replay.py` | test | n/a | `tests/test_supervisor.py` | exact (`list(generator)` consumption + fake collaborators) |
| `tests/conftest.py` **(modified)** | test fixture | n/a | `tests/conftest.py` (itself, Phase 1 fixtures) | exact (extend in place) |
| `config.json` **(modified)** | config | file-I/O | `config.json` + `src/config.py::load_settings` | exact (extend in place) |
| `tools/roi_overlay.py` | utility (offline tool) | file-I/O (batch, PNG out) | `scripts/probe_privratnik_auth.py` | role-match (standalone manual-only script with `main()` + `sys.exit`) |

## Pattern Assignments

### `src/detect/barrier.py` (service, transform)

**Analog:** `src/capture/stream_client.py` — the codebase's only existing cv2/numpy frame-processing module. Its module skeleton (docstring → stdlib imports → third-party imports → module logger → module constants → pure functions) is the exact shape `barrier.py` must copy.
**Reference for the algorithm itself:** `02-RESEARCH.md` lines 339-361 (`closed_ratio`) and 365-397 (`arm_angle`) — both validated by execution this session. There is no codebase precedent for the ROI signal, so RESEARCH.md's snippets are the authority for the *body*; stream_client.py is the authority for the *form*.

**Module skeleton pattern** — copy this exact header order from `src/capture/stream_client.py:1-24`:
```python
"""<one-line purpose> (<requirement IDs>).

<2-4 line paragraph naming the requirement/decision IDs and the empirical
assumption. Phase 1 consistently cites D-0x / Pitfall N inline.>
"""

import logging

import cv2
import numpy as np

logger = logging.getLogger(__name__)
```
Concretely: the docstring always names requirement IDs; `logger = logging.getLogger(__name__)` at module level, never a root logger; no `print()` in library modules (`scripts/probe_privratnik_auth.py` uses `print` — but that file is a *manual CLI utility*, and `tools/roi_overlay.py` is its category, not this one).

**Module-level constant pattern** — module-scope tuning values live in the config, not the module, but any *fixed* constant follows `stream_client.py:20-22`:
```python
# JPEG SOI / EOI markers.
_JPEG_SOI = b"\xff\xd8"
_JPEG_EOI = b"\xff\xd9"
```
So: `_ALPHA = 0.88` style module constants are acceptable only if they are genuine invariants; `alpha`, `release`, `seat`, `confirm` come from `config.json` and are constructor arguments.

**Core signal pattern** (`02-RESEARCH.md:345-360`, verbatim):
```python
def closed_ratio(frame, band, alpha=0.88):
    """Fraction of BAND pixels markedly darker than the LOCAL reference.

    band is (y0, y1, x0, x1) — a rect strictly above the traffic lane where the
    arm rests when DOWN. Illumination-invariant: the test is RELATIVE darkness
    inside the ROI, not an absolute grey level (Pitfall 4).
    """
    y0, y1, x0, x1 = band
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray = cv2.medianBlur(gray, 3)          # Pitfall 3: noise killer, ksize=3 only
    ref = max(float(np.median(gray)), 1e-3)  # per-frame illumination reference
    crop = gray[y0:y1, x0:x1] / ref
    return float(np.count_nonzero(crop < alpha)) / crop.size
```

**Input-validation pattern — the `None`-frame guard.** This is Phase 2's analogue of Phase 1's "never treat 'no frame' as 'no event'" rule (`02-RESEARCH.md:665`). Phase 1 already models the idiom at `src/capture/stream_client.py:121-125`:
```python
frame = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
if frame is not None:
    return frame
# Corrupt frame — skip and keep reading.
continue
```
and at `stream_client.py:129-131` where EOF is an explicit, named outcome (`return None`), never a silent fallthrough. `BarrierDetector.detect(frame)` must mirror both halves: reject `None`/wrong-shape input with a **named** exception or a logged skip, and never return a ratio that reads as "closed". The `ref = max(float(np.median(gray)), 1e-3)` floor in the snippet above is the same defensive instinct against a degenerate input (all-black frame → median 0 → `ZeroDivisionError`).

**ROI-geometry validation pattern — startup, not per-frame.** `02-RESEARCH.md:667` requires `0 <= y0 < y1 <= height`, `0 <= x0 < x1 <= width`, polygon in-bounds, "rejected at startup with a clear message". Phase 1's precedent for a loud startup rejection is `src/main.py:31-42` `_preflight_ffmpeg`:
```python
def _preflight_ffmpeg(settings):
    """Verify ffmpeg resolves; print a clear install hint and exit non-zero if not."""
    ffmpeg_path = settings.get("ffmpeg_path", "ffmpeg")
    resolved = _resolve_ffmpeg(ffmpeg_path)
    if resolved is None:
        print(
            "ffmpeg not found — install via: winget install Gyan.FFmpeg\n"
            "  (or set 'ffmpeg_path' in config.json to an absolute ffmpeg binary path)",
            file=sys.stderr,
        )
        sys.exit(1)
    return resolved
```
Copy the *shape* (pre-flight before the loop starts, one clear actionable message, non-zero exit) but use `raise`/`logger.error` rather than `print` inside `barrier.py` — the pre-flight belongs at the `main.py` call site, consistent with how `_preflight_ffmpeg` sits there rather than in `stream_client.py`.

**Error-handling pattern — named exception defined at the detection point.** `src/capture/auth.py:25-32` is the canonical example and explains *why* the class lives where it does:
```python
class AuthExpiredError(Exception):
    """Raised when the session/token is detected as expired or unextractable.

    Defined here (rather than in ``supervisor``) because it is raised at the
    point of token extraction/log-in where the failure is actually detected.
    ``supervisor`` imports and catches it to drive the reconnect loop.
    """
```
Follow it exactly: define `class InvalidFrameError(Exception)` / `class ROIConfigError(Exception)` in `barrier.py` (where they are raised), not in a central `errors.py`, and document in the docstring who catches them. `src/capture/supervisor.py:27-28` shows the sibling style (one-line docstring, no rationale needed once the rationale was given at the origin).

**Optional secondary signal** (`arm_angle`) — if built, `02-RESEARCH.md:365-397` is the body, and Pitfall 2 (`02-RESEARCH.md:295-301`) is the hard constraint: Canny on the **full** frame, then filter Hough results by `cv2.pointPolygonTest(poly, midpoint, False) >= 0`. Never `bitwise_and` the mask before Canny.

---

### `src/detect/fsm.py` (store, event-driven)

**Analog:** `src/capture/frame_buffer.py` is the closest existing *shape* — a small, dependency-free, single-responsibility class with no I/O and no logging. It is a **weak** match (a bounded queue is not a state machine) but it establishes the house style for this kind of module. The algorithm is `02-RESEARCH.md:403-447`.
**No true FSM exists in the codebase.** This is the phase's one genuinely novel role.

**Class style to copy** from `src/capture/frame_buffer.py:22-40`:
```python
class FrameBuffer:
    """A per-camera bounded drop-oldest queue of tagged frames."""

    def __init__(self, camera_id, maxsize=15):
        self.camera_id = camera_id
        self.q = queue.Queue(maxsize=maxsize)

    def push(self, data):
        """Non-blocking drop-oldest put (D-09). Tag with camera_id at enqueue (D-10)."""
```
Note the conventions: docstring on the class AND on every public method, one-line docstring citing the decision ID, plain `__init__` with defaults, no dataclasses, no type hints anywhere in the existing codebase (`grep` for `->` / `: str` across `src/` finds none). **Match the existing no-annotations style** — adding type hints to only the new package would split the codebase's conventions.

**Core FSM pattern** (`02-RESEARCH.md:403-447`, verbatim — validated by simulation this session):
```python
class BarrierFSM:
    """4-state barrier FSM. CLOSED -> OPENING -> OPEN -> CLOSING -> CLOSED.

    Pure: no wall clock, no I/O, no threading. Deterministic under replay.
    Emits a transition ONLY on a state edge  (BARRIER-03).
    """

    def __init__(self, release=0.25, seat=0.35, confirm=2):
        self.release, self.seat, self.confirm = release, seat, confirm
        self.state = "CLOSED"
        self.transitions = []
        self._up = self._down = 0

    def step(self, ratio):
        # Three zones. The intermediate zone clears BOTH counters, which is what
        # holds state at an intermediate arm angle instead of oscillating.
        if ratio >= self.seat:
            self._up, self._down = 0, self._down + 1
        elif ratio < self.release:
            self._down, self._up = 0, self._up + 1
        else:
            self._up = self._down = 0       # DEAD BAND: hold current state

        previous, self.state = self.state, self.state
        # ... transition table ...
        if self.state != previous:                      # EDGE ONLY
            self.transitions.append((previous, self.state, ratio))
        return previous, self.state
```

**Hard constraints on this module (from RESEARCH — treat as locked):**
- **No wall-clock call inside the FSM** (`02-RESEARCH.md:251`). Timestamps come from the frame index. The existing codebase does use `time.time()` / `time.monotonic()` (`frame_buffer.py:36`, `supervisor.py:33`), so this is a *deliberate divergence* — document it in the class docstring, as the snippet above already does ("no wall clock").
- **Injectable clock is already the house pattern for testability** — see `src/capture/supervisor.py:33` (`clock=time.monotonic` as a defaulted parameter) and `src/capture/supervisor.py:41-43` ("`clock` and `read_frame` are injectable for tests"). If any timing is needed, take it as a defaulted argument, never as a module-level `import time` call inside `step()`.
- **Intermediate ratio must clear BOTH counters** — this is the anti-oscillation mechanism (Pattern 1, `02-RESEARCH.md:233-237`). A two-zone implementation fails SC3.
- **Do not add a fifth `PARTIAL` state.** `02-RESEARCH.md:57-59` and Open Question 1: expose `closed_ratio` plus an `is_partial` flag on the reading so BARRIER-01's third case is satisfiable while the reported state stays one of four.

---

### `src/detect/replay.py` (service, streaming / file-I/O)

**Analog (contract):** `src/capture/supervisor.py` — `run_capture_with_supervisor` is **already a generator that yields frames**, and this is the single most important fact in the phase. **Analog (None-at-EOF idiom):** `src/capture/stream_client.py::read_jpeg_frame`.

**The generator contract to satisfy** — `src/capture/supervisor.py:31-44` (signature + docstring) and `:46-54` (the yield):
```python
def run_capture_with_supervisor(start, stop_event, session_mgr, camera_id, cam_url,
                                frame_stale_seconds=12, backoff_initial=1.0, backoff_max=60.0,
                                clock=time.monotonic, read_frame=None):
    """Run the capture loop for one camera with reconnect + re-auth.
    ...
    Yields each decoded frame. On a stale-stream or auth failure, re-auths, kills
    the ffmpeg process, waits with exponential backoff ...
    """
    backoff = backoff_initial
    while not stop_event.is_set():
        proc = None
        try:
            url, headers = session_mgr.stream_headers_and_url(camera_id, cam_url)
            proc = start(url, headers)
            for frame in _yield_frames_or_raise(proc, frame_stale_seconds, stop_event,
                                                clock=clock, read_frame=read_frame):
                backoff = backoff_initial  # healthy frame; reset
                yield frame
```
The observable contract `ReplaySource` must match: a **generator** (not a list, not a callback), yielding a **bare frame object** (the live path yields a numpy BGR array; `tests/test_supervisor.py:110-111` substitutes plain strings, proving the detector only needs "an iterable of frames"). `ReplaySource` yields `(frame, timestamp)` tuples per RESEARCH — the planner must be explicit about which shape the detector consumes, and keep live and replay paths identical (that is the whole point of Pattern 2, `02-RESEARCH.md:239-243`).

**Core replay pattern** (`02-RESEARCH.md:457-472`, verbatim). Note RESEARCH's snippet has a real defect to fix during implementation: `__init__` sets `self.files` but `__iter__` reads `self.clip_dir`, which `__init__` never assigns.
```python
class ReplaySource:
    """Yields (frame, timestamp) from a numbered-JPEG directory.

    Mirrors the generator contract of run_capture_with_supervisor
    (src/capture/supervisor.py:31-54) so BarrierDetector is source-agnostic.
    Timestamps come from the frame INDEX, never the wall clock — determinism.
    """

    def __init__(self, clip_dir, fps=1.5):
        self.files = sorted(f for f in os.listdir(clip_dir) if f.endswith(".jpg"))
        self.fps = fps

    def __iter__(self):
        for i, name in enumerate(self.files):
            frame = cv2.imread(os.path.join(self.clip_dir, name))
            yield frame, i / self.fps
```

**Mandatory hardening beyond the snippet:**
1. **Generator discipline (security, `02-RESEARCH.md:668`).** One `cv2.imread` per iteration, never a list comprehension — an unbounded operator-supplied directory must not be materialised.
2. **`None`-frame guard.** `cv2.imread` returns `None` on a truncated/corrupt file (`02-RESEARCH.md:665`). Copy the `stream_client.py:121-125` idiom: treat it as a skipped frame with a `logger.warning`, never as a valid frame. The snippet above has no such guard.
3. **Path resolution (security, `02-RESEARCH.md:669`).** Resolve against a fixed base directory; reject absolute paths from config.
4. **Prefer `pathlib` for the new code** — RESEARCH's stack table (`:98`) names `pathlib` as a stdlib to use. Phase 1 uses `os.path`/`os.getenv` (`config.py:9`, `scripts/probe_privratnik_auth.py:13`), so this is the one place where the newer idiom is explicitly sanctioned. Do not reformat Phase 1 files to match.

---

### `tests/test_barrier_detector.py` (test, transform)

**Analog:** `tests/test_stream_client.py` — the existing module that builds **synthetic images with cv2/numpy** and asserts on decoded arrays. This is the closest thing the repo has to what the detector tests need.

**Synthetic-frame helper pattern** (`tests/test_stream_client.py:11-23`):
```python
import cv2
import numpy as np

from src.capture.stream_client import build_ffmpeg_cmd, read_jpeg_frame, spawn_ffmpeg


def _synthetic_jpeg():
    """Return a tiny valid JPEG byte sequence."""
    img = np.zeros((8, 8, 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()
```
Note the house style: a leading-underscore module-private factory, a one-line docstring, `assert` on the encoder's success, and direct `from src.capture.<mod> import <names>` imports (`pythonpath = .` makes `src.*` importable — `pytest.ini:3`).

Phase 2's version belongs in `tests/conftest.py` (so all three new test modules share it) rather than duplicated per-file — see the conftest section below.

**Test naming and structure:** plain `def test_<behaviour>()` functions, **no classes**, no `pytest.mark` except the documented skips. Requirement IDs appear in the module docstring (`tests/test_stream_client.py:1-6`) and sometimes inline in the test body. Assertions are plain `assert` — `pytest.raises` only where an exception is the contract (`tests/test_auth.py:109`, `:132`).

**Degradation-matrix test:** `test_thresholds_survive_degradation` should build the degradation pipeline (JPEG q20 + noise σ40 + Gaussian blur 9×9 + 50% brightness gradient — `02-RESEARCH.md:526`) as a parametrised fixture, following `tests/test_stream_client.py`'s build-then-assert shape. RESEARCH `:263` explicitly recommends parametrising this sweep rather than hand-tuning numbers.

---

### `tests/test_barrier_fsm.py` (test, event-driven)

**Analog:** `tests/test_frame_buffer.py` — the codebase's model for **pure-logic tests with zero mocks, zero I/O, zero threads** (except the one deliberate race test).

**Style to copy** (`tests/test_frame_buffer.py:1-33`):
```python
"""Tests for the bounded drop-oldest frame buffer (STREAM-04/05, D-09/D-10)."""

import queue
import threading

from src.capture.frame_buffer import Frame, FrameBuffer


def test_frame_pops_with_camera_id():
    fb = FrameBuffer("cam_1")
    fb.push(b"frame-data")
    frame = fb.pop()
    assert frame.camera_id == "cam_1"
```
Instantiate the class directly in the test, drive it, assert on observable state. No fixtures when a direct construction is clearer — `FrameBuffer("cam_1")` is constructed inline here; the `frame_buffer` fixture is used only for the race test (`:35`).

**What the FSM tests must assert, per the VALIDATION map** (`02-VALIDATION.md:45-50`, names are the contract):
- `test_full_cycle_transitions` → exactly `['OPENING','OPEN','CLOSING','CLOSED']` (SC3)
- `test_intermediate_band_holds_state` → no oscillation at an intermediate ratio
- `test_no_duplicate_transitions_while_open` → a sustained open re-emits nothing (BARRIER-03)
- `test_two_cycles_emit_two_event_pairs` → two independent event pairs
- `test_traffic_with_gate_closed_is_silent` → `[]` (BARRIER-04)
- `test_light_swing_is_silent` → `[]` (BARRIER-04)

These are pure `step(ratio)`-driven sequences. RESEARCH's Hysteresis Validation table (`:528-539`) gives the exact expected sequences to encode — and the negative scenarios are driven by *ratio sequences*, not by frames, so they need no CV at all. That keeps this module fast and mock-free.

---

### `tests/test_replay.py` (test, streaming / file-I/O)

**Analog:** `tests/test_supervisor.py` — the existing module that consumes a generator to completion and injects fakes for every external collaborator.

**Generator-consumption pattern** (`tests/test_supervisor.py:42-61`):
```python
def _run_supervisor(read_frame, session_mgr, stop_event, start=None, **kwargs):
    """Run the supervisor to completion and return (frames, stop_event, procs)."""
    procs = []

    def _start(url, headers):
        proc = FakeProc()
        procs.append((url, headers, proc))
        return proc

    supervisor = run_capture_with_supervisor(
        start or _start, stop_event, session_mgr, "cam_1",
        "https://cam2.privratnik.net/80146f20_3105/preview.mp4",
        read_frame=read_frame, **kwargs,
    )
    frames = list(supervisor)
    return frames, stop_event, procs
```
`frames = list(supervisor)` is the idiom to reuse for `list(ReplaySource(clip_dir))`. The `start or _start` default-parameter override and the `**kwargs` passthrough are the house way to make a test inject collaborators without patching.

**Fake collaborator pattern** (`tests/test_supervisor.py:14-26`) — plain classes, not `unittest.mock`, for objects with recorded behaviour:
```python
class FakeProc:
    """A fake ffmpeg Popen handle that records kill/wait calls."""

    def __init__(self):
        self.killed = False
        self.waited = False

    def kill(self):
        self.killed = True

    def wait(self):
        self.waited = True
```
`unittest.mock` is used only for wholesale external boundaries (`requests.Session`, `subprocess.Popen`) — see `tests/test_auth.py:47` and `tests/test_stream_client.py:81`.

**Temp-directory fixtures:** `test_replay_is_deterministic` writes numbered JPEGs into a temp dir. pytest's built-in `tmp_path` fixture is the natural vehicle and is consistent with the house "no committed binary fixtures" stance (`02-RESEARCH.md:582` — synthetic fixtures generated deterministically, real footage gitignored). No existing test uses `tmp_path` yet, so this is new but idiomatic.

**The Pitfall 1 regression guard** (`test_bounded_buffer_replay_does_not_hang`) must NOT use `for _ in range(N): buf.pop()`. Valid patterns are `02-RESEARCH.md:289-292`. Note that the existing `tests/test_frame_buffer.py:56-61` *does* use a counted `pop()` loop — it is safe only because it pushes exactly `maxsize` frames (`maxsize=5`, pushes 3). Any new test must preserve that invariant or use pattern B/C. **`02-RESEARCH.md:645` recommends adding `pytest-timeout`** because this failure mode is a hang, not a failure — flag it as a `requirements.txt` decision for the planner (it would need the package-legitimacy gate; RESEARCH tags it `[ASSUMED]`).

---

### `tests/conftest.py` (test fixture — MODIFIED)

**Analog:** itself. Extend in place; do not create a second conftest (there is only one `conftest.py` in the repo, at `tests/conftest.py`, and `pytest.ini:2` sets `testpaths = tests`).

**Existing content to preserve verbatim** (`tests/conftest.py:1-29`):
```python
"""Shared pytest fixtures for the frame-buffer and config tests."""

import pytest

from src.capture.frame_buffer import FrameBuffer


@pytest.fixture
def settings():
    """A minimal Settings dict mirroring config.json defaults."""
    return {
        "cameras": {
            "cam_1": "https://cam2.privratnik.net/80146f20_3105/preview.mp4",
            "cam_2": "https://cam2.privratnik.net/f9456e90_3099/preview.mp4",
        },
        "queue_size": 15,
        "capture_fps": 1.5,
        "frame_stale_seconds": 12,
        "backoff_initial": 1.0,
        "backoff_max": 60.0,
        "ffmpeg_path": "ffmpeg",
        "referer": "https://privratnik.net/files/video-control.php",
    }


@pytest.fixture
def frame_buffer():
    """A small FrameBuffer for edge-case tests."""
    return FrameBuffer("cam_1", maxsize=3)
```

**Critical coupling:** the `settings` fixture **duplicates `config.json` by hand** (`config.json:1-13`). When the `rois` and `barrier` keys are added to `config.json`, this fixture becomes a stale mirror. Either update it in the same commit, or make it read the real file. Do not leave it diverged — `tests/test_config.py:9-13` already asserts against the real `config.json`, so a drift here would produce a fixture that tests nothing real.

**Additions required** (`02-VALIDATION.md:68`): a deterministic synthetic-frame factory using `np.random.default_rng(seed)` with an arm-angle parameter and optional car/truck/bus/shadow/brightness/degradation, plus a `FrameBuffer`-independent replay helper. Style: `@pytest.fixture` returning a **factory callable** (`def _make(...) -> frame`), matching the module's existing "fixture returns a ready-made object" simplicity while allowing parametrisation. Keep the seed an explicit defaulted argument so every test is reproducible — determinism is the phase's stated requirement (`02-RESEARCH.md:539`).

---

### `config.json` (config — MODIFIED)

**Analog:** itself + the loader at `src/config.py:19-32`.

**Backward compatibility is already proven** — `load_settings` spreads the raw JSON (`{**raw, ...}`), so unknown keys pass through untouched:
```python
def load_settings(config_path="config.json"):
    with open(config_path, encoding="utf-8") as f:
        raw = json.load(f)
    return {
        **raw,
        "login": os.getenv("PRIVRATNIK_LOGIN"),
        "password": os.getenv("PRIVRATNIK_PASSWORD"),
    }
```
Consequence: adding `rois` and `barrier` needs **no change to `src/config.py`** and cannot break Phase 1. This is `02-RESEARCH.md` assumption A4, verified by reading the source.

**Target shape** (`02-RESEARCH.md:501-519`):
```jsonc
{
  "cameras": { "cam_1": "...", "cam_2": "..." },   // existing, untouched
  "rois": {
    "cam_1": {
      "band":  { "x": [310, 330], "y": [150, 235] },
      "sweep": [[300, 235], [600, 235], [600, 120], [300, 120]],
      "lane_y": 235
    },
    "cam_2": { ... }
  },
  "barrier": {
    "release": 0.25, "seat": 0.35, "confirm_frames": 2, "alpha": 0.88
  }
}
```
**Constraints:**
- Non-secrets only, committed — the same rule `tests/test_config.py:24-28` enforces for `login`/`password`. Do not add a fixture path or any credential here.
- The `cam_2` zero-placeholder in RESEARCH is deliberate. Do not ship zeros: `02-RESEARCH.md:521` requires both cameras' ROIs be authored against real stills with an operator-review checkpoint. Note `band.y` upper bound `150..235` is the empirically-validated value from Pitfall 5 (`02-RESEARCH.md:318-324`) — it must stay strictly above the traffic lane.
- ROI bounds must be validated at load/startup (`02-RESEARCH.md:667`), per the `_preflight_ffmpeg` shape cited under `barrier.py` above.

**Also modified: `.gitignore`.** Add `fixtures/` (`02-RESEARCH.md:278`) — mirrors the existing `.env` / `data/` treatment (`\.gitignore:2,8`). Real gate footage is personal data (`02-RESEARCH.md:670`).

---

### `tools/roi_overlay.py` (utility, file-I/O — offline)

**Analog:** `scripts/probe_privratnik_auth.py` — the codebase's only existing standalone, manual-only utility script. `tools/` does not exist yet.

**Structure to copy** (`scripts/probe_privratnik_auth.py:1-27, 125-129, 181-182`):
```python
#!/usr/bin/env python
"""Standalone empirical auth probe for privratnik.net (A1/A2).

MANUAL-ONLY: requires real credentials in ``.env`` ...

Never prints the raw token or password — all output is redacted.
"""

import os
import sys

# ... helpers ...


def main():
    login = os.getenv("PRIVRATNIK_LOGIN")
    password = os.getenv("PRIVRATNIK_PASSWORD")
    if not login or not password:
        print("FAIL: PRIVRATNIK_LOGIN / PRIVRATNIK_PASSWORD not set in .env")
        sys.exit(1)
    # ...


if __name__ == "__main__":
    main()
```
Conventions: shebang, a docstring that says **MANUAL-ONLY** and states the precondition, `print()` is acceptable here (unlike `src/` modules), a single `main()` with `if __name__ == "__main__"`, and `sys.exit(code)` for pass/fail. `tools/roi_overlay.py` should read its inputs from **argv** (the VALIDATION map documents `python -m tools.roi_overlay --frame <still> --roi <json>`, `02-VALIDATION.md:82`) rather than env vars — so use `argparse`, which the codebase has not used before but which is stdlib and matches the "utility CLI" role.

**Core render pattern** (`02-RESEARCH.md:483-496`, verbatim — verified writing and reloading a PNG on this headless build):
```python
def write_roi_overlay(frame, band, sweep_poly, lane_y, out_path):
    """Draw ROIs onto a still frame and save as PNG for operator inspection.

    Band GREEN, sweep polygon ORANGE, lane cut BLUE. The overlay makes the two
    critical properties checkable by eye: the band must sit ABOVE the lane cut,
    and the sweep polygon must not include the lane.
    """
    y0, y1, x0, x1 = band
    overlay = frame.copy()
    cv2.polylines(overlay, [np.array([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], np.int32)],
                  True, (0, 255, 0), 2)
    cv2.polylines(overlay, [np.array(sweep_poly, np.int32)], True, (0, 165, 255), 2)
    cv2.line(overlay, (0, lane_y), (frame.shape[1], lane_y), (255, 0, 0), 1)
    cv2.imwrite(out_path, overlay)          # headless: PNG is the only review path
```
**Hard constraint (Pitfall 6, `02-RESEARCH.md:326-331`):** `cv2.imshow` / `namedWindow` **do not exist** in `opencv-python-headless`. PNG export is the only viable path. Any GUI call is a crash.

**Invocation (verified on this host):** because the tool imports `validate_roi`/`band_from_config` from `src.detect.barrier`, it must be run from the project root as `python -m tools.roi_overlay --frame <still> --roi <json>`. The direct form `python tools/roi_overlay.py` fails with `ModuleNotFoundError: No module named 'src'` — Python places the script's own directory (`tools/`), not the project root, first on `sys.path`. This is the one place the tool diverges from its analog: `scripts/probe_privratnik_auth.py` is runnable directly only because it imports no first-party modules. Resolve it with the `-m` form, not with a `sys.path` hack.

**No automated test.** `scripts/probe_privratnik_auth.py` has no test module — the same applies here; it is verified by the operator looking at the PNG (`02-VALIDATION.md:83`).

---

## Shared Patterns (cross-cutting)

### 1. Logging convention
**Source:** every `src/` module — `logger = logging.getLogger(__name__)` immediately after imports (`stream_client.py:24`, `auth.py:22`, `supervisor.py:24`, `main.py:21`).
**Apply to:** all new `src/detect/*` modules.
Format strings use `%s` lazy interpolation with named context, never f-strings in log calls:
```python
logger.warning("camera=%s capture error: %s; re-authing and reconnecting", camera_id, exc)   # supervisor.py:60
logger.debug("stream_headers_and_url camera=%s url=%s", camera_id, _redact_url(url))         # auth.py:165
```
`logging.basicConfig(level=logging.INFO)` is configured once, in `main()` (`main.py:57`) — never in library modules.

### 2. Named-error discipline ("make failures visible")
**Source:** `src/capture/auth.py:25-32` (`AuthExpiredError`), `src/capture/supervisor.py:27-28` (`StreamStaleError`).
**Apply to:** `barrier.py` (invalid frame), `replay.py` (missing/empty clip dir), config validation.
One-line or short docstring stating **what triggers it and who catches it**. Never a bare `except Exception: pass` around a real failure — the only such constructs in Phase 1 are explicitly annotated `# pragma: no cover - best-effort` (`stream_client.py:72-78`, `supervisor.py:63`, `:128-133`), and each has a comment justifying why swallowing is correct. Copy that annotation style if you must swallow.

### 3. Injectability for tests
**Source:** `src/capture/supervisor.py:31-33` — `clock=time.monotonic, read_frame=None` as defaulted parameters; docstring at `:41-43` says so explicitly ("injectable for tests").
**Apply to:** any new collaborator the tests must control. Do **not** use `mock.patch` on internals when a defaulted parameter will do — Phase 1 patches only true external boundaries (`requests.Session`, `subprocess.Popen`).

### 4. No wall clock in the detection path
**Source:** `02-RESEARCH.md:251` (anti-pattern) — reinforced by `supervisor.py`'s injectable clock.
**Apply to:** `fsm.py` (hard requirement) and `replay.py` (timestamps from frame index).
The existing codebase *does* call `time.time()` / `time.monotonic()` (`frame_buffer.py:36`, `supervisor.py:33`) — the new package intentionally diverges. Say so in the docstring rather than leaving the divergence unexplained.

### 5. Config/secrets separation
**Source:** `src/config.py:19-32`, enforced by `tests/test_config.py:24-28`.
**Apply to:** `config.json` edits.
Non-secrets in `config.json` (committed); secrets via `os.getenv()` only, no defaults. Phase 2 adds no secrets — do not introduce a fixture path or credential key.

### 6. Headless-only CV output
**Source:** Pitfall 6, `02-RESEARCH.md:326-331`; `CLAUDE.md` mandates `opencv-python-headless`.
**Apply to:** `tools/roi_overlay.py` and any debug visualisation.
`cv2.imwrite` → PNG. Never `cv2.imshow`.

### 7. Import style
**Source:** `stream_client.py:17-18`, `tests/*` — `import cv2` / `import numpy as np` (third-party), then `from src.capture.x import y` (absolute project imports), never relative imports.
**Apply to:** all new modules and tests. `pytest.ini:3` (`pythonpath = .`) is what makes `src.*` resolvable.

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| `src/detect/fsm.py` | store (state machine) | event-driven | No state machine exists anywhere in the codebase. `frame_buffer.py` supplies the class *shape* only. **Algorithm source: `02-RESEARCH.md:399-447`** (validated by simulation). Highest-risk novelty in the phase — the intermediate dead-band behaviour must be tested directly, since no precedent validates it. |
| `src/detect/__init__.py` | package marker | n/a | `src/capture/` has no `__init__.py` (namespace package). Decide deliberately whether to match that or introduce the project's first regular package. |

Partially novel (has a shape analog but no algorithmic precedent — use RESEARCH for the body):
- `src/detect/barrier.py` — cv2/numpy module form from `stream_client.py`; ROI signal algorithm from `02-RESEARCH.md:339-397`.
- `tools/roi_overlay.py` — script form from `scripts/probe_privratnik_auth.py`; rendering from `02-RESEARCH.md:477-497`.

## Metadata

**Analog search scope:** `git ls-files` (whole repo), `src/` (all 6 modules read in full), `tests/` (all 5 test modules + conftest read in full), `scripts/`, root config files (`config.json`, `pytest.ini`, `requirements.txt`, `.gitignore`).

**Tracked-source gate (#3645) — verified this session.** Every analog path named above satisfies `git ls-files -- <path>`:
`src/capture/auth.py`, `src/capture/frame_buffer.py`, `src/capture/stream_client.py`, `src/capture/supervisor.py`, `src/config.py`, `src/main.py`, `tests/conftest.py`, `tests/test_auth.py`, `tests/test_config.py`, `tests/test_frame_buffer.py`, `tests/test_stream_client.py`, `tests/test_supervisor.py`, `scripts/probe_privratnik_auth.py`, `config.json`, `.gitignore`, `pytest.ini`. No `.gsd/` mirror path is referenced anywhere in this document.

**Files scanned:** 6 source modules + 6 test modules + 4 root config files + 1 script = 17 application files read; 4 planning docs read (`02-RESEARCH.md`, `02-VALIDATION.md`, `ROADMAP.md`, `01-PATTERNS.md`) + `01-REVIEW-FIX.md`.

**Not present on disk (confirmed):** `src/detect/` (does not exist), `tools/` (does not exist), `fixtures/` (does not exist), any `__init__.py` anywhere.

**Pattern extraction date:** 2026-09-23

**Config flags honored:** `workflow.nyquist_validation: true` (test framework + per-requirement test map cited from `02-VALIDATION.md`), `security_enforcement: true` / `security_asvs_level: 1` (V5 input-validation controls — `None`-frame guard, ROI-bounds validation, generator discipline, path resolution — mapped to concrete analog code above).
