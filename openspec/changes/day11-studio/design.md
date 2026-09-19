# Design: day11-studio

## Context

Дизайн интерфейса утверждён итеративно (brainstorming, визуальные макеты A–M):
раскладка «Студия» (вариант M), тема mid-dark «чуть светлее», имена слоёв
«Диалог / Текущая задача / Долговременная» с английским обозначением при
наведении, в «Запросах» — пояснение каждого параметра + правило для
неизвестных. Подход реализации выбран «сбалансированный»: чистый CSS с
дизайн-токенами, React Context, SSE-стриминг, JSON-файлы, pytest + Vitest
smoke. Памятью управляет человек (семантика референса xfrwrd/ai-advent-9):
без авто-извлечения, без routing-правил.

Ограничения: ветка от `master` (НЕ от `day11`/`day10`), код дней 1–10 не
наследуется; RU-комментарии/docstring, ошибки — RU-сообщения; данные и
секреты в `.gitignore`.

Мотивация: proposal.md. Требования: specs/studio-app/spec.md.

## Goals / Non-Goals

**Goals:**

- Самостоятельное приложение: `studio/backend` (FastAPI) + `studio/frontend`
  (React/Vite/TS), запускается и тестируется без кода других дней.
- 3 слоя памяти, ручное управление, наблюдаемая инъекция в system-промпт.
- «Студийный» UI: три панели, mid-dark, имена слоёв RU + EN на hover.
- Token HUD и аннотированный журнал запросов (fallback для неизвестных
  параметров).
- Проверка: pytest (офлайн) + Vitest smoke + e2e + README.

**Non-Goals:**

- Механики дней 1–10 (сжатие/сводка, стратегии, sticky_facts, branching,
  request-journal day7, calibration token-норм дня 8) — не переносятся.
- Авто-извлечение фактов LLM, routing-правила, сема
  «архивация задачи», векторный поиск, мульти-пользовательность.
- Auth, деплой, CI.

## Decisions

### D1. Структура и ветка

```
studio/
  backend/
    main.py        # FastAPI app, маршруты, статика (prod)
    agent.py       # LLM-клиент (httpx), сборка payload, SSE-стрим
    memory.py      # 3 слоя: CRUD, инъекция, атомарные JSON
    glossary.py    # (опц.) серверный словарь — см. D7: словарь на фронте
    tests/         # pytest
  frontend/
    src/
      main.tsx, App.tsx
      components/  # Sidebar, ChatPanel, ContextPanel, MemoryTab,
                   # TokensTab, RequestsTab
      glossary.ts  # словарь пояснений параметров (отдельный файл)
      state.tsx    # React Context (диалоги, память, токены, запросы)
      styles.css   # дизайн-токены (CSS-переменные) + раскладка
    tests/         # Vitest smoke
  data/            # JSON-файлы (.gitignore)
  README.md        # запуск dev/prod, структура
```

Ветка `day11-studio` от `master`. Корневой README: строка «День 11 (студия)» +
секция.

Альтернатива: вложенный отдельный git-репозиторий — отклонено: challenge-репо
одно, ветки — конвейер дней.

### D2. Бэкенд: модули и контракты

- `main.py`: `FastAPI` + `uvicorn`; CORS для dev (origin :5173); в prod
  `StaticFiles(frontend/dist)` на `/` (после API-маршрутов). Все ошибки —
  RU-сообщения, HTTP 400/4002.
- `agent.py`: класс `StudioAgent`:
  - `ask_stream(dialogue_id, user_text) -> AsyncIterator[str]` — SSE:
    `data: {json}` чанки `{"type": "delta"|"done"|"error", ...}`; внутренний
    запрос к GPustack через `httpx.AsyncClient.stream` (OpenAI-совместимый
    `/chat/completions`, `stream: true`);
  - сборка payload: `[system] + ST-сообщения диалога` + блоки WM/LT (D4);
    параметры: `model` (из config, дефолт `qwen3.8-27b`), `temperature` 0.7,
    `max_tokens` 2048 — редактируемые `GET/POST /api/config`;
  - после ответа: запись usage в журнал запросов (D6), сохранение диалога.
  - Сбой API → событие `{"type":"error","message": RU}` (стрим не молчит).
