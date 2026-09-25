"""Тесты MCP-клиента (день 16, офлайн): fake stdio-процесс,
httpx.MockTransport — сеть и npx не используются."""
import json
import os
import sys
import threading
import time

import httpx
import pytest

from mcp import MCPClient, MCPError, _default_launcher

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


def test_default_launcher_missing_executable_is_mcp_error():
    # command[0] резолвится через PATH; нет имени — понятная MCPError,
    # а не [WinError 2] «The specified file was not found»
    with pytest.raises(MCPError, match="Не найден исполняемый файл"):
        _default_launcher(
            ["definitely_not_a_real_executable_xyz", "--flag"],
            dict(os.environ))


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


# ---------- http-транспорт (streamable-http, MockTransport) ----------

HTTP_SERVER = {"id": "mcp_h1", "name": "Remote", "type": "http",
               "command": [], "url": "https://mcp.example.com/sse",
               "env": {}, "enabled": True}


def mock_mcp_transport():
    """httpx.MockTransport: отвечает на POST JSON-RPC строкой JSON."""
    def _handler(request: httpx.Request) -> httpx.Response:
        msg = json.loads(request.content)
        if msg.get("method") == "initialize":
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": msg["id"],
                           "result": {"protocolVersion": "2024-11-05",
                                      "capabilities": {},
                                      "serverInfo": {"name": "r",
                                                     "version": "1"}}},
                headers={"Mcp-Session-Id": "s-1"})
        if msg.get("method") == "tools/list":
            return httpx.Response(200, json={"jsonrpc": "2.0",
                                             "id": msg["id"],
                                             "result": {"tools": [
                                                 {"name": "remote_search",
                                                  "description": "Поиск",
                                                  "inputSchema":
                                                      {"type": "object"}}]}})
        if msg.get("method") == "tools/call":
            return httpx.Response(200, json={"jsonrpc": "2.0",
                                             "id": msg["id"],
                                             "result": {
                                                 "content":
                                                     [{"type": "text",
                                                       "text": "ok"}],
                                                 "isError": False}})
        if msg.get("id") is None:  # notification
            return httpx.Response(202)
        return httpx.Response(200, json={"jsonrpc": "2.0",
                                         "id": msg.get("id"),
                                         "result": {}})
    return httpx.MockTransport(_handler)


def test_http_connect_returns_tools():
    client = MCPClient(HTTP_SERVER, http_client=httpx.Client(
        transport=mock_mcp_transport()))
    tools = client.connect()
    client.close()
    assert [t["name"] for t in tools] == ["remote_search"]
    assert tools[0]["input_schema"] == {"type": "object"}


def test_http_session_id_reused():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("Mcp-Session-Id"))
        msg = json.loads(request.content)
        if msg.get("method") == "initialize":
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": msg["id"],
                           "result": {"protocolVersion": "2024-11-05",
                                      "capabilities": {},
                                      "serverInfo": {"name": "r",
                                                     "version": "1"}}},
                headers={"Mcp-Session-Id": "s-42"})
        if msg.get("method") == "tools/list":
            return httpx.Response(200, json={"jsonrpc": "2.0",
                                             "id": msg["id"],
                                             "result": {"tools": []}})
        return httpx.Response(202)

    client = MCPClient(HTTP_SERVER,
                       http_client=httpx.Client(
                           transport=httpx.MockTransport(handler)))
    client.connect()
    client.close()
    # initialize — без id; notification initialized и tools/list — с s-42
    assert seen == [None, "s-42", "s-42"]


