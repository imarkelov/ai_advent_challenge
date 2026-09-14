"""Тесты персистентности strategy/strategy_state в dialogues.json (day10, Task 11).

Покрытие (restart round-trip: save → НОВЫЙ SimpleAgent на том же файле):
- test_sticky_facts_survive_restart — sticky-факты переживают «перезапуск»
  с точными значениями;
- test_branching_state_survives_restart — checkpoint (int) / содержимое
  веток A/B / активная ветка переживают «перезапуск»;
- test_legacy_day9_file_backcompat — рукописный dialogues.json day9-формы
  (без ключей strategy/strategy_state) грузится: стратегия "legacy", ask
  работает с инъекцией «Резюме диалога: ...»;
- test_corrupted_state_tolerant — strategy_state не-dict / strategy не из
  STRATEGIES → дефолт/legacy без crash.

Офлайн: urlopen подменяется (паттерн test_compression.install_fake),
файлы только в tmp_path, репозиторий не трогается.
"""
import json
from pathlib import Path

import pytest

from agent import STRATEGIES
from conftest import CANNED_CONTENT
from facts import FACTS_MARKER
from test_compression import install_fake


def make_agent(dialogues_file: str):
    """SimpleAgent на том же dialogues_file («перезапуск сервера»)."""
    from agent import SimpleAgent

    parent = Path(dialogues_file).parent
    return SimpleAgent(
        dialogues_file=dialogues_file,
        history_file=str(parent / "history-restart.json"),
    )


def is_extraction_call(payload) -> bool:
    """Маркер запроса извлечения sticky-фактов (task 9: служебная форма
    temp 0 / max_tokens 300 / thinking off + FACTS_MARKER в промпте)."""
    return (
        payload.get("temperature") == 0
        and payload.get("max_tokens") == 300
        and payload.get("chat_template_kwargs") == {"enable_thinking": False}
        and any(FACTS_MARKER in m["content"] for m in payload["messages"])
    )


def test_sticky_facts_survive_restart(agent_tmp, dialogues_file, monkeypatch):
    """Facts strategy_state переживают «перезапуск» с точными значениями."""
    FACTS = {"цель": "внутренний логистический портал", "стек": "FastAPI"}

    def responder(payload, calls):
        if is_extraction_call(payload):
            return json.dumps(FACTS, ensure_ascii=False)
        return CANNED_CONTENT

    install_fake(monkeypatch, responder)
    agent_tmp.switch_strategy("sticky_facts")
    agent_tmp.ask("первый вопрос")
    agent_tmp.ask("второй вопрос")  # каждый ask() пишет dialogues.json
    assert agent_tmp.get_strategy_info()["facts"] == FACTS  # sanity

    agent2 = make_agent(dialogues_file)  # «перезапуск» на том же файле
    info = agent2.get_strategy_info()
    assert info["strategy"] == "sticky_facts"
    assert info["facts"] == FACTS  # точные значения, не потеряно
    assert info["strategy_state"]["facts"] == FACTS
    assert info["strategy_state"]["window_size"] == 4


def test_branching_state_survives_restart(agent_tmp, dialogues_file, monkeypatch):
    """Checkpoint/ветки A/B/активная ветка переживают «перезапуск»."""
    install_fake(monkeypatch, lambda payload, calls: CANNED_CONTENT)
    a = agent_tmp
    a.switch_strategy("branching")
    a.ask("ход 1")
    a.ask("ход 2")              # история = 4 сообщения
    a.make_checkpoint()         # checkpoint = 4, ветки сброшены
    a.ask("вопрос A")           # A: user + assistant
    a.switch_branch("B")
    a.ask("вопрос B")           # B: user + assistant

    before = a.get_strategy_info()["strategy_state"]

    b = make_agent(dialogues_file)
    st = b.get_strategy_info()["strategy_state"]
    assert b.get_strategy_info()["strategy"] == "branching"
    assert st["checkpoint"] == 4
    assert isinstance(st["checkpoint"], int)
    assert st["active"] == "B"
    assert st["branches"] == before["branches"]
    assert st["branches"]["A"] == [
        {"role": "user", "content": "вопрос A"},
        {"role": "assistant", "content": CANNED_CONTENT},
    ]
    assert st["branches"]["B"] == [
        {"role": "user", "content": "вопрос B"},
        {"role": "assistant", "content": CANNED_CONTENT},
    ]


def test_legacy_day9_file_backcompat(dialogues_file, monkeypatch):
    """day9-файл без strategy/strategy_state грузится как legacy, ask работает."""
    data = {
        "active_id": "d-1",
        "dialogues": [
            {
                "id": "d-1",
                "created_at": "2026-01-01T00:00:00",
                "closed_at": None,
                "messages": [
                    {"role": "user", "content": "вопрос"},
                    {"role": "assistant", "content": "ответ"},
                ],
                "summary": "СВОДКА",  # day9: сводка на диалоге
            }
        ],
    }
    Path(dialogues_file).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    install_fake(monkeypatch, lambda payload, calls: CANNED_CONTENT)

    agent = make_agent(dialogues_file)  # не должно бросить
    assert agent.get_strategy_info()["strategy"] == "legacy"

    result = agent.ask("ещё вопрос")
    assert result["reply"] == CANNED_CONTENT
    sys_msg = agent.get_last_request()["messages"][0]
    assert sys_msg["role"] == "system"
    assert "Резюме диалога: СВОДКА" in sys_msg["content"]  # day9-инъекция
    assert agent._active["summary"] == "СВОДКА"  # сводка не потеряна


@pytest.mark.parametrize(
    "strategy, state, expected",
    [
        ("bogus", None, "legacy"),              # strategy не из STRATEGIES
        (12345, None, "legacy"),                # strategy не-str
        ("sticky_facts", "garbage-string", "sticky_facts"),  # state не-dict
        ("branching", ["не", "dict"], "branching"),          # state не-dict
    ],
)
def test_corrupted_state_tolerant(dialogues_file, monkeypatch, strategy, state, expected):
    """Повреждённые strategy/strategy_state — загрузка без crash, дефолт."""
    dialogue = {
        "id": "d-1",
        "created_at": "2026-01-01T00:00:00",
        "closed_at": None,
        "messages": [{"role": "user", "content": "привет"}],
        "summary": "",
    }
    if strategy is not None:
        dialogue["strategy"] = strategy
    if state is not None:
        dialogue["strategy_state"] = state
    Path(dialogues_file).write_text(
        json.dumps({"active_id": "d-1", "dialogues": [dialogue]}, ensure_ascii=False),
        encoding="utf-8",
    )
    install_fake(monkeypatch, lambda payload, calls: CANNED_CONTENT)

    agent = make_agent(dialogues_file)  # не должно бросить
    info = agent.get_strategy_info()
    assert info["strategy"] == expected
    if expected == "legacy":
        # legacy: live-значения подставляются в копию (сводка с диалога)
        assert info["strategy_state"]["compression_enabled"] is True
        assert info["strategy_state"]["summary"] == ""
    else:
        assert info["strategy_state"] == STRATEGIES[expected]().default_state()
    # ask после повреждённой загрузки работает
    assert agent.ask("вопрос")["reply"] == CANNED_CONTENT
