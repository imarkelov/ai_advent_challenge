# День 23: Реранкинг и фильтрация — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Поверх БЗ дня 21 и compare дня 22 добавить: порог отсечения
нерелевантных результатов `min_score` (0..1, дефолт 0.0 = off) — после
stage1-отбора (и rerank), до top_k-среза; query rewrite (один LLM-вызов,
compare-only, graceful fallback); 4-режимное сравнение качества в одном
`POST /api/rag/compare` (plain / rag / rag+filter / rag+rewrite — 5
LLM-вызовов) с live-отчётом (факт-чек + LLM-judge).

**Architecture:** Фильтр — одна точка в `kb.search_rag` (после
stage1/rerank, до `[:top_k]`; absolute `rerank_score >= min_score` при
работающем реранкере, relative `score >= min_score * best` без него;
guard `best == 0` → []; поля `filtered`/`dropped` — только при > 0;
min_score = 0 → байт-в-байт как до дня). `agent.rag_compare` — 4 армы,
аддитивный контракт (13 полей = 5 дня 22 + 8 новых), plain/rag никогда
не фильтруют; `rewrite_query` — один вызов T=0/mt=200, compare-only,
фолбэк `(question, False)` без «Ошибка:». Валидация (settings + body):
bool первым → 400, `int/float` 0..1, строки → 400 RU. UI — поле
«Порог отсечения» в KbTab + 4 панели сравнения + чипы «фильтр ≥ X» и
rewrite-чип. E2E — порт 8107, каркас e2e_day22 (Part A офлайн MUST
PASS, Part B live best-effort). Скрипт сравнения — `compare_day23.py`
(12 вопросов × 4 режима, body-override min_score=0.6).

Полная версия плана (source of truth):
`.omo/plans/day23-rerank-filter.md`. Спека:
`docs/superpowers/specs/2026-10-03-day23-rerank-filter-design.md`.

## Global Constraints

- stdlib-бэкенд + httpx (как дни 21–22); без новых зависимостей.
- Аддитивность — главный закон дня: поля дня 22 не меняются, новые
  добавляются; `e2e_day22.py` остаётся green (допустимое
  документированное отклонение — синхронизация под 5-вызовный контракт:
  A2 `len(captured)==5`, A6 — индексы арм 0/1/2/4 + rewrite(3),
  `COMPARE_TIMEOUT` 330→825; чеки старых полей не тронуты).
- min_score = 0.0 (дефолт) — нулевая регрессия: поведение байт-в-байт
  как до дня (без `filtered`/`dropped`).
- TDD: тест сначала, потом код; все тесты офлайн (tmp_path +
  MockTransport, без сети).
- RU-сообщения об ошибках в стиле дня 21 («min_score должен быть числом
  от 0 до 1»).
- Все файлы UTF-8; в репозиторий не попадают секреты и runtime-данные
  (`data/kb/` gitignored).
- Демо-видео — вне репозитория (паттерн дня 22).

### Константы (канонический список)

| Константа | Значение |
| --- | --- |
| `min_score` | 0..1 float, дефолт `0.0` (off), семантика `>=` |
| Absolute-порог | `rerank_score >= min_score` (reranker=api, rerank сработал) |
| Relative-порог | `score >= min_score * best`, `best = max(stage1 score)`; `best == 0` → `[]` |
| Позиция фильтра | после stage1 (и сортировки реранка), до среза `[:top_k]` |
| Поля фильтра | `filtered: true`, `dropped: int` — только при `min_score > 0` |
| `rewrite_query` | 1 вызов, T=0, max_tokens=200, `(str, bool)`; фолбэк `(question, False)` |
| Армы compare | plain / rag (никогда не фильтруют) / rag+filter / rag+rewrite (ответ на оригинальном вопросе) |
| Вызовы compare | 5 × `_task_llm_call`, non-stream, T=0; руки mt=1024, rewrite mt=200; журнал `requests.json` не пишется |
| Порядок вызовов | plain → rag → filter → rewrite(3) → rewrite-арма (captured[0..1] = plain/rag) |
| Валидация | `isinstance(v, bool)` → 400 первым; `int/float` 0..1; строки/None → 400 RU |
| E2E-порт | **8107** (8100 studio, 8101–8104 дни 17–20, 8105 day21, 8106 day22) |
| Fake-reranker scores | `[0.99, 0.5, 0.1]` (фикстуры `test_kb.py`) |
| Compare-прогон | 12 вопросов (10 контрольных дня 22 + 2 отвлекающих) × 4 режима, body-override `min_score=0.6` |
| Корпус отчёта | 6 файлов, 1666 чанков (structural, api-эмбеддер, dim 4096) |

