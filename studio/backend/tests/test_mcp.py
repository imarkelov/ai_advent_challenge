"""Тесты MCP-клиента (день 16, офлайн): fake stdio-процесс,
httpx.MockTransport — сеть и npx не используются."""
import json
import os
import sys
import threading
import time

import pytest

from mcp import MCPClient, MCPError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from memory import MemoryStore  # noqa: E402


class FakeMcpProcess:
    """In-memory замена subprocess.Popen: поток эмулирует MCP
    stdio-сервер. .stdin — поток записи JSON-RPC строк; .stdout —
    поток ответов; poll/kill/wait — интерфейс для _StdioSession."""

    def __init__(self, tools=None):
        self._tools = tools or [
            {"name": "mock_echo", "description": "Эхо-инструмент",
             "inputSchema": {"type": "object",
                             "properties": {"x": {"type": "string"}}}},
            {"name": "mock_ping", "description": "Пинг",
             "inputSchema": {"type": "object"}},
        ]
        self._cmd_r, self._cmd_w = os.pipe()
        self._res_r, self._res_w = os.pipe()
        self.stdin = os.fdopen(self._cmd_w, "w", encoding="utf-8")
        self.stdout = os.fdopen(self._res_r, "r", encoding="utf-8")
        self._alive = True
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        try:
            cin = os.fdopen(self._cmd_r, "r", encoding="utf-8")
            cout = os.fdopen(self._res_w, "w", encoding="utf-8")
            for line in cin:
                if not self._alive:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    m = json.loads(line)
                except ValueError:
                    continue
                if "id" not in m:  # notification — без ответа
                    continue
                if m["method"] == "initialize":
                    resp = {"protocolVersion": "2024-11-05",
                            "capabilities": {},
                            "serverInfo": {"name": "fake", "version": "1"}}
                elif m["method"] == "tools/list":
                    resp = {"tools": self._tools}
                elif m["method"] == "tools/call":
                    args = (m.get("params") or {}).get("arguments") or {}
                    resp = {"content": [{"type": "text",
                                         "text": str(args)}],
                            "isError": False}
                else:
                    self._send(cout, {"jsonrpc": "2.0", "id": m["id"],
                                      "error": {"code": -32601,
                                                "message": "метод не найден"}})
                    continue
                self._send(cout, {"jsonrpc": "2.0", "id": m["id"],
                                  "result": resp})
        finally:
            self._alive = False

    @staticmethod
    def _send(out, obj):
        out.write(json.dumps(obj, ensure_ascii=False) + "\n")
        out.flush()

    def poll(self):
        return None if self._alive else 0

    def kill(self):
        self._alive = False
        try:
            self.stdin.close()
        except Exception:
            pass

    def wait(self, timeout=None):
        self.thread.join(timeout if timeout else 5)


def make_fake_launcher(fail=False):
    """Фабрика launcher-а: fake-процесс или OSError (команда не нашлась)."""
    def launcher(command, env):
        if fail:
            raise OSError(2, "No such file or directory")
        return FakeMcpProcess()
    return launcher


STDIO_SERVER = {"id": "mcp_t1", "name": "Fake", "type": "stdio",
                "command": ["mock", "--flag"], "url": "", "env": {},
                "enabled": True}


def test_stdio_connect_returns_tools():
    client = MCPClient(STDIO_SERVER, launcher=make_fake_launcher())
    tools = client.connect()
    client.close()
    assert [t["name"] for t in tools] == ["mock_echo", "mock_ping"]
    assert tools[0]["description"] == "Эхо-инструмент"
    assert tools[0]["input_schema"]["type"] == "object"


def test_stdio_call_tool():
    client = MCPClient(STDIO_SERVER, launcher=make_fake_launcher())
    client.connect()
    try:
        result = client.call_tool("mock_echo", {"x": "1"})
    finally:
        client.close()
    assert result["isError"] is False
    assert result["content"][0]["text"] == "{'x': '1'}"


def test_stdio_call_tool_before_connect():
    client = MCPClient(STDIO_SERVER, launcher=make_fake_launcher())
    with pytest.raises(MCPError):
        client.call_tool("mock_echo")


def test_stdio_launch_failure_is_mcp_error():
    client = MCPClient(STDIO_SERVER, launcher=make_fake_launcher(fail=True))
    with pytest.raises(MCPError):
        client.connect()
    client.close()


def test_stdio_missing_env_placeholder_is_mcp_error():
    server = dict(STDIO_SERVER, command=["npx", "--key", "{NOPE_KEY}"])
    client = MCPClient(server, launcher=make_fake_launcher(), env={})
    with pytest.raises(MCPError, match="NOPE_KEY"):
        client.connect()
    client.close()


def test_stdio_env_placeholder_expanded():
    seen = {}

    def launcher(command, env):
        seen["command"] = command
        seen["env"] = dict(env)
        return FakeMcpProcess()

    server = dict(STDIO_SERVER, command=["npx", "--key", "{MY_KEY}"],
                  env={"FOO": "{MY_KEY}", "BAR": "static"})
    client = MCPClient(server, launcher=launcher, env={"MY_KEY": "secret"})
    client.connect()
    client.close()
    assert seen["command"] == ["npx", "--key", "secret"]
    assert seen["env"]["FOO"] == "secret"
    assert seen["env"]["BAR"] == "static"


def test_stdio_timeout_raises_mcp_error():
    class Silent:
        """Процесс, который молчит: connect() должен упасть по таймауту."""

        def __init__(self):
            # Два пайпа: в stdin пишут (никто не читает), из stdout
            # читают (никто не пишет) — процесс молчит навсегда.
            _r1, w1 = os.pipe()
            r2, _w2 = os.pipe()
            self.stdin = os.fdopen(w1, "w", encoding="utf-8")
            self.stdout = os.fdopen(r2, "r", encoding="utf-8")

        def poll(self):
            return None

        def kill(self):
            pass

        def wait(self, timeout=None):
            return None

    client = MCPClient(STDIO_SERVER, launcher=lambda c, e: Silent(),
                       timeout=0.3)
    start = time.monotonic()
    with pytest.raises(MCPError):
        client.connect()
    assert time.monotonic() - start < 5
    client.close()
