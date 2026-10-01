# Proposal: day22-rag-query

## Why

День 22: **первый RAG-запрос** — расширение базы знаний дня 21: студия
отвечает на вопрос двумя способами (без RAG и с RAG) **в одном HTTP-вызове**
(`POST /api/rag/compare`), а качество на 10 контрольных вопросах по базе
сравнивается скриптом с отчётом (JSON+MD: детерминированный факт-чек +
LLM-judge) и видно в UI (секция «Сравнение RAG» в KbTab). Ключевые решения
(зафиксированы в плане дня): один эндпоинт делает ОБА LLM-вызова (T=0,
max_tokens=1024, bare симметричный системный промпт = `config.system_prompt`
— без memory/profile/invariants); эндпоинт **игнорирует** тумблер
`rag` из settings (явное сравнение — обе руки всегда), но `rag_recall`/
`rag_top_k`/`reranker` читает из settings; RAG-рука — `kb.search_rag` +
`kb_block` (`agent._render_kb_block`) в system-промпте, plain-рука — только
системный промпт и вопрос; ошибки рук изолированы (один фейл не роняет
второй); корпус — 3 русских классика (Пушкин «Евгений Онегин», Чехов
«Вишнёвый сад», Толстой «Война и мир») с fallback chain (Гоголь «Мёртвые
души», Чехов «Дамы с собачкой») и committed seed-корпусом (2 рассказа
Чехова) для офлайн-деградации; 10 контрольных вопросов (каждый с
`expect_facts` — конкретные факты текста, и `expected_sources` — точные
имена загруженных файлов) коммитятся в `studio/backend/tests/fixtures/`
(`data/kb/` gitignored); отчёт `.omo/evidence/day22-rag-compare/`
(compare.json + report.md) пишется **всегда** (try/finally) с settings-эхом
и честным итогом (RAG может проиграть — это валидный исход); e2e на порту
8106 (день 21 — 8105).

## What Changes

- **`studio/backend/agent.py`**: новый метод `StudioAgent.rag_compare(
  question) -> dict` — retrieval через `kb.search_rag(question,
  rag_recall, rag_top_k, reranker)` (из settings, флаг `rag` НЕ
  consulted), `kb_block` через `self._render_kb_block`, два
  non-stream LLM-вызова через `_task_llm_call` (T=0, max_tokens=1024,
  bare `config.system_prompt`; у RAG-руки — + `kb_block`), каждый вызов в
  try/except (поле `error`); возвращает
  `{answer_plain, answer_rag, kb_block, chunks, rag_context}`. `ask_stream`
  / чат-пайплайн / `kb.py` не трогаются.
- **`studio/backend/main.py`**: маршрут `POST /api/rag/compare` (рядом с
  KB-блоком :639-786): валидация `question` non-empty (400 RU-detail),
  check индекса (404 «Индекс не построен»), вызов `agent.rag_compare`.
  JSON-тело `{question}`.
- **`studio/backend/tests/test_rag_compare.py`** (новый, TDD): 9 тестов
  (TestClient + fake-LLM с захватом payload): shape 200, plain без
  KB-блока, rag с KB-блоком, T=0/max_tokens=1024 в обоих payload, bare
  system prompt, игнор `settings["rag"]`, 400 пустой вопрос, 404 без
  индекса, изоляция ошибки одной руки.
- **Фикстуры** (новые, committed): `studio/backend/tests/fixtures/
  control_questions.json` (10 вопросов: `id`, `question`, `expect_facts[]`,
  `expected_sources[]` — точные имена файлов), `egg_book.txt`
  (детерминированная «книга»: ксилофон, IPhone 17Promax, имя/профессия/
  города), `seed_corpus/*.txt` (2 коротких публичных домена текста Чехова),
  `tests/test_fixtures.py` (схема: ровно 10, id 1..10, `expect_facts`/
  `expected_sources` non-empty, источники из белого списка).
- **`scripts/fetch_books.py`** (новый, stdlib urllib): 3 pinned-классики
  (literature.lib.ru → fallback Wikisource ru), fallback chain (Гоголь /
  Чехов «Дамы с собачкой»), seed-copy при полном провале, декодирование
  UTF-8 → cp1251, size sanity > 50KB, атомарная запись; exit 0 даже при
  провале (JSON-сводка).
