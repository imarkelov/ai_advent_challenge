# Design: day18-mcp-digest

## Context

See proposal.md — Why.

Актуальное состояние (ветка `day17-mcp-tool-loop`):

- **MCP pull-модель**: MCP-серверы — отдельные subprocess'ы
  (`_StdioSession`), обратного канала server → agent/LLM нет. Модель сама
  дёргает инструменты в tool-loop (день 17, кап 5 итераций).
- **`task_manager.py`** (день 17) — канонический шаблон собственного
  stdio MCP-сервера: только stdlib, newline-delimited JSON-RPC 2.0,
  `TOOLS` + `CALL_HANDLERS`, `initialize`/`tools/list`/`tools/call`,
  unknown method — `-32601`.
- **`MCPRegistry._default_servers()`** — дефолты Firecrawl, Git, Task
  Manager; seed только при пустом `mcp_servers.json`.
- **Тесты**: pytest (бэкенд, offline: subprocess + `MockTransport`),
  Vitest (фронт), e2e-гибриды `scripts/e2e_dayN.py` (Part A MUST PASS
  offline, Part B live best-effort).
- **GitHub**: репозиторий публичный на GitHub
  (`imarkelov/ai_advent_challenge`), default branch `master`,
  `.github/workflows/` нет.

## Goals / Non-Goals

**Goals:**

- Периодический (раз в 6 часов) сбор данных (погода + RU-tech новости) и
  агрегированный результат в JSON, 24/7 — без локального демона.
- Собственный stdio MCP-сервер `news_weather` (шаблон дня 17) с
  инструментами `get_weather` / `get_news` / `make_digest` /
  `get_latest_digest` — модель в tool-loop сама собирает и читает сводку.
- JSON-хранилище `data/digests/` (трекается в git) — репозиторий как
  message bus между 24/7-исполнителем и агентом.
- Единый код сбора (`collector.py`) для MCP-сервера и GitHub Actions —
  одинаковый результат с двух сторон.
- E2E-гибрид: детерминированное офлайн-ядро (MUST PASS) + live
  best-effort (PASS/SKIP).

**Non-Goals:**

- Разовые отложенные reminder'ы (задание: «отложенное **или**
  периодическое» — выбрано периодическое).
- Новые REST-эндпоинты, расширение SSE-протокола, изменения UI/фронта.
- LLM в воркфлоу/сборщике (агрегат детерминированный; LLM-агрегация —
  в чате при чтении результата).
- SQLite (задание и пользователь выбрали JSON).
- Новые pip/npm-зависимости, API-ключи.
- Push-уведомления агенту (нет канала в архитектуре pull-моделей).

## Decisions

### D1: 24/7-исполнитель — GitHub Actions cron, не in-process таймер

Внешний cron-воркфлоу: `on: schedule: cron '0 */6 * * *'` (UTC) +
`workflow_dispatch` (ручная проверка). Job: checkout →
`python studio/collector.py --out data/digests` → коммит `data/digests/`
(`GITHUB_TOKEN`, `permissions: contents: write`, автор
`ai-advent-digest[bot]`); коммит — только при изменениях
(`git diff --quiet` guard). Альтернативы: in-process `threading.Timer` /
`APScheduler` — отклонено (процесс умирает вместе со Studio/сервером, не
24/7, плюс зависимость); планировщик ОС (Windows Task Scheduler) —
отклонено (непереносимо, не воспроизводимо, не видно в репо). Цена:
коммит в репо каждые 6ч (принято — это журнал исполнения); free-plan
cron может задерживаться (для дайджеста допустимо); schedule fires только
на `master` — до мержа проверка через `workflow_dispatch` (ветка
запушена).

### D2: JSON в `data/digests/`, репозиторий = message bus

`data/digests/last-digest.json` — последний дайджест (перезапись),
`data/digests/history.json` — rollying-массив **полных** дайджестов,
**кап 96** (24 дня × 4/сутки); старое отваливается с конца. Запись атомарная (tmp + `os.replace`,
паттерн `memory.py`). Каталог — в корне репозитория (НЕ `studio/data/` —
тот gitignored; дайджесты должны коммититься). Схема дайджеста:

