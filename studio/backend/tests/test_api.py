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
    client.post("/api/profile/action", json={"dialogue_id": dialogue_id,
                                             "action": "decline"})
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
    client.post("/api/profile/action", json={"dialogue_id": dialogue_id,
                                             "action": "decline"})
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


def test_memory_toggles_routes(client, dialogue_id):
    # по умолчанию всё включено (и в GET /api/memory, и в /api/memory/toggles)
    assert client.get("/api/memory/toggles").json()["toggles"] == {"st": True, "wm": True, "lt": True}
    assert client.get("/api/memory").json()["toggles"]["wm"] is True
    # off/on в любой последовательности
    assert client.post("/api/memory/toggles", json={"layer": "wm", "enabled": False}).status_code == 200
    assert client.post("/api/memory/toggles", json={"layer": "st", "enabled": False}).json()["toggles"] == {"st": False, "wm": False, "lt": True}
    assert client.get("/api/memory").json()["toggles"] == {"st": False, "wm": False, "lt": True}
    assert client.post("/api/memory/toggles", json={"layer": "wm", "enabled": True}).json()["toggles"]["wm"] is True
    # обратно всё вкл
    assert client.post("/api/memory/toggles", json={"layer": "st", "enabled": True}).json()["toggles"]["st"] is True
    # ошибки: неизвестный слой, не-bool, отсутствующее поле
    assert client.post("/api/memory/toggles", json={"layer": "nope", "enabled": True}).status_code == 400
    assert client.post("/api/memory/toggles", json={"layer": "wm", "enabled": "да"}).status_code == 400
    assert client.post("/api/memory/toggles", json={"layer": "wm"}).status_code == 400


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


# ---------- инварианты (день 14) ----------

def test_invariants_crud_routes(client):
    # пустой список
    r = client.get("/api/invariants")
    assert r.status_code == 200
    assert r.json() == {"invariants": []}
    # создание
    r = client.post("/api/invariants", json={"key": "Стек", "value": "Kotlin"})
    assert r.status_code == 201
    rec = r.json()["invariant"]
    assert rec["key"] == "Стек" and rec["value"] == "Kotlin"
    assert rec["id"].startswith("inv_")
    # в списке
    assert client.get("/api/invariants").json()["invariants"] == [rec]
    # обновление по key: id сохраняется, значение меняется
    r2 = client.post("/api/invariants", json={"key": "Стек", "value": "Java"})
    assert r2.status_code == 201
    assert r2.json()["invariant"]["id"] == rec["id"]
    assert r2.json()["invariant"]["value"] == "Java"
    assert len(client.get("/api/invariants").json()["invariants"]) == 1
    # удаление
    assert client.delete(f"/api/invariants/{rec['id']}").json() == {"ok": True}
    assert client.get("/api/invariants").json() == {"invariants": []}
    assert client.delete(f"/api/invariants/{rec['id']}").status_code == 404
    assert "не найден" in client.delete("/api/invariants/nope").json()["detail"]


def test_invariants_post_invalid_400(client):
    # отсутствует поле
    assert client.post("/api/invariants", json={"key": "k"}).status_code == 400
    assert client.post("/api/invariants", json={"value": "v"}).status_code == 400
    # пустое / не str
    assert client.post("/api/invariants", json={"key": "", "value": "v"}).status_code == 400
    assert client.post("/api/invariants", json={"key": "k", "value": "  "}).status_code == 400
    assert client.post("/api/invariants", json={"key": 5, "value": "v"}).status_code == 400
    assert client.post("/api/invariants", json={"key": "k", "value": 7}).status_code == 400
    # после ошибок инвариантов нет
    assert client.get("/api/invariants").json() == {"invariants": []}


def test_rules_includes_invariants_block(client):
    r = client.get("/api/rules")
    assert r.status_code == 200
    assert r.json()["invariants_block"] == ""
    client.post("/api/invariants", json={"key": "Стек", "value": "Kotlin"})
    r2 = client.get("/api/rules")
    assert r2.json()["invariants_block"] == (
        "\n\nИнварианты (неукоснительно):\n- Стек: Kotlin")


