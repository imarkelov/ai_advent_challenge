# День 20 — Orchestration MCP: едицельные серверы и маршрутизация агента

Дата: 2026-09-25. Ветка: `day20-mcp-orchestration` (от `day19-mcp-pipeline`).
Статус: утверждён пользователем (итеративный brainstorming, решения Q1–Q5).

## 1. Задание дня

> Зарегистрируйте несколько MCP-серверов. Сделайте так, чтобы агент выбирал
> нужный инструмент, корректно маршрутизировал запросы, выполнял длинный
> флоу взаимодействия. Проверить: сценарий, в котором используются
> инструменты с разных серверов; корректность выбора и порядка вызовов.
> Результат: длинный флоу взаимодействия с несколькими MCP-серверами.

## 2. Утверждённые решения

| # | Решение |
|---|---|
| Q1 | Каждый **локальный** MCP-сервер = ровно **один** инструмент. Внешние (Firecrawl, Git) — не трогаем, их тулы управляемы не нами |
| Q2 | `news_weather` (4 тула) делится на 3 доменных сервера; `task_manager` (2) и `pipeline_tools` (3) — тоже делятся до «1 тул» |
| Q3 | Новый сервер `habr_news`: новости Habr по темам «тестирование» и «ИИ» (один тул `get_habr_news`) |
| Q4 | Маршрутизация = **A**: имена тулов в LLM-payload **всегда** с префиксом сервера + каталог «сервер → задача» в system-prompt; e2e ассертит точный выбор сервера |
| Q5 | Итоговый состав: **10 локальных** + 2 внешних = 12 в реестре |

## 3. Текущее состояние (день 19) — якоря

- `studio/backend/mcp.py` — MCP-клиент: `_StdioSession`, `_HttpSession`,
  `MCPClient`, `MCPRegistry`. Дефолты реестра — `MCPRegistry._default_servers()`
  (mcp.py:340–381): Firecrawl, Git, Task Manager, News & Weather,
  Pipeline Tools. Id сервера = `"mcp_" + uuid4().hex[:4]` (случайный,
  на каждый seed). Сид — только при пустом хранилище
  (`_ensure_defaults_locked`, mcp.py:383–391).
- `studio/backend/agent.py` — tool-loop в `ask_stream` (кап `range(5)`,
  agent.py:551); `_llm_tools()` (agent.py:445–474): префикс
  `{server_id}__` **только при коллизии имён**; `MCP_TOOLS_RULE`
  (agent.py:125–148) — правило композиции дня 19.
- Локальные серверы (stdio, newline JSON-RPC 2.0, 2024-11-05, stdlib):
  `task_manager.py` (2 тула, in-memory), `news_weather.py` (4 тула,
  `collector.py`), `pipeline_tools.py` (3 тула, `pdf_writer.py`).
  Скелетон JSON-RPC loop ~200 строк, скопирован 3 раза.
- `collector.py` — interface: `_default_fetch_weather(city)`,
  `_default_fetch_news(source)`, `_parse_rss_items(xml)`, `_dedup_top`,
  `collect_digest(city, now, fetch_weather=..., ...)`, `save_digest(digest,
  data_dir)`, `build_digest_id`, `build_summary`, `resolve_data_dir`.
- E2E-конвенция: Part A — детерминированное ядро в-процессе (реальные
  subprocess MCP-серверов + scripted fake-LLM на `httpx.MockTransport`),
  MUST PASS; Part B — live (uvicorn + реальный GPustack LLM), best-effort
  (PASS/SKIP, не FAIL). Порты: 17→8101, 18→8102, 19→8103, **20→8104**.

## 4. Целевая архитектура

### 4.1 Десять едицельных локальных серверов

Файлы в `studio/mcp_servers/`. Имена серверов в реестре — stable slug
(совпадает с именем файла). Каждый `tools/list` отдаёт **ровно один** тул.

