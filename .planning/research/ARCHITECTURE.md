# Architecture Research

**Domain:** Video analytics / license plate recognition (LPR/ANPR) from IP camera streams at a barrier gate
**Researched:** 2026-09-09
**Confidence:** HIGH (core pipeline patterns) / MEDIUM (OCR service specifics) / LOW (OCR service pricing/maintenance)

## Standard Architecture

### System Overview

A video-analytics LPR system is a **linear streaming pipeline** with a feedback loop for state detection. The canonical shape is: capture → decode → analyze → recognize → persist. Each stage is a separate component with a single responsibility, connected by queues/buffers so a slow stage (OCR) never blocks a fast stage (frame capture).

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        INGESTION LAYER                                  │
│  ┌──────────────┐   ┌──────────────┐   ┌─────────────────────────────┐  │
│  │ Camera 1     │   │ Camera 2     │   │ privratnik.net proxy        │  │
│  │ (MP4 stream) │   │ (MP4 stream) │   │ (auth + token + Referer)    │  │
│  └──────┬───────┘   └──────┬───────┘   └──────────────┬──────────────┘  │
│         │                 │                           │                 │
│         └─────────────────┴───────────┬───────────────┘                 │
│                                       ▼                                 │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │              Stream Client (ffmpeg / OpenCV VideoCapture)         │  │
│  │              decodes MP4 → raw frames at controlled rate          │  │
│  └──────────────────────────────────┬──────────────────────────────┘  │
├──────────────────────────────────────┼────────────────────────────────┤
│                                       ▼                                 │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │                    ANALYSIS LAYER (per camera)                    │  │
│  │  ┌──────────────────┐   ┌──────────────────┐   ┌───────────────┐  │  │
│  │  │ Barrier State     │   │ Vehicle          │   │ Plate         │  │  │
│  │  │ Detector          │   │ Detector        │   │ Recognizer    │  │  │
│  │  │ (motion/ROI diff) │   │ (YOLO / motion) │   │ (OCR service)  │  │  │
│  │  └────────┬─────────┘   └────────┬─────────┘   └───────┬───────┘  │  │
│  │           │                      │                      │          │  │
│  │           └──────────┬───────────┴──────────────────────┘          │  │
│  │                      ▼                                              │  │
│  │  ┌──────────────────────────────────────────────────────────────┐  │  │
│  │  │                 Event Coordinator (state machine)            │  │  │
│  │  │  decides: barrier opened? vehicle present? → emit event      │  │  │
│  │  └──────────────────────────────┬───────────────────────────────┘  │  │
│  └─────────────────────────────────┼──────────────────────────────────┘  │
├────────────────────────────────────┼────────────────────────────────────┤
│                                    ▼                                     │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │                    PERSISTENCE LAYER                               │  │
│  │  ┌──────────────────────┐   ┌──────────────────────────────────┐  │  │
│  │  │ SQLite (events)      │   │ Filesystem (photos/thumbnails)  │  │  │
│  │  │ event, time, camera, │   │ photo path referenced by event   │  │  │
│  │  │ plate, confidence    │   │                                  │  │  │
│  │  └──────────────────────┘   └──────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
```

### Component Responsibilities

| Component | Responsibility | Typical Implementation |
|-----------|----------------|------------------------|
| Stream Client | Connect to camera via proxy, authenticate, decode MP4, emit frames at controlled rate | ffmpeg subprocess piped to OpenCV, or `cv2.VideoCapture(url)` |
| Frame Buffer | Decouple capture from analysis; drop frames when analysis is slow | `queue.Queue(maxsize=N)` — bounded, drop-oldest |
| Barrier State Detector | Detect open/close transition of the barrier from frame differences in a fixed ROI | Background subtraction (`MOG2`) or ROI pixel-diff thresholding |
| Vehicle Detector | Detect a car present in the frame (triggers plate capture) | YOLO object detection, or motion blob in ROI |
| Plate Recognizer | Extract plate region, call OCR, return plate string + confidence | External API (Plate Recognizer / Google Vision) with Tesseract fallback |
| Event Coordinator | State machine: combine barrier state + vehicle presence → decide when to emit a persisted event | Finite state machine (closed→opening→open→closing) |
| Event Store | Persist event records (type, time, camera, plate, photo path) | SQLite table |
| Photo Store | Save the captured frame(s) as files referenced by events | Filesystem directory |
| Scheduler | Run the whole pipeline on a schedule / keep it alive | Windows Task Scheduler / a supervisor loop |

## Recommended Project Structure

```
schlagbaum/
├── src/
│   ├── capture/            # Stream ingestion & frame extraction
│   │   ├── stream_client.py   # ffmpeg/OpenCV decode, auth, token handling
│   │   ├── frame_buffer.py    # bounded queue, drop-oldest policy
│   │   └── camera.py          # per-camera config & lifecycle
│   ├── analysis/           # Frame analysis
│   │   ├── barrier_detector.py  # ROI diff / background subtraction
│   │   ├── vehicle_detector.py  # YOLO or motion blob
│   │   └── plate_recognizer.py  # OCR service client + Tesseract fallback
│   ├── events/             # Event coordination & state machine
│   │   ├── coordinator.py     # FSM combining detector outputs
│   │   └── models.py          # Event dataclass / schema
│   ├── storage/            # Persistence
│   │   ├── db.py              # SQLite connection & schema
│   │   ├── event_repo.py      # event CRUD
│   │   └── photo_store.py     # save/load photo files
│   ├── config.py          # cameras, tokens, thresholds, OCR settings
│   └── main.py            # entrypoint, wires components, scheduler loop
├── data/                  # runtime data (gitignored)
│   ├── events.db          # SQLite
│   └── photos/            # captured frames
├── tests/
└── requirements.txt
```

### Structure Rationale

- **capture/ vs analysis/ vs events/ vs storage/:** mirrors the linear pipeline — each layer only depends on the one before it. This makes each stage independently testable (you can feed a recorded video into analysis without a live camera).
- **events/ as its own module:** the state machine is the "brain" that decides *when* to persist. Keeping it separate from raw detectors lets you change trigger logic without touching CV code.
- **storage/ split into db + photo_store:** SQLite holds structured event metadata; photos are files on disk referenced by path. This avoids bloating the DB with BLOBs and keeps photo access cheap.
- **config.py centralizes** camera URLs, tokens, thresholds, and OCR keys — so the pipeline is data-driven, not hardcoded.

## Architectural Patterns

### Pattern 1: Producer–Consumer Pipeline with Bounded Queue

**What:** Each stage is a producer for the next and a consumer of the previous, connected by a bounded queue. The capture thread reads frames and pushes to a queue; the analysis thread pops and processes.
**When to use:** Always for video pipelines — capture runs at camera FPS, analysis/OCR is much slower. Without a buffer, a slow OCR call would stall capture and drop the very event you want.
**Trade-offs:** Bounded queue with drop-oldest means you may skip frames under load — acceptable because you only need *one* good frame per event, not every frame.

**Example:**
```python
import queue, threading

