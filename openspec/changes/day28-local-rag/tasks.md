# Tasks: day28-local-rag

Задачи дня 28: локальная LLM + RAG (индекс Недели 6 + Ollama).
Финальные цифры (счёт тестов, e2e-статусы) — после задачи 6.

## Бэкенд: эмбеддер (TDD)
- [x] Task 1: `studio/backend/kb.py` — `OllamaEmbedder` + фабрика + диспетчер (TDD): константы `EMBED_MODEL_LOCAL = "nomic-embed-text:v1.5"` и `DEFAULT_OLLAMA_EMBED_URL = "http://localhost:11434/v1"`; класс `OllamaEmbedder` по образцу `APIEmbedder` (name="ollama", model из аргумента/env/дефолта, `api_key` дефолт «ollama», батчи по `_API_BATCH`=16, `dim` с первого ответа, сортировка `data[]` по `index`, `KBError` с упоминанием Ollama при HTTP-сбое/сети/битом ответе, `client` DI для MockTransport); `ollama_embedder_from_env(model=None)` — единая точка сборки (OLLAMA_BASE_URL/OLLAMA_API_KEY/OLLAMA_EMBED_MODEL); ветка `"ollama"` в `_embedder_from_index` — модель ИЗ ИНДЕКСА (`idx["model"]`, `null` → дефолт); `"ollama"` в валидаторе `update_settings`; docstring модуля про полностью локальный RAG; тесты СНАЧАЛА в `test_kb.py` (`_ollama_handler` на MockTransport): дефолты/мета, переопределение модели, батчи 30→16+14, `dim`+`on_progress` на 35, URL+`Bearer`+`model` в теле, HTTP-500→KBError, ConnectError→KBError, нет `data[]`→KBError, перестановка `data[]` по `index`, фабрика из env, дефолты фабрики, диспетчер (hash/ollama-из-индекса/ollama-без-модели/неизвестный) — 15 тестов

## Бэкенд: роуты (TDD)
- [x] Task 2: `studio/backend/main.py` — `ollama` в `POST /api/kb/index` + `model` в stats (TDD): enum эмбеддера `hash|api|ollama` (400 иначе), ветка `ollama_embedder_from_env()` БЕЗ требования ключа; `GET /api/kb/stats` аддитивно отдаёт `model` (фактическая модель эмбеддера); импорт `ollama_embedder_from_env` в оба (rel/dev) блока импортов; тесты СНАЧАЛА в `test_kb_api.py`: сборка `ollama` (meta embedder/model/dim=768), работа при пустых `GPUSTACK_*`, `model` в stats, локальный поиск эмбеддером из индекса находит чанк, `POST /api/kb/settings {embedder: ollama}` → 200, мусор → 400

## E2E
- [x] Task 3: `scripts/e2e_day28.py` (stdlib, порт **8130**, паттерн e2e_day24: net cut `_NO_NET`, port-busy wait, kill-own-server-only, cleanup always, exit 0 PASS/SKIP / 1 FAIL): Part A MUST PASS офлайн — A1 сборка `ollama`-индекса через роут (meta embedder/model/dim, фейковый эмбеддер-двойник), A2 retrieval берёт эмбеддер ИЗ ИНДЕКСА (наблюдатель на `_embedder_from_index`), A3 регрессия `hash`-индекса, A4 RAG-блок в system-промпте LLM, A5 валидация (index 400 / settings 200-400), A6 недоступная Ollama → 502; Part B live best-effort — B0 зонд `/api/tags` (нет `nomic-embed-text:v1.5` → SKIP), B1 wipe+upload+сборка локально (stats), B2 контрольные вопросы находят иголки (≥3 из 5), B3 генерация локальной LLM (`/api/chat` SSE + непустой `rag_context.chunks` из диалога — в `done` его нет), B4 метка `local=true` в `/api/models`; cleanup настроек и сервера в finally

## Фронтенд
- [x] Task 4: `studio/frontend` — третий эмбеддер в UI: `KbEmbedder = 'hash' | 'api' | 'ollama'` и `model?: string | null` в `KbStats` (`api.ts`); `EMBEDDER_LABEL.ollama` + `<option value="ollama">` + обновлённый hint в `KbTab.tsx`; строка «Модель» в статистике (рендерится только при непустом `s.model` — undefined-safe для старого бэкенда); `npx tsc -b` exit 0

## Документация
- [x] Task 5: `.env.example` (шапка дня 28, `OLLAMA_EMBED_MODEL`, `GPUSTACK_KEY_EMBED`/`GPUSTACK_KEY_RERANK`, пометка «опционален с дня 28»); `studio/backend/README.md` — секция «Локальный RAG (день 28)» (три части пайплайна, таблица эмбеддеров, `ollama pull`, e2e); `openspec/changes/day28-local-rag/` — proposal/design/tasks/specs; `README.md` (строка таблицы дня 28 + секция) и `RELEASE.md` (секция дня 28) — после финальных цифр

## Интеграция и регрессия
- [x] Task 6: полная регрессия + живые прогоны: `cd studio/backend && python -m pytest -q -m "not live"` → 0 failed (607 passed, 6 skipped); `cd studio/frontend && npx tsc -b` → exit 0; `python scripts/e2e_day28.py` → Part A 6/6 PASS, Part B 5/5 PASS, exit 0; реальный корпус `data/kb` пересобран `ollama`-эмбеддером (3 файла, 33 чанка, 768 dim, 2.3 с) и проверен живьём: retrieval находит нужный файл, локальная LLM отвечает с цитатой [1]; перехват `httpx.Client.send` подтвердил, что все запросы пайплайна идут только на `localhost:11434`; финальные цифры в README/RELEASE

## Вне scope (guardrails)
- `search` / `search_rag` / `_rrf` / `_bm25_scores` не менять (логика поиска зафиксирована днями 21–23); `hash`- и `api`-эмбеддеры и их индексы не трогать; реранкер и `min_score` дня 23 не менять; `CITE_RULE` / dont-know дня 24 не менять; task-пайплайн не менять; маршрутизация локальной генерации дня 26 (`LOCAL_MODELS`, `_base_for`, `list_models`) не меняться; схема `POST /api/chat` без изменений; новых REST-эндпоинтов нет; новых pip/npm-зависимостей нет; новых MCP-серверов нет; формат `index.db` (version 1) не меняется — миграция не нужна; `hash`/`api` не удаляются и не заменяются; ветку не пушить до финала; main-спеки (`openspec/specs/`) — только после archive.
