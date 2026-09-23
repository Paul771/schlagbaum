# Roadmap: Smart Schlagbaum

## Overview

A local barrier-gate video analytics system. It fetches frames from two IP cameras through the `privratnik.net` proxy, detects when the barrier opens, detects the passing vehicle, reads its license plate via an external OCR service (with a local Tesseract offline fallback), and persists an event with a photo to a local SQLite database — all without manual intervention. The build follows a dependency-ordered, working-slice-at-every-step progression that tackles the riskiest unknown first (the proxy-stream authentication/reconnect behavior) and ends with persistent, self-healing deployment on the user's Windows PC.

## Phases

- [ ] **Phase 1: Stream Client + Frame Buffer** - Two-camera frame capture via privratnik.net with auth, bounded queue, supervise/reconnect loop, config/secrets
- [ ] **Phase 2: Barrier State Detector** - Reliable open/closed/partial detection with ROI+hysteresis and false-positive resistance
- [ ] **Phase 3: Event Store + Photo Store + Event Coordinator (FSM)** - Persist one event per barrier opening with photo evidence; FSM emits exactly one event; dedup
- [ ] **Phase 4: Vehicle Detector** - Detect cars to gate OCR calls and select the best frame
- [ ] **Phase 5: Plate Recognizer** - External ALPR with confidence, offline Tesseract fallback, swappable provider
- [ ] **Phase 6: Deployment & Startup Hardening** - Automatic boot-start, restart-on-failure, health check, file logging

## Phase Details

### Phase 1: Stream Client + Frame Buffer

**Goal**: Two cameras capture continuous frames through the privratnik.net proxy with working auth, an auto-reconnect/re-auth loop, a bounded drop-oldest queue that keeps capture non-blocking, and a config/secrets structure feeding the first token.
**Mode**: mvp
**Depends on**: Nothing (first phase)
**Requirements**: STREAM-01, STREAM-02, STREAM-03, STREAM-04, STREAM-05
**Success Criteria** (what must be TRUE):

  1. System connects to both cameras and delivers a continuous frame stream without manual intervention.
  2. When the stream session or token expires, the system re-authenticates and reconnects automatically within a few seconds, resuming capture (verified against the real stream, not just docs).
  3. Frames are pushed to a bounded drop-oldest queue, so a slow downstream stage never blocks or stalls frame capture.
  4. Every frame/event is tagged with the correct `camera_id`, so frames from camera 1 and camera 2 are distinguishable.
  5. Tokens, camera URLs, and secrets come from env/config, not hardcoded source, and secrets are not committed.

**Plans**: 2/2 plans executed

Plans:
**Wave 1**

- [x] 01-01-PLAN.md — Walking Skeleton data layer: config/secrets + bounded drop-oldest frame buffer

**Wave 2** *(blocked on Wave 1 completion)*

- [x] 01-02-PLAN.md — Capture path: auth session manager + ffmpeg stream client + reconnect supervisor

### Phase 2: Barrier State Detector

**Goal**: The system reliably reports whether the barrier is closed, opening, open, or closing across state transitions, while ignoring cars passing with the gate closed.
**Mode**: mvp
**Depends on**: Phase 1
**Requirements**: BARRIER-01, BARRIER-02, BARRIER-03, BARRIER-04
**Success Criteria** (what must be TRUE):

  1. System reports the barrier state as closed / opening / open / closing to match real gate footage.
  2. A car passing with the gate closed, shadows, or lighting change does not trigger a false "open" event (ROI + hysteresis filtering).
  3. State transitions follow CLOSED→OPENING→OPEN→CLOSING without spurious duplicate transitions.
  4. Barrier detection is validated against recorded real footage of both a genuine opening and a car passing with the gate closed.

**Plans**: 3 plans

