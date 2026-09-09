# Phase 1: Stream Client + Frame Buffer - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-09-09
**Phase:** 1-Stream Client + Frame Buffer
**Areas discussed:** Auth/session handling, Frame capture tech, Config/secrets structure, Queue & frame rate

---

## Auth/session handling

| Option | Description | Selected |
|--------|-------------|----------|
| Auto-login | Система сама логинится на privratnik.net по логину/паролю, получает PHPSESSID + token, авто-перелогин при истечении | ✓ |
| Manual token paste | Вручную вставлять свежий token/PHPSESSID при истечении | |
| Hybrid | Авто-логин + ручное переопределение token/куки | |

**User's choice:** Auto-login
**Notes:** Полностью автономно, без ручного вмешательства.

| Option | Description | Selected |
|--------|-------------|----------|
| .env file | Логин/пароль в .env (gitignored), os.getenv() | ✓ |
| config.json | Секреты в обычном конфиге | |
| Windows Credential Manager | Системное хранилище | |

**User's choice:** .env file

| Option | Description | Selected |
|--------|-------------|----------|
| Exponential backoff | 1с → 2с → 4с → … до максимума | ✓ |
| Fixed interval | Фиксированный интервал (5с) | |
| Immediate retry | Без задержки | |

**User's choice:** Exponential backoff

---

## Frame capture tech

| Option | Description | Selected |
|--------|-------------|----------|
| OpenCV VideoCapture | Напрямую на proxy URL | |
| ffmpeg subprocess | Декодирует поток, отдаёт кадры через pipe | ✓ |
| HTTP stream + ffmpeg | requests стримит байты, ffmpeg декодирует | |

**User's choice:** ffmpeg subprocess
**Notes:** Гибкость с заголовками (Referer/Range/cookie), надёжный reconnect.

| Option | Description | Selected |
|--------|-------------|----------|
| JPEG frames via pipe | image2pipe -vcodec mjpeg, Python декодирует OpenCV | ✓ |
| Raw frames via pipe | rawvideo, numpy напрямую | |
| Frames to disk | ffmpeg пишет файлы, Python читает | |

**User's choice:** JPEG frames via pipe

---

## Config/secrets structure

| Option | Description | Selected |
|--------|-------------|----------|
| Split config + .env | Не-секреты в config.json, секреты в .env | ✓ |
| Single .env | Всё в одном .env | |
| Single config.json | Всё в config.json, включая секреты | |

**User's choice:** Split config + .env

| Option | Description | Selected |
|--------|-------------|----------|
| URLs in config, token dynamic | URL в config.json, токен добавляется динамически | ✓ |
| Full URLs in .env | Полные URL с токеном в .env | |
| Hardcoded | URL в коде | |

**User's choice:** URLs in config, token dynamic

---

## Queue & frame rate

| Option | Description | Selected |
|--------|-------------|----------|
| 1-2 fps | Достаточно для медленных событий, экономит CPU | ✓ |
| 5-10 fps | Плавнее, избыточно | |
| Full stream rate | 25-30 fps, высокая нагрузка | |

**User's choice:** 1-2 fps

| Option | Description | Selected |
|--------|-------------|----------|
| Bounded drop-oldest | 10-20 кадров, старые отбрасываются | ✓ |
| Unbounded queue | Без ограничения | |
| Single-slot | Только последний кадр | |

**User's choice:** Bounded drop-oldest

| Option | Description | Selected |
|--------|-------------|----------|
| Per-camera queue | Каждый канал независим, тег camera_id | ✓ |
| Single shared queue | Одна общая очередь | |

**User's choice:** Per-camera queue

---

## Claude's Discretion

- Точный размер очереди (10 vs 20) и максимум backoff-интервала — на усмотрение Claude при планировании.

## Deferred Ideas

None — discussion stayed within phase scope
