# Day 19 — Композиция MCP-инструментов

Дата: 2026-09-24 · Ветка: `day19-mcp-pipeline` · Порт e2e: 8103

## Задание

Составить несколько MCP-инструментов в пайплайн: первый получает данные
(`search`), второй обрабатывает (`summarize`), третий сохраняет результат
(`saveToFile`). Проверить автоматическое выполнение цепочки и корректность
передачи данных между инструментами.

## Ключевое решение: LLM-driven композиция

Оркестратора в коде нет. Цепочка **динамическая**: модель видит все
инструменты всех подключённых MCP-серверов (день 16, реестр мержит tools)
и для каждого запроса пользователя сама решает, какие инструменты
вызвать, в каком порядке и сколько — через tool-loop дня 17
(`agent.py:514-688`, лимит 5 итераций). Пайплайн из 3 инструментов =
4 итерации — в лимит влезает.

| Запрос пользователя | Цепочка, которую соберёт модель |
|---|---|
| «какая погода?» | `get_weather` (News & Weather) — 1 tool |
| «найди новости про X, сохрани» | `search` → `saveToFile` (summarize пропущен) |
| «прогноз 3 дней в PDF» | `search` → `saveToFile(format=pdf)` |
| «найди, суммаризируй, сохрани» | `search` → `summarize` → `saveToFile` |
| «погода + новости, резюме в файл» | микс серверов: `get_weather` + `search` + `summarize` + `saveToFile` |

Инструменты не знают друг о друге — композиция целиком на стороне модели.
Модель сама выбирает инструмент, сама его вызывает, результат выполнения
выводит в ответ пользователю.

## Архитектура

```
Пользователь: «найди про погоду, суммаризируй, сохрани в PDF»
        │
        ▼
StudioAgent.ask_stream (tool-loop, день 17)
        │  iteration 1: LLM → tool_calls [search]
        │  MCPRegistry.call_tool → Pipeline Tools (stdio subprocess)
        │  iteration 2: LLM видит result (role:"tool") → [summarize]
        │  iteration 3: LLM видит result → [saveToFile format=pdf]
        │  iteration 4: финальный ответ (путь к файлу, что нашлось)
        ▼
data/pipeline/*.pdf — валидный PDF с кириллицей
```

### Новый stdio-сервер `studio/mcp_servers/pipeline_tools.py`

Паттерн `news_weather.py` (stdlib, JSON-RPC 2024-11-05, newline-delimited):
`TOOLS` + `CALL_HANDLERS` + `main()`. 5-й дефолт в
`MCPRegistry._default_servers()` (`backend/mcp.py`).

| Инструмент | Выход | Ошибки |
|---|---|---|
| `search(query)` | `{"query","count","matches":[{title,summary,city,generated_at,file}]}` по дайджестам дня 18 (`data/digests/*.json`, env `PIPELINE_SEARCH_DIR`), case-insensitive, топ-20 по generated_at desc | нет совпадений / пустая директория → isError |
| `summarize(text, max_points=8)` | `{"summary","points","input_chars","output_chars"}` — детерминированная экстрактивная (частотная оценка предложений, без LLM — сервер офлайн) | пустой текст → isError |
| `saveToFile(filename, content, format=md\|txt\|json\|pdf)` | `{"path","size","format"}` — атомарная запись (tmp + os.replace) в `data/pipeline/` (env `PIPELINE_OUT_DIR`); pdf → `pdf_writer.text_to_pdf` | PdfError → isError; filename: только basename, санитизация, traversal не проходит |

### PDF-движок `studio/mcp_servers/pdf_writer.py` (stdlib, ~400 строк)

- **TTF-парсер**: таблицы `head`, `hhea`, `hmtx` (ширины), `maxp`, `cmap`
  (format 4 + 12), `name`. **Без парсинга glyf** — весь шрифт целиком
  вставляется как `/FontFile2` в Type0/CIDFontType2 (Identity-H), вьюер
  сам рендерит глифы. Сабсеттинг не нужен (шрифт ~1 МБ).
- **PDF-билдер**: A4 595×842, поля 72pt, 11pt, перенос строк по hmtx-ширинам,
  мультистраничность; `/ToUnicode` CMap (GID→Unicode, иначе кириллица не
  копается); текст = hex-стримы UTF-16BE GID; валидный xref; детерминированные
  байты (без дат).
- Шрифт: `PIPELINE_FONT_PATH` (env) → `C:\Windows\Fonts\arial.ttf` →
  `segoeui.ttf` → `tahoma.ttf`; не найден → `PdfError`.
- API: `PdfError`, `find_default_font() -> str|None`,
  `text_to_pdf(text, title="", font_path=None) -> bytes`.

## Файлы

| Файл | Роль |
|---|---|
| `studio/mcp_servers/pdf_writer.py` | новый: TTF-парсер + PDF-билдер |
| `studio/mcp_servers/pipeline_tools.py` | новый: stdio-сервер, 3 инструмента |
| `studio/backend/mcp.py` | 5-й дефолт в `_default_servers()` |
| `studio/backend/tests/test_pdf_writer.py` | новый: структура PDF, xref, кириллица, детерминизм, PdfError |
| `studio/backend/tests/test_pipeline_tools.py` | новый: subprocess-клиент, 3 tool, error-кейсы, traversal, 5-й дефолт реестра |
| `scripts/e2e_day19.py` | новый: Part A + Part B |
| `README.md`, `RELEASE.md` | таблица/секция дня; блок сверху |
| `openspec/changes/day19-mcp-pipeline/` | proposal, design, tasks, spec |

## Проверка

**Part A e2e (детерминированный, MUST PASS, офлайн):**
1. `MCPRegistry` + реальный subprocess-сервер, 3 tool в `tools/list`.
2. Seed: 3 дайджест-JSON в tmp (schema collector.py, кириллица).
3. Прямые `call_tool`: search/summarize/saveToFile — sanity.
4. **Доказательство цепочки**: `StudioAgent` с fake-LLM (`httpx.MockTransport`,
   паттерн e2e_day17) — скриптованные 3 `tool_calls`, стадия выбирается
   по счётчику `role:"tool"` сообщений в payload. Assert:
   - 3 tool-сообщения в порядке search → summarize → saveToFile;
   - **выход этапа N присутствует в аргументах этапа N+1** (передача данных);
   - `pipeline_report.pdf` на диске: `%PDF-1.4` + `/ToUnicode`;
   - финальный `done`, ответ не пустой.

**Part B e2e (live, best-effort, порт 8103, SKIP допустим):**
uvicorn + реальный LLM (GPustack), запрос «найди в дайджестах про погоду,
суммаризируй, сохрани в pipeline_report.pdf». PASS = tool-сообщения в диалоге
+ PDF на диске. Модель могла собрать цепочку иначе — assert факт, не порядок.
GPustack лежит → SKIP.

**PDF-валидация**: структурные assert в pytest + ручная проверка
pdf-reader (text extract, кириллица 1:1).

**pytest**: `python -m pytest studio/backend/tests -q` — без новых падений.

## Риск и фолбэк

TTF cmap edge cases (форматы 4/12). Если парсер не тянет системный шрифт —
фолбэк: PDF только ASCII, кириллица → `.md` с объяснением модели.
Цель — полный PDF (user-решение, day 19).
