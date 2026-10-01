# Proposal: day21-knowledge-base

## Why

День 21: **база знаний** — пайплайн индексации документов (корпус
20–30 страниц, 2 стратегии chunking, эмбеддинги, локальный
JSON-индекс с метаданными, сравнение стратегий) и RAG-инъект в
агентский чат: top-k релевантных выдержек в system-промпт на каждое
сообщение пользователя + тумблеры RAG и цикла-агента (MCP-тулы
tool-loop дня 17). Ключевые решения (решение пользователя): корпус =
доки репозитория + код бэкенда + загрузки пользователя; 2 chunking
(**fixed** 1200 символов/overlap 200 и **structural** по
markdown-заголовкам `#..######`, суб-чанки секций > 2400); 2
эмбеддера (**hash** — stdlib, char 3-граммы → md5 → 256, L2,
детерминированный — офлайн/CI, дефолт; **api** — GPustack
`qwen3-vl-embedding-8b`, dim 4096, батчи 16, ключ
`GPUSTACK_KEY_EMBED`); индекс — **один локальный** `data/kb/index.json`
(атомарная запись, gitignored), активная стратегия — одна,
сравнительный отчёт по обеим (чанки, avg/max символов, hit@3,
precision@3, MRR по 8 встроенным gold-запросам) встроен в индекс;
RAG-выдержка ≤ 300 символов, сбой поиска **не ломает чат**;
тумблеры `data/kb/settings.json` читаются на каждый запрос.

## What Changes

- **`studio/backend/kb.py`** (новый, stdlib + httpx): `KBError`,
  `CorpusDoc`, `Chunk`, `FixedChunker` (1200/200), `StructuredChunker`
  (md `#..######`, секция > 2400 → суб-чанки; код — файл, > 2400 →
  суб-чанки), `HashEmbedder` (256, L2, только hashlib), `APIEmbedder`
  (`POST /embeddings`, `qwen3-vl-embedding-8b`, батчи 16, `client` DI),
  `cosine` (stdlib), `GOLD_QUERIES` (8), `KnowledgeBase` (корпус
  docs/code/upload, `build` — обе стратегии → метрики → index.json
  только активной, `search` top-k, `settings` с валидацией),
  `atomic_write_json` (tmp + `os.replace`, паттерн `memory.py` дня 11).
- **`studio/backend/main.py`**: `create_app(agent, kb)` (DI) + 6
  роутов `/api/kb/*`: `GET /api/kb/stats` (404 «Индекс не построен»),
  `POST /api/kb/index` (400 — значения/нет ключа `GPUSTACK_KEY_EMBED`,
  502 — сбой эмбеддинг-API), `POST /api/kb/upload` (multipart «file»,
  whitelist `UPLOAD_EXTS`, basename-safe), `GET /api/kb/search?q=&k=5`
  (400 — пустой q, 404 — индекс), `GET/POST /api/kb/settings` (400
  RU-detail).
- **`studio/backend/agent.py`**: `StudioAgent(..., kb=None)`,
  `build_kb_block(query, top_k)` (top-k, выдержка ≤ 300 символов,
  источник `[file · section]`; нет индекса/сбой → пустой блок, лог
  `[KB]`, чат жив); settings на каждый запрос; `agent_loop=false` →
  без `tools`/tool-loop (один LLM-вызов, паттерн дня 16 «без
  MCP-серверов»), без MCP-каталога дня 20.
- **`studio/backend/requirements.txt`**: + `python-multipart`
  (единственная новая зависимость).
- **Фронтенд**: `api.ts` — KB-хелперы; `KbTab.tsx` (новый) — вкладка
  «База знаний» («Включить»: тумблеры RAG/цикл-агента + Топ-K;
  «Индексация»: стратегия + эмбеддер + «Индексировать»; «Файлы»:
  «+ Добавить файл»; «Статистика»; «Сравнение стратегий» — таблица,
  активная подсвечена; «Поиск по базе» — top-5: чип score + `file ·
  section` + отрывок 300 символов); `ContextPanel.tsx`/`state.tsx` —
  вкладка `'kb'` после «Инвариантов»; `styles.css`.
- **E2E**: `scripts/e2e_day21.py` (новый, stdlib, порт 8105): Part A —
  офлайн-детерминированное ядро (10 assert: корпус, оба чанкера,
  hash build/rebuild/search, settings-валидация, `/api/kb/*` через
  TestClient, `agent_loop` с fake-MCP, RAG on/off в LLM-payload), MUST
  PASS; Part B — live best-effort (сборка индекса реальными
  эмбеддингами, поиск «tool-loop», live RAG-чат, `agent_loop=false`).
- **`.gitignore`**: `data/kb/` (index.json, settings.json, uploads/).

## Capabilities

### New Capabilities

- `knowledge-base`: база знаний Студии — пайплайн индексации
  документов (корпус 20–30 страниц: доки репозитория + код бэкенда +
  загрузки пользователя; 2 стратегии chunking; 2 эмбеддера; локальный
  JSON-индекс `data/kb/index.json` с метаданными чанков и встроенным
  сравнением стратегий по 8 gold-запросам; поиск косинусом top-k),
  RAG-инъект в агентский чат (top-k выдержек ≤ 300 символов в
  system-промпт, сбой не ломает чат), тумблеры RAG/цикл-агента
  (`data/kb/settings.json`, чтение на каждый запрос), вкладка
  «База знаний» в UI, e2e-гибрид (порт 8105).

### Modified Capabilities

- `mcp-tool-loop` (день 17) / `mcp-orchestration` (день 20):
  тумблер `agent_loop` из KB-settings — `false` → в LLM-payload нет
  `tools` и нет MCP-каталога, tool-loop не крутится (один LLM-вызов,
  поведение дня 16 «без MCP-серверов»); при `agent_loop=true`
  поведение дней 17–20 не меняется.

## Impact

- **Код**: новые `studio/backend/kb.py`,
  `studio/frontend/src/components/KbTab.tsx`, `scripts/e2e_day21.py`,
  тесты (`test_kb.py` 32, `test_kb_api.py` 19,
  `kb-tab.test.tsx` 6); правки `main.py` (DI + 6 роутов), `agent.py`
  (kb/build_kb_block/settings/agent_loop), `requirements.txt`
  (+python-multipart), `api.ts`, `ContextPanel.tsx`, `state.tsx`,
  `styles.css`, `.gitignore`.
- **Данные**: новое runtime-хранилище `data/kb/` (в `.gitignore`):
  `index.json` (активная стратегия + сравнение), `settings.json`,
  `uploads/`. Секрет — новая переменная `.env` `GPUSTACK_KEY_EMBED`
  (в git не попадает).
- **Зависимости**: + `python-multipart` (бэкенд); npm — без
  изменений.
- **Совместимость**: REST/SSE существующих роутов не меняются; тело
  `POST /api/chat` — только system-промпт (KB-блок при `rag=true`);
  регресс дня 16 (без подключённых серверов `tools` в payload нет) —
  сохраняется; дни 11–20 не трогаются.
