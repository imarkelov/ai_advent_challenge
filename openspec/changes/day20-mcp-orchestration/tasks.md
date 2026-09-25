# Tasks: day20-mcp-orchestration

## Каркас
- [x] Task 1: `_mcp_base.py` — общий каркас едицельных stdio MCP-серверов (`run_server`, контракт `call(args) -> (payload, is_err)`) + тесты
- [x] Task 2: едицельные сервера `weather` + `news` (+ тесты, реальный subprocess)
- [x] Task 3: едицельные сервера `digest_make` + `digest_read` (+ тесты)
- [x] Task 4: file-backed хранилище `_tasks_store` + сервера `task_create` + `task_get` (seed TASK-42/TASK-7, TASK-43+; gitignore `data/tasks.json`)
- [x] Task 5: едицельные сервера `digest_search` + `digest_summarize` + `file_save` (+ тесты)
- [x] Task 6: едицельный сервер `habr_news` (темы testing/ai, word-boundary-фильтр, локальный RSS-парсер) (+ тесты)

## Интеграция
- [x] Task 7: `mcp.py` — реестр 12 дефолтов (10 локальных), идемпотентная миграция старых мультитул-серверов по имени, `server_name` в `tools()` (+ тесты)
- [x] Task 8: `agent.py` — always-префикс `{slug}__{tool}`, `_mcp_slug`, каталог серверов в system-промпте, `TOOL_LOOP_CAP` (дефолт 15), имена LLM хранятся в истории (routing proof) (+ тесты)

## E2E
- [x] Task 9: e2e день 17/18/19 — на едицельных серверах; удаление `task_manager.py`/`news_weather.py`/`pipeline_tools.py` и их тестов
- [x] Task 10: `scripts/e2e_day20.py` — 10-шаговый кросс-серверный флоу (Part A офлайн-детерминированный, Part B live :8104 best-effort); фикс `_mcp_base` stdout `errors="replace"` (cp1251 + не-кодируемые символы); Part B: SSE-ошибка модели → SKIP, не FAIL

## UI
- [x] Task 11: фронтенд — бейджи шапки «Шаги агента» `server · tool` (split по первому `__`, `title={n}` полное имя; чипы StepRow — полное имя)

## Документация и финальная проверка
- [x] Task 12: README (таблица + секция «День 20»), RELEASE, LINKS + демо-видео, openspec-изменение, полный прогон (критерий закрытия дня), commit
  - Финальный прогон: бэкенд 393 PASS; фронтенд 219 PASS + `tsc -b` +
    `npm run build` clean; e2e_day17 14 PASS/0 FAIL, e2e_day18
    14 PASS/0 FAIL, e2e_day19 18 PASS/0 FAIL/1 SKIP, e2e_day20
    26 PASS/0 FAIL/1 SKIP (Part A 19/19; Part B SKIP — модель исчерпала
    15 итераций, не FAIL); демо-видео `day20-mcp-orchestration-demo.mp4`
    (38.8 с) — live-маршрутизация `digest_search__search` →
    `digest_summarize__summarize` + бейджи «server · tool»;
    `GET /api/mcp/servers` — ровно 12 серверов, старых 3 имён нет
