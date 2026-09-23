# Release Notes — day18-mcp-digest (день 18)

Ветка: [`day18-mcp-digest`](https://github.com/imarkelov/ai_advent_challenge/tree/day18-mcp-digest)
(от `day17-mcp-tool-loop`).

## Что в релизе

**Периодический дайджест 24/7.** MCP-инструмент с периодическим
выполнением: сохраняет данные (JSON), выполняется по расписанию
(GitHub Actions cron), возвращает агрегированный результат. Агент
отвечает на «покажи последнюю сводку» через tool-loop дня 17 — модель
сама вызывает `get_latest_digest`.

- `studio/collector.py` — общий stdlib-коллектор (только stdlib,
  `urllib`/`xml`/`json`): `collect_digest` (погода Open-Meteo
  (геокодинг + `current` + `daily.2d`, WMO-code → RU) + новости
  vc.ru/habr/tproger — top-5 на источник, дедуп по нормализованному
  заголовку), `build_summary` (город + число новостей), `save_digest`
  (атомарно: tmp + `os.replace`; `last-digest.json` перезапись;
  `history.json` кап 96 = 4 дня × 6/ч), CLI `--out DIR --city C`.
  Сбой источника — его поле `{"error": ...}`, дайджест не гибнет.
- `studio/mcp_servers/news_weather.py` — четвёртый дефолт реестра MCP
  (stdio JSON-RPC 2024-11-05, только stdlib, паттерн
  `task_manager.py`): 4 инструмента — `get_weather(city?)`,
  `get_news(source?)`, `make_digest(city?)` (сбор + запись JSON),
  `get_latest_digest()` (локальный файл → фолбэк GitHub API →
  `isError` «Дайджест недоступен»).
- `data/digests/` (корень репозитория, в git — «message bus»):
  `last-digest.json` + `history.json` (кап 96).
- `.github/workflows/digest.yml` — cron `0 */6 * * *` (UTC) +
  `workflow_dispatch`, ubuntu-latest, Python 3.12,
  `if: github.ref == 'refs/heads/master'`: `collector.py --out
  data/digests` → проверка схемы → `git add data/digests` → коммит
  `digest: <id>` → push с retry (3×). Без API-ключей (Open-Meteo и RSS
  открытые). **Cron живёт только в master — ветку нужно смержить.**
- `scripts/e2e_day18.py` — гибрид: Part A (офлайн, 6 assert, MUST
  PASS) — реальный subprocess `news_weather.py` через `MCPRegistry`
  (connect → 4 tools, `make_digest`/`get_latest_digest`
  (source=local, id совпадает), `collect_digest` (детерминированный
  id/generated_at), `save_digest` ×2 (история=2), CLI (exit 0); Part B
  (live, uvicorn :8102, реальный LLM), best-effort — диалог → decline
  профиля → «Покажи последнюю сводку (дайджест)» → модель сама
  вызывает `get_latest_digest` → `done.answer` содержит сводку.
  Exit 0 для PASS/SKIP, 1 для FAIL.
- `agent.py` — фикс cp1251: при импорте `sys.stdout`/`sys.stderr`
  реconfigure'ятся с `errors="replace"` — print лог-тегов
  (`[Final Response]` и др.) не рвёт SSE-стрим, если ответ содержит
  символы вне cp1251 (❌, эмодзи) (было: `UnicodeEncodeError` в
  SSE-генераторе → обрыв без `done`, клиент `IncompleteRead`).

## API

Новых REST-эндпоинтов нет. Реестр MCP дня 16 получает четвёртый
дефолт `news-weather` (`[sys.executable, <repo>/studio/mcp_servers/news_weather.py]`)
— подключение через существующие `/api/mcp/servers/*`. В чате —
обычный tool-loop дня 17 (модель вызывает `get_latest_digest`).

## Проверка задания

Бэкенд — 350 тестов PASS (офлайн: collect_digest/save_digest/RSS/дедуп/
CLI + `news_weather` на реальном subprocess + 4-й дефолт + регресс
дней 1–17). Фронтенд — без изменений (регресс `npm test`/`tsc -b`).
E2E `scripts/e2e_day18.py`: Part A 6/6 PASS; Part B — PASS (модель
вызвала `get_latest_digest`, ответ содержит сводку, `source: local`).

## Коммиты

- `d9e4eeb` — openspec change (proposal/design/spec/tasks)
- `a8a6526` — collector core: collect_digest + build_summary
- `56784ee` — collector: save_digest, атомарная запись, история кап 96
- `a2be5a8` — collector: RSS-фикстуры, дедуп, live-маркеры
- `c74ae2b` — collector CLI --out/--city
- `161eaa0` — news_weather stdio MCP server (4 tools)
- `f93b5fd` — News & Weather как четвёртый дефолт реестра
- `acfe45d` — GitHub Actions cron 6h (digest.yml)
- `1983d8e` — fix: cp1251-stdout не рвёт SSE-стрим
- `2e3ce57` — e2e_day18 + data/digests (первый дайджест)

---

# Release Notes — day17-mcp-tool-loop (день 17)

Ветка: [`day17-mcp-tool-loop`](https://github.com/imarkelov/ai_advent_challenge/tree/day17-mcp-tool-loop)
(от `day16-mcp-connect`).

## Что в релизе

**LLM-driven MCP tool-loop.** Инструменты подключённых MCP-серверов
уходят в тело LLM-запроса (`tools`, формат OpenAI), модель сама решает
вызвать инструмент (`tool_calls`), агент вызывает его на MCP-сервере,
результат возвращается модели сообщением `role: "tool"` в цикле до
финального ответа (кап 5 итераций). Пути дня 16 (вызов по команде
`/сервер тул`, system-маркер `mcp_tool`) не меняются — tool-loop
добавлен поверх.

- `studio/mcp_servers/task_manager.py` — новый stdio MCP-сервер
  (JSON-RPC 2024-11-05, только stdlib, без npx): in-memory задачи
  (TASK-42 `in_progress`/migor, TASK-7 `done`), инструменты
  `get_task_details` (required `task_id`), `create_task` (required
  `title`); «не найдено» → `{"error": "Задача не найдена: <id>"}` +
  `isError: true`; неизвестный метод → JSON-RPC -32601.
- `mcp.py` — Task Manager как третий дефолт реестра
  (`[sys.executable, <repo>/studio/mcp_servers/task_manager.py]`);
  `connect()` на успех → `[MCP Init] {name}: {n} инструментов:
  {список}` (stdout, flush).
- `agent.py` — tool-loop в `ask_stream` (кап 5 итераций): `tools` из
  подключённых серверов (коллизии имён — префикс `{server_id}__`),
  агрегация `delta.tool_calls` по `index`, assistant-сообщение с
  `tool_calls` + `role: "tool"`-сообщения (`tool_call_id`, `name`) в
  диалоге, цикл с повторными guards; превышение капа → SSE `error`
  «Tool-loop: превышен лимит итераций (5)»; ошибка инструмента → текст
  ошибки в tool-сообщении (цикл продолжается, без crash); без
  подключённых серверов `tools` в payload нет (регресс дня 16
  `test_chat_payload_has_no_mcp_tools`); журнал — запись на каждую
  итерацию, usage суммируется.
- Консоль-логи этапов: `[MCP Init]`, `[LLM Decision]`, `[MCP Response]`,
  `[Final Response]` (`print(..., flush=True)`).
- `state.tsx`/`ChatPanel.tsx` — служебные `role: "tool"`-сообщения
  хранятся в памяти диалога, но не рендерятся чат-пузырями.
- `scripts/e2e_day17.py` — гибрид: Part A — детерминированное ядро
  в-процессе (StudioAgent + `httpx.MockTransport` fake-LLM, эмитирующая
  `tool_calls` + реальный subprocess `task_manager.py` через
  `MCPRegistry`; 6 assert); Part B — live (uvicorn :8101, реальный LLM
  GPustack), best-effort SKIP; exit 0 для PASS/SKIP, 1 для FAIL.

## API

Новых REST-эндпоинтов нет; меняется только тело `POST /api/chat`:
`tools` в LLM-запросе при подключённых серверах, `tool_calls` /
`tool_call_id` проходят в историю `messages`. SSE-протокол не
расширяется (delta/done/error/invariant_violation — как есть).

## Проверка задания

Бэкенд — 330 тестов PASS (`pytest -q`, офлайн). Фронтенд — 212 тестов
PASS (Vitest) + `tsc -b` clean. E2E `scripts/e2e_day17.py` на этой
машине: Part A 6/6 PASS; Part B — SKIP (GPustack недоступен:
SSL CERTIFICATE_VERIFY_FAILED — окружение, не продукт).

## Коммиты

- `c7e341f` — mock task manager stdio MCP server
- `7f56b93` — task manager in registry defaults + `[MCP Init]` log
- `38df527` — LLM tool-calling loop in StudioAgent
- `cb69817` — UI: service tool messages not rendered
- `6cf4fa4` — e2e_day17 (deterministic core + live best-effort)

---

# Release Notes — day16-mcp-connect (день 16)

Ветка: [`day16-mcp-connect`](https://github.com/imarkelov/ai_advent_challenge/tree/day16-mcp-connect)
(от `day15-plan-review`).

## Что в релизе

**Подключение внешних MCP-серверов (Model Context Protocol) к Студии.**
Студия стартует с фиксированным реестром (Firecrawl — web-поиск, Git —
репозиторий; stdio-процессы `npx …`, поддерживается и streamable-http):
пользователь нажимает «Подключить» — и видит инструменты сервера в
отдельной панели «MCP» (своя кнопка «🧩» в шапке чата рядом с «⚙», панель
выезжает справа; в настройках «⚙» вкладки MCP больше нет).
День 16 — подключение, отключение, просмотр и вызов: инструмент подключённого
сервера вызывается из чата командой `/имя-сервера имя-тула` (автодополнение,
форма аргументов по `input_schema`); результат сохраняется в диалог и
виден LLM в следующих запросах (tool-loop).

- `mcp.py` — клиент MCP 2024-11-05 (JSON-RPC 2.0): `_StdioSession`
  (pipes, JSON по строкам, `{VAR}`-плейсхолдеры env расширяются из
  окружения на запуске), `_HttpSession` (streamable-http,
  `MCP-Protocol-Version`/`MCP-Session-Id`, SSE-кадры `data: {json}`),
  `MCPClient` (`connect` = `initialize` + `tools/list`, `call_tool` —
  tools/call), `MCPRegistry` (реестр + runtime-статусы
  `idle`/`connected`/`error` + `tools_count`).
- `memory.py` — CRUD реестра в `mcp_servers.json`; дефолты: Firecrawl, Git.
- `agent.py` — опциональный `mcp` (DI); `close_all()` — shutdown-хук.
- Сбой подключения — `status: error` + текст ошибки, агент не падает;
  повторный connect разрешён (self-heal).
- Определения инструментов (tool-definitions) в тело LLM-запроса НЕ
  инжектятся (регресс-тест); результат вызова сохраняется как
  system-сообщение с маркером `mcp_tool` и уходит в следующие запросы
  (tool-loop).

## API

| Метод | Путь | Назначение |
| --- | --- | --- |
| GET | `/api/mcp/servers` | Реестр MCP-серверов с runtime-статусом (idle/connected/error, tools_count) |
| POST | `/api/mcp/servers` | Добавить сервер `{name, type, command?, url?, env?, enabled?}` → 201 `{server}`; 400 — RU-detail |
| DELETE | `/api/mcp/servers/{id}` | Удалить сервер (404 — не найден) |
| POST | `/api/mcp/servers/{id}/connect` | Подключить (initialize + tools/list) → `{server}`; сбой = status error, 404 — не найден |
| POST | `/api/mcp/servers/{id}/disconnect` | Отключить (закрыть сессию, status → idle, сервер остаётся в реестре) → `{server}`; 404 — не найден |
| GET | `/api/mcp/tools` | Инструменты подключённых серверов `[{server, name, description, input_schema}]` |
| POST | `/api/mcp/servers/{id}/tools/{tool}` | Вызвать инструмент (tool-loop) `{dialogue_id, arguments}` → `{"ok": true}`; результат — system-сообщение с маркером `mcp_tool` в диалоге (видно LLM); 400 — сервер не подключён / arguments не объект, 404 — сервер или диалог не найден |

## Проверка задания

Бэкенд — 320 тестов PASS (stdio-транспорт на fake-процессе, http-транспорт
на `httpx.MockTransport` — JSON/SSE/session-id/ошибки, реестр, API-роуты,
вызов тула `call_tool` + роут `/tools/{tool}`, отключение (`disconnect`:
сессия → idle, сервер остаётся в реестре), регресс: tool-definitions MCP
вне LLM-payload, launcher: `npx.cmd` через `shutil.which`).
Фронтенд — 211 тестов PASS (включая отдельный overlay «MCP» по кнопке «🧩»
в шапке, автодополнение `/`, форму по `input_schema`, карточку результата и
тумблер «Подключить»/«Отключить»); tsc и `npm run build` — clean. E2E — 34 PASS,
4 SKIP, 0 FAIL: MCP-блок детерминированный (mock stdio-сервер `python -c`
без сети: POST 201 → connect → 2 tools → disconnect (idle) + reconnect → tool call 200 + `mcp_tool`-сообщение
в диалоге → 400/404 → connect-404 → DELETE 200/404); live Firecrawl —
best-effort SKIP (npx недоступен); чат/задачи — SKIP (GPustack недоступен
из-за SSL-сертификата Python).

## Безопасность

Секреты — только в `.env` (корень репозитория). В `mcp_servers.json`
для переменных окружения хранятся плейсхолдеры `{VAR}` — значение
подставляется из окружения в памяти при запуске процесса, в файл не
пишется. Таймаут подключения — `MCP_CONNECT_TIMEOUT` (дефолт 30 c).

---

# Release Notes — day13-task-state-machine (День 13b)

Ветка: [`day13-task-state-machine`](https://github.com/imarkelov/ai_advent_challenge/tree/day13-task-state-machine)
(от `day12-user-profile`).

## Что в релизе

**Задача = запрос пользователя в режиме «задача».** Тумблер чат/задача у поля
ввода (глобальный, состояние в `localStorage`) — в режиме «задача» отправленное
сообщение становится задачей, в режиме «чат» — обычным сообщением. Задача
пер-диалог.

### Unified FSM

Пайплайн `planning → execution (N work-шагов) → validation → done` и FSM из
6 значений: `planning | execution | validation | done | paused | failed`
(`paused`/`failed` — значения стадии, не флаги). Stage-агенты (Планировщик /
Исполнитель / Валидатор / Оркестратор) — один и тот же LLM из конфига с
разными system-промптами; пайплайн ведёт детерминированный код
(`agent.task_run`) — LLM не управляет FSM.

### Пошаговое исполнение

- Планировщик (1 LLM-вызов) — JSON-план из 1–5 work-шагов (best-effort
  парсинг; сбой/не-JSON — фолбэк в один шаг).
- Исполнитель — по LLM-вызову на work-шаг, стримингом (`step_updated` /
  `step_delta`).
- Валидатор — сверка с планом, вердикт `<verdict>pass|fail</verdict>`; fail →
  ретрай execution (максимум 1).
- Оркестратор — финальный синтез. Итого N+3 LLM-вызова (до 2N+3 с ретраем);
  stage/work-вызовы не пишутся в журнал `requests.json`.
- Пауза вступает на границе work-шага: текущий вызов доигрывается, следующий
  не начинается; фиксируется `context_snapshot`. Инструкция на паузе
  инжектится в промпт следующего шага/стадии и используется один раз.
  Продолжение — с сохранённого состояния (выполненные шаги/стадии не
  повторяются). Ошибка LLM — стадия `failed` + `task_failed`; повтор через
  «Продолжить»/новый run (auto-resume к первой невыполненной стадии).

### Карточка процесса в чате

Якорь карточки — user-сообщение с `task_id`. Живая карточка: спавн
stage-агентов (иконка, «спавн N с назад»), чек-лист work-шагов [✓]/[⏳]/[○],
живой бокс вывода текущего шага, сворачиваемые секции стадий (DONE — всё
свёрнуто, FAILED — секция ошибки развёрнута), кнопки Пауза/Продолжить/Повтор.
История после перезагрузки восстанавливается из маркеров сообщений
(`task_id`/`task_stage`/`task_step`).

### Контекст

Task-промпты собираются только из состояния задачи (`description`, `plan`,
`work_steps`, `instruction`); история сообщений чата в task-промпты не уходит.

## Исправления после основного релиза

- **Resume, пока стримится чужой run — не теряется.** Run-слот глобальный:
  если стримится run другого диалога (`taskRunning`), клик «Продолжить» ждёт
  освобождения слота (wait-loop, `sleep(400)`), затем `POST /api/task/resume` +
  `reloadTask` + `runTask`. Раньше resume отправлялся, пока слот занят, и
  состояние бэкенда рассинхронизировалось с фронтендом. `resumeInFlight`
  защищает от двойного клика (двойной «Продолжить»/«Повтор»). Ошибка (задача
  уже не resumable — 400-гард) → перечитывается авторитетная задача.
- **Карточка: кнопка «Продолжить» в зависшем состоянии.** Если задача в
  `execution`/`planning`, но run не идёт (`!running`) — показывается кнопка
  «Продолжить», которая запускает пайплайн через `POST /api/task/run`
  (восстановление из маркеров при перезагрузке).
- **Бренд сайдбара — «◆ День 13»** (было «День 11»), соответствует номеру
  текущего дня.

## API

| Метод | Путь | Назначение |
| --- | --- | --- |
| POST | `/api/task/start` | Создать задачу `{dialogue_id, description}` → 200 `task` + user-маркер; 400 — незавершённая активная задача / пустое описание; 404 — диалог |
| POST | `/api/task/run` | Запустить/продолжить пайплайн — SSE `agent_spawned` / `step_updated` / `step_delta` / `stage_done` (+`verdict`/+`plan`/+`retry`) / `task_paused` / `task_resumed` / `task_done` / `task_failed` / `error`; 400 — нет задачи / завершена |
| POST | `/api/task/pause` | Пауза на границе work-шага |
| POST | `/api/task/resume` | Снять паузу/ошибку |
| POST | `/api/task/instruction` | Инструкция на паузе `{text}` |
| POST | `/api/task/reset` | Сброс задачи |
| GET | `/api/task?dialogue_id=` | Текущее состояние задачи |

Ошибки — 400/404 с RU-detail; `task` — в выдаче `/api/dialogues` и
`/api/dialogues/{id}`. Гард чата: активная непаузанная незавершённая задача →
`POST /api/chat` → SSE `error` «Задача выполняется…».

## Проверка

- Бэкенд — **225 тестов PASS** (`python -m pytest -q`, офлайн, `httpx.MockTransport`).
- Фронтенд — **150 тестов PASS** (`npm test` / `vitest run`).
- Typecheck (tsc) и синтаксис e2e-скрипта — clean.
- E2E — 21/21 PASS (prod-сервер + реальный GPustack): чат, персонализация,
  пошаговый пайплайн задачи с паузой на границе шага.

## Безопасность

Секреты не попадают в git: в репозиторий не коммитятся `.env`, runtime-данные
(`studio/data/`, `*.json` истории/диалогов) и `*.log`. Ключи API берутся
только из `.env`, который в `.gitignore`. В треке и истории только имена
env-переменных (`GPUSTACK_API_KEY`, `GPUSTACK_KEY_DEEPSEEK`,
`GPUSTACK_KEY_GLM`) — без значений.
