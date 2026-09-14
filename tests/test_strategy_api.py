"""Стратегии контекста в SimpleAgent + strategy-конфиг (day10, Task 9) — TDD.

Покрытие:
- configure(strategy=...) — стратегия по умолчанию для НОВЫХ диалогов;
- дефолт (без config) — legacy-поведение day9: «Резюме диалога» в system
  после триггера сжатия;
- смена на sticky_facts посреди диалога: блок «Актуальные факты:» в system,
  извлекательные вызовы идут через llm_call (форма сводочного запроса +
  FACTS_MARKER), /agent/last-request НЕ перезаписывается извлечением;
- sliding_window: payload = 1 system + последние 4 + текущий user,
  история диалога на диске не мутируется (non-destructive);
- branching: make_checkpoint → checkpoint == len(history), ветка A растёт,
  switch_branch("B") → изоляция (A-ходы в B-payload не попадают), ответ
  ассистента — и в основную историю, и в активную ветку;
- валидация: ValueError на неизвестную стратегию/ветку, чекпоинт/ветка
  для не-branching диалога;
- get_config()["strategy"], get_token_stats() — ровно 3 ключа.

Офлайн: urlopen подменяется (паттерн tests/test_compression.py), файлы в
tmp_path. Сводочный и извлекательный запросы отличаются формой
(temperature=0 + max_tokens=300 + enable_thinking=False) и RU-маркером
промпта («Сожми» / FACTS_MARKER из facts.py).
"""
import io
import json

import pytest

from conftest import CANNED_CONTENT
from facts import FACTS_MARKER

# то, что fake возвращает на служебные запросы
SUMMARY_TEXT = "СВОДКА: решения по миграции зафиксированы."
FACTS_JSON = json.dumps(
    {"цель": "портал логистики", "стек": "FastAPI"}, ensure_ascii=False
)


def _service_shape(payload) -> bool:
    """Форма служебного (текстового) запроса: temp 0, max_tokens 300,
    thinking выключен — та же форма, что у day9-сводки."""
    return (
        payload.get("temperature") == 0
        and payload.get("max_tokens") == 300
        and payload.get("chat_template_kwargs") == {"enable_thinking": False}
    )


def is_summary_call(payload) -> bool:
    """Сводочный запрос day9 (маркер «Сожми» в промпте)."""
    return _service_shape(payload) and any(
        "Сожми" in m["content"] for m in payload["messages"]
    )


def is_extraction_call(payload) -> bool:
    """Извлекательный запрос sticky-фактов (FACTS_MARKER в промпте)."""
    return _service_shape(payload) and any(
        FACTS_MARKER in m["content"] for m in payload["messages"]
    )


def install_fake(monkeypatch):
    """Подмена urlopen: сводка -> SUMMARY_TEXT, извлечение -> FACTS_JSON,
    обычный ask -> CANNED_CONTENT. Возвращает список распарсенных payload."""
    calls = []

    def _fake(req, timeout=None):
        payload = json.loads(req.data.decode("utf-8"))
        calls.append(payload)
        if is_summary_call(payload):
            content = SUMMARY_TEXT
        elif is_extraction_call(payload):
            content = FACTS_JSON
        else:
            content = CANNED_CONTENT
        body = {
            "choices": [
                {"message": {"role": "assistant", "content": content,
                             "reasoning": None}}
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 3,
                "total_tokens": 13,
                "completion_tokens_details": {"reasoning_tokens": 0},
            },
        }
        return io.BytesIO(json.dumps(body, ensure_ascii=False).encode("utf-8"))

    monkeypatch.setattr("urllib.request.urlopen", _fake)
    monkeypatch.setenv("GPUSTACK_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("GPUSTACK_API_KEY", "test-key")
    return calls


def seed(agent, n):
    """Посадить n сообщений в активный диалог in-place."""
    agent.history.extend(
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"сообщение {i}"}
        for i in range(n)
    )


# ---------------------------------------------------------------------------
# 1. configure(strategy=...) — стратегия новых диалогов
# ---------------------------------------------------------------------------

