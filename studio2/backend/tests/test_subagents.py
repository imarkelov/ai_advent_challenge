import json

import pytest

from config import DEFAULT_CONFIG
from llm import LlmClient
from pipeline.artifacts import RunDir
from pipeline.subagents import (AgentReply, ToolNotAllowed, build_messages,
                                check_tool_allowed, parse_reply, run_subagent)
from roles import AgentSpec


class FakeBus:
    def __init__(self):
        self.events = []

    def publish(self, run_id, event):
        self.events.append(event)


def test_parse_reply_valid_json():
    reply = parse_reply('{"status": "done", "artifacts": ["kb/a.md"], "summary": "ok"}')
    assert reply.status == "done"
    assert reply.artifacts == ["kb/a.md"]
    assert reply.summary == "ok"


def test_parse_reply_handles_fenced_json():
    text = ('Ответ:\n```json\n{"status": "need_question", "artifacts": [], '
            '"summary": "", "question": {"text": "Какой экран?"}}\n```')
    reply = parse_reply(text)
    assert reply.status == "need_question"
    assert reply.question["text"] == "Какой экран?"


def test_parse_reply_without_json_is_fail():
    reply = parse_reply("не json")
    assert reply.status == "fail"
    assert "не удалось" in reply.summary


def test_parse_reply_invalid_status_is_fail():
    assert parse_reply('{"status": "weird"}').status == "fail"


def test_parse_reply_artifacts_must_be_list():
    assert parse_reply('{"status": "done", "artifacts": "x"}').artifacts == []


def test_check_tool_allowed_passes_and_raises():
    spec = AgentSpec(role="researcher", allowed_tools=["rag.search"])
    check_tool_allowed(spec, "rag.search")
    with pytest.raises(ToolNotAllowed):
        check_tool_allowed(spec, "gradle.run")


def test_build_messages_includes_sdd_and_context():
    spec = AgentSpec(role="researcher", body="Ты исследователь")
    messages = build_messages(spec, "# SDD — researcher", {"RAG": "- kb/a.md"})
    assert len(messages) == 1
    content = messages[0]["content"]
    assert "# SDD — researcher" in content
    assert "kb/a.md" in content
    assert "JSON" in content


def test_run_subagent_emits_sse_and_returns_reply(tmp_path):
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    for role in cfg["roles"]:
        cfg["roles"][role]["provider"] = "fake"
    run_dir = RunDir(str(tmp_path / "doc"), "r1")
    run_dir.ensure()
    run_dir.write("sdd/agent_researcher.md", "# SDD — researcher")
    bus = FakeBus()
    spec = AgentSpec(role="researcher", model="fake-1", body="Ты исследователь",
                     allowed_tools=["rag.search"])
    reply = run_subagent("researcher", spec, LlmClient(cfg), run_dir, {}, bus, "r1")
    assert isinstance(reply, AgentReply)
    assert reply.status == "done"
    types = [event["type"] for event in bus.events]
    assert types[0] == "agent_started"
    assert "agent_delta" in types
    assert types[-1] == "agent_done"
