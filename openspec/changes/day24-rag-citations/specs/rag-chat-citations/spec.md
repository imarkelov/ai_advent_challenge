## Purpose

Capability «rag-chat-citations» — цитаты, источники и анти-
галлюцинации (день 24) в live-чате Студии поверх RAG дня 21
(`ask_stream` + `kb_block` + `rag_context`) и фильтрации дня 23
(`min_score` уже действует в чате): каждый RAG-ответ несёт
обязательные источники (source + file + section + `chunk_id` + score,
±`rerank_score`/`stage1_rank`) и verbatim-цитаты (focused snippet
≤300 символов ⊂ текста чанка; цитаты генерирует код, не модель);
модель instructed ссылаться на выдержки маркерами [n]
(`CITE_RULE` — только в непустом `kb_block`); при 0 релевантных
чанков (триггер A′: RAG-вкл + индекс есть + 0 чанков при ЛЮБОМ
`min_score`, включая 0) — детерминированный `DONT_KNOW_TEXT`
(0 LLM-вызовов, `usage: None`, `request_id: None`,
`rag_context: {recall_total, reranked, chunks: [], dont_know: true}`);
UI — панель «📖 Источники и цитаты» (вариант B) + красная карточка
«🚫 Не знаю», сосуществование с `RagContextInspector` (оба живы);
маркеры [n] во фронтенде не рендерятся. E2E-гибрид —
`scripts/e2e_day24.py` (порт 8108; Part A MUST PASS офлайн — 10
контрольных вопросов; Part B live best-effort). `kb.py`,
`POST /api/rag/compare` (4 армы дня 23), task-пайплайн, body-схема
`POST /api/chat`, base system prompt, FlowInspector — не меняются.

## ADDED Requirements

### Requirement: RAG-ответ несёт источники и verbatim-цитаты

Система SHALL сохранять в `rag_context` каждого RAG-ответа live-чата
(assistant-сообщение) чанки с обязательными источниками: `source`,
`file`, `section`, `chunk_id` (совпадает с `chunk_id` индекса БЗ) и
`score`; при реранке — дополнительно `rerank_score` и `stage1_rank`.
Поле `chunk_id` добавляется в объект чанка `_kb_context`
**аддитивно** (рядом с rank/file/section/score); имена/типы
существующих полей `rag_context` не меняются. Цитата — verbatim
фрагмент `chunks[i].text` (focused snippet: `text` ⊂ сохранённого
текста чанка из БЗ, `len(text) <= 300`), генерируемый кодом
(`_focus_snippet`), а НЕ моделью. Полный текст чанка в сообщение не
хранится.

#### Scenario: Чанки rag_context несут chunk_id

- **WHEN** RAG-вкл, поиск вернул ≥1 чанка, ответ сохранён
- **THEN** каждый объект в `rag_context.chunks[]` содержит `chunk_id`, равный `chunk_id` результата `search_rag` (из индекса БЗ), плюс `source`, `file`, `section`, `score`; при `reranked` — `rerank_score`/`stage1_rank`; остальные поля `rag_context` (`query`, `reranked`, `recall_total`) не изменены

#### Scenario: Цитата — verbatim, ≤300, ⊂ текста чанка

- **WHEN** RAG-ответ содержит непустые `rag_context.chunks`
- **THEN** для каждого чанка `text` является подстрокой сохранённого текста чанка из БЗ (verbatim, без пересказа) и `len(text) <= 300`; полный текст чанка в сообщение не записывается

#### Scenario: Старое сообщение читается undefined-safe

- **WHEN** фронтенд/история читает сообщение до дня 24 (в чанках нет `chunk_id`, нет `dont_know`)
- **THEN** рендер и инспекторы работают без краха: новые поля обрабатываются как опциональные

### Requirement: [n]-правило цитирования в `kb_block`

Система SHALL добавлять строку правила `CITE_RULE`
(«Ссылайся на выдержки в ответе маркерами [1], [2], … по номерам.
Опирайся только на приведённые выдержки; если выдержки не покрывают
факт — так и скажи.») в конец блока «База знаний» в system-промпте
**только когда блок непустой** (есть выдержки). При пустом результате
поиска, RAG-off, нет индекса — правило в system-промпт не добавляется.
Модель instructed ссылаться на выдержки маркерами [1..k]; во
фронтенде маркеры [n] НЕ рендерятся (панель источников автономна от
текста ответа), комплаенс модели маркерам в e2e НЕ ассертится.

