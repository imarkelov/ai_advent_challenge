# Release Notes — day23-rerank-filter (день 23)

Ветка: [`day23-rerank-filter`](https://github.com/imarkelov/ai_advent_challenge/tree/day23-rerank-filter)
(от `day22-ui-rework`).

## Что в релизе

**Реранкинг и фильтрация.** Поверх day21-реранкера
(`APIReranker` `qwen3-reranker-4b`, 2-этапный `search_rag`) и
day22-сравнения (`POST /api/rag/compare`) добавлены: порог отсечения
нерелевантных результатов `min_score` (0..1, дефолт 0.0 = off,
байт-в-байт без изменений при 0.0), query rewrite (один non-stream
LLM-вызов T=0/max_tokens=200, только в compare, live-чат не тронут)
и 4-режимное сравнение качества в одном HTTP-вызове: plain | rag |
rag+filter | rag+rewrite (5 LLM-вызовов: 4 руки T=0/max_tokens=1024 +
rewrite T=0/max_tokens=200). Контракт compare-ответа аддитивный:
старые 5 полей дня 22 не тронуты, добавлено 8; `e2e_day22.py`
остаётся green (обновлён только под 5-вызовный контракт, см. ниже).

- `studio/backend/kb.py` — `DEFAULT_SETTINGS` + `"min_score": 0.0`
  (merge over defaults, старый settings.json backward-совместим);
  `update_settings`: валидация min_score (bool ПЕРВЫМ — Python-ловушка
  `isinstance(True, int)`, затем `int/float` и 0..1, строк-чисел нет;
  400 RU «min_score должен быть числом от 0 до 1», сохранение как
  float); `search_rag`: новый параметр `min_score: float = 0.0`,
  отсечение после stage1 (+rerank, если был) и до `[:top_k]`:
  reranked → `rerank_score >= min_score`, без реранка →
  `score >= min_score * best` (relative-нормализация; guard
  `best == 0` → пустой список); аддитивные поля ответа
  `filtered: true` / `dropped: int` только при `min_score > 0`.
  RRF/BM25/embedder/chunking/APIReranker-internals не меняются.
- `studio/backend/agent.py` — `rewrite_query(question) -> (str, bool)`
  (один non-stream `_task_llm_call`, T=0, max_tokens=200,
  `REWRITE_QUERY_PROMPT`; `(rewritten, True)` только при непустом и
  не-идентичном (case-insensitive) ответе; сбой/пустой/идентичный →
  `(question, False)` + log warning); `rag_compare(question,
  min_score=None)` — с 2 рук до 4 арм: plain/rag без изменений и
  никогда не фильтруют; rag+filter — retrieval с `min_score`
  (body-override, иначе settings); rag+rewrite — retrieval на
  перефразе, ANSWER-LLM отвечает на оригинальный вопрос;
  `ask_stream` не тронут (guardrail-тест: rewrite не вызывается из
  чата).
- `studio/backend/main.py` — `POST /api/rag/compare`: body
  `{question, min_score?}`, валидация min_score как в
  `update_settings` (400 RU), default = `settings["min_score"]`;
  `GET /api/kb/search` передаёт `min_score` из settings в
  `search_rag` (ответ + `filtered`/`dropped` при > 0);
  `GET /api/kb/settings` отдаёт `min_score` (merge).
- Фронтенд — `src/api.ts` (`KbSettings.min_score`, опциональные
  4-arm-поля `RagCompareResult`, `apiRagCompare(question, minScore?)` —
  body `min_score` только если > 0); `components/KbTab.tsx`: поле
  «Порог отсечения (0 = off)» 0..1 step 0.05 (коммит на blur,
  derived-значение без init-эффекта), 4 панели сравнения (flex-wrap,
  без новых CSS-классов), чип «фильтр ≥ X» (filter-панель + секция
  поиска) при min_score > 0, rewrite-chip с копированием (clipboard +
  execCommand-фолбэк, «Скопировано» 1.5 c) при
  `rewrite_applied === true`; undefined-safe для старого бэкенда.
- `scripts/e2e_day23.py` (новый, stdlib, порт 8107) — Part A офлайн
  MUST PASS (net cut, TestClient + fake-LLM + fake-reranker
  [0.99, 0.5, 0.1]): A1 settings/валидация, A2 абсолютный фильтр,
  A3 relative-режим, A4 4 армы + 5 LLM-вызовов + scripted rewrite,
  A5 rewrite-fallback, A6 всё-отфильтровано (0.999 → [] + непустой
  ответ), A7 day22-регрессия; Part B live best-effort
  (B1 rebuild, B2 settings, B3 compare min_score=0.5, B4
  easter-egg WARNING).
- `scripts/compare_day23.py` (новый, stdlib, паттерн compare_day22) —
  12 вопросов (10 контрольных дня 22 + 2 «отвлекающих») × 4 режима,
  body `min_score=0.6`, факт-чек (hard) + LLM-judge (soft, 12/12),
  отчёт `.omo/evidence/day23-compare/` (compare.json + report.md,
  всегда, try/finally).
- `scripts/e2e_day22.py` (обновлён, документированное отклонение от
  запрета «не менять e2e_day22»): Part A под 5-вызовный контракт —
  A2 `len(captured)==5`, A3 `no_calls==5`, A6 — индексы арм
  (0/1/2/4 mt=1024 + rewrite(3) mt=200), A7 `==5`,
  `COMPARE_TIMEOUT` 330→825 (5 live-вызовов); чеки старых полей не
  тронуты. Без этого Part A физически не может быть 7/7 (A6
  ассертил ровно 2 non-stream вызова).
- `openspec/changes/day23-rerank-filter/` (proposal, design, tasks,
  specs/rag-filter-rewrite/spec.md: 3 Requirement, 12 Scenario) +
  `docs/superpowers/specs/2026-10-03-day23-rerank-filter-design.md` +
  `docs/superpowers/plans/2026-10-03-day23-rerank-filter.md`.

## API

| Метод | Путь | Назначение |
| --- | --- | --- |
| GET / POST | `/api/kb/settings` | + `min_score` (0..1, float, дефолт 0.0); 400 RU — bool / строка / вне диапазона |
| GET | `/api/kb/search?q=&k=5` | `min_score` из settings; при `min_score > 0` в ответе аддитивно `filtered: true` + `dropped: int` |
| POST | `/api/rag/compare` | body `{question, min_score?}` → 13 полей: 5 дня 22 (`answer_plain`, `answer_rag`, `kb_block`, `chunks`, `rag_context`) + `answer_rag_filter`, `answer_rag_rewrite`, `chunks_rag_filter`, `chunks_rag_rewrite`, `rag_context_rag_filter`, `rag_context_rag_rewrite`, `rewritten_query`, `rewrite_applied`; 5 non-stream вызовов (руки T=0/max_tokens=1024, rewrite T=0/max_tokens=200); 400 — пустой вопрос / некорректный `min_score` (RU), 404 — индекс не построен; журнал `requests.json` не пишется |

Новых эндпоинтов нет: изменены существующие 3 маршрута (аддитивно).

## Проверка задания

Бэкенд — **581 тестов PASS** (pytest, офлайн; baseline дня 23 — 548,
+33 новых). Фронтенд — **296 тестов PASS** (Vitest; baseline 288, +8)
+ `tsc -b` clean + `npm run build` clean. E2E `scripts/e2e_day23.py`:
**Part A 7/7 PASS** (офлайн, net cut); **Part B live: PASS=11,
FAIL=0, WARNING=1** (B4 easter-egg — best-effort WARNING). Регрессия
`scripts/e2e_day22.py`: **12/12 PASS** (Part A 7/7 + Part B live) —
контракт compare аддитивен. Compare-отчёт
(`.omo/evidence/day23-compare/`, live: deepseek-v4-flash,
embedder/reranker api, recall 50, top_k 3, T=0, корпус 6 файлов /
1666 чанков / dim 4096, `min_score=0.6` в body): **12 вопросов × 4
режима**, judge 12/12; win-rate: plain 13/22 (59%), rag 13/22 (59%),
rag+filter 13/22 (59%), rag+rewrite 13/22 (59%); avg score:
plain 6.0 / rag 9.0 / rag+filter 9.0 / rag+rewrite 8.8;
judge-вердикты: tie=5, rag_wins=3, rag_filter_wins=2, plain_wins=2.
Честный итог (как в отчёте): на 1666-чанковом корпусе все 4 режима
примерно равны по факто-покрытию; фильтр выиграл на 2 «отвлекающих»
вопросах (Q3, Q9 — очевидная нерелевантность корпуса), plain — на 2
общих вопросах (Q11, Q12), где база знаний и не нужна.

### Исправление после релиза (live-chat min_score)

F2-ревью (code-quality) нашло: live-чат (`ask_stream`) не передавал
`min_score` в `_rag_retrieve` — спека требует единый фильтр у всех 3
потребителей `search_rag` (live-чат, `GET /api/kb/search`,
`POST /api/rag/compare`). Фикс `29f33d8`: pass-through настройки
+ 2 spy-теста (бэкенд 579 → **581** тестов PASS).

## Коммиты

| Коммит | Сообщение |
| --- | --- |
| `5f232bb` | feat(day23): min_score — settings + валидация + фильтр в search_rag |
| `7f5b6d8` | fix(day23): min_score guard на пустой stage1 (max default=0) |
| `19e73af` | docs(day23): openspec change day23-rerank-filter |
| `ca3a2f8` | feat(day23): rewrite_query + оффлайн-тесты |
| `feb6db3` | feat(day23): /api/rag/compare 4 армы + body override min_score |
| `39bd248` | feat(day23): UI — min_score поле + 4 панели сравнения |
| `3734236` | feat(day23): compare_day23.py — 10 вопросов × 4 режима |
| `d11e8df` | test(day23): e2e_day23.py — Part A offline + Part B :8107 |
| `339c3f8` | docs(day23): README + RELEASE + spec/plan |
| `25b2b2b` | docs(day23): tick tasks 7-9 in openspec tasks.md |
| `8c393f6` | docs(day23): tick task 10 in openspec tasks.md |
| `b9bcff2` | docs(day23): tick tasks 1-6 in openspec tasks.md |
| `29f33d8` | fix(day23): min_score в live-чат (ask_stream) + docstring «пять вызовов» |
| `b11a6a7` | docs(day23): бэкенд 581 тестов (fix live-chat min_score +2) |

---

# Release Notes — day22-ui-rework (день 22, UI-rework)

Ветка: [`day22-ui-rework`](https://github.com/imarkelov/ai_advent_challenge/tree/day22-ui-rework)
(от `day22-rag-query`).

## Что в релизе

**Реорганизация UI Студии** по утверждённому мокапу
(`.omo/mockup/ui-rework-v3.html`): стройный сайдбар,
токен-статистика на кольце TokenGauge (hover-поповер) и
inline-представление обработки запроса в ленте чата (строка
«📡 Обработка» под assistant-сообщением с раскрытием
RAG → Prompt-сборка → LLM → Ответ и реальным телом LLM-запроса
из журнала). Data-driven: строка и JSON — только где данные
есть, без фейковых данных.

- `studio/frontend/src/components/Sidebar.tsx` — убран блок
  «Инструменты» (вкладки «Токены»/«Запрос», перенесённые в
  день 15); управление диалогами (активация/ренейм/удаление/
  режим выбора, бейдж непрочитанных, флаг «Проект
  использовался») и сводка слоёв памяти со свитчами — без
  изменений.
- `studio/frontend/src/components/TokenGauge.tsx` +
  `TokenPopover.tsx` (новый) — по hover на кольцо (grace-период
  закрытия 300 ms, клик вне закрывает) открывается поповер:
  лимит контекста (progress-бар по total последнего запроса),
  последний usage (prompt/completion/total), сессионные токены,
  модель. Окно растягивается drag-grip (паттерн модалки JSON дня
  8: pointer events; min 320×200, max — вьюпорт минус отступы);
  размер — `localStorage` `token-pop-size` (JSON `{w,h}`, дефолт
  340×240), восстанавливается после перезагрузки страницы.
- `studio/frontend/src/components/FlowInspector.tsx` (новый) +
  `flow.css` (новый, самодостаточный, классы `.flow-*`, импорт в
  самом компоненте) — inline-строка «📡 Обработка» под
  assistant-сообщением (рендер в `ChatPanel.tsx` только при
  `assistant && (request_id != null || rag_context.chunks.length
  > 0)` — у task-сообщений строки нет). Свёрнутая строка —
  сегменты « → » (часть без данных не рендерится): «RAG N
  чанк/чанка/чанков», «Prompt ≈N tok», model, total из `usage`.
  Раскрытие — шаги: **RAG** (query, recall, чанки `file ·
  section` + score-чипы 🟢/🟡/🔴 + «из #N» и 🚨 при
  `stage1_rank > 20`), **Prompt-сборка** (чипы блоков
  system-промпта с оценкой токенов: базовый / профиль /
  инварианты — ленивый `GET /api/rules` по первому раскрытию,
  кэш / память WM-LT / RAG-блок; клиентская эвристика 0.44
  токена/символ), **LLM** (модель + пункт «📄 Запрос JSON ·
  req_N»: ленивый `GET /api/requests/{id}` по первому раскрытию,
  кэш, тело — поле `request` записи журнала, `<pre>` + bottom-
  fade, «Скопировать» — clipboard с execCommand-фолбэком,
  «Скопировано» 1.5 с), **Ответ** (usage или «—»).
- `studio/backend/memory.py` + `agent.py` (TDD) — финальное
  chat assistant-сообщение хранит `request_id` и `usage`
  (`append_message` — явные kwargs, merge только при `not
  None`; финальная ветка `ask_stream` передаёт значения из SSE
  `done`). Сообщения raw в `dialogues.json` → поля
  автоматически отдаются `GET /api/dialogues[/{id}]` (роуты не
  тронуты) — строка «Обработка» и JSON доступны после
  перезагрузки страницы.
- Мёртвый код: удалены `TokensTab.tsx`, `RequestsTab.tsx` (+ их
  тесты). Тесты: бэкенд `test_agent.py`/`test_api.py` (+2 на
  request_id/usage, обновлены 2 точных equality-ассерта);
  фронтенд `tests/flow-inspector.test.tsx` (новый, 14
  сценариев: строка+сегменты, условие ChatPanel, шаги, lazy
   fetch + «Загрузка…», кэш без рефетча, clipboard,
   execCommand-фолбэк, 404, без request_id → нет JSON-пункта).

## Исправление после релиза (kb.py: кодировки + опечатки)

Два robustness-фикса `studio/backend/kb.py` (TDD, +9 тестов в
`tests/test_kb.py`; бэкенд после фикса — **548 тестов PASS**):

- **`KnowledgeBase._read_file` — авто-определение кодировки**
  (utf-8 строгий → cp1251 строгий → utf-8/`errors="replace"`). До
  фикса Windows-1251 (ANSI) файл пользователя молча декодировался
  как utf-8 c replace: **5826 символов U+FFFD** в индексе, файл
  невидим для кириллических запросов. Байты читаются один раз,
  нормализация \r\n / \r → \n сохранена, валидный utf-8
  декодируется идентично прежнему поведению; недекодируемый
  бинарный файл не роняет сборку (U+FFFD).
- **`_match_tf` — typo-допуск в BM25-матчинге** (оба терма ≥ 5
  симв., `_PREFIX_MIN`): матчем считается также равные первые 5
  символов **или** один терм = другой минус ровно один символ
  (новый хелпер `_one_del`, детерминированный O(len)) — «телфон» ↔
  «телефон» в обе стороны. Живой кейс: запрос «Как телфон был у
  бабки…» — BM25-нога дала 0 для easter-egg чанка, одна
  векторная нога уронила его ниже top-3 на большом корпусе.
  Точный матч, substring-инфлексии (`_SUBSTR_MIN`, «телефона» ↔
  «телефон»), короткие термы (< 5) и max-tf семантика не
  изменены.

## API

Новых REST-эндпоинтов нет. `GET /api/dialogues` и
`GET /api/dialogues/{id}` — assistant-сообщения финального чата
дополнены полями `request_id` (int) и `usage`
(`prompt_tokens`/`completion_tokens`/`total_tokens`, возможны
доп. поля usage-чанка). Тело `POST /api/chat` не изменилось.

## Проверка задания

Полный прогон (все exit 0): бэкенд — **539 тестов PASS**
(`python -m pytest -q`, офлайн); фронтенд — **288 тестов PASS**
(`npm test` / Vitest) + `tsc -b` clean + `npm run build` clean
(dist собран). Live-верификация в браузере (prod-сервер
:8107, модель deepseek-v4-flash): **поповер** — hover на кольцо
→ окно (лимит 16384, последний usage 773/133/906, сессионные
906, модель), drag-grip 340×240 → 460×320
(`token-pop-size` = `{"w":460,"h":320}` в localStorage), размер
восстанавливается после перезагрузки; **флоу** — live-ответ
(2+2) → строка «Обработка: RAG 3 чанка → Prompt ≈600 tok → LLM
deepseek-v4-flash → 906 tok», **после перезагрузки страницы
строка и карточка на месте** (request_id пережил reload —
критерий T2), раскрытие: RAG (3 чанка, score-чипы, «из
#50/#35/#30» + 🚨), Prompt-сборка (базовый ≈192, память LT ≈12,
RAG-блок ≈396), LLM → «Запрос JSON · req_386» с реальным телом
из журнала + «Скопировано»; **сайдбар** — без «Инструментов»,
диалоги/память на месте; **регресс** — панель «Контекст»
(вкладка Память рендерит 3 слоя), MCP «🧩» (overlay, 12
серверов), RAG-инспектор («▼ Показать извлечённый контекст RAG
(3 чанков из 50)» → 3 чанка со score), консоль — 0 ошибок.
Скриншоты — `.omo/evidence/ui-rework/` (task4-popover-open.png,
task4-popover-resized.png, task4-flow-after-reload.png,
task4-sidebar.png).

## Коммиты

- `2ac86de` — sidebar без «Инструментов» + токен-поповер
  (hover, resize, localStorage); assistant-сообщение хранит
  request_id/usage
- `8fc2866` — FlowInspector: inline-флоу «Обработка» в ленте
  (RAG/Prompt/LLM/Ответ + Запрос JSON из журнала с
  копированием); удалён мёртвый RequestsTab

---

# Release Notes — day22-rag-query (день 22)

Ветка: [`day22-rag-query`](https://github.com/imarkelov/ai_advent_challenge/tree/day22-rag-query)
(от `day21-doc-indexing`).

## Что в релизе

**Первый RAG-запрос.** Студия отвечает на вопрос двумя способами
(без RAG и с RAG) **в одном HTTP-вызове** (`POST /api/rag/compare`):
оба LLM-вызова делает эндпоинт (T=0, max_tokens=1024 — пин, не из
конфига; симметричный голый system-промпт — у plain руки промпт как
есть, у RAG-руки + блок «База знаний» из `kb.search_rag`: recall
top-50 → cross-encoder top-3). Тумблер `rag` из settings не
consulted (сравнение явное); сбой руки — «Ошибка: …» в ответе этой
руки (статус 200); non-stream `_task_llm_call` — журнал
`requests.json` не пишется. Качество на 10 контрольных вопросах
(committed-фикстуры `expect_facts` + `expected_sources`) — скрипт
сравнения с отчётом (детерминированный факт-чек + источники,
LLM-judge), оба ответа рядом в UI (секция «Сравнение RAG» в
KbTab). Корпус — 5-книжный каталог (Пушкин «Евгений Онегин»,
Чехов «Вишнёвый сад», Толстой «Война и мир» (том 1), Гоголь
«Мёртвые души», Чехов «Дама с собачкой» (seed 34KB)):
primary → fallback → committed seed, stdlib only.

- `studio/backend/agent.py` — +38: `StudioAgent.rag_compare(question)`
  (retrieval `kb.search_rag` из settings `rag_recall`/`rag_top_k`/
  `reranker`, `kb_block` через `_render_kb_block`, два non-stream
  LLM-вызова `_task_llm_call` T=0/max_tokens=1024, каждая рука в
  try/except; возвращает `{answer_plain, answer_rag, kb_block,
  chunks, rag_context}`). `ask_stream`/чат-пайплайн не тронуты.
- `studio/backend/main.py` — +19: маршрут `POST /api/rag/compare`
  (400 пустой вопрос, 404 «Индекс не построен», RU-detail).
- `studio/backend/tests/test_rag_compare.py` (новый, 235 строк, 9
  тестов: shape 200, plain без KB-блока, rag с блоком, T=0/
  max_tokens=1024 в обоих payload, bare system-промпт, игнор
  `settings["rag"]`, 400/404, изоляция ошибки одной руки).
- Фикстуры (committed): `tests/fixtures/control_questions.json`
  (10 вопросов: `id`, `question`, `expect_facts[]`,
  `expected_sources[]`), `tests/fixtures/egg_book.txt` (2.6KB —
  ксилофон, IPhone 17Promax, имя/профессия/город/кот),
  `tests/fixtures/seed_corpus/` (chekhov_chameleon 54KB,
  chekhov_horse_first 11KB).
- `scripts/fetch_books.py` (новый, 338 строк, stdlib urllib) —
  5-книжный каталог: primary-источник → fallback (Гоголь / «Дама с
  собачкой») → seed-copy при полном провале; декодирование по
  частотным служебным словам (utf-8/koi8-r/cp1251), size sanity >
  50KB, атомарная запись; exit 0 даже при провале (JSON-сводка).
- `scripts/compare_day22.py` (новый, 867 строк, stdlib) — uvicorn
  :8106, probe GPustack (down → SKIP), wipe → fetch → upload +
  egg_book → index (api-эмбеддер) → 10 × `POST /api/rag/compare` →
  факт-чек (hard) + `sources_ok` (hard) + LLM-judge (soft, T=0
  strict JSON, никогда не фейлит); отчёт compare.json + report.md
  **всегда** (try/finally) с settings-эхом и честным итогом.
- `scripts/e2e_day22.py` (новый, 724 строк, stdlib, :8106) — Part A
  офлайн MUST PASS (net cut, TestClient + fake-LLM-захват + tmp-БЗ
  egg_book + HashEmbedder, 7 шагов), Part B live (wipe → upload →
  index → compare, B4 easter-egg — WARNING, не FAIL); port-busy —
  ожидание до 10 мин; cleanup всегда.
- Фронтенд: `src/api.ts` (+18) — `RagCompareResult` + `apiRagCompare`;
  `components/KbTab.tsx` (+117) — секция «Сравнение RAG» (textarea +
  «Сравнить» + две панели «Без RAG»/«С RAG» + чанки + `kb_block` в
  `<details>`); `src/styles.css` (+83); `tests/kb-tab.test.tsx`
  (+139, 3 теста секции).
- `openspec/changes/day22-rag-query/` (5 файлов: .openspec.yaml,
  proposal, design, tasks, specs) +
  `docs/superpowers/specs/2026-10-01-day22-rag-query-design.md` +
  `docs/superpowers/plans/2026-10-01-day22-rag-query.md`.

**Follow-up: RAG-режим per-диалог** (коммиты `7030e38` бэкенд,
`e2b83f1` фронтенд). Основной UX двух режимов — в обычном чате:
свитч «RAG» в шапке чата управляет RAG-режимом **активного диалога**;
пользователь задаёт вопрос в одном диалоге с RAG **off**, в новом
диалоге — с RAG **on**, и сравнивает ответы в голове. Эндпоинт
`/api/rag/compare` остаётся инструментом **прямого** сравнения (оба
сосуществуют). Ограничение оригинала «`ask_stream`/чат-пайплайн не
тронуты» снято для follow-up: изменение в `ask_stream` — только
резолвинг режима, механика дня 21 (retrieval, KB-блок,
`rag_context`) не тронута.

- `studio/backend/` — поле `rag: bool|null` в записи диалога (`null`
  = по глобальному `settings['rag']`); `POST /api/dialogues/{id}/rag`
  (`{rag: bool}`, 200 обновлённый диалог / 400 не-bool / 404 диалог,
  RU-detail); `rag` в выдаче `GET /api/dialogues[/{id}]`;
  `ask_stream`: эффективный режим = `dialogue.rag if not None else
  settings['rag']`.
- `studio/backend/tests/test_rag_mode.py` (новый, 8 тестов:
  маршрут 200/400/404, `null` по умолчанию, override в обе стороны,
  регресс дня 21).
- Фронтенд — `src/api.ts` (`rag?: boolean|null`, `apiSetDialogueRag`),
  свитч «RAG» в шапке чата (effective = `dialogue.rag ?? globalRag`
  из `apiKbSettings()`, кэш, fallback `true` при ошибке; оптимистичное
  обновление + авторитетное перечитывание, откат при ошибке; при
  смене диалога — режим его диалога); +5 тестов.

## API

| Метод | Путь | Назначение |
| --- | --- | --- |
| POST | `/api/rag/compare` | `{question}` → `{answer_plain, answer_rag, kb_block, chunks, rag_context}`: два non-stream LLM-вызова (T=0, max_tokens=1024, bare симметричный system-промпт; у RAG-руки + `kb_block` из search_rag recall top-50 → reranker top-3); 400 — пустой вопрос, 404 — «Индекс не построен» (RU-detail); журнал `requests.json` не пишется (non-stream) |
| POST | `/api/dialogues/{id}/rag` | (follow-up) `{rag: bool}` → RAG-режим per-диалог (`null` = по глобальному `settings['rag']`; эффективный режим в `ask_stream` = `dialogue.rag if not None else settings['rag']`); 200 — обновлённый диалог, 400 — не-bool, 404 — диалог (RU-detail) |

## Проверка задания

Бэкенд — **529 тестов PASS** (pytest, офлайн; замер на момент
написания). Фронтенд — **266 тестов PASS** (Vitest, 20 файлов) +
`tsc -b` clean (замер); build `dist` — clean (T5-проверка, коммит
a73ddcc). E2E `scripts/e2e_day22.py` на этой машине: **Part A 7/7
PASS** (net-cut: TestClient + fake-LLM-захват payload + tmp-БЗ
egg_book + HashEmbedder), **Part B live 12 PASS / 0 FAIL / 0 SKIP**
(B4 easter-egg — best-effort WARNING: на 3-файловом live-корпусе
top-chunk на вопрос про телефон — Чехов, не egg_book; контракт формы
соблюдён, семантика мелких моделей не гарантируем). Compare-отчёт
(`.omo/evidence/day22-rag-compare/`, compare.json + report.md; live:
qwen3.8-27b, embedder/reranker api, recall 50, top_k 3, T=0,
max_tokens=1024, корпус 6 файлов / 1666 чанков, индексация 177s):
**10/10 вопросов, 0 ошибок, 0 пропусков**; факт-чек (полное
покрытие expect_facts) — RAG **2/10**, plain **2/10**; совпадений
фактов — RAG **11/22**, plain **10/22**; sources_ok (chunks ∩
expected_sources) — **7/10**; LLM-judge — **rag_wins=2, plain_wins=3,
tie=5**. Честный итог (как в отчёте): на этом наборе вопросов
plain-ответ **чаще не хуже** RAG — retrieval (recall top-50 →
reranker top-3) не вытащил одиночный egg_book-чанк в top-k на
1666-чанковом корпусе; чанки в индексе есть (содержат «ксилофон» и
«IPhone») — проигрыш retrieval, не индексации. Демо-видео (live,
per-диалог-режим, deepseek-v4-flash):
`C:\Users\migor\OneDrive\Рабочий стол\AI Advent Challenge -
видео\day22_demo.mp4` (desktop, **не в репозитории** — единственное
хранилище демо по конвенции проекта), 32.48 s, ~551 КБ, 1440×900,
h264 faststart. Сценарий: диалог 1, свитч «RAG» в шапке чата
переключён на экране в **off** (`POST /api/dialogues/{d1}/rag
{rag:false}` 200) → «Какой телефон был у героя?» → ответ **без
фактов корпуса** (уточняющий вопрос «о каком герое и из какого
произведения», `rag_context` в сохранённом сообщении отсутствует);
новый диалог, свитч **on** (200) → тот же вопрос → ответ на
фактах БЗ («В приведённых отрывках из „Вишневого сада" телефон
героя не упоминается — в пьесе фигурируют телеграммы, а не
телефон») + раскрытый `RagContextInspector` (3 чанка,
reranked=true, recall 50) как визуальное доказательство активного
RAG (корпус записи: 3 файла / 94 чанка, api-эмбеддер dim 4096,
api-реранкер). Лог-цитаты (вербатим, `demo_video_server.log`):
`INFO: 127.0.0.1:59861 - "POST
/api/dialogues/a64fbe389ef842c79a4e6dfb4d5b02be/rag HTTP/1.1"
200 OK` (пер-диалог-режим переключён) и `[Final Response] В
приведённых отрывках из «Вишневого сада» телефон героя не
упоминается — в пьесе фигурируют телеграммы, а не телефон.`
(ответ RAG-on руки KB-grounded). Старое видео с панелями
сравнения (15.92 s, 601 365 байт ≈ 587 КБ, LLM qwen3.8-27b)
сохранено как `day22_demo_v1.mp4`. Отклонение (модель): в момент
записи qwen3.8-27b отдавал 403 «Api key not allowed» на все 5
ключей `.env` (ротация/перескоуп ключа на стороне GPustack, не баг
продукта, `.env` не тронут) — self-heal конфига дня 11 переключил
модель на deepseek-v4-flash, запись выполнена на ней; перезапись
на qwen возможна после восстановления ключа (build, индекс БЗ,
гигиена портов — все пре-условия остаются валидными).

## Безопасность

Секреты — только в `.env` (корень, в `.gitignore`):
`GPUSTACK_BASE_URL`, `GPUSTACK_API_KEY`, `GPUSTACK_KEY_EMBED`,
`GPUSTACK_KEY_RERANK` — все уже существовали к дню 21, **новых
`.env`-ключей нет**. Скан по новым day22-файлам (scripts/
fetch_books|compare_day22|e2e_day22.py, tests/fixtures/*,
test_rag_compare.py, test_fixtures.py, openspec change,
docs/superpowers specs+plans): литеральные ключи `gpustack_*` —
**0 совпадений**; `Bearer {key}` — только шаблоны с
`os.environ["GPUSTACK_API_KEY"]` (тот же паттерн, что в
e2e_day9/17–21) — **0 утечек**.

---

# Release Notes — day21-doc-indexing (день 21)

Ветка: [`day21-doc-indexing`](https://github.com/imarkelov/ai_advent_challenge/tree/day21-doc-indexing)
(от `day20-mcp-orchestration`).

## Что в релизе

**База знаний (индексация документов + RAG).** Пайплайн индексации
документов: корпус (загрузки пользователя — «+ Добавить файл»),
2 стратегии chunking, 2 эмбеддера, **локальный SQLite-индекс**
(`data/kb/index.db`) с метаданными чанков (`chunk_id`, `source`,
`file`, `section`) и сравнением стратегий (8 gold-запросов),
**гибридный поиск** (вектор + BM25, фузия RRF) с опциональным
2-м этапом — cross-encoder-реранкером (`qwen3-reranker-4b`),
RAG-инъект в агентский чат (top-k выдержек ≤ 300 символов в
system-промпт на каждое сообщение, RAG-контекст ответа — инспектор в
UI) + тумблеры RAG / реранкера / цикла-агента.

- `studio/backend/kb.py` (новый) — RAG-ядро: `KBError`, `CorpusDoc`,
  `Chunk`, `FixedChunker` (1200 символов, overlap 200),
  `StructuredChunker` (markdown `#..######`, секция > 2400 → суб-чанки
  по fixed; не-markdown — файл, > 2400 → суб-чанки), `HashEmbedder`
  (stdlib: char 3-граммы → md5 → 256 бакетов, L2-норм; детерминизм
  только через hashlib — встроенный `hash()` salted per-process),
  `APIEmbedder` (GPustack `POST /embeddings`,
  `qwen3-vl-embedding-8b`, dim 4096, батчи 16, ключ
  `GPUSTACK_KEY_EMBED`), `APIReranker` (Jina-совместимый
  `POST /v1/rerank`, `qwen3-reranker-4b`, срез документа 1024,
  батчи 16, ключ `GPUSTACK_KEY_RERANK`), гибридный поиск
  (косинус + Okapi BM25 со stopwords и substring-инфлексиями, RRF),
  `GOLD_QUERIES` (8), `KnowledgeBase` (`build` full/incremental/auto,
  `search`/`search_rag` 2-этапный, `delete_upload`/`wipe`,
  `settings` с валидацией).
- **`_IndexStore` (SQLite, stdlib sqlite3)** — хранилище индекса:
  `meta(key, value)` (strategy, embedder, model, dim, built_at,
  stats/comparison — JSON) + `chunks(chunk_id, source, file, section,
  chars, text, vector BLOB, terms JSON)`; вектор — BLOB float32
  little-endian (`struct.pack`, ~2× меньше JSON-текста); каждая
  сборка/удаление — одна транзакция; инкрементальная сборка —
  INSERT новых чанков без переписывания старых (terms-апгрейд —
  только для чанков без terms); `load()` возвращает тот же
  dict-формат, что был у JSON-индекса (search/agent/UI без
  изменений); **авто-миграция из legacy `index.json`** при первой
  сборке (одна транзакция, json-файл удаляется).
- `studio/backend/main.py` — `create_app(agent, kb)` (DI) + 8
  маршрутов `/api/kb/*` (400/404/409/502 с RU-detail).
- `studio/backend/agent.py` — `StudioAgent(..., kb=None)`,
  `build_kb_block(query, top_k)` (top-k, окно выдержки ≤ 300 символов,
  центрируется на характерном токене запроса; блок «База знаний» с
  источником `file · section`; нет индекса/сбой → пустой блок, чат
  жив, лог `[KB]`); RAG-контекст ответа (`rag_context`) сохраняется в
  assistant-сообщении (инспектор в UI, в LLM-пейлоад не уходит);
  settings читаются **на каждый запрос**; `agent_loop=false` → без
  `tools`/tool-loop, без MCP-каталога дня 20.
- `studio/backend/requirements.txt` — + `python-multipart`.
- Фронтенд: `src/api.ts` — KB-хелперы; `components/KbTab.tsx` (новый)
  — вкладка «База знаний»: «Включить» (тумблеры RAG/цикл-агента,
  select реранкера, recall 1..200, Топ-K), «Индексация» (стратегия +
  эмбеддер + «Индексировать» + прогресс), «Файлы» (upload, удаление,
  «Очистить базу»), «Статистика», «Сравнение стратегий» (таблица
  fixed vs structural: чанки, avg/max символов, hit@3, precision@3,
  MRR; активная подсвечена), «Поиск по базе» (top-5: чип score +
  `file · section` + отрывок; при `reranked` — чип `rerank_score` +
  «из #N»); `RagContextInspector` в `ChatPanel.tsx` (RAG-контекст под
  assistant-сообщением); `TokenGauge.tsx` (новый) — SVG-кольцо
  заполнения лимита контекста в шапке чата; `ContextPanel.tsx` /
  `state.tsx` — вкладка `'kb'` после «Инвариантов».
- `scripts/e2e_day21.py` (новый, stdlib, :8105) — гибрид (паттерн
  e2e_day17–20): **Part A** — офлайн-детерминированное ядро, **15
  шагов** (корпус, оба чанкера, hash-сборка/пересборка/поиск, **A4b**
  — миграция `index.json` → `index.db`, settings-валидация, все
  `/api/kb/*` через TestClient, A8b инкрементальная сборка +
  build-status, A8c удаление/wipe, A8d гибридный поиск с редким
  токеном (BM25 substring), A8e 2-этап с fake cross-encoder,
  `agent_loop` с fake-MCP, RAG on/off в LLM-payload), MUST PASS;
  **Part B** — live (uvicorn :8105, реальный LLM + API-эмбеддер +
  API-реранкер), best-effort. Port-busy — ожидание до 10 мин (не
  убивает чужой сервер), cleanup всегда.
- `.gitignore` — `data/kb/` (index.db, settings.json, uploads/).
- Тесты: `studio/backend/tests/test_kb.py` (64, включая `_IndexStore`:
  BLOB roundtrip 256/4096, миграция, incremental без full-rewrite,
  corrupt db → None), `test_kb_api.py` (19),
  `studio/frontend/tests/kb-tab.test.tsx` (6),
  `token-gauge.test.tsx`.

## Исправления после основного релиза

- **В индекс попадают ТОЛЬКО загрузки из «+ Добавить файл»**
  (`data/kb/uploads/`). По требованию пользователя: файлы, не
  добавленные кнопкой, индексироваться не могут — в списке документов
  после «Индексировать» оказывались ~80 документов репозитория.
  Корпус (`kb.corpus_files`) — только `uploads/*` (whitelist
  `UPLOAD_EXTS`, путь «uploads/\<имя>»); gold-метрики считаются по
  gold-файлам `GOLD_QUERIES`, присутствующим в корпусе (uploads-only
  корпус → обычно 0 пригодных запросов → нули; механизм сохранён).
- **Хранилище индекса: JSON → SQLite.** JSON с inline-векторами
  разрастался при dim 4096 (~30 KB текста на чанк) и переписывался
  целиком на каждую операцию; SQLite — BLOB float32, транзакции,
  инкремент без full-rewrite. Авто-миграция legacy-`index.json`.
  Пойманный e2e-баг: `model` как plain string в `meta` ломал
  `load()` на реальном api-индексе — исправлено + регресс-тест.
  Бэкендские docstring/README синхронизированы.

## API

| Метод | Путь | Назначение |
| --- | --- | --- |
| GET | `/api/kb/stats` | Статистика индекса (strategy, embedder, dim, built_at, stats, comparison, files, `reranker_key_configured`); 404 — «Индекс не построен» |
| GET | `/api/kb/uploads` | Список загруженных файлов (работает без индекса) |
| POST | `/api/kb/index` | Сборка индекса `{strategy: fixed\|structural, embedder: hash\|api}` (full/incremental/auto); 400 — RU-detail / нет ключа `GPUSTACK_KEY_EMBED`, 409 — сборка идёт, 502 — сбой эмбеддинг-API |
| GET | `/api/kb/build-status` | Прогресс сборки (фаза, done/total) |
| POST | `/api/kb/upload` | Upload в `data/kb/uploads/` (multipart «file», whitelist, basename-safe) → `{ok, file, size}`; 400 — неподдерживаемый формат |
| GET | `/api/kb/search?q=&k=5` | 2-этапный top-k: этап 1 — гибридный (вектор + BM25, RRF) top-`rag_recall`, этап 2 — cross-encoder (если `reranker=api`) → `{results: [...], recall_total, reranked}`; 400 — пустой q / сбой БЗ, 404 — индекс не построен |
| DELETE | `/api/kb/uploads/{name}` | Удалить файл: с диска + его чанки из индекса + stats; 400 — basename-гард, 404 — файла нет, 409 — идёт сборка |
| DELETE | `/api/kb` | Очистить базу: все uploads + `index.db` (settings сохраняются); 409 — идёт сборка |
| GET / POST | `/api/kb/settings` | Настройки БЗ `{agent_loop, rag, rag_top_k, strategy, embedder, reranker, rag_recall}` / частичное обновление; 400 — RU-detail |

## Проверка задания

Бэкенд — **505 тестов PASS** (офлайн: 64 `kb.py` включая `_IndexStore`
и гибридный поиск/реранкер, 131 agent включая 3 `rag_context`,
36 kb-API). Фронтенд — **263 теста PASS** (Vitest) + `tsc -b` + build
clean. E2E `scripts/e2e_day21.py` на этой машине: **28 PASS / 0 FAIL /
0 SKIP** (Part A 15/15 офлайн-детерминированно, включая A4b
миграцию; Part B live: индекс `index.db` — 372 чанка, dim 4096,
реальные эмбеддинги; RAG-чат B5 — ответ «15» на вопрос про
`TOOL_LOOP_CAP`, KB-блок подтверждён в журнале LLM-запросов; B7
live-реранкер — `reranked=true`, этап 1 нашёл пасхалку
`stage1_rank=2`, `rag_context` в сообщении; `agent_loop=false` → без
`tools` в payload). Честный live-факт: иголка в длинном чанке
cross-encoder'ом не всегда поднимается (этап 1 №2 → реранк №10) —
этапы решают разные задачи (BM25 — редкий токен, реранкер —
precision на общих запросах). Сравнение стратегий: на полном корпусе
репозитория (до исправления, 8 gold-запросов, api-эмбеддинги)
**fixed hit@3 = 0.5, structural hit@3 = 0.25** — structural-чанки
больше и их меньше (целостность секции), на том gold-наборе по
метрикам поиска впереди fixed; после ограничения корпуса
uploads-only gold-файлы в корпусе отсутствуют → нули (механизм
сохранён, таблица в UI). Демо-видео (20 с, live): вкладка «База
знаний» (статистика + сравнение + поиск) → вопрос «Какая модель
телефона была у героя книги?» → ответ модели «iPhone 17 Pro Max
(Источник: uploads/book.txt)» + RAG-инспектор → журнал запросов
(KB-блок в system-промпте). На диске оставлен собранный live-индекс:
`data/kb/index.db` (structural, api, `qwen3-vl-embedding-8b`,
dim 4096; gitignored runtime-артефакт).

## Безопасность

Секреты — только в `.env` (корень, в `.gitignore`). Индекс и
загрузки — `data/kb/` (в `.gitignore`): ни векторов, ни текстов
документов в git не попадает. Предкоммитный скан: значения ключей из
`.env` в треке и в изменяемых файлах отсутствуют (0 утечек; в коде и
доках — только имена env-переменных и публичный endpoint). В
`mcp_servers.json` — плейсхолдеры `{VAR}` (паттерн дня 16).

---

# Release Notes — day20-mcp-orchestration (день 20)

Ветка: [`day20-mcp-orchestration`](https://github.com/imarkelov/ai_advent_challenge/tree/day20-mcp-orchestration)
(от `day19-mcp-pipeline`).

## Что в релизе

**Orchestration MCP.** Мультитул-серверы дней 17–19 разбиты на
**10 едицельных локальных MCP-серверов** (1 сервер = 1 тул, без дублей;
`task_manager.py`/`news_weather.py`/`pipeline_tools.py` удалены, общий
каркас `_mcp_base.py`), агентская маршрутизация по серверам:
**always-префикс** `{server}__{tool}` в именах тулов + **каталог
подключённых серверов** в system-промпте, лимит tool-loop 5 → **15**
(`TOOL_LOOP_CAP`), реестр 12 дефолтов с миграцией, бейджи «server · tool»
в шапке «Шаги агента». Проверка задания — 10-шаговый кросс-серверный
флоу в e2e (порт 8104).

- `studio/mcp_servers/_mcp_base.py` (новый) — общий каркас едицельных
  stdio-серверов (JSON-RPC 2024-11-05, только stdlib):
  `run_server(server_name, tool, call)`, контракт
  `call(args) -> (payload: dict, is_error: bool)`; `initialize`
  (serverInfo `version: "1.0"`) / `tools/list` (ровно 1 тул) /
  `tools/call`; notification без ответа; `-32601` / `-32700`; исключение
  в `call` → `isError`, процесс жив. При старте `sys.stdout`/`stderr`
  реconfigure'ятся с `errors="replace"` — cp1251-пайп + символы вне cp1251
  (U+2011, эмодзи из реального контента) больше не убивают MCP-процесс
  (паттерн фикса дня 18 из `agent.py`; фикс по живому багу из Task 10).
- 10 едицельных серверов (новые, stdio, `[sys.executable, ...]`, без
  npx): `weather`/`get_weather` (Open-Meteo, `city?` Самара),
  `news`/`get_news` (vc.ru/habr/tproger, top-5, дедуп),
  `digest_make`/`make_digest` (сбор + сохранение в `data/digests/`),
  `digest_read`/`get_latest_digest` (файл → GitHub API → `isError`),
  `task_create`/`create_task` (title required), `task_get`/
  `get_task_details` (task_id required), `digest_search`/`search`
  (локальный поиск по `data/digests/*.json`, топ-20, поле `text`),
  `digest_summarize`/`summarize` (детерминированная экстрактивная
  сводка, без LLM), `file_save`/`saveToFile` (`md|txt|json|pdf`,
  `pdf_writer` дня 19, traversal-safe), `habr_news`/`get_habr_news`
  (темы `testing`/`ai`, word-boundary-фильтр по заголовку, `limit` до
  50, sort `published` desc). Live-источники — деградация в `isError`,
  процесс не падает.
- `studio/mcp_servers/_tasks_store.py` (новый) — file-backed хранилище
  задач `data/tasks.json` (в `.gitignore`): seed `TASK-42`/`TASK-7`
  (задачи дня 17), новая задача — `TASK-<max+1>` (первая — `TASK-43`),
  атомарная запись, env `TASKS_FILE`.
- Удалены: `studio/mcp_servers/task_manager.py`, `news_weather.py`,
  `pipeline_tools.py` и их тесты (тулы перенесены 1:1 в едицельные
  серверы; `collector.py`/`pdf_writer.py` переиспользуются).
- `studio/backend/mcp.py` — реестр **12 дефолтов** (Firecrawl, Git +
  10 локальных); **идемпотентная миграция** `_ensure_defaults_locked`:
  старые `Task Manager`/`News & Weather`/`Pipeline Tools` удаляются по
  имени (открытые сессии закрываются), недостающие дефолты добавляются,
  custom-серверы не трогаются; `tools()` — записи дополнены полем
  `server_name`.
- `studio/backend/agent.py` — always-префикс: `_llm_tools()` — имена в
  LLM-payload всегда `{slug}__{tool}` (`_mcp_slug`:
  `re.sub(r"[^a-z0-9]+","_",name.lower()).strip("_")`), `tool_map`
  обратного маршрута; `MCP_TOOLS_RULE` (v2) + `_mcp_catalog_block()` —
  каталог серверов в system-промпте; кап `_tool_loop_cap()` — env
  `TOOL_LOOP_CAP` (дефолт **15**, читается на каждый вызов);
  assistant-`tool_calls`/`role:"tool"` хранятся с префиксированными
  именами (routing proof в истории диалога).
- `studio/frontend/src/components/ChatPanel.tsx` — бейджи в шапке «🧩
  Шаги агента»: `formatToolName` режет имя по первому `__` →
  `server · tool`, полное имя — `title={n}` на hover; чипы StepRow
  сохраняют полное префиксированное имя.
- `scripts/e2e_day20.py` (новый, stdlib, :8104): **Part A** —
  офлайн-детерминированное ядро (fake-LLM + **реальные MCP-субпроцессы**
  всех 10 серверов, сеть отрезана `_NO_NET` 127.0.0.1:1): 10-шаговый
  флоу `task_create → weather → news → digest_make → digest_read →
  habr_news → digest_search → digest_summarize → file_save → task_get`;
  assert на порядок `{server}__{tool}`-сообщений, маршрутизацию (spy
  `reg.call_tool` == FLOW), передачу данных, кап (→ SSE error «Tool-loop:
  превышен лимит итераций»). MUST PASS. **Part B** — live (uvicorn :8104,
  реальный LLM), best-effort (PASS/SKIP, не FAIL). e2e дня 17–19 — на
  едицельных серверах (Part A 6/6, 6/6, 12/12).
- REST-роуты не меняются; `GET /api/mcp/tools` — добавлено поле
  `server_name`.

## API

Новых REST-эндпоинтов нет. Изменение: `GET /api/mcp/tools` —
`[{server, server_name, name, description, input_schema}]` (добавлено
`server_name`).

## Проверка задания

Бэкенд — 393 тестов PASS (офлайн). Фронтенд — 219 тестов PASS (Vitest)
+ `tsc -b` + `npm run build` clean. E2E: `e2e_day17.py` — Part A 6/6,
Part B PASS (14/0/0); `e2e_day18.py` — Part A 6/6, Part B PASS
(14/0/0); `e2e_day19.py` — Part A 12/12, Part B SKIP (18 PASS / 0 FAIL
/ 1 SKIP); `e2e_day20.py` — Part A 19/19, Part B SKIP (26 PASS / 0 FAIL
/ 1 SKIP: инфраструктура green, модель исчерпала 15 итераций на живом
10-серверном сценарии — поведение модели, не FAIL). Live: `GET
/api/mcp/servers` на живом dev-процессе — ровно 12 серверов, старых 3
имён нет (миграция на реальных `data/mcp_servers.json`). Live-
маршрутизация (демо-видео): `[LLM Decision] digest_search__search
{"query": "Самара"}` → `digest_summarize__summarize {"text": <поле text
из search>}` — префикс-имена + передача данных между серверами.
Демо-видео: `day20-mcp-orchestration-demo.mp4` (папка «AI Advent
Challenge - видео» на рабочем столе) — ссылка в `LINKS.md`.

## Коммиты

- `c63380d` — final-review — fixture references day-20 single-purpose server
- `f02719c` — final-review — unique default ids, prefixed few-shot, spec habr patterns
- `7131696` — final-review — sources arg in day-20 table, full commit list in RELEASE
- `6fd2181` — README/RELEASE/openspec — Orchestration MCP day
- `ad1aff6` — agent-steps badges render MCP tools as 'server · tool'
- `313ce1e` — e2e_day20 Part B — SSE model error → SKIP (not FAIL); remove duplicate _NO_NET injection
- `7cccc67` — _mcp_base stdout errors=replace (non-cp1251 payload chars crash server)
- `3f2854a` — e2e_day20 — 10-server cross-flow (Part A deterministic + Part B live :8104)
- `599335d` — e2e day17-19 on single-tool servers; remove task_manager/news_weather/pipeline_tools
- `2068ab0` — agent routing — always server__tool prefix, MCP catalog in system prompt, TOOL_LOOP_CAP=15, llm names stored in history
- `f2b0312` — MCP registry — 12 defaults (10 single-tool), old-server migration, server_name in tools()
- `9e75ead` — habr_news MCP server (testing/ai topics, word-boundary filter)
- `533481b` — single-tool MCP servers digest_search/digest_summarize/file_save
- `4f47ab7` — gitignore data/tasks.json (runtime task-store state)
- `6636d47` — isolate in-process task tests with temp TASKS_FILE (idempotent, no repo pollution)
- `9348b50` — file-backed tasks store + task_create/task_get servers
- `d1780ed` — single-tool MCP servers digest_make + digest_read
- `592af6b` — single-tool MCP servers weather + news
- `54d2231` — _mcp_base — shared single-tool stdio MCP server skeleton
- `57fcd58` — implementation plan — 12 tasks (servers, registry, agent, e2e, frontend, docs)
- `274b126` — spec fact-check vs collector.py — weather result shape, habr pubDate via local parser, patchable fetch
- `4e6cb19` — spec fact-check vs day17/18/19 server code — version 1.0, task/digest/search result shapes
- `bc5db5e` — spec self-review — flow covers all 10 servers (digest_read step), tighten habr filter boundaries
- `04ea37f` — design spec — 10 single-tool MCP servers + agent routing (always-prefix, server catalog, cap 15)

---

# Release Notes — day19-mcp-pipeline (день 19)

Ветка: [`day19-mcp-pipeline`](https://github.com/imarkelov/ai_advent_challenge/tree/day19-mcp-pipeline)
(от `day18-mcp-digest`).

## Что в релизе

**Композиция MCP-инструментов (pipeline).** Несколько MCP-инструментов,
комбинируемых в пайплайн: `search` (получает данные) → `summarize`
(обрабатывает) → `saveToFile` (сохраняет результат). **LLM-driven**:
оркестратора в коде нет — модель через tool-loop дня 17 (лимит 5 итераций)
сама решает, какие инструменты вызвать, в каком порядке и сколько. Пайплайн
динамический: «какая погода?» → 1 `search`; «найди, суммаризируй, сохрани в
PDF» → цепочка из 3. `saveToFile` — `format` md/txt/json/pdf; `pdf` — полный
PDF с кириллицей (встроенный TTF), чистый stdlib.

- `studio/mcp_servers/pipeline_tools.py` — **пятый** дефолт реестра MCP
  (stdio JSON-RPC 2024-11-05, только stdlib, паттерн `news_weather.py`):
  3 инструмента — `search(query)` (локальный поиск по
  `data/digests/*.json` дня 18, case-insensitive, топ-20), `summarize(text,
  max_points=8)` (детерминированная экстрактивная сводка, **без LLM**),
  `saveToFile(filename, content, format)` (атомарная запись в
  `data/pipeline/`, basename-санитизация, traversal-safe). Env:
  `PIPELINE_SEARCH_DIR` / `PIPELINE_OUT_DIR` / `PIPELINE_FONT_PATH`. Ошибка
  → `{"error": ...}` + `isError: true`, процесс жив.
- `studio/mcp_servers/pdf_writer.py` — stdlib-PDF-движок (только stdlib,
  ~450 строк): TTF-парсер `struct` (`head`/`hhea`/`maxp`/`hmtx`/`cmap` 4+12
  /`name`; `glyf` не парсится), PDF 1.4 (A4 595×842, 11/14pt, межстрочный
  1.45, перенос по hmtx, мультистраницы), шрифт Type0/`Identity-H` →
  CIDFontType2 (`/FontFile2` — сырой TTF, `/CIDToGIDMap /Identity`,
  `/ToUnicode` CMap для кириллицы). `find_default_font()` (env →
  `arial.ttf`/`segoeui.ttf`/`tahoma.ttf` в `C:\Windows\Fonts` → `None`),
  `text_to_pdf(text, title="", font_path=None) -> bytes`, `PdfError`.
  **Детерминизм**: без дат — повторный вызов → идентичные байты.
- `studio/backend/mcp.py` — **Pipeline Tools** как пятый дефолт
  (`[sys.executable, .../pipeline_tools.py]`, stdio, без npx). Порядок
  дефолтов: Firecrawl, Git, Task Manager, News & Weather, Pipeline Tools.
  Правка 3 тестов-списков-дефолтов (5-е имя).
- Тесты (новые, офлайн): `studio/backend/tests/test_pdf_writer.py`
  (7: структура PDF, xref, мультистраницы, детерминизм, кириллица,
  `PdfError`), `studio/backend/tests/test_pipeline_tools.py` (12: subprocess
  — 3 tools, `search` локально, `summarize` детерминизм, `saveToFile` 4
  формата + traversal + атомарность, error-ветки, 5-й дефолт).
- `scripts/e2e_day19.py` — гибрид (stdlib, :8103): **Part A** —
  детерминированное ядро в-процессе (без uvicorn/сети): `MCPRegistry` +
  реальный subprocess `pipeline_tools.py` + `StudioAgent`/`MockTransport`
  fake-LLM со скриптованной цепочкой `search → summarize → saveToFile`;
  assert на порядок tool-сообщений, **передачу данных** (выход этапа N ⊂
  вход N+1) и PDF на диске (`%PDF-1.4` + `/ToUnicode`) — MUST PASS.
  **Part B** — live (uvicorn :8103, реальный LLM), best-effort: «найди
  записи про Самара, суммаризируй, сохрани в PDF» → PASS (≥1 tool-сообщение
  + PDF на диске) или SKIP (поведение модели). Exit 0 для PASS/SKIP, 1 для
  FAIL.
- `studio/backend/agent.py` — `MCP_TOOLS_RULE`: при подключённых
  MCP-серверах в system-промпт добавляется правило «модель сама выбирает
  инструменты и **сама передаёт данные между ними** (результат вызова —
  входом в аргументы следующего), доводит цепочку до конца без
  согласования» (+ тесты). Без подключённых серверов правило не
  инжектится (регресс).
- `pipeline_tools.py` — результат `search` дополнен полем `text`
  (склейка `title — snippet` построчно): модель копирует его в
  `summarize.text` — передача данных между тулами на стороне модели.
- **UI: шаги агента** (`ChatPanel.tsx`, `Sidebar.tsx`, `styles.css`,
  `state.tsx`):
  - подряд идущие tool-сообщения одного ответа (`tool_calls` /
    `role:"tool"`) группируются в блок `🧩 Шаги агента · N` — свёрнут по
    умолчанию, клик — разворачивает; **каждый шаг — отдельно
    сворачиваемая строка** (`StepRow`: `🔧`/`↳` + payload в `<pre>`);
  - бейджи MCP-тулов в шапке блока (уникальные имена, порядок первого
    появления: `search` `summarize` `saveToFile`);
  - оценка токенов рядом с каждой кнопкой сворачивания: `estTokens`
    (эвристика 0.44 tok/символ, калибровка qwen3.8-27b), в шапке блока —
    сумма;
  - режимы переименованы: `Чат → Диалог`, `Задача → Проект`
    (placeholder «Опишите проект…», флаг «Проект использовался»);
  - сайдбар: 5 самых свежих диалогов + «Показать ещё N (старые) ▾» /
    «Свернуть ▴» (в режиме выбора — полный список);
  - `state.tsx`: `reloadDialogue()` после `done` — tool-сообщения не
    идут в SSE, лента перечитывается с сервера.
- REST-роуты не меняются: tool-loop дня 17 сам отдаёт 3 инструмента
  модели, композиция целиком на стороне модели.

## API

Новых REST-эндпоинтов нет. Реестр MCP дня 16 получает пятый дефолт
`pipeline-tools` (`[sys.executable, <repo>/studio/mcp_servers/pipeline_tools.py]`)
— подключение через существующие `/api/mcp/servers/*`. В чате — обычный
tool-loop дня 17 (модель сама вызывает `search`/`summarize`/`saveToFile`).

## Проверка задания

Бэкенд — 372 теста PASS (офлайн; 1 pre-existing live-network fail
`test_live_fetch_weather` — Open-Meteo недоступен с машины, окружение,
не продукт). Фронтенд — 218 тестов PASS (Vitest) + `tsc -b` + `npm run
build` clean. E2E `scripts/e2e_day19.py`: **18 PASS, 0 FAIL, 1 SKIP** —
Part A 12/12 (детерминированная цепочка + передача данных + валидный PDF
с кириллицей); Part B — вся инфраструктура green, единственный SKIP =
live PDF-артефакт (модель не доводит цепочку до `saveToFile` автономно —
best-effort, не FAIL). Валидация кириллицы: TTF `cmap` → ненулевые GID
для всех кириллических кодов, `/ToUnicode` покрывает использованный
диапазон, PDF содержит `/FontFile2` + `/Identity-H` + `/CIDToGIDMap`.

**Live-проверка в браузере** (qwen3.8-27b, реальный MCP subprocess):
1. «Найди новости про ИИ, сделай суммаризацию и сохрани в файл» →
   `search → summarize`, модель копирует результат `search` (поле `text`)
   в `summarize.text` — передача данных подтверждена посимвольно.
2. «Сохрани файл новостей про ИИ без суммаризации» → модель **автономно**
   выполнила полную цепочку `search → summarize(max_points=5) →
   saveToFile`; файл `data/pipeline/news_digest.md` (643 B) на диске.
3. UI: блок «🧩 Шаги агента · 3» с бейджами `search summarize saveToFile`,
   сворачивание блока и каждого шага по отдельности, оценка токенов
   рядом с каждой кнопкой; список диалогов 5 + «Показать ещё 18 (старые)».

Демо-видео: `day19-mcp-pipeline-demo.mp4` (папка «AI Advent Challenge —
видео» на рабочем столе) — ссылка в `LINKS.md`.

## Коммиты

- `ff110a2` — pipeline_tools stdio MCP server (search/summarize/saveToFile) + stdlib PDF writer
- `df63060` — MCP_TOOLS_RULE — LLM-driven tool composition, search text field for data passing
- `efaa753` — e2e_day19 — deterministic chain (data passing + PDF) + live best-effort
- `d17dd12` — feat(ui): agent steps — per-step collapse, MCP tool badges, token estimates, tabs, 5-dialogs sidebar
- `af6b0f1` — docs: day19 README/RELEASE/plan/openspec + LINKS.md

---

# Release Notes — day18-mcp-digest (день 18)

Ветка: [`day18-mcp-digest`](https://github.com/imarkelov/ai_advent_challenge/tree/day18-mcp-digest)
(от `day17-mcp-tool-loop`).

## Что в релизе

**Периодический дайджест 24/7.** MCP-инструмент с периодическим
выполнением: сохраняет данные (JSON), выполняется по расписанию
(GitHub Actions cron), возвращает агрегированный результат. Агент
отвечает на «покажи последнюю сводку» через tool-loop дня 17 — модель
сама вызывает `get_latest_digest`.

- `studio/collector.py` — общий stdlib-коллектор (только stdlib,
  `urllib`/`xml`/`json`): `collect_digest` (погода Open-Meteo
  (геокодинг + `current` + `daily.2d`, WMO-code → RU) + новости
  vc.ru/habr/tproger — top-5 на источник, дедуп по нормализованному
  заголовку), `build_summary` (город + число новостей), `save_digest`
  (атомарно: tmp + `os.replace`; `last-digest.json` перезапись;
  `history.json` кап 96 = 4 дня × 6/ч), CLI `--out DIR --city C`.
  Сбой источника — его поле `{"error": ...}`, дайджест не гибнет.
- `studio/mcp_servers/news_weather.py` — четвёртый дефолт реестра MCP
  (stdio JSON-RPC 2024-11-05, только stdlib, паттерн
  `task_manager.py`): 4 инструмента — `get_weather(city?)`,
  `get_news(source?)`, `make_digest(city?)` (сбор + запись JSON),
  `get_latest_digest()` (локальный файл → фолбэк GitHub API →
  `isError` «Дайджест недоступен»).
- `data/digests/` (корень репозитория, в git — «message bus»):
  `last-digest.json` + `history.json` (кап 96).
- `.github/workflows/digest.yml` — cron `0 */6 * * *` (UTC) +
  `workflow_dispatch`, ubuntu-latest, Python 3.12,
  `if: github.ref == 'refs/heads/master'`: `collector.py --out
  data/digests` → проверка схемы → `git add data/digests` → коммит
  `digest: <id>` → push с retry (3×). Без API-ключей (Open-Meteo и RSS
  открытые). **Cron живёт только в master — ветку нужно смержить.**
- `scripts/e2e_day18.py` — гибрид: Part A (офлайн, 6 assert, MUST
  PASS) — реальный subprocess `news_weather.py` через `MCPRegistry`
  (connect → 4 tools, `make_digest`/`get_latest_digest`
  (source=local, id совпадает), `collect_digest` (детерминированный
  id/generated_at), `save_digest` ×2 (история=2), CLI (exit 0); Part B
  (live, uvicorn :8102, реальный LLM), best-effort — диалог → decline
  профиля → «Покажи последнюю сводку (дайджест)» → модель сама
  вызывает `get_latest_digest` → `done.answer` содержит сводку.
  Exit 0 для PASS/SKIP, 1 для FAIL.
- `agent.py` — фикс cp1251: при импорте `sys.stdout`/`sys.stderr`
  реconfigure'ятся с `errors="replace"` — print лог-тегов
  (`[Final Response]` и др.) не рвёт SSE-стрим, если ответ содержит
  символы вне cp1251 (❌, эмодзи) (было: `UnicodeEncodeError` в
  SSE-генераторе → обрыв без `done`, клиент `IncompleteRead`).

## API

Новых REST-эндпоинтов нет. Реестр MCP дня 16 получает четвёртый
дефолт `news-weather` (`[sys.executable, <repo>/studio/mcp_servers/news_weather.py]`)
— подключение через существующие `/api/mcp/servers/*`. В чате —
обычный tool-loop дня 17 (модель вызывает `get_latest_digest`).

## Проверка задания

Бэкенд — 350 тестов PASS (офлайн: collect_digest/save_digest/RSS/дедуп/
CLI + `news_weather` на реальном subprocess + 4-й дефолт + регресс
дней 1–17). Фронтенд — без изменений (регресс `npm test`/`tsc -b`).
E2E `scripts/e2e_day18.py`: Part A 6/6 PASS; Part B — PASS (модель
вызвала `get_latest_digest`, ответ содержит сводку, `source: local`).

## Коммиты

- `d9e4eeb` — openspec change (proposal/design/spec/tasks)
- `a8a6526` — collector core: collect_digest + build_summary
- `56784ee` — collector: save_digest, атомарная запись, история кап 96
- `a2be5a8` — collector: RSS-фикстуры, дедуп, live-маркеры
- `c74ae2b` — collector CLI --out/--city
- `161eaa0` — news_weather stdio MCP server (4 tools)
- `f93b5fd` — News & Weather как четвёртый дефолт реестра
- `acfe45d` — GitHub Actions cron 6h (digest.yml)
- `1983d8e` — fix: cp1251-stdout не рвёт SSE-стрим
- `2e3ce57` — e2e_day18 + data/digests (первый дайджест)

---

# Release Notes — day17-mcp-tool-loop (день 17)

Ветка: [`day17-mcp-tool-loop`](https://github.com/imarkelov/ai_advent_challenge/tree/day17-mcp-tool-loop)
(от `day16-mcp-connect`).

## Что в релизе

**LLM-driven MCP tool-loop.** Инструменты подключённых MCP-серверов
уходят в тело LLM-запроса (`tools`, формат OpenAI), модель сама решает
вызвать инструмент (`tool_calls`), агент вызывает его на MCP-сервере,
результат возвращается модели сообщением `role: "tool"` в цикле до
финального ответа (кап 5 итераций). Пути дня 16 (вызов по команде
`/сервер тул`, system-маркер `mcp_tool`) не меняются — tool-loop
добавлен поверх.

- `studio/mcp_servers/task_manager.py` — новый stdio MCP-сервер
  (JSON-RPC 2024-11-05, только stdlib, без npx): in-memory задачи
  (TASK-42 `in_progress`/migor, TASK-7 `done`), инструменты
  `get_task_details` (required `task_id`), `create_task` (required
  `title`); «не найдено» → `{"error": "Задача не найдена: <id>"}` +
  `isError: true`; неизвестный метод → JSON-RPC -32601.
- `mcp.py` — Task Manager как третий дефолт реестра
  (`[sys.executable, <repo>/studio/mcp_servers/task_manager.py]`);
  `connect()` на успех → `[MCP Init] {name}: {n} инструментов:
  {список}` (stdout, flush).
- `agent.py` — tool-loop в `ask_stream` (кап 5 итераций): `tools` из
  подключённых серверов (коллизии имён — префикс `{server_id}__`),
  агрегация `delta.tool_calls` по `index`, assistant-сообщение с
  `tool_calls` + `role: "tool"`-сообщения (`tool_call_id`, `name`) в
  диалоге, цикл с повторными guards; превышение капа → SSE `error`
  «Tool-loop: превышен лимит итераций (5)»; ошибка инструмента → текст
  ошибки в tool-сообщении (цикл продолжается, без crash); без
  подключённых серверов `tools` в payload нет (регресс дня 16
  `test_chat_payload_has_no_mcp_tools`); журнал — запись на каждую
  итерацию, usage суммируется.
- Консоль-логи этапов: `[MCP Init]`, `[LLM Decision]`, `[MCP Response]`,
  `[Final Response]` (`print(..., flush=True)`).
- `state.tsx`/`ChatPanel.tsx` — служебные `role: "tool"`-сообщения
  хранятся в памяти диалога, но не рендерятся чат-пузырями.
- `scripts/e2e_day17.py` — гибрид: Part A — детерминированное ядро
  в-процессе (StudioAgent + `httpx.MockTransport` fake-LLM, эмитирующая
  `tool_calls` + реальный subprocess `task_manager.py` через
  `MCPRegistry`; 6 assert); Part B — live (uvicorn :8101, реальный LLM
  GPustack), best-effort SKIP; exit 0 для PASS/SKIP, 1 для FAIL.

## API

Новых REST-эндпоинтов нет; меняется только тело `POST /api/chat`:
`tools` в LLM-запросе при подключённых серверах, `tool_calls` /
`tool_call_id` проходят в историю `messages`. SSE-протокол не
расширяется (delta/done/error/invariant_violation — как есть).

## Проверка задания

Бэкенд — 330 тестов PASS (`pytest -q`, офлайн). Фронтенд — 212 тестов
PASS (Vitest) + `tsc -b` clean. E2E `scripts/e2e_day17.py` на этой
машине: Part A 6/6 PASS; Part B — SKIP (GPustack недоступен:
SSL CERTIFICATE_VERIFY_FAILED — окружение, не продукт).

## Коммиты

- `c7e341f` — mock task manager stdio MCP server
- `7f56b93` — task manager in registry defaults + `[MCP Init]` log
- `38df527` — LLM tool-calling loop in StudioAgent
- `cb69817` — UI: service tool messages not rendered
- `6cf4fa4` — e2e_day17 (deterministic core + live best-effort)

---

# Release Notes — day16-mcp-connect (день 16)

Ветка: [`day16-mcp-connect`](https://github.com/imarkelov/ai_advent_challenge/tree/day16-mcp-connect)
(от `day15-plan-review`).

## Что в релизе

**Подключение внешних MCP-серверов (Model Context Protocol) к Студии.**
Студия стартует с фиксированным реестром (Firecrawl — web-поиск, Git —
репозиторий; stdio-процессы `npx …`, поддерживается и streamable-http):
пользователь нажимает «Подключить» — и видит инструменты сервера в
отдельной панели «MCP» (своя кнопка «🧩» в шапке чата рядом с «⚙», панель
выезжает справа; в настройках «⚙» вкладки MCP больше нет).
День 16 — подключение, отключение, просмотр и вызов: инструмент подключённого
сервера вызывается из чата командой `/имя-сервера имя-тула` (автодополнение,
форма аргументов по `input_schema`); результат сохраняется в диалог и
виден LLM в следующих запросах (tool-loop).

- `mcp.py` — клиент MCP 2024-11-05 (JSON-RPC 2.0): `_StdioSession`
  (pipes, JSON по строкам, `{VAR}`-плейсхолдеры env расширяются из
  окружения на запуске), `_HttpSession` (streamable-http,
  `MCP-Protocol-Version`/`MCP-Session-Id`, SSE-кадры `data: {json}`),
  `MCPClient` (`connect` = `initialize` + `tools/list`, `call_tool` —
  tools/call), `MCPRegistry` (реестр + runtime-статусы
  `idle`/`connected`/`error` + `tools_count`).
- `memory.py` — CRUD реестра в `mcp_servers.json`; дефолты: Firecrawl, Git.
- `agent.py` — опциональный `mcp` (DI); `close_all()` — shutdown-хук.
- Сбой подключения — `status: error` + текст ошибки, агент не падает;
  повторный connect разрешён (self-heal).
- Определения инструментов (tool-definitions) в тело LLM-запроса НЕ
  инжектятся (регресс-тест); результат вызова сохраняется как
  system-сообщение с маркером `mcp_tool` и уходит в следующие запросы
  (tool-loop).

## API

| Метод | Путь | Назначение |
| --- | --- | --- |
| GET | `/api/mcp/servers` | Реестр MCP-серверов с runtime-статусом (idle/connected/error, tools_count) |
| POST | `/api/mcp/servers` | Добавить сервер `{name, type, command?, url?, env?, enabled?}` → 201 `{server}`; 400 — RU-detail |
| DELETE | `/api/mcp/servers/{id}` | Удалить сервер (404 — не найден) |
| POST | `/api/mcp/servers/{id}/connect` | Подключить (initialize + tools/list) → `{server}`; сбой = status error, 404 — не найден |
| POST | `/api/mcp/servers/{id}/disconnect` | Отключить (закрыть сессию, status → idle, сервер остаётся в реестре) → `{server}`; 404 — не найден |
| GET | `/api/mcp/tools` | Инструменты подключённых серверов `[{server, name, description, input_schema}]` |
| POST | `/api/mcp/servers/{id}/tools/{tool}` | Вызвать инструмент (tool-loop) `{dialogue_id, arguments}` → `{"ok": true}`; результат — system-сообщение с маркером `mcp_tool` в диалоге (видно LLM); 400 — сервер не подключён / arguments не объект, 404 — сервер или диалог не найден |

## Проверка задания

Бэкенд — 320 тестов PASS (stdio-транспорт на fake-процессе, http-транспорт
на `httpx.MockTransport` — JSON/SSE/session-id/ошибки, реестр, API-роуты,
вызов тула `call_tool` + роут `/tools/{tool}`, отключение (`disconnect`:
сессия → idle, сервер остаётся в реестре), регресс: tool-definitions MCP
вне LLM-payload, launcher: `npx.cmd` через `shutil.which`).
Фронтенд — 211 тестов PASS (включая отдельный overlay «MCP» по кнопке «🧩»
в шапке, автодополнение `/`, форму по `input_schema`, карточку результата и
тумблер «Подключить»/«Отключить»); tsc и `npm run build` — clean. E2E — 34 PASS,
4 SKIP, 0 FAIL: MCP-блок детерминированный (mock stdio-сервер `python -c`
без сети: POST 201 → connect → 2 tools → disconnect (idle) + reconnect → tool call 200 + `mcp_tool`-сообщение
в диалоге → 400/404 → connect-404 → DELETE 200/404); live Firecrawl —
best-effort SKIP (npx недоступен); чат/задачи — SKIP (GPustack недоступен
из-за SSL-сертификата Python).

## Безопасность

Секреты — только в `.env` (корень репозитория). В `mcp_servers.json`
для переменных окружения хранятся плейсхолдеры `{VAR}` — значение
подставляется из окружения в памяти при запуске процесса, в файл не
пишется. Таймаут подключения — `MCP_CONNECT_TIMEOUT` (дефолт 30 c).

---

# Release Notes — day13-task-state-machine (День 13b)

Ветка: [`day13-task-state-machine`](https://github.com/imarkelov/ai_advent_challenge/tree/day13-task-state-machine)
(от `day12-user-profile`).

## Что в релизе

**Задача = запрос пользователя в режиме «задача».** Тумблер чат/задача у поля
ввода (глобальный, состояние в `localStorage`) — в режиме «задача» отправленное
сообщение становится задачей, в режиме «чат» — обычным сообщением. Задача
пер-диалог.

### Unified FSM

Пайплайн `planning → execution (N work-шагов) → validation → done` и FSM из
6 значений: `planning | execution | validation | done | paused | failed`
(`paused`/`failed` — значения стадии, не флаги). Stage-агенты (Планировщик /
Исполнитель / Валидатор / Оркестратор) — один и тот же LLM из конфига с
разными system-промптами; пайплайн ведёт детерминированный код
(`agent.task_run`) — LLM не управляет FSM.

### Пошаговое исполнение

- Планировщик (1 LLM-вызов) — JSON-план из 1–5 work-шагов (best-effort
  парсинг; сбой/не-JSON — фолбэк в один шаг).
- Исполнитель — по LLM-вызову на work-шаг, стримингом (`step_updated` /
  `step_delta`).
- Валидатор — сверка с планом, вердикт `<verdict>pass|fail</verdict>`; fail →
  ретрай execution (максимум 1).
- Оркестратор — финальный синтез. Итого N+3 LLM-вызова (до 2N+3 с ретраем);
  stage/work-вызовы не пишутся в журнал `requests.json`.
- Пауза вступает на границе work-шага: текущий вызов доигрывается, следующий
  не начинается; фиксируется `context_snapshot`. Инструкция на паузе
  инжектится в промпт следующего шага/стадии и используется один раз.
  Продолжение — с сохранённого состояния (выполненные шаги/стадии не
  повторяются). Ошибка LLM — стадия `failed` + `task_failed`; повтор через
  «Продолжить»/новый run (auto-resume к первой невыполненной стадии).

### Карточка процесса в чате

Якорь карточки — user-сообщение с `task_id`. Живая карточка: спавн
stage-агентов (иконка, «спавн N с назад»), чек-лист work-шагов [✓]/[⏳]/[○],
живой бокс вывода текущего шага, сворачиваемые секции стадий (DONE — всё
свёрнуто, FAILED — секция ошибки развёрнута), кнопки Пауза/Продолжить/Повтор.
История после перезагрузки восстанавливается из маркеров сообщений
(`task_id`/`task_stage`/`task_step`).

### Контекст

Task-промпты собираются только из состояния задачи (`description`, `plan`,
`work_steps`, `instruction`); история сообщений чата в task-промпты не уходит.

## Исправления после основного релиза

- **Resume, пока стримится чужой run — не теряется.** Run-слот глобальный:
  если стримится run другого диалога (`taskRunning`), клик «Продолжить» ждёт
  освобождения слота (wait-loop, `sleep(400)`), затем `POST /api/task/resume` +
  `reloadTask` + `runTask`. Раньше resume отправлялся, пока слот занят, и
  состояние бэкенда рассинхронизировалось с фронтендом. `resumeInFlight`
  защищает от двойного клика (двойной «Продолжить»/«Повтор»). Ошибка (задача
  уже не resumable — 400-гард) → перечитывается авторитетная задача.
- **Карточка: кнопка «Продолжить» в зависшем состоянии.** Если задача в
  `execution`/`planning`, но run не идёт (`!running`) — показывается кнопка
  «Продолжить», которая запускает пайплайн через `POST /api/task/run`
  (восстановление из маркеров при перезагрузке).
- **Бренд сайдбара — «◆ День 13»** (было «День 11»), соответствует номеру
  текущего дня.

## API

| Метод | Путь | Назначение |
| --- | --- | --- |
| POST | `/api/task/start` | Создать задачу `{dialogue_id, description}` → 200 `task` + user-маркер; 400 — незавершённая активная задача / пустое описание; 404 — диалог |
| POST | `/api/task/run` | Запустить/продолжить пайплайн — SSE `agent_spawned` / `step_updated` / `step_delta` / `stage_done` (+`verdict`/+`plan`/+`retry`) / `task_paused` / `task_resumed` / `task_done` / `task_failed` / `error`; 400 — нет задачи / завершена |
| POST | `/api/task/pause` | Пауза на границе work-шага |
| POST | `/api/task/resume` | Снять паузу/ошибку |
| POST | `/api/task/instruction` | Инструкция на паузе `{text}` |
| POST | `/api/task/reset` | Сброс задачи |
| GET | `/api/task?dialogue_id=` | Текущее состояние задачи |

Ошибки — 400/404 с RU-detail; `task` — в выдаче `/api/dialogues` и
`/api/dialogues/{id}`. Гард чата: активная непаузанная незавершённая задача →
`POST /api/chat` → SSE `error` «Задача выполняется…».

## Проверка

- Бэкенд — **225 тестов PASS** (`python -m pytest -q`, офлайн, `httpx.MockTransport`).
- Фронтенд — **150 тестов PASS** (`npm test` / `vitest run`).
- Typecheck (tsc) и синтаксис e2e-скрипта — clean.
- E2E — 21/21 PASS (prod-сервер + реальный GPustack): чат, персонализация,
  пошаговый пайплайн задачи с паузой на границе шага.

## Безопасность

Секреты не попадают в git: в репозиторий не коммитятся `.env`, runtime-данные
(`studio/data/`, `*.json` истории/диалогов) и `*.log`. Ключи API берутся
только из `.env`, который в `.gitignore`. В треке и истории только имена
env-переменных (`GPUSTACK_API_KEY`, `GPUSTACK_KEY_DEEPSEEK`,
`GPUSTACK_KEY_GLM`) — без значений.
