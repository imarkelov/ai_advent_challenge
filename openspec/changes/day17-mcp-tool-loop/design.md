# Design: day17-mcp-tool-loop

## Context

See proposal.md — Why.

Актуальное состояние (студия, после дня 16, ветка `day16-mcp-connect`):

- **MCP подключён, но пассивен**: `mcp.py` (`MCPClient`/`MCPRegistry`,
  stdio/http), реестр `mcp_servers.json` (дефолты Firecrawl, Git), REST
  `/api/mcp/*`, панель «MCP». Вызов — только командой пользователя
  `/имя-сервера имя-тула` (роут
  `POST /api/mcp/servers/{id}/tools/{tool}`, system-маркер `mcp_tool`).
  В тело LLM-запроса `tools` не идёт (регресс-тест
  `test_chat_payload_has_no_mcp_tools`).
- **`agent.py`**: `build_payload` собирает `messages` из диалога;
  `ask_stream` — один LLM-стрим на запрос (guards: память/табу/
  инварианты), SSE-кадры delta/done/error; журнал LLM-запросов
  (`_append_request_log`), usage.
- **Диалог**: `MemoryStore.append_message` умеет хранить
  assistant-сообщения с маркерами (паттерн `mcp_tool` дня 16) — задел
  для tool-полей.
- **Тесты**: pytest (бэкенд, `MockTransport`, `FakeMcpProcess`),
  Vitest (фронт), `scripts/e2e_studio.py` (prod + live).

## Goals / Non-Goals

**Goals:**

- Модель сама вызывает инструменты подключённых MCP-серверов:
  `tools` в payload → `tool_calls` → вызов → `role: "tool"` → цикл →
  финальный ответ (кап 5 итераций).
- Собственный детерминированный MCP-сервер (Mock Task Manager) для
  проверки и обучения: stdio, запускается как отдельный процесс, без
  npx/сети.
- Визуализация этапов в консоли: `[MCP Init]`, `[LLM Decision]`,
  `[MCP Response]`, `[Final Response]`.
- E2E-гибрид: детерминированное ядро (MUST PASS, офлайн) + live
  best-effort (PASS/SKIP).
- Пути дня 16 не ломаются (роут вызова, `mcp_tool`, регресс «без
  подключённых серверов — без `tools`»).

**Non-Goals:**

- Новые REST-эндпоинты; расширение SSE-протокола `/api/chat`.
- Параллельные/асинхронные вызовы инструментов, стриминг самого
  MCP-вызова.
- UI-карточки tool-вызовов (служебные сообщения не рендерятся).
- Новые pip/npm-зависимости.

## Decisions

### D1: Mock Task Manager — отдельный файл, stdlib, без npx

`studio/mcp_servers/task_manager.py`: newline-delimited JSON-RPC 2.0
по stdin/stdout, только `json`/`sys`. Дефолт в реестре —
`[sys.executable, <путь>]` (путь — как в `mcp.py`:
`os.path.abspath(os.path.join(data_dir, "..", ".."))`). Альтернативы:
`npx`-пакет — отклонено (нужна сеть, недетерминированно); встроить в
`mcp.py` — отклонено (сервер — самостоятельный deliverable,
запускается как отдельный процесс, так же как Firecrawl/Git).
In-memory данные предзагружены (TASK-42 `in_progress`/migor, TASK-7
`done`) — детерминизм тестов; перезапуск сбрасывает (задокументировано
в модульной доке сервера).

### D2: Tool-loop в `ask_stream`, кап 5 итераций

Основной стрим-блок оборачивается `for iteration in range(5)`:

- `_llm_tools()` → `(openai_tools | None, {llm_name: (server_id,
  real_name)})`; пусто → `None` → `tools` в body не идёт (поведение
  дня 16).
- Агрегация `delta.tool_calls` по `index`: `{id, name, arguments}`
  (arguments — конкатенация строк из чанков; name может прийти в
  любом чанке).
- Есть `tool_calls`: сохранить assistant-сообщение с `tool_calls`;
  по каждому — parse args (сбой → `{"error": ...}` как результат),
  `self.mcp.call_tool` (MCPError → текст ошибки), результат —
  `append_message(dialogue_id, "tool", text, tool_call_id=...,
  name=...)`; `build_payload` заново; guards (память/табу/инварианты)
  повторно по исходному пользовательскому сообщению; `continue`.
