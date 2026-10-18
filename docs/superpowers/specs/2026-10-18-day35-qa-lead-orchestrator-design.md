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
  7. review — Reviewer (**другая LLM**), вердикт green/red по загруженным/из MCP тестам
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

- Инвариант `critic.must_differ_from_author`: Reviewer != testwriter (модель).
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
2. Стейдж-переходы: `sdd → study → skeleton → write → provision → execute → debug → review → (study|done)` (гейты — см. 4.10).
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
роль, модель моноширинно, прогресс) → **Стейдж** (`sdd→study→skeleton→write→
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

## 4. Роли, промпты и протокол SDD

### 4.1 Состав `doc/agents/`

| Файл | Роль | Делает | Не делает |
|---|---|---|---|
| `qa-lead.md` | Оркестратор | читает ТК, генерит SDD, гоняет цикл, собирает отчёт | не пишет код теста |
| `researcher.md` | Исследователь | git (branch/log/diff/blame) + RAG → `kb/*.md` | не пишет тесты |
| `testwriter.md` | Тестировщик | каркас → Kotlin-тест по SDD + patterns | не правит kb |
| `debugger.md` | Дебаггер | разбирает fail: adb-дамп, правки теста | не меняет требования |
| `reviewer.md` | Ревьюер (**другая LLM**) | ревью **загруженных/из MCP** тестов → зелёный/красный | не пишет тест |
| `device-inspector.md` | Разведчик экрана | adb dump → `kb/screens/<screen>.md` | не пишет тесты |
| `orchestrator-protocol.md` | Протокол | общие правила: роли, handoff, лимиты, формат обмена | — |

### 4.2 Шаблон SDD (`doc/templates/sdd.md`)

```markdown
# SDD — <role>

## Контекст
- Задача: <TC-4217 — название>
- Репозиторий / ветка: <path> @ <branch>
- Изменения в ветке (git): <log/diff summary>

## Цель
<что должен получить этот субагент>

## Вход
- RAG: kb/architecture.md, kb/patterns.md, kb/screens/...
- Артефакты предыдущих ролей: <пути>

## Инструкции
1. <шаг>

## Ограничения
- allowed_tools, max_rounds: 2, write_scope

## Формат ответа (JSON)
{"status":"done|need_question|fail","artifacts":[...],"summary":"...","question":{...}}

## Критерии готовности
- [ ] ...
```

### 4.3 Правила `qa-lead`

1. Читает ТК из `doc/tasks/<id>.md` + MCP-Allure (если подключён).
2. Определяет затронутые экраны (ТК + git diff ветки).
3. Генерит SDD: `agent_researcher.md`, `agent_testwriter.md`, `agent_debugger.md`,
   `agent_reviewer.md`, `agent_qa-lead.md`.
4. Порядок: `researcher → testwriter(skeleton) → [GATE апрув] → testwriter(write)
   → [GATE выбор исполнения] → provision → execute → debug → review`.
5. `need_question` → пробрасывает `question` в SSE, ждёт ответ, перезапускает
   того же субагента с добавленным ответом.
6. `reviewer.verdict = red` (для загруженных тестов) → round 2: researcher
   дописывает факты → повторный прогон.
7. 2 круга исчерпаны → `need_question` или `fail`.
8. Пишет `test_report.md`.

### 4.4 Правила `researcher` (двухкруговое изучение)

```
Круг 1: RAG(экран, фича, тег) → git(log/diff/blame) → чтение кода
        → пишет kb/architecture.md, kb/patterns.md, kb/principles.md
Круг 2: новый запрос / другой источник (device-inspector) → дописывает kb
Круги исчерпаны → status=need_question по templates/question.md
```

Ведёт: `kb/architecture.md` (модули, экраны, навигация, ключевые классы),
`kb/patterns.md` (Kaspresso/Kakao/Compose, `withTestTag`, `tagged`, Screen-объекты,
фикстуры `assets/responses`), `kb/principles.md` (что проверяем),
`kb/screens/<screen>.md` (иерархия + test-теги).

### 4.5 Правила `testwriter`

Два прохода:
1. **skeleton** — каркас без кода (см. 4.9), ждёт апрув/правки человека.
2. **write** — превращает утверждённый каркас в Kotlin-тест в
   `doc/runs/<id>/test_generated/<ClassName>.kt`.

Стиль копирует `kb/patterns.md`, использует **реальные** теги из `kb/screens/`,
исходники приложения не трогает.

### 4.6 Правила `debugger`

1. Читает логи gradle + скриншот + adb dump на момент падения.
2. Классифицирует: компиляция / тег не найден / таймаут / реальный баг продукта.
3. Компиляция/тег/таймаут → правит тест (не более N итераций).
4. Реальный баг продукта → `fail` с описанием (тест не «подкручивать»).
5. Пишет примечания в `sdd/agent_debugger.md`.

### 4.7 Правила `reviewer` — только результаты загруженных/из MCP тестов

| # | Проверка | Результат |
|---|---|---|
| 1 | Тест проходит на устройстве (gradlew exit=0) | `green` / `red` |
| 2 | Нет flaky-признаков (повторный прогон) | `stable` / `flaky` |
| 3 | Тест соответствует ТК из Allure (шаги покрыты) | `matches` / `mismatch` |
| 4 | Ссылка на ТК в MCP существует и актуальна | `valid` / `stale` |

Вердикт `green` — все тесты прогона прошли и стабильны; иначе `red`.
Пишет `runs/<id>/review_report.md`. Для **сгенерированных** в этом run тестов
ревью не блокирует пайплайн.

### 4.8 Гейт выбора исполнения (`execution_choice`)

Перед `provision` пайплайн спрашивает человека:

```
[question] testwriter: тест готов.
1. Использовать загруженный из Allure код как есть
2. Реализовать сгенерированный тест
3. Посмотреть каркас (см. 4.9)
```

Ответ фиксируется в `run.json` (`execution_choice`); без ответа переход
`write → provision` запрещён.

### 4.9 Гейт каркаса (`skeleton`)

Каркас — тест без кода:

```markdown
# Каркас — AddNewAddressTest (TC-4217)

## Шаги (структура)
1. Открыть экран «Добавить адрес»
2. Ввести улицу и дом
3. Проверить активность кнопки «Принять»
4. Нажать «Принять»
5. Проверить возврат на главный экран

## Используемые теги
- AddressAddUIConstants.ToolbarTitleText
- AddressSearchUIConstants.TextField
- AddressAddUIConstants.AcceptButton

## Входные данные
- строка адреса

## Границы (не проверяем)
- валидация сервера
- сохранение адреса в профиль

## Ожидаемые правки человека
- ...
```

- Сохраняется в `runs/<id>/skeleton.md`, правится человеком в UI.
- Только после `skeleton_approved` testwriter генерирует Kotlin.

### 4.10 Стейдж-флоу

```
sdd → study → skeleton → [GATE: апрув каркаса] → write
   → [GATE: выбор исполнения] → provision → execute → debug
   → review (загруженные/из MCP) → (round 2 | done)
```

Новые SSE-события: `skeleton_ready`, `skeleton_updated`, `skeleton_approved`,
`execution_choice`, `execution_chosen`.

Новые эндпоинты: `GET/PUT /api/runs/{id}/skeleton`,
`POST /api/runs/{id}/approve-skeleton`,
`POST /api/runs/{id}/execution-choice`.

### 4.11 Инварианты ролей

1. Каждая роль получает только свой SDD + разрешённые артефакты.
2. `allowed_tools` — жёсткий фильтр: неразрешённый вызов → ошибка.
3. Одна роль = одна модель из конфига ролей.
4. Ревьюер не может быть той же моделью, что тестировщик.
5. Переход `skeleton → write` — только после `skeleton_approved`.
6. Переход `write → provision` — только после `execution_chosen`.
7. Reviewer валидирует только загруженные/из MCP тесты.

## 5. Исполнение Android

### 5.1 Инструменты на машине

| Компонент | Значение |
|---|---|
| SDK | `C:\Users\migor\AppData\Local\Android\Sdk` |
| adb | `%SDK%\platform-tools\adb.exe` (не в PATH — абсолютный путь) |
| emulator | `%SDK%\emulator\emulator.exe` |
| AVD | `Pixel_8_Pro_API_33` (default), `Pixel_9_Pro` |
| platforms | `android-35` |
| build-tools | 34.0.0, 35.0.0, 35.0.1, 36.0.0 |
| JDK | Corretto 18 (`JAVA_HOME` задан) |
| Проект | `~/StudioProjects/lmru--mobile-customer--app` (ветка `develop`) |
| Gradle | wrapper `gradlew.bat` в корне проекта |

### 5.2 `android.py`

```python
class AndroidDevice:
    def list_devices() -> list[Device]      # adb devices -l
    def boot(avd: str) -> None              # emulator -avd X -no-snapshot
    def wait_ready(timeout=180) -> None     # sys.boot_completed=1
    def install(apk: Path) -> None          # adb install -r -t
    def launch(package: str) -> None        # am start
    def dump_ui() -> UiNode                 # uiautomator dump → XML → дерево
    def screenshot() -> Path                # screencap → runs/<id>/logs/
    def tap(node_or_xy) -> None
    def type_text(s) -> None
    def back() -> None
    def current_activity() -> str           # dumpsys
```

`dump_ui()` парсит XML в `{class, text, resource-id, content-desc, bounds, children}`,
сохраняет `runs/<id>/logs/ui_<n>.xml`.

### 5.3 `gradle.py`

```python
class GradleRunner:
    def assemble_debug() -> Path       # ./gradlew :android:app:assembleDebug
    def connected_test(filter: str)    # ./gradlew :android:app:connectedDebugAndroidTest
                                       #  -Pandroid.testInstrumentationRunnerArguments.class=...
    def stream_logs() -> Iterator[str] # построчно → SSE gradle_log
```

Рабочая директория — корень проекта. Таймауты: сборка 25 мин, прогон 15 мин.
Логи `runs/<id>/logs/gradle_<stage>.log`; парсим `BUILD SUCCESSFUL/FAILED`,
падения (class, test, stacktrace). Нет `gradlew` → RU-ошибка.

### 5.4 Разведка экрана (`device-inspector`)

```
1. install APK (или уже установленный)
2. launch(package)
3. dump_ui() → ui.xml
4. извлечь resource-id, text, content-desc, class, clickable
5. kb/screens/<screen>.md: экран, интерактивные узлы с тегами, дерево
6. SSE adb_dump {screen, nodes, path}
```

Закрывает требование «собирать информацию по экрану и тегам → в файл».

### 5.5 Цикл исполнения

```
provision: assemble_debug → boot(avd) → wait_ready → install → launch → dump_ui
execute:   connected_test(filter=ClassName) → {passed, failed[], duration}
debug:     screenshot + dump_ui → классификация (4.6) → правка → повтор (≤2)
           реальный баг продукта → fail с описанием
```

### 5.6 Деградация

| Режим | Поведение |
|---|---|
| `live` | AVD + gradlew, полный прогон |
| `adb-only` | разведка экрана работает, `connected_test` → `SKIP` |
| `fake` | детерминированные артефакты для e2e |

Автовыбор: `adb devices` пуст → `adb-only`; провайдер `fake` → `fake`.

### 5.7 Стабильность

- Каждый adb-вызов: таймаут + 1 ретрай.
- `adb kill-server` перед стартом.
- `adb install -r -t`.
- Эмулятор по умолчанию `Pixel_8_Pro_API_33` (меняется в `config.json`).
- Логи `runs/<id>/logs/cmd.log` (без секретов).

### 5.8 Безопасность

- Без явного флага не выполняются `adb shell rm` / `pm clear`.
- `repo_path` — git-репозиторий, `branch` существует.
- Gradle — только внутри `repo_path`.
- `doc/runs/` пишется только текущим run.

## 6. Тестирование, демо, ключи, релиз

### 6.1 Пирамида тестов `studio2`

| Уровень | Что | Инструмент |
|---|---|---|
| Unit backend | `roles.py` (парсинг MD), `rag.py`, `git_ops.py`, `store.py`, `llm.py` (fake) | pytest |
| Контракт API | все эндпоинты через `TestClient` | pytest + httpx |
| Оркестрация | `pipeline/lead.py` на fake-LLM: стейджи, гейты, вопросы | pytest |
| Frontend | компоненты + reducer | Vitest + Testing Library |
| E2E | полный run на fake-LLM + проверка артефактов | `scripts/e2e_day35.py` |

Инвариантные тесты:
1. `reviewer.model != testwriter.model`.
2. Без `skeleton_approved` переход в `write` → 400.
3. Без `execution_chosen` переход в `provision` → 400.
4. Инструмент вне `allowed_tools` → ошибка.
5. 3-й круг изучения невозможен.
6. `doc/kb` пишет только researcher.
7. Секреты не попадают в `run.json` и логи.

### 6.2 Демо-сценарий

1. Models: 3 модели Ollama, статус `ok`, Reviewer — `gemma4:12b`.
2. MCP: подключить Allure-сервер.
3. RAG: дерево `doc/kb`, «Переиндексировать».
4. Tasks: выбрать `TC-4217`.
5. Pipeline → «Запустить», live-трейс.
6. `skeleton`: показать каркас, правка, «Утвердить».
7. `execution_choice`: выбрать вариант.
8. `review_report.md` — зелёный.
9. `test_report.md` + сгенерированный `.kt`.
10. `doc/kb/patterns.md` — «выведет паттерны написания».

Запись: Playwright → `webm → mp4` (skill `studio-demo-video`), 2–4 мин.

### 6.3 Ключи

| Ключ | Назначение | Статус |
|---|---|---|
| `GPUSTACK_BASE_URL` | эндпоинт | задан |
| `GPUSTACK_API_KEY` / `_DEEPSEEK` / `_GLM` / `_EMBED` / `_RERANK` | доступ | заданы, сеть недоступна |
| `OLLAMA_BASE_URL` | `http://localhost:11435` | работает |
| Модели Ollama | `qwen3-coder:30b`, `gemma4:12b`, `qwen3.5:9b` | доступны |

`studio2/data/` в `.gitignore`. В release notes: GPUSTACK требует VPN, демо — Ollama.

### 6.4 Артефакты демо

`docs/day35_demo.png`, `docs/day35_pipeline_trace.txt`, `docs/day35_report.md`;
видео — в `LINKS.md`.

### 6.5 README / RELEASE / push

- `studio2/README.md` — запуск, роли, правка `doc/agents/*.md`.
- `RELEASE.md` — «Release Notes — day35-qa-lead-orchestrator».
- Корневой `README.md` — добавить день 35.
- `LINKS.md` — видео + ветка.
- Ветка `day35-qa-lead-orchestrator` → push.

### 6.6 Критерии готовности

- [ ] `pytest` зелёный (включая инварианты).
- [ ] `npm test` + `tsc -b` зелёные.
- [ ] `scripts/e2e_day35.py` — PASS на fake.
- [ ] Демо записано, скриншоты в `docs/`.
- [ ] `doc/agents/*.md` правятся без кода.
- [ ] Reviewer гарантированно на другой модели.
- [ ] Ключи проверены, отчёт в release notes.
- [ ] Push в `origin/day35-qa-lead-orchestrator`.
