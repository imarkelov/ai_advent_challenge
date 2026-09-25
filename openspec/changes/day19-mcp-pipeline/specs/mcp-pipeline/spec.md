## Purpose

Capability «mcp-pipeline» — композиция MCP-инструментов (день 19): собственный
stdio MCP-сервер `pipeline_tools` (3 компонуемых инструмента: `search` →
`summarize` → `saveToFile`), stdlib-PDF-движок `pdf_writer` (полный PDF с
кириллицей, встроенный TTF, детерминированные байты), LLM-driven цепочка через
tool-loop дня 17 (модель сама решает, какие инструменты вызвать, в каком
порядке и сколько — оркестратора в коде нет). Пути дней 16–18 (реестр,
tool-loop, UI, `data/digests`) не меняются — новый сервер, PDF-модуль и e2e
добавлены поверх; `saveToFile` кладёт результат в новый runtime-каталог
`data/pipeline/`.

## ADDED Requirements

### Requirement: MCP-сервер Pipeline Tools (stdio)

Система SHALL предоставлять stdio MCP-сервер
`studio/mcp_servers/pipeline_tools.py` (протокол 2024-11-05,
newline-delimited JSON-RPC 2.0 по stdin/stdout, только stdlib, сеть не
требуется). Сервер SHALL поддерживать `initialize` (protocolVersion
`2024-11-05`), `tools/list` и `tools/call`; notification (без `id`) — без
ответа; неизвестный метод — JSON-RPC error `-32601`. Инструменты (описания —
RU, с «когда звать / когда нет»): `search(query)` (поиск по локальным JSON
дайджестов), `summarize(text, max_points=8)` (детерминированная экстрактивная
сводка, без LLM), `saveToFile(filename, content, format)` (сохранение
результата, `format` ∈ `md|txt|json|pdf`, дефолт `md`). Каталог источника
поиска `PIPELINE_SEARCH_DIR` (дефолт `<repo>/data/digests`), каталог вывода
`PIPELINE_OUT_DIR` (дефолт `<repo>/data/pipeline`), путь шрифта
`PIPELINE_FONT_PATH` — env читаются **на время вызова инструмента**. Ошибка в
любом инструменте SHALL стать результатом `{"error": "..."}` с
`isError: true`; процесс MUST NOT падать.

#### Scenario: Перечисление инструментов

- **WHEN** клиент вызывает `initialize` и `tools/list`
- **THEN** сервер отвечает protocolVersion `2024-11-05` и список из трёх инструментов: `search`, `summarize`, `saveToFile`

#### Scenario: Сбой — ошибка результата, процесс жив

- **WHEN** `tools/call` с аргументами, приводящими к ошибке (пустой `query`, недопустимый `format`, отсутствующий шрифт для `pdf`)
- **THEN** результат `{"error": "..."}` с `isError: true`; последующий `tools/list` отвечает (процесс не упал)

### Requirement: search — локальные JSON

Инструмент `search(query)` SHALL сканировать `*.json` в
`PIPELINE_SEARCH_DIR` (дефолт `<repo>/data/digests`, день 18) и вернуть
совпадения `query` (case-insensitive подстрока) по полям `title`+`summary`+
`snippet`+`city`, топ-20 по `generated_at` desc, в форме
`{query, count, matches:[{title, summary, city, generated_at, file, snippet?}]}`.
Нет файлов/совпадений — `count: 0`, `matches: []` (не error).

#### Scenario: Поиск по дайджестам

- **WHEN** в каталоге поиска есть JSON-дайджесты и вызывается `search("Самара")`
- **THEN** результат содержит `count > 0` и `matches` с полями `title`/`summary`/`city`/`generated_at`; каждое совпадение реально присутствует в исходном JSON

#### Scenario: Пустой результат

- **WHEN** `search("несуществующий_термин_12345")`
- **THEN** `count: 0`, `matches: []`, `isError: false`

### Requirement: summarize — детерминированная сводка (без LLM)