def test_http_sse_response():
    def handler(request: httpx.Request) -> httpx.Response:
        msg = json.loads(request.content)
        if msg.get("method") == "initialize":
            return httpx.Response(
                200, json={"jsonrpc": "2.0", "id": msg["id"],
                           "result": {"protocolVersion": "2024-11-05",
                                      "capabilities": {},
                                      "serverInfo": {"name": "r",
                                                     "version": "1"}}})
        body = ('event: message\n'
                'data: {"jsonrpc": "2.0", "id": 1, "result": '
                '{"tools": [{"name": "sse_tool", "description": "SSE",'
                ' "inputSchema": {"type": "object"}}]}}\n\n')
        return httpx.Response(200, text=body,
                              headers={"content-type":
                                       "text/event-stream"})

    client = MCPClient(HTTP_SERVER,
                       http_client=httpx.Client(
                           transport=httpx.MockTransport(handler)))
    tools = client.connect()
    client.close()
    assert [t["name"] for t in tools] == ["sse_tool"]


def test_http_error_status_is_mcp_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = MCPClient(HTTP_SERVER,
                       http_client=httpx.Client(
                           transport=httpx.MockTransport(handler)))
    with pytest.raises(MCPError, match="500"):
        client.connect()
    client.close()


# ---------- MCPRegistry (реестр + статусы + дефолты) ----------

from mcp import MCPRegistry  # noqa: E402


def _store(tmp_path) -> MemoryStore:
    return MemoryStore(str(tmp_path))


# 12 дефолтов (день 20): Firecrawl, Git + 10 одиночных локальных
_EXPECTED12 = {"Firecrawl", "Git"} | {
    "weather", "news", "digest_make", "digest_read", "task_create",
    "task_get", "digest_search", "digest_summarize", "file_save",
    "habr_news"}


def test_registry_seeds_defaults_once(tmp_path):
    reg = MCPRegistry(_store(tmp_path), launcher=make_fake_launcher())
    try:
        names1 = [s["name"] for s in reg.servers()]
        assert set(names1) == _EXPECTED12
        assert all(s["status"] == "idle" for s in reg.servers())
        # повторный вызов не дублирует
        assert [s["name"] for s in reg.servers()] == names1
    finally:
        reg.close_all()
    # файл создан на диске
    assert (tmp_path / "mcp_servers.json").exists()


def test_registry_defaults_env(tmp_path):
    reg = MCPRegistry(_store(tmp_path), launcher=make_fake_launcher())
    try:
        servers = {s["name"]: s for s in reg.servers()}
        assert servers["Firecrawl"]["env"]["FIRECRAWL_API_URL"] == \
            "https://firecrawl.data.lmru.tech/"
        git_env = servers["Git"]["env"]
        assert git_env["MCP_TRANSPORT_TYPE"] == "stdio"
        assert git_env["GIT_SIGN_COMMITS"] == "false"
        assert git_env["GIT_BASE_DIR"]
    finally:
        reg.close_all()


def test_registry_connect_and_tools(tmp_path):
    reg = MCPRegistry(_store(tmp_path), launcher=make_fake_launcher())
    try:
        sid = reg.servers()[0]["id"]
        view = reg.connect(sid)
        assert view["status"] == "connected"
        assert view["tools_count"] == 2
        assert view["error"] is None
        tools = reg.tools()
        assert {(t["server"], t["name"]) for t in tools} == \
            {(sid, "mock_echo"), (sid, "mock_ping")}
        assert all("description" in t and "input_schema" in t
                   for t in tools)
        # повторное connect — новый клиент, без дублей
        assert reg.connect(sid)["tools_count"] == 2
        assert len(reg.tools()) == 2
    finally:
        reg.close_all()


def test_registry_connect_failure_is_error_view(tmp_path):
    reg = MCPRegistry(_store(tmp_path),
                      launcher=make_fake_launcher(fail=True))
    try:
        sid = reg.servers()[0]["id"]
        view = reg.connect(sid)
        assert view["status"] == "error"
        assert view["error"]
        assert view["tools_count"] == 0
        assert reg.tools() == []
        # повтор после сбоя — допустим (self-heal)
        assert reg.connect(sid)["status"] == "error"
    finally:
        reg.close_all()


