"""Тесты StudioAgent: конфиг, build_payload, ask_stream (SSE), журнал, сессионные токены.

Офлайн: httpx.MockTransport вместо реального GPustack.
"""
import json

import httpx
import pytest

from agent import CONTEXT_LIMITS, MEMORY_RULE, StudioAgent
from conftest import USAGE, delta_chunk, sse_body, usage_chunk

BASE = "https://mock.local/v1"


@pytest.fixture
def data_dir(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    return d


def make_agent(data_dir, handler, env=None):
    """Агент на MockTransport с данным handler(request) -> httpx.Response."""
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return StudioAgent(str(data_dir), base_url=BASE, api_key="test-key",
                       client=client, env=env)


def ready(agent, d):
    """Диалог готов к обычным запросам (профиль declined, день 12)."""
    agent.store.profile_action(d["id"], "decline")


def ok_handler(request: httpx.Request) -> httpx.Response:
    """Стандартный SSE-ответ: 2 дельты + usage + [DONE]."""
    body = sse_body([delta_chunk("Прив"), delta_chunk("ет"), usage_chunk(), "[DONE]"])
    return httpx.Response(200, content=body.encode("utf-8"))


# ---------- init / конфиг ----------

def test_init_creates_store(data_dir):
    agent = make_agent(data_dir, ok_handler)
    assert agent.store is not None
    assert agent.base_url == BASE
    assert agent.api_key == "test-key"


def test_config_defaults(data_dir):
    agent = make_agent(data_dir, ok_handler)
    cfg = agent.get_config()
    assert cfg["model"] == "qwen3.8-27b"
    assert cfg["temperature"] == 0.7
    assert cfg["max_tokens"] == 2048
    assert cfg["system_prompt"].startswith("Ты — ассистент в обучающем приложении «Студия»")


def test_set_config_partial_and_returns_current(data_dir):
    agent = make_agent(data_dir, ok_handler)
    cfg = agent.set_config({"temperature": 1.2})
    assert cfg["temperature"] == 1.2
    assert cfg["model"] == "qwen3.8-27b"  # остальное не тронуто
    assert agent.get_config()["temperature"] == 1.2


def test_set_config_persistence(data_dir):
    a1 = make_agent(data_dir, ok_handler)
    a1.set_config({"model": "deepseek-v4-flash", "max_tokens": 512})
    # новый экземпляр в том же dir видит конфиг
    a2 = make_agent(data_dir, ok_handler)
    assert a2.get_config()["model"] == "deepseek-v4-flash"
    assert a2.get_config()["max_tokens"] == 512


@pytest.mark.parametrize("updates", [
    {"model": 123},
    {"model": ""},
    {"temperature": 2.5},
    {"temperature": -0.1},
    {"temperature": True},
    {"max_tokens": 0},
    {"max_tokens": -5},
    {"max_tokens": 1.5},
    {"max_tokens": True},
    {"system_prompt": 5},
    {"unknown_key": 1},
])
def test_set_config_invalid_raises_and_keeps_config(data_dir, updates):
    agent = make_agent(data_dir, ok_handler)
    before = agent.get_config()
    with pytest.raises(ValueError):
        agent.set_config(updates)
    assert agent.get_config() == before  # конфиг не изменился


# ---------- build_payload ----------

def test_build_payload_system_with_blocks_and_messages(data_dir):
    agent = make_agent(data_dir, ok_handler)
    d = agent.store.new_dialogue()
    agent.store.append_message(d["id"], "user", "привет")
    agent.store.append_message(d["id"], "assistant", "здравствуй")
    agent.store.wm_set(d["id"], "t", "задача")
    agent.store.lt_set("u", "юзер")
    payload = agent.build_payload(d["id"])
    expected_system = (agent.get_config()["system_prompt"]
                       + "\n\nТекущая задача:\n- t: задача"
                       + "\n\nДолговременная память:\n- u: юзер"
                       + MEMORY_RULE)
    assert payload[0] == {"role": "system", "content": expected_system}
    assert payload[1] == {"role": "user", "content": "привет"}
    assert payload[2] == {"role": "assistant", "content": "здравствуй"}


def test_build_payload_no_memory_no_rule(data_dir):
    """Без записей памяти правило о противоречиях НЕ добавляется."""
    agent = make_agent(data_dir, ok_handler)
    d = agent.store.new_dialogue()
    payload = agent.build_payload(d["id"])
    assert payload[0]["content"] == agent.get_config()["system_prompt"]
    assert MEMORY_RULE not in payload[0]["content"]


# ---------- профиль пользователя (день 12): инъекция ----------

def test_profile_block_active_in_system(data_dir):
    agent = make_agent(data_dir, ok_handler)
    d = agent.store.new_dialogue()
    agent.store.profile_set(d["id"], "Иван", "backend-разработчик",
                            "кратко и по делу", "мат")
    agent.store.append_message(d["id"], "user", "привет")
    system = agent.build_payload(d["id"])[0]["content"]
    base = agent.get_config()["system_prompt"]
    assert system.startswith(base + "\n\nПрофиль пользователя:\n")
    assert "- Имя: Иван" in system
    assert "- Роль и сфера: backend-разработчик" in system
    assert "- Тон и стиль: кратко и по делу" in system
    assert "- Стоп-слова/табу: мат" in system
    assert "\nИзбегай: мат" in system


def test_profile_block_empty_fields_skipped(data_dir):
    agent = make_agent(data_dir, ok_handler)
    d = agent.store.new_dialogue()
    agent.store.profile_set(d["id"], "Иван", "", "", "")
    system = agent.build_payload(d["id"])[0]["content"]
    assert "\n\nПрофиль пользователя:\n- Имя: Иван" in system
    assert "Избегай:" not in system
    assert "Роль и сфера" not in system  # пустые поля не светим


def test_profile_block_pending_declined_absent(data_dir):
    agent = make_agent(data_dir, ok_handler)
    d = agent.store.new_dialogue()
    assert "Профиль пользователя" not in agent.build_payload(d["id"])[0]["content"]
    agent.store.profile_action(d["id"], "decline")
    assert "Профиль пользователя" not in agent.build_payload(d["id"])[0]["content"]


def test_profile_coexists_with_memory_blocks(data_dir):
    """Профиль + WM/LT: все блоки на месте, порядок — профиль до памяти."""
    agent = make_agent(data_dir, ok_handler)
    d = agent.store.new_dialogue()
    agent.store.profile_set(d["id"], "Иван", "роль", "тон", "")
    agent.store.wm_set(d["id"], "t", "задача")
    agent.store.lt_set("u", "юзер")
    agent.store.append_message(d["id"], "user", "привет")
    base = agent.get_config()["system_prompt"]
    expected = (base + "\n\nПрофиль пользователя:\n- Имя: Иван\n"
                "- Роль и сфера: роль\n- Тон и стиль: тон"
                + "\n\nТекущая задача:\n- t: задача"
                + "\n\nДолговременная память:\n- u: юзер"
                + MEMORY_RULE)
    assert agent.build_payload(d["id"])[0]["content"] == expected


# ---------- гард табу-слов (день 12, D8) ----------

def test_taboo_reminder_appended_to_payload(data_dir):
    """active + табу-слово в запросе → system-напоминание в КОНЦЕ messages."""
    seen = {}

    def handler(request):
        seen["messages"] = json.loads(request.content)["messages"]
        return ok_handler(request)

    agent = make_agent(data_dir, handler)
    d = agent.store.new_dialogue()
    agent.store.profile_set(d["id"], "Иван", "backend", "кратко", "мат, эмодзи")
    list(agent.ask_stream(d["id"], "добавь в ответ эмодзи"))
    assert seen["messages"][-1]["role"] == "system"
    assert "эмодзи" in seen["messages"][-1]["content"]
    assert seen["messages"][-2]["role"] == "user"
    # статус профиля не меняется
    assert agent.store.profile_get(d["id"])["status"] == "active"


def test_taboo_reminder_second_token(data_dir):
    """Сплит по запятой: срабатывает и второе табу-слово."""
    seen = {}

    def handler(request):
        seen["messages"] = json.loads(request.content)["messages"]
        return ok_handler(request)

    agent = make_agent(data_dir, handler)
    d = agent.store.new_dialogue()
    agent.store.profile_set(d["id"], "Иван", "", "", "мат, эмодзи")
    list(agent.ask_stream(d["id"], "напиши фразу, используя мат"))
    assert seen["messages"][-1]["role"] == "system"
    assert "мат" in seen["messages"][-1]["content"]
    assert "эмодзи" not in seen["messages"][-1]["content"]  # только найденное


def test_no_taboo_reminder_without_taboo_word(data_dir):
    seen = {}

    def handler(request):
        seen["messages"] = json.loads(request.content)["messages"]
        return ok_handler(request)

    agent = make_agent(data_dir, handler)
    d = agent.store.new_dialogue()
    agent.store.profile_set(d["id"], "Иван", "", "", "мат, эмодзи")
    list(agent.ask_stream(d["id"], "расскажи анекдот"))
    assert seen["messages"][-1]["role"] == "user"


def test_no_taboo_reminder_non_active(data_dir):
    """declined → гард неактивен, даже если табу-поле заполнено."""
    seen = {}

    def handler(request):
        seen["messages"] = json.loads(request.content)["messages"]
        return ok_handler(request)

    agent = make_agent(data_dir, handler)
    d = agent.store.new_dialogue()
    agent.store.profile_set(d["id"], "Иван", "", "", "мат")
    agent.store.profile_action(d["id"], "decline")
    assert agent.store.profile_get(d["id"])["taboos"] == "мат"  # поля сохранились
    list(agent.ask_stream(d["id"], "напиши фразу с матом"))
    assert seen["messages"][-1]["role"] == "user"


def test_no_taboo_reminder_empty_taboos(data_dir):
    seen = {}

    def handler(request):
        seen["messages"] = json.loads(request.content)["messages"]
        return ok_handler(request)

    agent = make_agent(data_dir, handler)
    d = agent.store.new_dialogue()
    agent.store.profile_set(d["id"], "Иван", "роль", "тон", "")
    list(agent.ask_stream(d["id"], "напиши фразу, используя мат"))
    assert seen["messages"][-1]["role"] == "user"


def test_taboo_short_token_skipped(data_dir):
    """Токен короче 2 символов не детектится (шум), остальные — да."""
    seen = {}

    def handler(request):
        seen["messages"] = json.loads(request.content)["messages"]
        return ok_handler(request)

    agent = make_agent(data_dir, handler)
    d = agent.store.new_dialogue()
    agent.store.profile_set(d["id"], "Иван", "", "", "x, мат")
    list(agent.ask_stream(d["id"], "напиши букву x"))
    assert seen["messages"][-1]["role"] == "user"
    list(agent.ask_stream(d["id"], "напиши фразу с матом"))
    assert seen["messages"][-1]["role"] == "system"
    assert "мат" in seen["messages"][-1]["content"]


def test_taboo_coexists_with_conflict_guard(data_dir):
    """Табу-гард и конфликт-гард WM/LT независимы: оба напоминания в хвосте."""
    seen = {}

    def handler(request):
        seen["messages"] = json.loads(request.content)["messages"]
        return ok_handler(request)

    agent = make_agent(data_dir, handler)
    d = agent.store.new_dialogue()
    agent.store.profile_set(d["id"], "Иван", "", "", "мат")
    agent.store.wm_set(d["id"], "Источник", "Яндекс")
    list(agent.ask_stream(d["id"], "напиши ТЗ с матом, источник — гугл"))
    tail = [m for m in seen["messages"][-2:] if m["role"] == "system"]
    assert len(tail) == 2
    joined = " ".join(m["content"] for m in tail)
    assert "мат" in joined and "Источник: Яндекс" in joined


# ---------- правило памяти ----------

def test_memory_rule_forbids_silent_compliance():
    """Правило: память — ограничения, тихое подчинение запрещено; при
    противоречии — вежливый отказ + юмор + решение действовать по памяти."""
    assert "противореч" in MEMORY_RULE
    assert "молча" in MEMORY_RULE
    assert "откажись" in MEMORY_RULE
    assert "шутк" in MEMORY_RULE
    assert "решени" in MEMORY_RULE


def test_memory_rule_prioritizes_memory_over_history():
    """Правило явно ставит память ВЫШЕ запросов диалога (вкл. последних)
    и требует сверять запрос с КАЖДЫМ пунктом до ответа — иначе модель при
    истории диалога трактует противоречащий запрос как «доработку» и
    подчиняется (регрессия: ТЗ с «Источник: Яндекс» vs «напиши ТЗ, где
    источник гугл»)."""
    assert "приоритет" in MEMORY_RULE
    assert "каждым пунктом" in MEMORY_RULE
    # few-shot: без явного примера мелкие модели подчиняются свежему
    # запросу, игнорируя правило (проверено на glm/deepseek/qwen)
    assert "Пример:" in MEMORY_RULE


# ---------- ask_stream: успех ----------

def test_ask_stream_success(data_dir):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        seen["body"] = json.loads(request.content)
        return ok_handler(request)

    agent = make_agent(data_dir, handler)
    d = agent.store.new_dialogue()
    ready(agent, d)
    events = list(agent.ask_stream(d["id"], "привет"))
    assert events[0] == {"type": "delta", "text": "Прив"}
    assert events[1] == {"type": "delta", "text": "ет"}
    done = events[2]
    assert done["type"] == "done"
    assert done["answer"] == "Привет"
    assert done["usage"] == USAGE
    assert done["request_id"] == 1
    # сообщение сохранено в диалоге (assistant — с model; ok_handler отвечает
    # SSE и на non-stream, поэтому авто-заголовок не сгенерирован)
    assert agent.store.get_messages(d["id"]) == [
        {"role": "user", "content": "привет"},
        {"role": "assistant", "content": "Привет", "model": "qwen3.8-27b"},
    ]
    assert agent.store.get_dialogue(d["id"])["title"] == "Новый диалог"
    # запрос ушёл с auth и полными параметрами
    assert seen["url"] == BASE + "/chat/completions"
    assert seen["auth"] == "Bearer test-key"
    assert seen["body"]["stream"] is True
    assert seen["body"]["stream_options"] == {"include_usage": True}
    assert seen["body"]["model"] == "qwen3.8-27b"
    assert seen["body"]["messages"][0]["role"] == "system"
    # сессионные токены
    assert agent.session_tokens() == {"prompt": 10, "completion": 5, "total": 15}
    assert agent.last_usage() == USAGE


# ---------- тумблеры слоёв памяти ----------

def test_payload_st_off_sends_only_current_message(data_dir):
    """ST выключен — в LLM уходит только ТЕКУЩЕЕ сообщение, история нет."""
    seen = {}

    def handler(request):
        if "stream" in json.loads(request.content):
            seen["messages"] = json.loads(request.content)["messages"]
        return ok_handler(request)

    agent = make_agent(data_dir, handler)
    d = agent.store.new_dialogue()
    ready(agent, d)
    list(agent.ask_stream(d["id"], "первое"))
    list(agent.ask_stream(d["id"], "второе"))
    agent.store.set_toggle("st", False)
    list(agent.ask_stream(d["id"], "третье"))
    roles = [m["role"] for m in seen["messages"]]
    assert roles == ["system", "user"]
    assert seen["messages"][-1]["content"] == "третье"
    # включили обратно — история снова уходит
    agent.store.set_toggle("st", True)
    list(agent.ask_stream(d["id"], "четвёртое"))
    # system + 3 хода (user+assistant: первое, второе, третье) + user (четвёртое)
    assert len(seen["messages"]) == 8

def test_payload_wm_lt_off_no_blocks_no_rule(data_dir):
    """WM и LT выключены — блоков памяти и правила в system-промте нет."""
    seen = {}

    def handler(request):
        if "stream" in json.loads(request.content):
            seen["system"] = json.loads(request.content)["messages"][0]["content"]
        return ok_handler(request)

    agent = make_agent(data_dir, handler)
    d = agent.store.new_dialogue()
    ready(agent, d)
    agent.store.wm_set(d["id"], "t", "задача")
    agent.store.lt_set("u", "юзер")
    agent.store.set_toggle("wm", False)
    agent.store.set_toggle("lt", False)
    list(agent.ask_stream(d["id"], "привет"))
    assert "Текущая задача:\n" not in seen["system"]
    assert "Долговременная память:\n" not in seen["system"]
    assert "Правило памяти" not in seen["system"]
    assert "Отключённые слои" in seen["system"]  # оба слоя непустые и off


def test_payload_wm_off_lt_on_keeps_rule(data_dir):
    """WM off + LT on — блок LT и правило на месте, WM-блока нет."""
    seen = {}

    def handler(request):
        if "stream" in json.loads(request.content):
            seen["system"] = json.loads(request.content)["messages"][0]["content"]
        return ok_handler(request)

    agent = make_agent(data_dir, handler)
    d = agent.store.new_dialogue()
    ready(agent, d)
    agent.store.wm_set(d["id"], "t", "задача")
    agent.store.lt_set("u", "юзер")
    agent.store.set_toggle("wm", False)
    list(agent.ask_stream(d["id"], "привет"))
    assert "Текущая задача:\n" not in seen["system"]
    assert "Долговременная память" in seen["system"]
    assert "Правило памяти" in seen["system"]
    assert "Отключённые слои" in seen["system"]  # WM непустой и off


def test_guard_skips_disabled_layers(data_dir):
    """Гард не смотрит отключённые слои."""
    agent = make_agent(data_dir, ok_handler)
    d = agent.store.new_dialogue()
    agent.store.wm_set(d["id"], "Источник", "Яндекс")
    agent.store.lt_set("Стек", "Kotlin")
    # оба включены — оба детектятся
    hits = agent._detect_memory_conflict(d["id"], "Напиши ТЗ: источник гугл, стек питон")
    assert {k for k, _ in hits} == {"Источник", "Стек"}
    agent.store.set_toggle("wm", False)
    hits = agent._detect_memory_conflict(d["id"], "Напиши ТЗ: источник гугл, стек питон")
    assert [k for k, _ in hits] == ["Стек"]
    agent.store.set_toggle("lt", False)
    assert agent._detect_memory_conflict(d["id"], "Напиши ТЗ: источник гугл, стек питон") == []


# ---------- заметка об отключённом слое в system-промте ----------

def test_payload_off_layer_note_present(data_dir):
    """LT off + непустой LT → заметка «слой отключён» в system-промте."""
    seen = {}

    def handler(request):
        if "stream" in json.loads(request.content):
            seen["system"] = json.loads(request.content)["messages"][0]["content"]
        return ok_handler(request)

    agent = make_agent(data_dir, handler)
    d = agent.store.new_dialogue()
    ready(agent, d)
    agent.store.lt_set("SA", "запросы BFF")
    agent.store.set_toggle("lt", False)
    list(agent.ask_stream(d["id"], "привет"))
    assert "Отключённые слои" in seen["system"]
    assert "Долговременная память" in seen["system"]
    assert "«SA: запросы BFF»" not in seen["system"]  # содержимое не светим
    assert "Текущая задача" not in seen["system"]


def test_payload_off_layer_note_absent_when_empty(data_dir):
    """LT off + ПУСТОЙ LT → заметки нет (ссылаться не на что)."""
    seen = {}

    def handler(request):
        if "stream" in json.loads(request.content):
            seen["system"] = json.loads(request.content)["messages"][0]["content"]
        return ok_handler(request)

    agent = make_agent(data_dir, handler)
    d = agent.store.new_dialogue()
    ready(agent, d)
    agent.store.set_toggle("lt", False)
    list(agent.ask_stream(d["id"], "привет"))
    assert "Отключённые слои" not in seen["system"]


def test_payload_off_layer_note_absent_when_on(data_dir):
    """Слои включены → заметки нет."""
    seen = {}

    def handler(request):
        if "stream" in json.loads(request.content):
            seen["system"] = json.loads(request.content)["messages"][0]["content"]
        return ok_handler(request)

    agent = make_agent(data_dir, handler)
    d = agent.store.new_dialogue()
    ready(agent, d)
    agent.store.lt_set("SA", "запросы BFF")
    list(agent.ask_stream(d["id"], "привет"))
    assert "Отключённые слои" not in seen["system"]
    assert "Долговременная память" in seen["system"]


# ---------- server-side гард «запрос ↔ память» ----------

def test_conflict_detection_key_verb_no_value(data_dir):
    agent = make_agent(data_dir, ok_handler)
    d = agent.store.new_dialogue()
    agent.store.wm_set(d["id"], "Источник", "Яндекс")
    hits = agent._detect_memory_conflict(
        d["id"], "Напиши ТЗ, где источник будет гугл")
    assert hits == [("Источник", "Яндекс")]


def test_conflict_detection_no_verb(data_dir):
    """Вопрос про память — не конфликт."""
    agent = make_agent(data_dir, ok_handler)
    d = agent.store.new_dialogue()
    agent.store.wm_set(d["id"], "Источник", "Яндекс")
    assert agent._detect_memory_conflict(d["id"], "Какой источник?") == []


def test_conflict_detection_value_present(data_dir):
    """Значение совпадает — конфликта нет."""
    agent = make_agent(data_dir, ok_handler)
    d = agent.store.new_dialogue()
    agent.store.wm_set(d["id"], "Источник", "Яндекс")
    assert agent._detect_memory_conflict(
        d["id"], "Напиши ТЗ, источник — яндекс") == []


def test_conflict_detection_lt_and_short_key(data_dir):
    """LT тоже проверяется; ключ короче 4 символов пропускается."""
    agent = make_agent(data_dir, ok_handler)
    d = agent.store.new_dialogue()
    agent.store.lt_set("Стек", "Kotlin")
    agent.store.wm_set(d["id"], "ТЗ", "проект каталог")
    hits = agent._detect_memory_conflict(d["id"], "Разработай код, стек — Python")
    assert hits == [("Стек", "Kotlin")]
    # без упоминания ключа в сообщении гард молчит (консервативно)
    assert agent._detect_memory_conflict(d["id"], "Разработай код на Python") == []
    # короткое ключ «ТЗ» в сообщении «Напиши ТЗ» не детектится
    assert agent._detect_memory_conflict(d["id"], "Напиши ТЗ") == []


def test_conflict_reminder_appended_to_payload(data_dir):
    """Конфликт → system-напоминание в КОНЦЕ messages (после user)."""
    seen = {}

    def handler(request):
        seen["messages"] = json.loads(request.content)["messages"]
        return ok_handler(request)

    agent = make_agent(data_dir, handler)
    d = agent.store.new_dialogue()
    ready(agent, d)
    agent.store.wm_set(d["id"], "Источник", "Яндекс")
    list(agent.ask_stream(d["id"], "Напиши ТЗ, источник — гугл"))
    assert seen["messages"][-1]["role"] == "system"
    assert "Источник: Яндекс" in seen["messages"][-1]["content"]
    assert seen["messages"][-2]["role"] == "user"


def test_no_conflict_reminder_without_conflict(data_dir):
    seen = {}

    def handler(request):
        if "stream" in json.loads(request.content):
            seen["messages"] = json.loads(request.content)["messages"]
        return ok_handler(request)

    agent = make_agent(data_dir, handler)
    d = agent.store.new_dialogue()
    ready(agent, d)
    agent.store.wm_set(d["id"], "Источник", "Яндекс")
    list(agent.ask_stream(d["id"], "Расскажи анекдот"))
    assert seen["messages"][-1]["role"] == "user"


# ---------- ask_stream: авто-заголовок и model в сообщениях ----------

def _title_handler(title_response, calls):
    """non-stream (авто-заголовок) → title_response; stream → SSE."""
    def handler(request):
        if request.url.path.endswith("/chat/completions"):
            if "stream" not in json.loads(request.content):
                calls["n"] += 1
                return title_response
            body = sse_body([delta_chunk("ok"), usage_chunk(), "[DONE]"])
            return httpx.Response(200, content=body.encode("utf-8"))
        return httpx.Response(404, json={})

    return handler


def test_auto_title_after_first_message(data_dir):
    """Первое сообщение нового диалога → non-stream запрос → title обновлён."""
    calls = {"n": 0}
    handler = _title_handler(
        httpx.Response(200, json={"choices": [{"message": {"content": " «Про акул». "}}]}),
        calls)
    agent = make_agent(data_dir, handler)
    d = agent.store.new_dialogue()
    events = list(agent.ask_stream(d["id"], "Расскажи про акул"))
    assert events[-1]["type"] == "done"
    assert calls["n"] == 1
    assert agent.store.get_dialogue(d["id"])["title"] == "Про акул"


def test_auto_title_only_for_first_message(data_dir):
    calls = {"n": 0}
    handler = _title_handler(
        httpx.Response(200, json={"choices": [{"message": {"content": "Тайтл"}}]}),
        calls)
    agent = make_agent(data_dir, handler)
    d = agent.store.new_dialogue()
    list(agent.ask_stream(d["id"], "первое"))
    list(agent.ask_stream(d["id"], "второе"))
    assert calls["n"] == 1
    assert agent.store.get_dialogue(d["id"])["title"] == "Тайтл"


def test_auto_title_failure_keeps_default(data_dir):
    """Ошибка генерации заголовка — чат завершается, title по умолчанию."""
    handler = _title_handler(httpx.Response(500, json={"error": "boom"}), {"n": 0})
    agent = make_agent(data_dir, handler)
    d = agent.store.new_dialogue()
    events = list(agent.ask_stream(d["id"], "привет"))
    assert events[-1]["type"] == "done"
    assert agent.store.get_dialogue(d["id"])["title"] == "Новый диалог"


def test_auto_title_extracts_from_thinking_tail(data_dir):
    """glm «думает» в content: название — в конце строки после точки."""
    thinking = ('The user asks about the capital of France. A good title would '
                'be "X" - that is 2 words. Good.Париж')
    handler = _title_handler(
        httpx.Response(200, json={"choices": [{"message": {"content": thinking}}]}),
        {"n": 0})
    agent = make_agent(data_dir, handler)
    d = agent.store.new_dialogue()
    list(agent.ask_stream(d["id"], "Назови столицу Франции"))
    assert agent.store.get_dialogue(d["id"])["title"] == "Париж"


def test_auto_title_skips_truncated_answer(data_dir):
    """finish_reason=length — обрезанный ответ не называем (даже с коротким хвостом)."""
    body = {"choices": [{"message": {"content": 'The user asks "OK". I need to create a short'},
                         "finish_reason": "length"}]}
    handler = _title_handler(httpx.Response(200, json=body), {"n": 0})
    agent = make_agent(data_dir, handler)
    d = agent.store.new_dialogue()
    list(agent.ask_stream(d["id"], "привет"))
    assert agent.store.get_dialogue(d["id"])["title"] == "Новый диалог"


def test_auto_title_rejects_truncated_thinking(data_dir):
    """Обрезанное «размышление» без границы — не мусор в title."""
    garbage = ('The user is asking me to name the capital and I should answer '
               'with a short title for this dialogu')
    handler = _title_handler(
        httpx.Response(200, json={"choices": [{"message": {"content": garbage}}]}),
        {"n": 0})
    agent = make_agent(data_dir, handler)
    d = agent.store.new_dialogue()
    list(agent.ask_stream(d["id"], "Назови столицу Франции"))
    assert agent.store.get_dialogue(d["id"])["title"] == "Новый диалог"


def test_payload_messages_clean_of_model(data_dir):
    """Во второй ход LLM-payload содержит только role/content (без model)."""
    calls = {"n": 0}
    seen = {}
    handler = _title_handler(
        httpx.Response(200, json={"choices": [{"message": {"content": "Т"}}]}),
        calls)

    def handler_with_capture(request):
        if "stream" in json.loads(request.content):
            seen["messages"] = json.loads(request.content)["messages"]
        return handler(request)

    agent = make_agent(data_dir, handler_with_capture)
    d = agent.store.new_dialogue()
    ready(agent, d)
    list(agent.ask_stream(d["id"], "первое"))
    list(agent.ask_stream(d["id"], "второе"))
    assert all(set(m.keys()) == {"role", "content"} for m in seen["messages"])


# ---------- LLM-экстракт профиля (день 12) ----------

def _profile_extract_handler(content_or_status, calls):
    """non-stream → str (JSON-ответ с content) или int (HTTP-статус)."""
    def handler(request):
        assert "stream" not in json.loads(request.content)
        calls["n"] += 1
        if isinstance(content_or_status, int):
            return httpx.Response(content_or_status, json={"error": "boom"})
        return httpx.Response(200, json={"choices": [
            {"message": {"content": content_or_status}}]})
    return handler


def test_extract_profile_valid_json(data_dir):
    calls = {"n": 0}
    agent = make_agent(data_dir, _profile_extract_handler(
        '{"name": "Иван", "role": "backend", "tone": "кратко", "taboos": "мат"}',
        calls))
    p = agent._extract_profile("qwen3.8-27b", "Иван, backend, кратко, не мат")
    assert p == {"name": "Иван", "role": "backend", "tone": "кратко", "taboos": "мат"}
    assert calls["n"] == 1


def test_extract_profile_fenced_with_garbage(data_dir):
    """Модель «думает» + markdown-ограды: вырезаем объект по {...}."""
    content = ('Хорошо, извлекаю поля.\n```json\n'
               '{"name": "Мария", "role": "дизайнер", "tone": "формально", '
               '"taboos": "шутки"}\n```')
    agent = make_agent(data_dir, _profile_extract_handler(content, {"n": 0}))
    p = agent._extract_profile("glm-5.3-flash", "ответ")
    assert p["name"] == "Мария"
    assert p["taboos"] == "шутки"


def test_extract_profile_missing_fields_become_empty(data_dir):
    content = '{"name": "Иван", "role": "", "tone": "", "taboos": ""}'
    agent = make_agent(data_dir, _profile_extract_handler(content, {"n": 0}))
    assert agent._extract_profile("qwen3.8-27b", "только имя Иван") == \
        {"name": "Иван", "role": "", "tone": "", "taboos": ""}


def test_extract_profile_non_json_returns_none(data_dir):
    agent = make_agent(data_dir, _profile_extract_handler(
        "не удалось разобрать", {"n": 0}))
    assert agent._extract_profile("qwen3.8-27b", "мусор") is None


def test_extract_profile_api_error_returns_none(data_dir):
    agent = make_agent(data_dir, _profile_extract_handler(500, {"n": 0}))
    assert agent._extract_profile("qwen3.8-27b", "ответ") is None


# ---------- state machine инициализации профиля (день 12) ----------

def _profile_handler(extract_content, calls, streams):
    """non-stream: 1-й — авто-заголовок (пустой), 2-й — экстракт; stream → SSE."""
    def handler(request):
        body = json.loads(request.content)
        if "stream" not in body:
            calls["n"] += 1
            if calls["n"] == 1:  # авто-заголовок
                return httpx.Response(200, json={"choices": [
                    {"message": {"content": "Тайтл"}}]})
            return httpx.Response(200, json={"choices": [
                {"message": {"content": extract_content}}]})
        streams.append(body)
        b = sse_body([delta_chunk("ок"), usage_chunk(), "[DONE]"])
        return httpx.Response(200, content=b.encode("utf-8"))
    return handler


def test_pending_first_message_gets_invite_not_llm(data_dir):
    calls, streams = {"n": 0}, []
    agent = make_agent(data_dir, _profile_handler("{}", calls, streams))
    d = agent.store.new_dialogue()
    events = list(agent.ask_stream(d["id"], "объясни лямбды"))
    assert len(streams) == 0  # LLM-стрим НЕ вызывался
    done = events[-1]
    assert done["type"] == "done"
    assert done["usage"] is None and done["request_id"] is None
    assert "инициализировать профиль" in done["answer"]
    assert "вручную" in done["answer"] and "интервью" in done["answer"]
    assert "отказ" in done["answer"]
    # user + assistant сохранены; запрос в journal не ушёл
    msgs = agent.store.get_messages(d["id"])
    assert msgs[0] == {"role": "user", "content": "объясни лямбды"}
    assert msgs[1]["role"] == "assistant"
    assert agent.requests_list() == []


def test_pending_unrecognized_repeats_invite(data_dir):
    calls, streams = {"n": 0}, []
    agent = make_agent(data_dir, _profile_handler("{}", calls, streams))
    d = agent.store.new_dialogue()
    list(agent.ask_stream(d["id"], "привет"))
    events = list(agent.ask_stream(d["id"], "расскажи про акул"))
    assert len(streams) == 0
    assert "инициализировать профиль" in events[-1]["answer"]
    assert agent.store.profile_get(d["id"])["status"] == "pending"


def test_pending_manual_marker_points_to_tab(data_dir):
    calls, streams = {"n": 0}, []
    agent = make_agent(data_dir, _profile_handler("{}", calls, streams))
    d = agent.store.new_dialogue()
    list(agent.ask_stream(d["id"], "вручную"))
    assert len(streams) == 0
    assert agent.store.profile_get(d["id"])["status"] == "pending"
    assert agent.store.profile_get(d["id"])["interview"] is False


def test_pending_decline_marker_sets_declined_then_normal_flow(data_dir):
    calls, streams = {"n": 0}, []
    agent = make_agent(data_dir, _profile_handler("{}", calls, streams))
    d = agent.store.new_dialogue()
    events = list(agent.ask_stream(d["id"], "отказ"))
    assert agent.store.profile_get(d["id"])["status"] == "declined"
    assert "отказ" in events[-1]["answer"].lower() or "обычно" in events[-1]["answer"]
    # следующий ход — уже обычный LLM-поток, без блока профиля
    list(agent.ask_stream(d["id"], "привет"))
    assert len(streams) == 1
    assert "Профиль пользователя" not in streams[0]["messages"][0]["content"]


def test_pending_interview_flow_creates_active_profile(data_dir):
    extract = ('{"name": "Иван", "role": "backend", "tone": "кратко", '
               '"taboos": "мат"}')
    calls, streams = {"n": 0}, []
    agent = make_agent(data_dir, _profile_handler(extract, calls, streams))
    d = agent.store.new_dialogue()
    # 1) выбор интервью
    e1 = list(agent.ask_stream(d["id"], "интервью"))
    p = agent.store.profile_get(d["id"])
    assert p["status"] == "pending" and p["interview"] is True
    assert "имя" in e1[-1]["answer"].lower()
    assert "стоп-слова" in e1[-1]["answer"].lower() or "табу" in e1[-1]["answer"].lower()
    # 2) ответ на анкету → экстракт → active
    e2 = list(agent.ask_stream(d["id"], "Иван, backend-разработчик, кратко, не мат"))
    p = agent.store.profile_get(d["id"])
    assert p["status"] == "active" and p["name"] == "Иван" and p["taboos"] == "мат"
    assert "сохранён" in e2[-1]["answer"].lower() or "профиль" in e2[-1]["answer"].lower()
    # 3) следующий ход — обычный LLM-поток С блоком профиля
    list(agent.ask_stream(d["id"], "привет"))
    assert len(streams) == 1
    assert "Профиль пользователя" in streams[0]["messages"][0]["content"]
    assert "Иван" in streams[0]["messages"][0]["content"]


def test_pending_interview_failed_extraction_repeats_questions(data_dir):
    calls, streams = {"n": 0}, []
    agent = make_agent(data_dir, _profile_handler("не разобрать", calls, streams))
    d = agent.store.new_dialogue()
    list(agent.ask_stream(d["id"], "интервью"))
    events = list(agent.ask_stream(d["id"], "мусор без полей"))
    p = agent.store.profile_get(d["id"])
    assert p["status"] == "pending" and p["interview"] is True
    assert "повтор" in events[-1]["answer"].lower()
    assert len(streams) == 0


def test_reset_back_to_pending_repeats_invite(data_dir):
    calls, streams = {"n": 0}, []
    agent = make_agent(data_dir, _profile_handler("{}", calls, streams))
    d = agent.store.new_dialogue()
    agent.store.profile_set(d["id"], "Иван", "", "", "")
    assert agent.store.profile_get(d["id"])["status"] == "active"
    agent.store.profile_action(d["id"], "reset")
    events = list(agent.ask_stream(d["id"], "привет"))
    assert len(streams) == 0
    assert "инициализировать профиль" in events[-1]["answer"]


# ---------- ask_stream: ошибки ----------

def test_ask_stream_connect_error(data_dir):
    def handler(request):
        raise httpx.ConnectError("нет сети", request=request)

    agent = make_agent(data_dir, handler)
    d = agent.store.new_dialogue()
    ready(agent, d)
    events = list(agent.ask_stream(d["id"], "привет"))
    assert len(events) == 1
    assert events[0]["type"] == "error"
    assert "Ошибка" in events[0]["message"]
    # user-сообщение осталось, assistant — нет
    msgs = agent.store.get_messages(d["id"])
    assert msgs == [{"role": "user", "content": "привет"}]
    # запись в журнале с ошибкой
    reqs = agent.requests_list()
    assert len(reqs) == 1
    assert reqs[0]["error"] is not None
    assert reqs[0]["total_tokens"] is None
    assert agent.session_tokens() == {"prompt": 0, "completion": 0, "total": 0}
    assert agent.last_usage() is None


def test_ask_stream_http_500(data_dir):
    def handler(request):
        return httpx.Response(500, text="internal error")

    agent = make_agent(data_dir, handler)
    d = agent.store.new_dialogue()
    ready(agent, d)
    events = list(agent.ask_stream(d["id"], "привет"))
    assert events == [{"type": "error", "message": "Модель вернула ошибку HTTP 500"}]
    assert agent.requests_list()[0]["error"] is not None


def test_ask_stream_broken_chunk(data_dir):
    def handler(request):
        return httpx.Response(200,
                              content="data: {битый json\n\ndata: [DONE]\n\n".encode("utf-8"))

    agent = make_agent(data_dir, handler)
    d = agent.store.new_dialogue()
    ready(agent, d)
    events = list(agent.ask_stream(d["id"], "привет"))
    assert len(events) == 1
    assert events[0]["type"] == "error"
    assert agent.requests_list()[0]["error"] is not None


# ---------- журнал ----------

def test_journal_list_and_get(data_dir):
    agent = make_agent(data_dir, ok_handler)
    d = agent.store.new_dialogue()
    ready(agent, d)
    list(agent.ask_stream(d["id"], "первый"))
    list(agent.ask_stream(d["id"], "второй"))
    lst = agent.requests_list()
    assert [r["id"] for r in lst] == [1, 2]
    for r in lst:
        assert set(r) == {"id", "ts", "model", "total_tokens", "error"}  # без тел
        assert r["model"] == "qwen3.8-27b"
        assert r["total_tokens"] == 15
    full = agent.requests_get(1)
    assert full["request"]["messages"][-1] == {"role": "user", "content": "первый"}
    assert full["usage"] == USAGE
    assert full["error"] is None
    assert agent.requests_get(99) is None


def test_journal_cap_100_fifo(data_dir):
    def minimal(request):
        return httpx.Response(200, content=sse_body([delta_chunk("x"), "[DONE]"]).encode())

    agent = make_agent(data_dir, minimal)
    d = agent.store.new_dialogue()
    ready(agent, d)
    for i in range(105):
        list(agent.ask_stream(d["id"], f"сообщение {i}"))
    lst = agent.requests_list()
    assert len(lst) == 100
    assert [r["id"] for r in lst] == list(range(6, 106))  # старые отброшены
    assert agent.requests_get(6) is not None
    assert agent.requests_get(5) is None


def test_requests_clear(data_dir):
    agent = make_agent(data_dir, ok_handler)
    d = agent.store.new_dialogue()
    ready(agent, d)
    list(agent.ask_stream(d["id"], "x"))
    agent.requests_clear()
    assert agent.requests_list() == []
    # после очистки id снова с 1
    list(agent.ask_stream(d["id"], "y"))
    assert [r["id"] for r in agent.requests_list()] == [1]


# ---------- список моделей ----------

def test_list_models_filters_unavailable(data_dir):
    """В списке только модели, прошедшие зонд: 403 «нет доступа у ключа» — мимо."""
    def handler(request):
        if request.url.path.endswith("/models"):
            assert request.headers.get("Authorization") == "Bearer test-key"
            return httpx.Response(200, json={"data": [
                {"id": "qwen3.8-27b"},
                {"id": "deepseek-v4-flash"},
                {"id": "модель-неизвестная"},
            ]})
        body = json.loads(request.content)
        if body["model"] == "deepseek-v4-flash":
            return httpx.Response(403, json={"message": "Api key not allowed"})
        return httpx.Response(200, json={"choices": []})

    agent = make_agent(data_dir, handler)
    models = agent.list_models()
    assert models == [
        {"id": "qwen3.8-27b", "context_limit": 32768},
        {"id": "модель-неизвестная", "context_limit": 32768},
    ]
    assert CONTEXT_LIMITS["glm-5.3-flash"] == 16384


def test_list_models_probe_error_excludes_model(data_dir):
    """Сетевой сбой зонда модели — модель не в списке, остальные остаются."""
    def handler(request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "a"}, {"id": "b"}]})
        if json.loads(request.content)["model"] == "b":
            raise httpx.ConnectError("нет сети", request=request)
        return httpx.Response(200, json={})

    agent = make_agent(data_dir, handler)
    assert [m["id"] for m in agent.list_models()] == ["a"]


