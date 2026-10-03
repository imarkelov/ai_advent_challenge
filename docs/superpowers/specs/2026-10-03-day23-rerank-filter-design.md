# День 23 — Реранкинг и фильтрация: min_score, query rewrite, 4-режимное сравнение

> **Дата:** 2026-10-03 · **Ветка:** `day23-rerank-filter` (от `day22-ui-rework`) · **Статус:** реализовано (бэкенд 579, фронтенд 296, e2e Part A 7/7 + Part B live 11 PASS / 0 FAIL / 1 WARNING)

## 1. Задание дня

Поверх базы знаний дня 21 и сравнения дня 22:

1. **Порог отсечения нерелевантных результатов** — настройка `min_score`
   (0..1, дефолт 0.0 = off): результат остаётся, если его оценка
   `>= min_score`; фильтрация — после stage1-отбора (и rerank), до
   top_k-среза.
2. **Топ-K до и после фильтрации** — показать, какие чанки отобраны
   без порога и какие прошли порог.
3. **Сравнение качества без фильтра/rewriting и с ними** — live-прогон
   контрольных вопросов в режимах plain / rag / rag+filter / rag+rewrite
   с отчётом (факт-чек + LLM-judge).
4. **Query rewrite** — LLM-перефраз вопроса для точного поиска по базе
   документов (одним предложением, ключевыми словами).

## 2. Связь с днями 21–22

- **День 21 (`day21-doc-indexing`)** — `search_rag` уже 2-этапный:
  гибридный recall top-`rag_recall` (вектор + BM25, RRF) → опциональный
  cross-encoder `qwen3-reranker-4b` (rerank_score 0..1) → top-`rag_top_k`.
  Фильтр встраивается в этот пайплайн, пайплайн не пересобирается.
- **День 22 (`day22-rag-query`)** — `POST /api/rag/compare` с 2 армами
  (plain / rag), `agent.rag_compare` на `_task_llm_call` (T=0,
  max_tokens=1024, bare system-промпт, журнал `requests.json` не
  пишется), 10 контрольных вопросов-фикстур, скрипт сравнения с
  отчётом. День 23 расширяет compare до 4 арм аддитивно.
- **День 22-ui-rework** — текущая база ветки: сайдбар, TokenGauge,
  FlowInspector.

## 3. Зафиксированные решения (Q1–Q8)

### Q1. Позиция фильтра: после stage1-отбора (и rerank), до top_k-среза

Одна точка в `kb.search_rag`. До реранка отсекать нельзя — реранкер
пересматривает оценки, порог по устаревшим счётам уберёт то, что
cross-encoder признал бы релевантным. После top_k-среза нельзя — задание
«топ-K до и после фильтрации» требует выбирать top_k из прошедших порог.

### Q2. Семантика min_score: on/off, `>=`, относительная нормализация без реранкера

`min_score` — число 0..1, дефолт **0.0**:

- `0.0` → фильтр выключен, поведение **байт-в-байт** как до дня
  (поля `filtered`/`dropped` вообще не добавляются; регрессия закреплена
  снимком-сравнением списков).
- `> 0` → семантика `>=`.
- `reranker=api` (rerank сработал) → отсечение по абсолютной шкале
  cross-encoder: `rerank_score >= min_score`.
- `reranker=off` (или rerank деградировал на stage1) → относительная
  нормализация: `score >= min_score * best`, где `best = max(score)`
  по stage1-выдаче (guard: `best == 0` → пустой список, деления нет).
- `min_score = 1.0` допустим: `>=`, не `>` — перфект-совпадение остаётся.

Почему: абсолютная шкала 0..1 осмыслена только для cross-encoder
(вероятность-подобная оценка пары). Сырой RRF-счёт = Σ 1/(60+rank) ≈
**0.003–0.033** — абсолютный порог 0.5 на нём отсекал бы ВСЕГДА, 0.01 —
почти никогда. Без реранкера порог интерпретируется относительно лучшей
выдачи: «отбрось чанки, заметно хуже лучшего для этого запроса».
Осознанное следствие: в relative-режиме лидер (`score/best = 1.0`)
проходит любой порог < 1.0 — фильтруется хвост, не лидер.