def test_registry_disconnect(tmp_path):
    reg = MCPRegistry(_store(tmp_path), launcher=make_fake_launcher())
    try:
        sid = reg.servers()[0]["id"]
        assert reg.connect(sid)["status"] == "connected"
        view = reg.disconnect(sid)
        assert view["status"] == "idle"
        assert view["error"] is None
        assert view["tools_count"] == 0
        # сервер остаётся в реестре
        s = next(x for x in reg.servers() if x["id"] == sid)
        assert s["status"] == "idle"
        assert s["tools_count"] == 0
        assert reg.tools() == []
        # повторный connect после disconnect — работает
        v2 = reg.connect(sid)
        assert v2["status"] == "connected"
        assert v2["tools_count"] == 2
        # disconnect без сессии (idle) — тоже ok
        reg.disconnect(sid)
        assert reg.servers()[0]["status"] == "idle"
    finally:
        reg.close_all()
    # запись на диске не тронута
    assert sid in _store(tmp_path).mcp_servers_items()


def test_registry_disconnect_unknown_sid(tmp_path):
    reg = MCPRegistry(_store(tmp_path), launcher=make_fake_launcher())
    try:
        with pytest.raises(KeyError):
            reg.disconnect("mcp_nope")
    finally:
        reg.close_all()


def test_registry_connect_unknown_sid(tmp_path):
    reg = MCPRegistry(_store(tmp_path), launcher=make_fake_launcher())
    try:
        with pytest.raises(KeyError):
            reg.connect("mcp_nope")
    finally:
        reg.close_all()


def test_registry_call_tool_success(tmp_path):
    reg = MCPRegistry(_store(tmp_path), launcher=make_fake_launcher())
    try:
        sid = reg.servers()[0]["id"]
        reg.connect(sid)
        result = reg.call_tool(sid, "mock_echo", {"x": "1"})
        assert result["isError"] is False
        assert result["content"][0]["text"] == "{'x': '1'}"
    finally:
        reg.close_all()


def test_registry_call_tool_not_connected(tmp_path):
    reg = MCPRegistry(_store(tmp_path), launcher=make_fake_launcher())
    try:
        sid = reg.servers()[0]["id"]  # в реестре, но idle
        with pytest.raises(MCPError, match="Сервер не подключён"):
            reg.call_tool(sid, "mock_echo")
    finally:
        reg.close_all()


def test_registry_call_tool_unknown_sid(tmp_path):
    reg = MCPRegistry(_store(tmp_path), launcher=make_fake_launcher())
    try:
        with pytest.raises(KeyError):
            reg.call_tool("mcp_nope", "mock_echo")
    finally:
        reg.close_all()


def test_registry_call_tool_unknown_tool(tmp_path):
    reg = MCPRegistry(_store(tmp_path), launcher=make_fake_launcher())
    try:
        sid = reg.servers()[0]["id"]
        reg.connect(sid)
        with pytest.raises(MCPError, match="не найден"):
            reg.call_tool(sid, "mock_nope", {"a": 1})
    finally:
        reg.close_all()


def test_registry_add_remove(tmp_path):
    reg = MCPRegistry(_store(tmp_path), launcher=make_fake_launcher())
    try:
        rec = reg.add("My", "stdio", command=["npx", "-y", "x"])
        assert rec["id"].startswith("mcp_")
        assert rec["name"] == "My"
        sids = [s["id"] for s in reg.servers()]
        assert rec["id"] in sids
        with pytest.raises(ValueError):
            reg.add("Bad", "tcp")
        with pytest.raises(ValueError):
            reg.add("Bad", "stdio")  # без command
        assert reg.remove(rec["id"]) is True
        assert reg.remove(rec["id"]) is False
        assert rec["id"] not in [s["id"] for s in reg.servers()]
    finally:
        reg.close_all()


def test_registry_disabled_server_not_in_tools(tmp_path):
    reg = MCPRegistry(_store(tmp_path), launcher=make_fake_launcher())
    try:
        sid = reg.servers()[0]["id"]
        rec = reg._store.mcp_servers_set(
            sid, "TestSrv", "stdio",
            command=reg._store.mcp_servers_items()[sid]["command"],
            enabled=False)
        reg.connect(sid)
        view = next(s for s in reg.servers() if s["id"] == sid)
        assert view["enabled"] is False
        assert reg.tools() == []
        reg._store.mcp_servers_set(sid, "TestSrv", "stdio",
                                   command=rec["command"], enabled=True)
        assert len(reg.tools()) == 2
    finally:
        reg.close_all()


