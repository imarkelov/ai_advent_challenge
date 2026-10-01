## Purpose

Capability «knowledge-base» — база знаний Студии (день 21): пайплайн
индексации документов (корпус 20–30 страниц: доки репозитория + код
бэкенда + загрузки пользователя; 2 стратегии chunking — fixed 1200/200
и structural по markdown-заголовкам; 2 эмбеддера — hash stdlib
детерминированный и api GPustack `qwen3-vl-embedding-8b` dim 4096;
локальный JSON-индекс `data/kb/index.json` с метаданными чанков и
встроенным сравнением стратегий по 8 gold-запросам; поиск косинусом
top-k), RAG-инъект в агентский чат (top-k выдержек ≤ 300 символов в
system-промпт на каждое сообщение, сбой не ломает чат), тумблеры
RAG/цикл-агента (`data/kb/settings.json`, чтение на каждый запрос),
вкладка «База знаний» в UI, e2e-гибрид (порт 8105). REST-роуты дней
11–20 не меняются.

## ADDED Requirements

### Requirement: Корпус документов

Система SHALL собирать корпус из трёх источников: `docs` (README.md,
RELEASE.md, `docs/superpowers/**/*.md`, `openspec/**/*.md`), `code`
(`studio/backend/*.py`) и `upload` (`data/kb/uploads/*` — только
whitelist `UPLOAD_EXTS`). Каждому документу присваивается
`source ∈ {docs, code, upload}`. Загруженные файлы принимаются только
с расширением из `UPLOAD_EXTS`, имя сохраняется только как basename
(traversal-safe). Корпус SHALL соответствовать заданию 20–30 страниц
(замер: 67 файлов, 139 261 слово).

#### Scenario: Сборка корпуса

- **WHEN** выполняется `KnowledgeBase.corpus_files()`
- **THEN** корпус содержит README.md/RELEASE.md/`docs/superpowers`/`openspec` как `docs`, `studio/backend/*.py` как `code` и файлы `data/kb/uploads` (только `UPLOAD_EXTS`) как `upload`

#### Scenario: Upload с чужим расширением отклонён

- **WHEN** `POST /api/kb/upload` с файлом, расширение которого не в `UPLOAD_EXTS`
- **THEN** 400 с RU-detail «Неподдерживаемый формат файла»; файл не сохраняется

#### Scenario: Имя upload — только basename

- **WHEN** `POST /api/kb/upload` с именем `../evil.md`
- **THEN** файл сохраняется в `data/kb/uploads/evil.md` (traversal-safe), ответ `{ok, file: "evil.md", size}`

### Requirement: Стратегии chunking

Система SHALL предоставлять две стратегии chunking:
**fixed** (`FixedChunker`: 1200 символов, overlap 200) и **structural**
(`StructuredChunker`: markdown — секции по заголовкам `#..######`,
текст до первого заголовка — «(без заголовка)», markdown без
заголовков — весь файл; секция длиннее 2400 символов — суб-чанки по
fixed; не-markdown — целый файл, длиннее 2400 — суб-чанки). У каждого
чанка SHALL быть метаданные `{chunk_id, source, file, section, chars,
text, vector}`, где `chunk_id = "{file_stem}-{strategy}-{NNNN}"`
(индекс в пределах файла).

#### Scenario: Фиксированный чанк

- **WHEN** текст длиннее 1200 символов обрабатывается `FixedChunker`
- **THEN** чанки по 1200 символов с перекрытием 200 (хвост предыдущего чанка = голова следующего)

#### Scenario: Markdown-секция

- **WHEN** markdown-файл с заголовками `##` обрабатывается `StructuredChunker`
- **THEN** чанки по секциям; метаданные `section` содержат текст заголовка секции

#### Scenario: Длинная секция / длинный код

- **WHEN** секция markdown или код-файл длиннее 2400 символов
- **THEN** он разбивается на суб-чанки по fixed-стратегии (1200/200), метаданные `section` сохраняются

### Requirement: Эмбеддинги

Система SHALL предоставлять два эмбеддера: **hash** — stdlib (char
3-граммы → md5 → 256 бакетов, L2-нормировка, детерминированный,
офлайн; только hashlib) и **api** — GPustack OpenAI-совместимый
`POST /embeddings` (модель `qwen3-vl-embedding-8b`, dim 4096, батчи
по 16, ключ — env `GPUSTACK_KEY_EMBED`). Сбой api-эмбеддера SHALL
приводить к `KBError`, а не к падению процесса.

