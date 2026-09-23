# Tasks: day18-mcp-digest

Ветка `day18-mcp-digest` (от `day17-mcp-tool-loop`). Порядок — TDD.

## 1. Открыть ветку и openspec-change

- [x] 1.1 Создать ветку `day18-mcp-digest` от `day17-mcp-tool-loop` — проверено: `git checkout -b`
- [x] 1.2 Создать `openspec/changes/day18-mcp-digest/` (`.openspec.yaml`, `proposal.md`, `design.md`, `specs/mcp-digest/spec.md`, `tasks.md`) — проверено: файлы написаны, self-review
- [x] 1.3 Коммит: openspec-change (design doc)
  <!-- commit: d9e4eeb -->

## 2. Сборщик `studio/collector.py` (TDD)

- [x] 2.1 Тесты collector (in-process, injected fake fetchers, `now` фиксирован): схема дайджеста, top-5 + дедуп, частичный сбой source, частичный сбой погоды, `summary` (город + число новостей) — проверено: тесты FAIL (модуля нет)
- [x] 2.2 Реализовать `collect_digest` (схема D2) + `build_summary` — проверено: тесты PASS
- [x] 2.3 Реальные фетчеры: `fetch_weather` (Open-Meteo geocoding + current, WMO-code → RU), `fetch_news` (RSS vc.ru/habr/tproger, top-5, дедуп, timeout, User-Agent) — проверено: live-проверка вручную (best-effort)
- [x] 2.4 Запись: `save_digest` (атомарно: tmp + `os.replace`; `last-digest.json` перезапись; `history.json` кап 96) + тесты истории (96→97 вытеснение, атомарность) — проверено: тесты PASS
- [x] 2.5 CLI `python studio/collector.py --out DIR` + smoke — проверено: локальный прогон пишет файлы
- [x] Коммит секции 2:
  <!-- commit: a8a6526, 56784ee, a2be5a8, c74ae2b -->

## 3. MCP-сервер `studio/mcp_servers/news_weather.py` (TDD)

- [x] 3.1 Тесты сервера (реальный subprocess, `DIGEST_DATA_DIR` → tmp): connect → 4 tools; `get_latest_digest` на seeded-файле (`source: local`); без файла + `DIGEST_GITHUB_REPO` → фейковый repo → `isError` (детерминированно); unknown method → `-32601`; notification → без ответа — проверено: тесты FAIL (сервера нет)
- [x] 3.2 Реализовать сервер по шаблону `task_manager.py` (2024-11-05, TOOLS + CALL_HANDLERS, импорты `collector` через `sys.path`, `DIGEST_DATA_DIR`) — проверено: тесты PASS
- [x] 3.3 Тесты live-инструментов в реальном subprocess вынести в e2e Part B (не в офлайн-тесты) — проверено: офлайн-тесты не требуют сети
- [x] Коммит секции 3:
  <!-- commit: 161eaa0 -->

## 4. Дефолт реестра

- [x] 4.1 Тест: четвёртый дефолт (command[0] = `sys.executable`, command[1] = `news_weather.py`, файл существует) — проверено: тест FAIL
- [x] 4.2 `mcp.py::_default_servers()`: запись `news-weather` — проверено: тест PASS + полный бэкенд-прогон зелёный
- [x] Коммит секции 4:
  <!-- commit: f93b5fd -->

## 5. GitHub Actions workflow

- [x] 5.1 `.github/workflows/digest.yml`: cron `'0 */6 * * *'` + `workflow_dispatch`, `permissions: contents: write`, checkout → `collector.py --out data/digests` → коммит `data/digests/` (guard на изменения, автор `ai-advent-digest[bot]`) — проверено: YAML валиден
- [x] 5.2 Запушить ветку; `workflow_dispatch` (или мерж в master → cron) — проверено: job success, коммит дайджеста в репо
  (best-effort: зависит от push-доступа; локально — синтаксис + lint) — мерж в master (`16537f8`), workflow_dispatch success, первый дайджест бота `d2df40c` (Самара 16.2°C, 15 новостей)
- [x] Коммит секции 5:
  <!-- commit: acfe45d -->

## 6. E2E `scripts/e2e_day18.py`

- [x] 6.1 Part A (офлайн, MUST PASS): subprocess `news_weather.py` через `MCPRegistry` → 4 tools → seed `last-digest.json` → `get_latest_digest` (схема + `source: local`) — проверено: прогон PASS
- [x] 6.2 Part B (live, best-effort SKIP): `get_weather` (Самара), `get_news` (≥1 source ≥1 item), `make_digest` → JSON создан + схема; exit 0/1 по паттерну дня 17 — проверено: прогон PASS/SKIP
- [x] Коммит секции 6:
  <!-- commit: 1983d8e (cp1251 fix, найден live-e2e), 2e3ce57 -->

## 7. Документация

- [x] 7.1 `README.md`: строка таблицы День 18 + секция `## День 18` (Что это / Архитектура / API / E2E / Проверка задания / Ветка) — проверено: по образцу дня 17
- [x] 7.2 `RELEASE.md`: запись наверху (Ветка, Что в релизе, API, Проверка задания, Коммиты) — проверено: по образцу
- [x] 7.3 Заполнить хеши коммитов в этом `tasks.md`
- [x] Коммит секции 7:
  <!-- commit: 3497f3c -->

## Итоговая проверка

- [x] Бэкенд: `cd studio/backend && python -m pytest -q` — всё зелёное (330 + новые → 350)
- [x] Фронтенд: без изменений (проверка: `npm test` / `tsc -b` clean — регресс)
- [x] E2E: `python scripts/e2e_day18.py` — Part A PASS (6/6), Part B PASS (live)
- [x] Живая проверка: Studio → «🧩» → News & Weather «Подключить» → 4 инструмента → в чате «дай сводку» (tool-loop: модель сама вызывает `get_latest_digest`/`get_news`)
