## Purpose

Capability «mcp-digest» — 24/7 периодический дайджест (погода +
RU-tech новости) поверх MCP pull-моделей дней 16–17: собственный stdio
MCP-сервер `news_weather` (4 инструмента, только stdlib), единый
stdlib-сборщик `collector.py`, JSON-хранилище `data/digests/`
(трекается в git), GitHub Actions cron-воркфлоу как внешний 24/7-исполнитель
(коммитит дайджест раз в 6 часов), чтение результата агентом через
`get_latest_digest`. Пути дней 16–17 (реестр, tool-loop, UI) не меняются —
новый сервер и воркфлоу добавлены поверх.

## ADDED Requirements

### Requirement: Сборщик дайджеста (stdlib)

Система SHALL предоставлять модуль `studio/collector.py` (только stdlib,
без внешних зависимостей) с функцией `collect_digest(city="Самара",
now=None, fetch_weather=None, fetch_news=None)`, возвращающей dict
дайджеста: `{id: "digest-YYYYMMDD-HHMM", generated_at (ISO, UTC),
weather, news, summary}`. Сетевые функции `fetch_weather` / `fetch_news`
SHALL быть инжестируемыми (дефолты — реальные). `fetch_weather` (дефолт)
SHALL запросить Open-Meteo (geocoding + current weather, без API-ключа) и
вернуть `{city, temp_c, feels_like_c, description, wind_ms}`
(`description` — из WMO weather code, RU). `fetch_news` (дефолт) SHALL
запросить RSS-фиды sources (vc.ru, habr, tproger) и вернуть по source
список `{title, url}` — **top-5** на source, дедуп по URL. Сбой одного
source SHALL стать в `news.<source>` значением `{"error": "..."}`
(не исключение); сбой погоды — `weather: {"error": "..."}`. Поле
`summary` — детерминированная строка (шаблон) с городом, погодой и
общим числом новостей по sources. Функция записи SHALL атомарно
(tmp + `os.replace`) писать `last-digest.json` (перезапись) и
`history.json` (массив, **кап 96** — старейшая запись вытесняется).
Модуль SHALL иметь CLI-обёртку `python studio/collector.py [--out DIR]`
(собрать → записать в DIR → напечатать `summary`, exit 0).

#### Scenario: Полный дайджест (все фетчеры успешны)

- **WHEN** `collect_digest` вызван с успешными фетчерами и фиксированным `now`
- **THEN** возвращен dict с `id`/`generated_at` (из `now`), `weather` с 5 полями, `news` по 3 sources (≤5 items, уникальные URL), `summary` не пуст и содержит город

#### Scenario: Частичный сбой source

- **WHEN** один из RSS-фетчей падает (исключение/таймаут)
- **THEN** дайджест возвращается; в `news.<source>` — `{"error": "..."}`; остальные sources заполнены; процесс не падает

#### Scenario: Кап истории

- **WHEN** в `history.json` уже 96 записей и записывается 97-я
- **THEN** после записи в `history.json` ровно 96 записей, старейшая вытеснена, новая — в конце

### Requirement: MCP-сервер News & Weather (stdio)

Система SHALL предоставлять stdio MCP-сервер
`studio/mcp_servers/news_weather.py` (протокол 2024-11-05,
newline-delimited JSON-RPC 2.0 по stdin/stdout, только stdlib; сеть
требуется только для реальных фетчей). Сервер SHALL поддерживать
`initialize` (protocolVersion `2024-11-05`), `tools/list` и
`tools/call`; notification (без `id`) — без ответа; неизвестный метод —
JSON-RPC error `-32601`. Инструменты (описания — RU, с «когда звать /
когда нет»): `get_weather` (optional `city`, дефолт `Самара`),
`get_news` (optional `sources` — подмножество `["vc.ru","habr","tproger"]`,
дефолт все), `make_digest` (optional `city`; собрать сейчас + записать в
`data/digests/` + вернуть агрегат), `get_latest_digest` (без аргументов;
последний дайджест). Каталог данных по умолчанию
`<repo>/data/digests`, переопределяется env `DIGEST_DATA_DIR`; репозиторий
для GitHub-фолбэка — `DIGEST_GITHUB_REPO` (дефолт
`imarkelov/ai_advent_challenge`).
Ошибка фетча в любом инструменте SHALL стать результатом
`{"error": "..."}` с `isError: true`; процесс MUST NOT падать.

#### Scenario: Перечисление инструментов

- **WHEN** клиент вызывает `initialize` и `tools/list`
- **THEN** сервер отвечает protocolVersion `2024-11-05` и список из четырёх инструментов: `get_weather`, `get_news`, `make_digest`, `get_latest_digest`

#### Scenario: make_digest записывает JSON локально

- **WHEN** `tools/call` `make_digest` (успешная сеть или seeded-фетчеры)
- **THEN** в каталоге данных появляются/обновляются `last-digest.json` и `history.json`; результат — текст дайджеста (JSON) с `isError: false`

#### Scenario: Сбой фетча — ошибка результата, процесс жив