def test_list_models_probe_cached(data_dir):
    """Повторный вызов в пределах TTL не повторяет зонды."""
    probes = {"n": 0}

    def handler(request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "qwen3.8-27b"}]})
        probes["n"] += 1
        return httpx.Response(200, json={})

    agent = make_agent(data_dir, handler)
    agent.list_models()
    agent.list_models()
    assert probes["n"] == 1


def test_probe_uses_model_specific_key(data_dir):
    """Зонд берёт ключ по модели (MODEL_KEY_ENV): 3 ключа -> 3 модели."""
    env = {"GPUSTACK_API_KEY": "k-main",
           "GPUSTACK_KEY_DEEPSEEK": "k-deep",
           "GPUSTACK_KEY_GLM": "k-glm"}

    def handler(request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [
                {"id": "qwen3.8-27b"}, {"id": "deepseek-v4-flash"},
                {"id": "glm-5.3-flash"}]})
        model = json.loads(request.content)["model"]
        wanted = {"qwen3.8-27b": "k-main", "deepseek-v4-flash": "k-deep",
                  "glm-5.3-flash": "k-glm"}[model]
        if request.headers["Authorization"] == "Bearer " + wanted:
            return httpx.Response(200, json={})
        return httpx.Response(403, json={})

    agent = make_agent(data_dir, handler, env=env)
    models = {m["id"] for m in agent.list_models()}
    assert models == {"qwen3.8-27b", "deepseek-v4-flash", "glm-5.3-flash"}


