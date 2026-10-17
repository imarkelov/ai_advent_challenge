# Пайплайн ассистента: где лежит архитектура

Этот документ — **карта кода**: что происходит от вопроса пользователя
до ответа, какой файл отвечает за каждый шаг и где задаются промпты.
Промпты агентов — **документы `agents/*.md`**, а не хардкод в Python
(см. [AGENTS.md](AGENTS.md)).

Существуют два входа и **один** код обработки:

| Вход | Кто запускает | Промпт агента |
|---|---|---|
| CLI | `studio/backend/cli.py`, `ask` / `review` / `support` / `scenario` | `agents/<name>.cli.md` |
| CLI (автономный) | `dev_assistant.py`, вопрос без команды | `SYSTEM_PROMPT` в `dev_assistant.py` (свой инструмент, свой поиск) |
| Веб | `AssistantTab.tsx` → `POST /api/assistant/*` | `agents/<name>.api.md` |

Логика (поиск, ревью, CRM, сценарии) живёт в одном месте —
`studio/backend/assistant.py`; CLI и HTTP — тонкие обёртки над ним.

## 1. Вопрос по проекту (RAG) — основной пайплайн

```
вопрос
  │
  ▼  (1) выбор промпта агента
agents/project-qa.api.md   ← веб          ┐
agents/project-qa.cli.md   ← терминал     ┘ AgentRegistry (studio/backend/agents.py)
  │                                        читает frontmatter + тело, кэш по mtime
  ▼  (2) поиск по корпусу проекта
studio/backend/assistant.py :: ProjectKnowledgeBase.search_project
  │     → studio/backend/kb.py :: search_rag
  │         этап 1: search() — гибрид
  │             ├─ векторная нога: косинус по эмбеддингам
  │             │    OllamaEmbedder, модель OLLAMA_EMBED_MODEL (nomic-embed-text:v1.5)
  │             └─ лексическая нога: BM25 по чанкам (+ RRF-слияние рангов)
  │         этап 2: реранкер (если есть GPUSTACK_KEY_RERANK), иначе гибрид
  │         фильтр: min_score, затем top-k
  ▼  (3) сборка промпта
assistant.py :: ask_project
  │     • блок «факты о репозитории» (ветка, изменённые файлы, коммиты)
  │     • фрагменты [i] файл :: секция + текст (до 1500 символов)
  ▼  (4) вызов модели
assistant.py :: _llm → Ollama /chat/completions (или GPustack)
  ▼  (5) ответ + источники
{"answer", "sources": [{file, section, chunk_id, score, quote}], "llm"}
  │     без модели (llm=false) — extractive-ответ по найденным фрагментам
  ▼  (6) где посмотреть
веб: studio/frontend/src/components/AssistantTab.tsx + SourcesPanel.tsx
CLI: studio/backend/cli.py :: cmd_ask → render_ask
```

Корпус проекта — `assistant.py :: ProjectCorpus` (README, RELEASE,
`docs/`, `openspec/`, код бэкенда, фронтенда, скриптов; исключены
`.venv`, `node_modules`, `data`, `.omo`). Индекс — отдельный
`data/kb/project.db`, загрузки дня 21 (`data/kb/index.db`) не
затрагиваются.

## 2. Ревью изменений

```
git diff → assistant.py :: parse_unified_diff   (parse_unified_diff)
         → check_diff                          (правила: secret, bare_except,
                                                missing_test, missing_docs,
                                                print_call, long_line, todo)
         → findings (детерминированно, без LLM)
         → agents/code-review.api.md → LLM формулирует ≤5 рекомендаций
```

Модель **не ищет** проблемы — она только объясняет уже найденные
(промпт это запрещает прямо).

## 3. Поддержка по тикетам (CRM)

```
ticket_id → assistant.py :: TicketStore.context  (data/crm/*.json)
          → персонализация (план, оплата, роль, история)
          → agents/support.api.md → LLM
          → без модели — _fallback_support (факты CRM + фрагменты БЗ)
```

## 4. Автономный CLI-ассистент (`dev_assistant.py`)

