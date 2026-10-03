## Purpose

Capability «rag-filter-rewrite» — реранкинг и фильтрация (день 23):
порог отсечения нерелевантных результатов `min_score` (0..1, default
0.0 = off; `>=`-семантика; фильтр в `kb.search_rag` после
stage1-отбора (и rerank) и до `top_k`-среза: `reranker=api` →
`rerank_score >= min_score`, `reranker=off` → relative-нормализация
`score >= min_score * best`) в settings и во всех 3 потребителях
поиска; query rewrite (один LLM-вызов T=0, max_tokens=200, только в
compare, graceful fallback на оригинал при сбое) и 4-режимное
сравнение в `POST /api/rag/compare` (plain | rag | rag+filter |
rag+rewrite; plain/rag никогда не фильтруют; body-override
`min_score` с валидацией 400 RU; аддитивный контракт ответа —
day22-поля не меняются, e2e_day22 остаётся green). E2E-гибрид —
`scripts/e2e_day23.py` (порт 8107; Part A MUST PASS офлайн, Part B
live best-effort). Чат-пайплайн (`ask_stream`), chunking,
embedder, RRF/BM25 и APIReranker-internals не меняются.

## ADDED Requirements

### Requirement: Порог `min_score` отсекает нерелевантные результаты

Система SHALL поддерживать настройку `min_score` (число 0..1, default
**0.0**) в settings базы знаний и параметр `min_score` в
`kb.search_rag`. При `min_score = 0.0` поведение `search_rag` SHALL
оставаться байт-в-байт идентичным поведению до дня (фильтр выключен).
При `min_score > 0` отсечение SHALL применяться **после** stage1-
отбора (и после rerank, если сработал) и **до** `top_k`-среза с
семантикой `>=`: при `reranker=api` результат проходит при
`rerank_score >= min_score`; при `reranker=off` — при
`score >= min_score * best` (best = максимальный score выдачи;
`best == 0` → пустой список, без деления). Ответ поиска дополняется
аддитивными полями `filtered` (bool) и `dropped` (int); существующие
поля не меняются. Фильтр действует консистентно во всех 3
потребителях `search_rag` (live-чат, `GET /api/kb/search`,
`POST /api/rag/compare`).

#### Scenario: Фильтр отключён (default) — поведение неизменно

- **WHEN** `min_score = 0.0` (default) и выполнен поиск `search_rag`
- **THEN** выдача идентична поиску без параметра (снимок-сравнение списков); `filtered == false`; существующие поля ответа не изменены

#### Scenario: Фильтр отсекает по rerank-оценке

- **WHEN** `reranker=api`, fake-reranker выдаёт оценки [0.99, 0.5, 0.1], `min_score = 0.6`
- **THEN** в выдаче ровно 1 результат (0.99), `dropped == 2`; при `min_score = 0.999` — 0 результатов (пустой список, не ошибка); при `min_score = 1.0` результат с оценкой 1.0 остаётся (`>=`)

#### Scenario: Relative-режим без реранкера

- **WHEN** `reranker=off` и `min_score = 0.9999`
- **THEN** лучший результат (score == best) проходит (`score/best = 1.0 >= 0.9999`), остальные отсечены при score < best; при пустой stage1-выдаче (`best == 0`) — пустой список без исключения

#### Scenario: Всё отфильтровано — graceful

- **WHEN** `min_score = 0.999` выше всех оценок выдачи
- **THEN** 200: `results == []`, `dropped` = число кандидатов, поиск не падает; потребители (чат/compare) отвечают на пустом контексте, не с ошибкой 5xx

#### Scenario: Валидация `min_score` → 400 RU

- **WHEN** `update_settings({"min_score": v})` с `v` = `true` / `"0.5"` / `1.5` / `-0.1`
- **THEN** 400 с RU-detail (bool проверяется ПЕРВЫМ, до проверки int/float; строки-числа не парсятся); `v` = `0.5` → 200, GET settings → `0.5`; `0` и `1` — валидны

### Requirement: Query rewrite в compare

Система SHALL предоставлять `StudioAgent.rewrite_query(question) ->
(str, bool)`: один non-stream LLM-вызов (T=0, max_tokens=200,
system-промпт «перефразируй одним предложением ключевыми словами»).
Возврат `(rewritten, True)` — только при непустом (после strip) и не
идентичном оригиналу (case-insensitive) ответе; пустой/identical —
`(question, False)`. Сбой LLM-вызова SHALL обрабатываться как
`(question, False)` с log warning: исключение наружу не
пробрасывается, «Ошибка:» не возвращается. `rewrite_query`
вызывается **только** из `rag_compare` (rewrite-арма); из
`ask_stream`/чат-пайплайна — никогда (guardrail-тест).