- `memory.py`: класс `MemoryStore`:
  - `dialogues` (ST): `{"dialogues": [{id, title, created, messages: [{role, content}]}], "active_id": ...}`;
  - `working` (WM): `{dialogue_id: {key: value}}`;
  - `long_term` (LT): `{key: value}`;
  - CRUD: `dialogue_new/list/get/switch/delete`, `wm_set/wm_get/wm_remove/wm_clear`,
    `lt_set/lt_get/lt_remove/lt_clear`, `st_clear`;
  - `build_memory_blocks(dialogue_id) -> str` — инъекция (D4);
  - `layer_stats() -> dict` — число записей + оценка токенов (chars/4,
    задокументированная эвристика);
  - атомарная запись (tmp + `os.replace`), UTF-8, `ensure_ascii=False`;
    битый/отсутствующий файл → дефолты (без crash); все мутации под `threading.Lock`.
- Файлы: `studio/data/dialogues.json`, `working.json`, `longterm.json`,
  `requests.json`.

Альтернатива: SQLite — отклонено: объём данных мал, JSON (как в референсе),
читаемость для обучающего проекта.

### D3. REST API

| Метод | Маршрут | Назначение |
|---|---|---|
| POST | `/api/chat` | `{"dialogue_id": str, "message": str}` → SSE-стрим (delta/done/error); `done` несёт `{answer, usage, request_id}` |
| GET | `/api/config` / POST | `{"model", "temperature", "max_tokens", "system_prompt"}` |
| GET | `/api/models` | список моделей GPustack + `context_limit` |
| GET | `/api/dialogues` | список (id, title, created, message_count) + active |
| POST | `/api/dialogues` | новый диалог (становится active) |
| GET | `/api/dialogues/{id}` | сообщения диалога |
| DELETE | `/api/dialogues/{id}` | удалить диалог (+ его WM) |
| POST | `/api/dialogues/{id}/activate` | сменить активный |
| POST | `/api/memory/st/clear` | очистить сообщения активного диалога |
| GET | `/api/memory` | `{dialogue: {count, tokens_est}, working: {entries, tokens_est, items}, long_term: {entries, tokens_est, items}}` (active-диалог) |
| POST | `/api/memory/working` | `{"key", "value"}` → set |
| DELETE | `/api/memory/working/{key}` | удалить key (active-диалог) |
| POST | `/api/memory/working/clear` | сброс WM активного диалога |
| POST | `/api/memory/longterm` | `{"key", "value"}` → set |
| DELETE | `/api/memory/longterm/{key}` | удалить key |
| POST | `/api/memory/longterm/clear` | сброс LT |
| GET | `/api/tokens` | `{session: {prompt, completion, reasoning, total}, last: {...}, context_limit}` |
| GET | `/api/requests` | журнал: `[{id, ts, model, tokens_total, has_error}]` (без тел) |
| GET | `/api/requests/{id}` | полное тело запроса + usage |
| DELETE | `/api/requests` | очистить журнал |

Валидация в роутах (тип/пустота → 400 с RU-текстом), бизнес-логика — в
`StudioAgent`/`MemoryStore`.

### D4. Инъекция памяти в system-промпт

- Базовый system-промт — из config (дефолт RU: роль ассистента).
- Непустая WM active-диалога → блок:
  `"\n\nТекущая задача:\n- key: value\n..."` (по всем key).
- Непустая LT → блок:
  `"\n\nДолговременная память:\n- key: value\n..."`.
- Пустой слой → блока нет (промт не меняется). Порядок: базовый → WM → LT.
- Слой «Диалог» (ST) в system-промпт НЕ инъектируется — это и есть
  `messages` payload.

### D5. Фронтенд: структура

- **App** — грид 3 колонки: `Sidebar` (260px) | `ChatPanel` (flex) |
  `ContextPanel` (360px); `styles.css` — CSS-переменные:
  `--bg: #1e1e24; --panel: #26262e; --panel-2: #2b2b34; --border: #3a3a44;
  --text: #ddd; --accent: #e07b39; --st: #4a9eff; --wm: #e0a039;
  --lt: #b48ce8; --ok: #3ddc84;`.
