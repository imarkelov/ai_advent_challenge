"""Тесты ядра сжатия контекста (day9, Task 7) — TDD.

Покрытие:
- порог сжатия: окно (window_size=6) + шаг (summary_gap=4) по умолчанию;
- инъекция сводки в system-сообщение («Резюме диалога: ...»);
- in-place trim до окна (алиас self.history не рвётся);
- деградация: ошибка/пустая сводка — ask() не падает, история не трогается,
  повтор на следующем ask();
- сжатие выключено — ни сводки, ни trim HISTORY_CAP (режим day8);
- сводка живёт на диалоге: new_dialogue() обнуляет активную, старая
  сохраняется на диске, activate_dialogue() возвращает её.

Офлайн: urlopen подменяется (как в conftest), файлы в tmp_path.
Сводочный запрос отличают от обычного по маркерам payload:
temperature=0 + max_tokens=300 + enable_thinking=False + RU-промпт «Сожми».
"""
import io
import json
import urllib.error

from conftest import CANNED_CONTENT

# то, что fake возвращает на сводочный запрос
SUMMARY_TEXT = "СВОДКА: миграция БД согласована."


def is_summary_call(payload) -> bool:
    """Маркер сводочного запроса (отличает его от обычного ask())."""
    return (
        payload.get("temperature") == 0
        and payload.get("max_tokens") == 300
        and payload.get("chat_template_kwargs") == {"enable_thinking": False}
        and any("Сожми" in m["content"] for m in payload["messages"])
    )


def install_fake(monkeypatch, responder):
    """Подмена urlopen: responder(payload, calls) -> content ответа.

    Возвращает список распарсенных payload-ов (для инспекций).
    """
    calls = []

    def _fake(req, timeout=None):
        payload = json.loads(req.data.decode("utf-8"))
        calls.append(payload)
        content = responder(payload, calls)
        body = {
            "choices": [
                {"message": {"role": "assistant", "content": content, "reasoning": None}}
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 3,
                "total_tokens": 13,
                "completion_tokens_details": {"reasoning_tokens": 0},
            },
        }
        return io.BytesIO(json.dumps(body, ensure_ascii=False).encode("utf-8"))

    monkeypatch.setattr("urllib.request.urlopen", _fake)
    monkeypatch.setenv("GPUSTACK_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("GPUSTACK_API_KEY", "test-key")
    return calls


def seed(agent, n):
    """Посадить n сообщений в активный диалог in-place (алиас не рвётся)."""
    agent.history.extend(
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"сообщение {i}"}
        for i in range(n)
    )


def responder_ok(payload, calls):
    """Сводочный запрос -> SUMMARY_TEXT, обычный -> canned."""
    return SUMMARY_TEXT if is_summary_call(payload) else CANNED_CONTENT


def test_no_trigger_before_boundary(agent_tmp, monkeypatch):
    """9 сообщений: 9 - 6 = 3 < summary_gap=4 — сводка НЕ строится."""
    calls = install_fake(monkeypatch, responder_ok)
    seed(agent_tmp, 9)
    agent_tmp.ask("вопрос")
    assert len(calls) == 1  # только обычный запрос
    assert not is_summary_call(calls[0])
    assert len(agent_tmp.history) == 11  # 9 + новая пара, trim не нужен (<= 20)


def test_trigger_at_boundary(agent_tmp, monkeypatch):
    """10 сообщений: 10 - 6 = 4 >= summary_gap=4 — ровно 1 сводка, trim до окна."""
    calls = install_fake(monkeypatch, responder_ok)
    seed(agent_tmp, 10)
    result = agent_tmp.ask("новый вопрос")
    assert result["reply"] == CANNED_CONTENT
    assert sum(is_summary_call(c) for c in calls) == 1  # ровно одна сводка
    assert len(agent_tmp.history) == 8  # 6 окна + новая пара
    # остаток окна — последние 6 seeded (4..9)
    assert [m["content"] for m in agent_tmp.history[:6]] == [
        f"сообщение {i}" for i in range(4, 10)
    ]
    # payload: system со сводкой + 6 окна + новый вопрос
    msgs = agent_tmp.get_last_request()["messages"]
    assert msgs[0]["role"] == "system"
    assert "Резюме диалога" in msgs[0]["content"]
    assert SUMMARY_TEXT in msgs[0]["content"]
    assert [m["content"] for m in msgs[1:7]] == [f"сообщение {i}" for i in range(4, 10)]
    assert msgs[7] == {"role": "user", "content": "новый вопрос"}
    assert len(msgs) == 8


