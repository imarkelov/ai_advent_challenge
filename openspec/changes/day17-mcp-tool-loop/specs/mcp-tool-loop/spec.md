## Purpose

Capability «mcp-tool-loop» — LLM-driven tool-calling: инструменты
подключённых MCP-серверов уходят в тело LLM-запроса (`tools`, формат
OpenAI), модель сама решает вызвать инструмент (`tool_calls`), агент
вызывает его на MCP-сервере, результат возвращается модели сообщением
`role: "tool"` в цикле до финального текстового ответа (кап 5
итераций). Новый собственный MCP-сервер — Mock Task Manager (stdio,
in-memory, только stdlib). Консоль-логи этапов: `[MCP Init]`,
`[LLM Decision]`, `[MCP Response]`, `[Final Response]`. Пути дня 16
(вызов по команде `/сервер тул`, system-маркер `mcp_tool`) не меняются —
tool-loop добавлен поверх.

## ADDED Requirements

### Requirement: Mock Task Manager (stdio MCP-сервер)

Система SHALL предоставлять stdio MCP-сервер
`studio/mcp_servers/task_manager.py` (протокол 2024-11-05,
newline-delimited JSON-RPC 2.0 по stdin/stdout, только stdlib — сеть и
внешние зависимости MUST NOT требоваться). Сервер SHALL поддерживать
`initialize` (protocolVersion `2024-11-05`), `tools/list` и
`tools/call`; notification (запрос без `id`) — без ответа; неизвестный
метод — JSON-RPC error `-32601`. Данные — in-memory, предзагружены:
TASK-42 (`status: in_progress`, assignee `migor`) и вторая задача
(`status: done`). Инструменты: `get_task_details` (required `task_id`)
и `create_task` (required `title`). Ненайденная задача — результат
`{"error": "Задача не найдена: <id>"}` с `isError: true`.

#### Scenario: Перечисление инструментов

- **WHEN** клиент вызывает `initialize` и `tools/list`
- **THEN** сервер отвечает protocolVersion `2024-11-05` и список из двух инструментов: `get_task_details`, `create_task`

#### Scenario: Ненайденная задача — ошибка результата

- **WHEN** `tools/call` `get_task_details` с несуществующим `task_id`
- **THEN** результат — текст `{"error": "Задача не найдена: <id>"}` с `isError: true`, процесс не падает

### Requirement: Task Manager — дефолт реестра

`MCPRegistry` SHALL включать **Task Manager** в дефолтные серверы
(рядом с Firecrawl и Git дня 16) с
`command = [sys.executable, <repo>/studio/mcp_servers/task_manager.py]`
(stdio, без npx). `MCPRegistry.connect()` на успешное подключение SHALL
записать в stdout строку `[MCP Init] {name}: {n} инструментов: {список}`
(`flush=True`).

#### Scenario: Дефолт запускается локальным python

- **WHEN** создаётся новый `MCPRegistry` и подключается дефолт Task Manager
- **THEN** `command[0]` — путь к python (`sys.executable`), `command[1]` оканчивается `task_manager.py` и файл существует; `connect` → status `connected`, `tools_count` = 2

### Requirement: LLM tool-calling loop

`StudioAgent.ask_stream` SHALL реализовывать tool-loop (максимум **5**
итераций): при наличии подключённых MCP-серверов инструменты уходят в
тело LLM-запроса полем `tools` (формат OpenAI:
`{"type": "function", "function": {"name", "description",
"parameters"}}`, `parameters` = `input_schema` тула; пусто →
`{"type": "object"}`); коллизии имён между серверами — первый
победитель, следующие — префикс `{server_id}__`. `delta.tool_calls` из
SSE-чанков SHALL агрегироваться по `index` (`{id, name, arguments}`,
`arguments` — конкатенация строк). При ответе модели с `tool_calls`:
assistant-сообщение сохраняется в диалог с `tool_calls`; каждый вызов
выполняется через `MCPRegistry.call_tool`; результат сохраняется как
сообщение `{"role": "tool", "tool_call_id": ..., "name": ...,
"content": ...}`; запрос к LLM повторяется. Превышение капа итераций
SHALL вернуть SSE `error` с текстом «Tool-loop: превышен лимит
итераций (5)». Ошибка инструмента (MCPError, некорректные JSON-аргументы)
SHALL стать tool-сообщением с текстом ошибки (цикл продолжается, агент
НЕ падает). Финальный текстовый ответ — SSE `done`; журнал LLM-запросов
получает запись на КАЖДУЮ итерацию; usage суммируется по итерациям.
Без подключённых серверов поле `tools` в тело запроса MUST NOT
попадать (поведение дня 16, регресс `test_chat_payload_has_no_mcp_tools`).

