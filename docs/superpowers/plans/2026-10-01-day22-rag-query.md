# День 22: Первый RAG-запрос — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Студия отвечает на вопрос двумя способами (с RAG и без) через ОДИН эндпоинт `POST /api/rag/compare`; качество на 10 контрольных вопросах (каждый с `expect_facts` + `expected_sources`) сравнивается скриптом с отчётом (JSON+MD: детерминированный факт-чек + источники — hard, LLM-judge — soft) и видно в UI (секция «Сравнение RAG» в KbTab).

**Architecture:** `agent.py` — новый метод `rag_compare(question)` (retrieval `kb.search_rag` с `rag_recall`/`rag_top_k`/`reranker` из settings, флаг `rag` игнорируется; `kb_block` через `_render_kb_block`; 2×`_task_llm_call` T=0/max_tokens=1024, bare `config.system_prompt` — у RAG-руки + `kb_block`; ошибки рук изолированы). `main.py` — новый маршрут (400/404 RU-detail). Корпус — 3 русских классика (`scripts/fetch_books.py`, stdlib urllib, fallback chain + committed seed) + `egg_book.txt`. 10 контрольных вопросов — committed фикстура. `scripts/compare_day22.py` — live-ран :8106 + отчёт `.omo/evidence/day22-rag-compare/` (всегда, try/finally, settings-эхо, честный итог). `scripts/e2e_day22.py` — Part A offline MUST PASS (только committed фикстуры) + Part B live :8106 (SKIP-абли). UI — секция в KbTab (`apiRagCompare`). `kb.py`/`ask_stream`/`GOLD_QUERIES` не трогаются.

**Полный план дня (source of truth):** `.omo/plans/day22-rag-query.md`
(волны параллелизма, dependency matrix, QA-сценарии с evidence,
commit strategy, final verification wave F1–F4). Документ — выжимка
для исполнителей.

**Tech Stack:** Python stdlib (`urllib` в fetch_books; compare/e2e — stdlib, httpx только если уже в requirements — без новых pip-зависимостей); FastAPI/TestClient (роуты); pytest (бэкенд); React 19 + Vitest (UI, без новых npm-зависимостей).

**Spec:** `docs/superpowers/specs/2026-10-01-day22-rag-query-design.md`

## Global Constraints

- Ветка: `day22-rag-query` (от `day21-doc-indexing`).
- Все пользовательские тексты и ошибки — на русском (RU-detail).
- Runtime-данные — `data/kb/` (gitignored): все тестовые фикстуры — committed в `studio/backend/tests/fixtures/`.
- Нет новых `.env`-ключей, LLM-моделей, MCP-серверов, pip/npm-зависимостей (только stdlib).
- Тесты бэкенда — офлайн (tmp_path + TestClient + fake-LLM с захватом payload, без сети).
- E2E порт: **8106** (8105 — день 21); Part A — MUST PASS без сети (net cut `_NO_NET`, только committed фикстуры).
- Запуск тестов: из `studio/backend` — `python -m pytest -q`; фронтенд — `npx tsc -b && npm test && npm run build`.

### Константы (канонический список)

| Константа | Значение |
|---|---|
| `temperature` (оба LLM-вызова compare) | 0 |
| `max_tokens` (оба LLM-вызова compare) | 1024 |
| System-промпт | bare `config.system_prompt` (без memory/profile/invariants); RAG-рука — + `kb_block` |
| Settings для compare | `rag_recall: 50`, `rag_top_k: 3`, `embedder: api`, `reranker: api` (флаг `rag` — игнорируется) |
| Контрольных вопросов | ровно 10 (Q1–Q7 — классики, Q8–Q10 — `egg_book.txt`) |
| E2E-порт | 8106 |
| Отчёт | `.omo/evidence/day22-rag-compare/{compare.json, report.md}` (всегда, try/finally) |
| Корпус | `pushkin_oneygin.txt`, `chekhov_cherry_orchard.txt`, `tolstoy_war_peace.txt`; fallback: `gogol_dead_souls.txt`, `chekhov_lady_with_dog.txt`; seed: `tests/fixtures/seed_corpus/*.txt` |

### Файлы (сводка)

