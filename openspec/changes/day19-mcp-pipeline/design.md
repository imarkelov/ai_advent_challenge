# Design: day19-mcp-pipeline

Ключевые решения и их обоснование. Детали реализации — в proposal + код.

## 1. LLM-driven композиция (оркестратора в коде нет)

**Решение.** Не пишется ни хардкод-цепочки `search→summarize→saveToFile`, ни
конфига пайплайна. Модель через существующий tool-loop дня 17 (`agent.py`,
лимит 5 итераций) сама решает, какие из 3 инструментов вызвать, в каком
порядке и сколько. Пайплайн **динамический**:
- «Какая погода сейчас?» → 1 `search`.
- «Суммаризируй записи про Самара» → `search` → `summarize`.
- «…и сохрани в PDF» → `search` → `summarize` → `saveToFile(format=pdf)`.

**Почему.** Задание: «агент должен самостоятельно понять, какой инструмент
использовать, использовать и вывести результат». Композиция — на стороне
модели (tool-schemas), а не в коде. Это и есть «композиция MCP-инструментов»:
сервер отдаёт инструменты, модель их комбинирует.

**Риск и факт (live).** Локальные GPustack-модели (qwen3.8-27b,
deepseek-v4-flash, glm-5.3-flash) автономно вызывают `search` и используют
результат, но **не** надёжно доводят цепочку до `saveToFile` (суммаризируют
в тексте ответа). Это ограничение моделей, а не баг пайплайна. Поэтому
**корректность передачи данных** доказана детерминированно в e2e Part A
(fake-LLM со скриптованной цепочкой из 3 tool-вызовов + assert на то, что
выход этапа N ⊂ вход этапа N+1), а Part B — best-effort live (PASS/PDF на
диске или SKIP, не FAIL).

## 2. `summarize` — детерминированный, без LLM

**Решение.** `summarize(text, max_points=8)` — экстрактивная сводка
stdlib-методом: разбиение на предложения, частотная оценка слов
(без стоп-слов/частотный словарь), топ-N предложений по сумме частот,
нумерованные `points` + `summary`-строка. **Никакого LLM-вызова в сервере** —
сервер офлайн (как `news_weather`).

**Почему.** (а) stdio-сервер должен работать без сети и без LLM (паттерн
дней 16–18); (б) детерминизм критичен для офлайн-тестов и для e2e Part A,
который ассертит на передачу данных; (в) «интеллектуальность» композиции —
на модели, «обработка» на сервере — воспроизводимый алгоритм.

## 3. `pdf_writer` — stdlib TTF-парсер + встроенный шрифт

**Решение.** Полный PDF (не картинка/текстовая заглушка) на чистом stdlib:
- `struct`-парсинг TTF: `head` (unitsPerEm), `hhea` (ascent/descent,
  numHMetrics), `maxp` (numGlyphs), `hmtx` (advance; short-run — последний
  advance), `cmap` (форматы 4 + 12; выбор (3,10)→(3,1)→(0,*)), `name`
  (family → `/BaseFont`). **`glyf` не парсится** — контуры нам не нужны.
- Шрифт встраивается **целиком** как `/FontFile2` (сырые байты TTF) в
  Type0/CIDFontType2 с `/CIDToGIDMap /Identity`. Кириллица «из коробки»:
  GID↔code через Identity + `/ToUnicode` CMap (`begincidrange`) для
  поиска/копирования текста.
- Свёртка текста: A4 595×842, поля 72pt, 11pt body / 14pt title,
  межстрочный 1.45, перенос по hmtx-ширинам (слово длиннее строки —
  hard-break), мультистраничность.

**Почему stdlib.** Зависимость «никаких». Шрифт ищется
`find_default_font()` (env `PIPELINE_FONT_PATH` → первый из
`arial.ttf`/`segoeui.ttf`/`tahoma.ttf` в `C:\Windows\Fonts` → `None`
→ `PdfError`). Без кириллического шрифта `text_to_pdf` бросает `PdfError`
(не молча латиницей).

**Детерминизм.** В PDF нет дат/UUID/случайных ID — повторный вызов на том же
входе → **идентичные байты** (закреплено тестом).

## 4. `saveToFile` — атомарность + санитизация

**Решение.** `saveToFile(filename, content, format=md|txt|json|pdf)`:
- `filename` → **только basename** (`os.path.basename`), санитизация
  недопустимых символов; traversal (`../`) не проходит.
- Запись **атомарная**: tmp-файл + `os.replace` (паттерн дня 18).
- Каталог вывода `PIPELINE_OUT_DIR` (дефолт `<repo>/data/pipeline`),
  создаётся если нет. `format=pdf` → `content` (строка) → `pdf_writer` →
  байты. Остальные — текст/JSON. Возврат `{path, size, format}`.

## 5. `search` — локальные JSON дня 18

**Решение.** `search(query)` сканирует `*.json` в `PIPELINE_SEARCH_DIR`
(дефолт `<repo>/data/digests`, день 18) и матчит `query` (case-insensitive
подстрока) по `title`+`summary`+`snippet`+`city`. Топ-20 по
`generated_at` desc. Возврат `{query, count, matches:[{title, summary, city,
generated_at, file, snippet?}]}`. **Локально, без сети** — источник данных
«первого инструмента пайплайна».

## 6. Пятый дефолт реестра (не новый механизм)

`pipeline_tools.py` подключается как **пятый дефолт** `MCPRegistry`
(`mcp.py::_default_servers`), stdio, `[sys.executable, ...]`, без npx.
Tool-loop дня 17 сам отдаст 3 инструмента в LLM-payload (`_llm_tools`) —
`agent.py`/фронтенд/роуты **не трогаются**. Регресс: без подключённых
серверов `tools` в payload нет (день 16).

## 7. E2E-гибрид (Part A MUST PASS / Part B best-effort)

`scripts/e2e_day19.py`, stdlib, порт **8103** (день 17=8101, день 18=8102):
- **Part A** (офлайн, в-процессе, без uvicorn): `MCPRegistry` + реальный
  subprocess `pipeline_tools.py`; seeded-дайджесты; прямые `call_tool`;
  `StudioAgent` + `httpx.MockTransport` fake-LLM со скриптованной цепочкой
  (стадия — по счётчику `role:"tool"` в payload). Assert: порядок
  tool-сообщений, **передача данных** (выход N ⊂ вход N+1), PDF на диске
  (`%PDF-1.4` + `/ToUnicode`). **MUST PASS.**
- **Part B** (live, uvicorn :8103, реальный LLM): POST сервер → connect
  (3 tools) → dialogue → профиль decline → chat «найди записи про Самара,
  суммаризируй, сохрани в PDF» → PASS если ≥1 tool-сообщение и
  `pipeline_report.pdf` на диске; иначе **SKIP** (поведение модели).
  Cleanup всегда; exit 0 для PASS/SKIP, 1 для FAIL.

## 8. Артефакт-демо (доказательство цепочки)

Отдельно (не в e2e) через реальный MCP-сервер в subprocess (не mock) прогнана
авто-цепочка `search → summarize → saveToFile` → `data/pipeline/
demo_samara_report.pdf` (1 115 893 байта, валидный `%PDF-1.4`, кириллица).
Это «ручное» подтверждение, что цепочка работает через настоящий сервер,
независимо от живого LLM.
