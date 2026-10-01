# День 21: База знаний — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Пайплайн индексации документов (2 стратегии chunking, 2 эмбеддера, локальный JSON-индекс с метаданными и сравнением стратегий) + RAG-инъект в агентский чат (top-k выдержек ≤ 300 символов в system-промпт на каждое сообщение) + тумблеры RAG/цикл-агента + вкладка «База знаний» в UI.

**Architecture:** Новый `studio/backend/kb.py` (stdlib + httpx): корпус (доки репозитория + код бэкенда + загрузки пользователя) → чанки (FixedChunker 1200/200 / StructuredChunker по markdown-заголовкам, суб-чанки > 2400) → эмбеддинги (HashEmbedder stdlib — офлайн/дефолт / APIEmbedder GPustack `qwen3-vl-embedding-8b` dim 4096, батчи 16) → `data/kb/index.json` (атомарная запись, только активная стратегия, встроенное сравнение по 8 gold-запросам) → поиск косинусом top-k. `agent.py` — `build_kb_block` + settings на каждый запрос + `agent_loop=false` → без `tools`/tool-loop. `main.py` — `create_app(agent, kb)` DI + 6 роутов `/api/kb/*`. Фронтенд — вкладка «База знаний» (`KbTab.tsx`).

**Tech Stack:** Python stdlib (hashlib/math, JSON) + httpx (API-эмбеддер) + python-multipart (upload); FastAPI/TestClient (роуты); React 19 + Vitest (вкладка, без новых npm-зависимостей).

**Spec:** `docs/superpowers/specs/2026-09-29-day21-knowledge-base-design.md`

## Global Constraints

- Ветка: `day21-doc-indexing` (создана; от `day20-mcp-orchestration`).
- Все пользовательские тексты и ошибки — на русском (RU-detail).
- Runtime-данные — `data/kb/` (index.json, settings.json, uploads/) — в `.gitignore`.
- Единственная новая зависимость — `python-multipart` (бэкенд); никаких новых npm-зависимостей.
- Hash-эмбеддер — только hashlib (встроенный `hash()` salted per-process — не годится); детерминизм обязателен.
- Тесты бэкенда — офлайн (tmp_path + TestClient + httpx.MockTransport, без сети).
- RAG-сбой (нет индекса/поиска) MUST NOT ломать чат (пустой блок + лог).
- E2E порт: **8105** (8100 — `e2e_studio.py`, 8101–8104 — дни 17–20); Part A — MUST PASS без сети.
- Запуск тестов: из `studio/backend` — `python -m pytest -q`.

### Константы (канонический список)

| Константа | Значение |
|---|---|
| `DEFAULT_CHUNK_CHARS` | 1200 |
| `DEFAULT_OVERLAP` | 200 |
| `DEFAULT_MAX_SECTION` | 2400 |
| `HASH_DIM` | 256 |
| `EMBED_MODEL` | `qwen3-vl-embedding-8b` |
| `_API_BATCH` | 16 |
| `rag_top_k` (дефолт) | 3 (1..10) |
| Дефолт settings | `{agent_loop: true, rag: true, rag_top_k: 3, strategy: "structural", embedder: "hash"}` |
| Выдержка RAG-блока | ≤ 300 символов |

### Файлы (сводка)

- Create: `studio/backend/kb.py`, `studio/frontend/src/components/KbTab.tsx`, `scripts/e2e_day21.py`
- Create (тесты): `studio/backend/tests/test_kb.py` (32), `studio/backend/tests/test_kb_api.py` (19), `studio/frontend/tests/kb-tab.test.tsx` (6)
- Modify: `studio/backend/main.py` (`create_app(agent, kb)` DI + 6 роутов `/api/kb/*`), `studio/backend/agent.py` (`kb=None`, `build_kb_block`, settings на каждый запрос, `agent_loop=false` → без `tools`), `studio/backend/requirements.txt` (+ `python-multipart`), `studio/frontend/src/api.ts` (KB-хелперы), `ContextPanel.tsx` + `state.tsx` (вкладка `'kb'` после «Инвариантов»), `styles.css`, `.gitignore` (`data/kb/`)
- Docs: `README.md` (таблица + секция «День 21»), `RELEASE.md`, `openspec/changes/day21-knowledge-base/` (структура как у `openspec/changes/day20-mcp-orchestration/`)