```json
{
  "id": "digest-20260923-1200",
  "generated_at": "2026-09-23T12:00:00Z",
  "weather": {"city": "Самара", "temp_c": 21.4, "feels_like_c": 19.8,
               "description": "Пасмурно", "wind_ms": 3.1},
  "news": {"vc.ru": [{"title": "...", "url": "..."}],
           "habr": [...], "tproger": [...]},
  "summary": "Сводка digest-...: Самара +21°C, Пасмурно; 15 новостей (vc.ru 5, habr 5, tproger 5)."
}
```

`summary` — детерминированная строка (шаблон, без LLM). Частичный сбой:
упавший source — `{"error": "..."}` внутри `news.<source>`, погода упала —
`weather: {"error": "..."}`; дайджест пишется и возвращается.
Альтернативы: SQLite — отклонено (JSON по заданию/пользователю; читается
из git); HTTP-push на локальный сервер — отклонено (нет постоянно
достигаемого адреса у локальной студии).

### D3: Единый сборщик `studio/collector.py` (single source of truth)

Модуль только stdlib: `collect_digest(city="Самара", now=None,
fetch_weather=None, fetch_news=None) -> dict` — сетевые функции
инжестируются (офлайн-тесты, детерминизм `generated_at` через `now`).
Реальные фетчеры: `fetch_weather` — Open-Meteo geocoding
(`geocoding-api.open-meteo.com/v1/search`) + current weather
(`api.open-meteo.com/v1/forecast`, `timezone=auto`), WMO-code → RU-описание
(маленькая карта кодов); `fetch_news` — RSS (vc.ru `https://vc.ru/rss`,
habr `https://habr.com/ru/rss/news/?fl=ru`, tproger
`https://www.tproger.ru/feed/`) через `urllib` + `xml.etree.ElementTree`,
top-5 на source, дедуп по URL, timeout 15 c, `User-Agent` заголовок.
CLI: `python studio/collector.py [--out DIR]` — собирает, пишет
`last-digest.json` + обновляет `history.json`, печатает `summary`. MCP-
сервер импортирует тот же модуль (`sys.path` + `studio/`) — один код,
два вызывающих (локально и в воркфлоу). Альтернатива: дублировать логику
в workflow-скрипте — отклонено (расхождение кода).

### D4: MCP-сервер `news_weather.py` (шаблон дня 17)

`studio/mcp_servers/news_weather.py`: newline-delimited JSON-RPC 2.0
(stdin/stdout), `json`/`sys`/`os`/`urllib` + импорты `collector`.
`initialize` (2024-11-05), `tools/list` (4 инструмента), `tools/call`;
notification (без `id`) — без ответа; unknown method — `-32601`.
Инструменты (описания — богатые RU, «когда звать / когда нет», паттерн
дня 17):

- `get_weather` — optional `city` (string, дефолт `Самара`): текущая
  погода.
- `get_news` — optional `sources` (array of `vc.ru|habr|tproger`,
  дефолт все): свежие заголовки.
- `make_digest` — без аргументов (optional `city`): собрать сейчас,
  записать в `data/digests/` (локально), вернуть агрегат.
- `get_latest_digest` — без аргументов: последний дайджест; **локальный
  `data/digests/last-digest.json` первым**, нет файла → GitHub API
  (`api.github.com/repos/imarkelov/ai_advent_challenge/contents/data/digests/last-digest.json`,
  base64, без ключа — public repo); payload включает `source:
  "local"|"github"` и `generated_at` (модель видит свежесть). Нет ни
  локального, ни удалённого — `{"error": ...}` + `isError: true`.

Env-переопределения: `DIGEST_DATA_DIR` (дефолт `<repo>/data/digests`) —
офлайн-тесты сервера в subprocess указывают tmp-каталог; `DIGEST_GITHUB_REPO`
(дефолт `imarkelov/ai_advent_challenge`) — офлайн-тесты указывают
несуществующий repo → детерминированная 404-ветка `get_latest_digest`
(аналог `MCP_CONNECT_TIMEOUT` из `.env`-таймаутов дня 16). Ошибка фетча в любом
инструменте — `{"error": "..."}` + `isError: true`, процесс не падает.
Альтернатива: github-first — отклонено (сеть не нужна в обычном dev-флоу;
`source` в payload делает выбор прозрачным).

### D5: Дефолт реестра

`mcp.py::_default_servers()` + запись `news-weather`
(`type: stdio`, `command = [sys.executable, <repo>/studio/mcp_servers/news_weather.py]`,
`enabled: true`) рядом с Task Manager. Старые инстансы (реестр непуст)
дефолт не подхватывают — ограничение seed-логики дня 16, не меняется;
e2e-Part A добавляет сервер через `POST /api/mcp/servers` (паттерн дня
16) или напрямую в store тестов.

