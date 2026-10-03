# Proposal: day23-rerank-filter

## Why

День 23: **реранкинг и фильтрация** — поверх существующего day21-реранкера
(`APIReranker` `qwen3-reranker-4b`, 2-этапный `search_rag`) и day22-
сравнения (`POST /api/rag/compare`) добавить: (1) **порог отсечения
нерелевантных результатов** `min_score` (0..1, дефолт 0.0 = off), (2)
**query rewrite** (один LLM-вызов, только в compare, не трогает live-чат)
и (3) **4-режимное сравнение качества** (plain | rag | rag+filter |
rag+rewrite) в одном HTTP-вызове. Ключевые решения (зафиксированы в плане
дня `.omo/plans/day23-rerank-filter.md`):

- **Фильтр — после stage1-отбора (и rerank, если был), до `top_k`-среза.**
  Семантика `>=`. При `reranker=api` отсечение по `rerank_score >=
  min_score` (шкала 0..1 от cross-encoder); при `reranker=off` — по
  **относительной нормализации** `score >= min_score * best` (best =
  максимальный RRF-счёт выдачи; guard: `best == 0` → пустой список),
  потому что сырой RRF-счёт (Σ 1/(60+rank) ≈ 0.003–0.033) несовместим с
  абсолютной шкалой 0..1.
- **`min_score = 0.0` (default) → поведение байт-в-байт как до дня**
  (регрессия-гард: снимок-сравнение списков).
- **Rewrite — compare-only**: один non-stream LLM-вызов (T=0,
  max_tokens=200) перефразирует вопрос для retrieval; ANSWER-LLM отвечает
  на **оригинальный** вопрос; сбой/пустой/идентичный ответ → graceful
  fallback на оригинал, `rewrite_applied=false`, исключение не
  пробрасывается. Rewrite **не вызывается из `ask_stream`**
  (guardrail-тест).
- **4 армы compare**: plain | rag | rag+filter | rag+rewrite; **plain/rag
  НИКОГДА не фильтруют** (контрольные руки; фильтр даже при заданном
  `min_score` действует только в filter-арме). Контракт ответа
  **аддитивный**: старые поля дня 22 (`answer_plain`, `answer_rag`,
  `kb_block`, `chunks`, `rag_context`) не трогаются (e2e_day22 ссылается на
  них ~51 место и должен остаться green); добавляются `answer_rag_filter`,
  `answer_rag_rewrite`, per-arm `chunks_rag_filter`/`chunks_rag_rewrite`
  и `rag_context_*`, `rewritten_query`, `rewrite_applied`.
- **Валидация `min_score`** (settings и body-override): bool **первым**
  (Python-ловушка `isinstance(True, int)`), затем `int/float` и диапазон
  0..1, строк-чисел нет — 400 с RU-detail.
- **Degradation**: всё отфильтровано → 200, пустой kb_block, ответ без
  контекста (не «Ошибка:»); реранкер down при `min_score > 0` → фильтр
  работает на stage1-счётах (relative-режим), без crash.
- E2E на порту **8107** (день 22 — 8106, 8105 — день 21).

## What Changes

- **`studio/backend/kb.py`**: `DEFAULT_SETTINGS` — `+ "min_score": 0.0`
  (merge over defaults → старые `settings.json` backward-совместимы);
  `update_settings` — валидация min_score (bool-гвард первым, затем
  тип/диапазон; RU-detail в существующем стиле; сохранение как float);
  `search_rag` — новый параметр `min_score: float = 0.0`: при `> 0` после
  stage1 (+rerank) и до `[:top_k]` отсечь (reranked → `rerank_score >=
  min_score`; не reranked → `score >= min_score * best`); ответ —
  аддитивные поля `filtered: bool` и `dropped: int`. RRF/BM25/embedder/
  chunking/APIReranker-internals не меняются.
- **`studio/backend/agent.py`**: новый метод `rewrite_query(question) ->
  (str, bool)` (один `_task_llm_call`-вызов T=0, max_tokens=200, RU-system-
  промпт «одним предложением, ключевыми словами»; возврат `(rewritten,
  True)` только при непустом и не-идентичном (case-insensitive) ответе;
  исключение/пустота/идентичность → `(question, False)`, log warning);
  `rag_compare` — расширение с 2 до 4 арм: plain/rag без изменений,
  `rag+filter` — retrieval с `min_score` (body-override или settings),
  `rag+rewrite` — retrieval на `rewritten`, ответ на оригинальном вопросе;
  аддитивные поля ответа (см. Why). `ask_stream` **не трогается**
  (guardrail-тест: rewrite из неё не вызывается).
- **`studio/backend/main.py`**: `POST /api/rag/compare` — тело
  `{question: str, min_score?: float}`: optional `min_score`, та же
  валидация что в `update_settings` (400 RU), default =
  `settings["min_score"]`, передача в `rag_compare`; `GET /api/kb/settings`
  возвращает `min_score` (merge); `GET /api/kb/search` передаёт
  `min_score` из settings в `search_rag` (фильтр действует на всех 3
  потребителя консистентно).
