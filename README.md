# AI Advent Challenge

Репозиторий для обучения работе с AI.

Код каждого дня — в отдельной ветке:

| День | Ветка | Задание |
| ---- | ----- | ------- |
| День 1 | [`day1`](https://github.com/imarkelov/ai_advent_challenge/tree/day1) | Запросы к LLM через API + чат-интерфейс (CLI и Web) |
| День 2 | [`day2`](https://github.com/imarkelov/ai_advent_challenge/tree/day2) | Контроль ответа LLM |
| День 3 | [`day3`](https://github.com/imarkelov/ai_advent_challenge/tree/day3) | Разные способы рассуждения |
| День 4 | [`day4`](https://github.com/imarkelov/ai_advent_challenge/tree/day4) | Температура |
| День 5 | [`day5`](https://github.com/imarkelov/ai_advent_challenge/tree/day5) | Версии моделей |
| День 6 | [`day6`](https://github.com/imarkelov/ai_advent_challenge/tree/day6) | Простой AI-агент |
| День 7 | [day7](https://github.com/imarkelov/ai_advent_challenge/tree/day7) | Сохранение контекста |
| День 8 | [`day8`](https://github.com/imarkelov/ai_advent_challenge/tree/day8) | Подсчёт токенов |
| День 9 | [day9](https://github.com/imarkelov/ai_advent_challenge/tree/day9) | Управление контекстом |
| День 11 (студия) | [`day11-studio`](https://github.com/imarkelov/ai_advent_challenge/tree/day11-studio) | Студия |
| День 12 | [`day12-user-profile`](https://github.com/imarkelov/ai_advent_challenge/tree/day12-user-profile) | Персонализация |
| День 13b | [`day13-task-state-machine`](https://github.com/imarkelov/ai_advent_challenge/tree/day13-task-state-machine) | Задача как запрос в режиме «задача» |
| День 14 | [`day14-invariants`](https://github.com/imarkelov/ai_advent_challenge/tree/day14-invariants) | Инварианты |
| День 15 | [`day15-plan-review`](https://github.com/imarkelov/ai_advent_challenge/tree/day15-plan-review) | Проверка плана |
| День 16 | [`day16-mcp-connect`](https://github.com/imarkelov/ai_advent_challenge/tree/day16-mcp-connect) | Подключение MCP |
| День 17 | [`day17-mcp-tool-loop`](https://github.com/imarkelov/ai_advent_challenge/tree/day17-mcp-tool-loop) | MCP Tool-Loop, управляемый LLM |
| День 18 | [`day18-mcp-digest`](https://github.com/imarkelov/ai_advent_challenge/tree/day18-mcp-digest) | Периодический дайджест 24/7 |
| День 19 | [`day19-mcp-pipeline`](https://github.com/imarkelov/ai_advent_challenge/tree/day19-mcp-pipeline) | Композиция MCP-инструментов |
| День 20 | [`day20-mcp-orchestration`](https://github.com/imarkelov/ai_advent_challenge/tree/day20-mcp-orchestration) | Orchestration MCP |
| День 21 | [day21-doc-indexing](https://github.com/imarkelov/ai_advent_challenge/tree/day21-doc-indexing) | База знаний |
| День 22 | [day22-rag-query](https://github.com/imarkelov/ai_advent_challenge/tree/day22-rag-query) | Первый RAG-запрос |
| День 23 | [day23-rerank-filter](https://github.com/imarkelov/ai_advent_challenge/tree/day23-rerank-filter) | Реранкинг и фильтрация |
| День 24 | [day24-rag-citations](https://github.com/imarkelov/ai_advent_challenge/tree/day24-rag-citations) | Цитаты |
| День 25 | [`day25-task-memory`](https://github.com/imarkelov/ai_advent_challenge/tree/day25-task-memory) | Мини-чат с RAG и памятью  |
| День 26 | [`day26-local-llm`](https://github.com/imarkelov/ai_advent_challenge/tree/day26-local-llm) | Локальная LLM (Ollama) + CLI-харнес myharness |
| День 33 | [day33-support-assistant](https://github.com/imarkelov/ai_advent_challenge/tree/day33-support-assistant) | Ассистент поддержки |
| День 34 | [day34-files-assistant](https://github.com/imarkelov/ai_advent_challenge/tree/day34-files-assistant) | Файловый ассистент |


## День 34: ассистент для работы с файлами проекта

CLI-ассистент (`python -m devassist`): задача ставится по цели — он сам ищет по нескольким файлам, читает, анализирует, создаёт и меняет файлы, показывает diff.

- **Инструменты** (MCP, stdio, только stdlib): `read_file`, `search_files`, `write_file` (бэкап + атомарная запись), `diff_files`.
- **Режимы:** LLM tool-loop (модель сама выбирает инструменты) и `--offline` (детерминированный план — воспроизводимо без сети).
- **Безопасность:** песочница workspace (`.env`, `.git`, `.omo` закрыты), бэкапы прогона в `.devassist/backup/<run_id>/`.
- **Сценарии:** где используется символ, документация по коду, changelog, проверка правил проекта, diff/список изменений.

```bash
python -m devassist "найди все места, где используется CRMClient"
python -m devassist --offline --demo
python scripts/e2e_day34.py     # офлайн-ядро + live
```

Ветка `day34-files-assistant` (от `day33-support-assistant`).
