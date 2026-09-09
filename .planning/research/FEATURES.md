# Feature Research

**Domain:** Barrier-gate video analytics / license plate recognition (ANPR/LPR) from IP camera streams
**Researched:** 2026-09-09
**Confidence:** MEDIUM

> **Confidence note:** Live web/Context7 sources were unreachable in this environment (network blocked, search budget exhausted). This file is synthesized from established domain knowledge of the ANPR/access-control ecosystem (Genetec AutoVu, Plate Recognizer, OpenALPR, Milestone, Nedap, parking/barrier LPR systems) plus the specific constraints in `PROJECT.md`. Treat vendor-specific accuracy/pricing figures as directional, not verified. The feature *categorization* (table stakes vs differentiators) is high-confidence domain knowledge.

## Feature Landscape

### Table Stakes (Users Expect These)

Features users assume exist. Missing these = product feels incomplete. For a barrier-gate audit system, the core value is **reliable event capture with evidence** — every opening must be recorded with a photo and a readable plate.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| **Barrier open/close detection** | The whole point — "did the gate open, and when?" | MEDIUM | Frame-diff / motion in a fixed ROI around the barrier arm. Must distinguish open vs closed vs partial. False positives (car passing, shadows) are the main risk. |
| **Vehicle detection in frame** | You only want to OCR a plate when a vehicle is actually present, not on every frame | MEDIUM | Background subtraction or YOLO-class detector. Gate the OCR call on this to save API cost. |
| **License plate recognition (external OCR)** | Core deliverable — "whose car went through?" | MEDIUM | External API (Plate Recognizer / OpenALPR Cloud / Google Vision). Accuracy depends on plate size in frame, angle, lighting. |
| **Event logging with timestamp** | Audit trail requires a durable, queryable record | LOW | SQLite row per event: type (open/close), timestamp, camera id, plate, photo path. |
| **Photo storage alongside event** | Evidence — a plate string alone is not proof | LOW | Save JPEG crop of the vehicle/plate + full frame. Store path in DB, file on disk. |
| **Two-camera support** | Project explicitly has two cameras (entry/exit or two lanes) | MEDIUM | Each camera is an independent capture pipeline; events tagged with camera id. |
| **Local database storage (SQLite)** | Autonomy, no cloud dependency, audit retention | LOW | SQLite is the right call for single-node local. No server needed. |
| **Automatic startup / scheduled run** | User expects it to "just run" on the local PC | LOW | Windows Task Scheduler wrapper. Not a feature users praise, but absence = broken product. |
| **Session/token handling for camera stream** | Stream requires auth (PHPSESSID cookie + token + Referer) | MEDIUM | Must maintain/renew the privratnik.net session; token expiry is a real failure mode. |

### Differentiators (Competitive Advantage)

Features that set the product apart. Not required, but valuable. For this project, the differentiators are the ones that make the audit *trustworthy* and *low-maintenance* — not flashy UI.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| **Plate confidence score + low-confidence flagging** | Lets the user trust the data — know which reads are uncertain and need manual review | MEDIUM | External OCR APIs return a confidence. Store it; flag events below a threshold. This is what separates "a log" from "an audit trail." |
| **Offline OCR fallback (Tesseract)** | Keeps the system working when the external API is down or network fails | MEDIUM | Local Tesseract as degraded mode. Lower accuracy but better than nothing. Aligns with PROJECT.md's stated fallback. |
| **Dedup / event coalescing** | Prevents one gate opening from generating 50 near-identical events | MEDIUM | Cooldown window per camera (e.g., ignore re-triggers within N seconds). Without this, the DB fills with noise and the audit is unusable. |
| **Photo crop of plate region** | Better evidence than a full frame — reviewer sees the plate clearly | MEDIUM | Crop around detected plate/vehicle before saving. Requires plate localization (from OCR API bbox or detector). |
| **Retention / cleanup policy** | Prevents unbounded disk growth on a local PC | LOW | Configurable retention (days or max events); auto-purge old photos. Local systems silently fill disks otherwise. |
| **Manual review / correction of misreads** | Lets the user fix a wrong plate and keep the audit accurate | MEDIUM | Simple "edit plate" on an event. High value for an audit tool where accuracy matters. |
| **Export (CSV/JSON)** | Audit data needs to leave the system for reporting | LOW | Cheap to add, high perceived value. |

### Anti-Features (Commonly Requested, Often Problematic)

