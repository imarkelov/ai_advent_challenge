"""Запуск субагента: промпт, вызов LLM, разбор JSON-ответа, SSE-события."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from roles import AgentSpec

VALID_STATUSES = {"done", "need_question", "fail"}
JSON_RE = re.compile(r"\{[\s\S]*\}")


class ToolNotAllowed(RuntimeError):
    pass


@dataclass
class AgentReply:
    status: str
    artifacts: list[str] = field(default_factory=list)
    summary: str = ""
    question: dict | None = None


def check_tool_allowed(spec: AgentSpec, tool: str) -> None:
    if tool not in spec.allowed_tools:
        raise ToolNotAllowed(f"роль {spec.role} не может вызывать {tool}")


def parse_reply(text: str) -> AgentReply:
    match = JSON_RE.search(text or "")
    if not match:
        return AgentReply(status="fail", summary="не удалось разобрать ответ агента: нет JSON")
    try:
        payload = json.loads(match.group(0))
    except ValueError:
        return AgentReply(status="fail", summary="не удалось разобрать JSON ответа агента")
    if not isinstance(payload, dict):
        return AgentReply(status="fail", summary="ответ агента не является объектом JSON")
    status = payload.get("status", "fail")
    if status not in VALID_STATUSES:
        return AgentReply(status="fail", summary=f"недопустимый статус: {status}")
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list):
        artifacts = []
    question = payload.get("question")
    return AgentReply(
        status=status,
        artifacts=[str(item) for item in artifacts],
        summary=str(payload.get("summary") or ""),
        question=question if isinstance(question, dict) else None,
    )


def build_messages(spec: AgentSpec, sdd: str, context: dict) -> list[dict]:
    parts = [f"# SDD\n{sdd}"]
    for name, value in context.items():
        if value:
            parts.append(f"# {name}\n{value}")
    parts.append("Ответь строго одним JSON-объектом по формату из SDD.")
    return [{"role": "user", "content": "\n\n".join(parts)}]


def run_subagent(role: str, spec: AgentSpec, llm, run_dir, context: dict,
                 bus, run_id: str) -> AgentReply:
    sdd = run_dir.read(f"sdd/agent_{role}.md")
    bus.publish(run_id, {"type": "agent_started", "role": role, "model": spec.model})
    chunks = []
    for chunk in llm.stream(role, build_messages(spec, sdd, context), system=spec.body):
        chunks.append(chunk)
        bus.publish(run_id, {"type": "agent_delta", "role": role, "text": chunk})
    reply = parse_reply("".join(chunks))
    bus.publish(run_id, {"type": "agent_done", "role": role,
                         "status": reply.status, "summary": reply.summary})
    return reply