Инструмент `summarize(text, max_points=8)` SHALL возвращать
`{summary, points, input_chars, output_chars}`, где `points` — до
`max_points` предложений исходного текста, отобранных детерминированным
частотным (экстрактивным) алгоритмом, `summary` — непустая строка.
Вызов LLM/сети в сервере MUST NOT выполняться. Пустой `text` —
`{"error": ...}` + `isError: true`.

#### Scenario: Сводка текста

- **WHEN** `summarize` вызван на тексте из N предложений (N > max_points)
- **THEN** `points` ≤ max_points, каждое предложение — из исходного текста, `input_chars` = длина входа, `output_chars` > 0; повторный вызов → идентичный результат

#### Scenario: Пустой текст

- **WHEN** `summarize("")`
- **THEN** `{"error": ...}` + `isError: true`

### Requirement: saveToFile — атомарная, traversal-safe, 4 формата

Инструмент `saveToFile(filename, content, format)` SHALL сохранять `content`
в `PIPELINE_OUT_DIR/<sanitized-basename>` (каталог создаётся при
отсутствии) **атомарно** (tmp + `os.replace`). `filename` — только basename;
traversal (`../`) MUST NOT выходить за каталог вывода. `format`:
`md`/`txt` — текст, `json` — валидный JSON, `pdf` — `content` (строка) →
`pdf_writer.text_to_pdf` → байты PDF. Возврат `{path, size, format}`;
`path` — абсолютный, файл существует на диске.

#### Scenario: Сохранение md/json

- **WHEN** `saveToFile("out.md", "текст", "md")` и `saveToFile("out.json", "...", "json")`
- **THEN** оба файла существуют в каталоге вывода, результат `{path, size, format}`, размер > 0

#### Scenario: Traversal отклонён

- **WHEN** `saveToFile("../evil.md", "x", "md")`
- **THEN** файл пишется только как `evil.md` внутри каталога вывода (не выше); результат указывает путь внутри каталога

#### Scenario: PDF-файл

- **WHEN** `saveToFile("r.pdf", "Содержимое с кириллицей", "pdf")`
- **THEN** файл существует, начинается байтами `%PDF-1.4`, содержит `/ToUnicode`; результат `format: "pdf"`, `size > 0`

### Requirement: PDF-движок (stdlib, кириллица, детерминизм)

Система SHALL предоставлять модуль `studio/mcp_servers/pdf_writer.py`
(только stdlib) с `PdfError(Exception)`, `find_default_font() -> str|None`
(env `PIPELINE_FONT_PATH` — если задан, обязан существовать и быть `.ttf`;
иначе первый из `arial.ttf`/`segoeui.ttf`/`tahoma.ttf` в
`C:\Windows\Fonts`; иначе `None`) и `text_to_pdf(text, title="",
font_path=None) -> bytes`. PDF 1.4: A4 595×842, поля 72pt, 11pt body /
14pt title, межстрочный 1.45, перенос по ширинам `hmtx` (слово длиннее
строки — hard-break), мультистраничность; шрифт — Type0/`Identity-H` →
CIDFontType2 (`/CIDToGIDMap /Identity`, `/DW` + `/W`), FontDescriptor +
сырые байты TTF как `/FontFile2`, `/ToUnicode` CMap (`begincidrange`
GID→Unicode). Байты MUST NOT содержать дат/случайных ID: повторный вызов на
одинаковых входах → идентичные байты. `font_path` на несуществующий/битый
файл — `PdfError`.

#### Scenario: PDF со структурой

- **WHEN** `text_to_pdf("Привет, мир. Second line here.")` с найденным шрифтом
- **THEN** результат начинается `%PDF-1.4`, содержит `/FontFile2`, `/ToUnicode`, `/Identity-H`, `/CIDToGIDMap`; xref-сдвиги указывают на валидные объекты

#### Scenario: Детерминизм

