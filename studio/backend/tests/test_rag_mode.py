"""Тесты follow-up дня 22 (T1): per-диалог RAG-режим в обычном чате.

Пользователь задаёт вопрос в диалоге с RAG off, в новом — с RAG on, и
сравнивает ответы в голове. RAG-режим — first-class per-диалог контроль:

- поле `rag: bool | null` в записи диалога (null = «следовать глобальному
  settings['rag']»; новые диалоги — null; старые записи без ключа — null);
- POST /api/dialogues/{id}/rag {rag: bool} — 200 (обновлённый диалог),
  400 RU-detail (нет ключа / не настоящий bool — 1/0/строки отклоняются),
  404 RU-detail (диалог не найден);
- GET /api/dialogues + GET /api/dialogues/{id} — поле rag в каждом диалоге;
- ask_stream: эффективный режим = dialogue.rag if not None else
  settings['rag']; false — без блока «База знаний» и без rag_context;
  true — ровно поведение дня 21; без БЗ/индекса — чат жив, падения нет.

Офлайн: tmp-БЗ (music.md-загрузка + HashEmbedder), tmp-данные агента,
httpx.MockTransport fake-LLM (захват system-промпта). Реальные data/kb
и GPustack не используются.
"""
import json
import os

import httpx
import pytest
from fastapi.testclient import TestClient

from agent import StudioAgent
from conftest import delta_chunk, sse_body, usage_chunk
from kb import HashEmbedder, KnowledgeBase
from tests.test_kb_api import _mini_repo, _upload_music

BASE = "https://mock.local/v1"
QUESTION = "что про ксилофон?"
KB_BLOCK = "База знаний"


def _make_kb(tmp_path, build: bool = True) -> KnowledgeBase:
    """Tmp-БЗ (корпус uploads-only, день 21); при build — индекс собран
    (structural + HashEmbedder)."""
    kb = KnowledgeBase(str(tmp_path / "kb"), _mini_repo(tmp_path))
    _upload_music(kb)
    if build:
        kb.build("structural", HashEmbedder())
    return kb


def _make_agent(tmp_path, kb: KnowledgeBase, seen: dict | None = None
                ) -> StudioAgent:
    """Agent на MockTransport; stream-ответы (чат) — 2 дельты + usage;
    seen['system'] — system-промпт последнего stream-payload."""
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if "stream" not in payload:
            return httpx.Response(200, json={"choices": [
                {"message": {"content": "Название"}}]})
        if seen is not None:
            seen["system"] = payload["messages"][0]["content"]
        body = sse_body([delta_chunk("Прив"), delta_chunk("ет"),
                         usage_chunk(), "[DONE]"])
        return httpx.Response(200, content=body.encode("utf-8"))

    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    return StudioAgent(str(data_dir), base_url=BASE, api_key="test-key",
                       client=httpx.Client(
                           transport=httpx.MockTransport(handler)),
                       kb=kb)


def _client(agent, kb) -> TestClient:
    """Ленивый импорт main (паттерн test_rag_compare/test_kb_api: main.py
    в момент импорта load_dotenv'ит .env в os.environ)."""
    from main import create_app
    return TestClient(create_app(agent, kb))


@pytest.fixture
def env(tmp_path):
    """(client, agent, kb, seen, data_dir): индекс собран, глобальный
    settings['rag'] = true (дефолт дня 21)."""
    kb = _make_kb(tmp_path)
    seen = {}
    agent = _make_agent(tmp_path, kb, seen)
    return _client(agent, kb), agent, kb, seen, str(tmp_path / "data")


def _new_dialogue(client) -> dict:
    return client.post("/api/dialogues").json()["dialogue"]


def _set_rag(client, dialogue_id: str, rag):
    return client.post(f"/api/dialogues/{dialogue_id}/rag",
                       json={"rag": rag})


def _last_message(agent, dialogue_id: str) -> dict:
    d = agent.store.get_dialogue(dialogue_id)
    return d["messages"][-1]


# ---------- поле rag в диалогах ----------

def test_new_dialogue_rag_null(env):
    """Новый диалог — rag: null; поле присутствует в GET-списке и
    GET-диалоге (null, а не «отсутствует»)."""
    client, agent, kb, seen, _ = env
    d = _new_dialogue(client)
    assert d["rag"] is None
    lst = client.get("/api/dialogues").json()["dialogues"]
    assert all(x["rag"] is None for x in lst)
    one = client.get(f"/api/dialogues/{d['id']}").json()
    assert one["dialogue"]["rag"] is None


# ---------- POST /api/dialogues/{id}/rag ----------

