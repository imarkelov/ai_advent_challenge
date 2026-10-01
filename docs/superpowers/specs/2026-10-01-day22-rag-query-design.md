# День 22 — Первый RAG-запрос: RAG vs без RAG

Дата: 2026-10-01. Ветка: `day22-rag-query` (от `day21-doc-indexing`).
Статус: утверждён (дизайн подтверждён в чате; решения зафиксированы в
плане дня `.omo/plans/day22-rag-query.md`).

## 1. Задание дня

Первый полноценный RAG-запрос поверх базы знаний дня 21:

- **вопрос → поиск релевантных чанков → объединение с вопросом →
  запрос к LLM**;
- **сравнение** ответа модели **без RAG** и **с RAG** — рядом, на
  одном и том же вопросе;
- **усиление**: мини-набор из **10 контрольных вопросов** по базе
  знаний; для каждого — **ожидание** (что должно быть в ответе:
  `expect_facts`) и **какие источники** должны быть использованы
  (`expected_sources`);
- результат — **агент с двумя режимами** (RAG / no-RAG) за одним
  эндпоинтом + **сравнение качества** на 10 вопросах
  (детерминированный факт-чек + LLM-judge) + **отчёт** (JSON + MD) +
  **UI-секция** в вкладке «База знаний».

## 2. Связь с днём 21

День 21 построил базу знаний (`kb.py`): корпус `data/kb/uploads/`,
чанки, эмбеддинги, SQLite-индекс `data/kb/index.db`, **гибридный
поиск** (вектор + BM25, RRF) с опциональным 2-м этапом —
cross-encoder-реранкером, RAG-инъект в чат (блок «База знаний» в
system-промпте, тумблер `rag`), вкладка «База знаний» в UI. RAG в чате
уже работал — но **слепой**: видно, что база подмешана, не видно,
что она **добавляет**.

День 22 — контрольный эксперимент над этой же базой:

- **retrieval** — переиспользуется `kb.search_rag` (2 этапа,
  `rag_recall`/`rag_top_k`/`reranker` из settings дня 21);
- **рендер блока** — переиспользуется `agent._render_kb_block`
  (формат «База знаний», `[file · section]`, окно выдержки);
- **новые** — только метод `agent.rag_compare`, маршрут
  `POST /api/rag/compare`, UI-секция, корпус-книги, контрольные
  вопросы, compare-скрипт, e2e.

`kb.py`, `ask_stream` (чат-пайплайн), `GOLD_QUERIES` и `/api/kb/*`
**не меняются** (guardrail дня).

## 3. Зафиксированные решения (Q1–Q9)

| # | Решение |
|---|---|
| Q1 | **Один** эндпоинт `POST /api/rag/compare` выполняет **оба** LLM-вызова (plain + RAG) в одном HTTP-вызове и возвращает оба ответа (`answer_plain`, `answer_rag`, `kb_block`, `chunks`, `rag_context`) |
| Q2 | Методология: **T=0**, **max_tokens=1024**, **bare симметричный** системный промпт — только `config.system_prompt` (блоков memory/profile/invariants в пейлоадах **нет**) |
| Q3 | Эндпоинт **игнорирует** тумблер `rag` из settings (явное сравнение — обе руки всегда); `rag_recall`/`rag_top_k`/`reranker` читаются из settings; **новых ключей settings нет** |
| Q4 | RAG-рука: retrieval `kb.search_rag`, `kb_block` через `agent._render_kb_block` + к `config.system_prompt`; plain-рука: только `config.system_prompt` + вопрос |
| Q5 | **Hard/soft gate split**: детерминированные проверки (контракт, завершённость, факт-чек, источники) hard-гейтят скрипт сравнения; **LLM-judge — soft**: записывается, **никогда не фейлит** прогон |
| Q6 | E2E-порт **8106** (день 21 — 8105); **все** тестовые фикстуры — committed в `studio/backend/tests/fixtures/` (`data/kb/` gitignored) |
| Q7 | Корпус: 3 русских классика (Пушкин «Евгений Онегин», Чехов «Вишнёвый сад», Толстой «Война и мир») + **fallback chain** (Гоголь «Мёртвые души», Чехов «Дамы с собачкой») + **committed seed-корпус** (2 рассказа Чехова) для офлайн-деградации |
| Q8 | Отчёт `.omo/evidence/day22-rag-compare/` (compare.json + report.md) пишется **всегда** (try/finally), с settings-эхом; **честный итог**: RAG может проиграть — это валидный исход |
| Q9 | **10 контрольных вопросов**, у каждого `expect_facts` (конкретные факты текста, не абстракции) + `expected_sources` (точные имена загружаемых файлов) |

