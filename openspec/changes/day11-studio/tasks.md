# Tasks: day11-studio

<!-- Черновой уровень: детализировать (TDD-шаги, acceptance-критерии)
     writing-plans после утверждения design.md. -->

## 1. Каркас

- [ ] 1.1 Создать ветку `day11-studio` от `master`; папка `studio/`
      (backend/, frontend/, data/, README.md), `.gitignore` (studio/data/,
      node_modules, dist)
- [ ] 1.2 Бэкенд: FastAPI hello + uvicorn + pytest (первый тест проходит)
- [ ] 1.3 Фронтенд: Vite + React + TS hello, dev-прокси `/api` → :8000

## 2. Бэкенд: память (TDD)

- [ ] 2.1 `memory.py`: MemoryStore — диалоги (ST) CRUD + активный
- [ ] 2.2 `memory.py`: WM (per-dialogue key/value) + LT (глобальный key/value) CRUD
- [ ] 2.3 `memory.py`: build_memory_blocks (инъекция, формат D4) + layer_stats
- [ ] 2.4 Атомарные JSON-записи, битый файл → дефолты (тесты)

## 3. Бэкенд: агент и чат (TDD)

- [ ] 3.1 `agent.py`: StudioAgent — сборка payload ([system+блоки] + ST), конфиг (model/temperature/max_tokens/system_prompt)
- [ ] 3.2 `agent.py`: ask_stream — SSE (delta/done/error), httpx-стрим GPustack, деградация (D8)
- [ ] 3.3 Журнал запросов (requests.json, cap 100) + usage-учёт
- [ ] 3.4 Маршруты main.py: /api/chat, /api/config, /api/models, /api/dialogues* (валидации 400)
- [ ] 3.5 Маршруты main.py: /api/memory* (CRUD + clear + 400/404), /api/tokens, /api/requests*

## 4. Фронтенд: раскладка и панели (TDD)

- [ ] 4.1 styles.css — дизайн-токены mid-dark (D5), грид 3 панели
- [ ] 4.2 state.tsx — Context (диалоги, память, токены, запросы, конфиг, showRequests)
- [ ] 4.3 Sidebar — диалоги + сводка памяти (имена RU, title EN)
- [ ] 4.4 ChatPanel — лента, SSE-поток (fetch + ReadableStream), инпут
- [ ] 4.5 ContextPanel: вкладка «Память» (3 секции, CRUD, title EN)
- [ ] 4.6 ContextPanel: вкладка «Токены» (HUD + по слоям + сессия)
- [ ] 4.7 glossary.ts + вкладка «Запрос» (аннотации, fallback-пометка, toggle, журнал)

## 5. Сборка, e2e, README

- [ ] 5.1 Prod: `npm run build` → dist, StaticFiles в main.py (SPA fallback)
- [ ] 5.2 `scripts/e2e_studio.py` (порт 8100, поток D10, exit 0/1)
- [ ] 5.3 README: строка «День 11 (студия)» + секция
- [ ] 5.4 Финальная проверка: pytest -q, vitest run, e2e, openspec validate