def test_chat_uses_model_specific_key(data_dir):
    """Чат шлёт ключ, привязанный к выбранной модели."""
    env = {"GPUSTACK_API_KEY": "k-main",
           "GPUSTACK_KEY_DEEPSEEK": "k-deep",
           "GPUSTACK_KEY_GLM": "k-glm"}
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("Authorization")
        body = sse_body([delta_chunk("ok"), usage_chunk(), "[DONE]"])
        return httpx.Response(200, content=body.encode("utf-8"))

    agent = make_agent(data_dir, handler, env=env)
    agent.set_config({"model": "deepseek-v4-flash"})
    d = agent.store.new_dialogue()
    ready(agent, d)
    events = list(agent.ask_stream(d["id"], "привет"))
    assert events[-1]["type"] == "done"
    assert seen["auth"] == "Bearer k-deep"


def test_probe_unknown_model_falls_back_to_main_key(data_dir):
    """Модель вне маппинга MODEL_KEY_ENV — зонд переменной DEFAULT_KEY_ENV."""
    env = {"GPUSTACK_API_KEY": "k-main"}
    seen = {}

    def handler(request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "gpt-4o"}]})
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={})

    agent = make_agent(data_dir, handler, env=env)
    agent.list_models()
    assert seen["auth"] == "Bearer k-main"


