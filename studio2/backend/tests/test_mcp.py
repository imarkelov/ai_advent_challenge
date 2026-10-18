import sys
import textwrap

import pytest

from mcp import McpError, McpRegistry, McpServerStore

SERVER = textwrap.dedent(
    """
    import json, sys

    def send(obj):
        sys.stdout.write(json.dumps(obj) + "\\n")
        sys.stdout.flush()

    for line in sys.stdin:
        if not line.strip():
            continue
        msg = json.loads(line)
        method = msg.get("method")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": msg["id"], "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "test", "version": "1"}}})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": [
                {"name": "ping", "description": "ping tool",
                 "inputSchema": {"type": "object", "properties": {}}}]}})
        elif method == "tools/call":
            send({"jsonrpc": "2.0", "id": msg["id"], "result": {
                "content": [{"type": "text", "text": "pong"}]}})
        elif method == "notifications/initialized":
            continue
        else:
            send({"jsonrpc": "2.0", "id": msg.get("id", 0),
                  "error": {"code": -32601, "message": "unknown"}})
    """
)


@pytest.fixture()
def test_server(tmp_path):
    path = tmp_path / "server.py"
    path.write_text(SERVER, encoding="utf-8")
    return str(path)


def test_store_add_and_list(tmp_path):
    store = McpServerStore(str(tmp_path))
    server = store.add({"name": "allure", "type": "http", "url": "https://allure/api"})
    assert server["id"]
    assert [s["name"] for s in store.list()] == ["allure"]
    assert store.get(server["id"])["url"] == "https://allure/api"


def test_store_delete(tmp_path):
    store = McpServerStore(str(tmp_path))
    server = store.add({"name": "x", "type": "http", "url": "http://x"})
    store.delete(server["id"])
    assert store.list() == []
    with pytest.raises(McpError):
        store.get(server["id"])


def test_add_rejects_unknown_type(tmp_path):
    with pytest.raises(McpError):
        McpServerStore(str(tmp_path)).add({"name": "x", "type": "carrier-pigeon"})


def test_stdio_connect_lists_tools(tmp_path, test_server):
    store = McpServerStore(str(tmp_path))
    server = store.add({"name": "local", "type": "stdio",
                        "command": [sys.executable, test_server]})
    registry = McpRegistry(store)
    result = registry.connect(server["id"])
    assert [tool["name"] for tool in result["tools"]] == ["ping"]
    assert registry.status(server["id"])["state"] == "connected"
    registry.disconnect(server["id"])


def test_stdio_call_tool(tmp_path, test_server):
    store = McpServerStore(str(tmp_path))
    server = store.add({"name": "local", "type": "stdio",
                        "command": [sys.executable, test_server]})
    registry = McpRegistry(store)
    registry.connect(server["id"])
    result = registry.call(server["id"], "ping", {})
    assert result["content"][0]["text"] == "pong"
    registry.disconnect(server["id"])


def test_call_without_connect_raises(tmp_path):
    store = McpServerStore(str(tmp_path))
    server = store.add({"name": "local", "type": "stdio", "command": [sys.executable]})
    with pytest.raises(McpError):
        McpRegistry(store).call(server["id"], "ping", {})


def test_status_idle_before_connect(tmp_path):
    store = McpServerStore(str(tmp_path))
    server = store.add({"name": "local", "type": "stdio", "command": [sys.executable]})
    assert McpRegistry(store).status(server["id"])["state"] == "idle"


def test_tools_aggregates_connected_servers(tmp_path, test_server):
    store = McpServerStore(str(tmp_path))
    server = store.add({"name": "local", "type": "stdio",
                        "command": [sys.executable, test_server]})
    registry = McpRegistry(store)
    registry.connect(server["id"])
    tools = registry.tools()
    assert tools and tools[0]["server_id"] == server["id"]
    registry.disconnect(server["id"])
