"""MCP-сервер "crm_get_user" (день 33): один тул — get_user.

Пользователь CRM по id (контекст обращения: тариф, статус аккаунта, история
обращений, заметки). Чтение из JSON (tools/crm.json, env SUPPORT_CRM_FILE).
Только stdlib.
Запуск: python tools/mcp_servers/crm_get_user.py
"""
import os
import sys

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = os.path.dirname(SERVER_DIR)
REPO_DIR = os.path.dirname(TOOLS_DIR)
for _p in (SERVER_DIR, TOOLS_DIR, REPO_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import _support_store  # noqa: E402
from studio.mcp_servers._mcp_base import run_server  # noqa: E402

TOOL = {
    "name": "get_user",
    "description": ("Пользователь CRM по id: id, name, email, company, plan, "
                    "role, account_status, registered, history[], notes."),
    "input_schema": {"type": "object", "properties": {
        "user_id": {"type": "string",
                    "description": "Идентификатор пользователя (U-101)"}},
        "required": ["user_id"]},
}


def call(args: dict) -> tuple:
    uid = (args or {}).get("user_id")
    if not isinstance(uid, str) or not uid.strip():
        return {"error": "Не задан обязательный аргумент: user_id"}, True
    uid = uid.strip()
    data = _support_store.load_crm()
    user = data["users"].get(uid)
    if user is None:
        return {"error": "Пользователь не найден: " + uid}, True
    return user, False


if __name__ == "__main__":
    run_server("crm_get_user", TOOL, call)
