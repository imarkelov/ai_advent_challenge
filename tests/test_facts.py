"""Тесты facts.py (day10, Task 2) — TDD.

Покрытие:
- parse_facts_json: мусор -> {}; валидный JSON -> dict; markdown-ограды
  ```json ... ``` срезываются; не-словарь -> {};
- extract_facts: фикс-схема — только FACT_KEYS выживают (лишние ключи
  ЛЛМ бросаются), пропущенные ключи сохраняют прежнее значение;
  ошибка llm_call или разбор -> существующие факты без изменений (фолбэк);
- FACTS_MARKER присутствует в промпте, который уходит llm_call
  (аналог JUDGE_MARKER в benchmark.py для mock-детекции типа запроса).

Офлайн: llm_call — fake (callable str -> str), сети нет.
"""
import json

from facts import FACT_KEYS, FACTS_MARKER, parse_facts_json, extract_facts


# ---------------------------------------------------------------- parse


def test_parse_facts_json_garbage():
    assert parse_facts_json("garbage") == {}


def test_parse_facts_json_valid():
    data = {"цель": "портал", "стек": "FastAPI"}
    assert parse_facts_json(json.dumps(data, ensure_ascii=False)) == data


def test_parse_facts_json_markdown_fence():
    data = {"стек": "PostgreSQL"}
    text = "```json\n" + json.dumps(data, ensure_ascii=False) + "\n```"
    assert parse_facts_json(text) == data


def test_parse_facts_json_not_dict():
    """JSON-массив — не словарь фактов -> {}."""
    assert parse_facts_json("[1, 2, 3]") == {}


# ---------------------------------------------------------------- extract


def test_extract_facts_valid_json_subset():
    """Валидный JSON ЛЛМ -> dict, ключи — подмножество FACT_KEYS."""
    def fake_llm(prompt):
        return json.dumps({"цель": "портал", "стек": "FastAPI"}, ensure_ascii=False)

    facts = extract_facts(fake_llm, "ввод ТЗ")
    assert facts["цель"] == "портал"
    assert facts["стек"] == "FastAPI"
    assert set(facts) <= set(FACT_KEYS)


def test_extract_facts_extra_key_dropped():
    """Лишний ключ от ЛЛМ (не из FACT_KEYS) бросается."""
    def fake_llm(prompt):
        return json.dumps({"цель": "x", "лишнее": "y"}, ensure_ascii=False)

    assert extract_facts(fake_llm, "ввод") == {"цель": "x"}


def test_extract_facts_omitted_key_keeps_existing():
    """Ключ, который ЛЛМ пропустила, хранит прежнее значение (не выдумывается)."""
    existing = {"цель": "старый", "стек": "старый"}

    def fake_llm(prompt):
        return json.dumps({"цель": "новый"}, ensure_ascii=False)

    facts = extract_facts(fake_llm, "ввод", existing)
    assert facts["цель"] == "новый"
    assert facts["стек"] == "старый"


def test_extract_facts_llm_raises_returns_existing():
    """llm_call упала -> существующие факты без изменений."""
    existing = {"цель": "x"}

    def fake_llm(prompt):
        raise RuntimeError("backend down")

    assert extract_facts(fake_llm, "ввод", existing) == existing


def test_extract_facts_bad_json_returns_existing():
    """LLM вернула не-JSON -> существующие факты без изменений."""
    existing = {"стек": "y"}
    assert extract_facts(lambda p: "не json", "ввод", existing) == existing


def test_extract_facts_llm_raises_no_existing():
    """llm_call упала, прежних фактов нет -> {}."""
    def fake_llm(prompt):
        raise RuntimeError("backend down")

    assert extract_facts(fake_llm, "ввод") == {}


def test_extract_facts_prompt_has_marker():
    """В промпт извлечения уходит FACTS_MARKER (mock отличает запрос типа)."""
    captured = {}

    def fake_llm(prompt):
        captured["prompt"] = prompt
        return "{}"

    extract_facts(fake_llm, "ввод ТЗ")
    assert FACTS_MARKER in captured["prompt"]