def test_configure_sliding_window_new_dialogue(agent_tmp, monkeypatch):
    """strategy=sliding_window: payload = 1 system + последние 4 + user,
    без day9-инъекции «Резюме диалога»."""
    agent_tmp.configure(strategy="sliding_window")
    agent_tmp.new_dialogue()  # стратегия по умолчанию — для НОВЫХ диалогов
    calls = install_fake(monkeypatch)
    for i in range(5):
        agent_tmp.ask(f"ход {i}")
    msgs = agent_tmp.get_last_request()["messages"]
    assert msgs[0]["role"] == "system"
    assert "Резюме диалога" not in msgs[0]["content"]
    assert len(msgs) == 6  # 1 system + 4 окна + 1 user
    assert msgs[-1] == {"role": "user", "content": "ход 4"}
    # срез = последние 4 сообщения истории до текущего хода (ходы 2 и 3)
    assert [m["content"] for m in msgs[1:5]] == [
        "ход 2", CANNED_CONTENT, "ход 3", CANNED_CONTENT
    ]
    assert not any(is_summary_call(c) for c in calls)


# ---------------------------------------------------------------------------
# 2. Дефолт (без config) — legacy day9
# ---------------------------------------------------------------------------

def test_default_legacy_summary_injection(agent_tmp, monkeypatch):
    """Без configure: legacy — после триггера сжатия system содержит
    «Резюме диалога: ...» (day9-поведение сохранено)."""
    agent_tmp.configure(window_size=3, summary_gap=2, compression_enabled=True)
    install_fake(monkeypatch)
    seed(agent_tmp, 10)  # 10 - 3 = 7 >= 2 — сжатие сработает
    agent_tmp.ask("вопрос")
    msgs = agent_tmp.get_last_request()["messages"]
    assert "Резюме диалога" in msgs[0]["content"]
    assert SUMMARY_TEXT in msgs[0]["content"]


# ---------------------------------------------------------------------------
# 3. Смена на sticky_facts посреди диалога
# ---------------------------------------------------------------------------

def test_switch_to_sticky_facts_mid_dialogue(agent_tmp, monkeypatch):
    """2 хода legacy → switch_strategy("sticky_facts") → следующий ask:
    в system блок «Актуальные факты:», ровно 1 извлекательный вызов
    (за ход после смены), last-request — МАЙН-пейлоад, не извлечение."""
    calls = install_fake(monkeypatch)
    agent_tmp.ask("ход 1")
    agent_tmp.ask("ход 2")
    info = agent_tmp.switch_strategy("sticky_facts")
    assert info["strategy"] == "sticky_facts"
    assert info["facts"] == {}
    agent_tmp.ask("ход 3: цель — корпоративный портал")
    msgs = agent_tmp.get_last_request()["messages"]
    assert "Актуальные факты:" in msgs[0]["content"]
    assert "- цель: портал логистики" in msgs[0]["content"]
    assert "- стек: FastAPI" in msgs[0]["content"]
    # извлекательные вызовы — только ходы после смены (1 ход)
    assert sum(is_extraction_call(c) for c in calls) == 1
    # /agent/last-request НЕ перезаписан извлечением:
    lr = agent_tmp.get_last_request()
    assert not is_extraction_call(lr)
    assert not is_summary_call(lr)
    assert lr["messages"][-1] == {"role": "user", "content": "ход 3: цель — корпоративный портал"}
    assert "temperature" not in lr  # у основного ask temperature=None
    # факты живут в state диалога
    assert agent_tmp.get_strategy_info()["facts"] == {
        "цель": "портал логистики", "стек": "FastAPI"
    }


# ---------------------------------------------------------------------------
# 4. sliding_window — non-destructive
# ---------------------------------------------------------------------------

def test_sliding_window_non_destructive(agent_tmp, monkeypatch):
    """После 6 ходов: payload = 6 сообщений (1 system + 4 + 1 user),
    история диалога на диске не тронута (12 сообщений)."""
    agent_tmp.configure(strategy="sliding_window")
    agent_tmp.new_dialogue()  # стратегия по умолчанию — для НОВЫХ диалогов
    install_fake(monkeypatch)
    for i in range(6):
        agent_tmp.ask(f"q{i}")
    msgs = agent_tmp.get_last_request()["messages"]
    assert len(msgs) == 6  # 1 system + 4 окна + 1 user
    assert msgs[-1] == {"role": "user", "content": "q5"}
    history = agent_tmp.get_history()
    assert len(history) == 12  # 6 ходов × 2, trim/удалений не было
    # срез — последние 4 сообщения истории ДО добавления пары текущего хода
    # (history[-4:] здесь — уже пара последнего хода)
    assert [m["content"] for m in msgs[1:5]] == [
        m["content"] for m in history[-6:-2]
    ]


