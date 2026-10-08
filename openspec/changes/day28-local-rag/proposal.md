# Proposal: day28-local-rag

## Why

День 28: **локальная LLM + RAG** — RAG-пайплайн Студии должен уметь
работать **целиком на машине**, без GPustack и без сети. К дню 28 уже
есть все части, но они разорваны между провайдерами:

- **индекс** (Неделя 6 → дни 21–25, `kb.py`, `index.db` SQLite) умеет
  два эмбеддера: `hash` (офлайн, но 256-мерный char-3-gram md5 —
  «мешок грамм», слабая семантика) и `api` (GPustack
  `qwen3-vl-embedding-8b` — хорошая семантика, но сеть + ключ);
- **генерация** (день 26) уже умеет локальную Ollama
  (`LOCAL_MODELS`, `_base_for()`, `list_models()` помечает `local`).

Не хватает середины — **локального семантического эмбеддера**, из-за
чего локальная генерация (день 26) либо работает без RAG, либо тянет
retrieval из сети. Задание дня 28 закрывает этот разрыв: индекс из
Недели 6 + retrieval локально + генерация локально, всё на Ollama.

Ключевые решения:

- **Третий эмбеддер `ollama`, а не замена `api`.** `hash` и `api`
  остаются нетронутыми (регрессия дней 21–25: индексы и тесты живы).
  `ollama` — новая опция в том же контракте `Embedder` (`name` /
  `model` / `dim` / `embed(texts, on_progress)`), поэтому `kb.build`,
  `search`, `search_rag`, инкремент и метрики работают без правок.
- **Модель эмбеддера берётся ИЗ ИНДЕКСА, а не из env.**
  `_embedder_from_index({"embedder": "ollama", "model": ...})` строит
  эмбеддер на `idx["model"]`: запрос обязан считаться той же моделью,
  что и чанки, иначе косинус в векторной ноге бессмыслен (смена
  `OLLAMA_EMBED_MODEL` в env не должна ломать поиск по старому
  индексу).
- **Ключ не нужен.** Ollama не проверяет `Authorization`; уходит
  заглушка `OLLAMA_API_KEY` («ollama») — как в дне 26. Это и есть
  признак локальности: `embedder=ollama` работает при пустых
  `GPUSTACK_*`.
- **Смена эмбеддера = другой индекс.** `mode: "auto"` пересобирает
  полностью при несовпадении `embedder` (существующее правило дня 21
  распространяется на `ollama` без изменений) — векторы `api` (8b,
  иная размерность) и `ollama` (768) несравнимы.
- **Сеть не деградирует молча.** Недоступная Ollama на сборке → 502
  (как сбой `api`-эмбеддера), а не тихий `hash`-фолбэк: подмена
  эмбеддера дала бы индекс с несравнимыми векторами. Лексический
  (BM25) фолбэк в `search` остаётся для старого поведения при
  недоступности векторной ноги (у `ollama`-индекса всегда есть
  `terms`).

## What Changes

- `studio/backend/kb.py`: класс `OllamaEmbedder` (OpenAI-совместимый
  `POST {base}/embeddings`, батчи по 16, `dim` с первого ответа,
  `KBError` при сбое), константы `EMBED_MODEL_LOCAL =
  "nomic-embed-text:v1.5"` / `DEFAULT_OLLAMA_EMBED_URL`; фабрика
  `ollama_embedder_from_env(model=None)`; ветка `"ollama"` в
  `_embedder_from_index` (модель из индекса); `"ollama"` в валидаторе
  `update_settings`.
- `studio/backend/main.py`: `POST /api/kb/index` принимает
  `embedder: hash|api|ollama` (ollama — без требования ключа);
  `GET /api/kb/stats` аддитивно отдаёт `model` (фактическая модель
  эмбеддера — в UI видно, чем построен индекс).
- `studio/frontend`: `KbEmbedder = 'hash' | 'api' | 'ollama'`, опция и
  подпись в селекторе `KbTab`, строка «Модель» в статистике,
  `KbStats.model?: string | null` (`api.ts`).
- `scripts/e2e_day28.py`: Part A офлайн (MUST PASS) + Part B live на
  Ollama (best-effort, SKIP без Ollama).
- Тесты: `test_kb.py` (15 тестов: `OllamaEmbedder` + диспетчер),
  `test_kb_api.py` (6 тестов роутов `ollama`).
- Документация: `.env.example` (`OLLAMA_EMBED_MODEL`,
  `GPUSTACK_KEY_EMBED`/`GPUSTACK_KEY_RERANK`), `studio/backend/README.md`
  (секция «Локальный RAG (день 28)»), `README.md` / `RELEASE.md`.

## Capabilities

- **ADDED** `local-rag-ollama` — локальный эмбеддер `ollama` в
  RAG-пайплайне: сборка индекса, retrieval (модель из индекса),
  работа без ключей и сети, деградация 502, UI-выбор и статистика.

## Impact

Затронуты: `kb.py` (аддитивно — новый класс + ветки диспетчера и
валидатора), `main.py` (расширение enum эмбеддера + поле `model` в
stats), фронтенд (тип, селектор, строка статистики). **Не меняются:**
`search` / `search_rag` / `_rrf` / `_bm25_scores` (логика поиска
зафиксирована), `hash`/`api`-эмбеддеры и их индексы, реранкер дня 23,
`min_score`-фильтрация, `CITE_RULE` / dont-know дня 24, task-пайплайн,
day26-маршрутизация локальной генерации (`LOCAL_MODELS`,
`_base_for`), схема `POST /api/chat`, новых REST-эндпоинтов нет,
новых pip/npm-зависимостей нет. `index.db` того же формата (version
1) — старые индексы читаются как раньше; `model` в meta уже писался
днями 21–25 (у `hash` — `null`).

## Guardrails

- Сеть не деградирует молча: недоступная Ollama → `KBError` → 502
  (не подмена эмбеддера, не пустые векторы).
- Модель для retrieval берётся из индекса; env — только дефолт для
  новой сборки.
- Ветку не пушить до финала.
