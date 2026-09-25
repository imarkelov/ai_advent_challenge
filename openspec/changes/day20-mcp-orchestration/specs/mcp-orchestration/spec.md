## Purpose

Capability «mcp-orchestration» — оркестрация MCP-инструментов (день 20):
мультитул-серверы дней 17–19 (`task_manager`, `news_weather`,
`pipeline_tools`) разбиты на 10 едицельных локальных stdio MCP-серверов
(1 сервер = 1 тул, общий каркас `_mcp_base.py` с контрактом
`call(args) -> (payload, is_err)`), агентская маршрутизация по серверам
(always-префикс `{server}__{tool}` + каталог серверов в system-промпте,
кап tool-loop 15 через `TOOL_LOOP_CAP`), реестр 12 дефолтов с
идемпотентной миграцией старых мультитул-серверов, file-backed задачи
(`data/tasks.json`), бейджи «server · tool» в шапке «Шаги агента», e2e-
гибрид с 10-шаговым кросс-серверным флоу (порт 8104). REST-роуты дня 16
не меняются (изменение: поле `server_name` в `GET /api/mcp/tools`).

## ADDED Requirements

### Requirement: Едицельные локальные MCP-серверы

Система SHALL предоставлять 10 едицельных stdio MCP-серверов
(`studio/mcp_servers/<name>.py`, протокол 2024-11-05, только stdlib,
запуск `[sys.executable, .../<name>.py]`, без npx): `weather`/
`get_weather`, `news`/`get_news`, `digest_make`/`make_digest`,
`digest_read`/`get_latest_digest`, `task_create`/`create_task`,
`task_get`/`get_task_details`, `digest_search`/`search`,
`digest_summarize`/`summarize`, `file_save`/`saveToFile`, `habr_news`/
`get_habr_news`. Каждый сервер описывается константой `TOOL` (ровно 1
тул) и функцией `call(args: dict) -> (payload: dict, is_error: bool)`,
запускается общим каркасом `_mcp_base.run_server` (`initialize` —
serverInfo `{name, version: "1.0"}`, `tools/list` — ровно 1 тул,
`tools/call`, notification без ответа, неизвестный метод — `-32601`,
битая JSON-строка — `-32700`). Ошибка любого инструмента SHALL стать
`{"error": ...}` с `isError: true`; процесс MUST NOT падать. Сбой
live-источника (сеть) в `get_weather`/`get_news`/`get_habr_news` —
деградация в `isError`, не падение. Каркас SHALL реconfigure'ить
`sys.stdout`/`sys.stderr` с `errors="replace"` при старте (cp1251-пайп +
символы вне cp1251 в JSON-RPC-ответе не должны убивать процесс).

#### Scenario: Перечисление единственного инструмента

- **WHEN** клиент вызывает `initialize` и `tools/list` на любом из 10 серверов
- **THEN** сервер отвечает protocolVersion `2024-11-05`, serverInfo `version: "1.0"` и список ровно из одного инструмента

#### Scenario: Сбой — ошибка результата, процесс жив

- **WHEN** `tools/call` приводит к ошибке (отсутствующий аргумент, недоступная сеть, символы вне cp1251 в payload)
- **THEN** результат `{"error": "..."}` с `isError: true`; последующий `tools/list` отвечает (процесс не упал)

#### Scenario: Чужой тул отклонён

- **WHEN** `tools/call` с `name`, отличным от единственного тула сервера
- **THEN** результат с `isError: true` и текстом «не найден»; процесс жив

### Requirement: Always-префикс и каталог

Имена MCP-тулов в LLM-payload SHALL быть **всегда** с префиксом
сервера: `{slug}__{tool}`, где `slug = re.sub(r"[^a-z0-9]+","_",
name.lower()).strip("_")` (фолбэк `server`); логики детекта/разрешения
коллизий нет. `agent._llm_tools` SHALL возвращать `(llm_tools,
tool_map)`, где `tool_map: llm_name -> (server_id, real_tool)`. При
подключённых MCP-тулах в system-промпт SHALL добавляться
`MCP_TOOLS_RULE` + каталог серверов (`_mcp_catalog_block`): строки
`- {server} ({slug}): {tool} — описание (до 120 симв.)` по каждому
подключённому серверу. Без подключённых серверов правило/каталог и
поле `tools` в LLM-payload MUST NOT присутствовать (регресс дня 16).
Assistant-`tool_calls` и `role:"tool"`-сообщения SHALL храниться в
истории диалога с префиксированными (LLM) именами — routing proof.

#### Scenario: Префикс в payload

- **WHEN** подключён сервер `digest_search` (тул `search`) и выполняется `/api/chat`
- **THEN** в body LLM-запроса `tools` содержит имя `digest_search__search`; вызов модели `digest_search__search` маршрутизируется на сервер `digest_search`, тул `search`

#### Scenario: Каталог в system-промпте

- **WHEN** подключены ≥2 сервера и выполняется `/api/chat`
- **THEN** system-промпт содержит блок каталога со строкой на каждый сервер вида `- <имя> (<slug>): <тул> — <описание>`

#### Scenario: Имена в истории = имена LLM

- **WHEN** модель вызывает `task_create__create_task` и получает результат
- **THEN** сохранённые `assistant.tool_calls` и `role:"tool"`-сообщение содержат имя `task_create__create_task` (не голый `create_task`)

#### Scenario: Без серверов — без tools (регресс)

