"""Декларации агентов: парсинг front-matter из doc/agents/*.md."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

_LIST_FIELDS = {"allowed_tools", "inputs", "outputs"}
_STR_FIELDS = {"display_name", "model", "stage", "on_no_info"}


@dataclass
class AgentSpec:
    role: str
    display_name: str = ""
    model: str = ""
    temperature: float = 0.2
    stage: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    max_rounds: int = 1
    on_no_info: str = "ask_human"
    body: str = ""
    path: str = ""


def _parse_inline_list(value: str) -> list[str]:
    inner = value.strip()
    if inner.startswith("[") and inner.endswith("]"):
        inner = inner[1:-1]
    return [item.strip() for item in inner.split(",") if item.strip()]


def parse_agent(text: str, path: str = "") -> AgentSpec:
    if not text.startswith("---"):
        raise ValueError(f"нет front-matter в {path or 'агенте'}")
    _, _, rest = text.partition("---")
    meta_text, sep, body = rest.partition("\n---")
    if not sep:
        raise ValueError(f"front-matter не закрыт в {path or 'агенте'}")
    meta: dict[str, str] = {}
    for line in meta_text.strip().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    if not meta.get("role"):
        raise ValueError(f"не задано поле role в {path or 'агенте'}")
    kwargs: dict = {"role": meta["role"], "path": path, "body": body.strip()}
    for key in _STR_FIELDS:
        if key in meta:
            kwargs[key] = meta[key]
    if "temperature" in meta:
        kwargs["temperature"] = float(meta["temperature"])
    if "max_rounds" in meta:
        kwargs["max_rounds"] = int(meta["max_rounds"])
    for key in _LIST_FIELDS:
        if key in meta:
            kwargs[key] = _parse_inline_list(meta[key])
    return AgentSpec(**kwargs)


def load_agents(doc_dir: str) -> dict[str, AgentSpec]:
    agents: dict[str, AgentSpec] = {}
    for path in sorted((Path(doc_dir) / "agents").glob("*.md")):
        spec = parse_agent(path.read_text(encoding="utf-8"), str(path))
        if spec.role == "protocol":
            continue
        agents[spec.role] = spec
    return agents
