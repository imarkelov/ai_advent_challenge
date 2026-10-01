## Purpose

Capability «rag-compare» — первый RAG-запрос (день 22): один эндпоинт
`POST /api/rag/compare` отвечает на вопрос двумя способами (plain /
RAG — два non-stream LLM-вызова с T=0, max_tokens=1024 и bare
симметричным системным промптом) и возвращает оба ответа с
`kb_block`, чанками и `rag_context` в одном JSON; эндпоинт игнорирует
тумблер `rag` из settings (явное сравнение), ошибки рук изолированы.
Качество на 10 контрольных вопросах (`expect_facts` +
`expected_sources`, committed фикстуры в
`studio/backend/tests/fixtures/`) сравнивается скриптом
`compare_day22.py` с отчётом (`.omo/evidence/day22-rag-compare/`
compare.json + report.md; детерминированный факт-чек и источники —
hard gate, LLM-judge — soft; отчёт всегда пишется; settings-эхо;
честный итог). Корпус — 3 русских классика + fallback chain +
committed seed. UI — секция «Сравнение RAG» в KbTab. E2E-гибрид —
`scripts/e2e_day22.py` (порт 8106; Part A MUST PASS офлайн, Part B
live best-effort). `kb.py`, `ask_stream` и `/api/kb/*` дня 21 не
меняются.

## ADDED Requirements

### Requirement: Эндпоинт сравнения `POST /api/rag/compare`

Система SHALL предоставлять маршрут `POST /api/rag/compare` с
JSON-телом `{question}`: один HTTP-вызов выполняет ОБА LLM-вызова
(plain-рука и RAG-рука) и возвращает 200 с полями `answer_plain`
(str), `answer_rag` (str), `kb_block` (str), `chunks` (list чанков
retrieval), `rag_context` (dict). Пустое `question` → 400 с
RU-detail; индекс не построен → 404 «Индекс не построен».

#### Scenario: Контракт 200

- **WHEN** `POST /api/rag/compare {"question": "…"}` при построенном индексе
- **THEN** 200: `answer_plain` и `answer_rag` — непустые строки, `kb_block` — непустая строка, `chunks` — непустой список, `rag_context` — объект

#### Scenario: Пустой вопрос

- **WHEN** `POST /api/rag/compare {"question": ""}`
- **THEN** 400 с RU-detail; LLM-вызовы не выполняются

#### Scenario: Индекс не построен

- **WHEN** `POST /api/rag/compare` до `POST /api/kb/index`
- **THEN** 404 «Индекс не построен» (RU-detail)

### Requirement: Детерминированная методология (T=0, max_tokens=1024, bare промпт)

Оба LLM-вызова эндпоинта SHALL выполняться с `temperature=0` и
`max_tokens=1024` (переопределение над cfg из config). System-промпт
SHALL быть bare и симметричным: только `config.system_prompt` — без
блоков memory/profile/invariants (маркеры «Профиль», «Память»,
«Инварианты» в пейлоадах отсутствуют); у RAG-руки к system-промпту
SHALL добавляться `kb_block` (формат «База знаний», источник
`[file · section]`), у plain-руки — ничего.

#### Scenario: Параметры в обоих payload

- **WHEN** выполнен `POST /api/rag/compare` (захват payload fake-LLM)
- **THEN** в обоих LLM-пейлоадах `temperature=0` и `max_tokens=1024`

#### Scenario: Bare симметричный промпт

- **WHEN** выполнен `POST /api/rag/compare` (захват payload fake-LLM)
- **THEN** system-промпт plain-руки = `config.system_prompt` (в нём нет «База знаний», «Профиль», «Память», «Инвариантов»); system-промпт RAG-руки = `config.system_prompt` + `kb_block` (с «База знаний» и текстом из корпуса)

### Requirement: Независимость от тумблера `rag` в settings

Эндпоинт `POST /api/rag/compare` SHALL игнорировать флаг `rag` из
`kb.settings()` (явное сравнение — обе руки всегда производятся).
Параметры retrieval — `rag_recall`, `rag_top_k`, `reranker` — SHALL
читаться из settings (те же значения, что у RAG в чате дня 21); новых
ключей settings добавлено не SHALL быть.

#### Scenario: `rag=false` — обе руки

- **WHEN** `kb.update_settings({"rag": false})`, затем `POST /api/rag/compare`
- **THEN** 200: `answer_plain` и `answer_rag` оба непустые, `chunks` непустой (флаг `rag` не consulted)

### Requirement: Изоляция ошибок рук

Сбой одного LLM-вызова SHALL не ронять другой: эндпоинт отвечает 200,
в повреждённой руке — поле `error` (текст ошибки, ответ пуст/помечен),
в целой руке — обычный ответ.

