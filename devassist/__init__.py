"""Ассистент для работы с файлами проекта (день 34).

Модули:
    bridge.py      — MCP-мост файловых инструментов (stdio, 4 сервера);
    agent.py       — DevAgent: LLM tool-loop + детерминированный офлайн-план;
    guardrails.py  — проверка инвариантов по файлам проекта;
    cli.py         — REPL (python -m devassist).

Запуск: python -m devassist --demo
"""

__version__ = "1.0"
