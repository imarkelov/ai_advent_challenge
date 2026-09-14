"""Тесты сценария сбора ТЗ (day10, task 3).

Проверяют структуру scenario_day10: ровно 12 ходов, каждый ход —
непустая строка, каждый KEY_DETAIL встречается буквально (без учёта
регистра) хотя бы в одном ходе, маркеров не меньше пяти и ходы 7-12
явно отсылаются к деталям из ходов 1-6 (это ядро проверки
устойчивости контекста в benchmark_day10).
"""
from scenario_day10 import KEY_DETAILS, SCENARIO_TURNS


def test_exactly_12_turns():
    """S3-протокол бенчмарка хардкодит чекпоинт на 6-м ходе."""
    assert len(SCENARIO_TURNS) == 12


def test_every_turn_is_nonempty_string():
    for i, turn in enumerate(SCENARIO_TURNS, start=1):
        assert isinstance(turn, str), f"ход {i} не строка: {type(turn)}"
        assert turn.strip(), f"ход {i} пуст"


def test_at_least_five_key_details():
    assert len(KEY_DETAILS) >= 5
    # маркеры без дублей (в нижнем регистре)
    lowered = [d.lower() for d in KEY_DETAILS]
    assert len(set(lowered)) == len(lowered), "дубли в KEY_DETAILS"


def test_every_key_detail_appears_in_some_turn():
    """Каждый маркер встречается буквально в одном или нескольких ходах."""
    turns_lower = [t.lower() for t in SCENARIO_TURNS]
    for detail in KEY_DETAILS:
        dl = detail.lower()
        assert any(dl in t for t in turns_lower), (
            f"KEY_DETAIL {detail!r} не найден ни в одном ходе"
        )


def test_late_turns_reference_earlier_details():
    """Ходы 7-12 уточняют детали и явно отсылаются к ходам 1-6.

    Зафиксированные отсылки (индексация с нуля, ход N = индекс N-1):
    - ход 7 (индекс 6) возвращается к дедлайну из хода 4: «15 ноября»;
    - ход 8 (индекс 7) возвращается к стеку из хода 2: «FastAPI»;
    - ход 11 (индекс 10) переспрашивает стек из ходов 2-3: «PostgreSQL».
    """
    assert "15 ноября" in SCENARIO_TURNS[6].lower()
    assert "fastapi" in SCENARIO_TURNS[7].lower()
    assert "postgresql" in SCENARIO_TURNS[10].lower()