---

### Task 1: `kb.py` — корпус + FixedChunker

**Files:**
- Create: `studio/backend/kb.py`
- Test: `studio/backend/tests/test_kb.py`

**Interfaces:**
- Produces: `KBError`, `CorpusDoc(path, text, source)`, `Chunk(text, source, file, section, chunk_index)`, `atomic_write_json`, `read_json`, `KnowledgeBase.corpus_files()`, `FixedChunker(chunk_chars=1200, overlap=200)`.

- [x] **Step 1: Write the failing tests** — корпус (3 источника: docs/code/upload; whitelist `UPLOAD_EXTS` для uploads), FixedChunker (размер/overlap, хвост предыдущего = голова следующего, пустой текст → `[]`, валидация ValueError).
- [x] **Step 2: Run tests, verify they fail** — `ModuleNotFoundError: No module named 'kb'`.
- [x] **Step 3: Implement** — `KBError`, dataclass'ы, `atomic_write_json` (tmp + `os.replace`, паттерн `memory.py` дня 11), `read_json` (битый файл → default), `corpus_files()` (README/RELEASE, `docs/superpowers/**/*.md`, `openspec/**/*.md`, `studio/backend/*.py`, `uploads/*`), `FixedChunker`.
- [x] **Step 4: Run tests, verify they pass.**
- [x] **Step 5: Commit.**

---

### Task 2: `kb.py` — StructuredChunker

**Files:**
- Modify: `studio/backend/kb.py`, `studio/backend/tests/test_kb.py`

**Interfaces:**
- Consumes: `CorpusDoc`, `Chunk`, `FixedChunker`.
- Produces: `StructuredChunker(max_section=2400)` — md: секции по `#{1,6}` (заголовок в `section`; текст до первого заголовка → «(без заголовка)»; md без заголовков → `whole file`); секция > 2400 → суб-чанки по FixedChunker с сохранением секции; не-md: файл целиком, > 2400 → суб-чанки.

- [x] **Step 1: Write the failing tests** — заголовки, «(без заголовка)», md без заголовков, длинная секция (> 2400), длинный код-файл.
- [x] **Step 2: Run, verify they fail.**
- [x] **Step 3: Implement** `StructuredChunker` (regex `^(#{1,6})\s+(.*\S)\s*$`, `_sections`, `_chunk_md`).
- [x] **Step 4: Run, verify they pass.**
- [x] **Step 5: Commit.**

---

### Task 3: `kb.py` — эмбеддеры + cosine

**Files:**
- Modify: `studio/backend/kb.py`, `studio/backend/tests/test_kb.py`

**Interfaces:**
- Produces: `cosine(a, b)` (stdlib, нулевой вектор → 0.0), `HashEmbedder` (name `hash`, dim 256: char 3-граммы → `md5` → `int(hex[:8],16) % 256` → weight → L2), `APIEmbedder` (name `api`, model `qwen3-vl-embedding-8b`: `POST {base}/embeddings`, батчи 16, dim из первого ответа, sort по `index`, сбой → `KBError`; `client` DI).

- [x] **Step 1: Write the failing tests** — hash: детерминизм, L2-норма ≈ 1, dim 256; api (MockTransport): батчи 16, сортировка по `index`, HTTP/битый ответ → `KBError`; cosine: параллельные/перпендикулярные/нулевой.
- [x] **Step 2: Run, verify they fail.**
- [x] **Step 3: Implement** (`hashlib` only — без salted `hash()`; httpx — локальный импорт).
- [x] **Step 4: Run, verify they pass.**
- [x] **Step 5: Commit.**

---

### Task 4: `kb.py` — KnowledgeBase: build/metrics/search/settings

**Files:**
- Modify: `studio/backend/kb.py`, `studio/backend/tests/test_kb.py`

