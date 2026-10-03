# Tasks: day23-rerank-filter

Свой список задач; согласован с планом дня
(`.omo/plans/day23-rerank-filter.md`) по разбивке, нумерация своя.

## Бэкенд: ядро фильтра
- [ ] Task 1: baseline-замеры + ветка `day23-rerank-filter` (от `day22-ui-rework`): pytest backend (ожидалось 548), npm test frontend (факт), числа в черновик доков; код не трогать, ветку не пушить

## Бэкенд: `min_score` (TDD)
- [ ] Task 2: `kb.py` — `"min_score": 0.0` в `DEFAULT_SETTINGS`; валидация в `update_settings` (bool-гвард ПЕРВЫМ, затем int/float + диапазон 0..1, RU-detail, сохранение float); `search_rag(min_score=0.0)`: при `> 0` фильтр после stage1 (+rerank) и до `[:top_k]` — reranked → `rerank_score >= min_score`, не reranked → `score >= min_score * best` (best==0 → `[]`); аддитивные поля ответа `filtered`/`dropped`; pass-through в 3 потребителя (`_rag_retrieve`, `/api/kb/search`, сигнатура `rag_compare`); тесты `test_kb.py`/`test_kb_api.py`: валидация (0.5→200; 1.5/-0.1/"0.5"/true→400; 0 и 1→200), default GET 0.0, fake-reranker [0.99, 0.5, 0.1]: 0.6→1 результат + dropped=2, 0.999→0, 0.0→снимок-сравнение идентичности, relative-режим (лучший проходит 0.9999), best==0 guard, `>=` при 1.0; BM25/RRF/embedder-код не меняется

## Бэкенд: `rewrite_query` (TDD)
- [ ] Task 3: `agent.py` — `rewrite_query(question) -> (str, bool)`: один `_task_llm_call` T=0/max_tokens=200, RU-system-промпт (одним предложением, ключевыми словами); `(rewritten, True)` только при непустом и не-идентичном (case-insensitive) ответе; пустой/identical/исключение → `(question, False)` + log warning (НЕ «Ошибка:»); НЕ вызывается из `ask_stream`; тесты `test_agent.py` (fake-LLM: scripted → (rewrite, True) + spy T=0/200; ""/whitespace/identical → (question, False); raise → (question, False), исключение наружу не пробрасывается; guardrail: rewrite в ask_stream не вызывается)

## OpenSpec
- [ ] Task 4: openspec change `day23-rerank-filter` — proposal.md / design.md / tasks.md / specs/rag-filter-rewrite/spec.md (структура day22-rag-query)

## Бэкенд: compare 4 армы + body-override (TDD)
- [ ] Task 5: `agent.rag_compare` — 4 армы (plain/rag без изменений; rag+filter с `min_score`; rag+rewrite: retrieval на `rewritten`, ответ на оригинале; per-arm failure «Ошибка: …»; rewrite-sбой → `rewrite_applied: false`, арма не «Ошибка:»); аддитивные поля ответа (`answer_rag_filter`, `answer_rag_rewrite`, `chunks_rag_filter`, `chunks_rag_rewrite`, `rag_context_*`, `rewritten_query`, `rewrite_applied`); `main.py`: `POST /api/rag/compare` body `{question, min_score?}` (default = settings, валидация как settings → 400 RU), `/api/kb/settings` возвращает `min_score`, `/api/kb/search` передаёт `min_score` в `search_rag`; тесты `test_rag_compare.py`/`test_kb_api.py`: 4 армы + старые поля intact (снимок day22-формы), body 0.6 → chunks_rag_filter=1 / chunks=3, body true/"0.5"/1.5 → 400, всё отфильтровано (0.999) → 200 + `chunks_rag_filter == []` + непустой ответ, fake-LLM rewrite scripted/raise; `e2e_day22.py` Part A → 7/7 PASS (регрессия)