def test_ensure_model_available_resets_unavailable(data_dir):
    """Модель из конфига недоступна (403) — сброс на первую доступную, persist."""
    def handler(request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "qwen3.8-27b"}, {"id": "b"}]})
        if json.loads(request.content)["model"] == "b":
            return httpx.Response(403, json={})
        return httpx.Response(200, json={})

    agent = make_agent(data_dir, handler)
    agent.set_config({"model": "whisper-large-v3-turbo"})
    agent.ensure_model_available()
    assert agent.get_config()["model"] == "qwen3.8-27b"


def test_ensure_model_available_keeps_available(data_dir):
    def handler(request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "qwen3.8-27b"}, {"id": "b"}]})
        return httpx.Response(200, json={})

    agent = make_agent(data_dir, handler)
    agent.set_config({"model": "qwen3.8-27b"})
    agent.ensure_model_available()
    assert agent.get_config()["model"] == "qwen3.8-27b"


def test_ensure_model_available_api_down_no_change(data_dir):
    """API недоступен — конфиг не трогаем (неизвестно, что доступно)."""
    def handler(request):
        raise httpx.ConnectError("нет сети", request=request)

    agent = make_agent(data_dir, handler)
    agent.set_config({"model": "ghost-model"})
    agent.ensure_model_available()
    assert agent.get_config()["model"] == "ghost-model"