- Create: `studio/backend/tests/test_rag_compare.py`, `studio/backend/tests/test_fixtures.py`, `studio/backend/tests/test_fetch_books.py`, `studio/backend/tests/fixtures/control_questions.json`, `studio/backend/tests/fixtures/egg_book.txt`, `studio/backend/tests/fixtures/seed_corpus/*.txt`, `scripts/fetch_books.py`, `scripts/compare_day22.py`, `scripts/e2e_day22.py`
- Modify: `studio/backend/agent.py` (только новый метод `rag_compare`), `studio/backend/main.py` (только новый маршрут `POST /api/rag/compare`), `studio/frontend/src/api.ts` (`RagCompareResult` + `apiRagCompare`), `studio/frontend/src/components/KbTab.tsx` (секция «Сравнение RAG»), `studio/frontend/src/styles.css`, vitest-тесты секции
- Docs: `README.md` (строка таблицы + секция «День 22»), `RELEASE.md` (секция сверху), `openspec/changes/day22-rag-query/`, `docs/superpowers/plans/2026-10-01-day22-rag-query.md` (этот план)
- Video: `day22_demo.mp4` (desktop, НЕ в репо; скилл `studio-demo-video`)

---

### Task 1: Корпус — `fetch_books.py` + committed seed

**Files:**
- Create: `scripts/fetch_books.py`, `studio/backend/tests/fixtures/seed_corpus/*.txt` (2 текста Чехова, публичный домен), `studio/backend/tests/test_fetch_books.py`

**Interfaces:**
- Produces: pinned-список (Пушкин «Евгений Онегин» → `pushkin_oneygin.txt`, Чехов «Вишнёвый сад» → `chekhov_cherry_orchard.txt`, Толстой «Война и мир» → `tolstoy_war_peace.txt`; literature.lib.ru → fallback Wikisource ru), fallback chain (Гоголь «Мёртвые души» / Чехов «Дамы с собачкой»), seed-copy при полном провале; CLI `--out data/kb/uploads`, `--list`; exit 0 + JSON-сводка.

- [ ] **Step 1: Тесты** (patched urlopen, tmp_path): URL-список, fallback chain, size sanity > 50KB (seed except), seed-copy.
- [ ] **Step 2: Implement** (stdlib urllib: UTF-8 → cp1251, атомарная запись, exit 0 даже при провале).
- [ ] **Step 3: Прогон live** (`python scripts/fetch_books.py`) — книги в `data/kb/uploads/`, валидный UTF-8, без mojibake.
- [ ] **Step 4: Commit** (`feat(day22): book fetcher + committed seed corpus`).

---

### Task 2: Фикстуры — 10 контрольных вопросов + egg-книга

**Files:**
- Create: `studio/backend/tests/fixtures/control_questions.json`, `studio/backend/tests/fixtures/egg_book.txt`, `studio/backend/tests/test_fixtures.py`

**Interfaces:**
- Produces: ровно 10 `{id: 1..10, question, expect_facts[], expected_sources[]}` (Q1–Q7 — классики: 2 «Онегин», 2 «Вишнёвый сад», 1 «Война и мир», 2 перекрёстных; Q8–Q10 — `egg_book.txt`: IPhone 17Promax, ксилофон, имя героя); `expect_facts` — конкретные факты текста (вычитываются ПОСЛЕ скачивания книг; книга не скачалась → вопросы по fallback); `expected_sources` — точные имена загружаемых файлов; `egg_book.txt` — ~2–5KB, герой + ксилофон + IPhone 17Promax + 3–5 уникальных фактов.

- [ ] **Step 1: `egg_book.txt`** (детерминированная «книга», паттерн e2e_day21:716).
- [ ] **Step 2: `control_questions.json`** (факты — из реально скачанного текста; схема: id 1..10, оба массива non-empty, источники из белого списка).
- [ ] **Step 3: `test_fixtures.py`** (схема-тесты) — `python -m pytest studio/backend/tests/test_fixtures.py -q` → PASS.
- [ ] **Step 4: Commit** (`feat(day22): control questions + egg book fixtures`).

---

### Task 3: Обвязка — openspec change + design/plan доки

**Files:**
- Create: `openspec/changes/day22-rag-query/` (proposal.md, design.md, tasks.md, specs/rag-compare/spec.md, .openspec.yaml — структура `day21-knowledge-base` 1:1), `docs/superpowers/specs/2026-10-01-day22-rag-query-design.md`, `docs/superpowers/plans/2026-10-01-day22-rag-query.md` (этот план)

- [ ] **Step 1: openspec change** (why/what-impact, 9 зафиксированных решений в design, свой чек-лист в tasks, spec с SHALL/Scenarios).
- [ ] **Step 2: design-дока** (design.md + контекст дня: что день 22, связь с KB дня 21, якоря, риски).
- [ ] **Step 3: plan-дока** (этот файл: выжимка `.omo/plans/day22-rag-query.md`).
- [ ] **Step 4: Commit** (`docs(day22): openspec change + design/plan docs`).

