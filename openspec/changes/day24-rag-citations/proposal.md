# Proposal: day24-rag-citations

## Why

День 24: **цитаты, источники и анти-галлюцинации** — поверх RAG-чата дня
21 (`ask_stream` + блок «База знаний» + `rag_context` в
assistant-сообщении) и фильтрации дня 23 (`min_score` уже действует в
`ask_stream`) добавить: (1) **обязательные источники и verbatim-цитаты**
в каждом RAG-ответе (source + file + section + `chunk_id`, цитата =
focused snippet ≤300 символов из `chunks[].text`), (2) **[n]-правило
цитирования** (модель ссыляется на выдержки маркерами [1], [2], …;
правило живёт только в `kb_block`) и (3) **dont-know по триггеру A′** —
RAG-вкл + индекс есть + 0 чанков после поиска/фильтра (при ЛЮБОМ
`min_score`, включая 0) → детерминированный RU-ответ «не знаю»
**без LLM-вызова**. UI — панель «Источники и цитаты» (вариант B:
карточки с номером, `file · section`, score-чипом, «из #N» при
реранке, verbatim-цитатой в «») + красная карточка «Не знаю»
(причина + подсказка). Проверка — `scripts/e2e_day24.py` (порт
**8108**, 10 контрольных вопросов); смысл ответа ↔ цитат проверяет
пользователь вручную в живом диалоге (не LLM-judge, не эмбеддинги).
Ключевые решения (зафиксированы в плане дня
`.omo/plans/day24-rag-citations.md`):

- **Гибрид A+B (выбор пользователя).** Код прикладывает
  sources/citations **verbatim** из результатов `search_rag` — модель
  цитаты НЕ генерирует; модель лишь ссылается на выдержки маркерами
  [n] по правилу в `kb_block`. `chunk_id` добавляется в объект чанка
  `_kb_context` **аддитивно** (рядом с rank/file/section/score).
- **Dont-know-триггер A′.** Срабатывает только когда RAG-вкл (эффективный
  per-диалог режим дня 22), поиск **успешен** (не KBError) и
  `len(results) == 0` — при любом `min_score`, включая 0. Ответ =
  константа `DONT_KNOW_TEXT` (точная RU-строка из контракта),
  **0 LLM-вызовов** (`usage: None`, `request_id: None`), assistant-
  сообщение сохраняется с `rag_context = {recall_total, reranked,
  chunks: [], dont_know: True}`. Нет индекса (KBError) → обычный
  LLM-чат (без dont_know, без краха); `rag=false` диалог → retrieval
  не идёт, dont-know невозможен.
- **CITE_RULE — только в `kb_block`, только когда блок непустой**:
  «Ссылайся на выдержки в ответе маркерами [1], [2], … по номерам.
  Опирайся только на приведённые выдержки; если выдержки не покрывают
  факт — так и скажи.» Пустой результат / RAG-off → правила нет.
  e2e ассертит **наличие строки правила** в LLM-payload, НЕ
  комплаенс модели маркерам (мелкие модели ненадёжны).
- **UI = вариант B (выбран пользователем из 3 мокапов)**. Панель
  «📖 Источники и цитаты» под assistant-ответом (карточки B-1) +
  красная карточка «🚫 Не знаю» (B-2, причина + подсказка «Уточните
  вопрос»). **Оба инспектора сосуществуют**: новый `SourcesPanel` +
  существующий `RagContextInspector` (не удаляется и не меняется);
  `FlowInspector` не трогается. Маркеры [n] во фронтенде НЕ
  рендерятся (панель автономна от текста ответа).
- **`kb.py` НЕ меняется** (сигнатуры и логика `search_rag`
  зафиксированы): `search_rag` уже отдаёт
  `{chunk_id, source, file, section, score, text, ±rerank_score,
  stage1_rank}`, а `chunks[].text` — уже verbatim focused snippet
  ≤300 (`_focus_snippet`). `/api/rag/compare` (4 армы дня 23) НЕ
  меняется: dont-know в compare не добавляется (compare = явный
  инструмент, не чат).