| Сервер (имя в реестре) | Файл | Тул | Аргументы | Поведение / источник |
|---|---|---|---|---|
| `weather` | `weather.py` | `get_weather` | `city?` (default Самара) | Open-Meteo `current` + `daily.2d` (collector._default_fetch_weather). Сбой сети → `isError: true` |
| `news` | `news.py` | `get_news` | `sources?` (vc.ru\|habr\|tproger, default все) | top-5 на источник, дедуп (collector._default_fetch_news). Сбой одного источника → `{"error"}` у этого источника, остальные живы (паттерн дня 18) |
| `digest_make` | `digest_make.py` | `make_digest` | `city?` | collect_digest + save_digest в `data/digests/` (env `DIGEST_DATA_DIR`); возвращает JSON дайджеста |
| `digest_read` | `digest_read.py` | `get_latest_digest` | — | локальный `last-digest.json` → фолбэк GitHub API (env `DIGEST_GITHUB_REPO`) → `isError` |
| `task_create` | `task_create.py` | `create_task` | `title` (required), `description?` | **File-backed** `data/tasks.json` (env `TASKS_FILE`). Id = `TASK-<n>`, n = max(числовые id в хранилище) + 1. При отсутствии файла — сид TASK-42/TASK-7 (содержимое дня 17) |
| `task_get` | `task_get.py` | `get_task_details` | `task_id` (required) | Читает тот же `tasks.json`. Не найдено → `isError`, `{"error": "Задача не найдена: <id>"}` (паттерн дня 17) |
| `digest_search` | `digest_search.py` | `search` | `query` (required) | Субстринг по `data/digests/*.json` (env `PIPELINE_SEARCH_DIR`), top-20 по `generated_at` desc, возвращает готовое поле `text` (алгоритм дня 19) |
| `digest_summarize` | `digest_summarize.py` | `summarize` | `text` (required), `max_points?`=8 (max 20) | Детерминированная extractive-сводка (частотная оценка, **без LLM**, алгоритм дня 19) |
| `file_save` | `file_save.py` | `saveToFile` | `filename`, `content`, `format?`=md\|txt\|json\|pdf | Атомарная запись (tmp + `os.replace`) в `data/pipeline/` (env `PIPELINE_OUT_DIR`), basename-санитизация; pdf → `pdf_writer` |
| `habr_news` | `habr_news.py` | `get_habr_news` | `topics?` (массив: `testing` \| `ai`, default оба), `limit?`=10 | RSS Habr (URL из collector + `_parse_rss_items`), фильтр по заголовкам (ниже), top-`limit` по `published` desc. Сбой сети → `isError` |

**Фильтр тем `habr_news` (детерминированный, case-insensitive,
`re.UNICODE`):**
- `testing`: подстрока `тест` (покрывает тест/тестов/тестирование),
  word-boundary `\bqa\b`, `\btest\b`
- `ai`: word-boundary `\b(ai|llm|gpt|ml)\b` («maintain» не проходит),
  `\бии\b` (RU: «акции»/«студии» не проходят), подстроки `нейросет`,
  `machine learning`, `искусственный интеллект`
- Новость проходит, если заголовок совпал с паттерном **хотя бы одной**
  из запрошенных тем; результат помечает совпавшую тему (`topic`).
- Темы не приходят или неизвестное значение → 400-семантика
  `isError` «Неизвестная тема: …» (допустимые: testing, ai).

### 4.2 Общий каркас `_mcp_base.py`

stdlib, ~80 строк, заменяет трёхкратно скопированный скелетон:

```
run_server(name: str, tool: dict, handler: Callable[[dict], dict]) -> None
```

- stdin readline → JSON-RPC 2.0: `initialize` (protocolVersion
  2024-11-05, serverInfo `{name, version: "1.0.0"}`), `tools/list`
  (ровно 1 тул), `tools/call` (handler); notification (без `id`) — без
  ответа; неизвестный метод → `-32601`; исключение handler →
  `{"error": ...}` + `isError: true`, процесс не падает.
- Каждый из 10 файлов: определение `tool` (name/description/input_schema)
  + `handler(args)` + `run_server(...)` — итого ~40–60 строк на файл.

**Удаляются** `news_weather.py`, `task_manager.py`, `pipeline_tools.py`
(заменены 10 файлами). `collector.py` и `pdf_writer.py` — не трогаются,
переиспользуются.

### 4.3 Состояние `task_create` / `task_get` — file-backed

