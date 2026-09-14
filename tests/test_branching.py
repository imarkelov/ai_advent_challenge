"""Тесты BranchingStrategy (day10, Task 7) — TDD.

Покрытие (протокол бенчмарка S3, Task 12):
- default_state: checkpoint=None, обе ветки пустые, active="A";
- режим БЕЗ чекпоинта: build_payload — полная история + текущий ход;
- чекпоинт@12 (после 6 ходов): срез = общая преамбула history[:12]
  + активная ветка, текущий user-ход ПОСЛЕДНИМ (on_user_message кладёт
  его в ветку ДО build_payload — контракт порядка вызовов);
- изоляция веток: в срезе B нет НИ ОДНОГО сообщения из хода 7-9 ветки A;
- возврат в A: срез A снова содержит ходы 7-9;
- implicit-ветвление: branch() при checkpoint=None ставит первый чекпоинт;
- switch_branch на не-A/B → ValueError;
- on_user_message ДО чекпоинта — no-op (ветки остаются пустыми).

Контракт порядка вызовов (обязателен для агента, Task 9): агент ВСЕГДА
вызывает ``on_user_message(...)`` ПЕРЕД ``build_payload(...)``. Для S3
on_user_message кладёт user-ход в активную ветку, поэтому build_payload
НЕ добавляет его повторно; ответ ассистента агент сам дописывает и в
основную историю, и (только S3) в ``state["branches"][active]`` под
своим локом.

Офлайн: llm_call=None (ветвление извлечения не делает), сети нет, файлов нет.
"""
import pytest

from strategies import BranchingStrategy, ContextStrategy


def _history_12():
    """12 сообщений: 6 пар user+assistant (после 6 пользовательских ходов)."""
    history = []
    for i in range(1, 7):
        history.append({"role": "user", "content": f"вопрос {i}"})
        history.append({"role": "assistant", "content": f"ответ {i}"})
    return history


# ---------------------------------------------------------------------------
# 1. default_state / имя стратегии
# ---------------------------------------------------------------------------

def test_branching_default_state():
    """default_state: checkpoint=None, обе ветки пустые, active='A'."""
    s = BranchingStrategy()
    assert isinstance(s, ContextStrategy)
    assert s.strategy_name == "branching"
    assert s.default_state() == {
        "checkpoint": None,
        "branches": {"A": [], "B": []},
        "active": "A",
    }


# ---------------------------------------------------------------------------
# 2. Режим без чекпоинта — полная история
# ---------------------------------------------------------------------------

def test_branching_no_checkpoint_full_history():
    """checkpoint=None: срез = ВЕСЬ история + текущий ход последним
    (ни окна, ни ветки). system-промт пробрасывается без изменений."""
    s = BranchingStrategy()
    state = s.default_state()
    history = _history_12()
    snapshot = [dict(m) for m in history]
    system_content, slice_ = s.build_payload("СИСТЕМА", history, "вопрос 7", state)
    assert system_content == "СИСТЕМА"
    assert slice_[:-1] == history
    assert slice_[-1] == {"role": "user", "content": "вопрос 7"}
    # non-destructive: история не мутируется
    assert history == snapshot


# ---------------------------------------------------------------------------
# 3. Протокол чекпоинт@12 (зеркало бенчмарка S3): ходы 7-9 в ветке A
# ---------------------------------------------------------------------------

def test_branching_checkpoint_protocol_turns_7_9():
    """checkpoint(12): ходы 7-9 в ветке A.

    Порядок на каждый ход (контракт агента): on_user_message(turn) ПЕРЕД
    build_payload — user-ход оказывается в ветке A, поэтому срез =
    history[:12] + сообщения A на данный момент, user-ход ПОСЛЕДНИМ.
    Ответ ассистента ДОПИСЫВАЕТ АГЕНТ (Task 9) — здесь симулируем прямой
    append в state["branches"]["A"] (дело агента, не стратегии).
    """
    s = BranchingStrategy()
    state = s.default_state()
    history = _history_12()
    s.checkpoint(state, 12)
    assert state["checkpoint"] == 12
    assert state["branches"] == {"A": [], "B": []}
    assert state["active"] == "A"

    for turn in ("вопрос 7", "вопрос 8", "вопрос 9"):
        s.on_user_message(turn, state, None)
        system_content, slice_ = s.build_payload("СИСТЕМА", history, turn, state)
        assert system_content == "СИСТЕМА"
        assert slice_[:12] == history  # общая преамбула до чекпоинта
        assert slice_[-1] == {"role": "user", "content": turn}
        # user-ход уже в ветке: в срезе ровно 12 преамбулы + ветка A
        assert slice_[12:] == state["branches"]["A"]
        # симуляция работы агента: ответ ассистента в ветку A (под его локом)
        state["branches"]["A"].append(
            {"role": "assistant", "content": f"ответ на: {turn}"}
        )

    # после 3 ходов: в A — 3 пары user+assistant, B пуста
    assert [m["content"] for m in state["branches"]["A"]] == [
        "вопрос 7", "ответ на: вопрос 7",
        "вопрос 8", "ответ на: вопрос 8",
        "вопрос 9", "ответ на: вопрос 9",
    ]
    assert state["branches"]["B"] == []


# ---------------------------------------------------------------------------
# 4. Изоляция веток: в B нет ничего из ходов 7-9 ветки A
# ---------------------------------------------------------------------------