### Q3. Четыре армы compare: plain | rag | rag+filter | rag+rewrite

- **plain** — голый system-промпт, без retrieval. Без изменений.
- **rag** — `config.system_prompt` + kb_block из `search_rag`. Без
  изменений.
- **rag+filter** — retrieval тем же `search_rag`, но с `min_score`
  (body-override запроса, иначе `settings["min_score"]`); ответ на
  оригинальном вопросе.
- **rag+rewrite** — сначала `rewrite_query(question)`; retrieval на
  **перефразированном** вопросе; ANSWER-LLM отвечает на **оригинальный**.

**plain/rag НИКОГДА не фильтруют**, даже при `min_score > 0` — это
контрольные руки, иначе фильтр неотличим от baseline. Body-override
`min_score` обязателен: при дефолте 0.0 арма rag+filter без override
дублировала бы rag. Ответ на оригинале в rewrite-арме: rewrite улучшает
**retrieval**, а не вопрос. Все руки — non-stream `_task_llm_call`
(T=0, max_tokens=1024, bare промпт, журнал не пишется); сбой руки —
строка «Ошибка: …», статус 200. Порядок вызовов: plain → rag → filter →
rewrite (T=0, max_tokens=200) → rewrite-арма (max_tokens=1024) — так
captured[0..1] остаются plain/rag (контракт захвата e2e).

### Q4. Query rewrite: один вызов, compare-only, graceful fallback

`StudioAgent.rewrite_query(question) -> (str, bool)`: один non-stream
LLM-вызов (тот же механизм `_task_llm_call`), **T=0, max_tokens=200**.
RU-system-промпт: «Ты перефразируешь вопрос пользователя для точного
поиска по базе документов. Ответь ОДНИМ предложением, ключевыми
словами, без приветствий и объяснений». Возврат:

- непустой ответ, **не идентичный** оригиналу (case-insensitive) →
  `(rewritten.strip(), True)`;
- пустой/whitespace/идентичный → `(question, False)`;
- исключение LLM → `(question, False)` + log warning (НЕ «Ошибка:»
  наружу).

Вызывается **только** из rewrite-армы `rag_compare`; из
`ask_stream`/чат-пайплайна — никогда (guardrail-тест). Один вызов, не
multi-query fan-out (удвоил бы LLM-вызовы без гарантированного выигрыша);
T=0/max_tokens=200 — детерминизм и жёсткий бюджет. Фолбэк — rewrite это
оптимизация: LLM down не должен ломать арму. Compare-only — решение
пользователя: live-чат остаётся предсказуемым, пока эффект rewrite не
доказан сравнением.

### Q5. Деградация: rewrite-сбой, reranker-down, всё-отфильтровано

Все три сценария — «ответ остаётся, качество ниже», ни один не даёт
5xx/«Ошибка:» на живой руке:

- **Rewrite-сбой**: `(question, False)` — retrieval на оригинале,
  `rewrite_applied: false`, `rewritten_query` = оригинал, ответ обычный.
- **Reranker down при `min_score > 0`**: фильтр не отключается и не
  падает — переключается на relative-режим Q2 по stage1-счётам.
- **Всё отфильтровано** (напр. `min_score=0.999`): 200,
  `chunks_rag_filter == []`, kb_block пуст, `answer_rag_filter` —
  непустой ответ без контекста («не знаю по базе» — честный ответ, это
  и есть смысл фильтра).

### Q6. Аддитивный контракт compare (e2e_day22 остаётся green)

