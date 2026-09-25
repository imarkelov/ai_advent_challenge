"""File-backed хранилище задач (день 20): data/tasks.json.

Общий для task_create и task_get (разные процессы — память не общая,
в отличие от in-memory дня 17). Формат: {"tasks": {task_id: task}}.
Env TASKS_FILE — путь файла (дефолт <repo>/data/tasks.json).
Атомарная запись (tmp + os.replace). Сид — задачи дня 17.
"""
import json
import os

REPO_ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))

SEED = {
    "tasks": {
        "TASK-42": {"id": "TASK-42",
                    "title": "Реализовать MCP tool-loop в агенте",
                    "description": ("LLM-driven tool-calling: tools -> "
                                    "tool_calls -> MCP -> role tool, "
                                    "цикл до 5 итераций"),
                    "status": "in_progress", "assignee": "migor",
                    "updated": "2026-09-23T10:00:00Z"},
        "TASK-7": {"id": "TASK-7",
                   "title": "Подключить Git MCP-сервер",
                   "description": ("Дефолт реестра MCP: "
                                   "npx @cyanheads/git-mcp-server"),
                   "status": "done", "assignee": "migor",
                   "updated": "2026-09-22T18:30:00Z"},
    }
}


def tasks_file() -> str:
    return (os.environ.get("TASKS_FILE")
            or os.path.join(REPO_ROOT, "data", "tasks.json"))


def _atomic_write(path: str, obj: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load() -> dict:
    """Файл -> dict; нет файла или битый JSON -> сид (и запись сида)."""
    path = tasks_file()
    d = None
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                loaded = json.load(f)
            if (isinstance(loaded, dict)
                    and isinstance(loaded.get("tasks"), dict)):
                d = loaded
        except (ValueError, OSError):
            d = None
    if d is None:
        d = json.loads(json.dumps(SEED))  # deep copy
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _atomic_write(path, d)
    return d


def save(data: dict) -> None:
    os.makedirs(os.path.dirname(tasks_file()), exist_ok=True)
    _atomic_write(tasks_file(), data)


def next_id(data: dict) -> str:
    """TASK-<max(числовые id) + 1> (алгоритм _next_task_id дня 17)."""
    mx = 0
    for k in data["tasks"]:
        if k.startswith("TASK-") and k[5:].isdigit():
            mx = max(mx, int(k[5:]))
    return "TASK-" + str(mx + 1)