**Interfaces:**
- Consumes: chunk'еры, эмбеддеры, `cosine`, `atomic_write_json`.
- Produces: `GOLD_QUERIES` (8 пар `(query, expected_file)` — файлы из реального корпуса), `KnowledgeBase(kb_dir, repo_root=None)`: `build(strategy, embedder)` (чанки **обеих** стратегий → метрики hit@3/precision@3/MRR + chunks/avg/max chars → index.json **только активной**: `{version, strategy, embedder, model, dim, built_at, stats{docs, files, chunks, total_chars, build_ms, corpus_words}, comparison, chunks[{chunk_id, source, file, section, chars, text, vector}]}`; `chunk_id = "{stem}-{strategy}-{i:04d}"`), `search(query, k=5)` (эмбеддер из индекса; нет индекса → `KBError` «Индекс не построен»), `settings()`/`update_settings(patch)` (дефолты, RU `ValueError`, лишние ключи игнорируются, атомарная запись).

- [x] **Step 1: Write the failing tests** — build (метрики, только активная стратегия, chunk_id, атомарность), search (top-k, эмбеддер из индекса, 404-семантика), settings (валидация: `rag_top_k=11` → ValueError, bool-тумблеры, strategy/embedder enum, лишние ключи).
- [x] **Step 2: Run, verify they fail.**
- [x] **Step 3: Implement.**
- [x] **Step 4: Run, verify they pass** (итого `test_kb.py` — 32).
- [x] **Step 5: Commit.**

---

### Task 5: `main.py` — create_app DI + 6 роутов `/api/kb/*`

**Files:**
- Modify: `studio/backend/main.py`, `studio/backend/requirements.txt`, `studio/backend/tests/test_kb_api.py` (новый)

**Interfaces:**
- Consumes: `kb.KnowledgeBase`, `kb.HashEmbedder`, `kb.APIEmbedder`, `kb.KBError`, `kb.UPLOAD_EXTS`.
- Produces: `create_app(agent=None, kb=None)`; роуты: `GET /api/kb/stats` (404 «Индекс не построен»), `POST /api/kb/index` (400 — значения/ключ `GPUSTACK_KEY_EMBED`, 502 — `KBError`), `POST /api/kb/upload` (multipart «file», basename-safe, whitelist; `{ok, file, size}`), `GET /api/kb/search?q=&k=5` (400 — пустой q, 404 — индекс), `GET/POST /api/kb/settings` (400 RU-detail).

- [x] **Step 1: Write the failing tests** (TestClient, tmp `kb_dir`) — 200/400/404/502 каждого роута, upload basename-safe (`../x.md` → `x.md`) + whitelist, DI (`create_app(kb=...)`).
- [x] **Step 2: Run, verify they fail.**
- [x] **Step 3: Implement** (+ `python-multipart` в `requirements.txt`).
- [x] **Step 4: Run, verify they pass.**
- [x] **Step 5: Commit.**

---

### Task 6: `agent.py` — RAG-блок + тумблеры

**Files:**
- Modify: `studio/backend/agent.py`, `studio/backend/tests/test_kb_api.py`

**Interfaces:**
- Consumes: `kb` (DI, `kb=None`).
- Produces: `StudioAgent(..., kb=None)`; `build_kb_block(query, top_k=3)` — блок «База знаний» (top-k, текст ≤ 300 символов, источник `[file · section]`); нет индекса/сбой/нет результатов → `""` (лог `[KB]`, чат жив); в `ask_stream`: settings на каждый запрос — `rag=true` → блок в system-промпт; `agent_loop=false` → без `tools`-пейлоада и без tool-loop (один LLM-вызов, паттерн дня 16), без MCP-каталога дня 20.

- [x] **Step 1: Write the failing tests** — формат блока/усечение 300/сбой → пусто; `agent_loop=false` → в payload нет `tools` (fake-MCP с подключённым сервером); `rag=false` → блок отсутствует; settings читаются на каждый запрос (переключил между запросами — поведение сменилось).
- [x] **Step 2: Run, verify they fail.**
- [x] **Step 3: Implement.**
- [x] **Step 4: Run, verify they pass** (итого `test_kb_api.py` — 19; бэкенд в целом — 447).
- [x] **Step 5: Commit.**

---

### Task 7: Фронтенд — вкладка «База знаний»