#### Scenario: Детерминизм hash-эмбеддера

- **WHEN** один и тот же текст эмбеддится `HashEmbedder` дважды (в разных процессах)
- **THEN** векторы идентичны (dim 256, L2-норма ≈ 1)

#### Scenario: API-эмбеддер

- **WHEN** выполняется сборка индекса с `embedder: api`
- **THEN** запросы уходят батчами по 16 в `POST /embeddings` с моделью `qwen3-vl-embedding-8b`; векторы dim 4096; при недоступном API — 502 на `POST /api/kb/index` (`KBError`)

#### Scenario: Ключ не настроен

- **WHEN** `POST /api/kb/index {embedder: "api"}` без env `GPUSTACK_KEY_EMBED`
- **THEN** 400 с RU-detail «Ключ эмбеддингов не настроен (GPUSTACK_KEY_EMBED)»

### Requirement: Локальный JSON-индекс с метаданными

Индекс SHALL храниться в `data/kb/index.json` (в `.gitignore`),
запись — атомарная (tmp + `os.replace`). В индексе SHALL быть
метаданные (strategy, embedder, model, dim, built_at, stats) и чанки
**одной активной** стратегии, каждый — с метаданными из требования
«Стратегии chunking». В индексе SHALL быть встроенное **сравнение**
обеих стратегий: число чанков, среднее/максимальное число символов,
hit@3, precision@3, MRR по 8 встроенным gold-запросам. `data/kb/`
SHALL не попадать в git.

#### Scenario: Сборка индекса

- **WHEN** `POST /api/kb/index {strategy, embedder}`
- **THEN** создаётся `data/kb/index.json` (атомарно) с чанками активной стратегии и `comparison` обеих стратегий; ответ — `{stats, comparison, strategy}`

#### Scenario: Статистика без индекса

- **WHEN** `GET /api/kb/stats` при отсутствии `index.json`
- **THEN** 404 «Индекс не построен»

#### Scenario: Сравнение стратегий

- **WHEN** индекс построен
- **THEN** `comparison` содержит по каждой стратегии chunks, avg_chars, max_chars, hit_at_3, precision_at_3, mrr (по 8 gold-запросам); UI-таблица «Сравнение стратегий» подсвечивает активную

### Requirement: Поиск

Система SHALL поддерживать поиск top-k чанков по косинусной
близости (stdlib): `GET /api/kb/search?q=&k=5` →
`{results: [{chunk_id, source, file, section, score, text}]}` по
убыванию score. Эмбеддер запроса берётся из индекса (hash — офлайн,
api — из env). Индекс обязателен.

#### Scenario: Поиск по запросу

- **WHEN** `GET /api/kb/search?q=tool-loop&k=3` при построенном индексе
- **THEN** 200 с top-3 чанками по косинусу (score desc), у каждого — метаданные и текст

#### Scenario: Поиск без индекса / пустой запрос

- **WHEN** `GET /api/kb/search?q=…` без индекса / `q` пустой
- **THEN** 404 «Индекс не построен» / 400 RU-detail

### Requirement: RAG-инъект в чат

Агент (`agent.build_kb_block`) SHALL на каждое сообщение
пользователя при `rag=true` добавлять в system-промпт блок «База
знаний»: top-k (настройка `rag_top_k`, дефолт 3) выдержек поиска по
сообщению, каждая усечена до 300 символов, с указанием источника
`[file · section]`. Отсутствие индекса, сбой поиска или отсутствие
результатов SHALL приводить к пустому блоку; **чат MUST NOT
ломаться** (только лог `[KB]`).

#### Scenario: RAG включён

- **WHEN** `rag=true`, индекс построен, отправлено сообщение пользователя
- **THEN** тело LLM-запроса (в журнале) содержит блок «База знаний» с ≤ `rag_top_k` выдержками по ≤ 300 символов

#### Scenario: RAG выключен / индекс отсутствует

- **WHEN** `rag=false` / индекс не построен / поиск упал
- **THEN** блока «База знаний» в LLM-payload нет; чат отвечает как обычно (нет SSE error)

#### Scenario: Live RAG-ответ

- **WHEN** (Part B e2e) live-чат: «Какой лимит итераций tool-loop у агента (TOOL_LOOP_CAP)?»
- **THEN** модель отвечает «15» (значение из `studio/backend/agent.py`), KB-блок подтверждён в журнале LLM-запросов

### Requirement: Тумблеры RAG и цикла-агента

