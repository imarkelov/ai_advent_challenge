# Day 35 — QA-Lead Orchestrator (studio2)

Статус: черновик (секции 1–1.2 согласованы; секции 2+ в работе).
Дата: 2026-10-18.
Проект: **новый, `studio2/`**, `studio/` не трогаем.

## 1. Общая архитектура

### 1.1 Раскладка

```
studio2/
├── doc/                    # документы: агенты, шаблоны, база знаний, ТК, артефакты
├── backend/                # Python + FastAPI
│   ├── main.py             # эндпоинты, SSE
│   ├── config.py           # .env + studio2/data/config.json
│   ├── llm.py              # провайдеры: fake | ollama | gpustack
│   ├── store.py            # JSON-хранилище рантайма
│   ├── rag.py              # индекс по doc/kb, doc/tasks, doc/agents
│   ├── git_ops.py          # ветки, log, diff, blame
│   ├── android.py          # adb: dump, screencap, tap, wait
│   ├── gradle.py           # ./gradlew connectedAndroidTest
│   ├── roles.py            # загружает и парсит doc/agents/*.md (front-matter + тело)
│   ├── pipeline/
│   │   ├── lead.py         # qa-lead: SDD-генерация, оркестрация, round-лимиты
│   │   ├── researcher.py
│   │   ├── testwriter.py
│   │   ├── debugger.py
│   │   └── reviewer.py
│   └── tests/
├── frontend/               # React 19 + Vite + TS, чистый старт
├── data/                   # рантайм: config.json, mcp_servers.json (gitignore)
└── mcp_servers/            # локальные MCP: allure-клиент, android-хелперы
```

### 1.2 Поток запуска

```
UI: задача (ТК + репозиторий + ветка)
  → POST /api/runs → run_id
  → SSE /api/runs/{run_id}/stream

qa-lead:
  1. SDD  — генерит doc/runs/<id>/sdd/agent_*.md по doc/templates/sdd.md
  2. study round 1 — researcher: git (branch/log/diff/blame) + RAG
       не хватает → question_card (SSE) → ждёт человека
       пишет doc/kb/{architecture,patterns,principles}.md, doc/kb/screens/*.md
  3. write — testwriter: Kotlin/Kaspresso/Kakao/Compose по SDD + patterns
  4. provision — сборка APK (assemble) + запуск AVD
  5. execute — gradlew connectedAndroidTest
  6. debug — при fail: adb dump → правка теста → повтор (лимит)
  7. review — reviewer (**другая LLM**), вердикт pass/fail
  8. fail → round 2 study (новые факты в RAG) → назад к write
  9. done — test_report.md + review_report.md
```

Оркестрация: **Supervisor + специализированные субагенты** с SDD на каждого
(без внешних фреймворков). Разделение ролей изолировано, ревью отдельной моделью,
цикл «2 круга → вопрос человеку» встроен.

### 1.3 Папка `doc/` (документы и база знаний)

```
doc/
├── agents/                    MD-декларации агентов (source of truth)
│   ├── qa-lead.md
│   ├── researcher.md
│   ├── testwriter.md
│   ├── debugger.md
│   ├── reviewer.md
│   ├── device-inspector.md
│   └── orchestrator-protocol.md
├── templates/
│   ├── sdd.md
│   ├── run-summary.md
│   ├── question.md
│   └── review-report.md
├── kb/                        RAG-корпус, пишет агент + человек
│   ├── architecture.md
│   ├── patterns.md
│   ├── principles.md
│   ├── test-design.md
│   ├── screens/<screen>.md
│   └── questions.md
├── tasks/                     тест-кейсы (ТК)
│   └── TC-XXXX.md
└── runs/<run_id>/             артефакты запусков (gitignore)
    ├── sdd/agent_*.md
    ├── test_generated/*.kt
    ├── logs/
    ├── review_report.md
    └── test_report.md
```

| Что | Где | Пишет | В git |
|-----|-----|-------|-------|
| Декларации агентов | `doc/agents/` | человек | да |
| Шаблоны | `doc/templates/` | человек | да |
| База знаний | `doc/kb/` | researcher + человек | да |
| Тест-кейсы | `doc/tasks/` | человек / импорт Allure-MCP | да |
| Артефакты запусков | `doc/runs/` | агент | нет |
| Рантайм-конфиг, ключи, MCP | `studio2/data/` | UI | нет |

RAG индексирует `doc/kb/`, `doc/tasks/`, `doc/agents/`.

### 1.4 Формат MD-агента

Front-matter + тело:

