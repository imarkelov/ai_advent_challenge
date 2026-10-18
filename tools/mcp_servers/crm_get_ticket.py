"""MCP-сервер "crm_get_ticket" (день 33): один тул — get_ticket.

Ассистент поддержки берёт контекст тикета через MCP (задание дня: CRM-контекст
подключается как MCP-инструмент). Чтение из JSON (tools/crm.json, env
SUPPORT_CRM_FILE). Только stdlib.
Запуск: python tools/mcp_servers/crm_get_ticket.py
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
    "name": "get_ticket",
    "description": ("Тикет поддержки по id: id, subject, status, priority, "
                    "created, updated, user_id, messages[], facts[], "
                    "sync_status."),
    "input_schema": {"type": "object", "properties": {
        "ticket_id": {"type": "string",
                      "description": "Идентификатор тикета (TCK-1234)"}},
        "required": ["ticket_id"]},
}


def call(args: dict) -> tuple:
    tid = (args or {}).get("ticket_id")
    if not isinstance(tid, str) or not tid.strip():
        return {"error": "Не задан обязательный аргумент: ticket_id"}, True
    tid = tid.strip()
    data = _support_store.load_crm()
    ticket = data["tickets"].get(tid)
    if ticket is None:
        return {"error": "Тикет не найден: " + tid}, True
    return ticket, False


if __name__ == "__main__":
    run_server("crm_get_ticket", TOOL, call)