def test_branching_isolation_B_sees_no_A_messages():
    """После ходов 7-9 в A: switch_branch('B') + альтернативный вопрос 7.

    Срез B = history[:12] + [альт-user-ход]; НИ ОДНО сообщение из ветки A
    (вопросы 7-9 И ответы на них) в срез B не попадает.
    """
    s = BranchingStrategy()
    state = s.default_state()
    history = _history_12()
    s.checkpoint(state, 12)

    # ходы 7-9 в A (как в test 3: on_user_message -> agent append ответа)
    for turn in ("вопрос 7", "вопрос 8", "вопрос 9"):
        s.on_user_message(turn, state, None)
        state["branches"]["A"].append(
            {"role": "assistant", "content": f"ответ на: {turn}"}
        )

    alt_turn_7 = "альтернативный вопрос 7"
    assert s.switch_branch(state, "B") == "B"
    s.on_user_message(alt_turn_7, state, None)
    _, slice_b = s.build_payload("СИСТЕМА", history, alt_turn_7, state)
    assert slice_b == history[:12] + [{"role": "user", "content": alt_turn_7}]

    # изоляция: ни одного сообщения из ветки A в срезе B
    a_contents = {m["content"] for m in state["branches"]["A"]}
    b_contents = {m["content"] for m in slice_b[12:]}
    assert a_contents.isdisjoint(b_contents)
    for content in a_contents:
        assert content not in {m["content"] for m in slice_b}


# ---------------------------------------------------------------------------
# 5. Возврат в A: срез A снова содержит ходы 7-9
# ---------------------------------------------------------------------------

def test_branching_switch_back_to_A():
    """switch_branch('A') после 'B': активна A, её срез снова содержит
    ходы 7-9."""
    s = BranchingStrategy()
    state = s.default_state()
    history = _history_12()
    s.checkpoint(state, 12)
    for turn in ("вопрос 7", "вопрос 8", "вопрос 9"):
        s.on_user_message(turn, state, None)
        state["branches"]["A"].append(
            {"role": "assistant", "content": f"ответ на: {turn}"}
        )
    assert s.switch_branch(state, "B") == "B"
    s.on_user_message("альтернативный вопрос 7", state, None)

    assert s.switch_branch(state, "A") == "A"
    assert state["active"] == "A"
    _, slice_a = s.build_payload("СИСТЕМА", history, "вопрос 10", state)
    assert slice_a[:12] == history
    a_contents = {m["content"] for m in slice_a}
    for turn in ("вопрос 7", "вопрос 8", "вопрос 9"):
        assert turn in a_contents
        assert f"ответ на: {turn}" in a_contents
    # альтернативный ход B в срез A не попал
    assert "альтернативный вопрос 7" not in a_contents


# ---------------------------------------------------------------------------
# 6. Implicit-ветвление: branch() ставит первый (единственный) чекпоинт
# ---------------------------------------------------------------------------

def test_branching_implicit_branch_sets_first_checkpoint():
    """Fresh state: branch(state, 12) → checkpoint == 12 (имплицитный
    первый чекпоинт). Повторный branch() при установленном чекпоинте —
    no-op (чекпоинт НЕ смещается)."""
    s = BranchingStrategy()
    state = s.default_state()
    s.branch(state, 12)
    assert state["checkpoint"] == 12
    s.branch(state, 14)  # уже стоит — не трогаем
    assert state["checkpoint"] == 12


def test_branching_checkpoint_replaces_old_one():
    """checkpoint() второй раз: старый чекпоинт ЗАМЕНЯЕТСЯ, обе ветки
    сбрасываются в пустые, active остаётся 'A' (один чекпоинт, merge нет)."""
    s = BranchingStrategy()
    state = s.default_state()
    s.checkpoint(state, 12)
    s.on_user_message("вопрос 7", state, None)
    assert state["branches"]["A"] == [{"role": "user", "content": "вопрос 7"}]
    s.checkpoint(state, 14)  # перезапись
    assert state["checkpoint"] == 14
    assert state["branches"] == {"A": [], "B": []}
    assert state["active"] == "A"


# ---------------------------------------------------------------------------
# 7. switch_branch на не-A/B → ValueError
# ---------------------------------------------------------------------------

def test_branching_switch_invalid_name_raises():
    """switch_branch('C') — ValueError (не более двух веток)."""
    s = BranchingStrategy()
    state = s.default_state()
    with pytest.raises(ValueError):
        s.switch_branch(state, "C")


# ---------------------------------------------------------------------------
# 8. on_user_message ДО чекпоинта — no-op
# ---------------------------------------------------------------------------

def test_branching_on_user_message_before_checkpoint_noop():
    """checkpoint=None: on_user_message ничего не пишет (ветки пустые),
    build_payload остаётся в режиме полной истории."""
    s = BranchingStrategy()
    state = s.default_state()
    s.on_user_message("вопрос 1", state, None)
    assert state == {
        "checkpoint": None,
        "branches": {"A": [], "B": []},
        "active": "A",
    }
    history = [
        {"role": "user", "content": "вопрос 1"},
        {"role": "assistant", "content": "ответ 1"},
    ]
    _, slice_ = s.build_payload("СИСТЕМА", history, "вопрос 2", state)
    assert slice_ == history + [{"role": "user", "content": "вопрос 2"}]
