# День 21 — База знаний: индексация документов и RAG

Дата: 2026-09-29. Ветка: `day21-doc-indexing` (от `day20-mcp-orchestration`).
Статус: утверждён пользователем (итеративный brainstorming, решения Q1–Q6).

## 1. Задание дня

База знаний агента-чата Студии:

- корпус документов **20–30 страниц**;
- пайплайн: **chunking (2 стратегии)**, **эмбеддинги**, **локальный индекс**;
- **метаданные** у каждого чанка;
- **сравнение двух стратегий** chunking;
- **RAG-инъект** в агентский чат;
- **тумблеры**: RAG вкл/выкл, цикл-агента (MCP-тулы) вкл/выкл;
- результат — **локальный индекс** + работающий RAG в чате.

## 2. Утверждённые решения

| # | Решение |
|---|---|
| Q1 | Корпус = доки репозитория (`README.md`, `RELEASE.md`, `docs/superpowers/**/*.md`, `openspec/**/*.md`, `studio/backend/*.py`) + загрузки пользователя `data/kb/uploads/` (whitelist `UPLOAD_EXTS`) |
| Q2 | 2 стратегии chunking: **fixed** (1200 символов, overlap 200) и **structural** (markdown-заголовки `#..######`; секция > 2400 символов → суб-чанки по fixed; код — целый файл, > 2400 → суб-чанки) |
| Q3 | 2 эмбеддера: **hash** (stdlib: char 3-граммы → md5 → 256 бакетов, L2; детерминированный — офлайн/CI; дефолт) и **api** (GPustack OpenAI-совместимый `/embeddings`, `qwen3-vl-embedding-8b`, dim 4096, батчи 16, ключ `GPUSTACK_KEY_EMBED`) |
| Q4 | Индекс — **один локальный JSON** `data/kb/index.json` (атомарная запись, в `.gitignore`); активная стратегия — одна; сравнение обеих стратегий (чанки, avg/max символов, hit@3, precision@3, MRR по 8 gold-запросам) **встроено в индекс** |
| Q5 | RAG = top-k (настройка `rag_top_k`, дефолт 3) выдержек по **каждому** сообщению пользователя в system-промпт, выдержка ≤ **300** символов; сбой поиска **не ломает чат** (пустой блок + лог) |
| Q6 | Тумблеры — `data/kb/settings.json` (`agent_loop`, `rag`, `rag_top_k`, `strategy`, `embedder`), читаются **на каждый запрос**; `agent_loop=false` → без `tools` и без tool-loop (один LLM-вызов, паттерн дня 16 «без MCP-серверов») |

## 3. Текущее состояние (день 20) — якоря

- `studio/backend/agent.py` — `StudioAgent`: `build_payload` (базовый
  промпт → профиль → инварианты → блоки памяти → правила), tool-loop
  дня 17 (кап 15, env `TOOL_LOOP_CAP`), `_llm_tools` + MCP-каталог
  дня 20, журнал LLM-запросов (тело запроса — в `requests.json`).
- `studio/backend/main.py` — FastAPI-приложение, роуты с 400/404
  RU-detail (паттерн дня 11); приложение строится на уровне модуля →
  выносится `create_app(agent, kb)` (DI для тестов).
- `studio/backend/memory.py` — JSON-хранилища с атомарной записью
  (tmp + `os.replace`) — переиспользуемый паттерн.
- Фронтенд — `ContextPanel` (вкладки Память/Профили/Инварианты),
  `api.ts`-хелперы, Vitest + Testing Library.
- E2E-конвенция: Part A — офлайн-детерминированное ядро, MUST PASS;
  Part B — live (uvicorn + реальный GPustack LLM), best-effort
  (PASS/SKIP, не FAIL). Порты: 8100 — `e2e_studio.py`, 17→8101,
  18→8102, 19→8103, 20→8104, **21→8105**; port-busy — ожидание до 10
  мин, чужой сервер не убивается (паттерн дня 20).
- `.env` (корень репозитория) — `GPUSTACK_BASE_URL`, per-model ключи
  (`MODEL_KEY_ENV`); новая переменная **`GPUSTACK_KEY_EMBED`** — ключ
  эмбеддинг-модели.

## 4. Целевая архитектура

### 4.1 `studio/backend/kb.py` — RAG-ядро (новый файл)

stdlib + httpx (локальный импорт — модуль грузится и без httpx):
`KBError`, `CorpusDoc`, `Chunk`, `FixedChunker`, `StructuredChunker`,
`HashEmbedder`, `APIEmbedder`, `cosine`, `GOLD_QUERIES`,
`KnowledgeBase`, `atomic_write_json`/`read_json`.