- **WHEN** `/api/chat` без подключённых MCP-серверов
- **THEN** тело LLM-запроса не содержит поля `tools`; system-промпт без правила/каталога MCP

### Requirement: Маршрутизация длинного флоу

Лимит итераций tool-loop SHALL составлять **15** (env `TOOL_LOOP_CAP`,
читается на каждый вызов; было 5 в дни 17–19). Превышение лимита SHALL
отдавать SSE `error` с текстом «Tool-loop: превышен лимит итераций
(15)». Система SHALL прогонять 10-шаговый кросс-серверный флоу
(`task_create → weather → news → digest_make → digest_read → habr_news
→ digest_search → digest_summarize → file_save → task_get`) через tool-
loop: модель вызывает тулы 10 разных серверов и передаёт результат
одного сервера во вход следующего.

#### Scenario: Кап 15

- **WHEN** скриптованный tool-цикл превышает `TOOL_LOOP_CAP`
- **THEN** SSE `error` «Tool-loop: превышен лимит итераций (15)»; агент не падает

#### Scenario: 10-серверный флоу (e2e Part A)

- **WHEN** в e2e_day20 Part A fake-LLM вызывает 10 тулов в порядке FLOW на реальных MCP-субпроцессах (сеть отрезана, live-тулы — `isError`)
- **THEN** tool-сообщения диалога = `{server}__{tool}` в порядке FLOW; spy на `call_tool` — (server, tool) пары == FLOW; данные передаются между серверами (выход N → вход N+1)

### Requirement: Миграция реестра

`MCPRegistry` SHALL содержать 12 дефолтов: Firecrawl, Git (npx, день
16) + 10 локальных едицельных серверов (`[sys.executable, ...]`, stdio,
`enabled: true`). При первом обращении к реестру SHALL выполняться
**идемпотентная миграция**: старые мультитул-серверы `Task Manager`/
`News & Weather`/`Pipeline Tools` удаляются **по имени** (открытые
сессии закрываются), недостающие дефолты добавляются по имени,
custom-серверы не изменяются; повторный вызов — no-op. `GET
/api/mcp/tools` SHALL возвращать записи с полем `server_name` (имя
сервера). Файлы `task_manager.py`/`news_weather.py`/`pipeline_tools.py`
MUST NOT присутствовать в репозитории (тулы перенесены 1:1 в
едицельные сервера).

#### Scenario: Миграция на реальном реестре

- **WHEN** `data/mcp_servers.json` содержит дефолты до дня 20 (включая `Task Manager`, `News & Weather`, `Pipeline Tools`) и создаётся `MCPRegistry`
- **THEN** в реестре ровно 12 серверов: старые 3 имени отсутствуют, 10 локальных + Firecrawl + Git присутствуют; повторное обращение не дублирует записи

#### Scenario: Задачи в файле

- **WHEN** `task_create`/`create_task` вызывается на чистой установке
- **THEN** создаётся задача `TASK-43` (seed — `TASK-42`/`TASK-7`), запись видна `task_get`/`get_task_details` (разные процессы, общий файл `data/tasks.json`)

### Requirement: Бейджи server · tool

Фронтенд SHALL рендерить бейджи MCP-тулов в шапке блока «🧩 Шаги
агента» в виде `server · tool`: префикс-имя режется по **первому**
`__` (`formatToolName`), полный префикс-имен — `title={n}` (hover).
Чипы строк шагов (StepRow) сохраняют полное префиксированное имя.

#### Scenario: Бейдж в шапке блока

- **WHEN** ответ модели содержит tool-сообщения `digest_search__search` и `file_save__saveToFile`
- **THEN** шапка блока «Шаги агента» показывает бейджи `digest_search · search` и `file_save · saveToFile`; hover (`title`) — `digest_search__search` / `file_save__saveToFile`; чипы StepRow — полные имена

### Requirement: Проверка (тесты и e2e)

Репозиторий SHALL содержать офлайн-тесты (pytest, без сети): каркас
`_mcp_base` (JSON-RPC, error-ветки, `-32601`/`-32700`), 10 едицельных
серверов на **реальных subprocess** (поведение тулов 1:1 с прежними
мультитул-серверами), file-backed store (seed, `TASK-43`, idempotent
re-run на tmp `TASKS_FILE`), реестр 12 дефолтов + миграция, agent-
префикс/каталог/кап/имена-в-истории, бейджи (Vitest).
`scripts/e2e_day20.py` (stdlib, порт 8104) SHALL быть гибридом:
**Part A** — офлайн-детерминированное ядро (fake-LLM + реальные
MCP-субпроцессы всех 10 серверов, `_NO_NET` 127.0.0.1:1; 10-шаговый
флоу; assert на порядок `{server}__{tool}`, маршрутизацию, передачу
данных, кап) — MUST PASS; **Part B** — live (uvicorn :8104, реальный
LLM), best-effort — PASS или SKIP (поведение модели не FAIL). e2e
дней 17–19 SHALL оставаться зелёными на едицельных серверах (Part A
6/6, 6/6, 12/12).

#### Scenario: E2E прогон

- **WHEN** запущен `python scripts/e2e_day20.py`
- **THEN** Part A — все assert PASS (офлайн, детерминированно); Part B — PASS или SKIP; exit 0

#### Scenario: Регресс e2e дней 17–19

- **WHEN** запущены `e2e_day17.py`/`e2e_day18.py`/`e2e_day19.py`
- **THEN** Part A каждой — все assert PASS (едицельные сервера вместо удалённых мультитул-серверов)
