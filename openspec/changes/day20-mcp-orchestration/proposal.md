# Proposal: day20-mcp-orchestration

## Why

День 20: **оркестрация MCP-инструментов** — разбить мультитул-серверы
дней 17–19 (`task_manager`, `news_weather`, `pipeline_tools` — 3 файла,
9 тулов) на едицельные локальные MCP-серверы (1 сервер = 1 тул, без
дублей) и дать агенту **маршрутизацию по серверам**: длинные
кросс-серверные цепочки (до 10 шагов) не проходили лимит tool-loop 5
итераций, а имена тулов разных серверов (например, `search`)
амбивалентны — модель могла не понять, к какому серверу адресован
вызов. Ключевые решения (решение пользователя): **always-префикс**
`{server}__{tool}` в именах тулов (логика коллизий не пишется —
префикс снимает проблему по построению) + **каталог подключённых
серверов** в system-промпте; лимит tool-loop 5 → **15** (env
`TOOL_LOOP_CAP`). Проверка задания — 10-шаговый кросс-серверный флоу:
модель сама маршрутизирует запрос через 10 разных серверов и передаёт
данные между ними.

## What Changes

- **`studio/mcp_servers/_mcp_base.py`** (новый, только stdlib): общий
  каркас едицельных stdio-серверов (JSON-RPC 2024-11-05, паттерн
  `task_manager.py`): `run_server(server_name, tool, call)`, контракт
  `call(args) -> (payload: dict, is_error: bool)`; `initialize`
  (serverInfo `{name, version: "1.0"}`), `tools/list` (ровно 1 тул),
  `tools/call` (чужое имя — `isError`), notification без ответа,
  `-32601`/`-32700`; исключение в `call` → `isError`, процесс жив. При
  старте `sys.stdout`/`stderr` реconfigure'ятся с `errors="replace"`
  (cp1251-пайп + символы вне cp1251 — фикс живого бага Task 10, паттерн
  дня 18 из `agent.py`).
- **10 едицельных серверов** (новые, stdio, только stdlib):
  `weather`/`get_weather` (Open-Meteo, `city?`), `news`/`get_news`
  (vc.ru/habr/tproger, top-5, дедуп), `digest_make`/`make_digest`
  (сбор + сохранение в `data/digests/`), `digest_read`/
  `get_latest_digest` (файл → GitHub API → `isError`),
  `task_create`/`create_task`, `task_get`/`get_task_details`,
  `digest_search`/`search` (топ-20, поле `text`), `digest_summarize`/
  `summarize` (детерминированная сводка, без LLM), `file_save`/
  `saveToFile` (`md|txt|json|pdf`), `habr_news`/`get_habr_news` (темы
  `testing`/`ai`, word-boundary-фильтр, `limit` до 50).
- **`studio/mcp_servers/_tasks_store.py`** (новый): file-backed
  хранилище задач `data/tasks.json` (seed `TASK-42`/`TASK-7`, новая —
  `TASK-<max+1>`, атомарная запись, env `TASKS_FILE`).
- **Удалены**: `task_manager.py`, `news_weather.py`, `pipeline_tools.py`
  и их тесты (тулы перенесены 1:1; `collector.py`/`pdf_writer.py`
  переиспользуются).
- **`studio/backend/mcp.py`**: реестр **12 дефолтов** (Firecrawl, Git +
  10 локальных); **идемпотентная миграция** (старые 3 мультитул-сервера
  удаляются по имени, недостающие дефолты добавляются, custom не
  трогаются); `tools()` — поле `server_name`.
- **`studio/backend/agent.py`**: `_llm_tools()` — имена всегда
  `{slug}__{tool}` (`_mcp_slug`: `re.sub(r"[^a-z0-9]+","_",
  name.lower()).strip("_")`), `tool_map` обратного маршрута;
  `MCP_TOOLS_RULE` (v2) + `_mcp_catalog_block()` — каталог серверов в
  system-промпте; `_tool_loop_cap()` — env `TOOL_LOOP_CAP` (дефолт 15,
  на каждый вызов); assistant-`tool_calls`/`role:"tool"` хранятся с
  префиксированными именами (routing proof в истории).
