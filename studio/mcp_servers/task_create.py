"""MCP-сервер "task_create" (день 20): один тул — create_task.

Файл-хранилище data/tasks.json (env TASKS_FILE); id = TASK-<n>,
n = max(числовые id) + 1; созданная задача — форма дня 17
(status "todo", assignee null). Только stdlib.
Запуск: python studio/mcp_servers/task_create.py
"""
import os
import sys

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)
import _tasks_store  # noqa: E402
from _mcp_base import run_server  # noqa: E402

TOOL = {
    "name": "create_task",
    "description": ("Создать задачу (файл data/tasks.json). Возвращает "
                    "задачу: id (TASK-N), title, description, status "
                    "'todo', assignee null."),
    "input_schema": {"type": "object", "properties": {
        "title": {"type": "string", "description": "Название задачи"},
        "description": {"type": "string",
                        "description": "Описание задачи"}},
        "required": ["title"]},
}


def call(args: dict) -> tuple:
    title = (args or {}).get("title")
    if not isinstance(title, str) or not title.strip():
        return {"error": "Не задан обязательный аргумент: title"}, True
    data = _tasks_store.load()
    tid = _tasks_store.next_id(data)
    task = {"id": tid, "title": title.strip(),
            "description": (args or {}).get("description") or "",
            "status": "todo", "assignee": None}
    data["tasks"][tid] = task
    _tasks_store.save(data)
    return task, False


if __name__ == "__main__":
    run_server("task_create", TOOL, call)
