"""MCP-мост файловых инструментов (день 34).

Четыре едицельных stdio-сервера (tools/mcp_servers/fs_*.py) — read_file,
search_files, write_file, diff_files. Общение JSON-RPC 2.0 через
`studio.backend.mcp.MCPClient` (клиент дней 16–20, не дублируем).

Без MCP (сбой запуска) — прямой вызов тех же функций инструментов
(модули серверов импортируются из tools/mcp_servers), результат помечается
как файловый фолбэк. Все операции проходят песочницу workspace.
"""
from __future__ import annotations

import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVERS_DIR = os.path.join(REPO_ROOT, "tools", "mcp_servers")

_TOOLS = {
    "read_file": "fs_read",
    "search_files": "fs_search",
    "write_file": "fs_write",
    "diff_files": "fs_diff",
}


def _tool_text(result: dict) -> tuple:
    is_error = bool(result.get("isError"))
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            try:
                return json.loads(item.get("text") or "{}"), is_error
            except ValueError:
                return {"text": item.get("text")}, is_error
    return {"error": "MCP: пустой ответ инструмента"}, True


class FileTools:
    """Файловые инструменты ассистента: MCP по stdio или прямой фолбэк."""

    def __init__(self, launcher=None, timeout: float = 30.0,
                 env: dict | None = None) -> None:
        self._launcher = launcher
        self._timeout = timeout
        # явный env: MCPClient по умолчанию подменяет окружение сервера на
        # os.environ процесса клиента — серверы должны видеть FS_WORKSPACE,
        # FS_RUN_ID, FS_BACKUP_DIR, PYTHONIOENCODING
        self._env = env if env is not None else dict(os.environ)
        self._env.setdefault("PYTHONIOENCODING", "utf-8")
        self._sessions: dict[str, object] = {}
        self.connected = False
        self.error: str | None = None
        self.backend = "mcp"

    def connect(self) -> bool:
        from studio.backend.mcp import MCPClient, MCPError
        try:
            for tool, server in _TOOLS.items():
                spec = {"name": server, "type": "stdio",
                        "command": [sys.executable,
                                    os.path.join(SERVERS_DIR, server + ".py")],
                        "url": "", "env": {}, "enabled": True}
                client = MCPClient(spec, launcher=self._launcher,
                                   env=dict(self._env), timeout=self._timeout)
                client.connect()
                self._sessions[tool] = client
        except (MCPError, OSError) as exc:
            self.close()
            self.connected = False
            self.error = str(exc)
            self.backend = "direct"
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

    def call_tool(self, name: str, arguments: dict) -> tuple:
        arguments = arguments if isinstance(arguments, dict) else {}
        if name == "search_files":
            arguments = dict(arguments)
            root = os.path.abspath(os.environ.get("FS_WORKSPACE", REPO_ROOT))
            path = str(arguments.get("path") or "").strip()
            target = os.path.abspath(os.path.join(root, path)) if path else root
            if path and os.path.isfile(target):
                # модель может передать файл вместо каталога — ищем рядом
                arguments["path"] = os.path.dirname(path) if os.path.dirname(
                    path) else "."
        if name == "diff_files":
            arguments.setdefault("run_id", os.environ.get("FS_RUN_ID", ""))
        if self.connected and name in self._sessions:
            try:
                return _tool_text(self._sessions[name].call_tool(name, arguments))
            except Exception as exc:  # MCPError / обрыв процесса
                return {"error": "MCP: " + str(exc)}, True
        return self._direct(name, arguments)

    def restart(self) -> bool:
        """Перезапустить серверы (после смены FS_RUN_ID / корня)."""
        self.close()
        return self.connect()

    @staticmethod
    def _direct(name: str, arguments: dict) -> tuple:
        sys.path.insert(0, SERVERS_DIR)
        sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))
        try:
            if name == "read_file":
                import fs_read as mod
            elif name == "search_files":
                import fs_search as mod
            elif name == "write_file":
                import fs_write as mod
            elif name == "diff_files":
                import fs_diff as mod
            else:
                return {"error": "Неизвестный инструмент: " + str(name)}, True
            return mod.call(arguments)
        except Exception as exc:  # noqa: BLE001
            return {"error": "инструмент " + name + ": " + str(exc)}, True

    @staticmethod
    def tool_definitions() -> list:
        """tools для /chat/completions (OpenAI-формат)."""
        return [
            {"type": "function", "function": {
                "name": "read_file",
                "description": "Прочитать файл проекта (content, номер строки).",
                "parameters": {"type": "object", "properties": {
                    "path": {"type": "string"},
                    "from_line": {"type": "integer"},
                    "to_line": {"type": "integer"}},
                    "required": ["path"]}}},
            {"type": "function", "function": {
                "name": "search_files",
                "description": ("Найти все места использования (regex) по "
                                "нескольким файлам; glob-фильтр, сводка by_file."),
                "parameters": {"type": "object", "properties": {
                    "pattern": {"type": "string"},
                    "glob": {"type": "string"},
                    "path": {"type": "string"},
                    "ignore_case": {"type": "boolean"}},
                    "required": ["pattern"]}}},
            {"type": "function", "function": {
                "name": "write_file",
                "description": ("Создать/изменить файл: полное новое "
                                "содержимое; бэкап + атомарная запись + diff."),
                "parameters": {"type": "object", "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"}},
                    "required": ["path", "content"]}}},
            {"type": "function", "function": {
                "name": "diff_files",
                "description": ("Diff/список изменений относительно бэкапа "
                                "прогона. Без path — все изменённые файлы."),
                "parameters": {"type": "object", "properties": {
                    "path": {"type": "string"}}},
            }},
        ]