frame_q = queue.Queue(maxsize=5)  # bounded: drop-oldest when full

def capture_loop(cap):
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        if frame_q.full():
            try: frame_q.get_nowait()  # drop oldest
            except queue.Empty: pass
        frame_q.put(frame)

def analysis_loop():
    while True:
        frame = frame_q.get()
        process(frame)  # barrier + vehicle + plate
```

### Pattern 2: Finite State Machine for Event Triggering

**What:** Model the barrier as a state machine: `CLOSED → OPENING → OPEN → CLOSING → CLOSED`. Emit an event only on a meaningful transition (e.g., `OPENING` with a vehicle present), not on every frame.
**When to use:** When you need to avoid duplicate events and false triggers. A naive "barrier is open → save event" fires dozens of times per opening.
**Trade-offs:** Adds a small stateful component, but eliminates the #1 LPR pitfall (event spam / duplicate records).

**Example:**
```python
class BarrierFSM:
    CLOSED, OPENING, OPEN, CLOSING = range(4)
    def __init__(self): self.state = self.CLOSED
    def on_frame(self, barrier_open: bool, vehicle_present: bool):
        if self.state == self.CLOSED and barrier_open:
            self.state = self.OPENING
            return ("opening", vehicle_present)   # emit event
        if self.state == self.OPENING and barrier_open:
            self.state = self.OPEN
        if self.state in (self.OPENING, self.OPEN) and not barrier_open:
            self.state = self.CLOSING
        if self.state == self.CLOSING and not barrier_open:
            self.state = self.CLOSED
        return None