- **`scripts/compare_day22.py`** (новый, stdlib; httpx только если уже в
  requirements для e2e, иначе urllib): uvicorn на :8106,
  probe GPustack (down → SKIP, exit 0), wipe → fetch → upload + egg_book
  → index (api-эмбеддер, poll build-status) → settings явно
  (`embedder: api, reranker: api, rag_recall: 50, rag_top_k: 3`);
  10×`POST /api/rag/compare`; оценка: факт-чек (нормализованная
  подстрока) + `sources_ok` — **HARD** (завершённость), LLM-judge (T=0,
  strict JSON `{score, verdict, reason}`, паттерн `benchmark.py`) —
  **SOFT** (записывается, никогда не фейлит); отчёт compare.json +
  report.md **всегда** (try/finally), settings-эхо, честный итог.
- **`scripts/e2e_day22.py`** (новый, stdlib, порт **8106**): Part A —
  офлайн MUST PASS (net cut, TestClient + fake-LLM, только committed
  фикстуры: shape, KB-блок есть/нет, 400/404, игнор `rag`-флага, схема
  фикстур, T=0/max_tokens=1024, bare prompt); Part B — live best-effort
  (uvicorn :8106, GPustack down → SKIP, port-busy → wait 10 мин → SKIP),
  B4 (easter-egg факт в RAG-ответе) — WARNING, не FAIL.
- **Фронтенд**: `api.ts` — `RagCompareResult` + `apiRagCompare` (рядом с
  KB-хелперами); `KbTab.tsx` — НОВАЯ секция «Сравнение RAG» (секция, НЕ
  новый таб): вопрос → «Сравнить» → две панели рядом «Без RAG» / «С RAG»
  + чанки + `kb_block` в `<details>`; 400/404/500 → RU-сообщение из
  detail; vitest-тесты (mock `apiRagCompare`).
- **Доки**: README (строка таблицы + секция «День 22»), RELEASE (секция
  сверху), openspec-изменение, design/plan-доки в `docs/superpowers/`,
  демо-видео `day22_demo.mp4` (desktop, не в репо).

## Capabilities

### New Capabilities

- `rag-compare`: первый RAG-запрос (день 22) — один эндпоинт
  `POST /api/rag/compare` отвечает на вопрос двумя способами (plain /
  RAG, два non-stream LLM-вызова T=0, max_tokens=1024, bare симметричный
  системный промпт; retrieval — `kb.search_rag`, `kb_block` в
  system-промпте RAG-руки), игнорирует тумблер `rag` из settings
  (явное сравнение), ошибки рук изолированы; 10 контрольных вопросов
  (`expect_facts` + `expected_sources`, committed фикстуры) и скрипт
  сравнения с отчётом (детерминированный факт-чек + источники — hard
  gate; LLM-judge — soft, никогда не фейлит; отчёт всегда пишется,
  settings-эхо, честный итог); корпус — 3 русских классика + fallback
  chain + committed seed; секция «Сравнение RAG» в KbTab; e2e-гибрид
  (порт 8106, Part A MUST PASS офлайн).

### Modified Capabilities

- `knowledge-base` (день 21): добавляется потребитель `kb.search_rag` —
  метод `agent.rag_compare` + маршрут `POST /api/rag/compare` и
  compare-скрипт; `kb.py` (search/search_rag/chunking/эмбеддеры/схема
  settings — новые ключи settings запрещены), `ask_stream` и чат-пайплайн
  НЕ меняются; поведение `/api/kb/*` и RAG-инъекта в чат сохраняется
  как есть.

## Impact

- **Код**: новые `studio/backend/tests/test_rag_compare.py` (9),
  `studio/backend/tests/test_fixtures.py`, `studio/backend/tests/fixtures/
  {control_questions.json, egg_book.txt, seed_corpus/*.txt}`,
  `scripts/fetch_books.py`, `scripts/compare_day22.py`,
  `scripts/e2e_day22.py`; правки `studio/backend/agent.py` (только новый
  метод `rag_compare`), `studio/backend/main.py` (только новый маршрут),
  `studio/frontend/src/api.ts` (`apiRagCompare`), `KbTab.tsx` (секция),
  `styles.css`; vitest-тесты секции.
- **Данные**: runtime-корпус `data/kb/uploads/` (gitignored) — скачанные
  классики; runtime-отчёт `.omo/evidence/day22-rag-compare/` (gitignored);
  committed: `studio/backend/tests/fixtures/` (задачи, egg-книга, seed).
  Никаких новых `.env`-ключей.
- **Зависимости**: только stdlib (`urllib` в fetch_books; compare/e2e —
  stdlib, httpx только если уже присутствует в requirements — без новых
  pip-зависимостей); npm — без изменений.
- **Совместимость**: REST/SSE существующих роутов не меняются;
  `kb.py`/`ask_stream`/`GOLD_QUERIES`/`e2e_day21.py` не трогаются; дни
  11–21 не регрессируют.
