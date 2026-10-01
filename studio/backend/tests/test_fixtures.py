"""Схема-тесты фикстур дня 22 (control_questions.json + egg_book.txt).

Офлайн, без сети: egg_book.txt — committed-фикстура, поэтому факт-чек
Q8-Q10 возможен офлайн; факт-чек Q1-Q7 по реальным книгам — в QA
(книги в data/kb/uploads — gitignored runtime-артефакты).
"""
import json
import re
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"

ALLOWED_SOURCES = {
    "pushkin_oneygin.txt",
    "chekhov_cherry_orchard.txt",
    "tolstoy_war_peace.txt",
    "gogol_dead_souls.txt",
    "chekhov_lady_with_dog.txt",
    "chekhov_chameleon.txt",
    "chekhov_horse_first.txt",
    "egg_book.txt",
}

EGG_ANCHORS = ["IPhone 17Promax", "ксилофон", "Аркадий Фонарёв"]


def _normalize(text: str) -> str:
    """Нормализация: lower, collapse пробелов, ё→е (как у BM25-ноги)."""
    return re.sub(r"\s+", " ", text.lower()).replace("ё", "е")


def _load_questions() -> list[dict]:
    data = json.loads((FIXTURES / "control_questions.json").read_text("utf-8"))
    assert isinstance(data, list), "control_questions.json — массив"
    return data


def test_exactly_10_questions() -> None:
    questions = _load_questions()
    assert len(questions) == 10, f"ровно 10 вопросов, а не {len(questions)}"
    ids = [q["id"] for q in questions]
    assert sorted(ids) == list(range(1, 11)), "id уникальны и равны 1..10"


def test_question_fields_schema() -> None:
    for q in _load_questions():
        assert isinstance(q.get("id"), int), f"id int у вопроса {q.get('id')}"
        assert isinstance(q.get("question"), str) and q["question"].strip(), (
            f"непустой question у вопроса {q.get('id')}")
        facts = q.get("expect_facts")
        assert isinstance(facts, list) and facts, (
            f"expect_facts — непустой список у вопроса {q.get('id')}")
        assert all(isinstance(f, str) and f.strip() for f in facts), (
            f"expect_facts — непустые строки у вопроса {q.get('id')}")
        sources = q.get("expected_sources")
        assert isinstance(sources, list) and sources, (
            f"expected_sources — непустой список у вопроса {q.get('id')}")
        assert all(isinstance(s, str) and s.strip() for s in sources), (
            f"expected_sources — непустые строки у вопроса {q.get('id')}")
        for s in sources:
            assert s in ALLOWED_SOURCES, (
                f"expected_source '{s}' не из разрешённого набора "
                f"(вопрос {q.get('id')})")
        assert "chekhov_lady_with_dog.txt" not in sources, (
            f"«Дама с собачкой» — seed-копия, нельзя в expected_sources "
            f"(вопрос {q.get('id')})")


def test_egg_book_exists_and_anchors() -> None:
    egg = FIXTURES / "egg_book.txt"
    assert egg.is_file(), "egg_book.txt существует"
    text = egg.read_text("utf-8")  # не-UTF-8 упадёт UnicodeDecodeError
    assert 2_000 <= len(text.encode("utf-8")) <= 5_000, (
        f"egg_book.txt ~2-5KB, а {len(text.encode('utf-8'))} байт")
    for anchor in EGG_ANCHORS:
        assert _normalize(anchor) in _normalize(text), (
            f"якорная строка '{anchor}' есть в egg_book.txt")


def test_egg_questions_facts_present_in_egg_book() -> None:
    """Q8-Q10: expect_facts — подстроки egg_book.txt (офлайн-факт-чек).
    Q1-Q7 проверяются по реальным книгам только в QA (книги gitignored)."""
    egg = _normalize((FIXTURES / "egg_book.txt").read_text("utf-8"))
    for q in _load_questions():
        if "egg_book.txt" not in q["expected_sources"]:
            continue
        for fact in q["expect_facts"]:
            assert _normalize(fact) in egg, (
                f"факт '{fact}' из вопроса {q['id']} — подстрока "
                f"egg_book.txt")
