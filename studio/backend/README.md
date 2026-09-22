# Студия — бэкенд (день 11)

FastAPI-бэкенд обучающего приложения «Студия»: чат с LLM (GPustack,
OpenAI-совместимый API) со стримингом по SSE, три слоя памяти
(диалоги / рабочая / долговременная), конфиг, журнал запросов, токены.

## Структура

| Файл | Назначение |
|---|---|
| `main.py` | FastAPI-приложение и все роуты (валидация 400/404 с RU detail) |
| `agent.py` | `StudioAgent` — запрос к LLM, SSE-стрим, конфиг, журнал, сессионные токены |
| `memory.py` | `MemoryStore` — диалоги (ST), рабочая (WM) и долговременная (LT) память, реестр MCP-серверов (день 16) |
| `mcp.py` | MCP-клиент (день 16): stdio/http транспорты, `MCPClient`, `MCPRegistry` |
| `tests/` | Офлайн-тесты (tmp_path + httpx.MockTransport, без сети) |
| `requirements.txt` | fastapi, uvicorn, httpx, pytest, python-dotenv |

Данные — в `../data/` (создаётся при работе: `dialogues.json`, `working.json`,
`longterm.json`, `requests.json`, `config.json`). Секреты — из `.env` в корне
репозитория: `GPUSTACK_BASE_URL` (по умолчанию `https://gpustack.data.lmru.tech/v1`),
`GPUSTACK_API_KEY` (qwen), `GPUSTACK_KEY_DEEPSEEK`, `GPUSTACK_KEY_GLM`
(per-model ключи: маппинг `MODEL_KEY_ENV` в `agent.py`).

## Запуск (dev)

```bash
pip install -r requirements.txt
uvicorn main:app --port 8000
```

Тесты (офлайн):

```bash
python -m pytest -q
```

## API

| Метод | Путь | Назначение |
|---|---|---|
| POST | `/api/chat` | Чат: SSE-стрим `data: {delta\|done\|error}` |
| GET / POST | `/api/config` | Конфиг LLM (GET — текущий, POST — частичное обновление) |
| GET | `/api/models` | Доступные модели (зонд с per-model ключом, кэш 10 мин) с лимитами; self-heal модели конфига (502 при недоступности) |
| GET / POST | `/api/dialogues` | Список диалогов / создать (201, становится активным) |
| GET / DELETE | `/api/dialogues/{id}` | Диалог с сообщениями / удалить |
| POST | `/api/dialogues/{id}/rename` | Переименовать диалог (`{title}`; 400 пустой, 404 не найден) |
| POST | `/api/dialogues/{id}/activate` | Сделать диалог активным |
| POST | `/api/task/start` | Создать задачу `{dialogue_id, description}` → 200 `task` (новый `task_id`, `stage: planning`, `plan` из 4 записей) + user-сообщение-маркер; 400 — активная незавершённая задача / пустое описание; 404 — диалог; после done/failed — новая задача (новый `task_id`) |
| POST | `/api/task/run` | Запустить/продолжить пайплайн: SSE-стрим `data: {json}\n\n` (события `agent_spawned` / `step_updated` / `step_delta` / `stage_done` (+`verdict`/+`plan`/+`retry`) / `task_paused` / `task_resumed` / `task_done` / `task_failed` / `error`; 400 — нет задачи или задача завершена; failed → 200, auto-resume) |
| POST | `/api/task/pause` | Пауза на границе work-шага `{dialogue_id}` (400 — нет активной задачи / задача done или failed) |
| POST | `/api/task/resume` | Снять паузу/ошибку `{dialogue_id}` (400 — задача не в `paused`/`failed`) |
| POST | `/api/task/instruction` | Инструкция на паузе `{dialogue_id, text}` (400 — не на паузе / не-строка) |
| POST | `/api/task/reset` | Сброс задачи `{dialogue_id}` (active=false, поля чистые) |
| GET | `/api/task?dialogue_id=` | Текущее состояние задачи (без задачи — неактивная форма; 404 — диалог) |
| POST | `/api/memory/st/clear` | Очистить сообщения активного диалога |
| GET | `/api/memory` | Статистика слоёв памяти + `active_id` + `toggles` |
| GET / POST | `/api/memory/toggles` | Тумблеры слоёв `{st,wm,lt: bool}` / `{layer, enabled}` (400 — неизвестный слой или не-bool) |
| POST | `/api/memory/working` | Поставить заметку в WM активного диалога `{key,value}` |
| DELETE | `/api/memory/working/{key}` | Удалить заметку WM |
| POST | `/api/memory/working/clear` | Очистить WM активного диалога |
| POST | `/api/memory/longterm` | Поставить глобальную заметку `{key,value}` |
| DELETE | `/api/memory/longterm/{key}` | Удалить глобальную заметку |
| POST | `/api/memory/longterm/clear` | Очистить глобальные заметки |
| GET | `/api/tokens` | Последний usage, сессионные токены, лимит контекста модели |
| GET | `/api/requests` | Журнал LLM-запросов (без тел) |
| GET | `/api/requests/{id}` | Полная запись журнала (с телом запроса) |
| DELETE | `/api/requests` | Очистить журнал |
| GET | `/api/mcp/servers` | Реестр MCP-серверов с runtime-статусом (idle/connected/error, tools_count) |
| POST | `/api/mcp/servers` | Добавить сервер `{name, type, command?, url?, env?, enabled?}` → 201 `{server}`; 400 — RU-detail |
| DELETE | `/api/mcp/servers/{id}` | Удалить сервер (404 — не найден) |
| POST | `/api/mcp/servers/{id}/connect` | Подключить (initialize + tools/list) → `{server}`; сбой = status error, 404 — не найден |
| GET | `/api/mcp/tools` | Инструменты подключённых серверов `[{server, name, description, input_schema}]` |