## 4. Текущее состояние (день 21) — якоря

- `studio/backend/kb.py` — `search_rag(query, recall, top_k,
  reranker_mode)` (:1139): этап 1 — гибридный recall top-`rag_recall`
  (вектор + BM25, RRF), этап 2 — cross-encoder (если `reranker=api`);
  чанки с метаданными `{chunk_id, source, file, section, chars, text,
  vector}`; поле `file` = имя загруженного файла.
- `studio/backend/agent.py` — `_task_llm_call` (:1159): non-stream
  LLM-вызов (не пишет requests.json — docstring); `_render_kb_block`
  (:466-478): блок «База знаний» (top-k выдержек, `[file · section]`);
  `_kb_context` (:480-502): `rag_context` для инспектора.
- `studio/backend/main.py` — KB-роуты :639-786: `create_app(agent, kb)`
  (DI), 400/404 RU-detail; settings дня 21
  `{rag, rag_top_k, embedder, reranker, rag_recall, strategy,
  agent_loop}` — читаются на каждый запрос.
- `studio/backend/tests/test_kb_api.py` — паттерн TestClient +
  fake-LLM `_llm_handler` с захватом payload; `data/kb/` gitignored
  (`.gitignore:48`) → фикстуры в `tests/fixtures/`.
- `scripts/e2e_day21.py` — шаблон гибридного e2e: Part A offline
  (`_NO_NET` dead-proxy, TestClient + fake-LLM, MUST PASS), Part B
  live (:8105, port-busy wait 10 мин → SKIP, cleanup всегда).
- `benchmark.py` — единственный прецедент LLM-judge (T=0, strict JSON
  `{score, verdict, reason}`, graceful judge-fail).

## 5. Целевая архитектура

### 5.1 Эндпоинт и метод (`agent.py` + `main.py`)

- `StudioAgent.rag_compare(question) -> dict`:
  1. `s = self.kb.settings()` — `rag_recall`/`rag_top_k`/`reranker` из
     settings; **флаг `rag` НЕ consulted**;
  2. retrieval: `rag = self.kb.search_rag(question, s["rag_recall"],
     s["rag_top_k"], s["reranker"])`;
  3. `kb_block = self._render_kb_block(question, rag["results"])`;
     `rag_context = self._kb_context(question, rag)`;
  4. plain: `_task_llm_call(cfg, config["system_prompt"], question)`;
     rag: `_task_llm_call(cfg, config["system_prompt"] + kb_block,
     question)`; `cfg` — model из config, **temperature=0,
     max_tokens=1024** (переопределение над cfg);
  5. каждый вызов в try/except → поле `error`;
  6. return `{answer_plain, answer_rag, kb_block, chunks:
     rag["results"], rag_context}`.
- `main.py`: `POST /api/rag/compare` рядом с KB-блоком:
  `question` non-empty (400 RU-detail), индекс (404 «Индекс не
  построен»), вызов `agent.rag_compare`.
- Вызовы non-stream, последовательно (parallel — усложнение без
  выгоды). Журнал `requests.json` не пишется (non-stream-паттерн).

### 5.2 Методология сравнения (почему именно так)

- **Один эндпоинт = две руки.** Сравнение атомарно: один вопрос, одни
  settings, один момент времени. Два отдельных запроса из клиента
  допускают сдвиг settings/индекса/состояния LLM между руками.
- **T=0 + max_tokens=1024** — детерминизм и общий бюджет: повторный
  прогон сравним, ни одна рука не «договаривает» за счёт лимита.
- **Bare симметричный промпт** — руки отличаются ровно `kb_block`:
  изоляция одного фактора (наличие RAG-выдержек). Memory/profile/
  invariants/tool-loop в экспериментах не участвуют.
- **Игнор `rag`-флага** — тумблер управляет чатом дня 21; сравнение —
  явное действие («покажи обе стороны»), результат не должен зависеть
  от чат-тумблера.
- **Hard/soft gate split** — «PASS» = «механизм работает», не «модель
  умная»: мелкие модели непредсказуемы, ассерты на качество сделали бы
  прогон флейком (прецедент дня 21: live-ассерты на смысл — WARNING,
  не FAIL). Факт-чек и источники детерминированы — на них можно
  гейтить; judge — opinion-слой для человека.