Два процесса → in-memory не общее. Хранилище `data/tasks.json`:
`{"tasks": {"TASK-7": {...}, "TASK-42": {...}}}`; запись атомарная
(tmp + `os.replace`), lock-файл не нужен (однопоточные процессы, запись
короткая). Env `TASKS_FILE` (default `<repo>/data/tasks.json`). При
отсутствии/пустом файле — сид TASK-42 (`in_progress`, исполнитель migor)
и TASK-7 (`done`) из дня 17. `task_create` пишет, `task_get` читает —
кросс-процессно и переживает рестарт.

### 4.4 Миграция `mcp_servers.json`

Существующие dev-реестры содержат «Task Manager»/«News & Weather»/
«Pipeline Tools», указывающие на удаляемые файлы. Логика
`_ensure_defaults_locked` расширяется:

1. Хранилище пустое → сид 12 дефолтов (как сейчас).
2. Хранилище содержит записи с name ∈
   `{"Task Manager", "News & Weather", "Pipeline Tools"}` → удалить эти
   записи, затем досеять отсутствующие (по name) новые дефолты.
   Пользовательские серверы (не из обоих множеств дефолтов) сохраняются,
   дубли не создаются.

### 4.5 Дефолты реестра — 12 записей

`_default_servers()`: Firecrawl, Git (без изменений) + 10 локальных
(`command = [sys.executable, <repo>/studio/mcp_servers/<file>.py]`,
`type: stdio`, `env: {}`, `enabled: true`). Порядок в списке: Firecrawl,
Git, weather, news, digest_make, digest_read, task_create, task_get,
digest_search, digest_summarize, file_save, habr_news.

## 5. Агентский слой (маршрутизация)

### 5.1 Всегда-префикс, стабильный slug

`_llm_tools()` (agent.py:445–474): `llm_name = f"{slug}__{name}"`
**для каждого** тула (не только при коллизии), где
`slug = re.sub(r"[^a-z0-9]+", "_", server_name.lower()).strip("_")`.
Имена локальных серверов уже slug-совместимы (`weather`, `digest_make`,
…), поэтому `slug(name) == name`. Внешние: `firecrawl__scrape`,
`git__git_status` и т.п. Страховка от коллизии llm_name сохранена
(дописывается `_`). `tool_map[llm_name] = (server_id, real_name)` —
маршрутизация обратно на сервер (id — как сейчас, uuid).

### 5.2 `MCP_TOOLS_RULE` v2 — каталог + правило маршрутизации

Статическая часть (заменяет `MCP_TOOLS_RULE`, agent.py:125–148):
- сохранить композицию дня 19 (передавать результат тула во вход
  следующего; few-shot с обновлёнными префиксованными именами);
- добавить: «Имена инструментов имеют вид `<сервер>__<инструмент>`.
  Выбери сервер по домену задачи, не угадывай. Если нужного сервера нет
  в каталоге — он не подключён: сообщи пользователю, а не вызывай
  похожий инструмент.»
- **динамический каталог** подключённых серверов строится из
  `mcp.tools()` (только подключённые): строка на сервер
  `<slug> — <описание домена>`; описания доменов — статическая карта в
  `agent.py` (DOMAINS: weather → «погода», news → «общие tech-новости
  vc.ru/habr/tproger», digest_make → «собрать и сохранить дайджест»,
  digest_read → «последний сохранённый дайджест», task_create →
  «создать задачу», task_get → «получить детали задачи», digest_search →
  «поиск по сохранённым дайджестам», digest_summarize → «сводка текста»,
  file_save → «сохранить файл md/txt/json/pdf», habr_news →
  «новости Habr: тестирование и ИИ»; неизвестный сервер → «тулы:
  <список имён>»).
- Инъекция — только когда есть подключённые тулы (как сейчас,
  agent.py:417–420).

### 5.3 Кап итераций

`TOOL_LOOP_CAP = int(os.environ.get("TOOL_LOOP_CAP") or "15")` (default
**15**, было 5; флоу §6 = 10 tool-вызовов + финал = 11 итераций —
влезает).
`for iteration in range(TOOL_LOOP_CAP)`; error-сообщение
параметризовано: «Tool-loop: превышен лимит итераций (N)».