## Задача (день 13b)

Задача = запрос пользователя в режиме «задача», per-диалог. Пайплайн:
`planning → execution (N work-шагов) → validation → done`. Цикл —
детерминированный код (`agent.task_run`, синхронный генератор): LLM не
управляет FSM, он выполняет очередную стадию/шаг по своему
system-промпту.

- **Unified FSM**: `planning | execution | validation | done | paused |
  failed` — одна стадия, 6 значений (`paused`/`failed` — значения
  стадии, не флаги). Единственный переход «назад» —
  `validation → execution` (ретрай, максимум 1; фидбэк исполнителю =
  замечания валидатора). Вердикт валидатора — метка
  `<verdict>pass|fail</verdict>`: best-effort парсинг, отсутствие
  метки = pass.
- **Пошаговое исполнение**: Планировщик — JSON-план из 1–5 work-шагов
  (best-effort парсинг, сбой — фолбэк в один шаг «Выполнить запрос»);
  Исполнитель — **по LLM-вызову на work-шаг** (стриминг, события
  `step_updated`/`step_delta`); Валидатор; Оркестратор — финальный
  синтез. Итого N+3 LLM-вызова (до 2N+3 с ретраем). Stage/work-вызовы
  не пишутся в `requests.json`. Task-промпты — только из состояния
  задачи (`description`/`plan`/`work_steps`/`instruction`); история
  чата в task-промпты не уходит.
- **Хранение**: поле `task` в записи диалога (`dialogues.json`):
  `task_id` (`"t_" + 12 hex`), unified `stage`, `current_step`,
  `total_steps`, `expected_action` (`agent_response`/`resume_wait`/
  `human_input`), `plan[]` (4 записи stage-агентов: `status`/`output`/
  `verdict`/`spawn_ts`/`ts`), `work_steps[]` (`name`/`status`/
  `output`/`ts`), `context_snapshot` (description + work_steps +
  instruction на паузе), `description`, `instruction`, `retries`,
  `error`, `updated`. Нет поля `task` или запись без `task_id`
  (старая схема дня 13) = задача неактивна (бэкворд-совместимость).
  Позиция при resume — производная: первая невыполненная запись
  `plan[]`.
- **Маркеры сообщений** (в `messages` диалога, в тело LLM не уходят):
  user-запрос — `{role, content, task_id}` (якорь карточки); выводы
  стадии/шага — assistant с `task_id`, `task_stage`
  (`planning`/`execution`/`validation`) и `task_step` (только
  work-шаг); финальный синтез — assistant с `task_id` **без**
  `task_stage` (обычный bubble). Сообщения с `task_stage` не
  рендерятся как bubble.
- **Семантика API**: `start` — новая задача (400 — активная
  незавершённая / пустое описание; после done/failed — новый
  `task_id`, старая остаётся в истории). `run` — SSE-пайплайн; 400 —
  нет задачи / done; failed → 200 (auto-resume, событие
  `task_resumed`). `pause` — на границе work-шага (текущий LLM-вызов
  доигрывается, следующий шаг не начинается; 400 — нет активной /
  done / failed); фиксируется `context_snapshot`. `resume` — из
  `paused`/`failed` к первой невыполненной стадии (выполненные
  шаги/стадии не повторяются; 400 — не в `paused`/`failed`).
  `instruction` — только на паузе, инжектится один раз. Ошибка
  LLM-вызова — `task_failed` + stage `failed`; повтор — resume/новый
  run.
- **Гард чата**: активная непаузанная незавершённая задача →
  `POST /api/chat` отвечает SSE `error` «Задача выполняется…»,
  сообщение пользователя не сохраняется; на паузе/done/failed чат
  работает.
- **SSE-протокол `/api/task/run`**: кадры `data: {json}\n\n`; события
  `agent_spawned` (спавн stage-агента, `spawn_ts` в `plan[]`),
  `step_updated` (статус work-шага: `in_progress`/`completed`+
  `output`), `step_delta` (живой вывод шага), `stage_done` (вывод
  стадии; +`verdict` для validation, +`plan` для planning, +`retry`
  при ретрае), `task_paused`, `task_resumed`, `task_done` (финальный
  синтез сохранён как assistant-сообщение), `task_failed` (ошибка
  LLM-вызова), `error` (нет задачи / задача завершена).

CORS открыт для `http://localhost:5173` и `http://127.0.0.1:5173`.
