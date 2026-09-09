# Smart Schlagbaum

## What This Is

Локальная система анализа видеопотока с IP-камер у шлагбаумов. Получает видеопоток с двух камер через веб-прокси `privratnik.net`, фиксирует факты открытия шлагбаума, распознаёт номера проезжающих автомобилей и сохраняет события (включая фото) в локальную базу данных для последующего аудита. Информирование (SMS/Telegram) отложено на будущее.

## Core Value

Надёжно фиксировать каждое открытие шлагбаума и распознавать номер проезжающего автомобиля, сохраняя событие с фото в локальную базу — без пропусков и без ручного вмешательства.

## Requirements

### Validated

(None yet — ship to validate)

### Active

- [ ] Получать видеопоток с двух камер через `privratnik.net` (авторизация + токен)
- [ ] Детектировать открытие/закрытие шлагбаума по кадрам
- [ ] Обнаруживать автомобиль в кадре
- [ ] Распознавать государственный номер автомобиля (внешний OCR-сервис)
- [ ] Сохранять событие (тип, время, камера, номер, фото) в локальную БД
- [ ] Хранить фото автомобиля рядом с записью события

### Out of Scope

- Информирование (SMS/Telegram/email) — отложено на будущее
- Управление шлагбаумом (открытие/закрытие по команде) — только наблюдение
- Мобильное приложение — только локальный сервис анализа

## Context

- Доступ к камерам: веб-сайт `https://privratnik.net/` (логин/пароль) → страница `video-control.php` с плеером двух камер.
- Мобильное приложение `ru.techmas.barrier` (Android) — вход по номеру телефона + SMS-код, те же две камеры.
- Видеопоток отдаётся через `https://privratnik.net/files/proxy.php?link=<cam_url>?token=<TOKEN>`.
- Токен в query-параметре обязателен; сессия поддерживается cookie `PHPSESSID`; запрос идёт с `Referer: https://privratnik.net/files/video-control.php` и `Range: bytes=0-`.
- Примеры ссылок на камеры (токены могут меняться):
  - `https://cam2.privratnik.net/80146f20_3105/preview.mp4`
  - `https://cam2.privratnik.net/f9456e90_3099/preview.mp4`
- Пользователь может сбросить лог вкладки Network из DevTools для анализа обмена токенами.
- Распознавание номеров — через внешний сервис (OpenALPR Cloud / Plate Recognizer / Google Vision OCR), локальный Tesseract как офлайн-фолбэк.

## Constraints

- **Tech stack**: Python (OpenCV, ffmpeg) для захвата и обработки кадров; SQLite для локальной БД; внешний OCR-API для распознавания номеров.
- **Timeline**: MVP — быстрый рабочий прототип, итеративная разработка.
- **Deployment**: локальный сервер/ПК пользователя (Windows), автоматический запуск через планировщик задач.
- **Compatibility**: поток отдаётся как MP4/превью через прокси; нужен ffmpeg для декодирования.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Локальная SQLite-БД вместо облачной | Простота, автономность, отсутствие внешних зависимостей для MVP | — Pending |
| Внешний OCR-сервис для номеров | Высокая точность без обучения собственной модели | — Pending |
| Захват кадров через ffmpeg | Универсальная поддержка форматов, низкая задержка | — Pending |
| Информирование отложено | Не требуется для MVP, добавят позже | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-09-09 after initialization*