```

### Pattern 3: External OCR with Local Fallback (Strategy)

**What:** Define a `PlateRecognizer` interface; implement a cloud client (Plate Recognizer / Google Vision) and a local Tesseract fallback. Try cloud first, fall back on network failure or low confidence.
**When to use:** When accuracy matters but you must stay operational offline. The cloud service gives high accuracy; Tesseract keeps the system alive when the network/API is down.
**Trade-offs:** Two code paths to maintain, but resilience is worth it for a "no missed events" requirement.

**Example:**
```python
class PlateRecognizer:
    def recognize(self, image) -> PlateResult: ...
class CloudRecognizer(PlateRecognizer): ...   # POST image -> JSON
class TesseractRecognizer(PlateRecognizer): ... # local fallback

def recognize_with_fallback(image, cloud, local):
    try:
        r = cloud.recognize(image)
        if r.confidence >= 0.7: return r
    except Exception:
        pass
    return local.recognize(image)
```

## Data Flow

### Request Flow (event capture)

```
Camera frame
    ↓
Stream Client (decode) → Frame Buffer (bounded queue)
    ↓
Barrier Detector → barrier_open (bool)
Vehicle Detector → vehicle_present (bool)
    ↓
Event Coordinator (FSM) → decision: emit event?
    ↓
Plate Recognizer → plate string + confidence
    ↓
Event Store (SQLite) ← photo path
Photo Store (filesystem) ← saved frame
```

### State Management

```
BarrierFSM (per camera)
    ↓ (on each frame)
[barrier_open, vehicle_present] → transition → [event decision]
    ↓
