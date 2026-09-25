# Proposal: day19-mcp-pipeline

## Why

День 19: **композиция MCP-инструментов** — создать несколько MCP-инструментов
(`search`, `summarize`, `saveToFile`) и реализовать пайплайн: первый получает
данные, второй обрабатывает, третий сохраняет результат. Проверить:
автоматическое выполнение цепочки и корректность передачи данных между
инструментами. Ключевое архитектурное решение (решение пользователя):
**LLM-driven композиция** — оркестратора в коде нет. Модель сама, через
tool-loop дня 17 (лимит 5 итераций), решает, какие инструменты вызвать, в
каком порядке и сколько. Пайплайн динамический и меняется от запроса
пользователя: «какая погода?» → 1 инструмент; «найди, суммаризируй, сохрани
в PDF» → цепочка из 3. `saveToFile` принимает `format`
(`md|txt|json|pdf`); `pdf` — полный PDF с кириллицей (встроенный TTF-шрифт),
реализованный на чистом stdlib (TTF-парсер `head`/`hhea`/`hmtx`/`maxp`/
`cmap` 4+12 + `name`; glyf **не** парсится — весь шрифт встраивается целиком
как `FontFile2` в Type0/CIDFontType2).

## What Changes

- **`studio/mcp_servers/pipeline_tools.py`** (новый, только stdlib): stdio
  MCP-сервер (2024-11-05, newline-delimited JSON-RPC) по образцу
  `news_weather.py`. 3 инструмента: `search(query)` (сканирует `*.json` из
  каталога дайджестов дня 18, case-insensitive подстрока по
  `title`+`summary`+`snippet`+`city`, топ-20 по `generated_at` desc),
  `summarize(text, max_points=8)` (детерминированная экстрактивная
  сводка — частотная оценка предложений, без LLM; сервер офлайн),
  `saveToFile(filename, content, format=md|txt|json|pdf)` (атомарная запись
  tmp + `os.replace` в `data/pipeline/`; `filename` — только basename,
  санитизация, traversal не проходит; `pdf` → `pdf_writer.text_to_pdf`).
  Env (читаются на время вызова): `PIPELINE_SEARCH_DIR`
  (дефолт `<repo>/data/digests`), `PIPELINE_OUT_DIR`
  (дефолт `<repo>/data/pipeline`), `PIPELINE_FONT_PATH`. Ошибка —
  `{"error": ...}` + `isError: true`, процесс не падает.
- **`studio/mcp_servers/pdf_writer.py`** (новый, только stdlib, ~450 строк):
  `PdfError`, `find_default_font() -> str|None` (env `PIPELINE_FONT_PATH`
  (если задан — обязан существовать и быть `.ttf`) → первый из
  `arial.ttf`/`segoeui.ttf`/`tahoma.ttf` в `C:\Windows\Fonts` → `None`),
  `text_to_pdf(text, title="", font_path=None) -> bytes`. TTF-парсер
  `struct`: `head` (unitsPerEm), `hhea` (ascent/descent/numHMetrics/bbox),
  `maxp` (numGlyphs), `hmtx` (advance; short-run — последний advance
  повторяется), `cmap` (форматы 4 + 12; приоритет (3,10)→(3,1)→(0,*)),
  `name` (family/subfamily → санитизированный `/BaseFont`). PDF 1.4: A4
  595×842, поля 72pt, 11pt body / 14pt title, межстрочный 1.45, перенос по
  hmtx-ширинам (hard-break слов длиннее строки), мультистраничность;
  Type0/`Identity-H` → CIDFontType2 (`/CIDToGIDMap /Identity`, `/DW` +
  `/W`-исключения из hmtx), FontDescriptor + сырой TTF как `/FontFile2`,
  `/ToUnicode` CMap через `begincidrange` (GID→Unicode). Без дат — байты
  детерминированы (повторный вызов → идентичные байты).
- **`studio/backend/mcp.py`**: **Pipeline Tools** — пятый дефолт реестра
  (`command = [sys.executable, <repo>/studio/mcp_servers/pipeline_tools.py]`,
  stdio, без npx, `enabled: true`). Порядок дефолтов: Firecrawl, Git,
  Task Manager, News & Weather, Pipeline Tools.
- **Тесты** (новые, офлайн): `studio/backend/tests/test_pdf_writer.py`
  (7 тестов: структура PDF, xref-сдвиги, мультистраничность, детерминизм,
  кириллица, `PdfError` при битом/отсутствующем шрифте),
  `studio/backend/tests/test_pipeline_tools.py` (12 тестов: subprocess-клиент
  — 3 tools, error-ветки, traversal, атомарность, 5-й дефолт реестра).
  Существующие тесты дефолтного списка серверов дополнены 5-м именем
  (однстрочная правка, последствие нового дефолта).
- **E2E** (`scripts/e2e_day19.py`, новый, stdlib, порт 8103): **Part A** —
  детерминированное ядро в-процессе (без uvicorn/сети): `MCPRegistry` +
  реальный subprocess `pipeline_tools.py` (3 tools), seeded-дайджесты,
  прямые `call_tool` sanity, затем `StudioAgent` + `httpx.MockTransport`
  fake-LLM со скриптованной цепочкой `search → summarize → saveToFile`
  (стадия — по счётчику `role:"tool"` в payload): assert на порядок
  tool-сообщений, **передачу данных** (выход этапа N ⊂ вход N+1),
  `pipeline_report.pdf` (`%PDF-1.4` + `/ToUnicode`). **Part B** — live
  (uvicorn :8103, реальный LLM), best-effort: запрос «найди записи про
  Самара, суммаризируй, сохрани в PDF» → PASS, если ≥1 tool-сообщение и
  PDF на диске; иначе SKIP (поведение модели, не FAIL).
- **Агент/фронтенд/роуты не трогаются**: tool-loop дня 17 сам подхватит
  3 новых инструмента (`_llm_tools`). Композиция целиком на стороне модели.

## Capabilities

### New Capabilities

- `mcp-pipeline`: композиция MCP-инструментов — stdio-сервер
  `pipeline_tools` (`search`/`summarize`/`saveToFile`), stdlib-PDF-движок
  `pdf_writer` (кириллица, встроенный TTF, детерминированные байты),
  LLM-driven цепочка через tool-loop дня 17, e2e-гибрид с assert на
  передачу данных между этапами.

### Modified Capabilities

- `mcp-integration` (день 16): пятый дефолт реестра `Pipeline Tools`
  (stdio, локальный python, без npx); регресс: без подключённых серверов
  `tools` в LLM-payload нет — не меняется.
- `mcp-tool-loop` (день 17): модель получает 3 составных инструмента
  (`search`/`summarize`/`saveToFile`); логика цикла не меняется.

## Impact

- **Код**: новые `studio/mcp_servers/pipeline_tools.py`,
  `studio/mcp_servers/pdf_writer.py`,
  `studio/backend/tests/test_pdf_writer.py`,
  `studio/backend/tests/test_pipeline_tools.py`, `scripts/e2e_day19.py`;
  правки `studio/backend/mcp.py` (`_default_servers`) и 3 существующих
  теста-списка-дефолтов (5-е имя).
- **Данные**: новый runtime-каталог `data/pipeline/` (результаты
  `saveToFile`; в `.gitignore`); источник `search` — `data/digests/`
  (день 18).
- **Зависимости**: никаких (только stdlib; без npx; без API-ключей).
- **Совместимость**: REST/SSE/UI не меняются; старые 4 дефолта на месте.
