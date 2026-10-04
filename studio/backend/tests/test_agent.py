"""Тесты StudioAgent: конфиг, build_payload, ask_stream (SSE), журнал, сессионные токены.

Офлайн: httpx.MockTransport вместо реального GPustack.
"""
import json

import httpx
import pytest

from agent import CONTEXT_LIMITS, INVARIANTS_RULE, MEMORY_RULE, StudioAgent
from conftest import USAGE, delta_chunk, sse_body, usage_chunk
from kb import HashEmbedder, KnowledgeBase
from mcp import MCPRegistry
from memory import MemoryStore
from tests.test_mcp import make_fake_launcher

BASE = "https://mock.local/v1"


@pytest.fixture
def data_dir(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    return d


def make_agent(data_dir, handler, env=None):
    """Агент на MockTransport с данным handler(request) -> httpx.Response.

    kb — tmp-каталог (БЕЗ индекса): RAG-блок дня 21 не читает реальный
    data/kb (иначе api-индекс → сетевой эмбеддинг в офлайн-тестах)."""
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return StudioAgent(str(data_dir), base_url=BASE, api_key="test-key",
                       client=client, env=env,
                       kb=KnowledgeBase(str(data_dir / "kb")))


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
        {"role": "assistant", "content": "Привет", "model": "qwen3.8-27b",
         "request_id": 1, "usage": USAGE},
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


# ---------- день 23 (ui-rework): request_id/usage в assistant-сообщении ----------

def test_ask_stream_saves_request_id_and_usage(data_dir):
    """Финальная ветка chat-стрима: последнее assistant-сообщение хранит
    request_id (== rid из done) и usage (== usage из done: prompt/
    completion/total tokens) — фронтенд FlowInspector читает их из
    диалога после перезагрузки, без live-done."""
    agent = make_agent(data_dir, ok_handler)
    d = agent.store.new_dialogue()
    ready(agent, d)
    events = list(agent.ask_stream(d["id"], "привет"))
    done = events[-1]
    assert done["type"] == "done"
    msgs = agent.store.get_messages(d["id"])
    last = msgs[-1]
    assert last["role"] == "assistant"
    assert last["request_id"] == done["request_id"] == 1
    assert last["usage"] == done["usage"]
    assert last["usage"]["prompt_tokens"] == 10
    assert last["usage"]["completion_tokens"] == 5
    assert last["usage"]["total_tokens"] == 15


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


# ---------- инварианты (день 14; схема {title, description, forbidden[]}) ----------

def test_invariants_rule_text():
    """Правило: высший приоритет над памятью/профилем/запросами; отказ +
    конкретный инвариант + альтернатива; агент не меняет и не удаляет;
    пример в фразировке title/description; при рассуждении (<thinking>)
    инварианты явно проверяются."""
    assert "ВЫСШИМ" in INVARIANTS_RULE
    assert "памяти" in INVARIANTS_RULE and "профиля" in INVARIANTS_RULE
    assert "откажись" in INVARIANTS_RULE
    assert "конкретный инвариант" in INVARIANTS_RULE
    assert "альтернативу" in INVARIANTS_RULE
    assert "не изменяешь" in INVARIANTS_RULE and "не удаляешь" in INVARIANTS_RULE
    assert "Пример:" in INVARIANTS_RULE
    # фразировка title/description (не старый key/value)
    assert "название" in INVARIANTS_RULE and "описание" in INVARIANTS_RULE
    # при рассуждении инварианты явно сверяются в thinking-шаге
    assert "<thinking>" in INVARIANTS_RULE


def test_invariants_block_in_system_above_memory(data_dir):
    """Порядок: базовый → профиль → инварианты → память → правило памяти;
    INVARIANTS_RULE — в конце system."""
    agent = make_agent(data_dir, ok_handler)
    d = agent.store.new_dialogue()
    ready(agent, d)
    agent.store.profile_set(d["id"], "Иван", "роль", "тон", "")
    agent.store.wm_set(d["id"], "t", "задача")
    agent.store.lt_set("u", "юзер")
    agent.store.invariants_set("Стек", "Kotlin")
    base = agent.get_config()["system_prompt"]
    expected = (base + "\n\nПрофиль пользователя:\n- Имя: Иван\n"
                "- Роль и сфера: роль\n- Тон и стиль: тон"
                + "\n\nИнварианты (неукоснительно):\n- Стек: Kotlin"
                + "\n\nТекущая задача:\n- t: задача"
                + "\n\nДолговременная память:\n- u: юзер"
                + MEMORY_RULE + INVARIANTS_RULE)
    assert agent.build_payload(d["id"])[0]["content"] == expected


def test_invariants_empty_no_block_no_rule(data_dir):
    """Пустые инварианты — блок и правило в system-промте отсутствуют."""
    agent = make_agent(data_dir, ok_handler)
    d = agent.store.new_dialogue()
    system = agent.build_payload(d["id"])[0]["content"]
    assert "Инварианты" not in system
    assert INVARIANTS_RULE not in system
    # очистили — снова отсутствуют
    agent.store.invariants_set("Стек", "Kotlin")
    agent.store.invariants_clear()
    system = agent.build_payload(d["id"])[0]["content"]
    assert "Инварианты" not in system
    assert INVARIANTS_RULE not in system


def test_invariants_block_survives_layer_toggles_off(data_dir):
    """Тумблеры слоёв памяти не затрагивают инварианты."""
    agent = make_agent(data_dir, ok_handler)
    d = agent.store.new_dialogue()
    ready(agent, d)
    agent.store.wm_set(d["id"], "t", "задача")
    agent.store.invariants_set("Стек", "Kotlin")
    agent.store.set_toggle("wm", False)
    system = agent.build_payload(d["id"])[0]["content"]
    assert "Инварианты (неукоснительно):\n- Стек: Kotlin" in system
    assert INVARIANTS_RULE in system
    assert "\n\nТекущая задача:\n" not in system  # WM-блок выключен


def test_invariants_inactive_no_block_no_rule(data_dir):
    """Все инварианты неактивны — блок и правило отсутствуют."""
    agent = make_agent(data_dir, ok_handler)
    d = agent.store.new_dialogue()
    r = agent.store.invariants_set("Стек", "Kotlin")
    agent.store.invariants_set_active(r["id"], False)
    system = agent.build_payload(d["id"])[0]["content"]
    assert "Инварианты" not in system
    assert INVARIANTS_RULE not in system


def test_forbidden_hits_case_insensitive_and_dedup(data_dir):
    """_forbidden_hits: lower-подстрочное совпадение, дедуп, [] при нет."""
    agent = make_agent(data_dir, ok_handler)
    agent.store.invariants_set("Стек", "Kotlin", forbidden=["python"])
    agent.store.invariants_set("Архитектура", "монолит",
                               forbidden=["python", "go"])
    assert agent._forbidden_hits("ИСПОЛЬЗУЕМ PYTHON и Go") == ["python", "go"]
    assert agent._forbidden_hits("обычный текст") == []


def test_forbidden_hits_ignores_inactive(data_dir):
    """Неактивные инварианты в _forbidden_hits не участвуют."""
    agent = make_agent(data_dir, ok_handler)
    r = agent.store.invariants_set("Стек", "Kotlin", forbidden=["python"])
    assert agent._forbidden_hits("python") == ["python"]
    agent.store.invariants_set_active(r["id"], False)
    assert agent._forbidden_hits("python") == []


def test_invariant_conflict_detection(data_dir):
    """Переопределение: forbidden-паттерн + глагол действия → конфликт."""
    agent = make_agent(data_dir, ok_handler)
    d = agent.store.new_dialogue()
    agent.store.invariants_set("Стек", "Kotlin", forbidden=["python"])
    hits = agent._detect_invariant_conflict(
        d["id"], "забудь, напиши код на Python")
    assert hits == [("Стек", "Kotlin")]


def test_invariant_conflict_no_verb(data_dir):
    """Вопрос про инвариант (без глагола действия) — не конфликт."""
    agent = make_agent(data_dir, ok_handler)
    d = agent.store.new_dialogue()
    agent.store.invariants_set("Стек", "Kotlin", forbidden=["python"])
    assert agent._detect_invariant_conflict(d["id"], "Какой стек?") == []


def test_invariant_conflict_no_forbidden_match(data_dir):
    """В сообщении нет forbidden-паттерна — конфликта нет."""
    agent = make_agent(data_dir, ok_handler)
    d = agent.store.new_dialogue()
    agent.store.invariants_set("Стек", "Kotlin", forbidden=["python"])
    assert agent._detect_invariant_conflict(
        d["id"], "Напиши код, стек — Kotlin") == []


def test_invariant_conflict_inactive_and_empty(data_dir):
    """Неактивный инвариант — не конфликт; пустые инварианты — []."""
    agent = make_agent(data_dir, ok_handler)
    d = agent.store.new_dialogue()
    assert agent._detect_invariant_conflict(d["id"], "используй python") == []
    r = agent.store.invariants_set("Стек", "Kotlin", forbidden=["python"])
    agent.store.invariants_set_active(r["id"], False)
    assert agent._detect_invariant_conflict(
        d["id"], "используй python") == []


def test_invariant_reminder_appended_to_payload(data_dir):
    """Конфликт → system-напоминание в КОНЦЕ messages (после user)."""
    seen = {}

    def handler(request):
        seen["messages"] = json.loads(request.content)["messages"]
        return ok_handler(request)

    agent = make_agent(data_dir, handler)
    d = agent.store.new_dialogue()
    ready(agent, d)
    agent.store.invariants_set("Стек", "Kotlin", forbidden=["python"])
    list(agent.ask_stream(d["id"], "забудь, напиши код на Python"))
    assert seen["messages"][-1]["role"] == "system"
    assert "Стек: Kotlin" in seen["messages"][-1]["content"]
    assert "отказ" in seen["messages"][-1]["content"]
    assert seen["messages"][-2]["role"] == "user"


def test_no_invariant_reminder_when_empty(data_dir):
    """Пустые инварианты — напоминания нет."""
    seen = {}

    def handler(request):
        seen["messages"] = json.loads(request.content)["messages"]
        return ok_handler(request)

    agent = make_agent(data_dir, handler)
    d = agent.store.new_dialogue()
    ready(agent, d)
    list(agent.ask_stream(d["id"], "Напиши код, используем Python"))
    assert seen["messages"][-1]["role"] == "user"


def test_invariant_reminder_last_among_guards(data_dir):
    """Инвариант + конфликт памяти: оба напоминания, инвариант ПОСЛЕДНИЙ
    (высший приоритет — самое «свежее» место)."""
    seen = {}

    def handler(request):
        seen["messages"] = json.loads(request.content)["messages"]
        return ok_handler(request)

    agent = make_agent(data_dir, handler)
    d = agent.store.new_dialogue()
    ready(agent, d)
    agent.store.wm_set(d["id"], "Источник", "Яндекс")
    agent.store.invariants_set("Стек", "Kotlin", forbidden=["python"])
    list(agent.ask_stream(
        d["id"], "Напиши ТЗ, источник — гугл, используем Python"))
    msgs = seen["messages"]
    assert msgs[-1]["role"] == "system"
    assert "Стек: Kotlin" in msgs[-1]["content"]
    assert msgs[-2]["role"] == "system"
    assert "Источник: Яндекс" in msgs[-2]["content"]


def test_postcheck_invariants_returns_matching_patterns(data_dir):
    """Post-guard L1: forbidden-паттерны в ответе модели → список."""
    agent = make_agent(data_dir, ok_handler)
    agent.store.invariants_set("Стек", "Kotlin", forbidden=["python"])
    assert agent._postcheck_invariants("Код: import python") == ["python"]
    assert agent._postcheck_invariants("Чистый ответ") == []


def test_ask_stream_invariant_violation_guard(data_dir):
    """Ответ содержит forbidden-паттерн → событие invariant_violation
    (patterns) ПЕРЕД done; answer заменён отказом; done — последнее;
    сохранённое сообщение — отказ."""
    body = sse_body([delta_chunk("Используй "), delta_chunk("python для сервера"),
                     usage_chunk(), "[DONE]"])

    def handler(request):
        return httpx.Response(200, content=body.encode("utf-8"))

    agent = make_agent(data_dir, handler)
    d = agent.store.new_dialogue()
    ready(agent, d)
    agent.store.invariants_set("Стек", "Kotlin", forbidden=["python"])
    events = list(agent.ask_stream(d["id"], "Напиши код сервера"))
    types = [e["type"] for e in events]
    assert "invariant_violation" in types
    assert events[-1]["type"] == "done"
    v = next(e for e in events if e["type"] == "invariant_violation")
    assert v["patterns"] == ["python"]
    refusal = events[-1]["answer"]
    assert "Не могу выполнить" in refusal
    assert "python" in refusal
    assert "Используй python" not in refusal
    stored = agent.store.get_messages(d["id"])[-1]
    assert stored["role"] == "assistant"
    assert stored["content"] == refusal


def test_ask_stream_no_invariant_violation_when_clean(data_dir):
    """Ответ без forbidden-паттернов — события invariant_violation нет."""
    agent = make_agent(data_dir, ok_handler)
    d = agent.store.new_dialogue()
    ready(agent, d)
    agent.store.invariants_set("Стек", "Kotlin", forbidden=["python"])
    events = list(agent.ask_stream(d["id"], "Напиши код"))
    assert all(e["type"] != "invariant_violation" for e in events)
    assert events[-1]["type"] == "done"
    assert events[-1]["answer"] == "Привет"


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


def test_pending_first_message_goes_to_llm_normally(data_dir):
    # День 20: обязательное приглашение удалено — первое сообщение нового
    # диалога (pending, без interview-флага) уходит в LLM обычным потоком.
    calls, streams = {"n": 0}, []
    agent = make_agent(data_dir, _profile_handler("{}", calls, streams))
    d = agent.store.new_dialogue()
    events = list(agent.ask_stream(d["id"], "объясни лямбды"))
    assert len(streams) == 1  # обычный LLM-стрим вызвался
    done = events[-1]
    assert done["type"] == "done"
    assert done["answer"] == "ок"
    assert "инициализировать профиль" not in done["answer"]
    # профиль остался pending (ничего не инициализировалось)
    assert agent.store.profile_get(d["id"])["status"] == "pending"
    # user + assistant сохранены; запрос ушёл в журнал
    msgs = agent.store.get_messages(d["id"])
    assert msgs[0] == {"role": "user", "content": "объясни лямбды"}
    assert msgs[1]["role"] == "assistant"
    assert len(agent.requests_list()) == 1


def test_pending_second_message_also_goes_to_llm(data_dir):
    # pending без интервью — ни первый, ни второй ход не перехватываются
    calls, streams = {"n": 0}, []
    agent = make_agent(data_dir, _profile_handler("{}", calls, streams))
    d = agent.store.new_dialogue()
    list(agent.ask_stream(d["id"], "привет"))
    events = list(agent.ask_stream(d["id"], "расскажи про акул"))
    assert len(streams) == 2
    assert events[-1]["answer"] == "ок"
    assert agent.store.profile_get(d["id"])["status"] == "pending"


def test_chat_markers_no_longer_intercepted(data_dir):
    # День 20: маркеры «интервью»/«вручную» в чате больше не активируют
    # служебный ход — сообщение уходит в LLM, профиль не меняется
    calls, streams = {"n": 0}, []
    agent = make_agent(data_dir, _profile_handler("{}", calls, streams))
    d = agent.store.new_dialogue()
    e1 = list(agent.ask_stream(d["id"], "давай интервью"))
    p = agent.store.profile_get(d["id"])
    assert p["status"] == "pending" and p["interview"] is False
    e2 = list(agent.ask_stream(d["id"], "вручную"))
    assert len(streams) == 2
    assert e1[-1]["answer"] == "ок" and e2[-1]["answer"] == "ок"
    assert agent.store.profile_get(d["id"])["status"] == "pending"


def test_decline_marker_unit_level_sets_declined_then_normal_flow(data_dir):
    # Маркерная ветка _profile_init_turn (unit-level): чистый отказ —
    # declined + без остатка; следующий чат-ход — обычный LLM-поток
    calls, streams = {"n": 0}, []
    agent = make_agent(data_dir, _profile_handler("{}", calls, streams))
    d = agent.store.new_dialogue()
    text, remainder = agent._profile_init_turn(d["id"], "отказ",
                                               "qwen3.8-27b")
    assert agent.store.profile_get(d["id"])["status"] == "declined"
    assert remainder is None
    assert "отказ" in text.lower() or "обычно" in text.lower()
    list(agent.ask_stream(d["id"], "привет"))
    assert len(streams) == 1
    assert "Профиль пользователя" not in streams[0]["messages"][0]["content"]


def test_decline_with_remainder_unit_level_executes(data_dir):
    # «отказ, теперь объясни лямбды» (баг №2): unit-level отказ +
    # возврат содержательного остатка для обычного чат-потока
    calls, streams = {"n": 0}, []
    agent = make_agent(data_dir, _profile_handler("{}", calls, streams))
    d = agent.store.new_dialogue()
    text, remainder = agent._profile_init_turn(d["id"],
                                               "отказ, теперь объясни лямбды",
                                               "qwen3.8-27b")
    assert agent.store.profile_get(d["id"])["status"] == "declined"
    assert remainder is not None and "объясни лямбды" in remainder
    # остаток выполняется обычным LLM-потоком (declined → без блока профиля)
    events = list(agent.ask_stream(d["id"], remainder))
    assert len(streams) == 1
    assert events[-1]["type"] == "done"


def test_decline_pure_single_word_unit_level_closes(data_dir):
    # «не хочу» без дополнения — чистый отказ: остаток None (регресс бага №2)
    calls, streams = {"n": 0}, []
    agent = make_agent(data_dir, _profile_handler("{}", calls, streams))
    d = agent.store.new_dialogue()
    text, remainder = agent._profile_init_turn(d["id"], "не хочу",
                                               "qwen3.8-27b")
    assert agent.store.profile_get(d["id"])["status"] == "declined"
    assert remainder is None
    assert len(streams) == 0  # LLM не вызывался


def test_interview_flow_via_api_creates_active_profile(data_dir):
    # День 20: интервью запускается явно (API), не чат-маркером.
    # answer → экстракт → active.
    extract = ('{"name": "Иван", "role": "backend", "tone": "кратко", '
               '"taboos": "мат"}')
    calls, streams = {"n": 0}, []
    agent = make_agent(data_dir, _profile_handler(extract, calls, streams))
    d = agent.store.new_dialogue()
    # 1) явный запуск интервью (путь API: POST /api/profile/action)
    agent.store.profile_action(d["id"], "interview")
    p = agent.store.profile_get(d["id"])
    assert p["status"] == "pending" and p["interview"] is True
    # 2) следующий чат-ход = ответ на анкету → экстракт → active
    e2 = list(agent.ask_stream(d["id"], "Иван, backend-разработчик, кратко, не мат"))
    p = agent.store.profile_get(d["id"])
    assert p["status"] == "active" and p["name"] == "Иван" and p["taboos"] == "мат"
    assert "сохранён" in e2[-1]["answer"].lower() or "профиль" in e2[-1]["answer"].lower()
    assert len(streams) == 0  # на ответ анкеты LLM-стрим не шёл (только экстракт)
    # 3) следующий ход — обычный LLM-поток С блоком профиля
    list(agent.ask_stream(d["id"], "привет"))
    assert len(streams) == 1
    assert "Профиль пользователя" in streams[0]["messages"][0]["content"]
    assert "Иван" in streams[0]["messages"][0]["content"]


def test_interview_failed_extraction_repeats_questions(data_dir):
    # interview-флаг + неразборчивый ответ → просьба повторить, без LLM-стрима
    calls, streams = {"n": 0}, []
    agent = make_agent(data_dir, _profile_handler("не разобрать", calls, streams))
    d = agent.store.new_dialogue()
    agent.store.profile_action(d["id"], "interview")
    events = list(agent.ask_stream(d["id"], "мусор без полей"))
    p = agent.store.profile_get(d["id"])
    assert p["status"] == "pending" and p["interview"] is True
    assert "повтор" in events[-1]["answer"].lower()
    assert len(streams) == 0


def test_reset_back_to_pending_no_intercept(data_dir):
    # После reset профиль снова pending — но без интервью чат-ход уже
    # не перехватывается (день 20): обычный LLM-поток
    calls, streams = {"n": 0}, []
    agent = make_agent(data_dir, _profile_handler("{}", calls, streams))
    d = agent.store.new_dialogue()
    agent.store.profile_set(d["id"], "Иван", "", "", "")
    assert agent.store.profile_get(d["id"])["status"] == "active"
    agent.store.profile_action(d["id"], "reset")
    events = list(agent.ask_stream(d["id"], "привет"))
    assert len(streams) == 1
    assert "инициализировать профиль" not in events[-1]["answer"]
    assert agent.store.profile_get(d["id"])["status"] == "pending"


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


# ---------- задача: оркестратор stage-агентов (день 13b) ----------

def make_task_handler(steps, step_outputs, verdict="pass",
                      planning_output=None, done_output="ФИНАЛЬНЫЙ ОТВЕТ",
                      fail_stage=None, on_first_exec=None,
                      validator_verdicts=None):
    """MockTransport: stream=True — SSE-кадры, stream=False — JSON.
    calls — список {"system","user","stream"}. on_first_exec(request) —
    колбэк на ПЕРВОМ вызове Исполнителя (для тестов паузы на границе
    шага: пауза ставится «внутри» LLM-вызова). validator_verdicts —
    список вердиктов по вызовам (для ретра-сценария)."""
    calls = []
    state = {"exec": 0, "val": 0}

    def handler(request):
        body = json.loads(request.content)
        system = body["messages"][0]["content"]
        user = body["messages"][1]["content"]
        calls.append({"system": system, "user": user,
                      "stream": body.get("stream", False), "body": body})
        if fail_stage and fail_stage in system:
            return httpx.Response(500, text="boom") if body.get("stream") \
                else httpx.Response(500, json={"error": "boom"})
        if "Планировщик" in system:
            content = planning_output if planning_output is not None \
                else json.dumps(steps, ensure_ascii=False)
        elif "Исполнитель" in system:
            state["exec"] += 1
            if on_first_exec and state["exec"] == 1:
                on_first_exec()
            step = next((s for s in steps if f"«{s}»" in system), "")
            content = step_outputs.get(step, f"Результат шага {step}")
        elif "Валидатор" in system:
            state["val"] += 1
            v = validator_verdicts[state["val"] - 1] if validator_verdicts else verdict
            content = f"Заключение {state['val']}.\n<verdict>{v}</verdict>"
        else:  # Оркестратор
            content = done_output
        usage = {"prompt_tokens": 11, "completion_tokens": 22,
                 "total_tokens": 33}
        if body.get("stream"):
            frame = ("data: " + json.dumps({"choices": [{"delta": {"content": content}}]})
                     + "\n\n")
            usage_frame = ("data: " + json.dumps({"choices": [], "usage": usage})
                           + "\n\n")
            return httpx.Response(200, text=frame + usage_frame + "data: [DONE]\n\n")
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}],
                                         "usage": usage})
    return handler, calls