def test_list_models_unavailable_raises(data_dir):
    def handler(request):
        raise httpx.ConnectError("нет сети", request=request)

    agent = make_agent(data_dir, handler)
    with pytest.raises(httpx.HTTPError):
        agent.list_models()


# ---------- задача: оркестратор stage-агентов (день 13) ----------

def make_task_script_handler(script):
    """Mock-LLM для пайплайна задачи: non-stream, ответ — по роли стадии.

    script: {"planning": str, "execution": [str, ...], "validation": [str, ...]}
    Возвращает (handler, calls); calls — список system-промптов вызовов.
    """
    state = {"execution": list(script.get("execution", [])),
             "validation": list(script.get("validation", []))}
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        system = (payload["messages"][0] or {}).get("content", "")
        calls.append(system)
        if "Планировщик" in system:
            text = script["planning"]
        elif "Исполнитель" in system:
            text = state["execution"].pop(0)
        elif "Валидатор" in system:
            text = state["validation"].pop(0)
        else:
            text = "ИТОГОВОЕ ОТВЕЧЕНИЕ"
        return httpx.Response(200, json={"choices": [
            {"message": {"content": text}}]})

    return handler, calls


def task_ready(agent):
    """Диалог: профиль declined (день 12) — готов к обычным запросам."""
    d = agent.store.new_dialogue()
    ready(agent, d)
    return d


