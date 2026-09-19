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


def test_memory_rule_forbids_silent_compliance():
    """Само правило: память — ограничения, тихое подчинение запрещено."""
    assert "противореч" in MEMORY_RULE
    assert "молча" in MEMORY_RULE


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
    events = list(agent.ask_stream(d["id"], "привет"))
    assert events[0] == {"type": "delta", "text": "Прив"}
    assert events[1] == {"type": "delta", "text": "ет"}
    done = events[2]
    assert done["type"] == "done"
    assert done["answer"] == "Привет"
    assert done["usage"] == USAGE
    assert done["request_id"] == 1
    # сообщение сохранено в диалоге
    assert agent.store.get_messages(d["id"]) == [
        {"role": "user", "content": "привет"},
        {"role": "assistant", "content": "Привет"},
    ]
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


# ---------- ask_stream: ошибки ----------

def test_ask_stream_connect_error(data_dir):
    def handler(request):
        raise httpx.ConnectError("нет сети", request=request)

    agent = make_agent(data_dir, handler)
    d = agent.store.new_dialogue()
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
    events = list(agent.ask_stream(d["id"], "привет"))
    assert events == [{"type": "error", "message": "Модель вернула ошибку HTTP 500"}]
    assert agent.requests_list()[0]["error"] is not None


def test_ask_stream_broken_chunk(data_dir):
    def handler(request):
        return httpx.Response(200,
                              content="data: {битый json\n\ndata: [DONE]\n\n".encode("utf-8"))

    agent = make_agent(data_dir, handler)
    d = agent.store.new_dialogue()
    events = list(agent.ask_stream(d["id"], "привет"))
    assert len(events) == 1
    assert events[0]["type"] == "error"
    assert agent.requests_list()[0]["error"] is not None


# ---------- журнал ----------

def test_journal_list_and_get(data_dir):
    agent = make_agent(data_dir, ok_handler)
    d = agent.store.new_dialogue()
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