### 5.4 Не меняется

`main.py` (роуты `/api/mcp/*` уже server-level), `mcp.py`-транспорт,
ручной вызов `/сервер тул` (день 16), фронтенд-панель «MCP»
(список серверов отрастит сам).

## 6. Длинный флоу (сценарий задания)

Один запрос пользователя → **10 вызовов через все 10 локальных серверов**:

```
 1. task_create__create_task     {title, description}        → TASK-<n>
 2. weather__get_weather         {city: "Самара"}            → погода
 3. news__get_news               {} (все источники)          → новости
 4. digest_make__make_digest     {city: "Самара"}            → дайджест сохранён
 5. digest_read__get_latest_digest {}                        → подтверждение: дайджест прочитан
 6. habr_news__get_habr_news     {topics: ["testing","ai"]}  → Habr: тесты + ИИ
 7. digest_search__search        {query: "Самара"}           → text
 8. digest_summarize__summarize  {text: <step7.text>}        → summary
 9. file_save__saveToFile        {filename, content: <step8.summary>, format: "pdf"}
10. task_get__get_task_details   {task_id: <step1.id>}       → детали задачи
→ финальный текстовый ответ
```

## 7. Обработка ошибок

- Ошибка любого тула → `{"error": ...}` + `isError: true`; процесс
  сервера не падает (паттерн дня 17).
- Сбой live-источника (Open-Meteo/RSS) → деградация паттерна дня 18
  (`{"error"}` в поле источника / `isError`), флоу не рвётся.
- Некорректные аргументы LLM (битый JSON, не-объект) → текст ошибки в
  tool-сообщение, цикл продолжается (паттерн дня 17).
- Тул с неизвестным llm_name → фолбэк на первый подключённый сервер
  (поведение дня 17, сохранено).
- Кап → SSE `error` с параметризованным сообщением.

## 8. Тесты (pytest, бэкенд — офлайн)

Новый `tests/test_mcp_servers_day20.py`:
- Параметризованно ×10: реальный subprocess сервера → `tools/list`
  ровно 1 тул, имя/описание/input_schema ожидаемые.
- Дефолты реестра: 12 записей, ожидаемый набор имён.
- Миграция: (a) пустое хранилище → 12; (b) старые 3 + кастомный сервер →
  старые удалены, 12 дефолтов досеяно, кастомный сохранён, дублей нет.
- Кросс-процессные задачи: `task_create` (процесс A) создаёт →
  `task_get` (процесс B) находит; новый файл → сид TASK-42/TASK-7;
  следующий id = max+1; не найдено → isError.
- `habr_news`-фильтр: фиктивный RSS-XML — RU-паттерны, word-boundary
  («maintain» ≠ «ai»), темы по одной, неизвестная тема → isError.
- `_mcp_base`: `-32601`, notification без ответа, исключение handler →
  isError.

Новые тесты в `test_agent.py` (или `test_agent_day20.py`):
- Все имена тулов в payload совпадают `^[a-z0-9_]+__[a-z0-9_]+$`
  (префикс **всегда**, без «голых» имён).
- `tool_map`: llm_name → ожидаемый (server_id, real_name).
- `TOOL_LOOP_CAP` из env: fake-LLM, зацикленный на tool_call, →
  error-сообщение с числом капа; 15 по умолчанию.
- Каталог: в system-prompt попадают только **подключённые** серверы с
  описанием домена.

Обновление существующих: day17-тесты (subprocess task_manager →
task_create/task_get; имена тулов теперь префиксованы), day18 (news_weather
→ 4 сервера), day19 (pipeline_tools → 3 сервера), тесты дефолтного
списка реестра (5 → 12).

## 9. E2E

### 9.1 `scripts/e2e_day20.py` (порт 8104)

**Part A** (MUST PASS; без uvicorn/сети; реальные subprocess всех 10
серверов + scripted fake-LLM):
- env на tmp-каталоги: `DIGEST_DATA_DIR`, `PIPELINE_SEARCH_DIR`,
  `PIPELINE_OUT_DIR`, `TASKS_FILE`; в tmp-дайджесты заранее посеян
  детерминированный дайджест с «Самара» (search не зависит от live-сети).