- **Контракт dont-know (зафиксирован)**: done-кадр
  `{"type":"done","answer":DONT_KNOW_TEXT,"usage":None,"request_id":
  None}`; `DONT_KNOW_TEXT = "Не знаю. В базе знаний не нашлось
  релевантных материалов. Уточните, пожалуйста: о каком документе или
  теме вы спрашиваете?"`.
- E2E на порту **8108** (день 23 — 8107, день 22 — 8106, 8105 — день
  21, 8100–8104 — `e2e_studio.py` и дни 17–20).

## What Changes

- **`studio/backend/agent.py`**: константы `DONT_KNOW_TEXT` (точная
  RU-строка) и `CITE_RULE`; dont-know intercept в `ask_stream` — после
  retrieval и до LLM/tool-loop: если RAG-вкл, поиск успешен (не
  KBError) и `len(results) == 0` → сохранить assistant-сообщение
  (content=`DONT_KNOW_TEXT`, `rag_context={recall_total, reranked,
  chunks:[], dont_know: True}`) → `yield done` (usage `None`,
  request_id `None`) → return (паттерн no-LLM done profile-turn,
  guard-порядок task-guard/profile-turn не трогается);
  `_render_kb_block` — в конец непустого блока строка `CITE_RULE`;
  `_kb_context` — в объект чанка аддитивное `"chunk_id"` (рядом с
  rank/file/section/score). `kb.py`, `rag_compare`, task-пайплайн,
  body-схема `/api/chat`, base system prompt — не меняются.
- **Тесты** (TDD, паттерн day21/22/23): `test_agent.py` — dont-know
  (min_score=0.999 + fake-реранкер <0.999 → `done.answer ==
  DONT_KNOW_TEXT`, `usage is None`, `request_id is None`, fake-LLM
  count == 0, `rag_context == {recall_total, reranked, chunks:[],
  dont_know: True}`), trigger A′ при min_score=0 (корпус без
  совпадений → dont-know, 0 LLM-вызовов), нет индекса (KBError) →
  обычный LLM-вызов (count == 1, без dont_know), `rag=false` →
  retrieval не идёт, kb_block непустой → CITE_RULE-строка в
  system-сообщении (пустой результат → нет), `_kb_context` chunks
  несут `chunk_id` + `text` ⊂ сохранённого текста чанка и
  `len(text) <= 300`; регрессия: все существующие agent-тесты
  зелёные.
- **Фронтенд**: `api.ts` — типы `RagContextChunk` + `chunk_id:
  string`, `RagContext` + `dont_know?: boolean` (опционально — старые
  сообщения); новый `components/SourcesPanel.tsx` (props
  `{ragContext, minScore?}`; карточки B-1: номер 1..N, `file ·
  section`, score-чип 🟢/🚨 та же шкала что RagContextInspector,
  «из #N» при `reranked`, verbatim-цитата в «»; dont-know карточка
  B-2 «🚫 Не знаю» с причиной и подсказкой; undefined-safe для
  старых сообщений; `chunks` пусто и `dont_know` нет → null);
  `styles.css` — стили панели (design-токены репо, паттерн
  RagContextInspector); `ChatPanel.tsx` — рендер `<SourcesPanel />`
  под assistant-ответом рядом с `RagContextInspector` (оба живы).
  Vitest-тесты на компонент и wiring; `tsc -b` + build clean.
