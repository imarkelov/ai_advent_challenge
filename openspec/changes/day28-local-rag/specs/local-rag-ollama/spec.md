## Purpose

Capability «local-rag-ollama» — полностью локальный RAG-пайплайн
(день 28) поверх индекса Недели 6 (`kb.py`, `index.db` дней 21–25) и
локальной генерации дня 26 (Ollama): третий эмбеддер `ollama`
(`nomic-embed-text:v1.5`, 768 dim) считает векторы чанков и запроса
на `POST {OLLAMA_BASE_URL}/embeddings`, поэтому retrieval не выходит
в сеть и не требует ключей. Модель эмбеддера для поиска берётся
**из индекса** (`meta.model`): запрос считается той же моделью, что и
чанки. Смена эмбеддера — другой индекс (полная пересборка при
несовпадении; инкремент только при совпадении strategy+embedder).
Недоступная Ollama → `KBError` → 502 на сборке (без тихой подмены
эмбеддера). `hash`/`api`-эмбеддеры, логика `search`/`search_rag`,
реранкер и `min_score` дня 23, `CITE_RULE`/dont-know дня 24,
task-пайплайн и маршрутизация локальной генерации дня 26 — не
меняются. UI: выбор «Ollama nomic-embed-text:v1.5 (локально)» в
`KbTab` + фактическая модель в статистике. E2E —
`scripts/e2e_day28.py` (Part A MUST PASS офлайн; Part B live
best-effort, SKIP без Ollama).

## ADDED Requirements

### Requirement: Локальный эмбеддер `ollama`

Система SHALL предоставлять эмбеддер `ollama` с контрактом, идентичным
существующим (`name = "ollama"`, `model = "nomic-embed-text:v1.5"` по
умолчанию, `dim` узнаётся с первого ответа, `embed(texts,
on_progress=None) -> list[list[float]]`), считающий эмбеддинги через
OpenAI-совместимый `POST {OLLAMA_BASE_URL}/embeddings` батчами по 16.
Модель MUST быть переопределяемой (аргумент конструктора, затем
`OLLAMA_EMBED_MODEL`, затем дефолт). Ключ MUST NOT требоваться: уходит
заглушка `OLLAMA_API_KEY` (дефолт «ollama»), которую Ollama игнорирует.
Базовый URL — `OLLAMA_BASE_URL` (дефолт
`http://localhost:11434/v1`), завершающий `/` срезается.

#### Scenario: Дефолты и переопределение модели

- **WHEN** эмбеддер создаётся без аргументов при пустых переменных окружения
- **THEN** `name == "ollama"`, `model == "nomic-embed-text:v1.5"`, `api_key == "ollama"`, базовый URL `http://localhost:11434/v1`, `dim is None` (до первого ответа)

#### Scenario: Модель из аргумента приоритетнее env

- **WHEN** заданы `OLLAMA_EMBED_MODEL=custom:1` и явная модель `idx:2`
- **THEN** используется `idx:2`

#### Scenario: Батчи по 16 и прогресс

- **WHEN** `embed` вызывается на 35 текстах с `on_progress`
- **THEN** уходит 3 запроса (16/16/3), `dim` устанавливается с первого ответа, `on_progress` вызывается с `(16,35)`, `(32,35)`, `(35,35)`, длина результата 35

#### Scenario: Порядок векторов соответствует порядку текстов

- **WHEN** сервер вернул `data[]` не по порядку (перевёрнутый)
- **THEN** результат отсортирован по полю `index`, вектор i соответствует тексту i

#### Scenario: Сбой Ollama — KBError, не пустые векторы

- **WHEN** Ollama недоступна (соединение отклонено), отвечает HTTP-ошибкой или в ответе нет `data[]`/`embedding`
- **THEN** `embed` бросает `KBError` с упоминанием «Ollama»; пустой список векторов не возвращается

### Requirement: Retrieval использует эмбеддер из индекса

Система SHALL выбирать эмбеддер для поиска по полю `embedder` индекса,
а для `ollama` — строить его на модели из поля `model` этого же
индекса (`model` отсутствует/`null` → дефолт
`nomic-embed-text:v1.5`). Модель из окружения MUST NOT подменять модель
индекса: векторная нога обязана считать запрос той же моделью, что и
чанки.

#### Scenario: Модель для запроса берётся из индекса

- **WHEN** индекс имеет `embedder="ollama"`, `model="nomic-embed-text:v1.5"`, а в env задано `OLLAMA_EMBED_MODEL=env-model:1`
- **THEN** эмбеддер поиска имеет `model == "nomic-embed-text:v1.5"` (из индекса), а не `env-model:1`

#### Scenario: Индекс без записанной модели

- **WHEN** индекс имеет `embedder="ollama"` и `model: null`
- **THEN** используется дефолтная модель `nomic-embed-text:v1.5`

#### Scenario: Неизвестный эмбеддер индекса

- **WHEN** `embedder` индекса не входит в {`hash`, `api`, `ollama`}
- **THEN** `_embedder_from_index` бросает `KBError`

#### Scenario: `hash`-индекс продолжает работать