### Файлы (сводка)

| Файл | Изменение |
| --- | --- |
| `studio/backend/kb.py` | `search_rag(..., min_score: float = 0.0)` + фильтр-блок после stage1/rerank; `DEFAULT_SETTINGS["min_score"] = 0.0`; валидация в `update_settings` |
| `studio/backend/agent.py` | `rewrite_query()`; `rag_compare(question, min_score=None)` — 4 армы, 5 вызовов |
| `studio/backend/main.py` | `min_score` в GET/POST `/api/kb/settings` (по умолчанию уже в settings), body-override в `POST /api/rag/compare` (400-валидация) |
| `studio/backend/tests/test_kb.py`, `test_rag_compare.py`, `test_main.py` | +31 тест |
| `studio/frontend/src/components/KbTab.tsx` | поле «Порог отсечения (0 = off)», 4 панели сравнения, чипы «фильтр ≥ X», rewrite-чип с копированием |
| `studio/frontend/src/api.ts` | типы: `min_score` в KbSettings/CompareBody, новые compare-поля (опциональные) |
| `studio/frontend/tests/*` | +8 тестов |
| `scripts/e2e_day23.py` | новый, порт 8107, Part A 7 шагов + Part B live |
| `scripts/e2e_day22.py` | синхронизация под 5-вызовный контракт (см. выше) |
| `scripts/compare_day23.py` | новый, stdlib, 12 × 4 → `.omo/evidence/day23-compare/` |
| `README.md`, `RELEASE.md` | секция дня 23 |
| `docs/superpowers/specs/`, `plans/` | настоящие доки |

### Task 1: Обвязка — openspec change + design/plan доки

- [ ] Создать `openspec/changes/day23-rerank-filter/` (proposal, design
      с 8 решениями, tasks).
- [ ] `.omo/plans/day23-rerank-filter.md` — полный план-источник.
- [ ] Зеркала: spec + план в `docs/superpowers/` (эти файлы).
- [ ] Коммит: `chore(day23): openspec change + design + plan`.

### Task 2: Порог `min_score` в `search_rag` (TDD)

- [ ] Тест: settings GET/POST `min_score` (дефолт 0.0, merge over
      defaults — старый settings.json backward-совместим).
- [ ] Тест: валидация — `true`/`"0.5"`/1.5/-0.1 → 400 RU; 0/0.5/1 → 200.
- [ ] Тест: absolute — fake-reranker `[0.99, 0.5, 0.1]`, `min_score=0.6`
      → ровно 1 результат; `filtered`/`dropped` только при > 0.
- [ ] Тест: relative — reranker off, `score >= min_score * best`,
      порядок сохранён; `best == 0` → `[]`.
- [ ] Тест: `min_score=0.0` — ответ байт-в-байт как до дня (снимок
      сравнением списков, без `filtered`/`dropped`).
- [ ] Тест: `min_score=1.0` — лидер остаётся (`>=`, не `>`).
- [ ] Реализация: фильтр-блок в `search_rag` после stage1/rerank, до
      `[:top_k]`; `out["filtered"]`/`out["dropped"]`.
- [ ] Коммит: `feat(kb): min_score threshold filter (settings + search)`.

### Task 3: Гард регрессии `min_score=0` (fix)

- [ ] Тест: при `min_score=0.0` `search_rag` возвращает ровно те же
      объекты/порядок, что и до дня (byte-identical контракт).
- [ ] Фикс: фильтр-блок выполняется только при `min_score > 0`.
- [ ] Коммит: `fix(kb): min_score=0 byte-identical guard`.

### Task 4: Query rewrite (TDD)

- [ ] Тест: `rewrite_query` — валидный перефраз → `(rewritten, True)`;
      пустой/идентичный (case-insensitive) → `(question, False)`;
      исключение LLM → `(question, False)` + warning, без «Ошибка:».
- [ ] Тест: guardrail — `rewrite_query` не вызывается из `ask_stream`
      (monkeypatch-spy).
- [ ] Реализация: RU-промпт «одним предложением, ключевыми словами,
      без приветствий», T=0, max_tokens=200, `_task_llm_call`.
- [ ] Коммит: `feat(agent): rewrite_query (compare-only, graceful fallback)`.

### Task 5: UI — порог + 4 панели (TDD)

- [ ] Тесты фронтенда: поле «Порог отсечения (0 = off)» (0..1, step
      0.05, коммит на blur через patchSettings); 4 панели сравнения;
      чип «фильтр ≥ X»; rewrite-чип (копирование, виден при
      `rewrite_applied === true`); undefined-safe (опциональные поля).