## Фронтенд
- [ ] Task 6: `api.ts` (`KbSettings.min_score`, опциональные compare-поля, body `{question, min_score?}`) + `KbTab.tsx`: input «Порог отсечения (0 = off)» 0..1 step 0.05 (паттерн patchSettings), 4 панели сравнения с чипами («фильтр ≥ X» у filter-панели; rewritten_query-чип у rewrite-панели), чип «фильтр ≥ X» в секции поиска, один спиннер, per-arm «Ошибка: …», undefined-safe; Vitest: persist min_score (spy POST), 4 панели, чипы при 0.5/0, rewrite-чип по `rewrite_applied`; `tsc -b` + build clean

## E2E и сравнение
- [x] Task 7: `scripts/e2e_day23.py` (stdlib, порт **8107**, паттерн e2e_day22): Part A MUST PASS офлайн (net cut, A1 settings-валидация, A2 фильтр fake-reranker, A3 relative-режим, A4 4 армы + rewrite scripted, A5 rewrite fallback, A6 всё отфильтровано, A7 регрессия day22-формы byte-совместима); Part B live best-effort (down/port-busy → SKIP); cleanup всегда; exit 0/1; повторный запуск green — **Part A 7/7 PASS, Part B live 11 PASS / 0 FAIL / 1 WARNING (B4 пасхалка — best-effort), повторный прогон green**
- [x] Task 8: `scripts/compare_day23.py` (паттерн compare_day22): 10 вопросов (переиспользование `control_questions.json` + 2 «отвлекающих») × 4 режима через `POST /api/rag/compare` (live :8107); факт-чек (hard) + LLM-judge (soft, T=0 strict JSON); отчёт `.omo/evidence/day23-compare/report.md` (таблица вопрос×режим, win-rate, вывод) **всегда** (try/finally); GPustack down → SKIP-метка, exit 0 — **12/12 вопросов, judge: tie=5, rag_wins=3, rag_filter_wins=2, plain_wins=2; rewrite_applied 12/12**

## Документация и релиз
- [x] Task 9: README (строка таблицы «День 23» + секция: что это, архитектура «stage1 → filter → top_k», семантика min_score on/off, 4 армы, API-таблица, E2E 8107, Проверка задания, Статус — АКТУАЛЬНЫЕ числа из логов Task 2/5/6/7) + RELEASE (секция сверху) + `docs/superpowers/specs/2026-10-03-day23-rerank-filter-design.md` + `docs/superpowers/plans/2026-10-03-day23-rerank-filter.md` + отметки в этом tasks.md — **готово: README (строка + секция «День 23» с бэкенд 579 / фронтенд 296 / e2e A 7/7 + B 11/0/1), RELEASE секция сверху с `---`, spec (10 разделов, Q1–Q8 из design.md) + plan (Implementation Plan, формат day22) написаны, чеки — `.omo/evidence/task-9-docs-check.txt`**
- [x] Task 10: демо-видео (скилл `studio-demo-video`: поле порога → поиск с чипом «фильтр ≥ 0.5» → 4 панели сравнения → rewrite-чип; webm → MP4, desktop вне репо; log-evidence: rerank-вызовы + filter-drop) — **готово: `day23_demo.mp4` (42.08s, 1440×900, h264, desktop «AI Advent Challenge - видео», НЕ в репо); демо-корпус = pushkin_oneygin.txt (258 чанков, structural + api-эмбеддер, dim 4096); в кадре: порог 0.5 + blur-коммит → чип «фильтр ≥ 0.5», поиск (0.880 из #39, 0.839 из #3), «Сравнить» → 4 панели + «С RAG + rewrite»; log-evidence — `.omo/evidence/task-10-demo-log.txt` (POST /api/kb/settings 200, GET /api/kb/search 200, POST /api/rag/compare 200)**

## Вне scope (guardrails)
- Нет 5-й армы (filter+rewrite), multi-query rewrite, auto-tuning порога; нет rewrite в live-чат (`ask_stream`); нет новых эндпоинтов/моделей/MCP-серверов/pip/npm-зависимостей; нет правок: chunking, embedder, RRF/BM25, APIReranker-internals, FlowInspector, per-dialog toggle, e2e_day21/22, compare_day22.py, control_questions.json; контракт compare — только аддитивные поля (e2e_day22 green); ветку не пушить до финала (push — после F1–F4); нет archive этого изменения и sync в main specs без запроса.
