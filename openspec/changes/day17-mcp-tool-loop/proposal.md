# Proposal: day17-mcp-tool-loop

## Why

День 17: **LLM-driven tool-calling** поверх подключения MCP дня 16. К
дню 16 инструменты подключённых серверов вызывались только по команде
пользователя (`/имя-сервера имя-тула`, system-маркер `mcp_tool`), а
определения инструментов (`tools`) в тело LLM-запроса не уходили — модель
не знала, что инструменты вообще существуют. Задание: модель сама решает
вызвать инструмент — `tools` в payload, `tool_calls` от модели, вызов на
MCP-сервере, результат обратно модели (`role: "tool"`) в цикле до
финального ответа. Для демонстрации — новый собственный MCP-сервер Mock
Task Manager (stdio, in-memory, только stdlib).

## What Changes

- **Mock Task Manager** (`studio/mcp_servers/task_manager.py`, новый):
  stdio JSON-RPC MCP-сервер (2024-11-05), только stdlib, без npx.
  In-memory задачи (предзагружены TASK-42: `in_progress`, assignee
  `migor`; TASK-7: `done`), инструменты `get_task_details` (required
  `task_id`), `create_task` (required `title`); ошибка «не найдено» —
  `{"error": "Задача не найдена: <id>"}` + `isError: true`; неизвестный
  метод — JSON-RPC -32601.
- **`mcp.py`**: Task Manager — третий дефолт реестра
  (`command = [sys.executable, <repo>/studio/mcp_servers/task_manager.py]`);
  `connect()` на успех — `[MCP Init] {name}: {n} инструментов: {список}`
  в stdout (flush).
- **`agent.py`**: tool-loop в `ask_stream` (кап 5 итераций):
  `_llm_tools()` → OpenAI-`tools` из подключённых серверов (коллизии
  имён — префикс `{server_id}__`); `delta.tool_calls` агрегируются по
  `index`; assistant-сообщение сохраняется с `tool_calls`; вызов через
  `MCPRegistry.call_tool`; результат — `role: "tool"`-сообщение с
  `tool_call_id` + `name` в диалоге; цикл; финальный текст — `done`.
  Превышение капа — SSE `error` «Tool-loop: превышен лимит итераций
  (5)». Ошибка инструмента (MCPError, битый JSON args) — текст ошибки в
  tool-сообщении, цикл продолжается. Без подключённых серверов
  `tools` в payload нет (поведение дня 16, регресс
  `test_chat_payload_has_no_mcp_tools` остаётся зелёным).
- **Логирование этапов** в консоль: `[MCP Init]`, `[LLM Decision]`,
  `[MCP Response]`, `[Final Response]` (`print(..., flush=True)`).
- **Фронтенд**: сообщения `role: "tool"` — служебные: хранятся в
  памяти диалога, не рендерятся чат-пузырями.
- **E2E** (`scripts/e2e_day17.py`, новый, stdlib): Part A —
  детерминированное ядро в-процессе (StudioAgent + MockTransport
  fake-LLM с `tool_calls` + реальный subprocess `task_manager.py`
  через `MCPRegistry`; 6 assert); Part B — live (uvicorn :8101,
  реальный LLM), best-effort SKIP.
- **Пути дня 16 не трогаются**: роут `/api/mcp/servers/{id}/tools/{tool}`,
  вызов из чата (`/сервер тул`), system-маркер `mcp_tool` — полностью
  на месте, tool-loop добавлен поверх.

## Capabilities

### New Capabilities

- `mcp-tool-loop`: LLM-driven tool-calling — инструменты подключённых
  MCP-серверов уходят в LLM-пейлоад (`tools`), модель сама вызывает
  инструменты (`tool_calls` → MCP-сервер → `role: "tool"` → цикл до
  финального ответа), новый Mock Task Manager (stdio MCP-сервер,
  in-memory), console-логи этапов, e2e-гибрид.

### Modified Capabilities

- `mcp-integration` (день 16): сценарий «инъекция не происходит
  (задел)» заменяется: `tools` уходит в payload при подключённых
  серверах; без подключённых серверов `tools` в payload по-прежнему
  нет (регресс `test_chat_payload_has_no_mcp_tools` остаётся зелёным).

## Impact

- **Код**: новый `studio/mcp_servers/task_manager.py`;
  `studio/backend/mcp.py` (третий дефолт + `[MCP Init]`-лог),
  `studio/backend/agent.py` (`_llm_tools`, tool-loop, лог-теги),
  фронтенд `src/state.tsx` + `components/ChatPanel.tsx` (role "tool" не
  рендерится).
- **Данные**: `dialogues.json` — новые типы записей (assistant с
  `tool_calls`, `role: "tool"` с `tool_call_id`/`name`);
  `mcp_servers.json` — третий дефолт Task Manager.
- **Зависимости**: никаких (task_manager — только stdlib; без npx).
- **Совместимость**: REST-эндпоинты не меняются; SSE-протокол
  `/api/chat` не расширяется (delta/done/error/invariant_violation);
  поведение без подключённых MCP-серверов — как в дне 16.