---

### Task 4: Эндпоинт `POST /api/rag/compare` (TDD)

**Files:**
- Create: `studio/backend/tests/test_rag_compare.py`
- Modify: `studio/backend/agent.py`, `studio/backend/main.py`

**Interfaces:**
- Consumes: `kb.search_rag`, `agent._render_kb_block`/`_kb_context`/`_task_llm_call`, `create_app(agent, kb)`.
- Produces: `StudioAgent.rag_compare(question) -> {answer_plain, answer_rag, kb_block, chunks, rag_context}` (T=0, max_tokens=1024, bare prompt; `rag`-флаг не consulted; recall/top_k/reranker из settings; try/except → `error`); маршрут `POST /api/rag/compare` (400 пустой вопрос, 404 без индекса, RU-detail).

- [ ] **Step 1: Красные тесты** (9, TestClient + fake-LLM с захватом payload, паттерн `test_kb_api.py`): shape 200 (оба ответа non-empty, kb_block, chunks ≥1, rag_context), plain без «База знаний», rag с «База знаний» + текст фикстуры, T=0/max_tokens=1024 в обоих payload, bare prompt (нет «Профиль»/«Память»/«Инварианты»), `settings["rag"]=false` → обе руки, 400 пустой вопрос, 404 без индекса, изоляция ошибки руки (200 + `error`, другая рука цела).
- [ ] **Step 2: Run, verify they fail.**
- [ ] **Step 3: Implement** (`rag_compare` + маршрут; правки `ask_stream`/`kb.py` запрещены).
- [ ] **Step 4: Run, verify they pass** (9/9 + весь backend, 0 регрессий).
- [ ] **Step 5: Commit** (`feat(day22): POST /api/rag/compare — RAG vs no-RAG answers in one call`).

---

### Task 5: UI — секция «Сравнение RAG» в KbTab

**Files:**
- Modify: `studio/frontend/src/api.ts`, `studio/frontend/src/components/KbTab.tsx`, `studio/frontend/src/styles.css`
- Test: `studio/frontend/tests/` (vitest-тесты секции)

**Interfaces:**
- Consumes: `apiRagCompare` (Task 4 контракт).
- Produces: секция «Сравнение RAG» (НЕ новый таб; `ContextPanel` TABS не трогать): textarea 1–3 строки + «Сравнить» → spinner → две панели рядом «Без RAG» / «С RAG» (monospace, pre-wrap; narrow — колонкой) + чанки (`file · section`, score/rerank_score) + `kb_block` в `<details>`; 400/404/500 → RU-сообщение из detail.

- [ ] **Step 1: Красные тесты** (mock `apiRagCompare`: рендер секции, input → click → две панели + чанки; mock 404 → error-текст, без crash).
- [ ] **Step 2: Run, verify they fail.**
- [ ] **Step 3: Implement** (`RagCompareResult` + `apiRagCompare`, секция, стили).
- [ ] **Step 4: Run, verify they pass** (фронтенд: 263 + новые, замеры на релизе) + `tsc -b` + `npm run build` clean.
- [ ] **Step 5: Commit** (`feat(day22): UI «Сравнение RAG» in KbTab (side-by-side answers)`).

---

### Task 6: Скрипт сравнения `scripts/compare_day22.py` + отчёт

**Files:**
- Create: `scripts/compare_day22.py` (stdlib)

- [ ] **Step 1: Infra (HARD)** — порт 8106 (busy → wait 10 мин → SKIP); uvicorn subprocess (логи → `%TEMP%\opencode\compare_day22_server.log`); probe GPustack → down: SKIP, exit 0; wipe → `fetch_books.py` → upload книг + `egg_book.txt` → index `{strategy: structural, embedder: api}` → poll build-status (timeout 30 мин) → settings ЯВЛЬНО `{embedder: api, reranker: api, rag_recall: 50, rag_top_k: 3}`.
- [ ] **Step 2: Ран (HARD: завершённость)** — 10 вопросов; источник не в корпусе → `skipped`, не фейл; ошибка вопроса → `error` в записи, продолжаем.
- [ ] **Step 3: Оценка (SOFT)** — факт-чек (нормализованная подстрока, `{rag, plain}`), `sources_ok` (`chunks[].file ∩ expected_sources ≠ ∅`), LLM-judge (T=0, strict JSON `{score, verdict, reason}`, паттерн `benchmark.py`; сбой → `judge: null`; никогда не фейлит).
- [ ] **Step 4: Отчёт (ВСЕГДА, try/finally)** — `.omo/evidence/day22-rag-compare/compare.json` (settings_echo, 10 записей, summary) + `report.md` (settings-таблица, таблица вопросов, честный итог: RAG может проиграть).
- [ ] **Step 5: Прогон** (GPustack up: exit 0, отчёт 10 записей, порт свободен после; GPustack down: SKIP, exit 0; cleanup uvicorn всегда).
- [ ] **Step 6: Commit** (`feat(day22): compare_day22.py — 10-question RAG/no-RAG report`).