### D6: Тесты

- `studio/backend/tests/test_news_weather.py`:
  - **collector** (in-process, injected fake fetchers, `now` фиксирован):
    схема дайджеста (все поля), top-5 + дедуп, частичный сбой source /
    погоды (error-объект, не исключение), история: кап 96 (97-я запись
    вытесняет старейшую), атомарная запись (файл валиден), `summary`
    содержит город + число новостей.
  - **сервер** (реальный subprocess, `DIGEST_DATA_DIR` → tmp): connect →
    4 инструмента; `get_latest_digest` на seeded-файле → дайджест +
    `source: local`; без файла и без сети (GitHub API недосягаем в
    изоляции) → `isError` + error; unknown method → `-32601`;
    notification → без ответа.
  - **реестр**: четвёртый дефолт (command[0] = python,
    command[1] = `news_weather.py`, файл существует).
  - Регресс: tool-loop «без подключённых серверов — `tools` в payload
    нет» остаётся зелёным (сервер новый, `_llm_tools` не тронут).
- Живые сетевые тулы (`get_weather`/`get_news`/`make_digest` в реальном
  subprocess) — не в офлайн-тестах; покрыты e2e Part B.

### D7: E2E-гибрид `scripts/e2e_day18.py`

Part A (офлайн, MUST PASS): реальный subprocess `news_weather.py` через
`MCPRegistry` (`DIGEST_DATA_DIR` → tmp): connect → 4 tools → seed
`last-digest.json` → `get_latest_digest` (схема + `source: local`) →
`tools/list` стабильность. Part B (live, best-effort SKIP при отсутствии
сети): `get_weather` (Самара), `get_news` (≥1 source с ≥1 item),
`make_digest` → файл `data/digests/last-digest.json` создан + схема
валидна. Cleanup всегда; exit 0 — PASS/SKIP, 1 — FAIL (паттерн
`e2e_day17.py`).

### D8: Документация

- openspec-change `day18-mcp-digest` (этот артефакт).
- `README.md`: строка таблицы `| День 18 | [day18-mcp-digest](…/tree/day18-mcp-digest) | … |`
  + секция `## День 18: Планировщик и фоновые задачи (MCP digest 24/7)`
  по образцу дня 17 (Что это / Архитектура / API / E2E / Проверка
  задания / Ветка).
- `RELEASE.md`: новая запись наверху (Ветка, Что в релизе, API, Проверка
  задания, Коммиты).

## Risks / Trade-offs

- **GitHub cron на free-plan задерживается / не гарантирован**: для
  дайджеста с горизонтом 6ч допустимо; `workflow_dispatch` даёт ручную
  проверку. → D1.
- **Шум коммитов** в `data/digests/` (4/сутки): принят — это журнал
  24/7-исполнения; history.json кап 96 ограничивает рост файла. → D2.
- **RSS-фиды меняют структуру/исчезают**: частичный сбой (error-объект
  на source), дайджест не рвётся; map фидов — константа в `collector.py`
  (легко правится). → D3.
- **`get_latest_digest` локальный-first может отдать устаревший
  дайджест** (локальный есть, но GitHub свежее): `source` + `generated_at`
  в payload дают модели/пользователю видеть свежесть; `make_digest`
  обновляет локальный. → D4.
- **GitHub API rate-limit** (60/h без ключа, аноним): один GET на
  вызов тула — в пределах лимита. → D4.
- **Сервер с сетевыми тулами в офлайн-тестах**: только protocol +
  `get_latest_digest` (offline-части); live — e2e Part B. → D6/D7.

## Migration Plan

Полностью аддитивно: 4 новых файла + 1 правка (`_default_servers`) +
доки. Откат — удалить дефолт из `mcp.py`, удалить файлы; `data/digests/`
и workflow безвредны без сервера. Rollback воркфлоу — удалить/отключить
`.github/workflows/digest.yml` (cron перестаёт коммитить).

## Open Questions

- Нет (закрыты при согласовании: частота — 6ч; данные — JSON; новости —
  RU tech vc.ru/habr/tproger; погода — Самара по дефолту; 24/7 — GitHub
  Actions; разовые reminder'ы — не в scope).