```markdown
---
role: researcher
display_name: Исследователь
model: qa-lead-default
temperature: 0.2
stage: study
allowed_tools: [git_ops.read_branch, git_ops.log_diff, rag.search, rag.write, android.dump]
inputs: [sdd/agent_researcher.md, kb/architecture.md]
outputs: [kb/architecture.md, kb/patterns.md, kb/principles.md]
max_rounds: 2
on_no_info: ask_human
---

## Роль / Что делаешь / Когда не хватает информации / Формат ответа
```

`roles.py` парсит front-matter → `AgentSpec(role, model, tools, stage, limits)`;
тело MD → system-prompt; `allowed_tools` фильтрует tool-реестр.
Правка MD не требует изменения кода.

### 1.5 UI

Три колонки: `sidebar | main | inspector`. Вкладки main:
Models (провайдер+ключ+модель на роль, проверка связи), RAG (дерево `doc/`,
загрузка файлов, просмотр/правка MD, «Переиндексировать», бейдж устаревания),
MCP (подключение серверов: command/url/env), Tasks (список ТК),
Pipeline (запуск, live SSE, карточки субагентов, просмотр SDD, карточка вопроса).

## 2. API-контракты и LLM-маршрутизация

### 2.1 Модели по ролям (`data/config.json`)

```json
{
  "providers": {
    "ollama":   { "base_url": "http://localhost:11435", "api_key": null },
    "gpustack": { "base_url": "https://gpustack.data.lmru.tech/v1", "api_key": "..." },
    "fake":     { "base_url": null, "api_key": null }
  },
  "roles": {
    "qa-lead":    { "provider": "ollama", "model": "qwen3-coder:30b", "temperature": 0.2 },
    "researcher": { "provider": "ollama", "model": "qwen3.5:9b",      "temperature": 0.2 },
    "testwriter": { "provider": "ollama", "model": "qwen3-coder:30b", "temperature": 0.4 },
    "debugger":   { "provider": "ollama", "model": "qwen3-coder:30b", "temperature": 0.3 },
    "reviewer":   { "provider": "ollama", "model": "gemma4:12b",      "temperature": 0.1 }
  },
  "critic": { "must_differ_from_author": true }
}
```

- Инвариант `critic.must_differ_from_author`: reviewer != testwriter (модель).
- Fallback: провайдер недоступен → баннер + переезд на `fake` или `ollama`.
- `data/` в `.gitignore`, ключи маскируются в UI.

### 2.2 REST-контракты

Ошибки — HTTP + RU-detail.

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/api/health` | статус + версия |
| GET/POST | `/api/config` | провайдеры и роли |
| GET | `/api/models` | модели провайдера (dropdown) |
| POST | `/api/models/test` | проверка провайдер+ключ+модель |
| GET | `/api/doc/tree` | дерево `doc/` |
| GET | `/api/doc/file?path=` | содержимое MD |
| POST | `/api/doc/file` | сохранить MD |
| POST | `/api/doc/upload` | загрузка файла в `doc/kb` или `doc/tasks` |
| DELETE | `/api/doc/file?path=` | удалить (только `doc/kb`, `doc/tasks`) |
| POST | `/api/rag/reindex` | переиндексация |
| GET | `/api/rag/search?q=&limit=` | поиск по RAG |
| GET/POST/DELETE | `/api/mcp/servers[...]` | реестр MCP |
| POST | `/api/mcp/servers/{id}/connect` \| `/disconnect` | подключение |
| GET | `/api/mcp/tools` | инструменты |
| GET | `/api/tasks` | список ТК из `doc/tasks/` |
| POST | `/api/runs` | `{task_id, repo_path, branch}` → `{run_id}` |
| GET | `/api/runs` \| `/api/runs/{id}` | история / состояние |
| GET | `/api/runs/{id}/stream` | SSE |
| POST | `/api/runs/{id}/answer` | `{question_id, text}` |
| POST | `/api/runs/{id}/cancel` | остановить |

### 2.3 SSE-протокол

Формат `data: {json}\n\n`.

| `type` | Поля |
|---|---|
| `run_started` | run_id, task |
| `stage_changed` | stage, round |
| `agent_started` | role, model |
| `agent_delta` | role, text |
| `agent_done` | role, status, summary |
| `tool_call` | role, tool, args |
| `tool_result` | role, tool, ok, preview (≤2КБ) |
| `sdd_written` | role, path, title |
| `rag_written` | path, bytes |
| `test_written` | path, loc |
| `gradle_log` | line |
| `adb_dump` | screen, nodes, path |
| `review_verdict` | verdict, path |
| `question` | question_id, role, text, options |
| `question_answered` | question_id |
| `run_done` | status, report_path |
| `error` | message (RU) |

`question` блокирует пайплайн до `POST /api/runs/{id}/answer`
(`asyncio.Event` на run_id + таймаут).

### 2.4 LLM-слой `llm.py`

```python
class LlmClient:
    def chat(self, spec: AgentSpec, messages: list, tools=None) -> str: ...
    def stream(self, spec, messages, tools=None) -> Iterator[str]: ...