- [ ] Тесты API-типов: `min_score` в KbSettings, новые compare-поля
      опциональные.
- [ ] Коммит: `feat(ui): min_score field + 4-arm compare panels`.

### Task 6: Compare 4 арм (TDD)

- [ ] Тест: `rag_compare(question, min_score=None)` → 13 ключей;
      eff_min = body-override ?? settings; plain/rag НИКОГДА не
      фильтруют; rag+filter — retrieval с `min_score`; rag+rewrite —
      retrieval на перефразе, ответ на оригинальном вопросе; 5
      LLM-вызовов, порядок plain → rag → filter → rewrite(3) → арма.
- [ ] Тест: сбой руки — «Ошибка: …» в этой руке, статус 200.
- [ ] Тест: всё-отфильтровано — `chunks_rag_filter == []`,
      `answer_rag_filter` непустой.
- [ ] Реализация: `main.py` body-override `min_score` (bool-гвард
      первым → 400 RU) + `agent.rag_compare` 4 армы.
- [ ] Коммит: `feat(agent): rag_compare 4 arms (plain|rag|filter|rewrite)`.

### Task 7: E2E — `scripts/e2e_day23.py` (порт 8107)

- [ ] Part A (офлайн, MUST PASS, 7 шагов): A1 settings+валидация;
      A2 absolute-фильтр; A3 relative-режим; A4 compare 4 армы (13
      ключей, 5 вызовов, скриптованный rewrite); A5 сбой rewrite →
      фолбэк; A6 0.999 → пусто + непустой ответ; A7 day22-совместимость.
- [ ] Part B (live, best-effort): B1 пересборка корпуса, B2 настройки,
      B3 live compare `min_score=0.5`, B4 пасхалка (WARNING).
- [ ] Синхронизация `scripts22.py` (см. Global Constraints).
- [ ] Прогон: Part A 7/7 PASS; Part B live 11 PASS / 0 FAIL / 1
      WARNING; регрессия `e2e_day22.py` 12/12 PASS.
- [ ] Коммит: `test(e2e): day23 e2e (port 8107) + day22 sync`.

### Task 8: Скрипт сравнения `scripts/compare_day23.py` + отчёт

- [ ] Live-прогон: 12 вопросов (10 контроля дня 22 + 2 отвлекающих) ×
      4 режима, body-override `min_score=0.6`.
- [ ] Отчёт `.omo/evidence/day23-compare/` (compare.json + report.md):
      факт-чек + LLM-judge. Факт: judge tie=5 / rag 3 / filter 2 /
      plain 2; rewrite_applied 12/12; avg score 6.0/9.0/9.0/8.8;
      sources_ok 7/10.
- [ ] Коммит: `feat(scripts): compare_day23 + evidence report`.

### Task 9: Документация — README + RELEASE + зеркала доков

- [ ] README.md: строка в таблице дней + секция «День 23: Реранкинг и
      фильтрация» (Что это / Архитектура / API / UI / E2E / Проверка
      задания / Статус: backend 579, frontend 296, e2e A 7/7 + B
      11/0/1; сноска об отклонении e2e_day22).
- [ ] RELEASE.md: секция дня 23 сверху + `---`.
- [ ] `docs/superpowers/specs/2026-10-03-day23-rerank-filter-design.md`
      + `docs/superpowers/plans/2026-10-03-day23-rerank-filter.md`.
- [ ] Чеки: grep «День 23» README ≥ 2; grep «day23» RELEASE; файлы
      доков на месте; числа тестов совпадают с `.omo/evidence`.
- [ ] Коммит: `docs(day23): README + RELEASE + spec/plan`.

### Task 10: Демо-видео (скилл `studio-demo-video`)

- [ ] Свободный порт + `min_score=0.5` в settings + индексированный
      корпус.
- [ ] Playwright-запись 4 моментов: поле порога; поиск с чипом
      «фильтр ≥ X» и отсечёнными результатами; 4-панельное сравнение;
      rewrite-чип.
- [ ] Webm → MP4 (паттерн дня 22), **вне репозитория**.

### Финальная проверка (после всех tasks)

- [ ] Бэкенд: `python -m pytest -q` — **579 PASS**.
- [ ] Фронтенд: `npm test` — **296 PASS**; `npx tsc -b` clean;
      `npm run build` clean.
- [ ] `scripts/e2e_day23.py` — Part A 7/7, Part B 11/0/1;
      `scripts/e2e_day22.py` — 12/12.
- [ ] Отчёт сравнения на месте (`.omo/evidence/day23-compare/`).
- [ ] Доки: README / RELEASE / spec / plan — консистентны по числам.
- [ ] Коммит-цепочка чистая, видео вне репозитория.
