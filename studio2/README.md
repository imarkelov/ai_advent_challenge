# studio2 — QA-Lead Orchestrator

Автономный QA-пайплайн: агент `qa-lead` изучает Android-проект через git и RAG,
пишет SDD на каждого субагента, готовит каркас теста на апрув человеку, генерирует
Kotlin-тест, запускает его на эмуляторе, дебажит падения, а отдельная LLM
(`reviewer`) проверяет загруженные и полученные из MCP тесты.

## Запуск

```bash
# бэкенд
cd studio2/backend
python -m uvicorn main:app --port 8100

# фронтенд
cd studio2/frontend
npm install
npm run dev     # http://localhost:5174
```

## Тесты

```bash
cd studio2/backend && python -m pytest -q
cd studio2/frontend && npm test
python studio2/scripts/e2e_day35.py
```

## Роли и декларации

Поведение агентов описано в `doc/agents/*.md` (front-matter + Markdown).
Правка промпта не требует изменения кода: перезапустите бэкенд и нажмите
«Переиндексировать» во вкладке RAG.

| Файл | Роль |
|---|---|
| `qa-lead.md` | оркестратор, SDD и стадии |
| `researcher.md` | изучение проекта через git и RAG |
| `testwriter.md` | каркас, затем Kotlin-тест |
| `debugger.md` | разбор падений |
| `reviewer.md` | отдельная LLM: зелёный/красный для загруженных тестов |
| `device-inspector.md` | снятие иерархии экрана через adb |

## Документы

- `doc/kb/` — база знаний (архитектура, паттерны, принципы, экраны).
- `doc/tasks/` — тест-кейсы.
- `doc/runs/<id>/` — артефакты запуска (SDD, каркас, тесты, отчёты), не в git.

## Гейты

1. `skeleton → write` — только после утверждения каркаса человеком.
2. `write → provision` — только после выбора способа исполнения
   (`as_loaded` или `generated`).

## Провайдеры моделей

`fake` (детерминированный, для e2e), `ollama` (`http://localhost:11435`),
`gpustack` (требует корпоративную сеть). Провайдер и модель задаются на каждой
роли во вкладке «Модели».

Модель `reviewer` обязана отличаться от `testwriter`.
