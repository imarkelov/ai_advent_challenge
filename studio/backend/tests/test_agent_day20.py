"""Агент (день 20): always-префикс, каталог, cap из env,
сохраняемые имена = LLM-имена."""
import json
import os
import sys
import types

import httpx
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(REPO, "studio", "backend"))
import agent as agent_mod  # noqa: E402
from agent import StudioAgent  # noqa: E402
from conftest import USAGE, delta_chunk, sse_body, usage_chunk  # noqa: E402
from memory import MemoryStore  # noqa: E402

BASE = "https://mock.local/v1"

W = {"server": "mcp_1", "server_name": "weather", "name": "get_weather",
     "description": "Погода по городу", "input_schema": {"type": "object"}}
S = {"server": "mcp_2", "server_name": "digest search", "name": "search",
     "description": "d" * 200, "input_schema": {"type": "object"}}


class FakeMcp:
    def __init__(self, tools, calls=None):
        self._tools = tools
        self.calls = calls if calls is not None else []

    def tools(self):
        return self._tools

    def call_tool(self, sid, name, args):
        self.calls.append((sid, name, args))
        return {"content": [{"type": "text", "text": "ok"}]}


def test_mcp_slug():
    assert agent_mod._mcp_slug("Weather") == "weather"
    assert agent_mod._mcp_slug("News & Weather") == "news_weather"
    assert agent_mod._mcp_slug("digest-make") == "digest_make"
    assert agent_mod._mcp_slug("___") == "server"


def test_llm_tools_always_prefixed():
    fake = types.SimpleNamespace(mcp=FakeMcp([W, S]))
    tools, tool_map = StudioAgent._llm_tools(fake)
    names = [t["function"]["name"] for t in tools]
    assert names == ["weather__get_weather", "digest_search__search"]
    assert tool_map["weather__get_weather"] == ("mcp_1", "get_weather")
    assert tool_map["digest_search__search"] == ("mcp_2", "search")


def test_llm_tools_none_when_empty():
    fake = types.SimpleNamespace(mcp=FakeMcp([]))
    assert StudioAgent._llm_tools(fake) == (None, {})


def test_catalog_block():
    fake = types.SimpleNamespace(mcp=FakeMcp([W, S]))
    text = StudioAgent._mcp_catalog_block(fake)
    assert "- weather (weather): get_weather — Погода по городу" in text
    assert "digest_search__search" not in text  # каталог: real-name
    assert len([l for l in text.splitlines() if l.startswith("- ")]) == 2
    # длинное описание обрезано до 120
    assert "d" * 121 not in text


def test_tool_loop_cap_env(monkeypatch):
    monkeypatch.setenv("TOOL_LOOP_CAP", "7")
    assert agent_mod._tool_loop_cap() == 7
    monkeypatch.setenv("TOOL_LOOP_CAP", "garbage")
    assert agent_mod._tool_loop_cap() == 15
    monkeypatch.delenv("TOOL_LOOP_CAP")
    assert agent_mod._tool_loop_cap() == 15


# ---------- интеграционные (ask_stream, день 20) ----------


def _ready(agent, d):
    """Диалог готов к обычным запросам (профиль declined, день 12)."""
    agent.store.profile_action(d["id"], "decline")


def _day20_handler(always_tool_calls=False):
    """Fake-LLM: non-stream — JSON (авто-название); стрим — tool_call
    (имя берётся из payload["tools"], НЕ хардкодится; args — Самара);
    при role "tool" в истории (и не always_tool_calls) — финальный текст."""
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if "stream" not in payload:
            return httpx.Response(200, json={"choices": [
                {"message": {"content": "E2E"}}]})
        msgs = payload.get("messages") or []
        if not always_tool_calls and any(m.get("role") == "tool"
                                         for m in msgs):
            tool_text = next(m["content"] for m in msgs
                             if m.get("role") == "tool")
            body = sse_body([delta_chunk("Готово: " + tool_text),
                             usage_chunk(), "[DONE]"])
            return httpx.Response(200, content=body.encode("utf-8"))
        llm_name = payload["tools"][0]["function"]["name"]
        tc = {"index": 0, "id": "call_1", "type": "function",
              "function": {"name": llm_name,
                           "arguments": '{"city": "Самара"}'}}
        body = sse_body([
            {"choices": [{"index": 0, "delta": {"tool_calls": [tc]},
                          "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {},
                          "finish_reason": "tool_calls"}],
             "usage": USAGE},
            "[DONE]",
        ])
        return httpx.Response(200, content=body.encode("utf-8"))
    return handler


def _fake_mcp_agent(data_dir, handler, mcp):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return StudioAgent(str(data_dir), base_url=BASE, api_key="test-key",
                       client=client, mcp=mcp)


def test_stored_names_are_llm_names_and_mcp_uses_real(tmp_path, monkeypatch):
    """assistant tool_calls и role "tool" хранят LLM-имя (префикс),
    на MCP-сервер уходит real_name из tool_map."""
    monkeypatch.delenv("TOOL_LOOP_CAP", raising=False)
    d = tmp_path / "data"
    d.mkdir()
    mcp = FakeMcp([W])
    agent = _fake_mcp_agent(d, _day20_handler(), mcp)
    dial = agent.store.new_dialogue()
    _ready(agent, dial)
    events = list(agent.ask_stream(dial["id"], "Какая погода в Самаре?"))
    assert events[-1]["type"] == "done"
    msgs = agent.store.get_messages(dial["id"])
    asst_tc = [m for m in msgs
               if m["role"] == "assistant" and m.get("tool_calls")]
    assert len(asst_tc) == 1
    assert asst_tc[0]["tool_calls"][0]["function"]["name"] \
        == "weather__get_weather"
    tool_msgs = [m for m in msgs if m["role"] == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["name"] == "weather__get_weather"
    assert tool_msgs[0]["tool_call_id"] == "call_1"
    assert mcp.calls == [("mcp_1", "get_weather", {"city": "Самара"})]


def test_tool_loop_cap_hit_yields_error(tmp_path, monkeypatch):
    """Fake-LLM ВСЕГДА отдаёт tool_call, cap из env = 2 → error
    «Tool-loop: превышен лимит итераций (2)», ровно 2 tool-сообщения."""
    monkeypatch.setenv("TOOL_LOOP_CAP", "2")
    d = tmp_path / "data"
    d.mkdir()
    mcp = FakeMcp([W])
    agent = _fake_mcp_agent(d, _day20_handler(always_tool_calls=True), mcp)
    dial = agent.store.new_dialogue()
    _ready(agent, dial)
    events = list(agent.ask_stream(dial["id"], "Погода?"))
    assert events[-1] == {"type": "error",
                          "message": "Tool-loop: превышен лимит итераций (2)"}
    msgs = agent.store.get_messages(dial["id"])
    assert len([m for m in msgs if m["role"] == "tool"]) == 2


def test_catalog_block_in_system_payload(tmp_path, monkeypatch):
    """Каталог MCP-серверов попадает в system-промпт payload, имена
    тулов в каталоге — real-name (без префикса)."""
    monkeypatch.delenv("TOOL_LOOP_CAP", raising=False)
    d = tmp_path / "data"
    d.mkdir()
    mcp = FakeMcp([W])
    agent = _fake_mcp_agent(d, _day20_handler(), mcp)
    dial = agent.store.new_dialogue()
    _ready(agent, dial)
    system = agent.build_payload(dial["id"])[0]["content"]
    assert "- weather (weather): get_weather — Погода по городу" in system
    assert "server__tool" in system  # правило always-префикса
    assert "Каталог MCP-серверов" in system
