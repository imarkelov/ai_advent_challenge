# Tasks: day19-mcp-pipeline

## Исследование
- [x] Изучить `studio/mcp_servers/news_weather.py` (stdio MCP-паттерн) и `task_manager.py`
- [x] Изучить `studio/backend/mcp.py::_default_servers` (4 дефолта) и `_llm_tools` (день 17)
- [x] Изучить `agent.py` tool-loop (лимит 5 итераций) — композиция на модели
- [x] Изучить `data/digests/*.json` (формат для `search`), конвенцию openspec (день 18)

## pdf_writer.py (stdlib)
- [x] `PdfError`, `find_default_font()` (env → Windows Fonts → None)
- [x] TTF-парсер `struct`: head/hhea/maxp/hmtx/cmap(4+12)/name
- [x] `text_to_pdf`: A4, 11/14pt, межстрочный 1.45, перенос по hmtx, мультистраницы
- [x] Type0/Identity-H → CIDFontType2, `/FontFile2`, `/CIDToGIDMap /Identity`, `/ToUnicode`
- [x] Детерминизм (без дат) → идентичные байты
- [x] `PdfError` на битый/отсутствующий шрифт

## pipeline_tools.py (stdio MCP, 3 tools)
- [x] JSON-RPC 2024-11-05 (initialize/tools/list/tools/call, -32601, notification)
- [x] `search(query)` — локальные `data/digests/*.json`, case-insensitive, топ-20
- [x] `summarize(text, max_points=8)` — экстрактивный, без LLM
- [x] `saveToFile(filename, content, format=md|txt|json|pdf)` — атомарная, basename, traversal-safe
- [x] Env: PIPELINE_SEARCH_DIR / PIPELINE_OUT_DIR / PIPELINE_FONT_PATH
- [x] Ошибка → `{"error": ...}` + `isError: true`, процесс жив

## Интеграция
- [x] Пятый дефолт `Pipeline Tools` в `mcp.py::_default_servers`
- [x] Правка 3 тестов-списков-дефолтов (5-е имя)

## Тесты (офлайн)
- [x] `test_pdf_writer.py` (7): структура, xref, мультистраницы, детерминизм, кириллица, PdfError
- [x] `test_pipeline_tools.py` (12): subprocess-клиент, 3 tools, error-ветки, traversal, 5-й дефолт
- [x] Полный прогон: 369 pass, 2 pre-existing network-fail (без новых падений)

## E2E
- [x] `scripts/e2e_day19.py` (stdlib, :8103)
- [x] Part A: MCPRegistry + subprocess + fake-LLM цепочка, assert на передачу данных, PDF — MUST PASS
- [x] Part B: live uvicorn + реальный LLM, best-effort (PASS/PDF или SKIP)
- [x] Финальный прогон: **18 PASS, 0 FAIL, 1 SKIP, exit 0** (Part A 12/12; Part B SKIP = live PDF-артефакт)

## Документация
- [x] openspec change (yaml/proposal/design/tasks/spec)
- [ ] README: строка Day 19 в таблице + секция «День 19»
- [ ] RELEASE.md: блок дня 19 наверх