Ответ `POST /api/rag/compare` расширяется **только добавлением полей**:
день 22 (`answer_plain`, `answer_rag`, `kb_block`, `chunks`,
`rag_context`) не меняется; добавляются `answer_rag_filter`,
`answer_rag_rewrite`, `chunks_rag_filter`, `chunks_rag_rewrite`,
`rag_context_rag_filter`, `rag_context_rag_rewrite`, `rewritten_query`,
`rewrite_applied`. plain/rag-руки выполняются ровно как в дне 22.
Фронтенд читает новые поля опционально (undefined-safe при старом
бэкенде). Регрессия: `scripts/e2e_day22.py` Part A 7/7 без правок
старых чеков.

Задокументированное отклонение: `e2e_day22.py` **обновлён** под
5-вызовный контракт (A2 `len(captured)==5`, A6 — индексы арм 0/1/2/4 +
rewrite(3), `COMPARE_TIMEOUT` 330→825); чеки старых полей не тронуты —
без него Part A физически не проходит (A6 ассертил ровно 2 non-stream
вызова).

### Q7. Валидация min_score: bool первым, 400 RU

В `kb.update_settings` (settings) и в body-override `POST /api/rag/compare`
— одна логика: (1) `isinstance(v, bool)` → 400 **первым** (Python-ловушка
`isinstance(True, int)` пропустила бы `true` как 1.0); (2) `int/float` и
`0.0 <= v <= 1.0` → 200, float; (3) остальное (строки `"0.5"`, None,
контейнеры) → 400. Detail RU: «min_score должен быть числом от 0 до 1».
Диапазон инклюзивный.

### Q8. E2E-порт 8107

Продолжение последовательности: 8100 — `e2e_studio.py`, 8101–8104 — дни
17–20, 8105 — день 21, 8106 — день 22. Каркас `e2e_day22.py`: Part A —
офлайн-детерминированное ядро (net cut `_NO_NET`, TestClient + fake-LLM
+ fake-reranker scores `[0.99, 0.5, 0.1]`, egg-book-фикстуры), MUST
PASS; Part B — live (uvicorn :8107, реальный GPustack LLM + API-реранкер)
best-effort. Cleanup всегда; exit 0 PASS/SKIP, 1 FAIL.

## 4. Текущее состояние (день 22) — якоря

| Якорь | Значение |
| --- | --- |
| `kb.search_rag` | 2-этапный поиск: recall top-`rag_recall` (50) → optional rerank → `[:top_k]`; сигнатура расширена `min_score: float = 0.0` |
| `kb.DEFAULT_SETTINGS` | `{agent_loop, rag, rag_top_k: 3, strategy, embedder, reranker: "off", rag_recall: 50}` + `min_score: 0.0` |
| `agent.rag_compare` | 2 армы (plain/rag), `_task_llm_call`, T=0, mt=1024 |
| `main.py` | `POST /api/rag/compare` {question}; `GET/POST /api/kb/settings` |
| Корпус | 6 файлов, 1666 чанков (structural + api-эмбеддер `qwen3-vl-embedding-8b`, dim 4096) |
| Тесты | бэкенд 548 PASS, фронтенд 288 PASS |

## 5. Целевая архитектура

### 5.1 Пайплайн фильтрации (`kb.search_rag`)

```
stage1: гибридный recall top-rag_recall (вектор + BM25, RRF)
[stage2: cross-encoder, если reranker=api → сортировка по rerank_score]
→ если min_score > 0: отсечение
      reranked:      rerank_score >= min_score          (абсолют)
      иначе:         score >= min_score * best          (относительное)
      best == 0 → []   (guard деления)
→ out["filtered"]=True, out["dropped"]=len(stage1)-len(kept)
→ срез [:top_k]
```

min_score = 0 → ответ байт-в-байт как до дня (без `filtered`/`dropped`).
Фильтр консистентен на всех 3 потребителях `search_rag`: live-чат
(`_rag_retrieve`), `GET /api/kb/search`, `rag_compare` — из одних и тех
же settings.