#### Scenario: Успешный rewrite

- **WHEN** LLM-вызов возвращает перефраз, отличный от оригинала
- **THEN** `(rewritten, True)`; параметры вызова — T=0, max_tokens=200; в compare `rewritten_query == rewritten`, `rewrite_applied == true`, retrieval rewrite-армы выполнен на перефразе (её чанки ≠ чанки rag-армы при другом запросе к индексу)

#### Scenario: Сбой LLM → fallback на оригинал

- **WHEN** LLM-вызов rewrite завершился исключением (таймаут/ошибка API)
- **THEN** `(question, False)`, исключение наружу не пробрасывается; в compare `rewrite_applied == false`, `rewritten_query` == оригиналу, `answer_rag_rewrite` — обычный ответ (НЕ начинается «Ошибка:»)

#### Scenario: Skip — пустой или идентичный ответ

- **WHEN** LLM-вызов вернул пустую строку, whitespace или текст, идентичный оригиналу (case-insensitive)
- **THEN** `(question, False)`; retrieval выполняется на оригинале, `rewrite_applied == false`

### Requirement: Сравнение 4 режимов в `POST /api/rag/compare`

Маршрут `POST /api/rag/compare` SHALL принимать JSON-тело
`{question: str, min_score?: float}` и возвращать 4 армы: **plain**
(без retrieval), **rag** (`kb_block` в system-промпте), **rag+filter**
(retrieval с `min_score` — из body-override, иначе из settings),
**rag+rewrite** (retrieval на `rewritten_query`, ANSWER-LLM отвечает на
**оригинальный** вопрос). Армы plain и rag SHALL **никогда** не
фильтроваться (даже при `min_score > 0` в body или settings).
Валидация `min_score` в body — та же, что в settings (bool/str/
out-of-range → 400 RU). Контракт ответа — **аддитивный**:
существующие поля дня 22 (`answer_plain`, `answer_rag`, `kb_block`,
`chunks`, `rag_context`) сохраняются с теми же именами и типами;
добавляются `answer_rag_filter`, `answer_rag_rewrite`,
`chunks_rag_filter`, `chunks_rag_rewrite`, `rag_context_rag_filter`,
`rag_context_rag_rewrite`, `rewritten_query`, `rewrite_applied`.
Сбой LLM-вызова руки → «Ошибка: …» в этой руке (статус 200); сбой
rewrite не помечает руку как «Ошибка:» (fallback).

#### Scenario: 4 армы в одном вызове

- **WHEN** `POST /api/rag/compare {"question": "…"}` при построенном индексе
- **THEN** 200: `answer_plain`, `answer_rag`, `answer_rag_filter`, `answer_rag_rewrite` — непустые строки; `rewritten_query` (str) и `rewrite_applied` (bool) present; `chunks_rag_filter`, `chunks_rag_rewrite` — списки; все day22-поля (`answer_plain`, `answer_rag`, `kb_block`, `chunks`, `rag_context`) present с прежними именами и типами

#### Scenario: plain/rag не фильтруются; фильтр — только filter-арма

- **WHEN** `POST /api/rag/compare {"question": q, "min_score": 0.6}` (fake-reranker [0.99, 0.5, 0.1])
- **THEN** 200: `chunks` (rag-арма) — 3 результата (топ-3 без фильтра, идентичен запуску без body-override), `chunks_plain`-эквивалент (plain-рука) не использует retrieval; `chunks_rag_filter` — ровно 1 результат (0.99); settings при этом не меняются

#### Scenario: Body-override `min_score`: валидация и default

- **WHEN** `POST /api/rag/compare {"question": q, "min_score": v}` с `v` = `true` / `"0.5"` / `1.5`
- **THEN** 400 с RU-detail; без `min_score` в body — filter-арма использует `settings["min_score"]` (default 0.0 → выдача filter-армы совпадает с rag-армой)

#### Scenario: Аддитивный контракт (регрессия дня 22)

- **WHEN** выполнен `POST /api/rag/compare` и ответ сверяется с эталонным day22-payload
- **THEN** существующие поля byte-совместимы (имена, типы, значения plain/rag-рук); `scripts/e2e_day22.py` Part A проходит 7/7 без правок