def test_set_rag_true_persisted(env):
    """POST {rag: true} — 200 с обновлённым диалогом; значение пережило
    запись на диск (перечитываем dialogues.json напрямую)."""
    client, agent, kb, seen, data_dir = env
    d = _new_dialogue(client)
    r = _set_rag(client, d["id"], True)
    assert r.status_code == 200, r.text
    assert r.json()["dialogue"]["rag"] is True
    # с диска: запись на диске содержит rag=true (не только в памяти)
    disk = json.load(open(os.path.join(data_dir, "dialogues.json"),
                          encoding="utf-8"))
    rec = next(x for x in disk["dialogues"] if x["id"] == d["id"])
    assert rec["rag"] is True
    # и через API
    assert client.get(f"/api/dialogues/{d['id']}").json()["dialogue"][
        "rag"] is True


def test_set_rag_invalid_400(env):
    """400 RU-detail: rag отсутствует или не настоящий JSON-bool
    (1/0/"true"/None отклоняются — isinstance(v, bool)). Флаг не меняется."""
    client, agent, kb, seen, _ = env
    d = _new_dialogue(client)
    for bad in ({}, {"rag": 1}, {"rag": 0}, {"rag": "true"},
                {"rag": "1"}, {"rag": None}):
        r = client.post(f"/api/dialogues/{d['id']}/rag", json=bad)
        assert r.status_code == 400, (bad, r.text)
        detail = r.json()["detail"]
        assert isinstance(detail, str) and detail
    assert client.get(f"/api/dialogues/{d['id']}").json()["dialogue"][
        "rag"] is None  # ни одна попытка не записалась


def test_set_rag_unknown_id_404(env):
    """404 RU-detail — диалог не найден (паттерн дня 11)."""
    client, agent, kb, seen, _ = env
    r = _set_rag(client, "нет-такого-диалога", True)
    assert r.status_code == 404
    detail = r.json()["detail"]
    assert isinstance(detail, str) and detail


# ---------- ask_stream: эффективный режим per-диалог ----------

def test_ask_stream_dialogue_true_global_off(env):
    """Global rag=false, dialogue.rag=true → РОВНО день 21: блок
    «База знаний» в system-промпте + rag_context на assistant-сообщении."""
    client, agent, kb, seen, _ = env
    assert kb.update_settings({"rag": False})["rag"] is False
    d = _new_dialogue(client)
    assert _set_rag(client, d["id"], True).status_code == 200
    events = list(agent.ask_stream(d["id"], QUESTION))
    assert events[-1]["type"] == "done", events
    assert KB_BLOCK in seen["system"]
    last = _last_message(agent, d["id"])
    assert last.get("rag_context") is not None
    assert last["rag_context"]["chunks"]


def test_ask_stream_dialogue_false_global_on(env):
    """Global rag=true (дефолт), dialogue.rag=false → блока НЕТ,
    rag_context на assistant-сообщении НЕТ."""
    client, agent, kb, seen, _ = env
    assert kb.settings()["rag"] is True  # дефолт, не трогаем
    d = _new_dialogue(client)
    assert _set_rag(client, d["id"], False).status_code == 200
    events = list(agent.ask_stream(d["id"], QUESTION))
    assert events[-1]["type"] == "done", events
    assert KB_BLOCK not in seen["system"]
    assert "rag_context" not in _last_message(agent, d["id"])


def test_ask_stream_null_follows_global_on(env):
    """Регресс дня 21: dialogue.rag=null, global=true → блок присутствует
    (поведение дня 21 без персистентного флага не изменилось)."""
    client, agent, kb, seen, _ = env
    d = _new_dialogue(client)
    assert d["rag"] is None  # флаг НЕ устанавливали
    events = list(agent.ask_stream(d["id"], QUESTION))
    assert events[-1]["type"] == "done", events
    assert KB_BLOCK in seen["system"]
    assert _last_message(agent, d["id"]).get("rag_context") is not None


def test_no_kb_no_index_chat_works(tmp_path):
    """БЗ без индекса: чат жив (done + ответ), блока нет, rag_context
    нет, падения нет (день 21: build_kb_block тихо возвращает пусто)."""
    kb = _make_kb(tmp_path, build=False)
    seen = {}
    agent = _make_agent(tmp_path, kb, seen)
    d = agent.store.new_dialogue()
    events = list(agent.ask_stream(d["id"], QUESTION))
    assert events[-1]["type"] == "done", events
    assert events[-1]["answer"] == "Привет"
    assert KB_BLOCK not in seen["system"]
    assert "rag_context" not in _last_message(agent, d["id"])
