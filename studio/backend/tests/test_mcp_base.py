"""Тесты общего каркаса MCP-серверов (день 20)."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(REPO, "studio", "mcp_servers"))
import _mcp_base  # noqa: E402


def _srv(handler=None):
    tool = {"name": "do_thing", "description": "d",
            "input_schema": {"type": "object"}}
    if handler is None:
        handler = lambda args: ({"echo": args}, False)
    return _mcp_base.make_server("test-server", tool, handler)


def test_initialize_protocol_and_serverinfo():
    r = _srv()({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert r["result"]["protocolVersion"] == "2024-11-05"
    assert r["result"]["serverInfo"] == {"name": "test-server",
                                         "version": "1.0"}


def test_tools_list_single_tool():
    r = _srv()({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert [t["name"] for t in r["result"]["tools"]] == ["do_thing"]


def test_tools_call_ok_returns_json_text():
    r = _srv()({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "do_thing", "arguments": {"x": 1}}})
    assert "isError" not in r["result"]
    assert json.loads(r["result"]["content"][0]["text"]) == {"echo": {"x": 1}}


def test_tools_call_error_flag():
    tool = {"name": "t", "description": "", "input_schema": {"type": "object"}}
    h = _mcp_base.make_server("s", tool, lambda a: ({"error": "нет"}, True))
    r = h({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
           "params": {"name": "t", "arguments": {}}})
    assert r["result"]["isError"] is True
    assert json.loads(r["result"]["content"][0]["text"]) == {"error": "нет"}


def test_unknown_tool_name_is_error():
    r = _srv()({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                "params": {"name": "other", "arguments": {}}})
    assert r["result"]["isError"] is True


def test_unknown_method_32601():
    r = _srv()({"jsonrpc": "2.0", "id": 6, "method": "nope"})
    assert r["error"]["code"] == -32601


def test_notification_no_response():
    assert _srv()({"jsonrpc": "2.0",
                   "method": "notifications/initialized"}) is None


def test_handler_exception_wrapped_is_error():
    tool = {"name": "t", "description": "", "input_schema": {"type": "object"}}

    def boom(args):
        raise RuntimeError("упало")

    h = _mcp_base.make_server("s", tool, boom)
    r = h({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
           "params": {"name": "t", "arguments": {}}})
    assert r["result"]["isError"] is True
    assert "Внутренняя ошибка" in r["result"]["content"][0]["text"]
