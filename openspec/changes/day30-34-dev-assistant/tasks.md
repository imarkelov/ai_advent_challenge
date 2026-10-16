# Tasks: day30-34-dev-assistant

Задачи дней 30–34: единый AI-ассистент разработчика и поддержки —
RAG по документации и коду проекта, ревью изменений, поддержка с
CRM-данными, автономные файловые сценарии, вкладка «Ассистент».
Финальные цифры — после задачи 14.

## Данные и корпус
- [x] Task 1: `scripts/make_crm_data.py` → `data/crm/users.json`,
  `data/crm/tickets.json` (пользователи с планом/ролью/историей,
  тикеты по авторизации/оплате/конфигурации), детерминированно
- [x] Task 2: `ProjectCorpus` — сбор документов проекта по явным
  правилам (README, docs/, openspec/, backend-код, frontend, scripts)
  с исключениями (`.venv`, `node_modules`, `.git`, `data/`) и лимитом
  размера; тесты СНАЧАЛА в `test_assistant.py`

## Ядро (TDD)
- [x] Task 3: индекс проекта — сборка в `data/kb/project.db`
  (отдельно от `data/kb/index.db`), идемпотентность, статистика,
  поиск через `kb.py`; тесты (включая «не трогает индекс дня 21»)
- [x] Task 4: `TicketStore` — чтение CRM, поиск тикета по id,
  связь с пользователем, список с фильтрами, устойчивость к
  отсутствующим файлам; тесты
- [x] Task 5: разбор unified diff — файлы, ханки, +/- строки, номера
  строк в новой версии; устойчивость к битому diff; тесты
- [x] Task 6: детерминированные проверки ревью (7 правил: bare_except,
  print_call, long_line, secret, todo, missing_test, missing_docs) с
  severity и доказательством (файл+строка); тесты
- [x] Task 7: `GitReader` — ветка, изменённые файлы, diff; деградация
  без git/репозитория; тесты

## Модули ассистента (TDD)
- [x] Task 8: модуль А — `ask_project()`: RAG по корпусу проекта,
  ответ с источниками, «не знаю» без LLM при пустом поиске,
  extractive-деградация без LLM; тесты
- [x] Task 9: модуль Б — `review_diff()`: разбор + проверки +
  сопоставление с документацией через индекс + сводка; отчёт без
  LLM; тесты
- [x] Task 10: модуль В — `support_answer()`: персонализация по
  тикету и CRM, блок `context`, ответ по базе знаний; тесты
- [x] Task 11: модуль Г — сценарии `find_usages`, `update_docs`,
  `generate`, `check_invariants`, `diff_report`; allow-list путей
  записи, `ScenarioError` вне проекта, возврат `changed_files`+`diff`;
  тесты (включая отказ записи по `..` и абсолютному пути)

## Роуты (TDD)
- [x] Task 12: `studio/backend/main.py` — `GET /api/assistant/status`,
  `POST /api/assistant/index`, `POST /api/assistant/ask`,
  `POST /api/assistant/review`, `GET /api/assistant/tickets`,
  `POST /api/assistant/support`, `GET /api/assistant/scenarios`,
  `POST /api/assistant/scenario`, `GET /api/assistant/git`; тесты

## UI
- [x] Task 13: вкладка «Ассистент» (`AssistantTab.tsx` + кнопка в
  шапке, состояние в `state.tsx`, типы и вызовы в `api.ts`):
  4 секции — вопрос по проекту, ревью diff, тикеты, сценарии

## Проверка (e2e)
- [x] Task 14: `scripts/e2e_day31.py` — Part A офлайн (TestClient:
  вопрос по проекту с источниками, ревью diff с находками, поддержка
  по тикету с CRM-контекстом, 5 сценариев, отказ записи вне проекта)
  + Part B live (реальный сервер, реальная Ollama, реальный git diff)

## Документация
- [x] Task 15: README (строка дней 30–34 в таблице + секция),
  RELEASE.md (release notes), `.env.example` (если добавлены
  переменные), evidence в `.omo/evidence/day31/`

## Финальные цифры

- офлайн-тесты ассистента: `test_assistant.py` — **98**, `test_assistant_api.py` — **31** (всего 129)
- полная регрессия бэкенда: **1088 passed, 6 skipped** (до изменений — 959)
- фронтенд: `tsc -b` 0 ошибок, `vite build` успешно, vitest **330 passed**, oxlint 0 ошибок
- `scripts/e2e_day31.py`: **Part A 17/17 + Part B 7/7 = PASS 24, FAIL 0, SKIP 0**
- корпус проекта: **209 файлов** (docs 15, spec 88, code 106) → 4234 чанка; мусора нет
- evidence: `.omo/evidence/day31/`