def test_memory_get_includes_invariants(client):
    m = client.get("/api/memory").json()
    assert m["invariants"] == {"entries": 0, "tokens_est": 0, "items": {}}
    client.post("/api/invariants", json={"key": "Стек", "value": "Kotlin"})
    m2 = client.get("/api/memory").json()
    assert m2["invariants"]["entries"] == 1
    items = m2["invariants"]["items"]
    assert len(items) == 1
    assert all(set(v) == {"key", "value"} for v in items.values())
    assert next(iter(items.values())) == {"key": "Стек", "value": "Kotlin"}


# ---------- /api/tokens ----------

def test_tokens_before_chat(client):
    r = client.get("/api/tokens")
    assert r.status_code == 200
    assert r.json() == {"last": None, "session": {"prompt": 0, "completion": 0, "total": 0},
                        "context_limit": 32768}


def test_tokens_after_chat(client, dialogue_id):
    client.post("/api/profile/action", json={"dialogue_id": dialogue_id,
                                             "action": "decline"})
    with client.stream("POST", "/api/chat",
                       json={"dialogue_id": dialogue_id, "message": "привет"}) as resp:
        list(resp.iter_lines())
    t = client.get("/api/tokens").json()
    assert t["last"] == {"prompt": 10, "completion": 5, "reasoning": 2, "total": 15}
    assert t["session"] == {"prompt": 10, "completion": 5, "total": 15}
    assert t["context_limit"] == 32768


# ---------- /api/requests ----------

def test_requests_routes(client, dialogue_id):
    client.post("/api/profile/action", json={"dialogue_id": dialogue_id,
                                             "action": "decline"})
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


def test_rules_includes_profile(client, dialogue_id):
    # pending → блок пуст
    r = client.get("/api/rules")
    assert r.status_code == 200
    assert r.json()["profile_block"] == ""
    assert r.json()["profile_status"] == "pending"
    # active → блок заполнен
    client.post("/api/profile", json={
        "dialogue_id": dialogue_id, "name": "Иван", "role": "",
        "tone": "", "taboos": ""})
    r2 = client.get("/api/rules", params={"dialogue_id": dialogue_id})
    assert r2.json()["profile_status"] == "active"
    assert "Иван" in r2.json()["profile_block"]


# ---------- профиль: REST (день 12) ----------

def test_profile_set_endpoint(client, dialogue_id):
    r = client.post("/api/profile", json={
        "dialogue_id": dialogue_id, "name": "Иван",
        "role": "backend", "tone": "кратко", "taboos": "мат"})
    assert r.status_code == 200
    p = r.json()["profile"]
    assert p["status"] == "active" and p["name"] == "Иван"
    r2 = client.get(f"/api/dialogues/{dialogue_id}")
    assert r2.json()["dialogue"]["profile"]["status"] == "active"


def test_profile_set_empty_keeps_pending(client, dialogue_id):
    r = client.post("/api/profile", json={
        "dialogue_id": dialogue_id, "name": "", "role": "",
        "tone": "", "taboos": ""})
    assert r.status_code == 200
    assert r.json()["profile"]["status"] == "pending"


def test_profile_set_non_str_400(client, dialogue_id):
    r = client.post("/api/profile", json={
        "dialogue_id": dialogue_id, "name": 5, "role": "",
        "tone": "", "taboos": ""})
    assert r.status_code == 400
    assert "строки" in r.json()["detail"] or "Поля" in r.json()["detail"]


def test_profile_set_unknown_dialogue_404(client):
    r = client.post("/api/profile", json={
        "dialogue_id": "nope", "name": "a", "role": "",
        "tone": "", "taboos": ""})
    assert r.status_code == 404


