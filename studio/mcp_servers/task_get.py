"""MCP-сервер "task_get" (день 20): один тул — get_task_details.

Чтение задачи из data/tasks.json (env TASKS_FILE). Не найдена —
isError "Задача не найдена: <id>" (текст дня 17). Только stdlib.
Запуск: python studio/mcp_servers/task_get.py
"""
import os
import sys

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)
import _tasks_store  # noqa: E402
from _mcp_base import run_server  # noqa: E402

TOOL = {
    "name": "get_task_details",
    "description": ("Детали задачи по id (файл data/tasks.json): id, "
                    "title, description, status, assignee."),
    "input_schema": {"type": "object", "properties": {
        "task_id": {"type": "string",
                    "description": "Идентификатор задачи (TASK-N)"}},
        "required": ["task_id"]},
}


def call(args: dict) -> tuple:
    tid = (args or {}).get("task_id")
    if not isinstance(tid, str) or not tid.strip():
        return {"error": "Не задан обязательный аргумент: task_id"}, True
    tid = tid.strip()
    task = _tasks_store.load()["tasks"].get(tid)
    if task is None:
        return {"error": "Задача не найдена: " + tid}, True
    return task, False


if __name__ == "__main__":
    run_server("task_get", TOOL, call)