def test_task_run_full_cycle(data_dir):
    handler, calls = make_task_script_handler({
        "planning": "1. Шаг А\n2. Шаг Б",
        "execution": ["Код готов"],
        "validation": ["Всё ок <verdict>pass</verdict>"],
    })
    agent = make_agent(data_dir, handler)
    d = task_ready(agent)
    agent.store.task_new(d["id"], "Сделать кнопку")
    events = list(agent.task_run(d["id"]))
    kinds = [(e["type"], e.get("stage")) for e in events]
    assert ("stage", "planning") in kinds
    assert ("stage_done", "planning") in kinds
    assert ("stage", "execution") in kinds
    assert ("stage", "validation") in kinds
    assert ("stage", "done") in kinds
    assert events[-1]["type"] == "task_done"
    t = agent.store.task_get(d["id"])
    assert t["active"] is True and t["stage"] == "done"
    assert t["stages"]["planning"]["output"] == "1. Шаг А\n2. Шаг Б"
    assert t["stages"]["validation"]["verdict"] == "pass"
    # инъекция state: у Исполнителя — план, у Валидатора — план и работа
    assert any("Планировщик" in s and "Сделать кнопку" in s for s in calls)
    assert any("Исполнитель" in s and "1. Шаг А" in s for s in calls)
    assert any("Валидатор" in s and "Код готов" in s for s in calls)
    # stage-сообщения в истории с меткой, финальный ответ — обычный assistant
    msgs = agent.store.get_messages(d["id"])
    stage_msgs = [m for m in msgs if m.get("task_stage")]
    assert [m["task_stage"] for m in stage_msgs] == \
        ["planning", "execution", "validation"]
    assert msgs[-1].get("task_stage") is None
    assert msgs[-1]["role"] == "assistant"