Настройки БЗ SHALL храниться в `data/kb/settings.json` (в
`.gitignore`), дефолты `{agent_loop: true, rag: true, rag_top_k: 3,
strategy: "structural", embedder: "hash"}`; изменение —
`POST /api/kb/settings` (частичное, валидация: `agent_loop`/`rag` —
bool, `rag_top_k` — int 1..10, `strategy: fixed|structural`,
`embedder: hash|api`, лишние ключи игнорируются; 400 — RU-detail).
Настройки SHALL читаться на каждый запрос. `agent_loop=false` → в
LLM-payload нет `tools`, tool-loop не крутится (один LLM-вызов), MCP-
каталог не инжектится; при `agent_loop=true` поведение дней 17–20 не
меняется.

#### Scenario: Тумблер цикла-агента

- **WHEN** `agent_loop=false` и подключён MCP-сервер, отправлено сообщение
- **THEN** в теле LLM-запроса нет поля `tools`; в `GET /api/kb/settings` — `agent_loop: false`

#### Scenario: Валидация settings

- **WHEN** `POST /api/kb/settings {"rag_top_k": 11}` (или не-bool тумблер, неизвестный strategy/embedder)
- **THEN** 400 с RU-detail («rag_top_k — целое число от 1 до 10» и т.п.); настройки не изменяются

#### Scenario: Чтение на каждый запрос

- **WHEN** между двумя сообщениями изменён `rag` (true → false)
- **THEN** в первом запросе KB-блок есть, во втором — нет (без рестарта сервера)

### Requirement: UI — вкладка «База знаний»

Фронтенд SHALL содержать вкладку «База знаний» в панели «Контекст»
(после «Инвариантов»): тумблеры «RAG в диалоге»/«цикл-агента» +
«Топ-K в диалоге», «Индексация» (select стратегии + select эмбеддера
+ «Индексировать»), «+ Добавить файл» (upload + подсказка
переиндексировать), «Статистика» (документы/файлы/чанки/символов/слов
в корпусе/dim/время сборки), «Сравнение стратегий» (таблица fixed vs
structural, активная подсвечена), «Поиск по базе» (top-5: чип score +
`file · section` + отрывок 300 символов).

#### Scenario: Индексация из UI

- **WHEN** пользователь выбирает стратегию/эмбеддер и нажимает «Индексировать»
- **THEN** `POST /api/kb/index` выполняется, вкладка показывает статистику и таблицу сравнения; активная стратегия подсвечена

#### Scenario: Поиск из UI

- **WHEN** введён поисковый запрос
- **THEN** показаны top-5 результатов: чип score + `file · section` + отрывок ≤ 300 символов

### Requirement: Проверка (тесты и e2e)

Репозиторий SHALL содержать офлайн-тесты (pytest, без сети):
`test_kb.py` (32: корпус, оба чанкера, эмбеддеры (MockTransport),
cosine, build/метрики/search/settings) и `test_kb_api.py` (19: 6
роутов TestClient, upload whitelist/basename-safe, `build_kb_block`
(формат, усечение 300, сбой → пусто), `agent_loop` с fake-MCP, RAG
on/off в LLM-payload, settings на каждый запрос); фронтенд —
`kb-tab.test.tsx` (6, Vitest). `scripts/e2e_day21.py` (stdlib, порт
8105) SHALL быть гибридом: **Part A** — офлайн-детерминированное ядро
(10 assert: корпус, оба чанкера, hash build/rebuild/search,
settings-валидация, `/api/kb/*` через TestClient, `agent_loop` с
fake-MCP, RAG on/off в payload) — MUST PASS; **Part B** — live
(uvicorn :8105, реальный LLM + API-эмбеддер), best-effort — PASS или
SKIP (поведение модели не FAIL). Итоги: бэкенд 447 PASS, фронтенд
238 PASS + `tsc -b` clean.

#### Scenario: E2E прогон

- **WHEN** запущен `python scripts/e2e_day21.py`
- **THEN** Part A — все assert PASS (офлайн, детерминированно); Part B — PASS или SKIP; exit 0

#### Scenario: Live-индекс на диске

- **WHEN** выполнен Part B с реальными эмбеддингами
- **THEN** на диске `data/kb/index.json`: structural, api, `qwen3-vl-embedding-8b`, dim 4096, 1791 чанк, 67 файлов (gitignored runtime-артефакт); сравнение (8 gold-запросов): fixed hit@3 = 0.5, structural hit@3 = 0.25
