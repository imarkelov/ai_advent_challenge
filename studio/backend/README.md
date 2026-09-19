# Студия — бэкенд (день 11)

FastAPI-бэкенд обучающего приложения «Студия»: чат с LLM (GPustack,
OpenAI-совместимый API) со стримингом по SSE, три слоя памяти
(диалоги / рабочая / долговременная), конфиг, журнал запросов, токены.

## Структура

| Файл | Назначение |
|---|---|
| `main.py` | FastAPI-приложение и все роуты (валидация 400/404 с RU detail) |
| `agent.py` | `StudioAgent` — запрос к LLM, SSE-стрим, конфиг, журнал, сессионные токены |
| `memory.py` | `MemoryStore` — диалоги (ST), рабочая (WM) и долговременная (LT) память |
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
| POST | `/api/task/start` | Создать задачу по описанию `{dialogue_id, description}` (200; 400 — уже есть активная задача, включая done, или пустое описание; 404 — диалог) |
| POST | `/api/task/run` | Запустить/продолжить пайплайн: SSE-стрим `data: {json}\n\n` (события `stage` / `stage_done` / `task_paused` / `task_done` / `error`; 400 — задача не активна) |
| POST | `/api/task/pause` | Пауза на границе стадии `{dialogue_id}` (400 — нет активной задачи) |
| POST | `/api/task/resume` | Снять паузу `{dialogue_id}` (400 — задача не на паузе) |
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

## Задача (день 13)

Задача per-диалог как конечный автомат: описание задачи прогоняется
через стадии-агентов. Цикл пайплайна — детерминированный код
(`agent.task_run`, синхронный генератор): LLM не управляет FSM, он
выполняет очередную стадию по своему system-промпту.

- **FSM**: `planning → execution → validation → done`. Единственный
  переход «назад» — `validation → execution` (ретрай, максимум 1;
  фидбэк исполнителю = его работа + замечания валидатора).
- **Stage-агенты**: один LLM из конфига, разные system-промпты
  (Планировщик / Исполнитель / Валидатор / Оркестратор); все
  вызовы стадий non-stream.
- **Вердикт**: `<verdict>pass|fail</verdict>` в ответе валидатора —
  best-effort парсинг, отсутствие метки = pass.
- **Хранение**: поле `task` в записи диалога (`dialogues.json`); нет
  поля = задача неактивна (бэкворд-совместимость). Выводы стадий —
  сообщения истории с меткой `task_stage`.
- **Пауза**: вступает на границе стадии (текущий LLM-вызов
  доигрывается). Инструкция на паузе инжектится в ближайшую стадию и
  используется один раз. Продолжение — с сохранённого состояния,
  прошедшие стадии не повторяются. Ошибка LLM-вызова — событие
  `error` + пауза; повтор — через resume + новый run.
- **Гард чата**: активная непаузанная незавершённая задача →
  `POST /api/chat` отвечает SSE `error` «Задача выполняется…»,
  сообщение пользователя не сохраняется; на паузе чат работает.
- **SSE-протокол `/api/task/run`**: кадры `data: {json}\n\n`, события
  `stage` (старт стадии), `stage_done` (вывод + verdict),
  `task_paused` (пауза на границе), `task_done` (задача завершена,
  финальный синтез сохранён как assistant-сообщение), `error`
  (ошибка LLM-вызова).

CORS открыт для `http://localhost:5173` и `http://127.0.0.1:5173`.
