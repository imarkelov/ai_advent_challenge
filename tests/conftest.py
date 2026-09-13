"""Фикстуры pytest для тестов SimpleAgent (day9, Task 4).

Тесты полностью офлайн: сетевой вызов подменяется (urlopen),
диалоги живут в tmp_path — файлы репозитория не трогаются.
"""
import io
import json
import sys
from pathlib import Path

import pytest

# agent.py лежит в корне репозитория — добавить в sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import SimpleAgent  # noqa: E402

# Canned-контент, который возвращает fake_urlopen
CANNED_CONTENT = "Тестовый ответ модели (canned)."


@pytest.fixture()
def dialogues_file(tmp_path):
    """Путь к dialogues.json в tmp — тесты никогда не трогают репозиторий."""
    return str(tmp_path / "dialogues.json")


@pytest.fixture()
def agent_tmp(tmp_path, dialogues_file):
    """SimpleAgent на tmp-файлах (dialogues + history) — изоляция от репозитория.

    history_file тоже в tmp: иначе при отсутствии dialogues.json агент
    ушла бы читать legacy history.json из корня репозитория.
    """
    return SimpleAgent(
        dialogues_file=dialogues_file,
        history_file=str(tmp_path / "history.json"),
    )


@pytest.fixture()
def fake_urlopen(monkeypatch):
    """Подмена urllib.request.urlopen — canned OpenAI-совместимый ответ.

    - Перехватывает POST /chat/completions: сеть не используется.
    - Возвращаемый объект — io.BytesIO (поддерживает контекст-менеджер
      `with urlopen(...) as resp` и .read() -> bytes, как ждёт agent.ask()).
    - JSON повторяет форму, которую парсит ask():
      choices[0].message.content + usage.prompt_tokens/completion_tokens.
    - Заодно ставит GPUSTACK_BASE_URL и GPUSTACK_API_KEY (ask() проверяет
      их до urlopen; значения фиктивные, сети нет).

    Возвращает список распарсенных payload-ов запросов (для инспекций).
    """
    calls = []

    def _fake(req, timeout=None):
        calls.append(json.loads(req.data.decode("utf-8")))
        body = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": CANNED_CONTENT,
                        "reasoning": None,
                    }
                }
            ],
            "usage": {
                "prompt_tokens": 42,
                "completion_tokens": 7,
                "total_tokens": 49,
                "completion_tokens_details": {"reasoning_tokens": 0},
            },
        }
        return io.BytesIO(json.dumps(body).encode("utf-8"))

    monkeypatch.setattr("urllib.request.urlopen", _fake)
    monkeypatch.setenv("GPUSTACK_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("GPUSTACK_API_KEY", "test-key")
    return calls
