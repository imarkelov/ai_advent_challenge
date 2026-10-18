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

## 2. Открытые секции

- SEC 2 — API-контракты, LLM-маршрутизация (fake/ollama/gpustack), SSE-протокол.
- SEC 3 — визуальный стиль («QA-стиль», лаконичный).
- SEC 4 — промпты ролей и протокол SDD.
- SEC 5 — исполнение Android (adb-разведка, Gradle, дебаг-цикл).
- SEC 6 — тестирование и демо, ключи, release notes, push.
