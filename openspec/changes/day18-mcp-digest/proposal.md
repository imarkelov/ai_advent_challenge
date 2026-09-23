# Proposal: day18-mcp-digest

## Why

День 18: **планировщик и фоновые задачи** — MCP-инструмент с отложенным
или периодическим выполнением (примеры задания: reminder, периодический
сбор данных, регулярный summary). Итог — «агент, который работает 24/7 и
периодически выдаёт сводку». Ключевое ограничение архитектуры: MCP-сервер
— отдельный subprocess без обратного канала в LLM (pull-модель дней
16–17), поэтому «24/7» не реализуется in-process таймером. Решение:
**периодическое исполнение — внешний 24/7-исполнитель (GitHub Actions
cron)**, а **собственный MCP-сервер** собирает данные (погода + новости)
по запросу агента и читает результаты воркфлоу. Данные — JSON
(реестр-репозиторий = message bus: воркфлоу коммитит дайджест, агент
читает его через инструмент). Частота — раз в 6 часов (решение
пользователя); разовые reminder'ы из scope выведены («отложенное
**или** периодическое»).

## What Changes

- **`studio/collector.py`** (новый, только stdlib): единый сборщик
  дайджеста — `collect_digest(city="Самара")` → `{id, generated_at,
  weather, news, summary}`. Погода — Open-Meteo (geocoding + current
  weather, без API-ключа). Новости — RSS vc.ru / habr / tproger
  (`urllib` + `xml.etree`, top-5 на source, дедуп по URL). Фетчеры
  инжестируются (офлайн-тесты). Ошибка одного source не роняет дайджест
  (partial). CLI-обёртка `python studio/collector.py --out DIR` — её
  дёргает GitHub Actions.
- **`data/digests/`** (новый, трекится в git): `last-digest.json`
  (последний дайджест, перезаписывается) + `history.json` (rollying-массив,
  кап 96 записей = 24 дня при 6ч). Атомарная запись (tmp + `os.replace`).
- **`studio/mcp_servers/news_weather.py`** (новый, только stdlib): stdio
  MCP-сервер (2024-11-05, newline-delimited JSON-RPC) по образцу
  `task_manager.py` дня 17. Инструменты: `get_weather` (required `city`
  нет, дефолт Самара), `get_news` (optional `sources`), `make_digest`
  (собрать + записать в `data/digests/` + вернуть агрегат),
  `get_latest_digest` (локальный `last-digest.json`, фолбэк — GitHub API
  public repo, без ключа; в payload поле `source: local|github`).
  Ошибка фетча — `{"error": ...}` + `isError: true`, процесс не падает.
  Env-переопределение `DIGEST_DATA_DIR` (офлайн-тесты).
- **`mcp.py`**: `news-weather` — четвёртый дефолт реестра
  (`command = [sys.executable, <repo>/studio/mcp_servers/news_weather.py]`).
  Старые инстансы `mcp_servers.json` (реестр не пуст) дефолт не
  подхватывают (поведение дня 16: seed только при пустом) — e2e добавляет
  через `POST /api/mcp/servers`.
- **`.github/workflows/digest.yml`** (новый): `on: schedule: cron
  '0 */6 * * *'` (UTC) + `workflow_dispatch` (ручной запуск/проверка);
  `permissions: contents: write`; checkout → `python studio/collector.py
  --out data/digests` → коммит `data/digests/` (`GITHUB_TOKEN`).
  Schedule fires только на default branch (`master`) — воркфлоу живёт
  после мержа `day18-mcp-digest` → `master`; до мержа проверка через
  `workflow_dispatch`.
- **E2E** (`scripts/e2e_day18.py`, новый, stdlib): Part A —
  детерминированное ядро офлайн (реальный subprocess `news_weather.py`
  через `MCPRegistry`, `get_latest_digest` на seeded-файле, MUST PASS);
  Part B — live (реальные Open-Meteo/RSS, `make_digest` пишет JSON),
  best-effort SKIP.
- **Агент/фронтенд/роуты не трогаются**: tool-loop дня 17 сам подхватит
  4 новых инструмента подключённого сервера (`_llm_tools`).

## Capabilities

### New Capabilities

- `mcp-digest`: 24/7 периодический дайджест (погода + новости) —
  собственный stdio MCP-сервер `news_weather`, единый stdlib-сборщик
  `collector.py`, JSON-хранилище `data/digests/`, GitHub Actions cron
  воркфлоу как внешний 24/7-исполнитель, чтение результата через
  `get_latest_digest`, e2e-гибрид.

### Modified Capabilities

- `mcp-integration` (день 16): четвёртый дефолт реестра
  `news-weather` (stdio, локальный python, без npx); регресс: без
  подключённых серверов `tools` в LLM-payload нет — не меняется.
- `mcp-tool-loop` (день 17): новый сервер даёт модели 4 инструмента для
  tool-loop (`get_weather`/`get_news`/`make_digest`/`get_latest_digest`);
  логика цикла не меняется.

## Impact

- **Код**: новые `studio/collector.py`,
  `studio/mcp_servers/news_weather.py`,
  `.github/workflows/digest.yml`, `scripts/e2e_day18.py`,
  `studio/backend/tests/test_news_weather.py`; правка
  `studio/backend/mcp.py` (`_default_servers`).
- **Данные**: новый трекатый `data/digests/` (`last-digest.json`,
  `history.json`); `mcp_servers.json` — четвёртый дефолт (новые
  инстансы).
- **Зависимости**: никаких (только stdlib; без npx; без API-ключей —
  Open-Meteo keyless, RSS, GitHub API public).
- **Совместимость**: REST/SSE/UI не меняются; старые дефолты
  Firecrawl/Git/Task Manager на месте; коммиты воркфлоу в `data/digests/`
  — ожидаемый «шум» истории (это и есть журнал 24/7-исполнения).
