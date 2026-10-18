# SDD — {role}

## Контекст

- Задача: {task_id} — {task_title}
- Репозиторий / ветка: {repo_path} @ {branch}
- Изменения в ветке: {git_summary}

## Цель

{goal}

## Вход

- RAG: {rag_inputs}
- Артефакты предыдущих ролей: {artifacts}

## Инструкции

{instructions}

## Ограничения

- allowed_tools: {allowed_tools}
- max_rounds: {max_rounds}
- write_scope: {write_scope}

## Формат ответа (JSON)

```json
{"status": "done|need_question|fail", "artifacts": [], "summary": "", "question": null}
```

## Критерии готовности

{criteria}
