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