**Files:**
- Modify: `studio/frontend/src/api.ts`, `ContextPanel.tsx`, `state.tsx`, `styles.css`
- Create: `studio/frontend/src/components/KbTab.tsx`
- Test: `studio/frontend/tests/kb-tab.test.tsx`

**Interfaces:**
- Consumes: роуты Task 5 (`api.ts`-хелперы: kbStats/kbIndex/kbUpload/kbSearch/kbGetSettings/kbUpdateSettings).
- Produces: вкладка `'kb'` после «Инвариантов»: «Включить» (тумблеры RAG/цикл-агента + Топ-K), «Индексация» (select'ы + «Индексировать»/«Индексация…»), «Файлы» («+ Добавить файл», подсказка переиндексировать), «Статистика», «Сравнение стратегий» (таблица fixed vs structural, активная подсвечена), «Поиск по базе» (top-5: чип score + `file · section` + отрывок 300 символов).

- [x] **Step 1: Write the failing tests** (6, Vitest + Testing Library) — рендер вкладок/тумблеров, смена Tоп-K, «Индексировать» (fetch mock), upload (FormData), таблица сравнения с подсветкой активной, поиск (результаты с чипом score).
- [x] **Step 2: Run, verify they fail.**
- [x] **Step 3: Implement** (KbTab + api-хелперы + ContextPanel/state + стили).
- [x] **Step 4: Run, verify they pass** (фронтенд в целом — 238) + `tsc -b` clean.
- [x] **Step 5: Commit.**

---

### Task 8: E2E — `scripts/e2e_day21.py` (порт 8105)

**Files:**
- Create: `scripts/e2e_day21.py` (stdlib)

- [x] **Step 1: Part A** (MUST PASS, офлайн, без uvicorn/сети; 10 assert) — корпус (3 источника), оба чанкера (1200/200; md-заголовки, суб-чанки > 2400), hash-сборка/пересборка/поиск, settings-валидация (`rag_top_k=11` → 400), все `/api/kb/*` через TestClient, `agent_loop` с fake-MCP (без `tools` в payload), RAG-блок on/off в LLM-payload.
- [x] **Step 2: Part B** (live, uvicorn :8105, реальный GPustack LLM + API-эмбеддер; best-effort; 10 записей) — модели, сборка индекса (1791 чанк за 162 с, dim 4096), stats, search «tool-loop» (top hit), live RAG-чат (вопрос про `TOOL_LOOP_CAP` → ответ «15», KB-блок в журнале LLM-запросов — условие PASS; бессмысленный ответ — не FAIL), `agent_loop=false` → без `tools`.
- [x] **Step 3: Port-busy — ожидание до 10 мин (не убивать чужой сервер), cleanup всегда, exit 0 для PASS/SKIP, 1 для FAIL.** Прогон на этой машине: Part A 10/10 PASS, Part B 10/10 PASS.
- [x] **Step 4: Commit.**

---

### Task 9: Документация и финальная проверка

**Files:**
- Modify: `README.md` (строка в таблице + секция «День 21»), `RELEASE.md` (блок дня 21 сверху), `.gitignore` (`data/kb/`)
- Create: `openspec/changes/day21-knowledge-base/` (proposal.md, design.md, tasks.md, specs/knowledge-base/spec.md, `.openspec.yaml`), `docs/superpowers/plans/2026-09-29-day21-knowledge-base.md` (этот план)

- [x] **Step 1: README/RELEASE/openspec** — секция «День 21» (Что это / Архитектура / API / UI / E2E / Проверка задания / Статус); RELEASE — файлы, API, проверка задания; openspec — change с ADDED Requirements.
- [x] **Step 2: Live-артефакт** — собрать индекс реальными эмбеддингами (structural, api, `qwen3-vl-embedding-8b`, dim 4096, 1791 чанк, 67 файлов) и оставить на диске (`data/kb/index.json`, gitignored).
- [x] **Step 3: Финальный прогон** (критерий закрытия дня): бэкенд 447 PASS; фронтенд 238 PASS + `tsc -b` clean; e2e_day21 Part A 10/10 PASS, Part B 10/10 PASS.
- [x] **Step 4: Commit.**