- **WHEN** индекс собран с `embedder="hash"`
- **THEN** поиск использует `HashEmbedder` и возвращает результаты как в днях 21–25 (регрессия не сломана)

### Requirement: Сборка индекса локальным эмбеддером

Система SHALL принимать `embedder: "ollama"` в `POST /api/kb/index`
(наряду с `hash` и `api`), собирать индекс без обращения к GPustack и
без ключей, записывая в meta `embedder="ollama"`,
`model="nomic-embed-text:v1.5"` и фактический `dim`. Значение
`embedder`, не входящее в {`hash`, `api`, `ollama`}, MUST отклоняться
с 400. Недоступность Ollama при сборке MUST возвращать 502 (ошибка
эмбеддера не глотается и не подменяется другим эмбеддером).

#### Scenario: Сборка локальным эмбеддером без ключей

- **WHEN** `POST /api/kb/index {"strategy": "structural", "embedder": "ollama"}` при пустых `GPUSTACK_*`
- **THEN** ответ 200, индекс содержит `embedder="ollama"`, `model="nomic-embed-text:v1.5"`, `dim=768`, `chunks` непусто

#### Scenario: Недоступная Ollama — 502

- **WHEN** `OLLAMA_BASE_URL` указывает на мёртвый порт и запрошен `embedder="ollama"`
- **THEN** ответ 502, индекс не создаётся

#### Scenario: Некорректный эмбеддер — 400

- **WHEN** `POST /api/kb/index {"strategy": "fixed", "embedder": "nope"}`
- **THEN** ответ 400

#### Scenario: `ollama` в настройках БЗ

- **WHEN** `POST /api/kb/settings {"embedder": "ollama"}`
- **THEN** ответ 200 и `embedder == "ollama"`; значение вне {`hash`, `api`, `ollama`} → 400

### Requirement: Статистика индекса показывает модель эмбеддера

`GET /api/kb/stats` SHALL аддитивно отдавать поле `model` — фактическую
модель эмбеддера, которым построен индекс (`null` у `hash`). Существующие
поля ответа MUST NOT меняться.

#### Scenario: Модель видна для локального индекса

- **WHEN** индекс собран с `embedder="ollama"`
- **THEN** `GET /api/kb/stats` отдаёт `embedder="ollama"`, `model="nomic-embed-text:v1.5"`, `dim=768` и прежние поля (`strategy`, `built_at`, `stats`, `comparison`, `files`)

### Requirement: Выбор локального эмбеддера в UI

Фронтенд SHALL предлагать `ollama` как третий вариант эмбеддера
(`KbEmbedder = 'hash' | 'api' | 'ollama'`) с подписью о локальности и
показывать фактическую модель в статистике индекса. Тип `KbStats`
SHALL получать опциональное поле `model`, чтобы старый бэкенд
(без поля) не ломал рендер.

#### Scenario: Селектор и статистика

- **WHEN** пользователь открывает вкладку «База знаний»
- **THEN** в селекторе эмбеддера доступны три варианта, включая «Ollama nomic-embed-text:v1.5 (локально)»; при собранном `ollama`-индексе в статистике показана строка «Модель» со значением `nomic-embed-text:v1.5`

#### Scenario: Старый бэкенд без поля model

- **WHEN** ответ `/api/kb/stats` не содержит `model`
- **THEN** статистика рендерится без строки «Модель» и без краха

### Requirement: E2E локального RAG

Система SHALL поставлять `scripts/e2e_day28.py`, доказывающий
локальность пайплайна: Part A (офлайн, MUST PASS) — сборка
`ollama`-индекса, retrieval эмбеддером из индекса, регрессия
`hash`-индекса, RAG-блок в system-промпте, валидация и 502 при
недоступной Ollama; Part B (live, best-effort) — зонд Ollama,
локальная сборка, поиск по контрольным вопросам, генерация локальной
LLM с непустым `rag_context`, метка `local` в `/api/models`. Part B
без Ollama MUST завершаться SKIP, а не FAIL.

#### Scenario: Part A проходит без сети

- **WHEN** `python scripts/e2e_day28.py --part-a-only` запускается при вырезанной сети
- **THEN** все проверки A1–A6 в статусе PASS, код возврата 0

#### Scenario: Part B без Ollama — SKIP

- **WHEN** Ollama недоступна или в ней нет `nomic-embed-text:v1.5`
- **THEN** Part A проходит, Part B помечается SKIP, код возврата 0 (не FAIL)

#### Scenario: Полный локальный прогон

- **WHEN** Ollama запущена и содержит `nomic-embed-text:v1.5` и completion-модель
- **THEN** индекс собирается локально, контрольные вопросы находят иголки, `/api/chat` с RAG отдаёт непустой ответ локальной LLM с непустым `rag_context.chunks`

## MODIFIED Requirements

Нет. Изменения аддитивны: `hash`/`api`-эмбеддеры, `search`/`search_rag`,
реранкер и `min_score` дня 23, `CITE_RULE`/dont-know дня 24,
task-пайплайн и генерация дня 26 сохраняют текущее поведение.