- **WHEN** `text_to_pdf` вызывается дважды с одинаковыми аргументами
- **THEN** оба вызова возвращают идентичные байты

#### Scenario: Кириллица

- **WHEN** `text_to_pdf` с кириллическим текстом
- **THEN** все уникальные коды точек текста имеют ненулевой GID в `cmap` шрифта; `/ToUnicode` покрывает использованный диапазон

#### Scenario: Битый шрифт

- **WHEN** `text_to_pdf("x", font_path=<несуществующий или не-TTF путь>)`
- **THEN** выброшено `PdfError`

### Requirement: Дефолт реестра (пятый)

`MCPRegistry` SHALL включать **Pipeline Tools** в дефолтные серверы
(рядом с Firecrawl, Git, Task Manager, News & Weather) с
`command = [sys.executable, <repo>/studio/mcp_servers/pipeline_tools.py]`
(stdio, без npx, `enabled: true`). Порядок дефолтов: Firecrawl, Git, Task
Manager, News & Weather, Pipeline Tools.

#### Scenario: Дефолт запускается локальным python

- **WHEN** создаётся новый `MCPRegistry` и рассматривается дефолт Pipeline Tools
- **THEN** `command[0]` — python (`sys.executable`), `command[1]` оканчивается `pipeline_tools.py` и файл существует; `connect` → status `connected`, `tools_count` = 3

### Requirement: Tool-loop дня 17 подхватывает сервер

Подключённый `pipeline_tools` SHALL отдавать свои 3 инструмента в
LLM-payload через существующий `_llm_tools` (без изменений логики tool-loop
дня 17). Модель решает порядок и число вызовов самостоятельно (композиция
на модели). Без подключённых серверов поле `tools` в тело LLM-запроса MUST
NOT попадать (регресс дней 16/17).

#### Scenario: Инструменты в payload

- **WHEN** сервер `pipeline_tools` подключён и выполняется `/api/chat`
- **THEN** в body LLM-запроса `tools` содержит `search`, `summarize`, `saveToFile`

#### Scenario: Без серверов — без tools (регресс)

- **WHEN** `/api/chat` без подключённых MCP-серверов
- **THEN** тело LLM-запроса не содержит поля `tools`

### Requirement: Проверка (тесты и e2e)

Репозиторий SHALL содержать офлайн-тесты (pytest, без сети):
`pdf_writer` (структура PDF, xref, мультистраницы, детерминизм, кириллица,
`PdfError`), `pipeline_tools` на реальном subprocess (3 tools, `search`
локально, `summarize` детерминированность, `saveToFile` 4 формата +
traversal + атомарность, error-ветки, `-32601`), пятый дефолт реестра.
`scripts/e2e_day19.py` (stdlib, порт 8103) SHALL быть гибридом: **Part A** —
детерминированное ядро офлайн (`MCPRegistry` + реальный subprocess
`pipeline_tools.py` + `StudioAgent`/`MockTransport` fake-LLM со
скриптованной цепочкой `search → summarize → saveToFile`; assert на порядок
tool-сообщений, **передачу данных** (выход этапа N ⊂ вход N+1) и PDF на
диске) — MUST PASS; **Part B** — live (uvicorn :8103, реальный LLM, запрос
«найди, суммаризируй, сохрани в PDF»), best-effort — PASS (≥1 tool-сообщение
и `pipeline_report.pdf` на диске) или SKIP (поведение модели); cleanup
всегда; exit 0 для PASS/SKIP, 1 для FAIL.

#### Scenario: E2E прогон

- **WHEN** запущен `python scripts/e2e_day19.py`
- **THEN** Part A — все assert PASS (офлайн, детерминированно); Part B — PASS или SKIP; exit 0

#### Scenario: Передача данных между инструментами

- **WHEN** в Part A модель вызывает `search → summarize → saveToFile`
- **THEN** аргумент `summarize.text` содержит данные из результата `search`, аргумент `saveToFile.content` содержит `summary` из результата `summarize` (assert на вложенность)