def test_profile_action_interview_decline_reset(client, dialogue_id):
    r = client.post("/api/profile/action", json={
        "dialogue_id": dialogue_id, "action": "interview"})
    assert r.status_code == 200
    assert r.json()["profile"]["interview"] is True
    r = client.post("/api/profile/action", json={
        "dialogue_id": dialogue_id, "action": "decline"})
    assert r.json()["profile"]["status"] == "declined"
    r = client.post("/api/profile/action", json={
        "dialogue_id": dialogue_id, "action": "reset"})
    assert r.json()["profile"]["status"] == "pending"
    assert r.json()["profile"]["name"] == ""


def test_profile_action_unknown_400(client, dialogue_id):
    r = client.post("/api/profile/action", json={
        "dialogue_id": dialogue_id, "action": "bogus"})
    assert r.status_code == 400
    assert "Неизвестное действие" in r.json()["detail"]


def test_profile_action_unknown_dialogue_404(client):
    r = client.post("/api/profile/action", json={
        "dialogue_id": "nope", "action": "decline"})
    assert r.status_code == 404


# ---------- задача: новая семантика start/run (день 13b) ----------

def task_dialogue(client):
    """Диалог с declined-профилем (день 12) и активной задачей (start)."""
    did = client.post("/api/dialogues").json()["dialogue"]["id"]
    client.post("/api/profile/action",
                json={"dialogue_id": did, "action": "decline"})
    r = client.post("/api/task/start",
                    json={"dialogue_id": did, "description": "Сделать кнопку"})
    assert r.status_code == 200
    return did


def run_task(client, did):
    """POST /api/task/run: разобрать SSE-кадры в список событий."""
    with client.stream("POST", "/api/task/run",
                       json={"dialogue_id": did}) as resp:
        assert resp.status_code == 200
        return parse_sse(list(resp.iter_lines()))


def task_done_dialogue(client):
    """Диалог с завершённой задачей (пайплайн доведён до task_done)."""
    did = task_dialogue(client)
    assert run_task(client, did)[-1]["type"] == "task_done"
    return did


def test_start_returns_task_with_id_and_user_message(client, dialogue_id):
    r = client.post("/api/task/start",
                    json={"dialogue_id": dialogue_id, "description": "Сделать кнопку"})
    assert r.status_code == 200
    t = r.json()["task"]
    assert t["active"] is True
    assert t["task_id"].startswith("t_")
    assert t["stage"] == "planning"
    assert len(t["plan"]) == 4
    # user-сообщение-запрос — якорь карточки с маркером task_id
    d = client.get(f"/api/dialogues/{dialogue_id}").json()["dialogue"]
    first = d["messages"][0]
    assert first["role"] == "user"
    assert first["content"] == "Сделать кнопку"
    assert first["task_id"] == t["task_id"]


def test_start_rejects_active(client, dialogue_id):
    client.post("/api/task/start",
                json={"dialogue_id": dialogue_id, "description": "X"})
    r = client.post("/api/task/start",
                    json={"dialogue_id": dialogue_id, "description": "Y"})
    assert r.status_code == 400
    assert "уже активна" in r.json()["detail"]


def test_start_after_done_is_new(client, dialogue_id):
    did = task_dialogue(client)
    tid1 = client.get("/api/task",
                      params={"dialogue_id": did}).json()["task"]["task_id"]
    assert run_task(client, did)[-1]["type"] == "task_done"
    r = client.post("/api/task/start",
                    json={"dialogue_id": did, "description": "Новая"})
    assert r.status_code == 200
    t2 = r.json()["task"]
    assert t2["task_id"] != tid1
    assert t2["stage"] == "planning"
    # в диалоге — 2 user-сообщения с разными task_id
    msgs = client.get(f"/api/dialogues/{did}").json()["dialogue"]["messages"]
    user_tids = [m["task_id"] for m in msgs if m["role"] == "user"]
    assert user_tids == [tid1, t2["task_id"]]