### 5.2 Compare 4 арм (`agent.rag_compare`, 5 LLM-вызовов)

```
POST /api/rag/compare {question, min_score?}
  eff_min = body.min_score ?? settings["min_score"]
  plain            (mt=1024)   — никогда не фильтрует
  rag              (mt=1024)   — никогда не фильтрует
  rag+filter       (mt=1024)   — search_rag(..., min_score=eff_min)
  rewrite_query    (mt=200)    — T=0, «одним предложением, ключевыми словами»
  rag+rewrite      (mt=1024)   — retrieval на перефразе, ответ на оригинале
  → 13 полей: 5 дня 22 + answer_rag_filter, answer_rag_rewrite,
    chunks_rag_filter, chunks_rag_rewrite, rag_context_rag_filter,
    rag_context_rag_rewrite, rewritten_query, rewrite_applied
```

Сбой руки — «Ошибка: …» в этой руке, статус 200. Всё-отфильтровано —
200, пустой kb_block, непустой ответ без контекста.

### 5.3 Валидация (settings + body, одна логика)

bool ПЕРВЫМ → 400; `int/float` 0..1 → float; строки/None/контейнеры →
400 RU «min_score должен быть числом от 0 до 1». `min_score` в
`DEFAULT_SETTINGS` (merge over defaults — старый settings.json
backward-совместим).

### 5.4 UI (`KbTab.tsx`)

- Поле **«Порог отсечения (0 = off)»** (число 0..1, step 0.05) в секции
  «Включить»; коммит на blur через `POST /api/kb/settings` (паттерн
  patchSettings; derived-значение, без init-эффекта).
- Секция «Сравнение RAG»: **4 панели** (plain / RAG / RAG+filter /
  RAG+rewrite, flex-wrap); чип «фильтр ≥ X» на filter-панели (при > 0);
  rewrite-chip с перефразом (копирование: clipboard +
  execCommand-фолбэк, «Скопировано» 1.5 c; виден при
  `rewrite_applied === true`); пустая арма — «—».
- Секция «Поиск по базе»: информационный чип «фильтр ≥ X» при
  `min_score > 0` (ответ несёт `filtered`/`dropped`).
- Undefined-safe: новые compare-поля опциональны, 4 панели рендерятся
  без краха на старом бэкенде.

### 5.5 Скрипт сравнения `scripts/compare_day23.py` (stdlib)

Live-прогон: **12 вопросов** (10 контрольных дня 22 + 2 «отвлекающих»
с очевидной нерелевантностью к корпусу) × 4 режима, body-override
`min_score=0.6`; отчёт `.omo/evidence/day23-compare/` (compare.json +
report.md): факт-чек expect_facts + LLM-judge. Результат: judge tie=5,
rag_wins=3, rag_filter_wins=2, plain_wins=2; rewrite_applied 12/12;
факт-покрытие 13/22 (59%) одинаково у всех 4 режимов; avg score: plain
6.0 / rag 9.0 / rag+filter 9.0 / rag+rewrite 8.8; sources_ok 7/10.

### 5.6 E2E `scripts/e2e_day23.py` (порт 8107)

**Part A** (офлайн, MUST PASS, 7 шагов): A1 settings GET/POST
`min_score` + валидация 400 (1.5 / -0.1 / "0.5" / true); A2 абсолютный
фильтр по `rerank_score` (0.6 → ровно 1 результат, `filtered`/`dropped`
только при > 0); A3 relative-режим (`score >= min_score * best`, порядок
сохранён); A4 compare 4 армы (13 ключей, filter-арма отфильтрована,
rag-арма нет, 5 вызовов, скриптованный rewrite); A5 сбой rewrite (500
на вызове с max_tokens=200) → фолбэк (`rewrite_applied=false`, 200);
A6 `min_score=0.999` → `chunks_rag_filter == []`, `answer_rag_filter`
непустой; A7 совместимость с днём 22 (5 старых ключей compare + 6 полей
чанка). **Part B** (live, best-effort): B1 пересборка корпуса (chunks=94),
B2 настройки, B3 live compare с `min_score=0.5` (13 ключей,
`rewrite_applied=true`), B4 пасхалка IPhone 17Promax (WARNING, не FAIL).

