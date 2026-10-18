"""Ассистент поддержки (день 33): RAG по FAQ/документации + контекст тикета через MCP.

Модули:
    retrieval.py  — гибридный поиск по базе знаний (BM25 полей + хэш-вектор,
                    RRF, порог, честное «не знаю»);
    crm.py        — MCP-мост к CRM (stdio-серверы tools/mcp_servers/crm_*);
    llm.py        — клиент OpenAI-совместимого /chat/completions (tools, stream);
    session.py    — ход «вопрос -> ответ по тикету», источники, next_action;
    cli.py        — REPL (python -m support).

Запуск: python -m support --ticket TCK-4101
"""

__version__ = "1.0"
