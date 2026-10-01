# Tasks: day21-knowledge-base

## Пайплайн (kb.py)
- [x] Task 1: `kb.py` — корпус (docs/code/upload, whitelist `UPLOAD_EXTS`) + `CorpusDoc`/`Chunk` + `atomic_write_json` + `FixedChunker` (1200/200) (+ тесты)
- [x] Task 2: `StructuredChunker` — markdown `#..######` (заголовок в `section`, «(без заголовка)», md без заголовков → `whole file`), секция > 2400 → суб-чанки по fixed; код — файл, > 2400 → суб-чанки (+ тесты)
- [x] Task 3: `HashEmbedder` (stdlib: char 3-граммы → md5 → 256, L2; только hashlib) + `APIEmbedder` (GPustack `/embeddings`, `qwen3-vl-embedding-8b`, dim 4096, батчи 16, `GPUSTACK_KEY_EMBED`, `client` DI) + `cosine` (+ тесты, MockTransport)
- [x] Task 4: `KnowledgeBase` — `build` (чанки обеих стратегий → метрики hit@3/precision@3/MRR по 8 `GOLD_QUERIES` → index.json только активной, `chunk_id = {stem}-{strategy}-NNNN`, метаданные `{chunk_id, source, file, section, chars, text, vector}`, атомарная запись), `search` top-k (эмбеддер из индекса), `settings`/`update_settings` (дефолты, RU-валидация, лишние ключи игнорируются) (+ тесты; итого `test_kb.py` — 32)

## Интеграция
- [x] Task 5: `main.py` — `create_app(agent, kb)` DI + 6 роутов `/api/kb/*` (stats 404 «Индекс не построен»; index 400/502; upload multipart «file», whitelist, basename-safe; search 400/404; settings GET/POST 400 RU-detail); `requirements.txt` + `python-multipart` (+ `test_kb_api.py`)
- [x] Task 6: `agent.py` — `StudioAgent(..., kb=None)`, `build_kb_block(query, top_k)` (top-k, выдержка ≤ 300 символов, источник `[file · section]`; сбой → пустой блок + лог `[KB]`, чат жив), settings на каждый запрос, `agent_loop=false` → без `tools`/tool-loop и без MCP-каталога (+ тесты; итого `test_kb_api.py` — 19)

## UI
- [x] Task 7: фронтенд — `api.ts` KB-хелперы, `KbTab.tsx` (вкладка «База знаний»: «Включить» — тумблеры RAG/цикл-агента + Топ-K, «Индексация» — стратегия/эмбеддер + «Индексировать», «Файлы» — «+ Добавить файл», «Статистика», «Сравнение стратегий» — таблица с подсветкой активной, «Поиск по базе» — top-5 с чипом score + `file · section` + отрывок 300 символов), вкладка `'kb'` в `ContextPanel`/`state.tsx` после «Инвариантов», `styles.css` (6 тестов kb-tab)

## E2E
- [x] Task 8: `scripts/e2e_day21.py` (stdlib, порт 8105, гибрид): Part A — офлайн-детерминированное ядро, 10 assert (корпус, оба чанкера, hash build/rebuild/search, settings-валидация, `/api/kb/*` через TestClient, `agent_loop` с fake-MCP, RAG on/off в LLM-payload), MUST PASS; Part B — live best-effort (сборка индекса реальными эмбеддингами, поиск «tool-loop», live RAG-чат, `agent_loop=false`); port-busy — ожидание до 10 мин, cleanup всегда

## Документация и финальная проверка
- [x] Task 9: README (таблица + секция «День 21»), RELEASE, openspec-изменение, план, `.gitignore` (`data/kb/`); live-индекс на диске (structural, api, `qwen3-vl-embedding-8b`, dim 4096, 1791 чанк, 67 файлов); полный прогон (критерий закрытия дня)
  - Финальный прогон: бэкенд 447 PASS (включая 32 kb + 19 kb-роуты/агент); фронтенд 238 PASS (включая 6 kb-tab) + `tsc -b` clean; e2e_day21 Part A 10/10 PASS, Part B 10/10 PASS (live: 1791 чанк / 162 с / dim 4096; модель ответила «15» на вопрос про `TOOL_LOOP_CAP`, KB-блок в журнале LLM-запросов; `agent_loop=false` → без `tools` в payload)