class TestTaskRun13b:
    """Оркестратор задачи (день 13b): пошаговое исполнение, JSON-план,
    step-события, paused/failed как стадии."""

    def _setup(self, data_dir, handler):
        """Агент на MockTransport + новый диалог с task_new."""
        agent = make_agent(data_dir, handler)
        d = agent.store.new_dialogue()
        agent.store.task_new(d["id"], "Задача X")
        return agent, d["id"]

    def run_all(self, agent, did):
        """Прогнать пайплайн до конца. День 15: plan_review — человеческий
        гейт, task_run на нём yield-ит plan_review и возвращается. Для
        тестов полного цикла авто-одобряем и продолжаем."""
        events = []
        while True:
            batch = list(agent.task_run(did))
            events.extend(batch)
            if any(e["type"] == "plan_review" for e in batch):
                agent.store.task_approve(did)
                continue
            break
        return events

    def event_types(self, events):
        return [e["type"] for e in events]

    def test_full_cycle_events(self, data_dir):
        handler, calls = make_task_handler(["A", "B"],
                                           {"A": "Результат шага A",
                                            "B": "Результат шага B"})
        agent, did = self._setup(data_dir, handler)
        events = self.run_all(agent, did)
        # duration_s — wall-clock, из сравнения исключаем (проверяем отдельно)
        norm = [{k: v for k, v in e.items() if k != "duration_s"}
                for e in events]
        u = {"prompt": 11, "completion": 22, "total": 33}
        assert norm == [
            {"type": "agent_spawned", "stage": "planning", "agent": "Планировщик"},
            {"type": "stage_done", "stage": "planning",
             "output": json.dumps(["A", "B"], ensure_ascii=False),
             "plan": ["A", "B"], "usage": u},
            {"type": "plan_review", "stage": "plan_review",
             "constraints": [], "alternative": ""},
            {"type": "agent_spawned", "stage": "execution", "agent": "Исполнитель"},
            {"type": "step_updated", "index": 0, "name": "A", "status": "in_progress"},
            {"type": "step_delta", "index": 0, "text": "Результат шага A"},
            {"type": "step_updated", "index": 0, "name": "A",
             "status": "completed", "output": "Результат шага A", "usage": u},
            {"type": "step_updated", "index": 1, "name": "B", "status": "in_progress"},
            {"type": "step_delta", "index": 1, "text": "Результат шага B"},
            {"type": "step_updated", "index": 1, "name": "B",
             "status": "completed", "output": "Результат шага B", "usage": u},
            {"type": "stage_done", "stage": "execution",
             "output": "## A\nРезультат шага A\n\n## B\nРезультат шага B",
             "usage": {"prompt": 22, "completion": 44, "total": 66}},
            {"type": "agent_spawned", "stage": "validation", "agent": "Валидатор"},
            {"type": "stage_done", "stage": "validation",
             "output": "Заключение 1.\n<verdict>pass</verdict>", "verdict": "pass",
             "usage": u},
            {"type": "agent_spawned", "stage": "done", "agent": "Оркестратор"},
            {"type": "stage_done", "stage": "done", "output": "ФИНАЛЬНЫЙ ОТВЕТ",
             "usage": u},
            {"type": "task_done", "answer": "ФИНАЛЬНЫЙ ОТВЕТ"},
        ]
        # duration_s — у step_updated(completed) (wall-clock, ≥ 0)
        dur = [e["duration_s"] for e in events
               if e["type"] == "step_updated"
               and e.get("status") == "completed"]
        assert dur and all(isinstance(x, int) and x >= 0 for x in dur)
        t = agent.store.task_get(did)
        assert t["stage"] == "done"
        assert all(e["status"] == "completed" for e in t["plan"])
        assert all(w["status"] == "completed" for w in t["work_steps"])
        assert t["work_steps"][0]["output"] == "Результат шага A"
        assert t["work_steps"][1]["output"] == "Результат шага B"

    def test_usage_in_state_and_markers(self, data_dir):
        """Токены и длительность: в состоянии (plan[]/work_steps[]) и в
        маркерах сообщений (task_usage/task_duration — восстановление
        карточки после перезагрузки)."""
        handler, calls = make_task_handler(["A", "B"],
                                           {"A": "Результат шага A",
                                            "B": "Результат шага B"})
        agent, did = self._setup(data_dir, handler)
        self.run_all(agent, did)
        t = agent.store.task_get(did)
        u = {"prompt": 11, "completion": 22, "total": 33}
        # plan: usage у каждой стадии, duration_s у завершённых
        for e in t["plan"]:
            if e["agent"] == "execution":
                assert e["usage"] == {"prompt": 22, "completion": 44, "total": 66}
            else:
                assert e["usage"] == u
            assert isinstance(e["duration_s"], int) and e["duration_s"] >= 0
        # work-шаги: start_ts/usage/duration_s
        for w in t["work_steps"]:
            assert w["start_ts"]
            assert w["usage"] == u
            assert isinstance(w["duration_s"], int) and w["duration_s"] >= 0
        # маркеры: planning / execution-шаги / validation — с токенами
        msgs = [m for m in agent.store.get_messages(did)
                if m.get("task_stage")]
        assert len(msgs) == 4  # planning + 2 шага + validation
        for m in msgs:
            assert m["task_usage"] == u
            assert isinstance(m["task_duration"], int) and m["task_duration"] >= 0
        # финальный синтез — тоже маркер с токенами (без task_stage)
        final = agent.store.get_messages(did)[-1]
        assert final["task_id"] and not final.get("task_stage")
        assert final["task_usage"] == u

    def test_planning_prompt_no_chat_history(self, data_dir):
        handler, calls = make_task_handler(["A"], {})
        agent, did = self._setup(data_dir, handler)
        # история чата до старта — в task-промпты уходить не должна (D5)
        agent.store.append_message(did, "user", "секретное слово")
        agent.store.append_message(did, "assistant", "запомнил секретное слово")
        self.run_all(agent, did)
        assert calls
        assert all("секретное слово" not in c["system"] for c in calls)
        assert all("Задача X" in c["system"] for c in calls)

    def test_exec_step_prompt_has_previous_outputs(self, data_dir):
        handler, calls = make_task_handler(["A", "B"],
                                           {"A": "Результат шага A",
                                            "B": "Результат шага B"})
        agent, did = self._setup(data_dir, handler)
        self.run_all(agent, did)
        exec_calls = [c for c in calls if "Исполнитель" in c["system"]]
        assert len(exec_calls) == 2
        s = exec_calls[1]["system"]
        assert "Задача X" in s                      # описание задачи
        assert "План:" in s                          # план из planning-вывода
        assert "Результат шага A" in s               # output 1-го шага
        assert "Текущий шаг: B" in s                 # текущий work-шаг
        assert "Текущий шаг: A" not in s
        assert exec_calls[1]["user"] == "Выполни шаг плана: B"

    def test_planning_json_fallback_one_step(self, data_dir):
        handler, calls = make_task_handler(
            ["X"], {},
            planning_output="Сначала сделать анализ, потом тексты")
        agent, did = self._setup(data_dir, handler)
        events = self.run_all(agent, did)
        t = agent.store.task_get(did)
        assert [w["name"] for w in t["work_steps"]] == ["Выполнить запрос"]
        assert len([c for c in calls if "Исполнитель" in c["system"]]) == 1
        assert events[-1]["type"] == "task_done"

    def test_planning_limit_5_steps(self, data_dir):
        seven = [f"Шаг {i}" for i in range(1, 8)]
        handler, calls = make_task_handler(
            seven, {}, planning_output=json.dumps(seven, ensure_ascii=False))
        agent, did = self._setup(data_dir, handler)
        self.run_all(agent, did)
        t = agent.store.task_get(did)
        assert [w["name"] for w in t["work_steps"]] == seven[:5]

    def test_instruction_on_pause_injected_once(self, data_dir):
        box = {}
        handler, calls = make_task_handler(
            ["A", "B"], {"A": "Результат шага A", "B": "Результат шага B"},
            on_first_exec=lambda: box["store"].task_pause(box["did"]))
        agent, did = self._setup(data_dir, handler)
        box["store"] = agent.store
        box["did"] = did
        events1 = self.run_all(agent, did)
        # пауза на границе шага: после step_updated(0, completed)
        assert events1[-1] == {"type": "task_paused", "stage": "paused"}
        su = [e for e in events1 if e["type"] == "step_updated"]
        assert {k: v for k, v in su[-1].items() if k != "duration_s"} == {
            "type": "step_updated", "index": 0, "name": "A",
            "status": "completed", "output": "Результат шага A",
            "usage": {"prompt": 11, "completion": 22, "total": 33}}
        agent.store.task_set_instruction(did, "используй Kotlin")
        agent.store.task_resume(did)
        events2 = self.run_all(agent, did)
        assert events2[-1]["type"] == "task_done"
        exec_calls = [c for c in calls if "Исполнитель" in c["system"]]
        assert len(exec_calls) == 2  # A (первый run) + B (второй), A не повторяется
        assert "Kotlin" not in exec_calls[0]["system"]
        assert ("Инструкция пользователя (обязательно учти): используй Kotlin"
                in exec_calls[1]["system"])
        assert agent.store.task_get(did)["instruction"] == ""

    def test_pause_between_steps_no_next_call(self, data_dir):
        box = {}
        handler, calls = make_task_handler(
            ["A", "B"], {},
            on_first_exec=lambda: box["store"].task_pause(box["did"]))
        agent, did = self._setup(data_dir, handler)
        box["store"] = agent.store
        box["did"] = did
        events = self.run_all(agent, did)
        assert events[-1] == {"type": "task_paused", "stage": "paused"}
        # ровно 1 вызов Исполнителя — следующий work-шаг не стартует
        assert len([c for c in calls if "Исполнитель" in c["system"]]) == 1
        t = agent.store.task_get(did)
        assert t["stage"] == "paused"
        assert t["context_snapshot"]["description"] == "Задача X"
        assert len(t["context_snapshot"]["work_steps"]) == 2

    def test_resume_no_repeats(self, data_dir):
        box = {}
        handler, calls = make_task_handler(
            ["A", "B"], {},
            on_first_exec=lambda: box["store"].task_pause(box["did"]))
        agent, did = self._setup(data_dir, handler)
        box["store"] = agent.store
        box["did"] = did
        assert self.run_all(agent, did)[-1]["type"] == "task_paused"
        agent.store.task_resume(did)
        events2 = self.run_all(agent, did)
        assert events2[-1]["type"] == "task_done"
        exec_calls = [c for c in calls if "Исполнитель" in c["system"]]
        assert len(exec_calls) == 2  # A + B; шаг 0 (completed) не повторяется
        assert "Текущий шаг: A" not in exec_calls[1]["system"]
        assert "Текущий шаг: B" in exec_calls[1]["system"]
        # повторный spawn execution не происходит
        assert all(e.get("stage") != "execution"
                   for e in events2 if e["type"] == "agent_spawned")

    def test_validation_fail_retry(self, data_dir):
        handler, calls = make_task_handler(["A", "B"], {},
                                           validator_verdicts=["fail", "pass"])
        agent, did = self._setup(data_dir, handler)
        events = self.run_all(agent, did)
        assert events[-1]["type"] == "task_done"
        t = agent.store.task_get(did)
        assert t["stage"] == "done" and t["retries"] == 1
        val_done = [e for e in events if e["type"] == "stage_done"
                    and e.get("stage") == "validation"]
        assert val_done[0]["verdict"] == "fail" and val_done[0]["retry"] is True
        assert val_done[1]["verdict"] == "pass"
        exec_calls = [c for c in calls if "Исполнитель" in c["system"]]
        assert len(exec_calls) == 4  # 2 шага × 2 попытки
        assert ("Замечания валидатора (обязательно исправь): Заключение 1."
                in exec_calls[2]["system"])

    def test_validation_fail_twice(self, data_dir):
        handler, calls = make_task_handler(["A"], {},
                                           validator_verdicts=["fail", "fail"])
        agent, did = self._setup(data_dir, handler)
        events = self.run_all(agent, did)
        assert events[-1]["type"] == "task_done"  # без 3-го execution
        t = agent.store.task_get(did)
        assert t["stage"] == "done" and t["retries"] == 1
        assert t["plan"][2]["verdict"] == "fail"
        exec_calls = [c for c in calls if "Исполнитель" in c["system"]]
        assert len(exec_calls) == 2  # 1 шаг × 2 попытки, третий execution нет

    def test_llm_error_marks_failed(self, data_dir):
        handler, calls = make_task_handler(["A"], {}, fail_stage="Исполнитель")
        agent, did = self._setup(data_dir, handler)
        events = self.run_all(agent, did)
        assert events[-1]["type"] == "task_failed"
        assert "A" in events[-1]["message"]
        su = [e for e in events if e["type"] == "step_updated"]
        assert su[-1]["status"] == "in_progress"  # сбой внутри первого шага
        t = agent.store.task_get(did)
        assert t["stage"] == "failed"
        assert "Исполнитель" in t["error"]
        # повтор: resume + новый healthy-хендлер (подмена transport)
        agent.store.task_resume(did)
        healthy, _ = make_task_handler(["A"], {})
        agent._client = httpx.Client(transport=httpx.MockTransport(healthy))
        events2 = self.run_all(agent, did)
        assert events2[-1]["type"] == "task_done"
        assert agent.store.task_get(did)["stage"] == "done"

    def test_run_on_done_errors(self, data_dir):
        handler, calls = make_task_handler(["A"], {})
        agent, did = self._setup(data_dir, handler)
        self.run_all(agent, did)
        before = len(calls)
        events = list(agent.task_run(did))
        assert events == [{"type": "error", "message": "Задача завершена"}]
        assert len(calls) == before  # LLM-вызовов нет

    def test_chat_guard(self, data_dir):
        handler, calls = make_task_handler(["A"], {})
        agent = make_agent(data_dir, handler)
        d = agent.store.new_dialogue()
        agent.store.profile_action(d["id"], "decline")
        agent.store.task_new(d["id"], "Задача X")
        agent.store.task_spawn_stage(d["id"], "planning")
        agent.store.task_stage_done(d["id"], "planning", "план")
        agent.store.task_approve(d["id"])
        assert agent.store.task_get(d["id"])["stage"] == "execution"
        # активная непаузанная незавершённая — гард, сообщение не сохраняется
        events = list(agent.ask_stream(d["id"], "привет"))
        assert len(events) == 1 and events[0]["type"] == "error"
        assert "Задача выполняется" in events[0]["message"]
        assert agent.store.get_messages(d["id"]) == []
        # paused — гард не срабатывает
        agent.store.task_pause(d["id"])
        assert list(agent.ask_stream(d["id"], "привет"))[-1]["type"] == "done"
        # failed — гард не срабатывает
        agent.store.task_resume(d["id"])
        agent.store.task_set_failed(d["id"], "сбой")
        assert list(agent.ask_stream(d["id"], "привет"))[-1]["type"] == "done"
        # done — гард не срабатывает
        healthy, _ = make_task_handler(["A"], {})
        agent._client = httpx.Client(transport=httpx.MockTransport(healthy))
        agent.store.task_new(d["id"], "Задача 2")
        self.run_all(agent, d["id"])
        assert agent.store.task_get(d["id"])["stage"] == "done"
        assert list(agent.ask_stream(d["id"], "привет"))[-1]["type"] == "done"

    def test_task_calls_disable_thinking(self, data_dir):
        """Task-вызовы LLM: enable_thinking=False. Reasoning-модели
        (deepseek) по умолчанию «думают» и сжигают весь max_tokens-бюджет
        на размышления → контент-ответ пуст («Пустой ответ модели»).
        Паттерн — авто-название дня 11 (_generate_title)."""
        handler, calls = make_task_handler(["A"], {"A": "Результат шага A"})
        agent, did = self._setup(data_dir, handler)
        self.run_all(agent, did)
        assert calls  # и stage-вызовы, и stream-вызовы work-шага
        assert all(c["body"].get("chat_template_kwargs")
                   == {"enable_thinking": False} for c in calls)

    def test_task_stages_inject_invariants_rule(self, data_dir):
        """День 14: task-режим. Stage-промпты (planning/execution/
        validation/done) получают блок инвариантов и INVARIANTS_RULE — как
        чат build_payload. Инварианты глобальны (is_active), тумблеры слоёв
        их не отключают."""
        handler, calls = make_task_handler(["A"], {"A": "Результат шага A"})
        agent, did = self._setup(data_dir, handler)
        agent.store.invariants_set("Стек", "Kotlin", forbidden=["python"])
        self.run_all(agent, did)
        assert calls
        # planning + execution(stream) + validation + done
        assert len(calls) >= 4
        for c in calls:
            assert "Инварианты (неукоснительно):" in c["system"]
            assert "- Стек: Kotlin" in c["system"]
            assert INVARIANTS_RULE in c["system"]

    def test_task_stages_skip_invariants_when_empty(
            self, data_dir):
        """День 14: без активных инвариантов task-промпты не содержат
        ни блока, ни INVARIANTS_RULE (нет лишних токенов)."""
        handler, calls = make_task_handler(["A"], {"A": "Результат шага A"})
        agent, did = self._setup(data_dir, handler)
        self.run_all(agent, did)
        assert calls
        assert all("Инварианты (неукоснительно):" not in c["system"]
                   for c in calls)
        assert all(INVARIANTS_RULE not in c["system"] for c in calls)

    def test_task_done_postguard_replaces_violation(self, data_dir):
        """День 14: финальный синтез (done) с forbidden-паттерном
        активного инварианта заменяется отказом + событие
        invariant_violation перед task_done (как чат ask_stream)."""
        handler, calls = make_task_handler(
            ["A"], {"A": "Результат шага A"},
            done_output="Итог: используем python для всего.")
        agent, did = self._setup(data_dir, handler)
        agent.store.invariants_set("Стек", "Kotlin", forbidden=["python"])
        events = self.run_all(agent, did)
        types = self.event_types(events)
        assert "invariant_violation" in types
        viol = next(e for e in events if e["type"] == "invariant_violation")
        assert viol["patterns"] == ["python"]
        done = next(e for e in events if e["type"] == "task_done")
        assert "нарушит инвариант" in done["answer"]
        assert "python" in done["answer"]
        # сохранённый маркер финального синтеза — ответ-отказ
        msgs = agent.store.get_messages(did)
        last = [m for m in msgs if not m.get("task_stage")][-1]
        assert "нарушит инвариант" in last["content"]

    def test_task_done_no_violation_when_inactive(self, data_dir):
        """День 14: готовый ответ с forbidden-паттерном НЕ активного
        инварианта (is_active=false) проходит без гарда."""
        handler, calls = make_task_handler(
            ["A"], {"A": "Результат шага A"},
            done_output="Отлично, python — наше всё.")
        agent, did = self._setup(data_dir, handler)
        r = agent.store.invariants_set("Стек", "Kotlin", forbidden=["python"])
        agent.store.invariants_set_active(r["id"], False)
        events = self.run_all(agent, did)
        assert "invariant_violation" not in self.event_types(events)
        done = next(e for e in events if e["type"] == "task_done")
        assert done["answer"] == "Отлично, python — наше всё."

    def test_task_done_approved_plan_mentions_forbidden(self, data_dir):
        """День 15 (фикс): план содержит forbidden-паттерн, пользователь
        одобрил (plan_review → execution), и финальный синтез легитимно
        называет этот паттерн, объясняя согласованную альтернативу. Пост-гард
        НЕ должен рубить ответ: паттерны из одобренного плана не считаются
        нарушением. (Без фикса — ложное invariant_violation + отказ.)"""
        handler, calls = make_task_handler(
            ["A", "B"],
            {"A": "Результат шага A", "B": "Результат шага B"},
            planning_output='["A", "B"]\n(не используем python — иначе инвариант)',
            done_output="Итог: реализовано на Kotlin вместо Python.")
        agent, did = self._setup(data_dir, handler)
        agent.store.invariants_set("Стек", "Kotlin", forbidden=["python"])
        events = self.run_all(agent, did)
        types = self.event_types(events)
        # план нарушает инвариант → человеческий гейт plan_review
        assert "plan_review" in types
        # одобрили → пайплайн дошёл до done (без ложного отказа)
        assert "task_done" in types
        assert "invariant_violation" not in types
        done = next(e for e in events if e["type"] == "task_done")
        assert done["answer"] == "Итог: реализовано на Kotlin вместо Python."

    def test_task_done_approved_workstep_mentions_forbidden(self, data_dir):
        """День 15 (фикс, живой сценарий): forbidden-паттерн НЕ в плане,
        а в выводе work-шага Исполнителя (исполнитель объясняет отказ от
        запрета — «Python запрещён, поэтому TypeScript»). Пользователь
        одобрил план (plan_approved), финальный синтез легитимно называет
        паттерн. Пост-гард НЕ должен рубить ответ: паттерны из
        согласованного контекста задачи (описание + план + выводы
        work-шагов) не считаются нарушением. (Без фикса — ложный отказ.)"""
        handler, calls = make_task_handler(
            ["A", "B"],
            {"A": "Реализовано на Kotlin. Python запрещён инвариантом, "
                  "поэтому TypeScript.",
             "B": "Результат шага B"},
            planning_output='["A", "B"]',
            done_output="Итог: реализовано на Kotlin вместо Python.")
        agent, did = self._setup(data_dir, handler)
        agent.store.invariants_set("Стек", "Kotlin", forbidden=["python"])
        events = self.run_all(agent, did)
        types = self.event_types(events)
        assert "task_done" in types
        assert "invariant_violation" not in types
        done = next(e for e in events if e["type"] == "task_done")
        assert done["answer"] == "Итог: реализовано на Kotlin вместо Python."

    def test_task_run_refuses_planning_on_forbidden_description(self, data_dir):
        """День 14: постановка задачи с forbidden-паттерном в description
        → отказ на стадии planning ДО спавна агентов: нет agent_spawned,
        execution/validation/done не выполняются, stage=done, запись
        planning completed с ответом-отказом, LLM не вызывается."""
        handler, calls = make_task_handler(["A"], {"A": "Результат шага A"})
        agent, did = self._setup(data_dir, handler)
        # пересоздать задачу с нарушающим инвариант описанием
        agent.store.task_reset(did)
        agent.store.task_new(did, "Сделай проект на python")
        agent.store.invariants_set("Стек", "Kotlin", forbidden=["python"])
        events = self.run_all(agent, did)
        types = self.event_types(events)
        # ни один агент не спавнится, LLM не вызывается
        assert "agent_spawned" not in types
        assert calls == []
        # отказ-инварианты
        assert "invariant_violation" in types
        viol = next(e for e in events if e["type"] == "invariant_violation")
        assert viol["patterns"] == ["python"]
        done = next(e for e in events if e["type"] == "task_done")
        assert "нарушит инвариант" in done["answer"]
        assert "python" in done["answer"]
        # stage_done планирования — отказ, plan пуст
        sd = next(e for e in events if e["type"] == "stage_done")
        assert sd["stage"] == "planning"
        assert sd["output"] == done["answer"]
        assert sd["plan"] == []
        # состояние задачи: stage=done, planning completed, остальные pending
        t = agent.store.task_get(did)
        assert t["stage"] == "done"
        assert t["current_step"] == 5  # len(TASK_FLOW) — терминальная done
        by_agent = {e["agent"]: e["status"] for e in t["plan"]}
        assert by_agent["planning"] == "completed"
        assert by_agent["execution"] == "pending"
        assert by_agent["validation"] == "pending"
        assert by_agent["done"] == "pending"
        # маркер стадии planning + финальный bubble (task_id без task_stage)
        msgs = agent.store.get_messages(did)
        plan_msg = [m for m in msgs if m.get("task_stage") == "planning"]
        assert plan_msg and "нарушит инвариант" in plan_msg[-1]["content"]
        bubble = [m for m in msgs
                  if "task_id" in m and not m.get("task_stage")]
        assert bubble and "нарушит инвариант" in bubble[-1]["content"]
        # повторный run после done — терминальная ошибка
        again = self.run_all(agent, did)
        assert again == [{"type": "error", "message": "Задача завершена"}]

    def test_task_run_clean_description_spawns_planning(self, data_dir):
        """День 14: description без forbidden-паттерна → обычный спавн
        Планировщика, инвариант-отказа нет."""
        handler, calls = make_task_handler(["A"], {"A": "Результат шага A"})
        agent, did = self._setup(data_dir, handler)
        agent.store.invariants_set("Стек", "Kotlin", forbidden=["python"])
        events = self.run_all(agent, did)
        types = self.event_types(events)
        assert "agent_spawned" in types
        assert "invariant_violation" not in types
        assert types[0] == "agent_spawned"


