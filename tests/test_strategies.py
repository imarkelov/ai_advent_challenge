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


# ---------------------------------------------------------------------------
# Task 6: StickyFactsStrategy (день 10) — TDD RED
# ---------------------------------------------------------------------------

from strategies import StickyFactsStrategy  # noqa: E402
from facts import FACT_KEYS  # noqa: E402


def test_sticky_facts_on_user_message_updates_in_place():
    """on_user_message: извлечённые факты сохраняются in-place в state["facts"].

    default_state: {"facts": {}, "window_size": 4}; имя стратегии —
    "sticky_facts"; после хука state["facts"] — ТОТ ЖЕ объект dict (модификация
    на месте, а не подмена ссылки).
    """
    s = StickyFactsStrategy()
    assert s.strategy_name == "sticky_facts"
    state = s.default_state()
    assert state == {"facts": {}, "window_size": 4}
    facts_ref = state["facts"]
    s.on_user_message(
        "делай портал на FastAPI", state,
        lambda text: '{"цель": "портал", "стек": "FastAPI"}',
    )
    assert state["facts"] == {"цель": "портал", "стек": "FastAPI"}
    assert state["facts"] is facts_ref  # in-place, ссылка не поменялась


def test_sticky_facts_one_llm_call_per_user_turn():
    """Каждый пользовательский ход = ровно один LLM-вызов извлечения."""
    s = StickyFactsStrategy()
    state = s.default_state()
    counters = []
    for i in range(3):
        counters.append([0])

        def fake(text, _n=counters[-1]):
            _n[0] += 1
            return '{"цель": "портал"}'
        s.on_user_message(f"ход {i}", state, fake)
    assert sum(c[0] for c in counters) == 3
    assert all(c[0] == 1 for c in counters)


def test_sticky_facts_build_payload_with_facts():
    """Факты есть: system_content обогащён блоком «Актуальные факты:»,
    срез = последние N сообщений + текущий ход последним."""
    s = StickyFactsStrategy()
    state = s.default_state()
    state["facts"] = {"цель": "портал", "стек": "FastAPI"}
    history = [
        {"role": "user", "content": "вопрос 1"},
        {"role": "assistant", "content": "ответ 1"},
        {"role": "user", "content": "вопрос 2"},
        {"role": "assistant", "content": "ответ 2"},
    ]
    system_content, slice_ = s.build_payload("СИСТЕМА", history, "вопрос 3", state)
    assert "Актуальные факты:" in system_content
    assert "- цель: портал" in system_content
    assert system_content.startswith("СИСТЕМА")
    # срез: последние window_size=4 сообщения + текущий ход
    assert slice_[-1] == {"role": "user", "content": "вопрос 3"}
    assert slice_[:-1] == history[-4:]


def test_sticky_facts_build_payload_empty_facts():
    """Фактов нет: system_content == system_prompt байт-в-байт (никакого блока,
    ни одного лишнего перевода строки)."""
    s = StickyFactsStrategy()
    state = s.default_state()
    assert state["facts"] == {}
    system_content, _ = s.build_payload("СИСТЕМА", [], "вопрос", state)
    assert system_content == "СИСТЕМА"


def test_sticky_facts_llm_error_keeps_old_facts():
    """API упало (RuntimeError из llm_call): прежние факты не меняются,
    исключение наружу не выходит (есть фолбэк в facts.extract_facts)."""
    s = StickyFactsStrategy()
    state = s.default_state()
    state["facts"] = {"цель": "портал"}

    def broken(text):
        raise RuntimeError("API упал")

    s.on_user_message("ход", state, broken)
    assert state["facts"] == {"цель": "портал"}


def test_sticky_facts_malformed_json_keeps_old_facts():
    """LLM ответила не-JSON: прежние факты не меняются, исключений нет."""
    s = StickyFactsStrategy()
    state = s.default_state()
    state["facts"] = {"стек": "FastAPI"}
    s.on_user_message("ход", state, lambda text: "не json")
    assert state["facts"] == {"стек": "FastAPI"}