Features that seem good but create problems. These are the scope-creep traps for this project.

| Feature | Why Requested | Why Problematic | Alternative |
|---------|---------------|-----------------|-------------|
| **Barrier control (open/close by command)** | "Since we detect it, why not control it?" | Safety-critical; a software bug could open a gate into a pedestrian/vehicle. Liability. PROJECT.md explicitly scopes this OUT. | Keep observation-only. Never send control commands. |
| **Real-time alerting (SMS/Telegram/email)** | "Tell me when someone enters" | Adds notification infra, delivery guarantees, and 24/7 uptime expectations. PROJECT.md defers this. | Log events now; add notification in a later phase once capture is reliable. |
| **Mobile app / remote dashboard** | "Check the gate from my phone" | Large surface area (auth, push, hosting) unrelated to core capture value. | Local web UI or file export first. |
| **Training a custom plate model** | "The external OCR isn't perfect" | Requires labeled dataset, training pipeline, model hosting — a project in itself. | Use a good external API + confidence flagging + manual correction. |
| **Continuous full-frame video recording** | "Record everything, not just events" | Massive storage, no clear audit value over event+photo capture. | Event-triggered photo capture only. |
| **Multi-site / multi-gate cloud sync** | "Manage all my gates centrally" | Turns a local tool into a distributed system. | Keep single-node local; export for external use. |
| **Real-time "everything" (sub-second latency)** | "I want to see it live" | Live streaming adds latency/bandwidth complexity with no audit benefit. | Event-driven capture; latency of a few seconds is fine for audit. |

## Feature Dependencies

```
Barrier open/close detection
    └──requires──> Frame capture from camera stream
                       └──requires──> privratnik.net session/token handling

Vehicle detection
    └──requires──> Frame capture
    └──enhances──> Plate recognition (only OCR when vehicle present)

Plate recognition (external OCR)
    └──requires──> Vehicle detection (gate the API call)
    └──enhances──> Event logging (plate is part of the event)

Event logging
    └──requires──> Barrier detection OR vehicle detection (trigger)
    └──requires──> SQLite schema
    └──requires──> Photo storage (file on disk + path in DB)

Offline OCR fallback
    └──requires──> Plate recognition (same interface, different backend)

Dedup/coalescing
    └──requires──> Event logging (operates on the event stream)

Retention/cleanup
    └──requires──> Photo storage (purges old files)

Manual correction
    └──requires──> Event logging (edits stored events)
```

### Dependency Notes

- **Plate recognition requires vehicle detection:** Running OCR on every frame is wasteful (API cost) and noisy. Detect a vehicle first, then OCR the best frame. This is the single most important pipeline ordering.
- **Barrier detection and vehicle detection are independent triggers:** Either can fire an event. A gate opening with no vehicle (someone opened it manually) is still a valid audit event. Don't couple them.
- **Offline fallback shares the plate-recognition interface:** Design the OCR as a pluggable backend so Tesseract can drop in when the external API fails. This is an interface decision, not a feature bolt-on.
- **Dedup operates on the event stream:** It must be applied *after* detection but *before* DB write, so it needs the event pipeline to exist first.

## MVP Definition

### Launch With (v1)

Minimum viable product — what's needed to validate the concept. This is the "reliably capture every opening with a photo and a plate" core.

- [ ] **Frame capture from two cameras** (privratnik.net session/token handling) — without this nothing else works
- [ ] **Barrier open/close detection** — the primary trigger and core value
- [ ] **Vehicle detection** — gates the OCR call and provides the second trigger
- [ ] **Plate recognition via external OCR** — the "whose car" answer
- [ ] **Event logging to SQLite** (type, timestamp, camera, plate, photo path) — the audit record
- [ ] **Photo storage** (full frame + plate crop) — the evidence
- [ ] **Dedup/coalescing** — keeps the log usable (prevents event spam)
- [ ] **Automatic startup** (Task Scheduler) — it must "just run"

### Add After Validation (v1.x)

Features to add once core is working.

- [ ] **Offline OCR fallback (Tesseract)** — trigger: external API reliability becomes a problem in practice
- [ ] **Plate confidence flagging** — trigger: user needs to trust/verify reads
- [ ] **Manual correction of misreads** — trigger: confidence flagging surfaces errors worth fixing
- [ ] **Retention/cleanup policy** — trigger: disk usage becomes noticeable
- [ ] **CSV/JSON export** — trigger: user wants to analyze/report the data

