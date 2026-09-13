"""Тесты персистентности диалогов (day9, Task 12).

Покрытие:
- test_restart_roundtrip — диалог с непустой сводкой: save -> НОВЫЙ
  SimpleAgent на том же dialogues_file -> активная сводка и история
  идентичны (валидирует whitelist-фикс Task 3: "summary" читается при
  загрузке, а не теряется);
- test_corrupted_summary_tolerant — вручную записанный dialogues.json с
  summary: null / summary: 12345 (non-string): агент грузится без crash,
  сводка приводится к "";
- test_utf8_emoji_summary_roundtrip — длинная сводка с кириллицей и
  emoji: save -> reload -> строка идентична (byte-identical по смыслу),
  на диске — настоящий UTF-8, а не \\uXXXX-экраны.

Полностью офлайн: ask() не вызывается (urlopen не нужен), файлы — только
в tmp_path, файлы репозитория не трогаются.
"""
import json
from pathlib import Path

import pytest

from agent import SimpleAgent
from test_compression import seed

SUMMARY = "Миграция БД согласована: PostgreSQL 16, перенос в пятницу, роллабэк готов."

EMOJI_SUMMARY = (
    "🔥 миграция БД 🚀 — все таблицы перенесены (orders, users, payments). "
    "Индексы пересозданы: idx_orders_user, idx_orders_date. Перевод трафика "
    "завершён, данных не потеряно. Осталось: удаление legacy-полей, "
    "мониторинг 48 часов, отчёт в четверг. 🔥🚀"
)


def make_agent(dialogues_file: str) -> SimpleAgent:
    """SimpleAgent на том же dialogues_file, legacy history_file — tmp (не существует)."""
    parent = Path(dialogues_file).parent
    return SimpleAgent(
        dialogues_file=dialogues_file,
        history_file=str(parent / "history-restart.json"),
    )


def test_restart_roundtrip(agent_tmp, dialogues_file):
    """Сводка переживает «перезапуск»: новый агент видит ту же активную сводку."""
    seed(agent_tmp, 4)
    agent_tmp._active["summary"] = SUMMARY
    with agent_tmp._lock:
        agent_tmp._save_dialogues()  # зафиксировать на диске (как после ask())

    history_before = agent_tmp.get_history()
    active_id_before = agent_tmp._active["id"]

    # «перезапуск сервера» — новый экземпляр на том же файле
    agent2 = make_agent(dialogues_file)
    assert agent2._dialogues["active_id"] == active_id_before
    assert agent2._active["summary"] == SUMMARY  # whitelist Task 3: summary читается
    assert agent2.get_history() == history_before


@pytest.mark.parametrize("bad_summary", [None, 12345])
def test_corrupted_summary_tolerant(dialogues_file, bad_summary):
    """summary: null / 12345 (non-string) в файле — загрузка без crash, сводка -> ""."""
    p = Path(dialogues_file)
    data = {
        "active_id": "d-1",
        "dialogues": [
            {
                "id": "d-1",
                "created_at": "2026-01-01T00:00:00",
                "closed_at": None,
                "messages": [
                    {"role": "user", "content": "привет"},
                    {"role": "assistant", "content": "здравствуйте"},
                ],
                "summary": bad_summary,
            }
        ],
    }
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    agent = make_agent(dialogues_file)  # не должно бросить исключение
    assert agent._active["summary"] == ""
    assert agent.get_history() == [
        {"role": "user", "content": "привет"},
        {"role": "assistant", "content": "здравствуйте"},
    ]


def test_utf8_emoji_summary_roundtrip(agent_tmp, dialogues_file):
    """Кириллица + emoji в сводке: save -> reload -> строка идентична, на диске UTF-8."""
    seed(agent_tmp, 4)
    agent_tmp._active["summary"] = EMOJI_SUMMARY
    with agent_tmp._lock:
        agent_tmp._save_dialogues()

    raw = Path(dialogues_file).read_bytes()
    assert "🔥".encode("utf-8") in raw  # реальный UTF-8, не \ud83d\udd25-экранирование
    assert "миграция БД".encode("utf-8") in raw

    agent2 = make_agent(dialogues_file)
    assert agent2._active["summary"] == EMOJI_SUMMARY  # byte-identical после reload
