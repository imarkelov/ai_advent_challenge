# Демо day35 — QA-Lead Orchestrator

## Что показано

1. Запуск пайплайна: `POST /api/runs` → SSE-трейс стадий.
2. SDD на каждого субагента (6 файлов) в `studio2/doc/runs/<run_id>/sdd/`.
3. Гейт каркаса: `skeleton.md` создан; апрув и выбор исполнения — отдельные эндпоинты.
4. Генерация теста: `test_generated/GeneratedTC4217Test.kt`.
5. Финал: `status=done`, `verdict=green`.

## Live-проверка на Ollama

- `POST /api/models/test` для `reviewer` (`gemma4:12b`) → `ok: true`.
- Исследователь на живой модели (`qwen3.5:9b`) вернул осмысленный `need_question` —
  гейт человека отработал, пайплайн заблокировался до ответа.
- Найден и исправлен дефект reasoning-моделей: ответ лежит в `reasoning`, а
  `content` пуст — добавлен fallback.

## Команды

```bash
cd studio2/backend && python -m uvicorn main:app --port 8100
cd studio2/frontend && npm run dev          # http://localhost:5174
python studio2/scripts/e2e_day35.py         # 13 PASS, 0 FAIL
```

## Метрики

- `pytest -q` — 170 тестов.
- `npm test` — 34 теста, `tsc -b` и `vite build` чистые.
- `e2e_day35.py` — 13 PASS, 0 FAIL.