- Fake-LLM эмитит флоу §6 (10 tool_calls с префиксованными именами).
- Спай на `MCPRegistry.call_tool` фиксирует (server, tool, args).
- Ассерты:
  1. **Маршрутизация**: последовательность server-значений ==
     `[task_create, weather, news, digest_make, digest_read, habr_news,
     digest_search, digest_summarize, file_save, task_get]`;
  2. **Порядок**: 10 tool-сообщений в диалоге в том же порядке;
  3. **Передача данных**: `summarize.text` == `search.text` (step 7),
     `saveToFile.content` == `summarize.summary` (step 8),
     `task_get.task_id` == id из `create_task` (step 1);
  4. **Артефакты**: `day20_report.pdf` (`%PDF-1.4` + `/ToUnicode`) в
     tmp, задача из step 1 в `tasks.json`, дайджест step 4 в
     tmp-дайджестах, result step 5 содержит этот же дайджест;
  5. **Кап**: отдельный прогон — fake-LLM зациклен → SSE error с
     «превышен лимит итераций (15)».
- Live-сети в Part A нет ни на что не опираемся: сбой источника в
  `make_digest` — деградация, ассерты держатся.

**Part B** (live: uvicorn :8104 + реальный GPustack LLM, best-effort):
подключить все локальные серверы через API → запрос «создай задачу,
узнай погоду в Самаре, собери дайджест, найди в дайджестах про Самара,
суммаризируй и сохрани в PDF» → PASS (≥5 tool-сообщений, ≥3 разных
сервера, `done`), SKIP (модель не довела — поведение модели, не FAIL);
инфраструктурный сбой — FAIL. Cleanup всегда; exit 0 = PASS/SKIP.

### 9.2 Обновление e2e дней 17/18/19

Сценарии не меняются; указывают на новые серверы/файлы и
префиксованные имена: `e2e_day17.py` → task_create/task_get;
`e2e_day18.py` → weather/news/digest_make/digest_read; `e2e_day19.py` →
digest_search/digest_summarize/file_save (цепочка дня 19 становится
кросс-серверной — ассерты передачи данных сохраняются).

## 10. Фронтенд

Единственное изменение: бейджи тулов в блоке «Шаги агента»
(`ChatPanel.tsx`, день 19) рендерят `server__tool` как
`server · tool` (разделение по первому `__`) + обновление
соответствующих Vitest-кейсов. Остальное (панель «MCP», автодополнение
`/`, формы) — без изменений.

## 11. Документация и релиз

- `README.md`: строка в таблице дней + секция «День 20» (Что это /
  Архитектура / API / Проверка задания / Статус).
- `RELEASE.md`: блок дня 20 сверху (commit-хэши в конце).
- `openspec/changes/day20-mcp-orchestration/`: proposal.md, design.md,
  tasks.md, specs/mcp-orchestration/spec.md.
- `docs/superpowers/plans/2026-09-25-day20-mcp-orchestration.md` —
  implementation plan (skill writing-plans, после утверждения spec).
- `LINKS.md` — ссылка на демо-видео (skill `studio-demo-video`, в конце
  дня).

## 12. Риски

| Риск | Митигция |
|---|---|
| Мелкие модели не доводят 9-шаговую цепочку автономно (опыт дня 19) | Детерминированное доказательство — Part A; Part B best-effort |
| Always-префикс меняет имена тулов, которые «видели» дни 17–19 | E2E/тесты дней 17–19 обновляются в том же коммите; live — best-effort |
| Живой `mcp_servers.json` dev-машины со старыми записями | Миграция §4.4 + тесты |
| Habr RSS / Open-Meteo недоступны в Part A | Part A не опирается на live-источники (seeded-, tmp-env); деградация — ожидаемое поведение |

## 13. Вне scope (YAGNI)

- LLM-роутер отдельным этапом (решение Q4 = A, не B).
- Изменения ручного вызова `/сервер тул` (день 16).
- Новые UI-функции (кроме бейджа §10).
- HTTP-транспорт для локальных серверов (stdio — как в днях 17–19).
- LLM внутри MCP-серверов (серверы остаются офлайн/детерминированными).
- Изменения внешних серверов Firecrawl/Git.