#### Scenario: Happy path (модель вызывает инструмент)

- **WHEN** подключён сервер и модель отвечает `tool_calls`, затем — текстом
- **THEN** в диалоге assistant-сообщение с `tool_calls` и `role: "tool"` с `tool_call_id`/`name`; SSE-поток заканчивается `done`; в body первой итерации `tools` содержит инструменты сервера

#### Scenario: Превышение лимита итераций

- **WHEN** модель отвечает `tool_calls` во всех 5 итерациях
- **THEN** SSE-поток заканчивается `error` «Tool-loop: превышен лимит итераций (5)»; LLM-вызовов ровно 5 (5 записей в журнале)

#### Scenario: Ошибка инструмента не роняет цикл

- **WHEN** вызов инструмента падает (MCPError) или аргументы — некорректный JSON
- **THEN** в диалог пишется `role: "tool"`-сообщение с текстом ошибки; цикл продолжается; при следующем текстовом ответе модели — `done`

#### Scenario: Без подключённых серверов `tools` нет

- **WHEN** выполняется `/api/chat` без подключённых MCP-серверов
- **THEN** тело LLM-запроса не содержит поля `tools` (поведение дня 16)

### Requirement: Консоль-логи этапов

Система SHALL писать в stdout (`print(..., flush=True)`) ровно четыре
лог-тега: `[MCP Init]` (успешный `connect`: имя сервера, число и
имена инструментов), `[LLM Decision]` (имя инструмента + аргументы),
`[MCP Response]` (сырой результат инструмента), `[Final Response]`
(финальный текстовый ответ).

#### Scenario: Полный цикл виден в консоли

- **WHEN** выполняется запрос с вызовом инструмента
- **THEN** в stdout по порядку: `[MCP Init]` (при connect), `[LLM Decision]`, `[MCP Response]`, `[Final Response]`

### Requirement: UI — служебные tool-сообщения вне ленты

Фронтенд (`state.tsx`, `ChatPanel.tsx`) SHALL сохранять сообщения
`role: "tool"` в памяти диалога (для LLM), но MUST NOT рендерить их как
чат-пузыри (паттерн служебных mcp_tool-записей дня 16).

#### Scenario: Tool-сообщение не видно пользователю

- **WHEN** в диалоге есть сообщение `role: "tool"` с уникальным текстом
- **THEN** текст отсутствует в DOM ленты чата; диалог и следующий LLM-ответ работают

### Requirement: Проверка (тесты и e2e)

Репозиторий SHALL содержать офлайн-тесты (pytest/Vitest, без сети):
task_manager на реальном subprocess, tool-loop на scripted `tool_calls`
+ fake-процессе, регресс «без подключённых серверов — без `tools`»,
рендер role "tool". `scripts/e2e_day17.py` (stdlib) SHALL быть
гибридом: Part A — детерминированное ядро в-процессе (`StudioAgent` +
`httpx.MockTransport` fake-LLM + реальный subprocess `task_manager.py`
через `MCPRegistry`; MUST PASS); Part B — live (uvicorn :8101,
реальный LLM GPustack), best-effort: инфраструктурные шаги FAIL на
реальном баге, модель не вызвала инструмент → SKIP (не FAIL); cleanup
всегда; exit 0 для PASS/SKIP, 1 для FAIL.

#### Scenario: E2E прогон

- **WHEN** запущен `python scripts/e2e_day17.py`
- **THEN** Part A — все assert PASS (офлайн, детерминированно); Part B — PASS или SKIP; exit 0
