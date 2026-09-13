"""Базовые санити-тесты SimpleAgent (day9, Task 4).

Только базовое поведение (без тестов сжатия):
1) legacy day8-файл без ключа summary -> у каждого диалога summary == "";
2) save -> reload round-trip сохраняет сообщения;
3) get_token_stats() содержит history_tokens и reply_tokens;
4) ask() с fake_urlopen отвечает canned-контентом без сети.
"""
import json

from agent import SimpleAgent
from conftest import CANNED_CONTENT


def test_legacy_day8_dialogues_get_empty_summary(tmp_path):
    """Day8-файл (без ключа summary / с summary=None) -> summary == "".

    Фиксация whitelist-миграции Task 3 (commit b0d1e98): после загрузки
    у КАЖДОГО диалога есть строковый summary, отсутствующий ключ и None
    оба превращаются в "".
    """
    df = tmp_path / "dialogues.json"
    legacy = {
        "active_id": "d1",
        "dialogues": [
            {  # day8-формат: ключа summary нет вообще
                "id": "d1",
                "created_at": "2026-09-12T10:00:00",
                "closed_at": None,
                "messages": [{"role": "user", "content": "привет"}],
            },
            {  # не-str значение (None) — тоже из day8-эпохи
                "id": "d2",
                "created_at": "2026-09-12T11:00:00",
                "closed_at": "2026-09-12T12:00:00",
                "messages": [],
                "summary": None,
            },
        ],
    }
    df.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")

    agent = SimpleAgent(
        dialogues_file=str(df),
        history_file=str(tmp_path / "history.json"),
    )

    assert agent._dialogues["active_id"] == "d1"
    assert all(d["summary"] == "" for d in agent._dialogues["dialogues"])
    # файл при чистой загрузке не перезаписывается (миграция только в памяти)
    assert json.loads(df.read_text(encoding="utf-8")) == legacy


def test_save_reload_roundtrip_preserves_messages(agent_tmp, fake_urlopen):
    """ask() пишет диалог на диск; новый SimpleAgent на том же файле видит те же сообщения."""
    agent_tmp.ask("вопрос 1")
    assert len(agent_tmp.get_history()) == 2  # user + assistant

    reloaded = SimpleAgent(
        dialogues_file=agent_tmp.dialogues_file,
        history_file=agent_tmp.history_file,
    )
    assert reloaded.get_history() == agent_tmp.get_history()
    assert reloaded.get_history()[0] == {"role": "user", "content": "вопрос 1"}
    assert reloaded.get_history()[1] == {"role": "assistant", "content": CANNED_CONTENT}


def test_token_stats_contains_history_and_reply(agent_tmp):
    """get_token_stats(): dict с ключами history_tokens и reply_tokens (проверка присутствия)."""
    stats = agent_tmp.get_token_stats()
    assert isinstance(stats, dict)
    assert "history_tokens" in stats
    assert "reply_tokens" in stats


def test_ask_offline_returns_canned_reply(agent_tmp, fake_urlopen):
    """ask() с fake_urlopen: canned-ответ без сети, usage из фикстуры, ответ в истории."""
    result = agent_tmp.ask("тестовый вопрос")

    assert result["reply"] == CANNED_CONTENT
    assert result["usage"]["prompt_tokens"] == 42
    assert result["usage"]["completion_tokens"] == 7
    assert result["usage"]["total_tokens"] == 49

    # запрос ушёл через подменённый urlopen (payload доступен для инспекции)
    assert len(fake_urlopen) == 1
    sent = fake_urlopen[0]
    assert sent["model"] == agent_tmp.model
    assert sent["messages"][0]["role"] == "system"
    assert sent["messages"][-1] == {"role": "user", "content": "тестовый вопрос"}

    # ответ добавлен в историю активного диалога
    assert agent_tmp.get_history()[-1] == {"role": "assistant", "content": CANNED_CONTENT}
