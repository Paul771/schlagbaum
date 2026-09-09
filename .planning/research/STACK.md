# Stack Research

**Domain:** Local video analytics / license plate recognition from IP camera streams at a barrier gate
**Researched:** 2026-09-09
**Confidence:** HIGH

## Recommended Stack

### Core Technologies

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| Python | 3.12+ (3.13 supported) | Application language | The de-facto standard for CV/ML pipelines. OpenCV, Ultralytics, pytesseract all have first-class Python bindings. Single language for capture, detection, OCR, and storage keeps the MVP simple. |
| OpenCV (`opencv-python-headless`) | 4.14.0.94 (pin; 5.0.0.93 is latest) | Frame capture, decoding, image preprocessing | The standard CV library. `VideoCapture` reads HTTP/MP4 streams directly. **Use `-headless` variant** — no GUI deps, smaller install, avoids Qt/GUI DLL conflicts on Windows. Pin to 4.14.x for MVP; OpenCV 5.0 is a major release with breaking API changes — do not adopt mid-project. |
| Ultralytics YOLO | 8.4.144 | Vehicle detection (car/truck/bus) | The current standard for real-time object detection. YOLO11n runs on CPU at usable speed, COCO-pretrained with `car`, `truck`, `bus` classes out of the box. One-line inference API. |
| SQLite (stdlib `sqlite3`) | 3.x (bundled with Python) | Local event + photo metadata storage | Zero-install, single-file, ACID, perfect for a single-user local audit DB. No server process. Photos stored as files on disk with paths in DB (not BLOBs). |
| `requests` | 2.34.2 | HTTP proxy auth + token handling, external OCR API calls | The standard HTTP client. Handles cookies (`PHPSESSID`), headers (`Referer`, `Range`), query params (`token`). |
| `pytesseract` | 0.3.13 | Offline license-plate OCR fallback | Thin wrapper over Tesseract-OCR. Works fully offline. Requires the Tesseract binary installed separately (Windows installer). |

### Supporting Libraries

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `numpy` | 2.5.3 | Array ops for frame processing | Required by OpenCV and Ultralytics; comes transitively but pin explicitly. |
| `Pillow` | 12.3.0 | Image encode/decode for saving photos, feeding OCR | Needed to save JPEG photos and to pass PIL images to pytesseract. |
| `opencv-python` (non-headless) | 4.14.0.94 | GUI preview windows | Only if you want a live `cv2.imshow` debug window. Prefer headless for the service; add non-headless only in a dev venv. |
| `schedule` or Windows Task Scheduler | — | Periodic/startup scheduling | Prefer **Windows Task Scheduler** (native, no dependency) over a Python scheduler for "run at boot / run continuously". |
| `python-dotenv` | 1.x | Load tokens/credentials from `.env` | Keep tokens, PHPSESSID, OCR API keys out of source. |
| `loguru` | 0.7.x | Structured logging | Optional; stdlib `logging` is fine for MVP. Use loguru if you want rotation + colored output cheaply. |

### Development Tools

| Tool | Purpose | Notes |
|------|---------|-------|
| `venv` + `pip` | Isolated env, dependency pinning | Use `requirements.txt` with exact pins (`==`), not ranges, for reproducibility on the user's Windows PC. |
| `uv` (optional) | Fast Python env/package manager | Faster than pip; good if you iterate a lot. Not required. |
| ffmpeg (system binary) | Fallback stream decode / re-encode | OpenCV's bundled FFmpeg usually handles the MP4 preview stream. Install standalone ffmpeg only if OpenCV fails to decode a specific stream. |
| Tesseract-OCR (system binary) | Offline OCR engine | Windows installer from UB-Mannheim. Must be on PATH or set `pytesseract.pytesseract.tesseract_cmd`. |

## Installation

```bash
# Core (Windows, in a venv)
python -m venv .venv
.venv\Scripts\activate
pip install opencv-python-headless==4.14.0.94 \
            ultralytics==8.4.144 \
            pytesseract==0.3.13 \
            requests==2.34.2 \
            numpy==2.5.3 \
            Pillow==12.3.0 \
            python-dotenv

# Dev / debug only (separate venv, not the service)
pip install opencv-python==4.14.0.94
```