#### Scenario: RAG-вызов упал

- **WHEN** RAG-LLM-вызов завершился ошибкой (plain-вызов успешен)
- **THEN** 200: у `answer_rag` — `error`, `answer_plain` — полный ответ

### Requirement: Контрольные вопросы (10, `expect_facts` + `expected_sources`)

Коммит-фикстура `studio/backend/tests/fixtures/control_questions.json`
SHALL содержать ровно 10 объектов `{id: 1..10, question, expect_facts[],
expected_sources[]}`: `expect_facts` — конкретные факты текста (имена,
события, жанр; абстрактные ожидания запрещены), `expected_sources` —
точные имена загружаемых файлов (поле `file` чанков). Q1–Q7 — про
классики корпуса, Q8–Q10 — про `egg_book.txt`. Схема SHALL
закрепляться тестом (`test_fixtures.py`: ровно 10, id уникальны 1..10,
оба массива non-empty, источники из белого списка).

#### Scenario: Схема фикстуры

- **WHEN** запущен `tests/test_fixtures.py`
- **THEN** PASS: ровно 10 вопросов, id 1..10 уникальны, у каждого `expect_facts` ≥1 и `expected_sources` ≥1, все `expected_sources` — имена из белого списка корпусных файлов

### Requirement: Скрипт сравнения и отчёт (hard/soft gate)

`scripts/compare_day22.py` SHALL прогнать все 10 контрольных вопросов
через `POST /api/rag/compare` (uvicorn :8106; probe GPustack down →
SKIP, exit 0) и написать в `.omo/evidence/day22-rag-compare/`
`compare.json` (settings-эхо: model/embedder/reranker/rag_recall/
rag_top_k/temperature/max_tokens/corpus_files; 10 записей: ответы,
факт-чек `{fact: {rag, plain}}`, `sources_ok`, `judge` (score/
verdict/reason | null), `error`/`skipped`; summary) и `report.md`
(settings-таблица, таблица вопросов, честный итог). Написание SHALL
происходить в try/finally — **всегда**, даже при крахе рана.
Детерминированные проверки (контракт, завершённость, факт-чек,
источники) — hard gate; LLM-judge (T=0, strict JSON) — soft:
записывается, **никогда не фейлит прогон**; «RAG проиграл» — валидный
исход. Exit 0 — PASS/SKIP, 1 — infra FAIL (сервер не поднялся, индекс
не собран, отчёт не записан).

#### Scenario: Отчёт при ошибке LLM

- **WHEN** в ране часть вопросов завершилась ошибкой LLM
- **THEN** `compare.json` существует: записи содержат `error`-поля, summary честно отражает ошибки, отчёт записан (try/finally), exit code не зависит от качества

#### Scenario: LLM-judge сбой не фейлит

- **WHEN** judge-вызов упал или вернул не-JSON
- **THEN** в записи `judge: null`; прогон не фейлит (exit 0 при корректной инфраструктуре)

### Requirement: UI — секция «Сравнение RAG» в KbTab

Фронтенд SHALL содержать в `KbTab.tsx` секцию «Сравнение RAG» (секция,
НЕ новый таб; `ContextPanel` TABS не меняется): поле вопроса + кнопка
«Сравнить»; результат — две панели рядом «Без RAG» / «С RAG»
(monospace, pre-wrap; на narrow — колонкой) + список чанков (`file ·
section`, score/rerank_score) + `kb_block` в `<details>`; ошибки
400/404/500 — RU-сообщение из `detail`. `api.ts` — `apiRagCompare`
(POST /api/rag/compare).

#### Scenario: Сравнение из UI

- **WHEN** пользователь ввёл вопрос и нажал «Сравнить»
- **THEN** показаны обе панели с ответами и чанки; при 404 — виден RU-текст «Индекс не построен», без crash

### Requirement: E2E (порт 8106)

`scripts/e2e_day22.py` SHALL быть гибридом (stdlib, порт **8106**):
**Part A** — офлайн-детерминированное ядро (net cut, TestClient +
fake-LLM, только committed фикстуры: shape, KB-блок есть/нет в
payload, 400/404, игнор `rag`-флага, схема control_questions,
T=0/max_tokens=1024, bare prompt) — MUST PASS; **Part B** — live
(uvicorn :8106, реальный LLM), best-effort — PASS или SKIP (GPustack
down / port-busy wait 10 мин; easter-egg факт в live-ответе —
WARNING, не FAIL). Exit 0 — PASS/SKIP, 1 — FAIL; cleanup всегда.

#### Scenario: E2E прогон

- **WHEN** запущен `python scripts/e2e_day22.py`
- **THEN** Part A — все шаги PASS (офлайн, детерминированно); Part B — PASS или SKIP; exit 0