#### Scenario: Непустой kb_block несёт правило

- **WHEN** RAG-вкл и retrieval вернул ≥1 чанка
- **THEN** system-сообщение LLM-payload содержит `kb_block` с нумерованными выдержками (`1. [file · section] — …`) и строкой `CITE_RULE` в конце блока (ассерт по константе `CITE_RULE`)

#### Scenario: Пустой результат / RAG-off — правила нет

- **WHEN** retrieval вернул 0 чанков (в т.ч. dont-know-ветка), либо диалог `rag=false`, либо индекса нет
- **THEN** `CITE_RULE` в system-промпт не добавляется; при RAG-off retrieval не выполняется

### Requirement: Dont-know по триггеру A′ (0 LLM-вызовов)

Система SHALL отвечать dont-know, когда **все** условия истинны:
RAG-вкл (эффективный per-диалог режим), поиск **успешен** (не KBError)
и после поиска/фильтра (при ЛЮБОМ `min_score`, включая 0)
`len(results) == 0`. Ответ — константа
`DONT_KNOW_TEXT = "Не знаю. В базе знаний не нашлось релевантных
материалов. Уточните, пожалуйста: о каком документе или теме вы
спрашиваете?"`, **без LLM-вызова**: SSE done-кадр
`{"type":"done","answer":DONT_KNOW_TEXT,"usage":None,
"request_id":None}` (паттерн no-LLM done profile-turn), assistant-
сообщение сохраняется с `rag_context = {recall_total, reranked,
chunks: [], dont_know: true}`. Нет индекса (KBError) → обычный
LLM-чат (без dont_know, без краха); диалог `rag=false` → retrieval
не идёт, dont-know невозможен. Dont-know **не применяется** в
`POST /api/rag/compare` (4 армы дня 23 не меняются).

#### Scenario: 0 чанков при min_score=0 → dont-know без LLM

- **WHEN** RAG-вкл, min_score=0, корпус без совпадений с вопросом (tmp-БЗ, HashEmbedder, fake-LLM)
- **THEN** `done.answer == DONT_KNOW_TEXT`, `usage is None`, `request_id is None`, fake-LLM-вызовов 0; сохранённое сообщение несёт `rag_context == {recall_total, reranked, chunks: [], dont_know: True}`

#### Scenario: 0 чанков из-за фильтра → dont-know

- **WHEN** RAG-вкл, min_score=0.999, fake-реранкер выдаёт оценки < 0.999 (все отсечены порогом дня 23)
- **THEN** dont-know-ветка: `done.answer == DONT_KNOW_TEXT`, 0 LLM-вызовов, `rag_context.dont_know == True`, `chunks == []`

#### Scenario: Нет индекса (KBError) → обычный чат

- **WHEN** RAG-вкл, индекс не построен (KBError)
- **THEN** обычный LLM-вызов (fake-LLM count == 1), обычный `done` с usage/request_id, `dont_know` в `rag_context` отсутствует, краха нет

#### Scenario: RAG-off → dont-know невозможен

- **WHEN** диалог `rag=false` (эффективный режим), вопрос без совпадений
- **THEN** retrieval не выполняется, dont-know-ветка не срабатывает, обычный LLM-ответ

### Requirement: UI — панель «Источники и цитаты» (вариант B) и «Не знаю»

Фронтенд SHALL рендерить `SourcesPanel` (props
`{ragContext: RagContext, minScore?: number}`) под assistant-
сообщением с `rag_context`, **сосуществуя** с `RagContextInspector`
(не удаляется, не меняется); `FlowInspector` не трогается. Панель:
заголовок «📖 ИСТОЧНИКИ И ЦИТАТЫ» + счётчик; карточка на каждый
чанк — номер 1..N, `file · section`, score-чип (🟢 ≥0.8 / 🟡
0.5–0.8 / 🔴 <0.5 — та же шкала, что RagContextInspector), «из #N»
(`stage1_rank`) при `reranked`, verbatim-цитата в «» из `text`.
При `rag_context.dont_know === true` — красная карточка «🚫 Не знаю»
с причиной (min_score/отсечено, `minScore` — undefined-safe) и
подсказкой «Уточните вопрос: о каком документе вы спрашиваете?»;
чанк-карточки НЕ рендерятся. `chunks` пусто и `dont_know` нет →
панель не рендерится (null). Старые сообщения (без `chunk_id`/
`dont_know`) — рендер без краха. Маркеры [n] в ответе не
интерпретируются и не рендерятся.