# ---------------------------------------------------------------------------
# 5. branching: чекпоинт + изоляция A/B
# ---------------------------------------------------------------------------

def test_branching_checkpoint_and_isolation(agent_tmp, monkeypatch):
    """checkpoint == len(history); A растёт; switch_branch("B") — A-ходы
    в B-payload не попадают; assistant-ответ — в основную историю И в
    активную ветку."""
    install_fake(monkeypatch)
    agent_tmp.ask("q1")
    agent_tmp.ask("q2")  # история = 4
    agent_tmp.switch_strategy("branching")
    state = agent_tmp.make_checkpoint()
    assert state["checkpoint"] == len(agent_tmp.get_history()) == 4
    assert state["active"] == "A"

    agent_tmp.ask("A-1")
    agent_tmp.ask("A-2")
    info = agent_tmp.get_strategy_info()
    # A-ветка: user-ходы on_user_message + assistant-ответы агентом
    assert [m["content"] for m in info["strategy_state"]["branches"]["A"]] == [
        "A-1", CANNED_CONTENT, "A-2", CANNED_CONTENT
    ]

    state = agent_tmp.switch_branch("B")
    assert state["active"] == "B"
    agent_tmp.ask("B-1")
    msgs = agent_tmp.get_last_request()["messages"]
    # изоляция: A-ходы в B-payload отсутствуют
    assert not any(m["content"] in ("A-1", "A-2") for m in msgs)
    assert msgs[-1] == {"role": "user", "content": "B-1"}
    # преамбула до чекпоинта в payload есть
    assert msgs[1]["content"] == "q1"

    history = agent_tmp.get_history()
    assert history[-1] == {"role": "assistant", "content": CANNED_CONTENT}
    state = agent_tmp.get_strategy_info()["strategy_state"]
    assert state["branches"]["A"][-1] == {"role": "assistant", "content": CANNED_CONTENT}
    assert state["branches"]["B"] == [
        {"role": "user", "content": "B-1"},
        {"role": "assistant", "content": CANNED_CONTENT},
    ]
    # основная история non-destructive: 4 + 2+2 (A) + 2 (B) = 10
    assert len(history) == 10


# ---------------------------------------------------------------------------
# 6. Валидация — ValueError
# ---------------------------------------------------------------------------

def test_validation_errors(agent_tmp):
    with pytest.raises(ValueError):
        agent_tmp.switch_strategy("bogus")
    agent_tmp.switch_strategy("branching")
    with pytest.raises(ValueError):
        agent_tmp.switch_branch("C")
    agent_tmp.switch_strategy("legacy")
    with pytest.raises(ValueError):
        agent_tmp.make_checkpoint()  # не branching
    with pytest.raises(ValueError):
        agent_tmp.switch_branch("A")  # не branching
    with pytest.raises(ValueError):
        agent_tmp.configure(strategy="нет-такой")
    # после ошибок валидации активный диалог цел
    assert agent_tmp.get_strategy_info()["strategy"] == "legacy"


# ---------------------------------------------------------------------------
# 7. get_config / get_token_stats
# ---------------------------------------------------------------------------

def test_config_strategy_and_token_stats_shape(agent_tmp, monkeypatch):
    """get_config отдаёт "strategy" (дефолт "legacy", после configure —
    новое значение); get_token_stats() — ровно 3 ключа (заморожено)."""
    assert agent_tmp.get_config()["strategy"] == "legacy"
    agent_tmp.configure(strategy="branching")
    assert agent_tmp.get_config()["strategy"] == "branching"
    install_fake(monkeypatch)
    agent_tmp.ask("q")
    stats = agent_tmp.get_token_stats()
    assert set(stats) == {"history_tokens", "reply_tokens", "summary_tokens"}