- Нет `tool_calls`: текущий done-путь + `[Final Response]`;
  `_append_request_log` на КАЖДУЮ итерацию; usage суммируется.
- После 5-й итерации с tool_calls → SSE `error` «Tool-loop: превышен
  лимит итераций (5)».

Альтернатива: отдельный рекурсивный метод `_tool_loop` — отклонено,
цикл в `ask_stream` ближе к существующей структуре и проще
офлайн-тестится.

### D3: Формат `tools` — OpenAI, коллизии — префикс

`{"type": "function", "function": {"name", "description",
"parameters"}}`; `parameters` = `input_schema` тула (пусто →
`{"type": "object"}`). Имённые коллизии между серверами: первый
победитель, далее префикс `{server_id}__`; внутренняя карта
`llm_name → (server_id, real_name)` возвращает обратно для
`call_tool`.

### D4: Ошибки инструмента — в диалог, цикл продолжается

MCPError / битые JSON-аргументы не роняют агента: текст ошибки
становится tool-сообщением (модель видит и может сформулировать
ответ/повторить), без SSE-error. SSE `error` — только превышение
лимит итераций и LLM-ошибки (текущий путь дня 16).

### D5: Логирование этапов

Четыре тега ровно: `[MCP Init]` (connect, `mcp.py`), `[LLM Decision]`
(имя + args), `[MCP Response]` (сырой текст результата),
`[Final Response]` (ответ, 200 символов) — `print(..., flush=True)` в
stdout. Консоль — учебный вывод «что происходит в цикле»; в журнал
LLM-запросов пишется каждая итерация (тело с `tools`).

### D6: Фронтенд — `role: "tool"` вне ленты

`state.tsx` + `ChatPanel.tsx`: сообщения `role: "tool"` — служебные
записи (паттерн mcp_tool-карточек дня 16): хранятся в памяти диалога
(LLM видит), не рендерятся пузырём; итог даёт финальный LLM-ответ.

### D7: E2E-гибрид `scripts/e2e_day17.py`

Part A — в-процессе, без uvicorn/сети: `StudioAgent` +
`httpx.MockTransport` fake-LLM (скриптованные `tool_calls`) + реальный
subprocess `task_manager.py` через `MCPRegistry`; 6 assert (MUST
PASS). Part B — live uvicorn :8101, реальный LLM GPustack:
инфраструктурные шаги FAIL на реальном баге; модель не вызвала
инструмент → SKIP (поведение модели, не баг скрипта); cleanup всегда.
Exit 0 — PASS/SKIP, 1 — FAIL. Альтернатива: только live — отклонено
(недетерминированно, зависит от доступности GPustack).

### D8: Тесты

- `tests/test_mcp.py`: task_manager на реальном subprocess (connect →
  2 tools; `get_task_details` TASK-42 → `in_progress`; неизвестный id
  → `isError` + «не найдена»; `create_task` → `TASK-…`/`todo`);
  третий дефолт (command[0] = python по `sys.executable`,
  command[1] = `task_manager.py`, файл существует).
- `tests/test_agent.py`: happy path (tool_calls → tool-сообщение →
  done; в диалоге assistant с tool_calls + role tool; в body первой
  итерации `tools`), без подключённых серверов — без `tools`, кап 5
  (ровно 5 LLM-вызовов, error), ошибка инструмента → tool-сообщение
  с текстом ошибки → done.
- Фронтенд: role "tool" не виден в DOM.

## Risks / Trade-offs

- **Мелкие модели могут не вызвать инструмент** (live): Part B
  best-effort SKIP; детерминизм держит Part A. → D7.
- **Бесконечный цикл модели** (всегда tool_calls): кап 5, SSE error.
  → D2.
- **Коллизии имён тулов** между серверами: префикс `{server_id}__`;
  обратная карта — внутренняя. → D3.
- **Разрастание `ask_stream`**: цикл + ветки удлиняют метод;
  принимается ради прозрачности учебного кода. → D2.

## Migration Plan

Аддитивные правки + новый файл; старые инстансы `mcp_servers.json`
подхватывают третий дефолт при следующем чтении реестра. Rollback —
удалить цикл и `_llm_tools` (`agent.py` вернётся к дню 16);
`task_manager.py` — самостоятельный файл, может остаться.

## Open Questions

- Нет (закрыты при реализации: формат `tools` — OpenAI; кап — 5;
  коллизии — префикс `{server_id}__`; ошибки инструментов — в диалог).