[Event] → Event Store → SQLite
```

### Key Data Flows

1. **Frame flow:** Camera → decode → bounded queue → analysis. The queue is the decoupling point; capture never blocks on slow analysis.
2. **Event flow:** Detector outputs (booleans) → FSM → event decision → plate OCR → persist event + photo. The FSM is the gate that prevents duplicate events.
3. **Photo flow:** On event, the current frame is saved to `data/photos/` and its path stored in the SQLite event row. The DB references the file; it does not embed it.

## Scaling Considerations

| Scale | Architecture Adjustments |
|-------|--------------------------|
| 1 camera, 1 gate (MVP) | Single process, threads per stage. Monolith is fine. |
| 2 cameras (this project) | One analysis thread per camera, or one shared thread round-robin. Still one process. |
| 10+ cameras / multiple gates | Split analysis into worker processes (multiprocessing) or a message broker (Redis/RabbitMQ); move OCR to a queue-backed worker pool. |

### Scaling Priorities

1. **First bottleneck:** OCR latency. A single cloud OCR call can take 0.5–2s. If the analysis thread blocks on OCR, it misses subsequent frames. Fix: run OCR on a separate worker thread/queue so frame analysis continues; only the event finalization waits on OCR.
2. **Second bottleneck:** CPU for vehicle detection (YOLO). If using a heavy model, downscale frames before detection or run detection at a lower FPS than capture. Fix: detect at 2–5 FPS, capture at full FPS.

## Anti-Patterns

### Anti-Pattern 1: Blocking the capture loop on slow analysis

**What people do:** Read a frame, run OCR synchronously in the same loop, then read the next frame.
**Why it's wrong:** OCR takes seconds; the camera buffer overflows and you drop the frames containing the event you're trying to catch. Missed events = the core value is broken.
**Do this instead:** Decouple capture from analysis with a bounded queue; run OCR on a separate thread. Capture always keeps up with the camera.

### Anti-Pattern 2: Saving an event on every "barrier is open" frame

**What people do:** `if barrier_open: save_event()` in the frame loop.
**Why it's wrong:** A single opening produces dozens of duplicate events, flooding the DB and making audit useless.
**Do this instead:** Use the FSM to emit exactly one event per open/close transition.

### Anti-Pattern 3: Storing photos as BLOBs in SQLite

**What people do:** `INSERT INTO events (photo_blob) VALUES (?)` with the full JPEG.
**Why it's wrong:** SQLite grows huge, backups are slow, and reading a photo requires a DB query. 
**Do this instead:** Save the frame to a file, store the path in the event row. Keep the DB small and fast.

### Anti-Pattern 4: Hardcoding camera tokens/URLs in source

**What people do:** Embed the `privratnik.net` token and camera URL in the code.
**Why it's wrong:** Tokens rotate; the user resets them from DevTools. Hardcoding means editing source to update, and risks committing secrets.
**Do this instead:** Centralize in `config.py` / a config file / env vars, and treat tokens as secrets (never commit).

## Integration Points

### External Services

| Service | Integration Pattern | Notes |
|---------|---------------------|-------|
| privratnik.net proxy | HTTP GET with `Referer` + `Range: bytes=0-` + cookie `PHPSESSID`; token in query param | Token rotates; session cookie required; stream is MP4 preview via ffmpeg |
| Plate Recognizer / Google Vision OCR | POST image → JSON with plate + confidence | External OCR; network dependency; fall back to Tesseract offline |
| Tesseract (local) | Local subprocess/`pytesseract` | Offline fallback; lower accuracy on angled plates |

### Internal Boundaries

| Boundary | Communication | Notes |
|----------|---------------|-------|
| capture ↔ analysis | Bounded queue (frames) | Drop-oldest policy; never block capture |
| analysis ↔ events | Direct function call (booleans) | Detectors are stateless; FSM holds state |
| events ↔ storage | Direct call (Event object) | Event Coordinator calls Event Store + Photo Store |
| storage ↔ filesystem | Path reference | DB stores photo path, not bytes |

## Build Order (dependencies)

1. **Stream Client + Frame Buffer** — everything depends on getting frames. Validate the privratnik.net auth/token flow first (highest risk).
2. **Barrier State Detector** — simplest analysis; proves the pipeline works end-to-end with a real event.
3. **Event Store + Photo Store + Event Coordinator (FSM)** — persist the barrier-open event with a photo. This is the MVP core value.
4. **Vehicle Detector** — refine when to capture (only when a car is present).
5. **Plate Recognizer (cloud + Tesseract fallback)** — add OCR last; it's the most external-dependency-heavy and can be layered on once events already persist.

**Rationale:** Each step produces a working, testable slice. The riskiest unknown (proxy auth) is tackled first. OCR is deferred because it's the most failure-prone external dependency and doesn't block the core "record every opening" value.

## Sources

- OpenCV Python docs (Context7, `/opencv/opencv-python`) — VideoCapture, frame loop, release pattern. Confidence: MEDIUM.
- deep-license-plate-recognition (Context7, `/parkpow/deep-license-plate-recognition`) — ALPR pipeline stages, stream service, camera status monitoring. Confidence: MEDIUM.
- Plate Recognizer / OpenALPR Cloud — external OCR service structure (websearch). Confidence: LOW (pricing/maintenance unverified this session).

---
*Architecture research for: Smart Schlagbaum (video analytics / LPR at barrier gate)*
*Researched: 2026-09-09*
