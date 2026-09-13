"""Тесты get_token_stats: ключи, summary_tokens (day9, Task 8).

Офлайн: сетевой вызов не нужен — только чтение состояния диалога.
"""
from agent import count_tokens

SUMMARY = "привет, мир! Это сводка."


def test_token_stats_exactly_three_int_keys(agent_tmp):
    stats = agent_tmp.get_token_stats()
    assert set(stats) == {"history_tokens", "reply_tokens", "summary_tokens"}
    for value in stats.values():
        assert isinstance(value, int)


def test_summary_tokens_counts_summary_text(agent_tmp):
    # Сводку пишем напрямую в активный диалог (не через сжатие).
    agent_tmp._active["summary"] = SUMMARY
    stats = agent_tmp.get_token_stats()
    assert stats["summary_tokens"] == count_tokens(SUMMARY, agent_tmp.model)
    assert stats["summary_tokens"] > 0


def test_summary_tokens_zero_without_summary(agent_tmp):
    # Свежий диалог: сводка пуста — 0 токенов.
    stats = agent_tmp.get_token_stats()
    assert stats["summary_tokens"] == 0
    assert stats["history_tokens"] == 0
