# Ссылки — день 32 (автоматическое AI-ревью PR)

## Ветка и PR

- GitHub: <https://github.com/imarkelov/ai_advent_challenge/tree/day32-pr-review>
- Базовая ветка: `day31-cli-assistant`
- Pull request: ветка запушена (`1b9509c`), PR не создавался (по просьбе).

## Как запустить

1. **Настройки модели** (один раз) — Settings → Secrets and variables →
   Actions:
   - variable `LLM_BASE_URL` — OpenAI-совместимый адрес вашей Ollama
     (`http://<host>:11434/v1`) или `run-model.sh serve` (`http://<host>:8080/v1`);
   - variable `LLM_MODEL` — имя модели (`qwen3:8b`, `abl27`);
   - secret `LLM_API_KEY` (`ollama` для Ollama; секрет обязателен —
     секреты добавятся в список, только если он есть);
   - публикуемый адрес должен быть доступен из GitHub-раннеров (local
     `localhost` — нет; нужен туннель/VPS).
   Без ключа пайплайн всё равно отработает — детерминированным отчётом.
2. **Запустить на PR** — открыть PR и нажать «Create pull request»:
   <https://github.com/imarkelov/ai_advent_challenge/compare/day31-cli-assistant...day32-pr-review?expand=1>
3. **Где смотреть** — Actions → «AI PR Review»:
   <https://github.com/imarkelov/ai_advent_challenge/actions/workflows/ai-review.yml>
   (комментарий появится в самом PR, сводка — в Summary запуска,
   отчёт — в артефакте `ai-pr-review`).
4. **Ручной запуск без PR** (workflow_dispatch, ревью последнего
   коммита выбранной ветки):
   <https://github.com/imarkelov/ai_advent_challenge/actions/workflows/ai-review.yml> →
   «Run workflow» → ветка `day32-pr-review`.

## Демо-видео (день 32: AI-ревью PR)

- Яндекс Диск: _— (заполнить после загрузки)_
- Локальная копия: `day32_ai_pr_review.mp4` (1 мин 57 с, 1600×1000,
  ~1.6 МБ, H.264 + голос) — папка `ai_advent_day32_video` на рабочем
  столе. Рядом — `real_output.json`: фактический вывод прогона, из
  которого собраны кадры.
- Сценарий (все кадры — **реальный вывод** пайплайна, не картинки):
  `git log`/`git diff --name-status` ветки PR → `pr-review check`
  (индекса ещё нет) → `index` (238 документов, 4 540 чанков) →
  `check` повторно (`✅ RAG: индекс проекта`) → `pr-review run --base
  main` на живой Ollama → что именно получила модель (промпт, RAG-фрагменты
  с пометкой «← какой файл», находки) → три блока отчёта (баги,
  архитектура, рекомендации) → аннотации `::error`/`::warning` и
  комментарий в PR → `scripts/e2e_day32.py` (PASS 26) и тесты.

## Воспроизвести

```bash
# локальный прогон пайплайна (как в Action)
.venv/bin/python studio/backend/cli.py pr-review check
.venv/bin/python studio/backend/cli.py index
.venv/bin/python studio/backend/cli.py pr-review run --base origin/master --out review.md

# перезаписать видео (нужны: ffmpeg или .venv/bin/pip install imageio-ffmpeg pillow,
# macOS `say` с голосом Milena)
DAY32_LLM_ENV_JSON='{"LLM_MODEL":"qwen3:8b","LLM_BACKEND":"ollama"}' \
  .venv/bin/python scripts/make_day32_video.py \
    --script scripts/day32_video_script.json --out day32_ai_pr_review.mp4
```

---

# Ссылки — день 20 (Orchestration MCP)

## Ветка

- GitHub: <https://github.com/imarkelov/ai_advent_challenge/tree/day20-mcp-orchestration>
- Базовая ветка: `day19-mcp-pipeline`

## Демо-видео (день 20: кросс-серверный флоу + бейджи «server · tool»)

- Яндекс Диск: _— (заполнить после загрузки)_
- Локальная копия: `day20-mcp-orchestration-demo.mp4` (38.8 с, 887 КБ) —
  папка «AI Advent Challenge - видео» на рабочем столе. Сценарий:
  подключение 3 едицельных серверов (`digest_search`/`digest_summarize`/
  `file_save`) в панели MCP → «найди в дайджестах про Самара,
  суммаризируй, сохрани в PDF» → блок «Шаги агента» с бейджами
  `digest_search · search`, `digest_summarize · summarize` (split по
  первому `__`, полное имя на hover) → вкладка «Запрос» (payload с
  `{server}__{tool}`-тулами).