- `CorpusDoc(path, text, source)` — `source ∈ {docs, code, upload}`.
- `Chunk(text, source, file, section, chunk_index)` — `section`: текст
  заголовка / `whole file` / `(без заголовка)`.
- `KnowledgeBase(kb_dir, repo_root=None)` — `kb_dir = <repo>/data/kb`
  (создаётся с `uploads/`), пути `index.json` / `settings.json`.

### 4.2 Сборка корпуса (`corpus_files`)

| Источник | Файлы | source |
|---|---|---|
| доки | `README.md`, `RELEASE.md`, `docs/superpowers/**/*.md`, `openspec/**/*.md` | `docs` |
| код | `studio/backend/*.py` (только верхний уровень) | `code` |
| загрузки | `data/kb/uploads/*` (только `UPLOAD_EXTS`) | `upload` |

`UPLOAD_EXTS = {.txt, .md, .py, .js, .ts, .tsx, .jsx, .json, .csv,
.html, .css, .yaml, .yml}`. Недоступный файл — пропускается, сборку не
роняет. Замеренный корпус: **67 файлов, 139 261 слово**.

### 4.3 Стратегии chunking

- **`FixedChunker(chunk_chars=1200, overlap=200)`** — жёсткое разбиение
  по символам; шаг `chunk_chars - overlap`; валидация `ValueError`
  (`chunk_chars > 0`, `0 ≤ overlap < chunk_chars`); пустой текст → `[]`.
- **`StructuredChunker(max_section=2400)`** —
  - markdown (`.md`): секции по заголовкам `^(#{1,6})\s+…$`; секция
    включает строку заголовка; текст до первого заголовка → секция
    «(без заголовка)»; markdown без единого заголовка → весь файл одним
    чанком (`whole file`); секция > `max_section` → суб-чанки по
    `FixedChunker` с сохранением секции;
  - не-md (код): весь файл = 1 чанк (`whole file`); > `max_section` →
    суб-чанки по `FixedChunker`.

**Метаданные чанка** в индексе:
`{chunk_id, source, file, section, chars, text, vector}`;
`chunk_id = "{file_stem}-{strategy}-{i:04d}"` (`i` — индекс в пределах
файла), `vector` — округлён до 6 знаков.

### 4.4 Эмбеддеры

- **`HashEmbedder`** (name `hash`, dim **256**) — char 3-граммы →
  `md5(gram)` → `bucket = int(md5hex[:8], 16) % 256` → `weight += 1` →
  L2-нормализация. **Только hashlib** (встроенный `hash()` salted
  per-process — не годится) → детерминирован, офлайн (тесты/CI).
- **`APIEmbedder`** (name `api`, model `qwen3-vl-embedding-8b`) —
  `POST {base_url}/embeddings` `{"model", "input": batch}`, батчи по
  **16**; `dim` узнаётся с первого ответа (реально — **4096**);
  сортировка строк по `index`; сбой сети/HTTP/битый ответ → `KBError`
  «Эмбеддинг-API недоступно: …»; `client` — DI (MockTransport-тесты).
  Ключ — env **`GPUSTACK_KEY_EMBED`** (новая переменная `.env`).

### 4.5 Индекс, метрики, сравнение (`build`)

`build(strategy, embedder)`:

1. корпус → чанки **обеих** стратегий (fixed + structural);
2. эмбеддинги обоих наборов;
3. метрики по каждой стратегии: `chunks`, `avg_chars`, `max_chars`,
   **hit@3 / precision@3 / mrr** по `GOLD_QUERIES` (8 встроенных пар
   `(query, expected_file)` — ожидаемые файлы существуют в реальном
   корпусе: agent.py, memory.py, mcp.py, spec'и дней 14/19/12/13,
   план дня 18);
4. в `index.json` пишется **только активная** стратегия:
   `{version: 1, strategy, embedder, model, dim, built_at, stats:
   {docs, files, chunks, total_chars, build_ms, corpus_words},
   comparison: {fixed: {...}, structural: {...}}, chunks: [...]}`;
5. запись — атомарная (tmp + `os.replace`).

Сравнительный отчёт — то, что пользователь видит в UI после
индексации (таблица, активная стратегия подсвечена). Честный замер на
реальном корпусе с реальными эмбеддингами: **fixed hit@3 = 0.5,
structural hit@3 = 0.25** — structural-чанки больше и их меньше
(целостность секции), на этом gold-наборе по метрикам поиска впереди
fixed.

### 4.6 Поиск (`search`)

`cosine` (чистый stdlib; нулевой вектор → 0.0). `search(query, k=5)` →
top-k `{chunk_id, source, file, section, score, text}` по убыванию
score. Эмбеддер запроса — **из индекса** (`hash` → офлайн, `api` →
env-ключ; нет ключа → `KBError`). Индекс обязателен (нет →
`KBError` «Индекс не построен»).