- **Sidebar**: брендинг «◆ День 11»; блок «ДИАЛОГИ» (список, активный
  подсвечен, «+ Новый диалог»); блок «ПАМЯТЬ» — три строки
  «● Диалог / Текущая задача / Долговременная» со счётчиками; у имён
  `title="Short-Term Memory"` и т.д. (hover — английское).
- **ChatPanel**: шапка (название диалога, бейдж модели); лента сообщений
  (user — справа оранжевая, assistant — слева панельная; стриминг —
  дописывание дельты); инпут-капсула (Enter — отправить, Shift+Enter —
  перенос).
- **ContextPanel**: вкладки Память / Токены / Запрос:
  - **Память**: три секции-карточки (имя RU + `title` EN, счётчик записей и
    токенов-оценка): «Диалог» — список сообщений (сворачивается) + [clear];
    «Текущая задача» — строки key→value + инпуты [key][value][+] + [clear],
    удаление по key; «Долговременная» — то же, глобально.
  - **Токены**: строки prompt / reasoning / total / лимит + прогресс-бар
    total/limit; блок «по слоям» (оценка); блок «за сессию» (кумулятив).
  - **Запрос**: toggle «Показывать запросы» (вкл/выкл — localStorage);
    последний запрос — карточка «→ Запрос #N · ts»: параметры строками
    `имя = значение — пояснение` (D7); карточка «← Ответ · расход токенов»:
    usage с пояснениями; журнал `#1 … #N` (клик — раскрыть, GET
    /api/requests/{id}).
- **Состояние**: один React Context (`state.tsx`): диалоги/активный, память,
  токены, запросы, конфиг, showRequests; обновление — после действий (не
  поллинг); SSE-стрим — `fetch` + `ReadableStream` (EventSource не умеет
  POST).
- Чистый CSS (без Tailwind), тёмная тема только (light — non-goal).

Альтернативы: Zustand/TanStack Query — отклонено (подход 3: Context
достаточен для трёх панелей); Tailwind — отклонено (полный контроль палитры
токенами, меньше зависимостей).

### D6. Журнал запросов

- Запись на каждый chat-запрос: `{id (seq), ts (ISO), model, request: {полное
  тело LLM-запроса}, usage: {prompt_tokens, completion_tokens,
  total_tokens, reasoning?}, error: str|null}`.
- Хранение: `requests.json` (cap 100 последних, FIFO-отбрасывание старых).
- Сервер отдаёт список без тел (`/api/requests`) и тело по id
  (`/api/requests/{id}`) — сетевая экономия; фронт кэширует раскрытые.

### D7. Аннотации параметров (glossary)

- `frontend/src/glossary.ts` — отдельный файл: `PARAM_G<string,
  string>` (RUпояснения) по стандарту OpenAI-совместимого API: `model`,
  `messages` (+ вложенные `role`/`content`), `temperature`, `top_p`,
  `max_tokens`, `stream`, `stop`, `seed`, `n`, `frequency_penalty`,
  `presence_penalty`, `logprobs`, `top_logprobs`, `tools`, `tool_choice`,
  `response_format`, `user`, `service_tier`, `logit_bias` + usage:
  `prompt_tokens`, `completion_tokens`, `total_tokens`,
  `prompt_tokens_details`, `completion_tokens_details` (включая
  `reasoning_tokens`).
- Правило рендера: ключ в словаре → цветное имя + пояснение; **не в
  словаре** → всё равно показывается со значением, серым, пунктирная рамка,
  пометка «(доп. параметр, без описания)». Вложенные структуры (messages,
  details) раскрываются рекурсивно тем же правилом.
- Расширение словаря = правка одного файла (тест Vitest фиксирует покрытие
  стандартных ключей + fallback).

### D8. SSE-контракт и деградация

- Формат: `text/event-stream`, события `data: {"type": ...}\n\n`:
  - `{"type":"delta","text": "..."}` — фрагмент ответа;
  - `{"type":"done","answer": "...", "usage": {...}, "request_id": N}`;
  - `{"type":"error","message": "..."}` — RU-сообщение (сеть/API/таймаут).
