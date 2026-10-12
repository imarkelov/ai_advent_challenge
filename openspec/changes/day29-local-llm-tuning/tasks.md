# Tasks: day29-local-llm-tuning

Задачи дня 29: оптимизация локальной LLM под задачу «поиск новых
сериалов» — параметры (temperature / max tokens / context window),
квантование, prompt-шаблон. Финальные цифры — после задачи 8.

## Данные: реальный каталог (TDD-неприменимо, источник внешний)
- [x] Task 1: `scripts/fetch_series.py` — загрузка каталога из Wikidata
  Query Service (SPARQL, открытые данные, без ключей): один большой
  запрос (сериалы P31=Q5398426 с годом P577, IMDb P345, жанрами P136,
  страной P495, языком P364, сезонами P2437, эпизодами P1113),
  терпеливый повтор на HTTP 429 (во время аварии WDQS лимит 1
  запрос/мин), кэш в `data/series/catalog_raw.json`
- [x] Task 2: `scripts/build_series_catalog.py` — нормализация:
  дедупликация по IMDb id (склейка строк «жанры × страны»),
  канонизация 263 грязных меток Wikidata в 18 жанров, отсев
  не-сюжетного (реалити/ток-шоу/новости/спорт/кулинарные),
  отсев записей с QID вместо названия, стабильные id →
  `catalog.json` + `catalog.txt` (400 сериалов, 18 жанров, 52 страны)