- **Фронтенд** (`ChatPanel.tsx`): бейджи в шапке «🧩 Шаги агента» —
  `formatToolName` режет по первому `__` → `server · tool`,
  `title={n}` (полное имя) на hover; чипы StepRow — полное имя.
- **E2E**: `scripts/e2e_day20.py` (новый, stdlib, порт 8104): Part A —
  офлайн-детерминированное ядро (fake-LLM + реальные MCP-субпроцессы
  всех 10 серверов, `_NO_NET` 127.0.0.1:1), 10-шаговый флоу
  `task_create → weather → news → digest_make → digest_read → habr_news
  → digest_search → digest_summarize → file_save → task_get`, assert на
  порядок `{server}__{tool}`, маршрутизацию (spy `reg.call_tool`),
  передачу данных, кап; Part B — live best-effort (PASS/SKIP, не FAIL).
  e2e дня 17–19 переписаны на едицельные сервера (Part A 6/6, 6/6, 12/12
  без изменений).

## Capabilities

### New Capabilities

- `mcp-orchestration`: оркестрация MCP-инструментов — 10 едицельных
  локальных MCP-серверов (каркас `_mcp_base.py`, 1 сервер = 1 тул),
  always-префикс `{server}__{tool}` + каталог серверов в
  system-промпте, кап tool-loop 15 (`TOOL_LOOP_CAP`), реестр 12
  дефолтов с миграцией, file-backed задачи, бейджи «server · tool»,
  e2e-гибрид с 10-шаговым кросс-серверным флоу (порт 8104).

### Modified Capabilities

- `mcp-integration` (день 16): реестр — 12 дефолтов вместо 5;
  мультитул-серверы удалены миграцией по имени; `GET /api/mcp/tools` —
  поле `server_name`. REST-роуты не добавлены.
- `mcp-tool-loop` (день 17): имена тулов в payload всегда с
  префиксом сервера; кап 5 → 15 (`TOOL_LOOP_CAP`); tool-имена,
  сохраняемые в историю, = имена LLM (routing proof).
- `mcp-digest` (день 18) / `mcp-pipeline` (день 19): тулы
  `get_weather`/`get_news`/`make_digest`/`get_latest_digest`/
  `search`/`summarize`/`saveToFile` живут в едицельных серверах
  `weather`/`news`/`digest_make`/`digest_read`/`digest_search`/
  `digest_summarize`/`file_save` (поведение 1:1).

## Impact

- **Код**: новые `studio/mcp_servers/{_mcp_base,_tasks_store,weather,
  news,digest_make,digest_read,task_create,task_get,digest_search,
  digest_summarize,file_save,habr_news}.py`,
  `scripts/e2e_day20.py`, новые тесты (`test_mcp_base.py`,
  `test_weather_news.py`, `test_digest_servers.py`,
  `test_tasks_servers.py`, `test_digest_compose_servers.py`,
  `test_habr_news.py`, `test_registry_day20.py`,
  `test_agent_day20.py`, `test_e2e_day20_units.py`); удалены
  `task_manager.py`/`news_weather.py`/`pipeline_tools.py` и их тесты;
  правки `mcp.py` (дефолты/миграция/`server_name`), `agent.py`
  (префикс/каталог/кап/имена в истории), `ChatPanel.tsx` (бейджи),
  e2e day17–19 (едицельные сервера).
- **Данные**: `data/mcp_servers.json` мигрирует при первом обращении
  (старые 3 записи удаляются, добавляются 10); новое runtime-
  хранилище `data/tasks.json` (в `.gitignore`, seed из кода).
- **Зависимости**: никаких (только stdlib; локальные сервера без npx).
- **Совместимость**: REST/SSE не меняются (кроме поля `server_name` в
  `GET /api/mcp/tools`); Firecrawl/Git (npx) на месте; регресс дня 16:
  без подключённых серверов `tools` в LLM-payload нет — не меняется.
