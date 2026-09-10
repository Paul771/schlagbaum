# Requirements: Smart Schlagbaum

**Defined:** 2026-09-09
**Core Value:** Надёжно фиксировать каждое открытие шлагбаума и распознавать номер проезжающего автомобиля, сохраняя событие с фото в локальную базу — без пропусков и без ручного вмешательства.

## v1 Requirements

Requirements for initial release. Each maps to roadmap phases.

### Захват потока (Stream Client)

- [x] **STREAM-01**: Система получает видеопоток с двух камер через `privratnik.net` proxy URL
- [x] **STREAM-02**: Система поддерживает авторизацию (PHPSESSID cookie + token + Referer) для доступа к потоку
- [x] **STREAM-03**: Система автоматически переподключается и обновляет токен при истечении сессии
- [x] **STREAM-04**: Каждая камера работает как независимый канал захвата, события тегируются camera_id
- [x] **STREAM-05**: Система извлекает кадры из потока для последующего анализа

### Детекция шлагбаума (Barrier State Detector)

- [ ] **BARRIER-01**: Система определяет состояние шлагбаума: открыт / закрыт / частично открыт
- [ ] **BARRIER-02**: Система отслеживает переходы состояния (CLOSED→OPENING→OPEN→CLOSING) через FSM
- [ ] **BARRIER-03**: Система генерирует одно событие на каждое открытие (без дубликатов)
- [ ] **BARRIER-04**: Система устойчива к false positives (тени, проезд авто, изменение освещения)

### Детекция авто (Vehicle Detector)

- [ ] **VEHICLE-01**: Система обнаруживает автомобиль в кадре (YOLO11n: car/truck/bus)
- [ ] **VEHICLE-02**: Детекция авто гейтирует вызов OCR (не на каждом кадре, экономия API-бюджета)
- [ ] **VEHICLE-03**: Система выбирает лучший кадр (best-frame) для распознавания номера

### Распознавание номеров (Plate Recognizer)

- [ ] **PLATE-01**: Система распознаёт госномер через внешний ALPR API (Plate Recognizer / OpenALPR Cloud)
- [ ] **PLATE-02**: Система имеет офлайн-fallback через Tesseract при недоступности внешнего API
- [ ] **PLATE-03**: Система фиксирует confidence-оценку распознавания и флагует низкую уверенность
- [ ] **PLATE-04**: OCR-провайдер абстрагирован за интерфейсом (сменяемый)

### Хранилище событий (Event Store + Photo Store)

- [ ] **STORE-01**: Система сохраняет событие в SQLite: тип, время, camera_id, номер, путь к фото
- [ ] **STORE-02**: Система сохраняет фото автомобиля/номера файлом на диске, в БД — путь
- [ ] **STORE-03**: Система дедуплицирует события (одно открытие = одна запись)
- [ ] **STORE-04**: Схема БД не зависит от конкретного OCR-провайдера

### Координатор/FSM (Event Coordinator)

- [ ] **COORD-01**: Очередь + отдельный поток для OCR, чтобы медленный OCR не блокировал захват кадров
- [ ] **COORD-02**: FSM генерирует событие только на реальном переходе состояния шлагбаума
- [ ] **COORD-03**: Система запускается автоматически (Windows Task Scheduler / сервис) и переживает перезагрузку

## v2 Requirements

Deferred to future release. Tracked but not in current roadmap.

### Trust & Maintenance

- **TRUST-01**: Ручная коррекция распознанных номеров
- **TRUST-02**: Retention policy (автоочистка старых фото/событий)
- **TRUST-03**: Экспорт событий в CSV/JSON

### Информирование

- **NOTIF-01**: Уведомления (SMS/Telegram/email) при проезде
- **NOTIF-02**: Уведомления при недоступности камеры

### Web UI

- **WEB-01**: Веб-интерфейс для просмотра событий и фото

## Out of Scope

| Feature | Reason |
|---------|--------|
| Управление шлагбаумом (открытие/закрытие по команде) | Только наблюдение; безопасность/ответственность |
| Мобильное приложение | Только локальный сервис анализа |
| Непрерывная запись видео | Только событийные кадры/фото |
| Обучение собственной модели | Внешний OCR достаточно точен для MVP |
| Мульти-сайт синхронизация | Один локальный узел |
| Real-time alerting | Отложено на v2 (информирование) |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| STREAM-01 | Phase 1 | Complete |
| STREAM-02 | Phase 1 | Complete |
| STREAM-03 | Phase 1 | Complete |
| STREAM-04 | Phase 1 | Complete |
| STREAM-05 | Phase 1 | Complete |
| BARRIER-01 | Phase 2 | Pending |
| BARRIER-02 | Phase 2 | Pending |
| BARRIER-03 | Phase 2 | Pending |
| BARRIER-04 | Phase 2 | Pending |
| VEHICLE-01 | Phase 4 | Pending |
| VEHICLE-02 | Phase 4 | Pending |
| VEHICLE-03 | Phase 4 | Pending |
| PLATE-01 | Phase 5 | Pending |
| PLATE-02 | Phase 5 | Pending |
| PLATE-03 | Phase 5 | Pending |
| PLATE-04 | Phase 5 | Pending |
| STORE-01 | Phase 3 | Pending |
| STORE-02 | Phase 3 | Pending |
| STORE-03 | Phase 3 | Pending |
| STORE-04 | Phase 3 | Pending |
| COORD-01 | Phase 3 | Pending |
| COORD-02 | Phase 3 | Pending |
| COORD-03 | Phase 6 | Pending |

**Coverage:**

- v1 requirements: 23 total
- Mapped to phases: 23
- Unmapped: 0 ✓

---
*Requirements defined: 2026-09-09*
*Last updated: 2026-09-09 after roadmap creation*
