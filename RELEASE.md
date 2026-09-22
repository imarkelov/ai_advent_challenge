# Release Notes — day16-mcp-connect (день 16)

Ветка: [`day16-mcp-connect`](https://github.com/imarkelov/ai_advent_challenge/tree/day16-mcp-connect)
(от `day15-plan-review`).

## Что в релизе

**Подключение внешних MCP-серверов (Model Context Protocol) к Студии.**
Студия стартует с фиксированным реестром (Firecrawl — web-поиск, Git —
репозиторий; stdio-процессы `npx …`, поддерживается и streamable-http):
пользователь нажимает «Подключить» — и видит инструменты сервера во
вкладке «MCP» настроек (кнопка «⚙» в шапке чата, панель выезжает справа).
Сценарий дня 16 — подключение и просмотр; вызов инструментов агентом
(tool-loop) — архитектурный задел, не реализуется.

- `mcp.py` — клиент MCP 2024-11-05 (JSON-RPC 2.0): `_StdioSession`
  (pipes, JSON по строкам, `{VAR}`-плейсхолдеры env расширяются из
  окружения на запуске), `_HttpSession` (streamable-http,
  `MCP-Protocol-Version`/`MCP-Session-Id`, SSE-кадры `data: {json}`),
  `MCPClient` (`connect` = `initialize` + `tools/list`), `MCPRegistry`
  (реестр + runtime-статусы `idle`/`connected`/`error` + `tools_count`).
- `memory.py` — CRUD реестра в `mcp_servers.json`; дефолты: Firecrawl, Git.
- `agent.py` — опциональный `mcp` (DI); `close_all()` — shutdown-хук.
- Сбой подключения — `status: error` + текст ошибки, агент не падает;
  повторный connect разрешён (self-heal).
- Тело LLM-запроса не изменилось: tools MCP НЕ инжектятся (регресс-тест).

## API

| Метод | Путь | Назначение |
| --- | --- | --- |
| GET | `/api/mcp/servers` | Реестр MCP-серверов с runtime-статусом (idle/connected/error, tools_count) |
| POST | `/api/mcp/servers` | Добавить сервер `{name, type, command?, url?, env?, enabled?}` → 201 `{server}`; 400 — RU-detail |
| DELETE | `/api/mcp/servers/{id}` | Удалить сервер (404 — не найден) |
| POST | `/api/mcp/servers/{id}/connect` | Подключить (initialize + tools/list) → `{server}`; сбой = status error, 404 — не найден |
| GET | `/api/mcp/tools` | Инструменты подключённых серверов `[{server, name, description, input_schema}]` |

## Проверка задания

Бэкенд — 305 тестов PASS (stdio-транспорт на fake-процессе, http-транспорт
на `httpx.MockTransport` — JSON/SSE/session-id/ошибки, реестр, API-роуты,
регресс: tools MCP вне LLM-payload, launcher: `npx.cmd` через `shutil.which`).
Фронтенд — 194 теста PASS (включая 5 на вкладку «MCP» и overlay настроек);
tsc и `npm run build` — clean. E2E — 29 PASS, 5 SKIP, 0 FAIL: MCP-блок
детерминированный (mock stdio-сервер `python -c` без сети: POST 201 →
connect → 2 tools → connect-404 → DELETE 200/404); live Firecrawl —
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
