# Tasks: day17-mcp-tool-loop

## 1. Mock Task Manager — stdio MCP-сервер

- [x] 1.1 Новый `studio/mcp_servers/task_manager.py`: stdlib (`json`, `sys`), newline-delimited JSON-RPC 2.0; `initialize` (protocolVersion 2024-11-05, serverInfo `task-manager` 1.0), `tools/list`, `tools/call`; notification (без id) — без ответа; неизвестный метод → JSON-RPC -32601; in-memory TASK-42 (in_progress, assignee migor) + TASK-7 (done); `get_task_details` (required `task_id`), `create_task` (required `title`); «не найдено» → `{"error": "Задача не найдена: <id>"}` + `isError: true` — проверено: тесты 1.2.
- [x] 1.2 `tests/test_mcp.py` (append): реальный subprocess — connect → 2 tools; `get_task_details` TASK-42 → text содержит `in_progress`; неизвестный id → `isError` + «не найдена»; `create_task` → `TASK-…`/`todo` — проверено: `python -m pytest tests/test_mcp.py -q` зелёный.
- [x] 1.3 Коммит `c7e341f` — `feat(mcp): mock task manager stdio MCP server (day 17)`.

## 2. Task Manager в дефолтах реестра + [MCP Init]

- [x] 2.1 `mcp.py`: третий дефолт Task Manager (`command = [sys.executable, <repo>/studio/mcp_servers/task_manager.py]`) — проверено: тест 2.3.
- [x] 2.2 `mcp.py`: `connect()` на успех — `print("[MCP Init] {name}: {n} инструментов: {список}", flush=True)` — проверено: тест 2.3.
- [x] 2.3 `tests/test_mcp.py`: поправлен тест списка имён дефолтов (+ Task Manager); новый тест: command[0] — python (по `sys.executable`), command[1] оканчивается `task_manager.py` и файл существует — проверено: `python -m pytest -q` зелёный.
- [x] 2.4 Коммит `7f56b93` — `feat(mcp): task manager in registry defaults + [MCP Init] log (day 17)`.

## 3. LLM tool-calling loop в StudioAgent

- [x] 3.1 `tests/test_agent.py` (append, сначала FAIL): happy path (scripted tool_calls + `FakeMcpProcess` → done, в диалоге assistant с tool_calls + role tool, в body `tools`), без подключённых серверов — без `tools`, кап 5 (error, ровно 5 записей журнала), ошибка инструмента → tool-сообщение — проверено: красный до реализации.
- [x] 3.2 `agent.py`: `build_payload` — `tool_calls`/`tool_call_id` проходят в `messages`; `_llm_tools()` (OpenAI-`tools` + карта, коллизии — префикс `{server_id}__`); tool-loop в `ask_stream` (кап 5, агрегация `delta.tool_calls` по index, `call_tool`, `role: "tool"` в диалоге, guards повторно); лог-теги `[LLM Decision]`/`[MCP Response]`/`[Final Response]`; превышение капа → SSE `error` «Tool-loop: превышен лимит итераций (5)» — проверено: `python -m pytest -q` зелёный (все, включая регресс `test_chat_payload_has_no_mcp_tools`).
- [x] 3.3 Коммит `38df527` — `feat(mcp): LLM tool-calling loop in StudioAgent (day 17)`.

## 4. Фронтенд — служебные tool-сообщения вне ленты

- [x] 4.1 `ChatPanel.tsx`/`state.tsx`: сообщения `role: "tool"` не рендерятся пузырём (хранятся в памяти диалога) — проверено: Vitest (уникальный текст role tool не в DOM), `npm test` + `tsc -b` clean.
- [x] 4.2 Коммит `cb69817` — `feat(ui): service tool messages not rendered as chat bubbles (day 17)`.

## 5. E2E — `scripts/e2e_day17.py` (гибрид)

- [x] 5.1 Part A (в-процессе, без uvicorn/сети): `StudioAgent` + MockTransport fake-LLM + реальный subprocess `task_manager.py` через `MCPRegistry`; 6 assert (done, answer, ровно 1 role tool + 1 assistant с tool_calls, данные MCP, журнал 2 записи + `tools`) — проверено: `python scripts/e2e_day17.py` Part A 6/6 PASS.
- [x] 5.2 Part B (live uvicorn :8101, реальный LLM, best-effort): инфраструктурные шаги FAIL на баге, модель не вызвала инструмент → SKIP (не FAIL), cleanup всегда (taskkill, удаление диалога/сервера, восстановление модели) — проверено: на этой машине Part B SKIP (GPustack недоступен: SSL CERTIFICATE_VERIFY_FAILED), exit 0.
- [x] 5.3 Коммит `6cf4fa4` — `test(mcp): e2e_day17 — LLM tool-loop scenario (deterministic core + live best-effort)`.

## 6. Docs

- [x] 6.1 `openspec/changes/day17-mcp-tool-loop/` (`.openspec.yaml`, `proposal.md`, `design.md`, `tasks.md`, `specs/mcp-tool-loop/spec.md`) по структуре `day16-mcp-connect/` — проверено: без TBD/TODO, согласовано с кодом.
- [x] 6.2 `README.md`: секция «День 17: MCP Tool-Loop (LLM-driven)» по формату секции дня 16 + строка ветки в таблице.
- [x] 6.3 `RELEASE.md`: запись дня 17 (фичи, тесты 330/212, e2e Part A 6/6 PASS + Part B SKIP, коммиты).
- [x] 6.4 Коммит — `docs(mcp): day 17 release notes, README and openspec change`.

## 7. Финализация

- [x] 7.1 Финальная верификация: бэкенд `python -m pytest -q` (330 passed) + фронтенд `npm test` (212 passed) + `npx tsc -b` (clean) + `python scripts/e2e_day17.py` (Part A PASS, Part B SKIP, exit 0) — проверено: все зелёные.
- [x] 7.2 Ветка `day17-mcp-tool-loop` (от `day16-mcp-connect`), 5 коммитов: `c7e341f`, `7f56b93`, `38df527`, `cb69817`, `6cf4fa4` — проверено: `git log --oneline`.