- Деградация: если стрим оборвался без `done` — фронт показывает
  «стрим прерван» + частичный текст помечается; повторный ask — новый
  запрос. Таймаут первого события — 30 с.

### D9. Dev / Prod

- **Dev**: `cd studio/backend && uvicorn main:app --port 8000`;
  `cd studio/frontend && npm run dev` (Vite :5173, proxy `/api` → :8000).
- **Prod**: `npm run build` → `dist/`; `uvicorn main:app --port 8000` отдаёт
  и API, и статику (`/` → index.html, SPA fallback).пуск одним скриптом: `studio/run-dev1` / `run-dev.sh` (опционально).

### D10. Тесты и проверка

- **pytest** (`studio/backend/tests/`, офлайн, fake httpx-транспорт):
  - память: CRUD 3 слоёв, per-dialogue WM, персистентность LT (свежий
    экземпляр = те же данные), атомарность (битый файл → дефолты),
    layer_stats;
  - инъекция: блок WM/LT в system (есть/пусто/отсутствует) — точное
    сравнение промпта;
  - API: TestClient — чат (SSE: delta+done), config, dialogues, memory
    CRUD + 400-валидации, tokens, requests (создание/чтение/cap 100);
  - agent: payload = [system+блоки] + сообщения; параметры из config.
- **Vitest smoke** (`studio/frontend/tests/`): glossary — покрытие
  стандартных ключей + fallback-маркер для неизвестного; чистые функции
  state-редьюсеров (добавление сообщения, обновление памяти); рендер
  `RequestsTab` с mock-данными (unknown-параметр отображается с пометкой).
- **E2E** (`scripts/e2e_studio.py`): поднимает `uvicorn` на порту 8100,
  проходит HTTP-поток: config → новый диалог → chat (SSE, парсинг
  delta/done) → memory CRUD (WM active + LT) → tokens (usage на месте) →
  requests (запись создана, тело по id) → UI (index.html отдаётся, ключевые
  id/строки присутствуют). При недостижимом GPustack chat-шаг — SKIP (остальное
  проверяется). exit 0 = PASS/SKIP, 1 = FAIL.
- **README**: строка «День 11 (студия)» в таблице + секция (структура,
  запуск dev/prod, API-таблица, память, токены, запросы/glossary, тесты).

## Risks / Trade-offs

- [SSE-стрим обрывается (прокси/таймауты)] → деградация D8
  (partial + error-событие); e2e с SKIP при недоступном API; при повторных
  сбоях — фолбэк на не-стриминг (`stream: false`) одним тумблером в agent.
- [Эвристика токенов chars/4 расходится с реальными usage] → HUD
  показывает ТОЧНЫЕ usage из API; оценка — только для слоёв/лимита с
  пометкой «оценка» (задокументировано).
- [Две ветки «день 11» (day11 и day11-studio) путают] → README и таблица
  дней различают: «День 11» (ветка day11) и «День 11 (студия)»
  (ветка day11-studio); ветки независимы.
- [npm-зависимости на Windows (esbuild/sass)] → только Vite+React (esbuild
  бинарики работают), без нативных сборок.
- [Одна ветка master без студии] → студия не мёрджится в master до
  решения; это обучающая ветка (паттерн day10/day11).

## Migration Plan

1. `git checkout -b day11-studio master`.
2. Каркас: backend (FastAPI hello + pytest) → frontend (Vite hello) —
   dev-запуск зелёный.
3. TDD-блоки по tasks.md: память → agent/SSE → диалоги/tokens/requests →
   UI-панели → glossary → сборка/e2e/README.
4. Прогон: `pytest -q`, `vitest run`, `e2e_studio.py`, `openspec validate`.
5. Откат: ветка самодостаточна — `git checkout master`, папка `studio/`
   исчезает с веткой; данные `studio/data` — в `.gitignore`.

## Open Questions

- Не блокирующих: дефолтный system-промт финализируется при реализации
  (RU, «ассистент); cap журнала 100 — константа в коде,
  обсуждается при e2e.