# ---------- tool-loop LLM-driven (день 17) ----------

def _tool_loop_agent(data_dir, handler):
    """Агент с офлайн MCPRegistry: fake stdio-процесс (mock_echo/mock_ping,
    call_tool — эхо str(args)), MockTransport для LLM. kb — tmp-каталог
    (изоляция от реальных data/kb/settings.json: тумблер agent_loop в
    UI не должен ломать офлайн-тесты tool-loop)."""
    client = httpx.Client(transport=httpx.MockTransport(handler))
    store = MemoryStore(str(data_dir))
    reg = MCPRegistry(store, launcher=make_fake_launcher())
    agent = StudioAgent(str(data_dir), base_url=BASE, api_key="test-key",
                        client=client, mcp=reg,
                        kb=KnowledgeBase(str(data_dir / "kb")))
    return agent, reg


def _tool_loop_handler(tool_name=None, always_tool_calls=False):
    """Fake-LLM: non-stream (авто-название) — JSON; в messages НЕТ
    role "tool" — SSE с tool-call чанком (день 20: LLM-имя берётся из
    payload["tools"] — всегда с префиксом сервера, напр.
    "firecrawl__mock_echo"; tool_name="nope" — имя не из реестра);
    role "tool" ЕСТЬ — SSE «Готово: <текст tool>» (+ usage + [DONE])."""
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if "stream" not in payload:
            return httpx.Response(200, json={"choices": [
                {"message": {"content": "E2E"}}]})
        msgs = payload.get("messages") or []
        if not always_tool_calls and any(m.get("role") == "tool"
                                         for m in msgs):
            tool_text = next(m["content"] for m in msgs
                             if m.get("role") == "tool")
            body = sse_body([delta_chunk("Готово: " + tool_text),
                             usage_chunk(), "[DONE]"])
            return httpx.Response(200, content=body.encode("utf-8"))
        if tool_name is None:
            llm_name = payload["tools"][0]["function"]["name"]
        else:
            llm_name = tool_name
        tc = {"index": 0, "id": "call_1", "type": "function",
              "function": {"name": llm_name,
                           "arguments": '{"x": "TASK-42"}'}}
        body = sse_body([
            {"choices": [{"index": 0, "delta": {"tool_calls": [tc]},
                          "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {},
                          "finish_reason": "tool_calls"}],
             "usage": USAGE},
            "[DONE]",
        ])
        return httpx.Response(200, content=body.encode("utf-8"))
    return handler


