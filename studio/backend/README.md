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
`GPUSTACK_API_KEY`.

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
| GET | `/api/models` | Модели API с контекстными лимитами (502 при недоступности) |
| GET / POST | `/api/dialogues` | Список диалогов / создать (201, становится активным) |
| GET / DELETE | `/api/dialogues/{id}` | Диалог с сообщениями / удалить |
| POST | `/api/dialogues/{id}/activate` | Сделать диалог активным |
| POST | `/api/memory/st/clear` | Очистить сообщения активного диалога |
| GET | `/api/memory` | Статистика слоёв памяти + `active_id` |
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

CORS открыт для `http://localhost:5173` и `http://127.0.0.1:5173`.