- **WHEN** `tools/call` `get_weather` при недоступной сети
- **THEN** результат `{"error": "..."}` с `isError: true`; последующий `tools/list` отвечает (процесс не упал)

### Requirement: get_latest_digest — локальный файл, фолбэк GitHub

Инструмент `get_latest_digest` SHALL читать `last-digest.json` из
каталога данных (env `DIGEST_DATA_DIR` / по умолчанию
`<repo>/data/digests`); при отсутствии файла SHALL попытаться получить
его из GitHub API public-репозитория (без API-ключа). Результат SHALL
включать поле `source`: `"local"` (файл) или `"github"` (API), а также
`generated_at` дайджеста. Ни локального файла, ни удалённого (или нет
сети) — результат `{"error": "..."}` с `isError: true`.

#### Scenario: Локальный дайджест

- **WHEN** в каталоге данных есть `last-digest.json` и вызывается `get_latest_digest`
- **THEN** результат — текст дайджеста с `source: "local"` и `isError: false`

#### Scenario: Фолбэк на GitHub

- **WHEN** локального файла нет, GitHub API доступен
- **THEN** результат — дайджест с `source: "github"`; при недоступности API — `{"error": "..."}` + `isError: true`

#### Scenario: Error-ветка детерминированна (тест)

- **WHEN** локального файла нет и `DIGEST_GITHUB_REPO` указывает на несуществующий репозиторий
- **THEN** результат — `{"error": "..."}` с `isError: true` (404 API или нет сети — в обоих случаях error, процесс жив)

### Requirement: Дефолт реестра

`MCPRegistry` SHALL включать **News & Weather** в дефолтные серверы
(рядом с Firecrawl, Git, Task Manager) с
`command = [sys.executable, <repo>/studio/mcp_servers/news_weather.py]`
(stdio, без npx, `enabled: true`).

#### Scenario: Дефолт запускается локальным python

- **WHEN** создаётся новый `MCPRegistry` и рассматривается дефолт News & Weather
- **THEN** `command[0]` — python (`sys.executable`), `command[1]` оканчивается `news_weather.py` и файл существует; `connect` → status `connected`, `tools_count` = 4

### Requirement: 24/7-исполнитель (GitHub Actions)

Репозиторий SHALL содержать workflow
`.github/workflows/digest.yml`: `on: schedule` — cron `'0 */6 * * *'`
(UTC) и `workflow_dispatch` (ручной запуск); `permissions:
contents: write`. Job SHALL: checkout → `python studio/collector.py --out
data/digests` → коммит изменений `data/digests/`
(`GITHUB_TOKEN`, автор `ai-advent-digest[bot]`); без изменений — без
коммита. Schedule SHALL выполняться на default branch (`master`).

#### Scenario: Ручной запуск воркфлоу

- **WHEN** запущен `workflow_dispatch` (или cron-тик) на ветке с workflow
- **THEN** job собирает дайджест и коммитит `data/digests/` (или пропускает коммит при отсутствии изменений); job — success

#### Scenario: Формат cron-расписания

- **WHEN** читается `.github/workflows/digest.yml`
- **THEN** есть `schedule` с cron-выражением `'0 */6 * * *'` и `workflow_dispatch`

### Requirement: Tool-loop дня 17 подхватывает сервер

Подключённый `news_weather` SHALL отдавать свои 4 инструмента в
LLM-payload через существующий `_llm_tools` (без изменений логики
tool-loop дня 17). Без подключённых серверов поле `tools` в тело
LLM-запроса MUST NOT попадать (регресс дня 16/17).

#### Scenario: Инструменты в payload

- **WHEN** сервер `news_weather` подключён и выполняется `/api/chat`
- **THEN** в body LLM-запроса `tools` содержит `get_weather`, `get_news`, `make_digest`, `get_latest_digest`

#### Scenario: Без серверов — без tools (регресс)

- **WHEN** `/api/chat` без подключённых MCP-серверов
- **THEN** тело LLM-запроса не содержит поля `tools`

### Requirement: Проверка (тесты и e2e)

Репозиторий SHALL содержать офлайн-тесты (pytest, без сети):
`collector` (схема, top-5/дедуп, частичные сбои, кап истории 96,
атомарная запись, `summary`), `news_weather` на реальном subprocess
(`DIGEST_DATA_DIR` → tmp: 4 tools, `get_latest_digest` local + error-ветка,
`-32601`, notification), четвёртый дефолт реестра. `scripts/e2e_day18.py`
(stdlib) SHALL быть гибридом: Part A — детерминированное ядро офлайн
(реальный subprocess `news_weather.py` через `MCPRegistry`; MUST PASS);
Part B — live (реальные Open-Meteo/RSS: `get_weather`, `get_news`,
`make_digest` + проверка записанного JSON), best-effort SKIP при
отсутствии сети; cleanup всегда; exit 0 для PASS/SKIP, 1 для FAIL.

#### Scenario: E2E прогон

- **WHEN** запущен `python scripts/e2e_day18.py`
- **THEN** Part A — все assert PASS (офлайн, детерминированно); Part B — PASS или SKIP; exit 0
