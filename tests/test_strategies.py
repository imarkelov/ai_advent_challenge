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


# ---------------------------------------------------------------------------
# Task 5: SlidingWindowStrategy (день 10) — TDD RED
# ---------------------------------------------------------------------------

from strategies import SlidingWindowStrategy  # noqa: E402


def _history_12():
    """12 сообщений: 6 пар user+assistant (после 6 пользовательских ходов)."""
    history = []
    for i in range(1, 7):
        history.append({"role": "user", "content": f"вопрос {i}"})
        history.append({"role": "assistant", "content": f"ответ {i}"})
    return history


def test_sliding_window_is_context_strategy():
    """SlidingWindowStrategy — конкретный подкласс ABC со стабильным именем."""
    s = SlidingWindowStrategy()
    assert isinstance(s, ContextStrategy)
    assert s.strategy_name == "sliding_window"


def test_sliding_window_default_state():
    """default_state: window_size = 4 (день 10, S1-спека)."""
    state = SlidingWindowStrategy().default_state()
    assert isinstance(state, dict)
    assert state["window_size"] == 4


def test_sliding_window_slices_last_n():
    """N=4: срез = последние 4 сообщения истории + текущий ход последним."""
    s = SlidingWindowStrategy()
    history = _history_12()
    system_content, slice_ = s.build_payload(
        "СИСТЕМА", history, "вопрос 7", s.default_state()
    )
    # system_content — исходный промт, БЕЗ сводки
    assert system_content == "СИСТЕМА"
    # срез: 4 последних сообщения истории + 1 текущий ход
    assert len(slice_) == 5
    assert slice_[-1] == {"role": "user", "content": "вопрос 7"}
    assert slice_[:-1] == history[-4:]
    # полный payload = 1 system + 4 истории + 1 user = 6 сообщений
    assert 1 + len(slice_) == 6


def test_sliding_window_non_destructive():
    """Срез — новый список: исходная история не мутируется и не удаляется."""
    s = SlidingWindowStrategy()
    history = _history_12()
    snapshot = [dict(m) for m in history]
    s.build_payload("СИСТЕМА", history, "вопрос 7", s.default_state())
    assert len(history) == len(snapshot)
    assert history == snapshot


def test_sliding_window_state_override():
    """state["window_size"] побеждает: N=2 → срез = 2 истории + 1 ход."""
    s = SlidingWindowStrategy()
    history = _history_12()
    _, slice_ = s.build_payload("СИСТЕМА", history, "вопрос 7", {"window_size": 2})
    assert len(slice_) == 3
    assert slice_[:-1] == history[-2:]
    assert slice_[-1] == {"role": "user", "content": "вопрос 7"}


def test_sliding_window_short_history():
    """История короче окна: срез = вся история + текущий ход (без падения)."""
    s = SlidingWindowStrategy()
    history = [
        {"role": "user", "content": "вопрос 1"},
        {"role": "assistant", "content": "ответ 1"},
    ]
    _, slice_ = s.build_payload(
        "СИСТЕМА", history, "вопрос 2", s.default_state()
    )
    assert len(slice_) == 3
    assert slice_[:2] == history
    assert slice_[-1] == {"role": "user", "content": "вопрос 2"}
