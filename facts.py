"""facts.py — извлечение стабильных фактов ТЗ (day10, Task 2).

Фиксированная схема фактов: цель/стек/ограничения/дедлайн/решения.
Ключи НЕ расширяются во время работы — это инвариант дня 10.

Извлечение — отдельный LLM-запрос с маркером ``FACTS_MARKER`` (аналог
JUDGE_MARKER в benchmark.py:103 — по маркеру mock-заглушка отличает
запрос извлечения от обычного ask/сводки).

Только стандартная библиотека (json, logging). Модуль импортируется
standalone: без побочных эффектов, сети, чтения .env, писем в last-request.
"""
import json
import logging

logger = logging.getLogger(__name__)

# фиксированная схема фактов — никогда не расширяется во время работы
FACT_KEYS = ["цель", "стек", "ограничения", "дедлайн", "решения"]

# маркер запроса извлечения (mock-заглушка по нему отличает extraction от ask/сводки)
FACTS_MARKER = "FACTS-ИЗВЛЕЧЕНИЕ-ТЗ"


def _build_extraction_prompt(user_input: str) -> str:
    """RU-промпт извлечения: FACTS_MARKER + «верни ТОЛЬКО JSON с FACT_KEYS»."""
    keys = ", ".join(FACT_KEYS)
    return (
        f"{FACTS_MARKER}\n"
        "Ты извлекаешь факты технического задания из сообщения пользователя.\n"
        f"Верни ТОЛЬКО JSON-объект с ключами: {keys}.\n"
        "Не выдумывай факты: ключ, о котором в сообщении нет данных, "
        "опусти из ответа.\n"
        "Не добавляй ключей вне списка и не оборачивай ответ в markdown.\n"
        f"Сообщение пользователя:\n{user_input}"
    )


def parse_facts_json(text: str) -> dict:
    """Разобрать ответ ЛЛМ как JSON-словарь фактов.

    Срезает markdown-ограды ```` ```json ... ``` ```` при наличии.
    Любая ошибка (не JSON, не словарь) -> ``{}`` + warning в лог;
    наружу исключений не идёт.
    """
    try:
        t = text.strip()
        if t.startswith("```"):
            lines = t.splitlines()
            if lines[0].strip().startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            t = "\n".join(lines).strip()
        data = json.loads(t)
        if not isinstance(data, dict):
            logger.warning("facts: ответ ЛЛМ — не JSON-словарь: %r", text[:120])
            return {}
        return data
    except Exception:
        logger.warning("facts: не удалось разобрать JSON фактов: %r", text[:120])
        return {}


def extract_facts(llm_call, user_input: str, existing_facts: dict | None = None) -> dict:
    """Извлечь факты из ``user_input`` через ``llm_call`` и слить с прежними.

    ``llm_call`` — callable ``str -> str`` (в тестах — fake); извлечение
    идёт ТОЛЬКО через него. Слияние по фикс-схеме:

    - выживают только ``FACT_KEYS`` — лишние ключи от ЛЛМ бросаются;
    - ключ, который ЛЛМ пропустила, хранит прежнее значение
      (значения не выдумываются).

    Любая ошибка (``llm_call`` упала, ответ не JSON) -> возвращаются
    ``existing_facts`` без изменений (фолбэк, без исключений);
    прежних фактов нет -> ``{}``.
    """
    existing = dict(existing_facts or {})
    try:
        reply = llm_call(_build_extraction_prompt(user_input))
        parsed = parse_facts_json(reply)
    except Exception:
        logger.warning("facts: извлечение упало, оставляю прежние факты", exc_info=True)
        return existing
    merged = {}
    for key in FACT_KEYS:
        if key in parsed:
            merged[key] = parsed[key]
        elif key in existing:
            merged[key] = existing[key]
    return merged