### 5.3 Корпус (3 классики + fallback + seed)

- `scripts/fetch_books.py` (stdlib urllib, никаких pip-зависимостей):
  pinned-список (порядок = приоритет): Пушкин «Евгений Онегин» →
  `pushkin_oneygin.txt`; Чехов «Вишнёвый сад» →
  `chekhov_cherry_orchard.txt`; Толстой «Война и мир» →
  `tolstoy_war_peace.txt`. Источники: literature.lib.ru (UTF-8/plain)
  с fallback на Wikisource (ru).
- Fallback chain при неудаче книги: Гоголь «Мёртвые души» →
  `gogol_dead_souls.txt`; Чехов «Дамы с собачкой» →
  `chekhov_lady_with_dog.txt`. Все 3 основных упали → копирование
  committed seed `studio/backend/tests/fixtures/seed_corpus/*.txt`
  (2 коротких публичных домена текста Чехова, ~30–80KB суммарно) в
  `data/kb/uploads/` — деградация, не фейл.
- Декодирование: UTF-8, затем cp1251 (lib.ru); size sanity > 50KB
  (seed except); атомарная запись (tmp → rename). CLI:
  `python scripts/fetch_books.py [--out data/kb/uploads] [--list]`;
  exit 0 даже при полном провале (JSON-сводка: что скачалось /
  фоллбек / seed).
- Тексты контрольных вопросов Q1–Q7 пишутся **после** скачивания
  (факты вычитываются из реально скачанного текста, чтобы
  `expect_facts` гарантированно присутствовали в корпусе; книга не
  скачалась → вопросы по fallback-книге).

### 5.4 Контрольные вопросы (10)

`studio/backend/tests/fixtures/control_questions.json` (committed):

```json
{"id": 1, "question": "…", "expect_facts": ["факт 1", "факт 2"], "expected_sources": ["chekhov_cherry_orchard.txt"]}
```

- Q1–Q7 — про 3 классики: 2 про «Онегина», 2 про «Вишнёвый сад», 1
  про «Войну и мир», 2 перекрёстных/фактологических (напр. «Какой
  жанр „Вишнёвый сад“?»);
- Q8–Q10 — про `egg_book.txt` (IPhone 17Promax, ксилофон, имя героя).
- `expect_facts` — конкретные факты текста (имена, события, жанр);
  «ответ должен быть осмысленным» — запрещённый формат.
- `expected_sources` — точные имена загружаемых файлов (поле `file`
  чанков; прецедент `GOLD_QUERIES` дня 21).
- Схема — тест `tests/test_fixtures.py`: ровно 10, id 1..10
  уникальны, оба массива non-empty, источники из белого списка
  (3 основных + 2 fallback + seed-имена + `egg_book.txt`).
- `egg_book.txt` — короткая детерминированная «книга» (~2–5KB) в духе
  e2e_day21 (e2e_day21.py:716): герой, ксилофон, IPhone 17Promax +
  3–5 уникальных фактов (имя, профессия, город).

### 5.5 Скрипт сравнения `scripts/compare_day22.py` (stdlib)

1. **Infra (HARD)**: порт 8106 (busy → wait 10 мин → SKIP, day-20
   паттерн); uvicorn subprocess (127.0.0.1:8106, `-X utf8`,
   PYTHONUTF8=1, логи → `%TEMP%\opencode\compare_day22_server.log`);
   probe GPustack (timeout 10s) → down: **SKIP, exit 0**; корпус:
   `DELETE /api/kb` → `fetch_books.py` → upload каждой книги +
   `egg_book.txt` (multipart) → `POST /api/kb/index
   {strategy: structural, embedder: api}` → poll `/api/kb/build-status`
   (1.5s, timeout 30 мин) → `POST /api/kb/settings`
   `{embedder: api, reranker: api, rag_recall: 50, rag_top_k: 3}`
   (ЯВЛЬНО, не «как есть»).
2. **Ран (HARD: завершённость)**: загрузить
   `control_questions.json`; вопросы, чьи `expected_sources` не в
   корпусе (стат `/api/kb/stats`) → `skipped: "source unavailable"`,
   не фейл; для каждого — `POST /api/rag/compare` (ответ:
   `answer_rag`, `answer_plain`, `chunks`); ошибка вопроса →
   `error: <msg>`, **продолжить** (изолирование).