def test_task_run_retry_on_validation_fail(data_dir):
    handler, calls = make_task_script_handler({
        "planning": "1. Шаг",
        "execution": ["Криво", "Правильно"],
        "validation": ["Не то <verdict>fail</verdict>",
                       "Ок <verdict>pass</verdict>"],
    })
    agent = make_agent(data_dir, handler)
    d = task_ready(agent)
    agent.store.task_new(d["id"], "X")
    events = list(agent.task_run(d["id"]))
    t = agent.store.task_get(d["id"])
    assert t["stage"] == "done"
    assert t["retries"] == 1
    assert t["stages"]["execution"]["output"] == "Правильно"
    assert t["stages"]["execution"]["attempts"] == 2
    assert any(e["type"] == "stage_done" and e.get("retry") for e in events)
    # фидбэк: промпт повторного Исполнителя — старая работа + замечания
    executor_prompts = [s for s in calls if "Исполнитель" in s]
    assert len(executor_prompts) == 2
    assert "Криво" in executor_prompts[1]
    assert "Не то" in executor_prompts[1]


def test_task_run_second_fail_goes_done(data_dir):
    handler, calls = make_task_script_handler({
        "planning": "1. Шаг",
        "execution": ["Криво", "Снова криво"],
        "validation": ["Нет <verdict>fail</verdict>",
                       "Снова нет <verdict>fail</verdict>"],
    })
    agent = make_agent(data_dir, handler)
    d = task_ready(agent)
    agent.store.task_new(d["id"], "X")
    events = list(agent.task_run(d["id"]))
    t = agent.store.task_get(d["id"])
    assert t["stage"] == "done"
    assert t["retries"] == 1
    assert t["stages"]["validation"]["verdict"] == "fail"
    assert events[-1]["type"] == "task_done"