---

### Task 7: E2E — `scripts/e2e_day22.py` (Part A offline + Part B :8106)

**Files:**
- Create: `scripts/e2e_day22.py` (stdlib, лог `[e2e-day22]`, эталон `e2e_day21.py`)

- [ ] **Step 1: Part A (MUST PASS, net cut `_NO_NET`, TestClient + fake LLM, ТОЛЬКО committed фикстуры: upload `egg_book.txt` → index HashEmbedder)** — C1 shape (все поля), C2 KB-блок есть/нет в payload, C3 400/404, C4 `rag=false` → обе руки, C5 схема `control_questions.json`, C6 T=0/max_tokens=1024, C7 bare prompt.
- [ ] **Step 2: Part B (live, uvicorn :8106, PASS/SKIP)** — probe GPustack down / port-busy → SKIP; B1 corpus rebuild (wipe → egg_book + 1–2 книги → index, poll), B2 settings, B3 один live-запрос (оба ответа non-empty, chunks non-empty), B4 easter-egg (IPhone 17Promax) — WARNING, не FAIL.
- [ ] **Step 3: Прогон** (net-cut: Part A все PASS, exit 0; live: PASS/SKIP, exit 0; порт 8106 свободен после, no orphan-процессы).
- [ ] **Step 4: Commit** (`test(day22): e2e_day22.py — Part A offline + Part B :8106`).

---

### Task 8: Документация — README + RELEASE

**Files:**
- Modify: `README.md` (строка «День 22» в таблице + секция по структуре day21), `RELEASE.md` (новая секция СВЕРХУ: файлы, API, Проверка задания, Безопасность)

- [ ] **Step 1: Фаза 1** (параллельно видео): README + RELEASE БЕЗ строк про демо-видео/log-цитаты (placeholder).
- [ ] **Step 2: Фаза 2** (после видео): в RELEASE — путь/размер/длительность mp4 + log-цитаты.
- [ ] **Step 3: Числа РЕАЛЬНЫЕ** (замерить на релизе: pytest backend, vitest frontend, e2e_day22, compare-отчёт — summary); day21-секции не менять (только добавления).
- [ ] **Step 4: Commit** (`docs(day22): README + RELEASE — RAG query day`).

---

### Task 9: Демо-видео (скилл `studio-demo-video`)

**Files:**
- Artifact: `day22_demo.mp4` (desktop «AI Advent Challenge - видео», НЕ в репо)

- [ ] **Step 1: Pre** — pytest backend green, `npm run build` свежий (dist), порт 8001 свободен, GPustack probe.
- [ ] **Step 2: Сценарий** — вкладка «База знаний» → секция «Сравнение RAG» → вопрос про корпус (egg-факт или вопрос про «Вишнёвый сад») → «Сравнить» → два ответа рядом + чанки.
- [ ] **Step 3: Log-evidence** — цитаты из server log (LLM-вызовы, retrieval) → верbatim в отчёт → Task 8 (RELEASE).
- [ ] **Step 4: Commit: НЕТ** (видео не в репо; log-цитаты идут в commit Task 8).

---

### Финальная проверка (после всех tasks)

- `cd studio/backend && python -m pytest -q` — 505 + новые (замерить на релизе) PASS, 0 FAIL
- `cd studio/frontend && npx tsc -b && npm test && npm run build` — 263+ (замерить на релизе) PASS, clean
- `python scripts/e2e_day22.py` — Part A: 0 FAIL; Part B: PASS|SKIP; exit 0
- `python scripts/compare_day22.py` — exit 0; `.omo/evidence/day22-rag-compare/{compare.json,report.md}` — 10 записей, settings-эхо, честный итог
- Final verification wave F1–F4 (plan-аудит, code quality, manual QA, scope fidelity) — см. `.omo/plans/day22-rag-query.md`; отметки в `openspec/changes/day22-rag-query/tasks.md` (паттерн `/opsx:apply`); архив — только по решению пользователя (прецедент дня 21).