- **Тесты** (TDD, паттерн day21/22): `test_kb.py` — валидация (0.5→200,
  1.5/-0.1/"0.5"/true→400, 0 и 1→200), default GET 0.0, fake-reranker
  [0.99, 0.5, 0.1]: 0.6→ровно 1 результат + dropped=2, 0.999→0, 0.0→
  снимок-сравнение идентичности, relative-режим (лучший проходит
  0.9999), best==0 guard, `>=` при 1.0; `test_kb_api.py` — pass-through
  min_score в `/api/kb/search` и settings; `test_rag_compare.py` — 4 армы
  (старые поля intact + новые), body-override фильтрует только
  filter-арму, 400 на `true`/`"0.5"`/`1.5`, всё отфильтровано → 200 +
  непустой ответ, fake-LLM rewrite scripted (rewritten_query +
  `chunks_rag_rewrite != chunks_rag`) и raise (fallback, арма не
  «Ошибка:»), регрессия day22-формы (byte-совместимый payload);
  `test_agent.py` — rewrite_query (success/empty/identical/raise) +
  guardrail (не в ask_stream).
- **Фронтенд**: `api.ts` — `KbSettings.min_score: number`, аддитивные
  опциональные поля compare-ответа, `apiRagCompare` body `{question,
  min_score?}`; `KbTab.tsx` — числовое поле «Порог отсечения (0 = off)»
  0..1 step 0.05 (паттерн patchSettings), 4 панели сравнения (вместо 2)
  с чипом «фильтр ≥ X» у filter-панели и чипом rewritten_query у
  rewrite-панели, чип «фильтр ≥ X» в секции поиска; undefined-safe для
  старого бэкенда; Vitest-тесты.
- **`scripts/e2e_day23.py`** (новый, stdlib, порт **8107**, паттерн
  e2e_day22): Part A — офлайн MUST PASS (net cut `_NO_NET`, TestClient +
  fake-LLM + fake-reranker: settings-валидация, фильтр, relative-режим,
  4 армы, rewrite fallback, всё-отфильтровано, регрессия day22-формы);
  Part B — live best-effort (GPustack down → SKIP). Cleanup всегда; exit
  0 для PASS/SKIP, 1 для FAIL.
- **`scripts/compare_day23.py`** (новый, stdlib, паттерн compare_day22):
  10 контрольных вопросов (переиспользование `control_questions.json`
  дня 22 + 2 «отвлекающих» с очевидной нерелевантностью) × 4 режима
  через `POST /api/rag/compare`; факт-чек + LLM-judge (T=0, strict JSON,
  soft); отчёт `.omo/evidence/day23-compare/` (report.md: таблица
  вопрос×режим, win-rate, вывод); отчёт всегда (try/finally), SKIP-метка
  при недоступности GPustack.
- **Доки**: README (строка таблицы + секция «День 23»), RELEASE (секция),
  `docs/superpowers/specs/2026-10-03-day23-rerank-filter-design.md` +
  plan, демо-видео (скилл `studio-demo-video`, desktop, не в репо).

## Capabilities

### New Capabilities

- `rag-filter-rewrite`: реранкинг и фильтрация (день 23) — порог
  отсечения `min_score` (0..1, default 0.0 = off, `>=`-семантика,
  после stage1/rerank до top_k; reranker=api → `rerank_score`,
  reranker=off → relative `score/best`) в settings и в
  `kb.search_rag` (все 3 потребителя), query rewrite (один LLM-вызов
  T=0/max_tokens=200, compare-only, graceful fallback на оригинал) и
  4-режимное сравнение в `POST /api/rag/compare` (plain | rag |
  rag+filter | rag+rewrite; plain/rag никогда не фильтруют; body-
  override `min_score`; аддитивный контракт ответа); валидация 400 RU;
  e2e-гибрид (порт 8107, Part A MUST PASS офлайн).

### Modified Capabilities

- `rag-compare` (день 22): `POST /api/rag/compare` расширяется с 2 до
  4 арм и body-override `min_score`; имена/типы существующих полей НЕ
  меняются (только аддитивные), `T=0`/`max_tokens=1024`/bare-промпт
  существующих рук не меняются, новые ручки (filter/rewrite) идут тем же
  non-stream `_task_llm_call`; `e2e_day22.py` остаётся green.
- `knowledge-base` (день 21): в settings добавляется ключ `min_score`
  (merge over defaults → backward-совместимо), `search_rag` получает
  параметр `min_score` + аддитивные поля ответа `filtered`/`dropped`;
  `chunking`, `embedder`, RRF/BM25, `APIReranker`, `/api/kb/index`,
  upload/wipe не меняются; при `min_score = 0.0` поведение
  `search_rag` байт-в-байт идентично дневному 21.

## Impact

- **Код**: правки `studio/backend/kb.py` (settings/валидация/фильтр),
  `agent.py` (`rewrite_query` + расширение `rag_compare`), `main.py`
  (compare body + settings/search pass-through); новые/расширенные
  тесты `test_kb.py`, `test_kb_api.py`, `test_rag_compare.py`,
  `test_agent.py`; `studio/frontend/src/{api.ts,
  components/KbTab.tsx}` + `tests/kb-tab.test.tsx`; новые
  `scripts/e2e_day23.py`, `scripts/compare_day23.py`.
- **Данные**: runtime `data/kb/settings.json` (gitignored) получает
  `min_score` при первом обращении (merge); runtime-отчёт
  `.omo/evidence/day23-compare/` (gitignored); committed — только
  openspec-изменение и доки. Никаких новых `.env`-ключей.
- **Зависимости**: только stdlib; без новых pip/npm-зависимостей,
  моделей и MCP-серверов.
- **Совместимость**: контракт `/api/rag/compare` — только аддитивные
  поля (e2e_day22 Part A 7/7 green); `/api/kb/*` не ломается (`min_score`
  в settings — merge, старые файлы без ключа читаются с дефолтом 0.0);
  `min_score=0.0` → `search_rag` байт-в-байт как день 21; дни 11–22 не
  регрессируют; `ask_stream`/чат-пайплайн не меняется (rewrite —
  compare-only, guardrail-тест).
