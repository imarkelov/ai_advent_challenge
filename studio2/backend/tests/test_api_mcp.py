import sys
import textwrap
from pathlib import Path

from fastapi.testclient import TestClient

from config import DEFAULT_CONFIG
from main import create_app

SERVER = textwrap.dedent(
    """
    import json, sys
    def send(obj):
        sys.stdout.write(json.dumps(obj) + "\\n"); sys.stdout.flush()
    for line in sys.stdin:
        if not line.strip():
            continue
        msg = json.loads(line); method = msg.get("method")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": msg["id"], "result": {
                "protocolVersion": "2024-11-05", "capabilities": {},
                "serverInfo": {"name": "t", "version": "1"}}})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": [
                {"name": "getTestCase", "description": "allure tc",
                 "inputSchema": {"type": "object"}}]}})
        elif method == "notifications/initialized":
            continue
        else:
            send({"jsonrpc": "2.0", "id": msg.get("id", 0),
                  "error": {"code": -32601, "message": "unknown"}})
    """
)


def _client(tmp_path):
    (tmp_path / "doc").mkdir()
    return TestClient(create_app(doc_dir=str(tmp_path / "doc"),
                                 data_dir=str(tmp_path / "data")))


def test_mcp_server_lifecycle(tmp_path):
    server_py = tmp_path / "server.py"
    server_py.write_text(SERVER, encoding="utf-8")
    client = _client(tmp_path)
    created = client.post("/api/mcp/servers", json={
        "name": "allure", "type": "stdio", "command": [sys.executable, str(server_py)],
    })
    assert created.status_code == 201
    server_id = created.json()["server"]["id"]
    listed = client.get("/api/mcp/servers").json()["servers"]
    assert listed[0]["status"]["state"] == "idle"
    connected = client.post(f"/api/mcp/servers/{server_id}/connect")
    assert connected.status_code == 200
    tools = client.get("/api/mcp/tools").json()["tools"]
    assert tools[0]["name"] == "getTestCase"
    assert client.post(f"/api/mcp/servers/{server_id}/disconnect").status_code == 200
    assert client.delete(f"/api/mcp/servers/{server_id}").status_code == 200


def test_mcp_add_rejects_bad_type(tmp_path):
    response = _client(tmp_path).post("/api/mcp/servers", json={"name": "x", "type": "ftp"})
    assert response.status_code == 400


def test_mcp_unknown_server_returns_404(tmp_path):
    assert _client(tmp_path).post("/api/mcp/servers/nope/connect").status_code == 404