### 4.7 RAG (`agent.py`)

- `StudioAgent(..., kb=None)` — DI.
- `build_kb_block(query, top_k=3)` → блок для system-промпта:
  «База знаний (релевантные выдержки из документов — используй их,
  если релевантно запросу; цитируй источник (file, section))» + строки
  `N. [file · section] — <текст>` (текст усечён до **300** символов).
  Нет индекса / сбой поиска (`KBError` и пр.) / нет результатов →
  пустая строка; **чат не ломается** (лог `[KB] RAG: поиск не
  удался: …`).
- Настройки БЗ читаются **на каждый запрос** (`self.kb.settings()`):
  `rag=true` → `build_kb_block(message, rag_top_k)` в system-промпт;
  `agent_loop=false` → `tools`-пейлоад не строится и tool-loop не
  крутится (один LLM-вызов, как без MCP-серверов в дне 16),
  MCP-каталог дня 20 не инжектится. Остальная сборка payload
  (профиль/инварианты/память/правила) не меняется.

### 4.8 Тумблеры (`data/kb/settings.json`)

Дефолты: `{"agent_loop": true, "rag": true, "rag_top_k": 3,
"strategy": "structural", "embedder": "hash"}`. Файл отсутствует →
дефолты (без записи). Частичное обновление с валидацией (RU
`ValueError` → 400): `agent_loop`/`rag` — bool; `rag_top_k` — int
1..10; `strategy` — `fixed|structural`; `embedder` — `hash|api`;
лишние ключи игнорируются. Атомарная запись.

### 4.9 Роуты (`main.py`)

`create_app(agent=None, kb=None)` — DI (тесты — TestClient + tmp
`kb_dir`). 6 маршрутов, 400/404/502 с RU-detail:

| Метод | Путь | Семантика |
|---|---|---|
| GET | `/api/kb/stats` | `{exists, strategy, embedder, dim, built_at, stats, comparison, files}`; нет index.json → **404** «Индекс не построен» |
| POST | `/api/kb/index` | `{strategy, embedder}` → `kb.build` → `{stats, comparison, strategy}`; 400 — `strategy: fixed\|structural` / `embedder: hash\|api` / «Ключ эмбеддингов не настроен (GPUSTACK_KEY_EMBED)»; **502** — `KBError` при сборке |
| POST | `/api/kb/upload` | multipart, поле `file`; имя — **только basename** (traversal-safe); расширение вне `UPLOAD_EXTS` → 400 «Неподдерживаемый формат файла»; → `{ok, file, size}` |
| GET | `/api/kb/search` | `?q=&k=5` → `{results: [...]}`; 400 — пустой q / `KBError` (напр. нет ключа); 404 — «Индекс не построен» |
| GET | `/api/kb/settings` | Текущие настройки |
| POST | `/api/kb/settings` | Частичное обновление; 400 — RU-detail |

`requirements.txt`: + **`python-multipart`** (единственная новая
зависимость).

### 4.10 Фронтенд

- `api.ts` — KB-хелперы (stats/index/upload/search/settings).
- `KbTab.tsx` (новый) — вкладка **«База знаний»** в `ContextPanel`
  (после «Инвариантов»; state — вкладка `'kb'`):
  - «Включить» — тумблеры «RAG в диалоге» / «цикл-агента» + «Топ-K в
    диалоге»;
  - «Индексация» — select стратегии + select эмбеддера +
    «Индексировать» (в процессе — «Индексация…»);
  - «Файлы» — «+ Добавить файл» (upload → подсказка переиндексировать);
  - «Статистика» — документы/файлы/чанки/символов/слов в корпусе/dim/
    время сборки;
  - «Сравнение стратегий» — таблица fixed vs structural (чанки,
    avg/max символов, hit@3, precision@3, MRR), активная подсвечена;
  - «Поиск по базе» — поле запроса → top-5 (чип score + `file ·
    section` + отрывок 300 символов).
- `styles.css` — стили вкладки.

## 5. Обработка ошибок

- Индекс не построен → `GET /api/kb/stats`/`GET /api/kb/search` → 404
  «Индекс не построен»; RAG-блок — пустой.
- `POST /api/kb/index` — 400 (значения/ключ), 502 (`KBError` — сбой
  эмбеддинг-API при сборке); `ValueError` стратегии — 400.
- Upload — 400 (формат), basename-safe (`os.path.basename`).
- RAG: любой сбой поиска → пустой блок + лог; **чат никогда не
  рвётся** (тумблер не должен ломать диалог).
- Битый `index.json`/`settings.json` → `read_json` вернёт default
  (индекс «не построен» / настройки = дефолты).

