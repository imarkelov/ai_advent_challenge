"""Тесты FastAPI-роутов «Студии» (офлайн, MockTransport для LLM)."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from agent import StudioAgent
from conftest import delta_chunk, sse_body, usage_chunk


@pytest.fixture
def agent_env(tmp_path):
    """Агент с MockTransport: /chat/completions -> SSE, /models -> JSON."""
    d = tmp_path / "data"
    d.mkdir()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [
                {"id": "qwen3.8-27b"},
                {"id": "unknown-x"},
            ]})
        payload = json.loads(request.content)
        # зонд доступности: у ключа нет доступа к unknown-x (403)
        if payload.get("model") == "unknown-x":
            return httpx.Response(403, json={"message": "no access"})
        # non-stream — запрос авто-заголовка диалога
        if "stream" not in payload:
            return httpx.Response(200, json={"choices": [
                {"message": {"content": "E2E-название"}}]})
        body = sse_body([delta_chunk("Прив"), delta_chunk("ет"), usage_chunk(), "[DONE]"])
        return httpx.Response(200, content=body.encode("utf-8"))

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    return StudioAgent(str(d), base_url="https://mock.local/v1",
                       api_key="test-key", client=http_client)


@pytest.fixture
def client(agent_env):
    from main import create_app
    return TestClient(create_app(agent_env))


@pytest.fixture
def dialogue_id(client):
    r = client.post("/api/dialogues")
    assert r.status_code == 201
    return r.json()["dialogue"]["id"]


def parse_sse(lines):
    """Разобрать SSE-строки 'data: {...}' в список событий."""
    out = []
    for l in lines:
        if l.startswith("data: "):
            out.append(json.loads(l[len("data: "):]))
    return out


# ---------- /api/chat ----------

def test_chat_sse_stream(client, dialogue_id):
    with client.stream("POST", "/api/chat",
                       json={"dialogue_id": dialogue_id, "message": "привет"}) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        lines = list(resp.iter_lines())
    events = parse_sse(lines)
    assert [e["type"] for e in events] == ["delta", "delta", "done"]
    assert events[0]["text"] == "Прив"
    done = events[-1]
    assert done["answer"] == "Привет"
    assert done["usage"]["total_tokens"] == 15
    assert done["request_id"] == 1


def test_chat_unknown_dialogue_404(client):
    r = client.post("/api/chat", json={"dialogue_id": "nope", "message": "привет"})
    assert r.status_code == 404
    assert "не найден" in r.json()["detail"]


def test_chat_empty_message_400(client, dialogue_id):
    r = client.post("/api/chat", json={"dialogue_id": dialogue_id, "message": ""})
    assert r.status_code == 400
    assert r.json()["detail"]


# ---------- /api/config ----------

def test_config_get_defaults(client):
    r = client.get("/api/config")
    assert r.status_code == 200
    cfg = r.json()
    assert cfg["model"] == "qwen3.8-27b"
    assert cfg["temperature"] == 0.7
    assert cfg["max_tokens"] == 2048


def test_config_post_partial(client):
    r = client.post("/api/config", json={"temperature": 1.2})
    assert r.status_code == 200
    assert r.json()["temperature"] == 1.2
    assert r.json()["model"] == "qwen3.8-27b"
    assert client.get("/api/config").json()["temperature"] == 1.2


def test_config_post_invalid_400(client):
    r = client.post("/api/config", json={"temperature": 5})
    assert r.status_code == 400
    assert r.json()["detail"]
    assert client.get("/api/config").json()["temperature"] == 0.7  # не изменился


# ---------- /api/models ----------

def test_models(client):
    """unknown-x не в списке — зонд получил 403 (нет доступа у ключа)."""
    r = client.get("/api/models")
    assert r.status_code == 200
    assert r.json() == {"models": [
        {"id": "qwen3.8-27b", "context_limit": 32768},
    ]}


def test_models_self_heals_config(client):
    """GET /api/models сбрасывает недоступную модель из конфига на доступную."""
    assert client.post("/api/config", json={"model": "ghost-model"}).status_code == 200
    r = client.get("/api/models")
    assert r.status_code == 200
    assert client.get("/api/config").json()["model"] == "qwen3.8-27b"


def test_models_per_model_keys(tmp_path):
    """3 ключа по моделям -> /api/models отдаёт 3 модели; gpt-4o (403) — нет."""
    d = tmp_path / "d"
    d.mkdir()

    def handler(request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [
                {"id": "qwen3.8-27b"}, {"id": "deepseek-v4-flash"},
                {"id": "glm-5.3-flash"}, {"id": "gpt-4o"}]})
        model = json.loads(request.content)["model"]
        wanted = {"qwen3.8-27b": "Bearer k-main", "deepseek-v4-flash": "Bearer k-deep",
                  "glm-5.3-flash": "Bearer k-glm", "gpt-4o": "Bearer k-none"}[model]
        return httpx.Response(200 if request.headers.get("Authorization") == wanted
                              else 403, json={})

    agent = StudioAgent(str(d), base_url="https://mock.local/v1", api_key="test-key",
                        client=httpx.Client(transport=httpx.MockTransport(handler)),
                        env={"GPUSTACK_API_KEY": "k-main",
                             "GPUSTACK_KEY_DEEPSEEK": "k-deep",
                             "GPUSTACK_KEY_GLM": "k-glm"})
    from main import create_app
    c = TestClient(create_app(agent))
    r = c.get("/api/models")
    assert r.status_code == 200
    assert [m["id"] for m in r.json()["models"]] == \
        ["qwen3.8-27b", "deepseek-v4-flash", "glm-5.3-flash"]


def test_models_unavailable_502(tmp_path):
    def handler(request):
        raise httpx.ConnectError("нет сети", request=request)

    d = tmp_path / "d"
    d.mkdir()
    agent = StudioAgent(str(d), base_url="https://mock.local/v1", api_key="k",
                        client=httpx.Client(transport=httpx.MockTransport(handler)))
    from main import create_app
    c = TestClient(create_app(agent))
    r = c.get("/api/models")
    assert r.status_code == 502
    assert r.json()["detail"]


def test_chat_auto_titles_new_dialogue(client, dialogue_id):
    """Первое сообщение в новом диалоге → бэкенд сам называет диалог."""
    r = client.post("/api/chat",
                    json={"dialogue_id": dialogue_id, "message": "Привет"})
    assert r.status_code == 200
    d = client.get(f"/api/dialogues/{dialogue_id}").json()["dialogue"]
    assert d["title"] == "E2E-название"
    # assistant-сообщение хранится с model
    assert d["messages"][-1] == {"role": "assistant", "content": "Привет",
                                 "model": "qwen3.8-27b"}


# ---------- /api/dialogues: rename ----------

def test_rename_dialogue(client, dialogue_id):
    r = client.post(f"/api/dialogues/{dialogue_id}/rename", json={"title": "Новое имя"})
    assert r.status_code == 200
    assert r.json()["dialogue"]["title"] == "Новое имя"
    titles = [d["title"] for d in client.get("/api/dialogues").json()["dialogues"]]
    assert "Новое имя" in titles


def test_rename_dialogue_missing_404(client):
    r = client.post("/api/dialogues/nope/rename", json={"title": "x"})
    assert r.status_code == 404


def test_rename_dialogue_empty_title_400(client, dialogue_id):
    r = client.post(f"/api/dialogues/{dialogue_id}/rename", json={"title": "  "})
    assert r.status_code == 400
    r2 = client.post(f"/api/dialogues/{dialogue_id}/rename", json={})
    assert r2.status_code == 400


# ---------- /api/dialogues ----------

def test_dialogues_list_empty(client):
    r = client.get("/api/dialogues")
    assert r.status_code == 200
    assert r.json() == {"active_id": None, "dialogues": []}


def test_dialogues_crud(client):
    r = client.post("/api/dialogues")
    assert r.status_code == 201
    body = r.json()
    assert body["active_id"] == body["dialogue"]["id"]
    did = body["dialogue"]["id"]
    assert body["dialogue"]["messages"] == []

    # второй диалог, порядок списка
    d2 = client.post("/api/dialogues").json()["dialogue"]["id"]
    lst = client.get("/api/dialogues").json()
    assert [x["id"] for x in lst["dialogues"]] == [did, d2]
    assert lst["active_id"] == d2

    # get с сообщениями
    r = client.get(f"/api/dialogues/{did}")
    assert r.status_code == 200
    assert r.json()["dialogue"]["id"] == did
    assert client.get("/api/dialogues/nope").status_code == 404

    # activate
    r = client.post(f"/api/dialogues/{did}/activate")
    assert r.status_code == 200
    assert r.json() == {"active_id": did}
    assert client.post("/api/dialogues/nope/activate").status_code == 404

    # delete
    assert client.delete(f"/api/dialogues/{did}").json() == {"ok": True}
    assert client.delete(f"/api/dialogues/{did}").status_code == 404
    # после удаления первого активным стал последний оставшийся
    assert client.get("/api/dialogues").json()["active_id"] == d2


# ---------- память ----------

def test_memory_st_clear_no_active_400(client):
    assert client.post("/api/memory/st/clear").status_code == 400


def test_memory_st_clear(client, dialogue_id):
    assert client.post("/api/memory/st/clear").json() == {"ok": True}


def test_memory_get(client, dialogue_id):
    client.post("/api/memory/working", json={"key": "k", "value": "v"})
    client.post("/api/memory/longterm", json={"key": "lk", "value": "lv"})
    r = client.get("/api/memory")
    assert r.status_code == 200
    m = r.json()
    assert m["active_id"] == dialogue_id
    assert m["dialogue"]["message_count"] == 0
    assert m["working"]["items"] == {"k": "v"}
    assert m["working"]["entries"] == 1
    assert m["long_term"]["items"] == {"lk": "lv"}


def test_memory_working_routes(client):
    # нет активного диалога
    assert client.post("/api/memory/working", json={"key": "k", "value": "v"}).status_code == 400
    did = client.post("/api/dialogues").json()["dialogue"]["id"]
    # пустой ключ
    assert client.post("/api/memory/working", json={"key": "", "value": "v"}).status_code == 400
    # успех
    assert client.post("/api/memory/working", json={"key": "k", "value": "v"}).json() == {"ok": True}
    assert client.get("/api/memory").json()["working"]["items"] == {"k": "v"}
    # delete: есть / нет
    assert client.delete("/api/memory/working/k").json() == {"ok": True}
    assert client.delete("/api/memory/working/k").status_code == 404
    # clear
    client.post("/api/memory/working", json={"key": "k2", "value": "v2"})
    assert client.post("/api/memory/working/clear").json() == {"ok": True}
    assert client.get("/api/memory").json()["working"]["items"] == {}


def test_memory_longterm_routes(client):
    assert client.post("/api/memory/longterm", json={"key": "", "value": "v"}).status_code == 400
    assert client.post("/api/memory/longterm", json={"key": "k", "value": "v"}).json() == {"ok": True}
    assert client.get("/api/memory").json()["long_term"]["items"] == {"k": "v"}
    assert client.delete("/api/memory/longterm/k").json() == {"ok": True}
    assert client.delete("/api/memory/longterm/k").status_code == 404
    client.post("/api/memory/longterm", json={"key": "k2", "value": "v2"})
    assert client.post("/api/memory/longterm/clear").json() == {"ok": True}
    assert client.get("/api/memory").json()["long_term"]["items"] == {}


# ---------- /api/tokens ----------

def test_tokens_before_chat(client):
    r = client.get("/api/tokens")
    assert r.status_code == 200
    assert r.json() == {"last": None, "session": {"prompt": 0, "completion": 0, "total": 0},
                        "context_limit": 32768}


def test_tokens_after_chat(client, dialogue_id):
    with client.stream("POST", "/api/chat",
                       json={"dialogue_id": dialogue_id, "message": "привет"}) as resp:
        list(resp.iter_lines())
    t = client.get("/api/tokens").json()
    assert t["last"] == {"prompt": 10, "completion": 5, "reasoning": 2, "total": 15}
    assert t["session"] == {"prompt": 10, "completion": 5, "total": 15}
    assert t["context_limit"] == 32768


# ---------- /api/requests ----------

def test_requests_routes(client, dialogue_id):
    with client.stream("POST", "/api/chat",
                       json={"dialogue_id": dialogue_id, "message": "привет"}) as resp:
        list(resp.iter_lines())
    # список без тел
    r = client.get("/api/requests")
    assert r.status_code == 200
    items = r.json()["requests"]
    assert len(items) == 1
    assert set(items[0]) == {"id", "ts", "model", "total_tokens", "error"}
    assert items[0]["total_tokens"] == 15
    assert items[0]["error"] is None
    # полная запись по id
    r = client.get("/api/requests/1")
    assert r.status_code == 200
    assert r.json()["request"]["messages"][0]["role"] == "system"
    assert r.json()["usage"]["total_tokens"] == 15
    assert client.get("/api/requests/99").status_code == 404
    # delete
    assert client.delete("/api/requests").json() == {"ok": True}
    assert client.get("/api/requests").json() == {"requests": []}


# ---------- /api/rules ----------

def test_rules_inactive_without_memory(client, dialogue_id):
    """Без записей памяти правило неактивно, но возвращается в полном виде."""
    r = client.get("/api/rules")
    assert r.status_code == 200
    body = r.json()
    assert body["rule_active"] is False
    assert body["memory_rule"]
    assert body["system_prompt"] == client.get("/api/config").json()["system_prompt"]


def test_rules_active_with_working_memory(client, dialogue_id):
    assert client.post("/api/memory/working", json={"key": "k", "value": "v"}).status_code == 200
    assert client.get("/api/rules").json()["rule_active"] is True


def test_rules_active_with_longterm_memory(client, dialogue_id):
    assert client.post("/api/memory/longterm", json={"key": "k", "value": "v"}).status_code == 200
    assert client.get("/api/rules").json()["rule_active"] is True
