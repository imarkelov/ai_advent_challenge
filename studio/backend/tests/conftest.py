"""Общие фикстуры и утилиты тестов (офлайн, без сети)."""
import json
import sys
from pathlib import Path

# backend-каталог в sys.path, чтобы тесты могли импортировать memory/agent/main
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def pytest_configure(config):
    """live — сетевые тесты (день 18): по умолчанию пропускаются
    через -m "not live"; прогоняются явно: -m live."""
    config.addinivalue_line("markers",
                            "live: сетевой тест (нужна сеть), "
                            "пропускается по умолчанию")


def sse_body(chunks: list) -> str:
    """Собирает SSE-тело из чанков (dict или строка "[DONE]")."""
    parts = []
    for c in chunks:
        if c == "[DONE]":
            parts.append("data: [DONE]\n\n")
        else:
            parts.append("data: " + json.dumps(c, ensure_ascii=False) + "\n\n")
    return "".join(parts)


def delta_chunk(text: str) -> dict:
    """SSE-чанк с дельтой контента."""
    return {"id": "c1", "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]}


USAGE = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
         "completion_tokens_details": {"reasoning_tokens": 2}}


def usage_chunk() -> dict:
    """Последний SSE-чанк: finish_reason + usage."""
    return {"id": "c1", "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": USAGE}