def test_start_empty_description(client, dialogue_id):
    r = client.post("/api/task/start",
                    json={"dialogue_id": dialogue_id, "description": "  "})
    assert r.status_code == 400
    assert "не может быть пустым" in r.json()["detail"]


def test_start_unknown_dialogue(client):
    r = client.post("/api/task/start",
                    json={"dialogue_id": "nope", "description": "X"})
    assert r.status_code == 404
    assert "не найден" in r.json()["detail"]


def test_run_without_task(client, dialogue_id):
    r = client.post("/api/task/run", json={"dialogue_id": dialogue_id})
    assert r.status_code == 400
    assert r.json()["detail"] == "Задача не активна"


def test_run_on_done(client):
    did = task_done_dialogue(client)
    r = client.post("/api/task/run", json={"dialogue_id": did})
    assert r.status_code == 400
    assert r.json()["detail"] == "Задача завершена"


def test_run_full_sse(client):
    did = task_dialogue(client)
    events = run_task(client, did)
    seq = [(e["type"], e.get("stage")) for e in events]
    assert seq[0] == ("agent_spawned", "planning")
    assert ("stage_done", "planning") in seq
    assert ("agent_spawned", "execution") in seq
    assert ("step_updated", None) in seq
    assert ("step_delta", None) in seq
    assert ("agent_spawned", "validation") in seq
    assert ("stage_done", "validation") in seq
    assert ("agent_spawned", "done") in seq
    assert ("stage_done", "done") in seq
    assert events[-1]["type"] == "task_done"
    # stage-сообщения с маркерами в истории диалога
    msgs = client.get(f"/api/dialogues/{did}").json()["dialogue"]["messages"]
    assert [m["task_stage"] for m in msgs if m.get("task_stage")] == \
        ["planning", "execution", "validation"]
    t = client.get("/api/task", params={"dialogue_id": did}).json()["task"]
    assert t["stage"] == "done"


def test_pause_resume(client, agent_env):
    did = task_dialogue(client)
    agent_env.store.task_pause(did)
    events = run_task(client, did)
    assert events[-1] == {"type": "task_paused", "stage": "paused"}
    t = client.get("/api/task", params={"dialogue_id": did}).json()["task"]
    assert t["stage"] == "paused"
    r = client.post("/api/task/instruction",
                    json={"dialogue_id": did, "text": "Используй Kotlin"})
    assert r.status_code == 200
    assert r.json()["task"]["instruction"] == "Используй Kotlin"
    assert client.post("/api/task/resume",
                       json={"dialogue_id": did}).status_code == 200
    events = run_task(client, did)
    assert events[-1]["type"] == "task_done"
    t = client.get("/api/task", params={"dialogue_id": did}).json()["task"]
    assert t["stage"] == "done" and t["instruction"] == ""


def test_instruction_rejected_outside_pause(client, dialogue_id):
    did = task_dialogue(client)
    r = client.post("/api/task/instruction",
                    json={"dialogue_id": did, "text": "x"})
    assert r.status_code == 400
    assert "только на паузе" in r.json()["detail"]


def test_chat_guard_during_run(client, agent_env):
    did = task_dialogue(client)
    r = client.post("/api/chat", json={"dialogue_id": did, "message": "привет"})
    assert r.status_code == 200  # SSE; внутри — error-событие
    assert "Задача выполняется" in r.text
    # user-сообщение чата в диалог НЕ добавлено
    msgs = client.get(f"/api/dialogues/{did}").json()["dialogue"]["messages"]
    assert all(m.get("content") != "привет" for m in msgs)
    # на paused — гард не срабатывает
    agent_env.store.task_pause(did)
    r2 = client.post("/api/chat", json={"dialogue_id": did, "message": "привет"})
    assert '"type": "done"' in r2.text


