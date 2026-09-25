"""Общий каркас едицельных MCP-серверов (день 20).

stdio JSON-RPC 2.0 (protocolVersion 2024-11-05), один тул на сервер.
Паттерн task_manager.py (день 17). Только stdlib.

Структура сервера:
    TOOL = {"name": ..., "description": ..., "input_schema": {...}}

    def call(args: dict) -> tuple:      # (payload: dict, is_error: bool)
        ...

    if __name__ == "__main__":
        run_server("<имя-сервера>", TOOL, call)
"""
import json
import sys

PROTOCOL = "2024-11-05"


def _ok(m, result):
    return {"jsonrpc": "2.0", "id": m.get("id"), "result": result}


def make_server(server_name, tool, call_handler):
    """Возвращает fn(m: dict) -> dict | None (JSON-RPC-ответ; None —
    на notification). call_handler(arguments: dict) -> (payload, is_error)."""

    def handle(m):
        if "id" not in m:  # notification — без ответа
            return None
        method = m.get("method")
        if method == "initialize":
            return _ok(m, {"protocolVersion": PROTOCOL,
                           "serverInfo": {"name": server_name,
                                          "version": "1.0"},
                           "capabilities": {"tools": {}}})
        if method == "tools/list":
            return _ok(m, {"tools": [tool]})
        if method == "tools/call":
            params = m.get("params") or {}
            if params.get("name") != tool["name"]:
                return _ok(m, {"content": [{"type": "text", "text":
                            json.dumps({"error": "Инструмент «%s» не найден"
                                          % params.get("name")},
                                       ensure_ascii=False)}],
                              "isError": True})
            try:
                payload, is_err = call_handler(params.get("arguments") or {})
            except Exception as e:
                payload, is_err = {"error": "Внутренняя ошибка: " + str(e)}, True
            result = {"content": [{"type": "text", "text":
                                   json.dumps(payload, ensure_ascii=False,
                                              indent=2)}]}
            if is_err:
                result["isError"] = True
            return _ok(m, result)
        return {"jsonrpc": "2.0", "id": m.get("id"),
                "error": {"code": -32601,
                          "message": "Неизвестный метод: " + str(method)}}

    return handle


def run_server(server_name, tool, call_handler) -> None:
    """stdio-цикл: одна JSON-строка из stdin -> одна JSON-строка в stdout."""
    handle = make_server(server_name, tool, call_handler)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            m = json.loads(line)
        except ValueError:
            sys.stdout.write(json.dumps(
                {"jsonrpc": "2.0", "id": None,
                 "error": {"code": -32700, "message": "Parse error"}},
                ensure_ascii=False) + "\n")
            sys.stdout.flush()
            continue
        resp = handle(m)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