def test_task_run_missing_verdict_treated_pass(data_dir):
    handler, calls = make_task_script_handler({
        "planning": "1. Шаг", "execution": ["Код"],
        "validation": ["Видимо ок"],
    })
    agent = make_agent(data_dir, handler)
    d = task_ready(agent)
    agent.store.task_new(d["id"], "X")
    events = list(agent.task_run(d["id"]))
    t = agent.store.task_get(d["id"])
    assert t["stage"] == "done"
    assert t["stages"]["validation"]["verdict"] == "pass"
    assert events[-1]["type"] == "task_done"


def test_task_run_pause_between_stages(data_dir):
    handler, calls = make_task_script_handler({
        "planning": "План", "execution": ["Работа"],
        "validation": ["<verdict>pass</verdict>"],
    })
    agent = make_agent(data_dir, handler)
    d = task_ready(agent)
    agent.store.task_new(d["id"], "X")
    # пауза на границе: после stage_done(planning) — флаг; execution не стартует
    for e in agent.task_run(d["id"]):
        if e["type"] == "stage_done" and e["stage"] == "planning":
            agent.store.task_pause(d["id"])
            break
    t = agent.store.task_get(d["id"])
    assert t["paused"] is True
    assert t["stage"] == "execution"
    assert "execution" not in t["stages"]
    # повторный run на паузе — сразу task_paused, LLM не вызывается
    before = len(calls)
    events2 = list(agent.task_run(d["id"]))
    assert events2 == [{"type": "task_paused", "stage": "execution"}]
    assert len(calls) == before
    # resume — продолжение с сохранённого состояния, planning не повторяется
    agent.store.task_resume(d["id"])
    before_planner = len([s for s in calls if "Планировщик" in s])
    events3 = list(agent.task_run(d["id"]))
    assert events3[-1]["type"] == "task_done"
    assert agent.store.task_get(d["id"])["stage"] == "done"
    after_planner = len([s for s in calls if "Планировщик" in s])
    assert after_planner == before_planner


def test_task_run_instruction_applied_once(data_dir):
    handler, calls = make_task_script_handler({
        "planning": "План", "execution": ["Работа"],
        "validation": ["<verdict>pass</verdict>"],
    })
    agent = make_agent(data_dir, handler)
    d = task_ready(agent)
    agent.store.task_new(d["id"], "X")
    agent.store.task_pause(d["id"])
    agent.store.task_set_instruction(d["id"], "Используй Kotlin")
    agent.store.task_resume(d["id"])
    list(agent.task_run(d["id"]))
    planner = next(s for s in calls if "Планировщик" in s)
    assert "Используй Kotlin" in planner
    executor = next(s for s in calls if "Исполнитель" in s)
    assert "Используй Kotlin" not in executor
    assert agent.store.task_get(d["id"])["instruction"] == ""


def test_task_run_stage_error_and_retry_stage(data_dir):
    state = {"fail_first": True}
    handler, calls = make_task_script_handler({
        "planning": "План", "execution": ["Р"],
        "validation": ["<verdict>pass</verdict>"],
    })

    def flaky(request: httpx.Request) -> httpx.Response:
        if state["fail_first"]:
            state["fail_first"] = False
            payload = json.loads(request.content)
            system = (payload["messages"][0] or {}).get("content", "")
            if "Планировщик" in system:
                return httpx.Response(500, json={"error": "boom"})
        return handler(request)

    agent = make_agent(data_dir, flaky)
    d = task_ready(agent)
    agent.store.task_new(d["id"], "X")
    events = list(agent.task_run(d["id"]))
    assert events[-1]["type"] == "error"
    t = agent.store.task_get(d["id"])
    assert t["error"] is not None
    assert t["paused"] is True
    assert t["stage"] == "planning"
    # повтор после resume — та же стадия, дальше — полный прогон
    agent.store.task_resume(d["id"])
    events2 = list(agent.task_run(d["id"]))
    assert events2[-1]["type"] == "task_done"
    assert agent.store.task_get(d["id"])["stage"] == "done"


def test_task_run_inactive_task(data_dir):
    agent = make_agent(data_dir, ok_handler)
    d = agent.store.new_dialogue()
    events = list(agent.task_run(d["id"]))
    assert events == [{"type": "error", "message": "Задача не активна"}]


def test_ask_stream_blocked_by_active_task(data_dir):
    agent = make_agent(data_dir, ok_handler)
    d = task_ready(agent)
    agent.store.task_new(d["id"], "X")
    events = list(agent.ask_stream(d["id"], "привет"))
    assert len(events) == 1
    assert events[0]["type"] == "error"
    assert "Задача выполняется" in events[0]["message"]
    assert agent.store.get_messages(d["id"]) == []  # не сохранено
    # на паузе чат работает
    agent.store.task_pause(d["id"])
    events2 = list(agent.ask_stream(d["id"], "привет"))
    assert events2[-1]["type"] == "done"