def test_task_in_dialogues_output(client, dialogue_id):
    client.post("/api/task/start",
                json={"dialogue_id": dialogue_id, "description": "X"})
    d = client.get("/api/dialogues").json()["dialogues"][0]
    t = d["task"]
    assert t["active"] is True
    assert t["task_id"].startswith("t_")
    assert isinstance(t["plan"], list) and len(t["plan"]) == 4
    assert isinstance(t["work_steps"], list)
    d2 = client.get(f"/api/dialogues/{dialogue_id}").json()["dialogue"]
    assert d2["task"]["task_id"] == t["task_id"]


def test_dialogues_list_used_task_flag(client, dialogue_id):
    # до задачи — флаг False (все диалоги в списке несли его)
    lst = client.get("/api/dialogues").json()["dialogues"]
    assert all(d["used_task"] is False for d in lst)
    # после task/start — True
    client.post("/api/task/start",
                json={"dialogue_id": dialogue_id, "description": "X"})
    assert client.get("/api/dialogues").json()["dialogues"][0]["used_task"] is True
    # флаг персистентный: reset задачу сбрасывает, а used_task — нет
    client.post("/api/task/reset", json={"dialogue_id": dialogue_id})
    assert client.get("/api/task",
                      params={"dialogue_id": dialogue_id}).json()["task"]["active"] is False
    assert client.get("/api/dialogues").json()["dialogues"][0]["used_task"] is True


def test_task_reset(client):
    did = task_dialogue(client)
    assert client.post("/api/task/reset",
                       json={"dialogue_id": did}).status_code == 200
    t = client.get("/api/task", params={"dialogue_id": did}).json()["task"]
    assert t["active"] is False and t["task_id"] is None
    # после reset — новая задача
    r = client.post("/api/task/start",
                    json={"dialogue_id": did, "description": "Новая"})
    assert r.status_code == 200
    assert r.json()["task"]["description"] == "Новая"


def test_run_on_failed_retries(tmp_path):
    """500 на первом run (Исполнитель) → task_failed; повторный run
    (mock healthy) → SSE: task_resumed → … → task_done."""
    state = {"fail": True}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "qwen3.8-27b"}]})
        payload = json.loads(request.content)
        if state["fail"]:
            system = (payload["messages"][0] or {}).get("content", "")
            if "Исполнитель" in system:
                return httpx.Response(500, text="boom") \
                    if payload.get("stream") \
                    else httpx.Response(500, json={"error": "boom"})
        if "stream" not in payload:
            return httpx.Response(200, json={"choices": [
                {"message": {"content": "OK"}}]})
        body = sse_body([delta_chunk("Р"), usage_chunk(), "[DONE]"])
        return httpx.Response(200, content=body.encode("utf-8"))

    d = tmp_path / "d"
    d.mkdir()
    agent = StudioAgent(str(d), base_url="https://mock.local/v1",
                        api_key="test-key",
                        client=httpx.Client(transport=httpx.MockTransport(handler)))
    from main import create_app
    c = TestClient(create_app(agent))
    did = c.post("/api/dialogues").json()["dialogue"]["id"]
    c.post("/api/profile/action",
           json={"dialogue_id": did, "action": "decline"})
    r = c.post("/api/task/start",
               json={"dialogue_id": did, "description": "Сделать кнопку"})
    assert r.status_code == 200
    with c.stream("POST", "/api/task/run",
                  json={"dialogue_id": did}) as resp:
        events = parse_sse(list(resp.iter_lines()))
    assert events[-1]["type"] == "task_failed"
    t = c.get("/api/task", params={"dialogue_id": did}).json()["task"]
    assert t["stage"] == "failed" and t["error"]
    # повтор: mock здоров
    state["fail"] = False
    with c.stream("POST", "/api/task/run",
                  json={"dialogue_id": did}) as resp:
        events = parse_sse(list(resp.iter_lines()))
    assert events[0]["type"] == "task_resumed"
    assert events[-1]["type"] == "task_done"
    t = c.get("/api/task", params={"dialogue_id": did}).json()["task"]
    assert t["stage"] == "done"