3. **Оценка (SOFT — записывается, никогда не фейлит)**:
   - факт-чек: каждый `expect_fact` — нормализованное (lower,
     collapse spaces) подстроковое совпадение в `answer_rag` **ИЛИ**
     `answer_plain` → `facts: {fact: {rag: bool, plain: bool}}`;
   - `sources_ok`: `set(chunks[].file) ∩ expected_sources ≠ ∅`;
   - LLM-judge (паттерн `benchmark.py`): один вызов на вопрос (оба
     ответа + `expect_facts`, T=0, strict JSON `{"score": 1-10,
     "verdict", "reason"}`; verdict: `rag_wins|plain_wins|tie|
     inconclusive`); judge down / парс-фейл → `judge: null`.
4. **Отчёт (ВСЕГДА, try/finally)**:
   - `.omo/evidence/day22-rag-compare/compare.json`:
     `{settings_echo: {model, embedder, reranker, rag_recall,
     rag_top_k, temperature, max_tokens, corpus_files}, questions:
     [10 записей], summary: {total, completed, errors, skipped,
     facts_rag_pass, facts_plain_pass, sources_ok_count,
     judge_verdicts}}`;
   - `.omo/evidence/day22-rag-compare/report.md`: заголовок,
     settings-таблица, таблица вопросов (вопрос | факты RAG ✓/✗ |
     факты plain ✓/✗ | sources ✓/✗ | judge score/verdict),
     итог-абзац (**честный**: RAG может проиграть — писать как есть).
5. **Выход**: exit 0 (PASS или SKIP), 1 (infra FAIL: сервер не
   поднялся, индекс не собрался, отчёт не записан).
6. stdout: `sys.stdout.reconfigure(errors="replace")` (day-18
   cp1251-паттерн). Cleanup: uvicorn всегда (try/finally).

### 5.6 E2E `scripts/e2e_day22.py` (порт 8106)

Эталон — `e2e_day21.py` (структура, лог `[e2e-day22]`, exit codes,
cleanup).

- **Part A (MUST PASS, офлайн, net cut `_NO_NET`, in-process
  TestClient, fake LLM)** — ТОЛЬКО committed фикстуры (никакой
  зависимости от `data/kb/uploads` runtime-состояния): upload
  `egg_book.txt` (tmp KB, HashEmbedder) → index. Шаги: C1 shape
  (все поля), C2 KB-блок: в rag-payload ЕСТЬ «База знаний», в
  plain-payload НЕТ (захват fake-LLM), C3 400 пустой вопрос / 404
  без индекса, C4 `settings rag=false` → оба ответа, C5 схема
  `control_questions.json` (10, non-empty, id 1..10), C6 T=0 +
  max_tokens=1024 в обоих payload, C7 bare system prompt (нет
  «Профиль»/«Память»/«Инварианты»).
- **Part B (live, uvicorn :8106, PASS/SKIP)**: probe GPustack → down:
  SKIP (exit 0); port busy → wait 10 мин → SKIP; B1 corpus rebuild
  (wipe → upload egg_book + 1–2 скачанные книги, если есть, → index;
  api-эмбеддер, при провале — hash + метка; poll build-status); B2
  settings `{embedder: api, reranker: api}`; B3 один live compare
  (вопрос про egg_book, напр. про телефон) → 200, оба ответа
  non-empty, chunks non-empty; B4 easter-egg факт (IPhone 17Promax)
  в RAG-ответе — **best-effort WARNING, не FAIL** (паттерн B5 дня 21).
- Exit 0 = PASS/SKIP, 1 = FAIL.

### 5.7 UI — секция «Сравнение RAG» в KbTab

- `api.ts`: тип `RagCompareResult {answer_plain, answer_rag,
  kb_block, chunks: KbSearchResult[], rag_context}` +
  `apiRagCompare(question)` (рядом с KB-хелперами :549-609).
- `KbTab.tsx`: НОВАЯ секция (секция, НЕ новый таб; `ContextPanel`
  TABS не трогать): textarea 1–3 строки + «Сравнить»; loading —
  простой spinner (запрос non-stream); результат — две панели рядом
  «Без RAG» / «С RAG» (flex; на narrow — колонкой; monospace,
  pre-wrap); под панелями — список чанков (`file · section`,
  score/rerank_score) + `kb_block` в `<details>`; 400/404/500 →
  RU-сообщение из detail.
- `styles.css` — стили секции (паттерн существующих секций KbTab).
- Vitest (в `studio/frontend/tests/`): mock `apiRagCompare` → рендер
  секции → input → click → две панели с моковыми ответами; mock 404
  → error-сообщение.

