"""Registry (день 20): 12 дефолтов, миграция старых мультитул-серверов,
server_name в tools()."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(REPO, "studio", "backend"))
import mcp  # noqa: E402
from memory import MemoryStore  # noqa: E402

LOCAL10 = ["weather", "news", "digest_make", "digest_read", "task_create",
           "task_get", "digest_search", "digest_summarize", "file_save",
           "habr_news"]
OLD3 = ("Task Manager", "News & Weather", "Pipeline Tools")
EXPECTED12 = {"Firecrawl", "Git"} | set(LOCAL10)


def make_reg(tmp_path):
    store = MemoryStore(str(tmp_path))
    return mcp.MCPRegistry(store), store


def test_fresh_store_seeds_12_defaults(tmp_path):
    reg, _ = make_reg(tmp_path)
    servers = reg.servers()
    assert len(servers) == 12
    assert {s["name"] for s in servers} == EXPECTED12
    # локальные — stdio-команды python studio/mcp_servers/<name>.py
    by_name = {s["name"]: s for s in servers}
    assert by_name["weather"]["type"] == "stdio"
    assert "weather.py" in by_name["weather"]["command"][-1]


def test_migration_removes_old_keeps_custom_idempotent(tmp_path):
    store = MemoryStore(str(tmp_path))
    for old in OLD3:
        store.mcp_servers_set("mcp_old_" + old, old, "stdio",
                              ["python", "x.py"], "", {}, True)
    store.mcp_servers_set("mcp_keep1", "My Custom", "stdio",
                          ["python", "y.py"], "", {}, True)
    reg = mcp.MCPRegistry(store)
    first = reg.servers()
    names = {s["name"] for s in first}
    for old in OLD3:
        assert old not in names
    assert names == EXPECTED12 | {"My Custom"}
    again = {s["name"] for s in reg.servers()}  # идемпотентность
    assert again == names
    assert len(again) == 13


def test_tools_includes_server_name(tmp_path):
    reg, _ = make_reg(tmp_path)
    assert reg.tools() == []  # никто не подключён
    sid = {s["name"]: s["id"] for s in reg.servers()}["weather"]
    reg._runtime[sid] = {"status": "connected", "error": None,
                         "client": None,
                         "tools": [{"name": "get_weather",
                                    "description": "Погода",
                                    "input_schema": {"type": "object"}}]}
    tools = reg.tools()
    assert len(tools) == 1
    assert tools[0] == {"server": sid, "server_name": "weather",
                        "name": "get_weather", "description": "Погода",
                        "input_schema": {"type": "object"}}
