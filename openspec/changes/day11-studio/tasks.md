# Tasks: day11-studio

## 1. Каркас

- [x] 1.1 Создать ветку `day11-studio` от `master`; папка `studio/`
      (backend/, frontend/, data/, README.md), `.gitignore` (studio/data/,
      node_modules, dist)
- [x] 1.2 Бэкенд: FastAPI hello + uvicorn + pytest (первый тест проходит)
- [x] 1.3 Фронтенд: Vite + React + TS hello, dev-прокси `/api` → :8000

## 2. Бэкенд: память (TDD)

- [x] 2.1 `memory.py`: MemoryStore — диалоги (ST) CRUD + активный
- [x] 2.2 `memory.py`: WM (per-dialogue key/value) + LT (глобальный key/value) CRUD
- [x] 2.3 `memory.py`: build_memory_blocks (инъекция, формат D4) + layer_stats
- [x] 2.4 Атомарные JSON-записи, битый файл → дефолты (тесты)

## 3. Бэкенд: агент и чат (TDD)

- [x] 3.1 `agent.py`: StudioAgent — сборка payload ([system+блоки] + ST), конфиг (model/temperature/max_tokens/system_prompt)
- [x] 3.2 `agent.py`: ask_stream — SSE (delta/done/error), httpx-стрим GPustack, деградация (D8)
- [x] 3.3 Журнал запросов (requests.json, cap 100) + usage-учёт
- [x] 3.4 Маршруты main.py: /api/chat, /api/config, /api/models, /api/dialogues* (валидации 400)
- [x] 3.5 Маршруты main.py: /api/memory* (CRUD + clear + 400/404), /api/tokens, /api/requests*

## 4. Фронтенд: раскладка и панели (TDD)

- [x] 4.1 styles.css — дизайн-токены mid-dark (D5), грид 3 панели
- [x] 4.2 state.tsx — Context (диалоги, память, токены, запросы, конфиг, showRequests)
- [x] 4.3 Sidebar — диалоги + сводка памяти (имена RU, title EN)
- [x] 4.4 ChatPanel — лента, SSE-поток (fetch + ReadableStream), инпут
- [x] 4.5 ContextPanel: вкладка «Память» (3 секции, CRUD, title EN)
- [x] 4.6 ContextPanel: вкладка «Токены» (HUD + по слоям + сессия)
- [x] 4.7 glossary.ts + вкладка «Запрос» (аннотации, fallback-пометка, toggle, журнал)

## 5. Сборка, e2e, README

- [x] 5.1 Prod: `npm run build` → dist, StaticFiles в main.py (SPA fallback)
- [x] 5.2 `scripts/e2e_studio.py` (порт 8100, поток D10, exit 0/1)
- [x] 5.3 README: строка «День 11 (студия)» + секция
- [x] 5.4 Финальная проверка: pytest -q (65 PASS), npm test (48 PASS),
      e2e_studio (10/10 PASS), openspec validate