## 6. Тесты (pytest, бэкенд — офлайн)

- `tests/test_kb.py` (32): корпус (3 источника, whitelist uploads),
  `FixedChunker` (размер/overlap/хвост/валидация),
  `StructuredChunker` (заголовки, «(без заголовка)», md без
  заголовков, суб-чанки > 2400, код), `HashEmbedder` (детерминизм,
  L2-норма, dim 256), `APIEmbedder` (MockTransport: батчи 16,
  сортировка по index, ошибки → `KBError`), `cosine`,
  `KnowledgeBase.build` (метрики по GOLD_QUERIES, только активная
  стратегия в chunks, chunk_id, атомарная запись), `search` (top-k,
  эмбеддер из индекса), `settings`/`update_settings` (валидация,
  лишние ключи).
- `tests/test_kb_api.py` (19): 6 роутов через TestClient (200/400/404/
  502, upload basename-safe + whitelist), `build_kb_block` (формат
  блока, усечение 300, сбой → пусто), `agent_loop=false` → без
  `tools` в LLM-payload (fake-MCP), RAG on/off в payload, settings на
  каждый запрос.
- Фронтенд: `tests/kb-tab.test.tsx` (6, Vitest) — вкладки/тумблеры/
  индексиция/upload/статистика/сравнение/поиск.
- Итого: бэкенд **447** PASS, фронтенд **238** PASS, `tsc -b` clean.

## 7. E2E — `scripts/e2e_day21.py` (порт 8105)

Гибрид (stdlib, паттерн e2e_day17–20):

- **Part A** (MUST PASS; офлайн, без uvicorn/сети; 10 assert):
  сборка корпуса (3 источника), оба чанкера (1200/200; md-заголовки,
  суб-чанки > 2400), hash-сборка/пересборка/поиск, валидация settings
  (`rag_top_k=11` → 400), все `/api/kb/*` через TestClient, тумблер
  `agent_loop` с fake-MCP (без `tools` в payload), RAG-блок on/off в
  LLM-payload.
- **Part B** (live: uvicorn :8105 + реальный GPustack LLM +
  API-эмбеддер; best-effort; 10 записей): модели, сборка индекса с
  реальными эмбеддингами (1791 чанк за 162 с, dim 4096, 67 файлов),
  `GET /api/kb/stats`, поиск «tool-loop» (top hit), live RAG-чат —
  «Какой лимит итераций tool-loop у агента (TOOL_LOOP_CAP)?» →
  `done`, модель ответила «15» (KB-блок в журнале LLM-запросов —
  обязательное условие PASS; бессмысленный ответ — НЕ FAIL),
  `agent_loop=false` → без `tools` в payload.
- Port-busy — ожидание до 10 мин (не убивает чужой сервер); cleanup
  всегда; exit 0 для PASS/SKIP, 1 для FAIL.

## 8. Документация и релиз

- `README.md`: строка в таблице дней + секция «День 21» (Что это /
  Архитектура / API / UI / E2E / Проверка задания / Статус).
- `RELEASE.md`: блок дня 21 сверху (файлы, API, проверка задания).
- `openspec/changes/day21-knowledge-base/`: proposal.md, design.md,
  tasks.md, specs/knowledge-base/spec.md.
- `docs/superpowers/plans/2026-09-29-day21-knowledge-base.md` —
  implementation plan (skill writing-plans).
- `.gitignore`: `data/kb/` (index.json, settings.json, uploads/).

## 9. Риски

| Риск | Митигция |
|---|---|
| API-эмбеддер недоступен/медленный (1791 чанк = 162 с) | hash-эмбеддер — дефолт, офлайн/детерминированный; Part A e2e — полностью без API; сбой API — 502/`KBError`, не падение |
| Structural-чанки проигрывают gold-метрикам (hit@3 0.25 vs 0.5) | Честный результат: сравнительный отчёт в индексе + таблица в UI; стратегия выбирается пользователем (дефолт `structural` — целостность секции) |
| Индекс — один JSON-файл (1791 вектор × 4096 dim) | YAGNI: локальный обучающий проект; поиск O(n·d) в stdlib приемлем; замена на векторную БД — вне scope |
| RAG «мусорит» system-промпт нерелевантными выдержками | Усечение 300 символов, top-k ≤ 10, тумблер RAG; инструкция «цитируй источник (file, section)» |

## 10. Вне scope (YAGNI)

- Векторная БД (FAISS/pgvector), инкрементальная индексация, кэш
  эмбеддингов.
- RAG-стриминг источников в ответ, пер-диалоговые базы.
- Изменения MCP-реестра/тулов (дни 16–20 не трогаются — только
  тумблер `agent_loop`).
- Неразрешённые форматы (PDF и пр.) в upload-корпусе.