#### Scenario: Панель с N карточками

- **WHEN** assistant-сообщение с `rag_context.chunks` из N чанков
- **THEN** SourcesPanel рендерится рядом с RagContextInspector (оба в DOM): N карточек с номерами 1..N, `file · section`, score-чипами по шкале, «из #N» при `reranked`, цитатами в «»; [n]-маркеры не рендерятся

#### Scenario: dont_know → только красная карточка

- **WHEN** `rag_context.dont_know === true`, `chunks == []`
- **THEN** SourcesPanel показывает красную карточку «🚫 Не знаю» (причина + подсказка); чанк-карточек 0; ответ = `DONT_KNOW_TEXT`

#### Scenario: Пусто без dont_know → панели нет

- **WHEN** `rag_context.chunks == []` и `dont_know` отсутствует
- **THEN** SourcesPanel не рендерится (null), сообщение рендерится как до дня 24

### Requirement: E2E day24 — структура на 10 вопросах (порт 8108)

Система SHALL поставлять `scripts/e2e_day24.py` (stdlib, порт
**8108**, паттерн e2e_day23: net cut `_NO_NET`, port-busy wait 10
min, cleanup всегда, exit 0 для PASS/SKIP, 1 для FAIL). Part A —
офлайн-детерминированное ядро, **MUST PASS** (TestClient +
fake-LLM + tmp-БЗ): 10 контрольных вопросов (`control_questions.
json`) × `POST /api/chat` (RAG-вкл, min_score=0) с per-question
ассертом «(chunks непусто → citations (chunks[].text) непусто И
sources-поля source/file/section/chunk_id присутствуют) OR
rag_context.dont_know == true»; dont-know-детерминизм (вопрос без
совпадений при min_score=0 → `done.answer == DONT_KNOW_TEXT`,
usage `None`, request_id `None`, fake-LLM count == 0, dont_know:
true); CITE_RULE-строка в captured LLM-payload; verbatim
(`chunks[i].text` ⊂ текста чанка из БЗ, `len <= 300`); chunk_id-
совпадение с БЗ; регрессия shape `rag_context` (recall_total/
reranked/chunks) и min_score-поля дня 23. Part B — live best-effort
(uvicorn :8108, реальный GPustack): 10/10 done — MUST;
sources/citations при <10/10 — WARNING, не FAIL (retrieval-
качество, паттерн дня 22); dont-know live (min_score=0.999 →
`DONT_KNOW_TEXT` + dont_know:true, сбой API → SKIP). Смысл ответа ↔
цитат — вне e2e (user manual check, задокументирован в скрипте).

#### Scenario: Part A — 10 вопросов MUST PASS

- **WHEN** `python scripts/e2e_day24.py` (Part A, net cut, tmp-БЗ)
- **THEN** exit 0; каждый из 10 вопросов: done 200 + (sources с chunk_id И citations) OR dont_know; dont-know-ветка — 0 LLM-вызовов; CITE_RULE в payload при непустом блоке; verbatim ⊂/≤300; chunk_id совпадает с БЗ; shape `rag_context` дней 21/22/23 без потерь

#### Scenario: Part B — live best-effort

- **WHEN** Part B (реальный GPustack, корпус fetch_books)
- **THEN** 10/10 done — PASS; sources/citations <10/10 → WARNING (не FAIL); dont-know при min_score=0.999 → DONT_KNOW_TEXT + dont_know:true (API down → SKIP); после прогона min_score сброшен в 0; `scripts/e2e_day23.py` и `scripts/e2e_day22.py` Part A проходят 7/7 без правок (контракт compare не тронут)
