"""Тесты ABC ContextStrategy (day10, Task 2) — TDD.

Покрытие:
- ContextStrategy нельзя инстанцировать напрямую (абстрактный);
- минимальная конкретная стратегия: round-trip build_payload
  (текущий user-ход — последний элемент среза), default_state — dict,
  strategy_name — стабильный str;
- on_user_message по умолчанию no-op: state не меняется, llm_call не вызывается.

Офлайн: llm_call — fake (callable str -> str), сети нет, файлов нет.
"""
import pytest

from strategies import ContextStrategy


class _DummyStrategy(ContextStrategy):
    """Минимальная конкретная стратегия для проверки ABC."""

    strategy_name = "dummy"

    def build_payload(self, system_prompt, history, user_input, state):
        # пробрасывает system-промт, срез = вся история + текущий ход
        return system_prompt, list(history) + [{"role": "user", "content": user_input}]

    def default_state(self):
        return {"dummy": 0}


def test_abc_cannot_instantiate():
    """Без concrete-методов ABC не создаётся."""
    with pytest.raises(TypeError):
        ContextStrategy()


def test_subclass_build_payload_roundtrip():
    """build_payload: system_content возвращается, текущий ход — в конце среза."""
    s = _DummyStrategy()
    history = [
        {"role": "user", "content": "вопрос 1"},
        {"role": "assistant", "content": "ответ 1"},
    ]
    system_content, slice_ = s.build_payload("СИСТЕМА", history, "вопрос 2", {})
    assert system_content == "СИСТЕМА"
    assert slice_[:2] == history
    assert slice_[-1] == {"role": "user", "content": "вопрос 2"}


def test_default_state_is_dict():
    assert isinstance(_DummyStrategy().default_state(), dict)


def test_strategy_name_is_str():
    assert isinstance(_DummyStrategy().strategy_name, str)
    assert _DummyStrategy().strategy_name == "dummy"


def test_on_user_message_default_noop():
    """on_user_message без переопределения: state не тронут, llm_call не вызван."""
    s = _DummyStrategy()
    state = s.default_state()
    snapshot = dict(state)
    calls = []

    def fake_llm(text):
        calls.append(text)
        return "ответ"

    s.on_user_message("привет", state, fake_llm)
    assert state == snapshot
    assert calls == []
