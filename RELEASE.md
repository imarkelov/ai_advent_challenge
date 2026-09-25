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

Бэкенд — 390 тестов PASS (офлайн). Фронтенд — 219 тестов PASS (Vitest)
+ `tsc -b` + `npm run build` clean. E2E: `e2e_day17.py` — Part A 6/6,
Part B PASS (14/0/0); `e2e_day18.py` — Part A 6/6, Part B PASS
(14/0/0); `e2e_day19.py` — Part A 12/12, Part B SKIP (18 PASS / 0 FAIL
/ 1 SKIP); `e2e_day20.py` — Part A 19/19, Part B SKIP (25 PASS / 0 FAIL
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

- `54d2231` — _mcp_base — shared single-tool stdio MCP server skeleton
- `592af6b` — single-tool MCP servers weather + news
- `d1780ed` — single-tool MCP servers digest_make + digest_read
- `9348b50` — file-backed tasks store + task_create/task_get servers
- `6636d47` — isolate in-process task tests with temp TASKS_FILE
- `4f47ab7` — gitignore data/tasks.json (runtime task-store state)
- `533481b` — single-tool MCP servers digest_search/digest_summarize/file_save
- `9e75ead` — habr_news MCP server (testing/ai topics, word-boundary filter)
- `f2b0312` — MCP registry — 12 defaults (10 single-tool), old-server migration, server_name in tools()
- `2068ab0` — agent routing — always server__tool prefix, MCP catalog in system prompt, TOOL_LOOP_CAP=15, llm names stored in history
- `599335d` — e2e day17-19 on single-tool servers; remove task_manager/news_weather/pipeline_tools
- `3f2854a` — e2e_day20 — 10-server cross-flow (Part A deterministic + Part B live :8104)
- `7cccc67` — _mcp_base stdout errors=replace (non-cp1251 payload chars crash server)
- `313ce1e` — e2e_day20 Part B — SSE model error → SKIP (not FAIL)
- `ad1aff6` — agent-steps badges render MCP tools as 'server · tool'

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