## 6. Обработка ошибок

Сводка деградаций — см. Q5. Общий принцип дней 18/21/22: каждый сбой
деградирует на ближайшую рабочую конфигурацию, а не прерывает запрос.
Rewrite — оптимизация (фолбэк на оригинал, без «Ошибка:»); фильтр при
сбое реранкера — relative-режим, не отмена; пустой контекст — валидный
результат фильтра, ответ генерируется на голом промпте.

## 7. Тесты

| Слой | Было | Стало | Прирост |
| --- | --- | --- | --- |
| Бэкенд (pytest, офлайн) | 548 | **579** | +31 |
| Фронтенд (Vitest) | 288 | **296** | +8 |
| `tsc -b` + `npm run build` | clean | clean | — |

Ключевые кейсы: настройки + валидация (bool-гвард, строки, диапазон),
абсолютный/относительный фильтр (`>=`, guard best==0, порядок,
`filtered`/`dropped`, byte-identical при 0.0), compare 4 арм (13 ключей,
никогда-не-фильтрация plain/rag, ответ на оригинале в rewrite-арме),
rewrite fallback (пустой/идентичный/сбой), guardrail «rewrite_query не
в ask_stream», day22-совместимость.

E2E: Part A **7/7 PASS**; Part B live **11 PASS / 0 FAIL / 1 WARNING**
(B4 пасхалка — best-effort). Регрессия `e2e_day22.py`: **12/12 PASS**.

## 8. Документация и релиз

- **README.md** — строка в таблице дней + секция «День 23:
  Реранкинг и фильтрация» (Что это / Архитектура / API / UI / E2E /
  Проверка задания / Статус).
- **RELEASE.md** — секция дня 23 сверху, разделитель `---`.
- **docs/superpowers** — настоящий spec + план
  `2026-10-03-day23-rerank-filter.md`.
- Сноска в README о документированном отклонении: `e2e_day22.py`
  обновлён под 5-вызовный контракт (см. Q6).
- Демо-видео — скилл `studio-demo-video`, **вне репозитория** (паттерн
  дня 22).

## 9. Риски

| Риск | Митигация |
| --- | --- |
| RRF-шкала несовместима с абсолютным порогом | Relative-нормализация без реранкера (Q2); byte-identical гард при 0.0 |
| Python-ловушка `isinstance(True, int)` — `true` пройдёт как 1.0 | Bool-гвард первым (Q7) + тест на `{"min_score": true}` |
| 5 LLM-вызовов на compare — ~10–30 c на арму | Задокументировано; 5-я арма (filter+rewrite) запрещена scope-гардом; `COMPARE_TIMEOUT` в e2e_day22 поднят 330→825 |
| Cross-encoder оценки «плавают» между запусками | Absolute-порог честен только внутри одного прогона; в отчёте фиксированный body-override `min_score=0.6` |
| Rewrite добавляет задержку и неопределённость | Compare-only (чат не тронут), T=0, mt=200, фолбэк без «Ошибка:» |

## 10. Вне scope (YAGNI)

- Multi-query rewrite (несколько перефразов + фузия) — удвоил бы
  LLM-вызовы без гарантированного выигрыша.
- Auto-tuning порога (подбор min_score по метрикам) — вне задания.
- 5-я арма filter+rewrite — 2 фактора сразу, размывает интерпретацию.
- Rewrite в live-чате (`ask_stream`) — задержка в каждый запрос; только
  после доказательства эффекта сравнением.
- UI-слайдер истории порога / визуализация распределения score.