## Оптимизация: профили (TDD)
- [x] Task 3: `studio/backend/tuning.py` — `TaskProfile` (frozen:
  model/temperature/top_p/num_predict/num_ctx/seed/system_prompt/
  stop/format_json), `PROFILES` (studio — регрессия дней 11–28 с
  num_ctx=None; series — цель дня; series_q8 — замер квантования;
  series_json — контроль JSON-режима), `profile_for`,
  `sampling_body`, `SERIES_SYSTEM_PROMPT` (роль + каталог + 4 жёстких
  правила + few-shot + лазейка «вернуть пусто»),
  `build_series_user_prompt`, `extract_json` (чистый JSON /
  ```json-обёртка / сбалансированный объект-массив в тексте с учётом
  строк и экранирования), `parse_series_answer` (никогда не бросает),
  `validate_profile_params`; тесты СНАЧАЛА в `test_tuning.py` — 67 тестов

## Оптимизация: кейс поиска сериалов (TDD)
- [x] Task 4: `studio/backend/series.py` — `SeriesFinder`: каталог
  (`load_catalog`/`catalog_stats`), разбор запроса в ограничения
  (`parse_query`: жанры с алиасами, страны в прилагательных
  «российск/британск/корейск», «после/новее/не старше/ровно N года»),
  детерминированный отбор кандидатов БЕЗ LLM (одинаковый вход для
  всех профилей; кандидатов больше, чем подходящих — «ловушки»),
  вызов модели: `num_ctx` → НАТИВНЫЙ `/api/chat` с `options.num_ctx`,
  иначе OpenAI-совместимый путь (регрессия), подстановка
  названий/годов/imdb ИЗ КАТАЛОГА (выдуманное название невозможно),
  метрики (format/hallucination/constraint/latency/tokens-per-sec),
  `compare` (падение запроса не ломает сравнение); тесты СНАЧАЛА в
  `test_series.py` — 43 теста

## Роуты (TDD)
- [x] Task 5: `studio/backend/main.py` — `GET /api/tuning/profiles`,
  `POST /api/tuning/profile` (400 на неизвестный), `GET
  /api/tuning/report`, `GET /api/series/catalog`, `POST
  /api/series/search` (400 запрос/limit/профиль; 502 каталог/Ollama),
  `POST /api/series/compare`; `SeriesFinder` в `create_app` (DI для
  тестов); `agent.get_tuning_profile`/`set_tuning_profile` — активный
  профиль отдельным файлом `tuning.json` (config.json не расширяем:
  контракт `POST /api/config` дней 11–28 сохраняется); тесты в
  `test_series_api.py` — 29 тестов

## E2E
- [x] Task 6: `scripts/series_gold.py` — золотой набор 20 кейсов
  (17 выполнимых + 3 «пустых» на честный отказ) с проверяемыми
  машинно ограничениями; `check_constraints`/`satisfiable_count`
- [x] Task 7: `scripts/series_eval.py` — оценка профиля на золотом
  наборе: format_validity, hallucination_rate, constraint_satisfaction,
  coverage, empty_honesty, latency_p50/p95, tokens_per_sec; сравнение
  профилей, отчёт `data/series/optimization_report.json`
- [x] Task 8: `scripts/e2e_day29.py` — Part A офлайн (8 проверок:
  профили, переключение, `num_ctx` нативным путём + регрессия studio,
  промпт-шаблон, антигаллюцинация, валидация, сравнение, каталог),
  Part B live (B1 `ollama ps` показывает CONTEXT == num_ctx профиля,
  B2 качество на золотом наборе, B3 квантование Q8_0 vs Q4_K_M,
  B4 отчёт)

## Фронтенд
- [x] Task 9: `studio/frontend` — вкладка «Сериалы» (`SeriesTab.tsx`:
  выбор профиля с показом temperature/max tokens/context window,
  промпт-шаблон по кнопке, статистика каталога, запрос + подсказки,
  карточки рекомендаций, метрики, сравнение профилей),
  `SeriesOverlay.tsx`, кнопка 🎬 в шапке чата, `seriesOpen` в
  `state.tsx`, типы и хелперы в `api.ts`, CSS; тесты
  `tests/series-tab.test.tsx`; `npx tsc -b` exit 0

## Документация
- [x] Task 10: `openspec/changes/day29-local-llm-tuning/`
  (proposal/design/tasks/specs); `studio/backend/README.md` (секция
  дня 29); `README.md` (строка дня + секция); `RELEASE.md`; `.env.example`
  при необходимости

## Интеграция и регрессия
- [x] Task 11: полная регрессия: `pytest -q -m "not live"` → 801
  passed, 6 skipped, 0 failed (было 607 до дня 29; +194 теста);
  `npx tsc -b` → exit 0; `vitest run` → 323 passed; `e2e_day29.py` →
  Part A 8/8 PASS, Part B 3 PASS + 1 SKIP (Q8_0 не скачался), exit 0;
  живые цифры (до/после) в отчёт `data/series/optimization_report.json`
  и README/RELEASE.

## Итоговые цифры (золотой набор, 20 кейсов)

| Профиль | Формат | Выдумки | Ограничения | Покрытие | p50 | Ток/с |
|---|---|---|---|---|---|---|
| `studio` (до) | 100 % | 0 % | **0 %** | 0 % | 3.62 с | 71.2 |
| `series` (после) | 100 % | 0 % | **99 %** | 100 % | 3.38 с | 65.7 |
| `series_glm` | 100 % | 0 % | 100 % | 100 % | 5.95 с | 41.2 |
| `series_gemma` | 100 % | 0 % | 98 % | 100 % | 18.45 с | 12.6 |

Ключевые находки, полученные замером (не из документации):
1. `num_ctx` игнорируется OpenAI-совместимым API — нужен нативный
   `/api/chat` (проверено `ollama ps`: 32768 → 8192).
2. Reasoning-модели (`glm-4.7-flash`) возвращают пустой `content` без
   `think=False` — 0 рекомендаций на 20/20 кейсов → 100 % после фикса.
3. Профиль без модели (`studio`) уходил запросом без `model` → 400;
   добавлен `_resolve_model` (явная → профиль → конфиг → дефолт).
4. Цена KV-кэша различается в 8 раз между моделями при одном
   `num_ctx` (qwen3-coder 0.75 ГиБ против gemma3 3.88 ГиБ).

## Вне scope (guardrails)
Провайдер/маршрутизация дней 26–28 (`LOCAL_MODELS`, `_base_for`,
`list_models`, `ollama`-эмбеддер) не меняются — только аддитивно
используются; профиль `studio` воспроизводит поведение до дня 29
(`POST /api/chat`, `build_payload`, память, инварианты, task-пайплайн,
MCP, реранкер/`min_score`, `CITE_RULE` — без изменений); RAG-поиск
(`search`/`search_rag`/`_rrf`/`_bm25_scores`) не трогаем; новых
pip/npm-зависимостей нет; формат `index.db` (version 1) не меняется;
fine-tuning вне scope — оптимизация только на инференсе.
