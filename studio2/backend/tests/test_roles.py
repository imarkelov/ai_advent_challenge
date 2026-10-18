from pathlib import Path

import pytest

from roles import AgentSpec, load_agents, parse_agent

SAMPLE = """---
role: researcher
display_name: Исследователь
model: qwen3.5:9b
temperature: 0.2
stage: study
allowed_tools: [git_ops.read_branch, rag.search, rag.write]
inputs: [sdd/agent_researcher.md]
outputs: [kb/architecture.md]
max_rounds: 2
on_no_info: ask_human
---

## Роль
Ты — Исследователь.
"""

DOC_DIR = Path(__file__).resolve().parents[2] / "doc"


def test_parse_agent_front_matter():
    spec = parse_agent(SAMPLE, "researcher.md")
    assert isinstance(spec, AgentSpec)
    assert spec.role == "researcher"
    assert spec.display_name == "Исследователь"
    assert spec.model == "qwen3.5:9b"
    assert spec.temperature == 0.2
    assert spec.stage == "study"
    assert spec.allowed_tools == ["git_ops.read_branch", "rag.search", "rag.write"]
    assert spec.inputs == ["sdd/agent_researcher.md"]
    assert spec.outputs == ["kb/architecture.md"]
    assert spec.max_rounds == 2
    assert spec.on_no_info == "ask_human"
    assert "Ты — Исследователь." in spec.body


def test_parse_agent_rejects_missing_front_matter():
    with pytest.raises(ValueError):
        parse_agent("no front matter")


def test_parse_agent_rejects_missing_role():
    with pytest.raises(ValueError):
        parse_agent("---\ndisplay_name: X\n---\nbody")


def test_load_agents_reads_repository_docs():
    agents = load_agents(str(DOC_DIR))
    assert set(agents) == {
        "qa-lead", "researcher", "testwriter", "debugger", "reviewer", "device-inspector"
    }
    assert agents["qa-lead"].stage == "sdd"
    assert agents["reviewer"].model == "gemma4:12b"
    assert agents["testwriter"].model != agents["reviewer"].model


def test_agent_bodies_are_real_prompts():
    for spec in load_agents(str(DOC_DIR)).values():
        assert len(spec.body) > 200, spec.role
        assert spec.allowed_tools, spec.role