Отдельный инструмент дня 31 — не использует сервис `assistant.py`,
поэтому у него свой поиск и свой промпт:

```
вопрос
  ▼  (1) индекс: .dev_assistant_rag.json (кэш, пересобирается по хэшу корпуса)
  │     StandaloneRAG (dev_assistant.py)
  │        • BM25: словоформы, стоп-слова, IDF уровня файла
  │        • векторы: Ollama bge-m3 (DEV_ASSISTANT_EMBED_MODEL)
  │        • слияние RRF
  ▼  (2) гейт релевантности
  │     плотность cosine + корпусность терминов (IDF)
  │     не о проекте → честный отказ, модель не вызывается
  ▼  (3) follow-up: «какой порт у него?» + предыдущий вопрос,
  │     добрать фрагмент с ответом из тех же файлов
  ▼  (4) промпт: dev_assistant.py :: SYSTEM_PROMPT
  ▼  (5) LLM: Ollama /api/chat (модель josiefied-qwen3-8b и др.)
```

Почему не `nomic-embed-text`, как в вебе: на корпусе проекта эта модель
не различает вопросы (cosine топ-5 отличался на 0.01), ассистент
отвечал не по запросу. `bge-m3` различает в разы — см. README,
раздел «День 36».

## 5. Токены и окно диалога

```
GET /api/tokens
  │  studio/backend/main.py :: tokens()
  ├─ окно: agent.model_context_window(model) → Ollama /api/show
  │        min(num_ctx из параметров, *.context_length архитектуры)
  │        (не справочник CONTEXT_LIMITS — он завышал бы окно)
  ├─ занято: prompt последнего запроса + резерв max_tokens (ответ)
  └─ остаток: окно − занято; «ещё влезет» = остаток / prompt
        │
        ▼
  studio/frontend/src/components/TokenGauge.tsx   (кольцо: занято / окно)
  studio/frontend/src/components/TokenPopover.tsx (остаток диалогу, резерв,
                                                   последний запрос, сессия)
```

Для автономного ассистента то же — в консоли после ответа:

```
🔢 Токены: промпт 2047 + ответ 487 = 2534 | окно 8 192 | остаток 5 658 (31% занято)
```

Числа — `prompt_eval_count` / `eval_count` Ollama (`dev_assistant.py ::
LLMClient.last_usage`), окно — `LLMClient.context_window()` из `/api/show`.
В вызов модели `num_ctx` передаётся из модели, а не константой 8192.

## 6. Где что лежит (сводная таблица)

| Что | Файл |
|---|---|
| Промпты агентов (веб) | `agents/<name>.api.md` |
| Промпты агентов (CLI) | `agents/<name>.cli.md` |
| Разбор и реестр агентов | `studio/backend/agents.py` |
| Сервис: поиск, ревью, CRM, сценарии | `studio/backend/assistant.py` |
| Индекс и гибридный поиск (веб) | `studio/backend/kb.py` |
| HTTP-роуты `/api/assistant/*` | `studio/backend/main.py` |
| CLI | `studio/backend/cli.py` |
| Автономный CLI-ассистент | `dev_assistant.py` |
| Корпус проекта | `assistant.py :: ProjectCorpus`, `CORPUS_RULES` |
| Данные CRM | `data/crm/users.json`, `data/crm/tickets.json` |
| Список агентов в API | `GET /api/assistant/agents` |
| Токены и остаток окна | `GET /api/tokens`, `TokenGauge.tsx`, `TokenPopover.tsx` |

## 7. Как проверить руками

```bash
# из корня репозитория
PY=.venv/bin/python

# любой агент: метаданные и тело промпта (ровно то, что пойдёт в модель)
$PY studio/backend/cli.py agents list
$PY studio/backend/cli.py agents show project-qa --execution cli

# вопрос по пайплайну — тот же путь, что и любой вопрос о проекте
$PY studio/backend/cli.py ask "как устроен пайплайн ответа на вопрос по проекту?"
$PY studio/backend/cli.py ask "для чего нужен агент support?"

# автономный ассистент
$PY dev_assistant.py "где лежит архитектура пайплайна?"
```