```

- `fake` — детерминированные ответы (e2e без сети).
- `ollama` — OpenAI-совместимый `/v1/chat/completions` (`:11435`).
- `gpustack` — `POST {base}/chat/completions`, `Authorization: Bearer`.
- Ретраи ×2, логи в `runs/<id>/logs/llm_<role>_<n>.json`, `usage` в `run.json`.

### 2.5 Live vs fake

UI-переключатель `Live | Fake (демо)` в Models. e2e по умолчанию `fake`.
Демо — `live` на Ollama.

### 2.6 Инварианты

1. `reviewer.model != testwriter.model` при `must_differ_from_author`.
2. Стейдж-переходы: `sdd → study → write → provision → execute → debug → review → (study|done)`.
3. `max_rounds=2`; после 2 кругов — только `question` или `fail`.
4. Субагент не пишет вне своего `doc/runs/<run_id>/`.
5. `doc/kb` пишет только researcher; testwriter — только `test_generated/`.

## 3. Визуальный стиль

### 3.1 Принципы

- Дашборд тест-менеджера, не чат-бот: главное — статус.
- Лаконично: 1 акцент, монохром, воздух, таблицы вместо карточек.
- Строка = один факт (роль, стейдж, вердикт).
- Тёмная тема по умолчанию, светлая — переключатель.

### 3.2 Токены

```css
--bg:#0f1115; --panel:#151922; --line:#232935;
--text:#d8dee9; --muted:#7b8794;
--accent:#4ea1ff; --ok:#3fb950; --warn:#d29922; --err:#f85149;
--radius:6px;
--font:"JetBrains Mono", ui-monospace;
--font-ui:"Inter", system-ui, sans-serif;
```

Без градиентов, теней и скруглений > 8px.

### 3.3 Каркас

```
┌ HEADER: run · задача · [Live/Fake] · [▶ Запустить] ┐
├ Sidebar ──┬─────── MAIN ────────┬── Inspector ────┤
│ Models    │                      │ SDD             │
│ RAG       │                      │ Логи            │
│ MCP       │                      │ RAG hits        │
│ Tasks     │                      │ Отчёт           │
│ Pipeline  │                      │                 │
│ Runs      │                      │                 │
└───────────┴──────────────────────┴─────────────────┘
```

Sidebar: текст + счётчик, активный — полоса `--accent`.

### 3.4 Экран Pipeline

Блоки: **SDD** (список файлов) → **Протокол** (роли: статус-точка,
роль, модель моноширинно, прогресс) → **Стейдж** (`sdd→study→write→
provision→execute→debug→review→done`) → **Журнал** (`ts [роль] действие`,
append-only, автоскролл).

Статус-точки: `●` выполняется, `○` ожидает, `✓` готово, `✕` ошибка.

### 3.5 Остальные экраны

- **Models** — таблица `Роль | Провайдер | Модель | Статус`, инлайн-правка,
  «Проверить»; блок провайдеров (base_url, маскированный ключ).
- **RAG** — дерево `doc/{kb,tasks,agents}` + MD-редактор с preview,
  «Переиндексировать», «Загрузить», бейдж `ok`/`устарел`.
- **MCP** — таблица `Имя | Тип | Статус | Инструменты | [Подключить][Удалить]`,
  инлайн-форма добавления.
- **Tasks** — таблица ТК `ID | Название | Экран | Приоритет | Статус`.
- **Runs** — история `Run | ТК | Ветка | Вердикт | Длительность | Статус`.
- **Вопрос** — модалка: текст, 2–4 варианта + свободный ввод, блокирует пайплайн.

### 3.6 Состояния

loading — скелет-строки; empty — одна подсказка; error — красная полоса
сверху (RU); «агент работает» — пульсирующая точка на `--accent`.

## 4. Открытые секции

- SEC 4 — промпты ролей и протокол SDD.
- SEC 5 — исполнение Android (adb-разведка, Gradle, дебаг-цикл).
- SEC 6 — тестирование и демо, ключи, release notes, push.
