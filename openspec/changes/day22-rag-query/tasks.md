# Tasks: day22-rag-query

Свой список задач; согласован с планом дня
(`.omo/plans/day22-rag-query.md`) по разбивке, нумерация своя.

## Корпус и фикстуры
- [ ] Task 1: `scripts/fetch_books.py` (stdlib urllib: 3 pinned-классики — Пушкин «Евгений Онегин», Чехов «Вишнёвый сад», Толстой «Война и мир»; literature.lib.ru → fallback Wikisource ru; fallback chain Гоголь «Мёртвые души» / Чехов «Дамы с собачкой»; все упали → seed-copy; UTF-8 → cp1251; size sanity > 50KB; атомарная запись; exit 0 + JSON-сводка) + committed `studio/backend/tests/fixtures/seed_corpus/` (2 текста Чехова) + `tests/test_fetch_books.py` (patched urlopen, tmp_path)
- [ ] Task 2: фикстуры — `studio/backend/tests/fixtures/control_questions.json` (ровно 10: `id` 1..10, `question`, `expect_facts[]` — конкретные факты текста (вычитываются после скачивания книг; книга не скачалась → вопросы по fallback), `expected_sources[]` — точные имена загружаемых файлов; Q1–Q7 — классики, Q8–Q10 — egg_book) + `egg_book.txt` (детерминированная «книга»: ксилофон, IPhone 17Promax, имя/профессия/город) + `tests/test_fixtures.py` (схема: ровно 10, id уникальны 1..10, `expect_facts` ≥1, `expected_sources` ≥1 из белого списка имён)

## Эндпоинт (TDD)
- [ ] Task 3: `studio/backend/tests/test_rag_compare.py` (красные тесты: shape 200 (answer_plain/answer_rag/kb_block/chunks/rag_context), plain без «База знаний», rag с «База знаний» + текст фикстуры, T=0/max_tokens=1024 в обоих payload, bare system prompt (нет «Профиль»/«Память»/«Инварианты»), игнор `settings["rag"]`, 400 пустой вопрос, 404 без индекса, изоляция ошибки одной руки) → `agent.rag_compare(question)` (`kb.search_rag` из settings: recall/top_k/reranker; флаг `rag` не consulted; `_render_kb_block`; 2×`_task_llm_call` T=0/max_tokens=1024; try/except → `error`) → маршрут `POST /api/rag/compare` в `main.py` (400/404 RU-detail) — зелёные тесты, 0 регрессий

## UI
- [ ] Task 4: секция «Сравнение RAG» в `KbTab.tsx` (секция, не новый таб): вопрос (textarea) + «Сравнить» → две панели рядом «Без RAG» / «С RAG» (monospace, pre-wrap; на narrow — колонкой) + список чанков (`file · section`, score/rerank_score) + `kb_block` в `<details>`; ошибки 400/404/500 → RU-сообщение из detail; `api.ts`: `RagCompareResult` + `apiRagCompare`; стили в `styles.css`; vitest-тесты (mock `apiRagCompare`: рендер двух панелей, 404 → error-текст)

## Сравнение и e2e
- [ ] Task 5: `scripts/compare_day22.py` — uvicorn :8106 (port-busy wait 10 мин → SKIP; probe GPustack down → SKIP, exit 0); wipe → fetch → upload книг + egg_book → index (api-эмбеддер, poll build-status 30 мин) → settings явно `{embedder: api, reranker: api, rag_recall: 50, rag_top_k: 3}`; 10×`POST /api/rag/compare` (ошибка вопроса → `error` в записи, продолжаем; источник не в корпусе → `skipped`, не фейл); оценка: факт-чек (нормализованная подстрока, hard) + `sources_ok` (hard) + LLM-judge (T=0, strict JSON `{score, verdict, reason}`, judge down/битый JSON → null; soft — никогда не фейлит); отчёт `.omo/evidence/day22-rag-compare/{compare.json, report.md}` **всегда** (try/finally): settings-эхо, 10 записей, summary, честный итог (RAG может проиграть — ок); exit 0 (PASS/SKIP), 1 (infra FAIL); cleanup uvicorn всегда
- [ ] Task 6: `scripts/e2e_day22.py` (stdlib, порт **8106**, лог `[e2e-day22]`): Part A — MUST PASS офлайн (net cut `_NO_NET`, TestClient + fake-LLM, только committed фикстуры: upload egg_book → index HashEmbedder; C1 shape, C2 KB-блок есть/нет в payload, C3 400/404, C4 `rag=false` → обе руки, C5 схема control_questions, C6 T=0/max_tokens=1024, C7 bare prompt); Part B — live best-effort (uvicorn :8106; probe GPustack down / port-busy → SKIP; B1 corpus rebuild, B2 settings, B3 один live-запрос, B4 easter-egg факт — WARNING не FAIL); exit 0 для PASS/SKIP, 1 для FAIL; cleanup всегда

## Документация и релиз
- [ ] Task 7: README (строка таблицы «День 22» + секция «День 22» по структуре day21: Что это / Архитектура / API / UI / E2E / Проверка задания / Статус) + RELEASE (новая секция сверху; API; Проверка задания — реальные числа, замерить на релизе; Безопасность) + отметки в этом tasks.md (паттерн `/opsx:apply`)
- [ ] Task 8: демо-видео (скилл `studio-demo-video`: секция «Сравнение RAG» → вопрос → два ответа рядом; `day22_demo.mp4` на desktop, не в репо; log-evidence цитаты → RELEASE) + финальный прогон (критерий закрытия дня: pytest backend, tsc + vitest + build, e2e_day22, compare_day22)

## Вне scope (guardrails)
- Нет правок `kb.py` / `ask_stream` / `GOLD_QUERIES` / `scripts/e2e_day21.py`; нет новых ключей settings, `.env`-переменных, LLM-моделей, MCP-серверов, pip/npm-зависимостей; нет нового UI-таба; нет HTML-отчёта; нет «RAG должен победить»-ассертов; нет archive этого изменения (прецедент дня 21) и sync в main specs без запроса.