- **`scripts/e2e_day24.py`** (новый, stdlib, порт **8108**, паттерн
  e2e_day23): Part A — офлайн MUST PASS (net cut `_NO_NET`,
  TestClient + fake-LLM + tmp-БЗ: 10 контрольных вопросов —
  per-question «chunks непусто → citations/sources с chunk_id
  присутствуют OR dont_know», dont-know-детерминизм (0 LLM-вызовов),
  CITE_RULE в captured payload, verbatim ⊂ текста чанка и ≤300,
  chunk_id-совпадение с БЗ, регрессия shape rag_context дней
  21/22/23); Part B — live best-effort (uvicorn :8108, реальный
  GPustack, 10 вопросов в одном диалоге: sources/citations —
  WARNING не FAIL при <10/10 (retrieval-качество, паттерн дня 22);
  dont-know live при min_score=0.999 — PASS/SKIP). Cleanup всегда;
  exit 0 для PASS/SKIP, 1 для FAIL.
- **Доки**: README (строка таблицы + секция «День 24»), RELEASE
  (секция); финальные числа тестов/e2e подставляются после задачи 7.

## Capabilities

### New Capabilities

- `rag-chat-citations`: цитаты, источники и анти-галлюцинации (день
  24) — каждый RAG-ответ несёт источники (source + file + section +
  `chunk_id` + score, ±`rerank_score`/`stage1_rank`) и verbatim-
  цитаты (focused snippet ≤300 ⊂ текста чанка, без генерации моделью);
  [n]-правило (`CITE_RULE`) — только в непустом `kb_block`; dont-know
  по триггеру A′ (RAG-вкл + индекс есть + 0 чанков при любом
  min_score → `DONT_KNOW_TEXT`, 0 LLM-вызовов, `usage: None`,
  `request_id: None`, `rag_context: {recall_total, reranked,
  chunks: [], dont_know: true}`); UI-панель «Источники и цитаты»
  (вариант B) + красная карточка «Не знаю», сосуществование с
  `RagContextInspector`; e2e-гибрид (порт 8108, Part A MUST PASS
  офлайн, Part B live best-effort, 10 контрольных вопросов).

### Modified Capabilities

- `rag-chat` (дни 21–23, live-чат `ask_stream`): assistant-сообщение
  получает аддитивное `chunk_id` в `rag_context.chunks[]` и новый
  вариант `rag_context.dont_know: true` (с `chunks: []`); в
  непустой `kb_block` добавляется строка `CITE_RULE` (поведение с
  непустым блоком и пустым результатом/без RAG не меняется, кроме
  этой строки); `kb.py`, chunking, embedder, RRF/BM25, `APIReranker`,
  `min_score`-механика дня 23 не меняются.
- `rag-compare` (день 23): **НЕ меняется** (гард): `POST
  /api/rag/compare` остаётся 4-армным, dont-know в compare не
  добавляется; `e2e_day22.py`/`e2e_day23.py` Part A проходят 7/7 без
  правок.

## Impact

- **Код**: правки `studio/backend/agent.py` (DONT_KNOW_TEXT,
  CITE_RULE, dont-know intercept, chunk_id в `_kb_context`), тесты
  `test_agent.py`; `studio/frontend/src/{api.ts, styles.css,
  components/SourcesPanel.tsx, components/ChatPanel.tsx}` +
  vitest-тесты; новый `scripts/e2e_day24.py`.
- **Данные**: runtime `dialogues.json` (gitignored) получает сообщения
  с `chunk_id`/`dont_know` (старые сообщения читаются undefined-safe);
  runtime-артефакты `.omo/evidence/day24/` (gitignored). Никаких
  новых `.env`-ключей, REST-эндпоинтов и зависимостей.
- **Зависимости**: только stdlib (backend) и существующий React-стек
  (frontend); без новых pip/npm-зависимостей, моделей и MCP-серверов.
- **Совместимость**: body-схема `POST /api/chat` и SSE-формы
  (`delta`/`done`/`error`) не меняются — dont-know едет существующим
  done-кадром (паттерн no-LLM done profile-turn); `rag_context` —
  только аддитивные поля (`chunk_id`, `dont_know`), старые
  сообщения/инспекторы работают без правок; `kb.py` и
  `/api/rag/compare` не трогаются (e2e_day22/e2e_day23 Part A 7/7
  green); дни 11–23 не регрессируют.
