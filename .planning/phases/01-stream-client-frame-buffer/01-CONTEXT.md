# Phase 1: Stream Client + Frame Buffer - Context

**Gathered:** 2026-09-09
**Status:** Ready for planning

## Phase Boundary

Захват кадров с двух камер через прокси `privratnik.net` с работающей авторизацией, ограниченной очередью (drop-oldest), циклом переподключения и структурой конфига/секретов. Эта фаза НЕ включает детекцию шлагбаума, детекцию авто или распознавание номеров — только надёжный, непрерывный, тегированный по `camera_id` поток кадров, готовый для следующих фаз.

## Implementation Decisions

### Auth / Session Handling
- **D-01:** Авто-логин — система сама логинится на `privratnik.net` по логину/паролю, получает `PHPSESSID` + `token`, и при истечении сессии автоматически перелогинивается. Полностью автономно, без ручного вмешательства.
- **D-02:** Логин/пароль `privratnik.net` хранятся в `.env` файле (в `.gitignore`), читаются через `os.getenv()`.
- **D-03:** Переподключение при обрыве потока/истечении сессии — экспоненциальный backoff (1с → 2с → 4с → … до максимума).

### Frame Capture Tech
- **D-04:** Захват кадров — ffmpeg-подпроцесс (декодирует поток, отдаёт кадры через pipe). Выбран за гибкость с HTTP-заголовками (`Referer`, `Range`, cookie) и надёжный reconnect.
- **D-05:** Формат кадров — JPEG через pipe (`image2pipe -vcodec mjpeg`), Python декодирует через OpenCV.

### Config / Secrets Structure
- **D-06:** Разделение конфига и секретов: не-секретные настройки (URL камер, параметры очереди, таймауты) — в `config.json`; секреты (логин/пароль/token) — в `.env`.
- **D-07:** URL камер — в `config.json` как список (`camera_id` → URL). Токен добавляется динамически при запросе, не хранится вшитым в URL.

### Queue & Frame Rate
- **D-08:** Частота захвата — 1-2 кадра/сек на камеру (достаточно для медленных событий шлагбаума, экономит CPU/память).
- **D-09:** Очередь — ограниченная (10-20 кадров) с политикой drop-oldest: медленный потребитель не блокирует захват.
- **D-10:** Одна очередь на камеру (каждый канал независим), кадры тегируются `camera_id` при помещении в очередь.

### Claude's Discretion
- Точный размер очереди (10 vs 20) и максимум backoff-интервала — на усмотрение Claude при планировании, в рамках выбранных политик.

## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project & Requirements
- `.planning/PROJECT.md` — контекст проекта, детали авторизации `privratnik.net` (PHPSESSID + token + Referer + Range), URL камер.
- `.planning/REQUIREMENTS.md` — требования STREAM-01..05 (захват потока, авторизация, reconnect, независимые каналы, извлечение кадров).
- `.planning/ROADMAP.md` — Phase 1 goal и success criteria.

### Research
- `.planning/research/STACK.md` — рекомендованный стек (OpenCV headless, ffmpeg, requests).
- `.planning/research/PITFALLS.md` — pitfall #1: token/session expiry (silent killer), блокировка захвата на OCR.
- `.planning/research/ARCHITECTURE.md` — линейный пайплайн, ограниченная очередь.

## Existing Code Insights

### Reusable Assets
- Нет существующего кода — зелёное поле (первая фаза).

### Established Patterns
- Нет устоявшихся паттернов — конвенции появятся в процессе разработки.

### Integration Points
- Выход фазы: очередь кадров с тегом `camera_id` — точка интеграции для Phase 2 (Barrier State Detector).

## Specific Ideas

- Поток отдаётся как MP4/превью через прокси (не постоянный RTSP) — нужно периодически переоткрывать поток, а не считать его непрерывным.
- Запрос идёт с `Referer: https://privratnik.net/files/video-control.php` и `Range: bytes=0-`.

## Deferred Ideas

None — discussion stayed within phase scope

---

*Phase: 1-Stream Client + Frame Buffer*
*Context gathered: 2026-09-09*
