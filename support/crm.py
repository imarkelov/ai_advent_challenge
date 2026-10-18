"""MCP-мост ассистента поддержки (день 33).

Контекст пользователя/тикета берётся из CRM ЧЕРЕЗ MCP (задание дня):
серверы `crm_get_ticket` / `crm_get_user` (tools/mcp_servers) запускаются как
stdio-процессы, общение — JSON-RPC 2.0 (initialize / tools/list / tools/call).
Транспорт переиспользуется из Студии: `studio.backend.mcp.MCPClient`
(тот же протокол, что в днях 16–20) — не дублируем клиент.

Без MCP (сбой запуска) ассистент работает офлайн: тикет и пользователь
читаются напрямую из того же JSON-хранилища (tools/_support_store), но
CLI/e2e явно показывают, что источник — файл, а не MCP.
"""
from __future__ import annotations

import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVERS_DIR = os.path.join(REPO_ROOT, "tools", "mcp_servers")

_TICKET_SERVER = "crm_get_ticket"
_USER_SERVER = "crm_get_user"


def _tool_text(result: dict) -> tuple:
    """tools/call result -> (payload, is_error)."""
    is_error = bool(result.get("isError"))
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            try:
                return json.loads(item.get("text") or "{}"), is_error
            except ValueError:
                return {"text": item.get("text")}, is_error
    return {"error": "MCP: пустой ответ инструмента"}, True


class CRMClient:
    """MCP-клиент CRM: два stdio-сервера (тикет + пользователь)."""

    def __init__(self, launcher=None, timeout: float = 30.0) -> None:
        self._launcher = launcher
        self._timeout = timeout
        self._sessions: dict[str, object] = {}
        self.connected = False
        self.error: str | None = None
        self.backend = "mcp"

    # --- подключение ---

    def _spec(self, name: str) -> dict:
        return {"name": name, "type": "stdio",
                "command": [sys.executable, os.path.join(SERVERS_DIR,
                                                         name + ".py")],
                "url": "", "env": {}, "enabled": True}

    def connect(self) -> bool:
        """Подключить оба сервера. Возврат True — MCP живой; False — офлайн."""
        from studio.backend.mcp import MCPClient, MCPError
        try:
            for name in (_TICKET_SERVER, _USER_SERVER):
                client = MCPClient(self._spec(name), launcher=self._launcher,
                                   timeout=self._timeout)
                client.connect()
                self._sessions[name] = client
        except (MCPError, OSError) as exc:
            self.close()
            self.connected = False
            self.error = str(exc)
            self.backend = "file"
            return False
        self.connected = True
        self.error = None
        self.backend = "mcp"
        return True

    def close(self) -> None:
        for client in self._sessions.values():
            try:
                client.close()
            except Exception:
                pass
        self._sessions.clear()

    # --- инструменты ---

    def _call(self, server: str, arguments: dict) -> tuple:
        client = self._sessions[server]
        try:
            result = client.call_tool(
                {"crm_get_ticket": "get_ticket",
                 "crm_get_user": "get_user"}[server], arguments)
        except Exception as exc:  # MCPError / обрыв процесса
            return {"error": "MCP: " + str(exc)}, True
        return _tool_text(result)

    def get_ticket(self, ticket_id: str) -> tuple:
        """(payload, is_error) — из MCP или из файла (офлайн-фолбэк)."""
        if self.connected:
            return self._call(_TICKET_SERVER, {"ticket_id": ticket_id})
        from tools import _support_store as store
        ticket = store.load_crm()["tickets"].get((ticket_id or "").strip())
        if ticket is None:
            return {"error": "Тикет не найден: " + str(ticket_id)}, True
        return ticket, False

    def get_user(self, user_id: str) -> tuple:
        """(payload, is_error) — из MCP или из файла (офлайн-фолбэк)."""
        if self.connected:
            return self._call(_USER_SERVER, {"user_id": user_id})
        from tools import _support_store as store
        user = store.load_crm()["users"].get((user_id or "").strip())
        if user is None:
            return {"error": "Пользователь не найден: " + str(user_id)}, True
        return user, False

    # --- инструменты для LLM (OpenAI-формат) ---

    @staticmethod
    def tool_definitions() -> list:
        """tools для /chat/completions: модель сама запрашивает контекст."""
        return [
            {"type": "function", "function": {
                "name": "get_ticket",
                "description": ("Контекст тикета поддержки: тема, статус, "
                                "приоритет, переписка, факты, состояние "
                                "аккаунта. Вызывай, когда нужны детали "
                                "обращения."),
                "parameters": {"type": "object", "properties": {
                    "ticket_id": {"type": "string",
                                  "description": "id тикета (TCK-4101)"}},
                    "required": ["ticket_id"]}}},
            {"type": "function", "function": {
                "name": "get_user",
                "description": ("Профиль пользователя CRM: имя, компания, "
                                "тариф, статус аккаунта, история обращений, "
                                "заметки поддержки."),
                "parameters": {"type": "object", "properties": {
                    "user_id": {"type": "string",
                                "description": "id пользователя (U-101)"}},
                    "required": ["user_id"]}}},
        ]

    def call_tool(self, name: str, arguments: dict) -> tuple:
        """Диспетчер для tool-loop: имя из tool_definitions -> (payload, err)."""
        arguments = arguments if isinstance(arguments, dict) else {}
        if name == "get_ticket":
            return self.get_ticket(str(arguments.get("ticket_id") or ""))
        if name == "get_user":
            return self.get_user(str(arguments.get("user_id") or ""))
        return {"error": "Неизвестный инструмент: " + str(name)}, True