def test_tool_loop_happy_path(data_dir):
    """Подключённый сервер: tools в body (LLM-имена с префиксом), LLM
    решает вызвать firecrawl__mock_echo, результат (role tool, LLM-имя)
    возвращается модели, финальный done с ответом."""
    agent, reg = _tool_loop_agent(data_dir, _tool_loop_handler())
    try:
        d = agent.store.new_dialogue()
        ready(agent, d)
        sid = reg.servers()[0]["id"]
        assert reg.connect(sid)["status"] == "connected"
        events = list(agent.ask_stream(d["id"], "Каков статус задачи TASK-42?"))
        assert events[-1]["type"] == "done"
        assert "TASK-42" in events[-1]["answer"]
        msgs = agent.store.get_messages(d["id"])
        # ровно один assistant с tool_calls (LLM-имя с префиксом, args — JSON)
        asst_tc = [m for m in msgs
                   if m["role"] == "assistant" and m.get("tool_calls")]
        assert len(asst_tc) == 1
        tc = asst_tc[0]["tool_calls"][0]
        assert tc["id"] == "call_1"
        assert tc["function"]["name"] == "firecrawl__mock_echo"
        assert json.loads(tc["function"]["arguments"]) == {"x": "TASK-42"}
        # ровно один tool-ответ (эхо fake-процесса, name — LLM-имя)
        tool_msgs = [m for m in msgs if m["role"] == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["tool_call_id"] == "call_1"
        assert tool_msgs[0]["name"] == "firecrawl__mock_echo"
        assert tool_msgs[0]["content"] == "{'x': 'TASK-42'}"
        # журнал: 2 записи LLM; в первой tools содержит LLM-имена с префиксом
        rl = agent.requests_list()
        assert len(rl) == 2
        first = agent.requests_get(rl[0]["id"])
        assert "tools" in first["request"]
        names = [t["function"]["name"] for t in first["request"]["tools"]]
        assert "firecrawl__mock_echo" in names and "firecrawl__mock_ping" in names
    finally:
        reg.close_all()


def test_tool_loop_no_tools_without_connected_servers(data_dir):
    """Без подключённых серверов в body НЕТ tools, ответ — обычный
    delta-путь (регрессия дня 16 на уровне агента)."""
    seen = {}

    def handler(request):
        payload = json.loads(request.content)
        if "stream" not in payload:
            return httpx.Response(200, json={"choices": [
                {"message": {"content": "E2E"}}]})
        seen["payload"] = payload
        return ok_handler(request)

    agent, reg = _tool_loop_agent(data_dir, handler)
    try:
        d = agent.store.new_dialogue()
        ready(agent, d)
        events = list(agent.ask_stream(d["id"], "привет"))
        assert events[-1]["type"] == "done"
        assert "Привет" in events[-1]["answer"]
        assert "tools" not in seen["payload"]
    finally:
        reg.close_all()


def test_tool_loop_iteration_cap(data_dir, monkeypatch):
    """LLM ВСЕГДА отдаёт tool_calls → кап из env (TOOL_LOOP_CAP=5):
    events заканчиваются error «Tool-loop: превышен лимит итераций (5)»,
    LLM-вызовов (записей журнала) ровно 5."""
    monkeypatch.setenv("TOOL_LOOP_CAP", "5")
    agent, reg = _tool_loop_agent(
        data_dir, _tool_loop_handler(always_tool_calls=True))
    try:
        d = agent.store.new_dialogue()
        ready(agent, d)
        sid = reg.servers()[0]["id"]
        assert reg.connect(sid)["status"] == "connected"
        events = list(agent.ask_stream(d["id"], "статус TASK-42?"))
        assert events[-1]["type"] == "error"
        assert events[-1]["message"] \
            == "Tool-loop: превышен лимит итераций (5)"
        assert len(agent.requests_list()) == 5
    finally:
        reg.close_all()


def test_tool_error_becomes_tool_message(data_dir):
    """Scripted tool_call «nope» (нет в fake-тулах) → MCPError →
    tool-сообщение с текстом ошибки; второй LLM-вызов видит role tool
    → done."""
    agent, reg = _tool_loop_agent(
        data_dir, _tool_loop_handler(tool_name="nope"))
    try:
        d = agent.store.new_dialogue()
        ready(agent, d)
        sid = reg.servers()[0]["id"]
        assert reg.connect(sid)["status"] == "connected"
        events = list(agent.ask_stream(d["id"], "вызови инструмент nope"))
        assert events[-1]["type"] == "done"
        msgs = agent.store.get_messages(d["id"])
        tool_msgs = [m for m in msgs if m["role"] == "tool"]
        assert len(tool_msgs) == 1
        assert "не найден" in tool_msgs[0]["content"]
        assert tool_msgs[0]["tool_call_id"] == "call_1"
        asst_tc = [m for m in msgs
                   if m["role"] == "assistant" and m.get("tool_calls")]
        assert len(asst_tc) == 1
        assert asst_tc[0]["tool_calls"][0]["function"]["name"] == "nope"
    finally:
        reg.close_all()


def test_mcp_tools_rule_only_when_tools_connected(data_dir):
    """День 19: правило композиции MCP-инструментов в system-промпте
    ТОЛЬКО когда есть подключённые тулы; без подключённых серверов —
    блок не добавляется (поведение без MCP не меняется)."""
    agent, reg = _tool_loop_agent(data_dir, ok_handler)
    try:
        d = agent.store.new_dialogue()
        ready(agent, d)
        agent.store.append_message(d["id"], "user", "привет")
        # без подключения: правило композиции отсутствует
        system = agent.build_payload(d["id"])[0]["content"]
        assert "композиция" not in system
        # подключил сервер (fake-тулы mock_echo/mock_ping): правило есть
        sid = reg.servers()[0]["id"]
        assert reg.connect(sid)["status"] == "connected"
        system = agent.build_payload(d["id"])[0]["content"]
        assert "композиция" in system
    finally:
        reg.close_all()


# ---------- День 21 (hybrid RAG): фокусное окно выдержки ----------

def test_focus_snippet_centers_on_query_token():
    """Факт в ~990-м символе чанка (слепой text[:300] его утопляет);
    запрос в другой форме («телефона» vs «телефон» в тексте) — окно
    центрируется на токене с обрезанной инфлексией."""
    text = ("x" * 990 + "телефон Zubravichka-9000 " + "y" * 200)
    snip = StudioAgent._focus_snippet(text, "какая модель телефона была")
    assert "Zubravichka-9000" in snip
    assert len(snip) <= 300
    # без некратных токенов в тексте — первые 300 символов (как раньше)
    snip2 = StudioAgent._focus_snippet("абв" * 200, "привет")
    assert snip2 == ("абв" * 200)[:300]


def test_kb_block_hidden_fact_in_long_chunk(tmp_path):
    """Полный путь: индекс (hash) → гибридный поиск → RAG-блок:
    факт в середине ~1900-символьного чанка доходит до LLM-выдержки."""
    repo = tmp_path / "repo"
    (repo / "data" / "kb" / "uploads").mkdir(parents=True)
    book = ("Жил-был герой, и был он не злой. " * 30
            + "У героя был телефон Zubravichka-9000, на который "
              "он делал много фото. "
            + "Герой шёл по улице медленно. " * 30)
    (repo / "data" / "kb" / "uploads" / "book.txt").write_text(
        book, encoding="utf-8")
    kb = KnowledgeBase(str(repo / "data" / "kb"), str(repo))
    kb.build("structural", HashEmbedder())

    data = tmp_path / "data"
    data.mkdir()
    agent = make_agent(data, ok_handler)
    agent.kb = kb
    block = agent.build_kb_block("какая модель телефона была у героя",
                                  top_k=5)
    assert block
    assert "Zubravichka-9000" in block


# ---------- День 21 (реранкер): rag_context в assistant-сообщении ----------

def _fact_repo(tmp_path, name):
    """Репозиторий с одной «книгой» (факт про телефон) + собранный индекс."""
    repo = tmp_path / name
    (repo / "data" / "kb" / "uploads").mkdir(parents=True)
    book = ("Жил-был герой, и был он не злой. " * 30
            + "У героя был телефон Zubravichka-9000, на который "
              "он делал много фото. "
            + "Герой шёл по улице медленно. " * 30)
    (repo / "data" / "kb" / "uploads" / "book.txt").write_text(book,
                                                              encoding="utf-8")
    kb = KnowledgeBase(str(repo / "data" / "kb"), str(repo))
    kb.build("structural", HashEmbedder())
    return kb


def test_ask_stream_attaches_rag_context(data_dir, tmp_path):
    """RAG вкл + индекс: assistant-сообщение сохраняется с rag_context
    (final top-k чанки для инспектора): rank, file, section, score,
    stage1_rank, reranked, text; reranked=False (реранкер off)."""
    kb = _fact_repo(tmp_path, "repo_ctx")
    agent = make_agent(data_dir, ok_handler)
    agent.kb = kb
    d = agent.store.new_dialogue()
    ready(agent, d)
    events = list(agent.ask_stream(d["id"],
                                   "какая модель телефона была у героя"))
    assert events[-1]["type"] == "done"
    msgs = agent.store.get_messages(d["id"])
    asst = [m for m in msgs if m["role"] == "assistant"]
    assert asst, "нет assistant-сообщения"
    last = asst[-1]
    assert "rag_context" in last
    ctx = last["rag_context"]
    assert ctx["reranked"] is False  # реранкер по умолчанию off
    assert ctx["recall_total"] >= 1
    assert ctx["chunks"], "чанки не пусты"
    c0 = ctx["chunks"][0]
    assert set(c0) >= {"rank", "file", "section", "score",
                       "stage1_rank", "reranked", "text"}
    assert c0["rank"] == 1
    assert c0["stage1_rank"] is None  # без реранка поле не заполняется
    assert c0["reranked"] is False
    # факт — в окне выдержки одного из чанков
    assert any("Zubravichka-9000" in c["text"] for c in ctx["chunks"])


def test_rag_context_not_in_llm_payload(data_dir, tmp_path):
    """rag_context — служебное поле: в LLM-payload (build_payload) не
    уходит (следующий ход видит assistant, но поля rag_context нет)."""
    kb = _fact_repo(tmp_path, "repo_nopayload")
    agent = make_agent(data_dir, ok_handler)
    agent.kb = kb
    d = agent.store.new_dialogue()
    ready(agent, d)
    list(agent.ask_stream(d["id"], "какая модель телефона была у героя"))
    # на диске assistant — с rag_context
    msgs = agent.store.get_messages(d["id"])
    asst = [m for m in msgs if m["role"] == "assistant"]
    assert asst and asst[0].get("rag_context") is not None
    # в payload следующего хода — без rag_context
    for m in agent.build_payload(d["id"]):
        assert "rag_context" not in m


def test_ask_stream_rag_context_reranked(data_dir, tmp_path, monkeypatch):
    """reranker=api + ключ + fake APIReranker: rag_context с reranked=True,
    stage1_rank заполнен (score = rerank_score)."""
    import kb as kb_module

    class FakeReranker:
        def __init__(self, base_url, api_key, client=None):
            pass

        def rerank(self, query, texts):
            return [round((i + 1) / 100.0, 6) for i in range(len(texts))]

    monkeypatch.setenv("GPUSTACK_KEY_RERANK", "fake-key")
    monkeypatch.setattr(kb_module, "APIReranker", FakeReranker)

    kb = _fact_repo(tmp_path, "repo_reranked")
    kb.update_settings({"reranker": "api", "rag_recall": 5})
    agent = make_agent(data_dir, ok_handler)
    agent.kb = kb
    d = agent.store.new_dialogue()
    ready(agent, d)
    events = list(agent.ask_stream(d["id"],
                                   "какая модель телефона была у героя"))
    assert events[-1]["type"] == "done"
    msgs = agent.store.get_messages(d["id"])
    asst = [m for m in msgs if m["role"] == "assistant"]
    assert asst
    ctx = asst[-1]["rag_context"]
    assert ctx["reranked"] is True
    assert ctx["chunks"]
    for c in ctx["chunks"]:
        assert c["reranked"] is True
        assert c["stage1_rank"] is not None


# ---------- День 23 (f2): min_score в live-чате (ask_stream) ----------

def test_ask_stream_passes_min_score_to_search_rag(data_dir, tmp_path,
                                                   monkeypatch):
    """Живой чат с порогом: settings['min_score'] > 0 — ask_stream
    передаёт порог в kb.search_rag (консистентность фильтра на всех
    3 потребителях search_rag: live-чат, /api/kb/search, rag_compare)."""
    kb = _fact_repo(tmp_path, "repo_minscore")
    kb.update_settings({"min_score": 0.5})
    seen = []
    orig = kb.search_rag

    def spy(query, recall, top_k, reranker_mode="off", **kwargs):
        seen.append(kwargs.get("min_score"))
        return orig(query, recall, top_k, reranker_mode, **kwargs)

    monkeypatch.setattr(kb, "search_rag", spy)
    agent = make_agent(data_dir, ok_handler)
    agent.kb = kb
    d = agent.store.new_dialogue()
    ready(agent, d)
    events = list(agent.ask_stream(d["id"],
                                   "какая модель телефона была у героя"))
    assert events[-1]["type"] == "done"
    # retrieval — ровно один раз за ход, с точным порогом из настроек
    assert seen == [0.5]


def test_ask_stream_min_score_default_zero(data_dir, tmp_path,
                                           monkeypatch):
    """Дефолт min_score=0.0 (настройки без переопределения): ask_stream
    передаёт ровно 0.0 — поведение байт-в-байт как до дня 23 (0.0 в
    search_rag — no-op по контракту дня 23)."""
    kb = _fact_repo(tmp_path, "repo_minscore0")
    seen = []
    orig = kb.search_rag

    def spy(query, recall, top_k, reranker_mode="off", **kwargs):
        seen.append(kwargs.get("min_score"))
        return orig(query, recall, top_k, reranker_mode, **kwargs)

    monkeypatch.setattr(kb, "search_rag", spy)
    agent = make_agent(data_dir, ok_handler)
    agent.kb = kb
    d = agent.store.new_dialogue()
    ready(agent, d)
    events = list(agent.ask_stream(d["id"],
                                   "какая модель телефона была у героя"))
    assert events[-1]["type"] == "done"
    assert seen == [0.0]


# ---------- День 23: rewrite_query ----------

from agent import REWRITE_QUERY_PROMPT  # noqa: E402

REWRITE_Q = "какой отпуск у инженера"


def _rewrite_handler(captured, reply):
    """Fake-LLM для rewrite_query: non-stream — 200 со скриптованным
    ответом, каждый non-stream payload сохраняется в captured (spy на
    параметры). Stream (остальные эндпоинты) — стандартные дельты."""
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if "stream" in payload:
            return httpx.Response(
                200, content=sse_body([delta_chunk("Ок"), usage_chunk(),
                                       "[DONE]"]).encode("utf-8"))
        captured.append(payload)
        return httpx.Response(200, json={"choices": [
            {"message": {"content": reply}}]})
    return handler


def test_rewrite_query_success_and_params(data_dir):
    """Успех: (rewrite.strip(), True); вызов — T=0, max_tokens=200,
    system=REWRITE_QUERY_PROMPT, user=исходный вопрос (один вызов)."""
    captured = []
    agent = make_agent(data_dir, _rewrite_handler(captured,
                                                  "  отпуск инженера дни  "))
    rewritten, ok = agent.rewrite_query(REWRITE_Q)
    assert ok is True
    assert rewritten == "отпуск инженера дни"
    assert len(captured) == 1  # ровно один LLM-вызов
    p = captured[0]
    assert p["temperature"] == 0
    assert p["max_tokens"] == 200
    assert "stream" not in p  # non-stream, как в rag_compare
    assert p["messages"][0] == {"role": "system",
                                "content": REWRITE_QUERY_PROMPT}
    assert p["messages"][1] == {"role": "user", "content": REWRITE_Q}


@pytest.mark.parametrize("reply", ["", "   "])
def test_rewrite_query_empty_reply_falls_back(data_dir, reply):
    """Пустой/whitespace-ответ модели → (question, False), без исключения."""
    agent = make_agent(data_dir, _rewrite_handler([], reply))
    assert agent.rewrite_query(REWRITE_Q) == (REWRITE_Q, False)


@pytest.mark.parametrize("q,reply", [
    ("какой отпуск у инженера", "какой отпуск у инженера"),
    ("Какой отпуск у инженера?", "какой отпуск у инженера?"),
])
def test_rewrite_query_identical_falls_back(data_dir, q, reply):
    """Ответ идентичен вопросу (case-insensitive) → (question, False)."""
    agent = make_agent(data_dir, _rewrite_handler([], reply))
    assert agent.rewrite_query(q) == (q, False)


def test_rewrite_query_http_error_falls_back(data_dir):
    """LLM-сбой (httpx.ConnectError — HTTPError) → (question, False),
    исключение наружу НЕ пробрасывается."""
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if "stream" in payload:
            return httpx.Response(
                200, content=sse_body([delta_chunk("Ок"), usage_chunk(),
                                       "[DONE]"]).encode("utf-8"))
        raise httpx.ConnectError("LLM down")
    agent = make_agent(data_dir, handler)
    assert agent.rewrite_query(REWRITE_Q) == (REWRITE_Q, False)


def test_rewrite_query_http_500_falls_back(data_dir):
    """LLM вернул 500 (RuntimeError из _task_llm_call) → (question, False)."""
    captured = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if "stream" in payload:
            return httpx.Response(
                200, content=sse_body([delta_chunk("Ок"), usage_chunk(),
                                       "[DONE]"]).encode("utf-8"))
        captured.append(payload)
        return httpx.Response(500, json={"error": "LLM down"})
    agent = make_agent(data_dir, handler)
    assert agent.rewrite_query(REWRITE_Q) == (REWRITE_Q, False)
    assert "Ошибка:" not in repr(agent.rewrite_query(REWRITE_Q))


# ---------- День 23: guardrail — rewrite_query вне ask_stream ----------

def test_ask_stream_does_not_call_rewrite_query(data_dir, monkeypatch):
    """Спиц на rewrite_query: ask_stream (живой чат) его НЕ вызывает."""
    agent = make_agent(data_dir, ok_handler)
    d = agent.store.new_dialogue()
    ready(agent, d)
    calls = []

    def spy(question):
        calls.append(question)
        raise AssertionError("rewrite_query вызван из ask_stream")
    monkeypatch.setattr(agent, "rewrite_query", spy)
    events = list(agent.ask_stream(d["id"], "привет"))
    assert events[-1]["type"] == "done"
    assert calls == []


def test_ask_stream_source_has_no_rewrite_query_reference():
    """Статический гард: в теле ask_stream нет ссылок на rewrite_query."""
    import inspect
    src = inspect.getsource(StudioAgent.ask_stream)
    assert "rewrite_query" not in src


# ---------- День 24: dont-know intercept + [n]-правило + chunk_id ----------

def _counting_handler(calls):
    """Fake-LLM: считает ВЕСЬ обращения (stream и non-stream), отдаёт
    стандартные дельты. Stream payload сохраняется в calls['payloads']."""
    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        payload = json.loads(request.content)
        if "stream" in payload:
            calls.setdefault("payloads", []).append(payload)
        return httpx.Response(
            200, content=sse_body([delta_chunk("Прив"), delta_chunk("ет"),
                                   usage_chunk(), "[DONE]"]).encode("utf-8"))
    calls = {"n": 0}
    return handler, calls


def test_ask_stream_dont_know_min_score_filters_all(data_dir, tmp_path,
                                                    monkeypatch):
    """День 24, триггер A: RAG-вкл, tmp-БЗ (HashEmbedder), fake-LLM,
    min_score=0.999 + fake-реранкер (scores < 0.999) — весь recall
    отфильтрован: done.answer == DONT_KNOW_TEXT, usage None,
    request_id None, LLM-вызовов 0, сохранённое сообщение с
    rag_context {recall_total, reranked, chunks: [], dont_know: True}."""
    import kb as kb_module
    from agent import DONT_KNOW_TEXT

    class FakeReranker:
        def __init__(self, base_url, api_key, client=None):
            pass

        def rerank(self, query, texts):
            return [round((i + 1) / 100.0, 6) for i in range(len(texts))]

    monkeypatch.setenv("GPUSTACK_KEY_RERANK", "fake-key")
    monkeypatch.setattr(kb_module, "APIReranker", FakeReranker)

    kb = _fact_repo(tmp_path, "repo_dk_minscore")
    kb.update_settings({"reranker": "api", "min_score": 0.999})
    handler, calls = _counting_handler(None)
    agent = make_agent(data_dir, handler)
    agent.kb = kb
    d = agent.store.new_dialogue()
    ready(agent, d)
    agent.store.rename_dialogue(d["id"], "Д24")  # без авто-заголовка
    events = list(agent.ask_stream(d["id"],
                                   "какая модель телефона была у героя"))
    assert events[-1] == {"type": "done", "answer": DONT_KNOW_TEXT,
                          "usage": None, "request_id": None}
    assert calls["n"] == 0, "LLM при dont-know не вызывается"
    msgs = agent.store.get_messages(d["id"])
    asst = [m for m in msgs if m["role"] == "assistant"]
    assert asst and asst[-1]["content"] == DONT_KNOW_TEXT
    ctx = asst[-1]["rag_context"]
    assert set(ctx) == {"recall_total", "reranked", "chunks", "dont_know"}
    assert ctx["chunks"] == []
    assert ctx["dont_know"] is True
    assert ctx["reranked"] is True
    assert ctx["recall_total"] >= 1


def test_ask_stream_dont_know_no_match_min_score_zero(data_dir, tmp_path,
                                                      monkeypatch):
    """День 24, триггер A′ при min_score=0 (дефолт, фильтр выключен):
    вопрос без общих термов с корпусом — поиск УСПЕШЕН, но результатов
    0 → dont-know, 0 LLM-вызовов. Векторная нога отключена (patch),
    чтобы результат был чисто лексическим (BM25 пуст → fused пуст →
    []); hash-векторная нога на любом запросе ранжирует все чанки,
    и «нет совпадений» с ней получить нельзя."""
    from kb import KBError
    from agent import DONT_KNOW_TEXT

    def _no_vec_leg(idx):
        raise KBError("векторная нога отключена (тест)")
    kb = _fact_repo(tmp_path, "repo_dk_nomatch")
    monkeypatch.setattr(kb, "_embedder_from_index", _no_vec_leg)

    handler, calls = _counting_handler(None)
    agent = make_agent(data_dir, handler)
    agent.kb = kb
    d = agent.store.new_dialogue()
    ready(agent, d)
    agent.store.rename_dialogue(d["id"], "Д24")
    events = list(agent.ask_stream(
        d["id"], "какой рецепт борща с говядиной и свёклой"))
    assert events[-1] == {"type": "done", "answer": DONT_KNOW_TEXT,
                          "usage": None, "request_id": None}
    assert calls["n"] == 0
    msgs = agent.store.get_messages(d["id"])
    asst = [m for m in msgs if m["role"] == "assistant"]
    assert asst and asst[-1]["content"] == DONT_KNOW_TEXT
    ctx = asst[-1]["rag_context"]
    assert ctx["chunks"] == []
    assert ctx["dont_know"] is True
    assert ctx["reranked"] is False
    assert ctx["recall_total"] == 0


def test_ask_stream_no_index_no_dont_know(data_dir):
    """День 24 (negative): индекс не построен (KBError) — обычный
    LLM-вызов (count == 1), dont-know НЕ срабатывает, без краха.
    Сбой поиска ≠ «в базе пусто»."""
    from agent import DONT_KNOW_TEXT

    handler, calls = _counting_handler(None)
    agent = make_agent(data_dir, handler)  # kb БЕЗ индекса
    d = agent.store.new_dialogue()
    ready(agent, d)
    agent.store.rename_dialogue(d["id"], "Д24")
    events = list(agent.ask_stream(d["id"], "привет"))
    assert events[-1]["type"] == "done"
    assert events[-1]["answer"] == "Привет"
    assert events[-1]["answer"] != DONT_KNOW_TEXT
    assert calls["n"] == 1
    msgs = agent.store.get_messages(d["id"])
    asst = [m for m in msgs if m["role"] == "assistant"]
    assert asst
    assert "dont_know" not in (asst[-1].get("rag_context") or {})


def test_ask_stream_rag_false_no_retrieval_no_dont_know(data_dir, tmp_path,
                                                        monkeypatch):
    """День 24: per-диалог rag=false — retrieval НЕ идёт (spy на
    search_rag), dont-know невозможен; обычный LLM-ответ."""
    kb = _fact_repo(tmp_path, "repo_ragoff")

    seen = []
    orig = kb.search_rag

    def spy(query, recall, top_k, reranker_mode="off", **kwargs):
        seen.append(query)
        return orig(query, recall, top_k, reranker_mode, **kwargs)
    monkeypatch.setattr(kb, "search_rag", spy)

    handler, calls = _counting_handler(None)
    agent = make_agent(data_dir, handler)
    agent.kb = kb
    d = agent.store.new_dialogue()
    ready(agent, d)
    agent.store.set_dialogue_rag(d["id"], False)
    agent.store.rename_dialogue(d["id"], "Д24")
    events = list(agent.ask_stream(d["id"],
                                   "какая модель телефона была у героя"))
    assert events[-1]["type"] == "done"
    assert calls["n"] == 1
    assert seen == []  # retrieval не шёл
    msgs = agent.store.get_messages(d["id"])
    asst = [m for m in msgs if m["role"] == "assistant"]
    assert asst
    assert "dont_know" not in (asst[-1].get("rag_context") or {})
    assert asst[-1]["content"] != "Не знаю."


def test_kb_block_cite_rule_in_system_payload(data_dir, tmp_path):
    """День 24: kb_block непустой → CITE_RULE (точная строка) в content
    system-сообщения captured LLM-payload; результат пустой (нет
    индекса) → правила в system-сообщении нет."""
    from agent import CITE_RULE

    kb = _fact_repo(tmp_path, "repo_cite")
    handler, calls = _counting_handler(None)
    agent = make_agent(data_dir, handler)
    agent.kb = kb
    d = agent.store.new_dialogue()
    ready(agent, d)
    agent.store.rename_dialogue(d["id"], "Д24")
    list(agent.ask_stream(d["id"],
                          "какая модель телефона была у героя"))
    sys_content = calls["payloads"][0]["messages"][0]["content"]
    assert CITE_RULE in sys_content

    # пустой результат (индекс не построен): правила нет
    data2 = tmp_path / "data2"
    data2.mkdir()
    handler2, calls2 = _counting_handler(None)
    agent2 = make_agent(data2, handler2)
    d2 = agent2.store.new_dialogue()
    ready(agent2, d2)
    agent2.store.rename_dialogue(d2["id"], "Д24")
    list(agent2.ask_stream(d2["id"], "привет"))
    assert CITE_RULE not in calls2["payloads"][0]["messages"][0]["content"]


def test_kb_context_chunk_id_snippet_subset(data_dir, tmp_path):
    """День 24: chunks[] в _kb_context несут chunk_id (аддитивно);
    text — focused snippet ≤ 300 символов И ⊂ сохранённого текста
    чанка из БЗ."""
    kb = _fact_repo(tmp_path, "repo_cid")
    agent = make_agent(data_dir, ok_handler)
    agent.kb = kb
    d = agent.store.new_dialogue()
    ready(agent, d)
    agent.store.rename_dialogue(d["id"], "Д24")
    list(agent.ask_stream(d["id"],
                          "какая модель телефона была у героя"))
    msgs = agent.store.get_messages(d["id"])
    asst = [m for m in msgs if m["role"] == "assistant"]
    ctx = asst[-1]["rag_context"]
    assert ctx["chunks"]
    idx_chunks = {c["chunk_id"]: c["text"]
                  for c in kb.load_index()["chunks"]}
    for c in ctx["chunks"]:
        assert "chunk_id" in c
        stored = idx_chunks[c["chunk_id"]]
        assert c["text"] in stored, "snippet не ⊂ текста чанка из БЗ"
        assert len(c["text"]) <= 300