## 6. Обработка ошибок

- `POST /api/rag/compare`: 400 (пустой `question`), 404 («Индекс не
  построен»); сбой одной LLM-руки → 200 + `error` в этой руке,
  вторая рука цела (изолирование).
- Корпус: книга не скачалась → fallback chain; все упали → seed-copy
  (деградация); `fetch_books.py` — exit 0 со сводкой в любом случае.
- Compare-скрипт: GPustack down → SKIP (exit 0); вопрос упал →
  `error` в записи, ран продолжается; отчёт пишется **всегда**
  (try/finally).
- E2E: Part A — офлайн (fake LLM + HashEmbedder); Part B — GPustack
  down / port busy → SKIP; B4 (смысл live-ответа) — WARNING.
- UI: 400/404/500 — RU-текст из `detail`, без crash.

## 7. Тесты

- Бэкенд (pytest, офлайн, TestClient + fake-LLM с захватом payload,
  паттерн `test_kb_api.py`): `test_rag_compare.py` — 9 тестов (shape
  200, plain без «База знаний», rag с «База знаний» + текст
  фикстуры, T=0/max_tokens=1024 в обоих payload, bare prompt,
  игнор `settings["rag"]`, 400, 404, изоляция ошибки руки) +
  `test_fixtures.py` (схема) + `test_fetch_books.py` (patched
  urlopen, tmp_path). Итого: 505 базовых + новые (замерить на
  релизе), 0 регрессий.
- Фронтенд (Vitest): секция «Сравнение RAG» (рендер, две панели,
  чанки, 404-error). Итого: 263 + новые (замерить на релизе) +
  `tsc -b` + build clean.

## 8. Документация и релиз

- `README.md`: строка в таблице дней + секция «День 22» (Что это /
  Архитектура / API / UI / E2E / Проверка задания / Статус).
- `RELEASE.md`: блок дня 22 сверху (файлы, API, проверка задания с
  реальными числами — замерить на релизе, Безопасность).
- `openspec/changes/day22-rag-query/`: proposal.md, design.md,
  tasks.md, specs/rag-compare/spec.md, .openspec.yaml.
- `docs/superpowers/plans/2026-10-01-day22-rag-query.md` —
  implementation plan.
- Демо-видео `day22_demo.mp4` (скилл `studio-demo-video`, desktop,
  не в репо) + log-evidence цитаты в RELEASE.
- Артефакты рана: `.omo/evidence/day22-rag-compare/`
  (compare.json + report.md; gitignored).

## 9. Риски

| Риск | Митигция |
|---|---|
| lib.ru/Wikisource недоступны или дали HTML-обёртку вместо plain-text | Fallback chain (2 запасные книги) + committed seed; size sanity > 50KB; декодирование UTF-8 → cp1251; выбор plain-text URL на этапе выполнения; exit 0 со сводкой — день не фейлится сетью |
| Маленькие модели дают нестабильные ответы на контрольные вопросы | Методология (T=0, bare симметричный промпт, max_tokens=1024) + hard/soft split: факт-чек/источники — мягкий подстроковый чек в отчёте, judge — soft; «RAG проиграл» — валидный исход, пишем честно |
| Live-ран сравнения долгий (индексация ~3 книг, 10×2 LLM-вызова + 10 judge) | Poll build-status с timeout 30 мин; GPustack down → SKIP (exit 0); cleanup uvicorn всегда |
| Сравнение «засорят» чат-контексты (память/профиль/инварианты) | Q2: bare симметричный промпт — только `config.system_prompt`; закреплено тестами захвата payload (C7) |
| `data/kb/` gitignored → фикстуры «потеряются» | Q6: все фикстуры committed в `studio/backend/tests/fixtures/`; Part A e2e — только committed-фикстуры |

## 10. Вне scope (YAGNI)

- Правки `kb.py` (search/search_rag/chunking/эмбеддеры/схема
  settings — новые ключи settings запрещены).
- Правки `ask_stream` / чат-пайплайна / `GOLD_QUERIES` /
  `scripts/e2e_day21.py`.
- Новые `.env`-ключи, LLM-модели, MCP-серверы, pip/npm-зависимости.
- Новый UI-таб (только секция в KbTab).
- HTML-отчёт (только JSON+MD), стриминг в compare-эндпоинте
  (non-stream, осознанно).
- «RAG должен победить»-ассерты (качество soft).
- Archive openspec-изменения (прецедент дня 21: не архивирован) и
  sync в main specs без явного запроса.