### Future Consideration (v2+)

Features to defer until product-market fit is established.

- [ ] **Notification (SMS/Telegram/email)** — deferred by PROJECT.md; only after capture is proven reliable
- [ ] **Local web UI / dashboard** — only if the user needs to browse events beyond file export
- [ ] **Multi-site / cloud sync** — only if the tool proves useful enough to scale

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Priority |
|---------|------------|---------------------|----------|
| Frame capture (2 cameras, auth) | HIGH | MEDIUM | P1 |
| Barrier open/close detection | HIGH | MEDIUM | P1 |
| Vehicle detection | HIGH | MEDIUM | P1 |
| Plate recognition (external OCR) | HIGH | MEDIUM | P1 |
| Event logging (SQLite) | HIGH | LOW | P1 |
| Photo storage | HIGH | LOW | P1 |
| Dedup/coalescing | MEDIUM | MEDIUM | P1 |
| Automatic startup | MEDIUM | LOW | P1 |
| Offline OCR fallback | MEDIUM | MEDIUM | P2 |
| Plate confidence flagging | MEDIUM | LOW | P2 |
| Manual correction | MEDIUM | MEDIUM | P2 |
| Retention/cleanup | MEDIUM | LOW | P2 |
| CSV/JSON export | MEDIUM | LOW | P2 |
| Notification (SMS/Telegram) | MEDIUM | HIGH | P3 |
| Local web UI | MEDIUM | HIGH | P3 |
| Multi-site sync | LOW | HIGH | P3 |

**Priority key:**
- P1: Must have for launch
- P2: Should have, add when possible
- P3: Nice to have, future consideration

## Competitor Feature Analysis

| Feature | Commercial LPR (Genetec AutoVu, Nedap) | Cloud API (Plate Recognizer, OpenALPR Cloud) | Our Approach |
|---------|--------------|--------------|--------------|
| Barrier open/close detection | Yes (integrated with gate controller) | No (API is plate-only) | **Frame-based detection** (no gate controller access — observation only) |
| Vehicle detection | Yes (trigger-based) | No | **YOLO / background subtraction** to gate OCR |
| Plate recognition | On-prem, high accuracy | Cloud API, high accuracy | **External API** (Plate Recognizer / OpenALPR / Google Vision) + Tesseract fallback |
| Event logging | Full VMS database | No (returns plate, you store it) | **SQLite** local, event+photo |
| Photo evidence | Yes (VMS snapshots) | No | **Full frame + plate crop** saved to disk |
| Confidence scoring | Yes | Yes (API returns confidence) | **Store + flag low-confidence** reads |
| Manual correction | Yes (review UI) | No | **Edit plate on event** (v1.x) |
| Notification | Yes (alerts) | No | **Deferred** (P3) |
| Gate control | Yes (opens/closes) | No | **Explicitly out of scope** (observation only) |
| Cost model | High (hardware+license) | Per-call / subscription | **Low** (local PC + per-call OCR) |

**Key insight:** Commercial LPR systems bundle gate control, VMS, and alerting — all of which we deliberately exclude. Cloud APIs give us the plate-reading accuracy without the hardware cost. Our niche is the *audit* value: reliable event+photo capture on a local PC, with confidence flagging and manual correction that pure-API consumers don't get.

## Sources

- **Project context:** `C:/dev/schlagbaum/.planning/PROJECT.md` (constraints, scope, camera/stream details)
- **Domain knowledge (MEDIUM confidence, not live-verified this session):**
  - ANPR/ALPR pipeline architecture (capture → plate localization → OCR → database matching) — Wikipedia "Automatic number-plate recognition"
  - Commercial LPR vendors: Genetec AutoVu, Milestone XProtect, Nedap ANPR, Axis (access-control / parking LPR feature sets)
  - Cloud OCR APIs: Plate Recognizer, OpenALPR Cloud, Google Cloud Vision (accuracy, confidence scores, per-call pricing)
  - Open-source: OpenALPR, Tesseract (offline fallback)
- **Research gap:** Live vendor docs (Plate Recognizer pricing/accuracy, OpenALPR maintenance status) could not be fetched this session. Re-verify before committing to a specific OCR provider in the STACK phase.

---
*Feature research for: barrier-gate video analytics / license plate recognition*
*Researched: 2026-09-09*