**System installs (not pip):**
- Tesseract-OCR: https://github.com/UB-Mannheim/tesseract/wiki (Windows installer)
- ffmpeg (only if OpenCV can't decode a stream): https://www.gyan.dev/ffmpeg/builds/

## Alternatives Considered

| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|-------------------------|
| OpenCV `VideoCapture` | Standalone ffmpeg subprocess | If OpenCV fails to decode the specific MP4/proxy stream. ffmpeg is more robust to exotic codecs but adds process management. |
| Ultralytics YOLO11n | YOLOv8 / YOLOv5 | YOLOv5 is legacy; YOLOv8 is superseded by YOLO11. Use YOLO11n (nano) for CPU speed; YOLO11s/m if accuracy matters more than speed. |
| External OCR API (primary) | Local Tesseract only | Tesseract alone gives poor accuracy on license plates (no plate-specific model). Use external API for accuracy, Tesseract as offline fallback. |
| SQLite | PostgreSQL / MySQL | Overkill for a single local user. SQLite is the right call for MVP; migrate only if multi-user/remote access becomes a requirement. |
| Photos as files + paths in DB | Photos as BLOBs in SQLite | Files are simpler to back up, view, and serve; BLOBs bloat the DB and complicate queries. |
| Windows Task Scheduler | `schedule` / `APScheduler` / systemd | Native Windows scheduling for "start at boot, run continuously" is most reliable and survives reboots without a Python daemon. |

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| OpenCV 5.0.x (latest) | Major version with breaking API changes; ecosystem (Ultralytics, tutorials) still largely 4.x-oriented. Adopting mid-MVP risks churn. | Pin `opencv-python-headless==4.14.0.94` |
| `opencv-python` (non-headless) in the service | Pulls in GUI/Qt dependencies that can conflict on Windows and bloat the install. | `opencv-python-headless` |
| YOLOv5 / YOLOv8 | Legacy/superseded; YOLO11 is current with better CPU speed and accuracy. | Ultralytics YOLO11n |
| Google Vision OCR for plates | Generic OCR, not plate-specialized; poor on plates vs dedicated ALPR services. Fine as a generic fallback but not the primary. | OpenALPR Cloud / Plate Recognizer (plate-specialized) |
| Storing photos as BLOBs | DB bloat, hard to back up/view, slow queries. | Store photo files on disk, save paths in SQLite |
| `APScheduler`/`schedule` for boot-start | Adds a dependency and a running process; Windows Task Scheduler is native and reliable. | Windows Task Scheduler |
| Building your own plate-detection model | Huge effort, needs labeled data, GPU training. Not needed for MVP. | Pretrained YOLO11 + external ALPR API |

## Stack Patterns by Variant

**If the proxy stream is a short MP4 preview (not a live RTSP):**
- Use OpenCV `VideoCapture` on the `proxy.php?link=...&token=...` URL directly.
- Poll/re-open the stream periodically (preview MP4s may be finite clips) rather than assuming a continuous live feed.
- Because: the `privratnik.net` proxy returns MP4 previews, not a persistent RTSP session.

**If OpenCV cannot decode the stream:**
- Fall back to spawning `ffmpeg -i <url> -f image2pipe -vcodec mjpeg -` and reading JPEG frames from stdout.
- Because: ffmpeg has broader codec support than OpenCV's bundled build.

**If the user's PC has no GPU:**
- Use YOLO11n (nano) with `device="cpu"` and `imgsz=640`.
- Because: nano is the only YOLO11 size that runs comfortably on CPU at a usable frame rate for a gate (low event frequency).

**If OCR accuracy on Russian plates is critical:**
- Prefer Plate Recognizer (supports RU plates well) or OpenALPR Cloud with RU region config.
- Because: dedicated ALPR services handle Cyrillic plate formats far better than generic OCR.

## Version Compatibility

| Package A | Compatible With | Notes |
|-----------|-----------------|-------|
| `opencv-python-headless==4.14.0.94` | `numpy>=1.26,<3` | OpenCV 4.14 works with numpy 2.x. Do not mix with OpenCV 5.0. |
| `ultralytics==8.4.144` | `torch` (auto-installed), `numpy`, `opencv-python` | Ultralytics pulls its own torch; on CPU-only Windows this is fine. It depends on opencv — install headless first to avoid GUI conflict. |
| `pytesseract==0.3.13` | Tesseract-OCR binary 4.x/5.x | Wrapper is version-agnostic; needs `tesseract_cmd` set on Windows. |
| `Pillow==12.3.0` | `numpy` | Pillow 12 works with numpy 2.x. |
| Python 3.12/3.13 | All above | All packages publish wheels for 3.12/3.13 on Windows. |

**Known compatibility gotcha:** Ultralytics depends on `opencv-python` (non-headless) by default. If you install `opencv-python-headless` first, Ultralytics will detect it and not pull the GUI variant. Install order matters — install headless before ultralytics.

## Sources

- Context7 `/opencv/opencv-python` — VideoCapture API, version access, resource release
- Context7 `/websites/ultralytics` — YOLO11 model sizes, CPU inference, COCO classes
- Context7 `/madmaze/pytesseract` — image_to_string, PSM/OEM config, Windows tesseract_cmd
- PyPI (via `pip index versions`) — verified current versions: opencv-python 5.0.0.93 / 4.14.0.94, ultralytics 8.4.144, pytesseract 0.3.13, pillow 12.3.0, requests 2.34.2, numpy 2.5.3
- Project context `C:/dev/schlagbaum/.planning/PROJECT.md` — privratnik.net proxy, token auth, MP4 preview streams, SQLite constraint

---
*Stack research for: video analytics / license plate recognition at a barrier gate*
*Researched: 2026-09-09*