def test_sticky_facts_block_order_follows_fact_keys():
    """Порядок строк блока = порядок FACT_KEYS (цель раньше стека),
    независимо от порядка установки в dict."""
    s = StickyFactsStrategy()
    state = s.default_state()
    # стек установлен раньше цели, но в FACT_KEYS цель на первом месте
    state["facts"] = {"стек": "FastAPI", "цель": "портал"}
    system_content, _ = s.build_payload("СИСТЕМА", [], "вопрос", state)
    i_goal = system_content.index("- цель: портал")
    i_stack = system_content.index("- стек: FastAPI")
    assert i_goal < i_stack
    assert FACT_KEYS.index("цель") < FACT_KEYS.index("стек")


# ---------------------------------------------------------------------------
# Task 8: LegacyStrategy (день 10) — TDD RED
# ---------------------------------------------------------------------------

from strategies import LegacyStrategy  # noqa: E402


def test_legacy_is_context_strategy():
    """LegacyStrategy — конкретный подкласс ABC со стабильным именем."""
    s = LegacyStrategy()
    assert isinstance(s, ContextStrategy)
    assert s.strategy_name == "legacy"


def test_legacy_default_state():
    """default_state: сводки ещё нет, сжатие включено (day9 по умолчанию)."""
    state = LegacyStrategy().default_state()
    assert isinstance(state, dict)
    assert state == {"summary": None, "compression_enabled": True}


def test_legacy_build_payload_with_summary_exact():
    """Сводка есть + сжатие вкл: day9-инъекция в system-промт БАЙТ-В-БАЙТ
    (одна строка «Резюме диалога:», без фейковых реплик); срез = ВЕСЬ
    история + текущий ход последним (окно НЕ применяется срезом)."""
    s = LegacyStrategy()
    history = _history_12()
    state = s.default_state()
    state["summary"] = "СВОДКА: зафиксированы решения."
    system_content, slice_ = s.build_payload("СИСТЕМА", history, "вопрос 7", state)
    assert system_content == "СИСТЕМА" + "\n\nРезюме диалога: " + "СВОДКА: зафиксированы решения."
    # срез: вся история (12) + текущий ход = 13, без окна
    assert len(slice_) == 13
    assert slice_[:12] == history
    assert slice_[-1] == {"role": "user", "content": "вопрос 7"}


def test_legacy_build_payload_compression_off_no_summary_injection():
    """Сводка есть, но сжатие выключено (режим day8): system_content ==
    system_prompt байт-в-байт, инъекции нет."""
    s = LegacyStrategy()
    state = s.default_state()
    state["summary"] = "СВОДКА"
    state["compression_enabled"] = False
    system_content, _ = s.build_payload("СИСТЕМА", [], "вопрос", state)
    assert system_content == "СИСТЕМА"


def test_legacy_build_payload_no_summary_full_history():
    """Сводки нет (None и ""): system_content == system_prompt; срез =
    ВЕСЬ история + текущий ход последним; исходная история не мутируется."""
    s = LegacyStrategy()
    history = _history_12()
    snapshot = [dict(m) for m in history]
    for empty in (None, ""):
        state = s.default_state()
        state["summary"] = empty
        system_content, slice_ = s.build_payload("СИСТЕМА", history, "вопрос 7", state)
        assert system_content == "СИСТЕМА"
        assert len(slice_) == 13
        assert slice_[:12] == history
        assert slice_[-1] == {"role": "user", "content": "вопрос 7"}
    assert history == snapshot  # non-destructive


def test_legacy_on_user_message_default_noop():
    """on_user_message НЕ переопределён: no-op по умолчанию (сжатие —
    движок агента, не стратегии); state не тронут, llm_call не вызван."""
    s = LegacyStrategy()
    state = s.default_state()
    snapshot = dict(state)
    calls = []

    def fake_llm(text):
        calls.append(text)
        return "ответ"

    s.on_user_message("ход", state, fake_llm)
    assert state == snapshot
    assert calls == []