def test_registry_close_all_resets_runtime(tmp_path):
    reg = MCPRegistry(_store(tmp_path), launcher=make_fake_launcher())
    sid = reg.servers()[0]["id"]
    reg.connect(sid)
    reg.close_all()
    assert reg.servers()[0]["status"] == "idle"
    assert reg.tools() == []
    # реестр на диске не тронут
    assert _store(tmp_path).mcp_servers_items()


# ---------- Task Get / Task Create (день 20): реальные subprocess ----------
# Файлы одиночных серверов сами deliverable — поэтому launcher реальный
# (python studio/mcp_servers/task_get.py / task_create.py), fake-процесс
# не используем. TASKS_FILE указывает на tmp — реальный data/tasks.json
# не трогается (при отсутствии файла сервер сеет TASK-42/TASK-7).

MCP_SERVERS_DIR = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "mcp_servers"))
TASK_GET_PATH = os.path.join(MCP_SERVERS_DIR, "task_get.py")
TASK_CREATE_PATH = os.path.join(MCP_SERVERS_DIR, "task_create.py")


def _task_client(server_path: str, tasks_file: str, name: str) -> MCPClient:
    """Клиент на реальный stdio-процесс task_get.py / task_create.py."""
    server = {"id": "mcp_tg", "name": name, "type": "stdio",
              "command": [sys.executable, server_path], "url": "",
              "env": {"TASKS_FILE": tasks_file}, "enabled": True}
    return MCPClient(server)


def test_task_get_connect_real_subprocess(tmp_path):
    client = _task_client(TASK_GET_PATH, str(tmp_path / "tasks.json"),
                          "Task Get")
    try:
        tools = client.connect()
    finally:
        client.close()
    assert [t["name"] for t in tools] == ["get_task_details"]
    assert all(t["input_schema"]["type"] == "object" for t in tools)
    assert all(t["description"] for t in tools)


def test_task_get_found(tmp_path):
    client = _task_client(TASK_GET_PATH, str(tmp_path / "tasks.json"),
                          "Task Get")
    client.connect()
    try:
        result = client.call_tool("get_task_details",
                                  {"task_id": "TASK-42"})
    finally:
        client.close()
    # _mcp_base ставит isError только при ошибке — успеха = нет ключа.
    assert result.get("isError") is not True
    text = result["content"][0]["text"]
    assert "in_progress" in text
    assert "TASK-42" in text


def test_task_get_not_found(tmp_path):
    client = _task_client(TASK_GET_PATH, str(tmp_path / "tasks.json"),
                          "Task Get")
    client.connect()
    try:
        result = client.call_tool("get_task_details",
                                  {"task_id": "TASK-999"})
    finally:
        client.close()
    assert result["isError"] is True
    assert "не найдена" in result["content"][0]["text"].lower()


def test_task_get_missing_argument(tmp_path):
    client = _task_client(TASK_GET_PATH, str(tmp_path / "tasks.json"),
                          "Task Get")
    client.connect()
    try:
        result = client.call_tool("get_task_details", {})
    finally:
        client.close()
    assert result["isError"] is True
    assert "обязательный" in result["content"][0]["text"].lower()


def test_task_create(tmp_path):
    client = _task_client(TASK_CREATE_PATH, str(tmp_path / "tasks.json"),
                          "Task Create")
    client.connect()
    try:
        result = client.call_tool("create_task", {"title": "E2E-тест"})
    finally:
        client.close()
    # _mcp_base ставит isError только при ошибке — успеха = нет ключа.
    assert result.get("isError") is not True
    task = json.loads(result["content"][0]["text"])
    assert task["id"] == "TASK-43"
    assert task["status"] == "todo"
    assert task["title"] == "E2E-тест"
