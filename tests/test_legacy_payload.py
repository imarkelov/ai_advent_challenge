"""Locking-тест (регрессионный щит) day9 legacy-payload — day10, Task 4.

Пиннит ТОЧНЫЙ вид messages, который текущий (day9) код отправляет в API
после 6 ходов пользователя при window_size=3 / summary_gap=2 /
compression_enabled=True. Golden-файл tests/fixtures/legacy_payload.json
СГЕНЕРИРОВАН из реального вызова (не рукописный): все LLM-ответы canned
(фиксированный текст сводки и ответа), поэтому payload полностью
детерминирован — только словари {role, content}.

Назначение — щит для Task 8 (LegacyStrategy): после рефакторинга
контекстной логики тест обязан продолжать проходить byte-identically.
Ожидаемая форма (agent.py:621-630, day9):
  [0]  system: system_prompt + "\\n\\nРезюме диалога: <summary>" (если сводка есть)
  [1..-2]  срез истории (после trim не длиннее window_size)
  [-1] текущее сообщение пользователя.

Офлайн: urlopen подменяется (паттерн tests/conftest.py:42-81 и
tests/test_compression.py:37-64), файлы диалогов — в tmp_path.
"""
import io
import json
from pathlib import Path

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "legacy_payload.json"

# Фиксированные тексты fake-LLM (детерминизм golden-а):
# сводочный запрос -> SUMMARY_TEXT, обычный ask() -> CANNED_CONTENT
CANNED_CONTENT = "Тестовый ответ модели (canned)."
SUMMARY_TEXT = "СВОДКА: зафиксированы решения по миграции."


def is_summary_call(payload) -> bool:
    """Маркер сводочного запроса (тот же, что в test_compression.py):
    temperature=0 + max_tokens=300 + enable_thinking=False + RU-промпт «Сожми»."""
    return (
        payload.get("temperature") == 0
        and payload.get("max_tokens") == 300
        and payload.get("chat_template_kwargs") == {"enable_thinking": False}
        and any("Сожми" in m["content"] for m in payload["messages"])
    )


def install_fake(monkeypatch):
    """Подмена urlopen: сводка -> SUMMARY_TEXT, обычный ask() -> CANNED_CONTENT.

    Возвращает список распарсенных payload-ов (для инспекций).
    """
    calls = []

    def _fake(req, timeout=None):
        payload = json.loads(req.data.decode("utf-8"))
        calls.append(payload)
        content = SUMMARY_TEXT if is_summary_call(payload) else CANNED_CONTENT
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


def drive_six_turns(agent):
    """6 ходов пользователя: ход 4 и ход 6 триггерят сжатие (window=3, gap=2)."""
    for i in range(1, 7):
        agent.ask(f"ход {i}")


def test_legacy_payload_byte_identical(agent_tmp, monkeypatch):
    """Щит: payload 6-го хода deep-equals golden, снятому с day9-кода."""
    install_fake(monkeypatch)
    agent_tmp.configure(window_size=3, summary_gap=2, compression_enabled=True)
    drive_six_turns(agent_tmp)

    msgs = agent_tmp.get_last_request()["messages"]

    # --- структурные инварианты (именованные, для читаемости) ---
    # [0] system с инъекцией сводки
    assert msgs[0]["role"] == "system"
    assert "Резюме диалога" in msgs[0]["content"]
    assert SUMMARY_TEXT in msgs[0]["content"]
    # [-1] текущий ввод пользователя
    assert msgs[-1] == {"role": "user", "content": "ход 6"}
    # середина — срез истории, не длиннее окна
    middle = msgs[1:-1]
    assert len(middle) <= agent_tmp._window_size
    assert all(m["role"] in ("user", "assistant") for m in middle)

    # --- LOCKING: byte-identical совпадение с golden ---
    assert FIXTURE.exists(), (
        "golden-файл отсутствует: tests/fixtures/legacy_payload.json "
        "(должен быть СГЕНЕРИРОВАН из реального вызова day9-кода, не рукописный)"
    )
    golden = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert msgs == golden