def test_summary_failure_degrades(agent_tmp, monkeypatch):
    """Сводочный запрос падает — ask() всё равно успешен, retry на след. ask()."""
    state = {"fail": True}

    def responder(payload, calls):
        if is_summary_call(payload):
            if state["fail"]:
                raise urllib.error.URLError("summary backend down")
            return SUMMARY_TEXT
        return CANNED_CONTENT

    calls = install_fake(monkeypatch, responder)
    seed(agent_tmp, 10)
    r = agent_tmp.ask("вопрос 1")  # сводка упала -> ask всё равно ок
    assert r["reply"] == CANNED_CONTENT
    assert len(agent_tmp.history) == 12  # trim НЕ было
    assert agent_tmp._active.get("summary", "") == ""
    # след. ask — повтор сводки
    state["fail"] = False
    agent_tmp.ask("вопрос 2")
    assert sum(is_summary_call(c) for c in calls) == 2
    assert agent_tmp._active["summary"] == SUMMARY_TEXT
    assert len(agent_tmp.history) == 8  # 6 окна + новая пара


def test_summary_empty_is_failure(agent_tmp, monkeypatch):
    """Сводка вернула пустую строку — как ошибка: trim нет, сводка остаётся ''."""
    def responder(payload, calls):
        return "" if is_summary_call(payload) else CANNED_CONTENT

    calls = install_fake(monkeypatch, responder)
    seed(agent_tmp, 10)
    result = agent_tmp.ask("вопрос")
    assert result["reply"] == CANNED_CONTENT
    assert sum(is_summary_call(c) for c in calls) == 1
    assert len(agent_tmp.history) == 12  # история не тронута
    assert agent_tmp._active.get("summary", "") == ""


def test_disabled_no_trim_no_cap(agent_tmp, monkeypatch):
    """compression_enabled=False: ни сводки, ни trim HISTORY_CAP (режим day8)."""
    agent_tmp.configure(compression_enabled=False)
    calls = install_fake(monkeypatch, responder_ok)
    seed(agent_tmp, 24)
    agent_tmp.ask("вопрос")
    assert not any(is_summary_call(c) for c in calls)
    assert len(agent_tmp.history) == 26  # 24 + 2, cap 20 не применён
    system = agent_tmp.get_last_request()["messages"][0]["content"]
    assert "Резюме диалога" not in system  # инъекции тоже нет


def test_dialogue_switch_summary(agent_tmp, monkeypatch):
    """Сводка на диалоге: new_dialogue() обнуляет, на диске старая живёт,
    activate_dialogue() возвращает её в активные."""
    install_fake(monkeypatch, responder_ok)
    seed(agent_tmp, 10)
    agent_tmp.ask("вопрос")  # сводка построена для активного
    assert agent_tmp._active["summary"] == SUMMARY_TEXT
    old_id = agent_tmp._active["id"]

    new_id = agent_tmp.new_dialogue()
    assert new_id != old_id
    assert agent_tmp._active["summary"] == ""  # новый диалог без сводки

    # старая сводка сохранилась на диске
    with open(agent_tmp.dialogues_file, encoding="utf-8") as f:
        data = json.load(f)
    old = next(d for d in data["dialogues"] if d["id"] == old_id)
    assert old["summary"] == SUMMARY_TEXT
    assert old["closed_at"] is not None

    # продолжаем старый — его сводка снова активна
    assert agent_tmp.activate_dialogue(old_id) == old_id
    assert agent_tmp._active["summary"] == SUMMARY_TEXT


def test_reset_clears_summary(agent_tmp, monkeypatch):
    """reset_history() — история и сводка активного обнуляются."""
    install_fake(monkeypatch, responder_ok)
    seed(agent_tmp, 10)
    agent_tmp.ask("вопрос")
    assert agent_tmp._active["summary"] == SUMMARY_TEXT
    agent_tmp.reset_history()
    assert agent_tmp.history == []
    assert agent_tmp._active["summary"] == ""
