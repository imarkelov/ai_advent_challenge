"""MCP-клиент (stdio JSON-RPC 2.0) и реестр серверов."""
from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path

PROTOCOL_VERSION = "2024-11-05"

ENV_ALLOWLIST = (
    "PATH", "SYSTEMROOT", "TEMP", "TMP", "HOME", "USERPROFILE",
    "PYTHONPATH", "PYTHONIOENCODING", "JAVA_HOME",
    "ANDROID_HOME", "ANDROID_SDK_ROOT",
)


def scrubbed_env(extra: dict | None = None) -> dict[str, str]:
    env = {name: os.environ[name] for name in ENV_ALLOWLIST if name in os.environ}
    env.update({str(k): str(v) for k, v in (extra or {}).items()})
    return env


class McpError(RuntimeError):
    pass


class McpServerStore:
    def __init__(self, data_dir: str):
        self.path = Path(data_dir) / "mcp_servers.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except ValueError:
            return []

    def _save(self, servers: list[dict]) -> None:
        self.path.write_text(json.dumps(servers, ensure_ascii=False, indent=2),
                             encoding="utf-8")

    def list(self) -> list[dict]:
        return self._load()

    def get(self, server_id: str) -> dict:
        for server in self._load():
            if server["id"] == server_id:
                return server
        raise McpError(f"сервер {server_id} не найден")

    def add(self, spec: dict) -> dict:
        server_type = spec.get("type")
        if server_type not in {"stdio", "http"}:
            raise McpError("тип сервера должен быть stdio или http")
        if server_type == "stdio" and not spec.get("command"):
            raise McpError("для stdio-сервера нужна команда")
        if server_type == "http" and not spec.get("url"):
            raise McpError("для http-сервера нужен url")
        server = {
            "id": uuid.uuid4().hex[:8],
            "name": spec.get("name") or "mcp",
            "type": server_type,
            "command": spec.get("command") or [],
            "url": spec.get("url") or "",
            "env": spec.get("env") or {},
            "enabled": bool(spec.get("enabled", True)),
        }
        servers = self._load()
        servers.append(server)
        self._save(servers)
        return server

    def delete(self, server_id: str) -> None:
        servers = self._load()
        remaining = [s for s in servers if s["id"] != server_id]
        if len(remaining) == len(servers):
            raise McpError(f"сервер {server_id} не найден")
        self._save(remaining)


class _StdioSession:
    def __init__(self, command: list[str], env: dict):
        full_env = scrubbed_env(env)
        try:
            self.proc = subprocess.Popen(
                command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                errors="replace", bufsize=1, env=full_env,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise McpError(f"не удалось запустить MCP-сервер: {exc}") from exc
        self._next_id = 0

    def _write(self, payload: dict) -> None:
        if not self.proc.stdin:
            raise McpError("stdin MCP-сервера закрыт")
        self.proc.stdin.write(json.dumps(payload) + "\n")
        self.proc.stdin.flush()

    def request(self, method: str, params: dict | None = None) -> dict:
        self._next_id += 1
        request_id = self._next_id
        self._write({"jsonrpc": "2.0", "id": request_id, "method": method,
                     "params": params or {}})
        while True:
            line = self.proc.stdout.readline() if self.proc.stdout else ""
            if not line:
                raise McpError("MCP-сервер закрыл соединение")
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise McpError(message["error"].get("message", "ошибка MCP"))
            return message.get("result", {})

    def notify(self, method: str) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": {}})

    def close(self) -> None:
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except (OSError, subprocess.SubprocessError):
            pass


class McpRegistry:
    def __init__(self, store: McpServerStore):
        self.store = store
        self._sessions: dict[str, _StdioSession] = {}
        self._tools: dict[str, list[dict]] = {}
        self._errors: dict[str, str] = {}

    def status(self, server_id: str) -> dict:
        if server_id in self._sessions:
            return {"state": "connected", "tools_count": len(self._tools.get(server_id, []))}
        if server_id in self._errors:
            return {"state": "error", "error": self._errors[server_id]}
        return {"state": "idle", "tools_count": 0}

    def connect(self, server_id: str) -> dict:
        server = self.store.get(server_id)
        if server["type"] != "stdio":
            raise McpError("поддержан только stdio-транспорт")
        self._errors.pop(server_id, None)
        session = _StdioSession(server["command"], server["env"])
        try:
            session.request("initialize", {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "studio2", "version": "day35"},
            })
            session.notify("notifications/initialized")
            tools = session.request("tools/list").get("tools", [])
        except McpError as exc:
            session.close()
            self._errors[server_id] = str(exc)
            raise
        self._sessions[server_id] = session
        self._tools[server_id] = tools
        return {"tools": tools}

    def disconnect(self, server_id: str) -> None:
        session = self._sessions.pop(server_id, None)
        self._tools.pop(server_id, None)
        if session:
            session.close()

    def tools(self) -> list[dict]:
        result = []
        for server_id, tools in self._tools.items():
            for tool in tools:
                result.append({"server_id": server_id, **tool})
        return result

    def call(self, server_id: str, tool: str, arguments: dict) -> dict:
        session = self._sessions.get(server_id)
        if session is None:
            raise McpError(f"сервер {server_id} не подключён")
        return session.request("tools/call", {"name": tool, "arguments": arguments})

    def close_all(self) -> None:
        for server_id in list(self._sessions):
            self.disconnect(server_id)