Plans:
- [ ] 02-01-PLAN.md — Tracer: synthetic clip → ReplaySource → closed_ratio → BarrierFSM transition, plus the signal layer and named failures
- [ ] 02-02-PLAN.md — Four-state FSM with two-sided hysteresis, dead band, and edge-only emission; duplicate suppression and false-positive resistance
- [ ] 02-03-PLAN.md — Degradation/determinism/deadlock hardening, ROI config surface with startup validation, and the user-gated SC4 real-footage acceptance test

### Phase 3: Event Store + Photo Store + Event Coordinator (FSM)

**Goal**: Every barrier opening persists as exactly one event record with a photo as evidence, driven by a state machine that prevents duplicate events and an async OCR queue that never blocks capture.
**Mode**: mvp
**Depends on**: Phase 2
**Requirements**: STORE-01, STORE-02, STORE-03, STORE-04, COORD-01, COORD-02
**Success Criteria** (what must be TRUE):

  1. Each barrier opening produces exactly one SQLite event record (type, timestamp, camera_id, plate, photo path), with no duplicate events.
  2. The event photo is saved as a file on disk and the DB stores the path, not the image blob.
  3. OCR runs on a separate thread fed by a bounded queue, so slow recognition never blocks frame capture or event logging.
  4. The FSM emits an event only on a real state transition, not on every frame where the barrier is open.
  5. The DB schema is independent of any specific OCR provider — the provider is pluggable.

**Plans**: TBD

### Phase 4: Vehicle Detector

**Goal**: The system detects a vehicle (car/truck/bus) in the frame to decide when OCR should run and to select the best frame for recognition.
**Mode**: mvp
**Depends on**: Phase 3
**Requirements**: VEHICLE-01, VEHICLE-02, VEHICLE-03
**Success Criteria** (what must be TRUE):

  1. System detects a vehicle (car/truck/bus) in the frame using the chosen detection model.
  2. OCR is invoked only when a vehicle is present, so frames without a vehicle consume no external OCR budget.
  3. System selects the best-quality candidate frame for plate recognition rather than the first available frame.

**Plans**: TBD

### Phase 5: Plate Recognizer

**Goal**: The system reads the license plate via an external ALPR API with a confidence score, degrades to local Tesseract when the API/network is down, and keeps the provider swappable.
**Mode**: mvp
**Depends on**: Phase 4
**Requirements**: PLATE-01, PLATE-02, PLATE-03, PLATE-04
**Success Criteria** (what must be TRUE):

  1. System reads a plate via the external ALPR API and stores the recognized text.
  2. When the external API or network is unavailable, system falls back to local Tesseract OCR and records the event with the fallback result instead of dropping it.
  3. Each recognition is stored with a confidence score, and results below the threshold are flagged as low-confidence.
  4. The OCR provider is abstracted behind a swappable interface, so the provider can be changed without touching event storage or detection.

**Plans**: TBD

### Phase 6: Deployment & Startup Hardening

**Goal**: The system starts on boot, restarts after a crash, survives sleep/wake, and confirms it is alive and capturing via a health check and file logging.
**Mode**: mvp
**Depends on**: Phase 5
**Requirements**: COORD-03
**Success Criteria** (what must be TRUE):

  1. System starts automatically on PC boot/login via Windows Task Scheduler or a service, without manual launch.
  2. After a crash, the system restarts automatically and resumes capture and event processing.
  3. System survives sleep/wake cycles and resumes the capture pipeline after wake.
  4. A health check confirms the service is running and capturing, and operational logs are written to a file.

**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 6

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Stream Client + Frame Buffer | 2/2 | In Progress|  |
| 2. Barrier State Detector | 0/TBD | Not started | - |
| 3. Event Store + Photo Store + Event Coordinator (FSM) | 0/TBD | Not started | - |
| 4. Vehicle Detector | 0/TBD | Not started | - |
| 5. Plate Recognizer | 0/TBD | Not started | - |
| 6. Deployment & Startup Hardening | 0/TBD | Not started | - |
