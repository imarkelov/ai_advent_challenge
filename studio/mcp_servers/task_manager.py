"""Mock Task Manager (день 17): MCP-сервер для tool-loop.

Протокол 2024-11-05: JSON-RPC 2.0 строками (newline-delimited JSON)
по stdin/stdout. Только stdlib (json, sys) — сеть и зависимости не
нужны. Запуск: python studio/mcp_servers/task_manager.py

Задачи — in-memory: при перезапуске сервера список сбрасывается
(TASK-42 и TASK-7 всегда на месте).
"""
import json
import sys

# In-memory-хранилище задач (предзагружено для детерминированных тестов)
TASKS = {
    "TASK-42": {
        "id": "TASK-42",
        "title": "Реализовать MCP tool-loop в агенте",
        "status": "in_progress",
        "assignee": "migor",
        "updated": "2026-09-23 10:00:00",
    },
    "TASK-7": {
        "id": "TASK-7",
        "title": "Подключить Git MCP-сервер",
        "status": "done",
        "assignee": "migor",
        "updated": "2026-09-22 18:30:00",
    },
}

TOOLS = [
    {
        "name": "get_task_details",
        "description": (
            "Получить полные детали задачи по её идентификатору "
            "(например, TASK-42). Вызывай, когда в диалоге или в "
            "сообщении пользователя есть id задачи и тебе нужны её "
            "текущие данные: название, статус (todo / in_progress / "
            "done), исполнитель, время обновления. Это нужно, чтобы "
            "ответить на вопрос пользователя о конкретной задаче или "
            "чтобы свериться с актуальным статусом до действий — а не "
            "опираться на устаревшие данные из истории. Не вызывай, "
            "если данные задачи уже были получены в этом диалоге и не "
            "перезаписаны."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": (
                        "Идентификатор задачи в формате TASK-<номер>, "
                        "например \"TASK-42\". Точное значение — как из "
                        "сообщения пользователя или из предыдущих "
                        "вызовов инструментов в этом диалоге."
                    ),
                },
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "create_task",
        "description": (
            "Создать новую задачу в трекере. Вызывай, когда пользователь "
            "просит добавить новую задачу, записать работу, поставить на "
            "контроль или зарегистрировать поручение — то есть когда "
            "задачу ещё не существует в трекере. Возвращает созданную "
            "задачу со сгенерированным id (TASK-<номер>) и стартовым "
            "статусом todo — этот id можно дальше использовать в "
            "get_task_details. Не вызывай для уже существующих задач "
            "(для них — get_task_details) и не дублируй создание, если "
            "задача уже была создана в этом диалоге."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": (
                        "Название новой задачи — короткая формулировка "
                        "того, что нужно сделать (глагол + объект)."
                    ),
                },
                "description": {
                    "type": "string",
                    "description": (
                        "Необязательное описание: детали, контекст и "
                        "критерии готовности, которые помогут выполнить "
                        "задачу позже."
                    ),
                },
            },
            "required": ["title"],
        },
    },
]


def _next_task_id() -> str:
    """Следующий id: максимальный числовой суффикс среди TASKS + 1."""
    nums = [int(tid.rsplit("-", 1)[1]) for tid in TASKS
            if tid.startswith("TASK-")
            and tid.rsplit("-", 1)[1].isdigit()]
    return "TASK-" + str(max(nums) + 1 if nums else 1)


def _ok_result(payload: dict) -> dict:
    """Успешный результат tools/call: текст = pretty-JSON."""
    return {"content": [{"type": "text",
                         "text": json.dumps(payload, ensure_ascii=False,
                                             indent=2)}],
            "isError": False}


def _error_result(message: str) -> dict:
    """Ошибка tools/call: текст = JSON {"error": ...}, isError: True."""
    return _ok_result({"error": message}) | {"isError": True}


def _call_get_task_details(arguments: dict) -> dict:
    task_id = (arguments or {}).get("task_id")
    if not isinstance(task_id, str) or not task_id:
        return _error_result("Задача не найдена: "
                             + str(task_id if task_id is not None
                                   else "(не указан)"))
    task = TASKS.get(task_id)
    if task is None:
        return _error_result("Задача не найдена: " + task_id)
    return _ok_result(task)


def _call_create_task(arguments: dict) -> dict:
    arguments = arguments or {}
    title = arguments.get("title")
    if not isinstance(title, str) or not title:
        return _error_result("Не задан обязательный аргумент: title")
    task_id = _next_task_id()
    task = {"id": task_id, "title": title,
            "description": arguments.get("description") or "",
            "status": "todo", "assignee": None}
    TASKS[task_id] = task
    return _ok_result(task)


CALL_HANDLERS = {
    "get_task_details": _call_get_task_details,
    "create_task": _call_create_task,
}


def _call_tool(params: dict) -> dict:
    name = (params or {}).get("name")
    handler = CALL_HANDLERS.get(name)
    if handler is None:
        return _error_result("Инструмент «%s» не найден" % name)
    return handler((params or {}).get("arguments") or {})


def main() -> None:
    """Цикл: по одной JSON-строке из stdin, ответ — строка в stdout.
    Notification (без id) не получает ответа."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if "id" not in m:  # notification — без ответа
            continue
        if m.get("method") == "initialize":
            result = {"protocolVersion": "2024-11-05", "capabilities": {},
                      "serverInfo": {"name": "task-manager",
                                     "version": "1.0"}}
        elif m.get("method") == "tools/list":
            result = {"tools": TOOLS}
        elif m.get("method") == "tools/call":
            result = _call_tool(m.get("params"))
        else:
            sys.stdout.write(json.dumps(
                {"jsonrpc": "2.0", "id": m["id"],
                 "error": {"code": -32601, "message": "метод не найден"}},
                ensure_ascii=False) + "\n")
            sys.stdout.flush()
            continue
        sys.stdout.write(json.dumps(
            {"jsonrpc": "2.0", "id": m["id"], "result": result},
            ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
