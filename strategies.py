"""strategies.py — ABC и стратегии управления контекстом (day10).

Контекст, который уходит LLM, собирает стратегия:

- ``build_payload`` решает, какой срез истории отправить. Текущий
  пользовательский ход стратегия ДОЛЖНА добавить в конец возвращаемого
  среза последней элементом (в тесте: ``slice_[-1] == {"role": "user", ...}``).
- ``on_user_message`` — хук на входящее сообщение (sticky-факты, точки
  ветвления). По умолчанию no-op; ``llm_call`` — callable ``str -> str``,
  который даёт агент (в тестах — fake). Извлечение идёт ТОЛЬКО через
  этот callable — не через ask(), чтобы не писать /agent/last-request.
- ``default_state`` — начальное состояние стратегии на диалог (additive
  поле strategy_state в dialogues.json, без деструктивной миграции).

Реализованные стратегии: ``SlidingWindowStrategy`` (скользящее окно N,
day10 Task 5), ``StickyFactsStrategy`` (sticky-факты ТЗ, day10 Task 6;
остальные — branching, legacy — в следующих задачах). Только стандартная библиотека (abc) + facts.py (чистые функции). Модуль
импортируется standalone: без побочных эффектов, сети и чтения .env.
"""
import abc

from facts import FACT_KEYS, extract_facts


class ContextStrategy(abc.ABC):
    """Базовая стратегия управления контекстом диалога.

    Конкретные стратегии (legacy, sliding_window, sticky_facts, branching)
    переопределяют build_payload / default_state / strategy_name;
    on_user_message переопределяют только те, что имеют хук.
    """

    @property
    @abc.abstractmethod
    def strategy_name(self) -> str:
        """Стабильный str-идентификатор стратегии (ключ в UI/настройках)."""

    @abc.abstractmethod
    def build_payload(
        self, system_prompt: str, history: list, user_input: str, state: dict
    ) -> tuple:
        """Собрать контекст запроса.

        history — список dict ``{"role", "content"}`` активного диалога.

        Возвращает ``(system_content, history_slice)``:

        - ``system_content`` — итоговый system-промт (стратегия может
          обогатить исходный, напр. вставить sticky-факты);
        - ``history_slice`` — выбранный срез истории. Текущий пользовательский
          ход (``user_input``) стратегия добавляет в конец среза последней
          элементом ``{"role": "user", "content": user_input}``.
        """

    @abc.abstractmethod
    def default_state(self) -> dict:
        """Начальное состояние стратегии на диалог (пустой/базовый dict)."""

    def on_user_message(self, user_input: str, state: dict, llm_call) -> None:
        """Хук на входящее сообщение ДО сборки ответа.

        По умолчанию no-op (legacy/окно-стратегии состояние не держат).
        Подклассы переопределяют: извлечение sticky-фактов через ``llm_call``,
        чекпоинты ветвления. ``state`` — dict из ``default_state()`` на
        диалог (модифицируется in-place). ``llm_call`` — callable
        ``str -> str``; извлечение НЕ идёт через ask() и НЕ пишет
        /agent/last-request.
        """
        return None


# ---------------------------------------------------------------------------
# Task 5: SlidingWindowStrategy — скользящее окно (non-destructive)
# ---------------------------------------------------------------------------

class SlidingWindowStrategy(ContextStrategy):
    """Скользящее окно: в LLM уходит последние N сообщений истории.

    В отличие от legacy (day9) срез НЕ деструктивен: исходная история
    диалога не мутируется и не удаляется — ``build_payload`` возвращает
    новый список. Сводку старых сообщений эта стратегия не держит,
    поэтому system-промт пробрасывает исходный, а ``on_user_message``
    (no-op по умолчанию) не переопределяет.
    """

    strategy_name = "sliding_window"

    def build_payload(
        self, system_prompt: str, history: list, user_input: str, state: dict
    ) -> tuple:
        """Срез = последние N сообщений истории + текущий ход последним.

        N берётся из ``state["window_size"]`` (день 10: по умолчанию 4);
        если история короче окна — срез = вся история + текущий ход.
        """
        n = state.get("window_size", 4)
        slice_ = list(history)[-n:] + [{"role": "user", "content": user_input}]
        return system_prompt, slice_

    def default_state(self) -> dict:
        """Начальное состояние: размер окна N=4 (ключ day9 — window_size)."""
        return {"window_size": 4}


# ---------------------------------------------------------------------------
# Task 6: StickyFactsStrategy — sticky-факты ТЗ (day10)
# ---------------------------------------------------------------------------

class StickyFactsStrategy(ContextStrategy):
    """Sticky-факты: стабильные факты ТЗ прилипают к system-промту.

    Фиксированная схема (цель/стек/ограничения/дедлайн/решения —
    ``FACT_KEYS`` из facts.py) живёт в ``state["facts"]``. Каждый входящий
    ход ``on_user_message`` синхронно извлекает факты через ``llm_call``
    (``facts.extract_facts``) и обновляет ``state["facts"]`` in-place
    (``clear()`` + ``update()`` — ссылка на dict не меняется). Ошибки
    извлечения (сбой API, не-JSON) уже проглочены ``extract_facts``
    (возвращает прежние факты) — ask никогда не ломается.

    ``build_payload``: не пустые факты вставляются в system-промт блоком
    «Актуальные факты:» (строки «- key: value» в порядке ``FACT_KEYS``);
    фактов нет — промт пробрасывается без изменений. Срез истории —
    последние ``window_size`` сообщений + текущий ход последним
    (то же окно, что у ``SlidingWindowStrategy``).

    Стратегия ЧИСТА: единственный LLM-канал — ``llm_call`` (ни ask(), ни
    сеть, ни /agent/last-request). Блокировка (self._lock в агенте —
    Task 9) НЕ стратегия: агент вызывает on_user_message под своим
    локом, стратегия лишь мутирует переданный ей dict состояния.
    """

    strategy_name = "sticky_facts"

    def default_state(self) -> dict:
        """Начальное состояние: пустые факты + окно N=4."""
        return {"facts": {}, "window_size": 4}

    def on_user_message(self, user_input: str, state: dict, llm_call) -> None:
        """Извлечь факты из ``user_input`` и обновить state["facts"] in-place."""
        merged = extract_facts(llm_call, user_input, state.get("facts"))
        facts = state.setdefault("facts", {})
        facts.clear()
        facts.update(merged)

    def build_payload(
        self, system_prompt: str, history: list, user_input: str, state: dict
    ) -> tuple:
        """system-промт + блок фактов (если не пуст) / окно N + текущий ход.

        Блок: «Актуальные факты:» строками «- key: value» по одному на
        непустой факт, в порядке ``FACT_KEYS``; фактов нет —
        ``system_prompt`` возвращается без изменений.
        """
        facts = state.get("facts") or {}
        if facts:
            lines = [f"- {key}: {facts[key]}" for key in FACT_KEYS if facts.get(key)]
            system_content = system_prompt + "\n\nАктуальные факты:\n" + "\n".join(lines)
        else:
            system_content = system_prompt
        n = state.get("window_size", 4)
        slice_ = list(history)[-n:] + [{"role": "user", "content": user_input}]
        return system_content, slice_
