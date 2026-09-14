"""strategies.py — ABC стратегий управления контекстом (day10, Task 2).

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

Только стандартная библиотека (abc). Модуль импортируется standalone:
без побочных эффектов, сети и чтения .env.
"""
import abc


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
