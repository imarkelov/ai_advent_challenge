---
role: protocol
display_name: Протокол оркестрации
model: none
temperature: 0
stage: none
allowed_tools: []
inputs: []
outputs: []
max_rounds: 0
on_no_info: none
---

# Протокол оркестрации QA-Lead

## Роли и порядок

1. `qa-lead` — генерирует SDD на каждого субагента и ведёт стадии.
2. `researcher` — изучает проект через git и RAG, ведёт `doc/kb/*`.
3. `testwriter` — сначала каркас, после апрува — Kotlin-тест.
4. `debugger` — разбирает падения прогона и правит только тест.
5. `reviewer` — отдельная LLM, проверяет результаты загруженных и MCP-тестов.

## Обмен

Каждый субагент отвечает JSON-объектом:

```json
{"status": "done|need_question|fail", "artifacts": [], "summary": "", "question": null}
```

## Лимиты

- `max_rounds = 2`: study round 1 и round 2.
- Инструменты вне `allowed_tools` запрещены.
- Субагент пишет только в свой `write_scope`.

## Гейты

- `skeleton → write` только после апрува каркаса человеком.
- `write → provision` только после выбора способа исполнения.
- Вопрос человеку блокирует пайплайн до ответа или таймаута 30 минут.
