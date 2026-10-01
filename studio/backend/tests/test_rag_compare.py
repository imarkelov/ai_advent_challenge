"""Тесты дня 22 (T4): POST /api/rag/compare — ответ на вопрос двумя
способами (без RAG и с RAG) в одном вызове.

Офлайн: tmp-каталог БЗ (egg_book.txt + HashEmbedder, uploads-only),
tmp-данные агента, httpx.MockTransport fake-LLM, который ЗАХВАТЫВАЕТ
каждый non-stream payload (проверки системного промпта и параметров
детерминированности). Реальные data/kb и GPustack не используются.

Контракт ответа (фронтенд зафиксирован, commit a73ddcc):
{answer_plain, answer_rag, kb_block, chunks, rag_context}; chunks — та
же форма, что у GET /api/kb/search (chunk_id/source/file/section/score/
text, +rerank_score/stage1_rank при реранке).

Форма ошибки руки (LLM-сбой одной из двух рук): НЕ отдельное поле, а
НЕПУСТОЕ человекочитаемое строка-ошибка в самом ответе руки
(answer_plain/answer_rag) — фронтенд рендерит ответ как текст.
"""
import json
import os

import httpx
import pytest
from fastapi.testclient import TestClient

from agent import StudioAgent
from conftest import delta_chunk, sse_body, usage_chunk
from kb import HashEmbedder, KnowledgeBase

BASE = "https://mock.local/v1"
EGG = os.path.join(os.path.dirname(__file__), "fixtures", "egg_book.txt")
QUESTION = "Какой телефон был у героя?"


def _client(agent, kb) -> TestClient:
    """Ленивый импорт main (паттерн test_kb_api: main.py в момент импорта
    load_dotenv'ит .env в os.environ — при модульном импорте это загрязнило
    бы окружение test_agent.py, идущего первым в алфавитном порядке)."""
    from main import create_app
    return TestClient(create_app(agent, kb))


def _llm_handler(captured: list, fail_rag: bool = False):
    """Fake-LLM: non-stream — 200 с детерминированным ответом, каждый
    non-stream payload сохраняется в captured (порядок = порядок вызовов:
    plain → rag). При fail_rag — 500 ТОЛЬКО если в system-промпте есть
    блок «База знаний» (rag-рука падает, plain-рука цела). Stream-ответы
    (остальные эндпоинты) — пара дельт + usage, как в test_kb_api."""
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if "stream" in payload:
            return httpx.Response(
                200, content=sse_body([delta_chunk("Ок"), usage_chunk(),
                                       "[DONE]"]).encode("utf-8"))
        system = (payload.get("messages") or [{}])[0].get("content", "")
        captured.append(payload)
        if fail_rag and "База знаний" in system:
            return httpx.Response(500, json={"error": "LLM down"})
        return httpx.Response(200, json={"choices": [
            {"message": {"content": "Детерминированный ответ."}}]})
    return handler


def _make_kb(tmp_path) -> KnowledgeBase:
    """Tmp-БЗ с загруженной egg_book.txt (корпус uploads-only, день 21)."""
    kb = KnowledgeBase(str(tmp_path / "kb"), str(tmp_path))
    up = os.path.join(kb.kb_dir, "uploads")
    os.makedirs(up, exist_ok=True)
    with open(EGG, encoding="utf-8") as f:
        text = f.read()
    with open(os.path.join(up, "egg_book.txt"), "w", encoding="utf-8") as g:
        g.write(text)
    return kb


def _make_agent(tmp_path, kb: KnowledgeBase, captured: list,
                fail_rag: bool = False) -> StudioAgent:
    d = tmp_path / "data"
    d.mkdir(exist_ok=True)
    return StudioAgent(
        str(d), base_url=BASE, api_key="test-key",
        client=httpx.Client(transport=httpx.MockTransport(
            _llm_handler(captured, fail_rag=fail_rag))),
        kb=kb)


@pytest.fixture
def built(tmp_path):
    """(client, agent, kb, captured): egg_book загружен, индекс собран
    (structural + HashEmbedder)."""
    kb = _make_kb(tmp_path)
    kb.build("structural", HashEmbedder())
    captured = []
    agent = _make_agent(tmp_path, kb, captured)
    return _client(agent, kb), agent, kb, captured


def _compare(client, question=QUESTION):
    return client.post("/api/rag/compare", json={"question": question})


# ---------- контракт ответа ----------

def test_compare_shape(built):
    client, agent, kb, _ = built
    r = _compare(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body["answer_plain"], str)
    assert body["answer_plain"].strip()
    assert isinstance(body["answer_rag"], str)
    assert body["answer_rag"].strip()
    assert isinstance(body["kb_block"], str)
    assert body["kb_block"].strip()
    assert isinstance(body["chunks"], list)
    assert len(body["chunks"]) >= 1
    assert isinstance(body["rag_context"], dict)
    # чанки — та же форма, что у GET /api/kb/search
    c0 = body["chunks"][0]
    for key in ("chunk_id", "source", "file", "section", "score", "text"):
        assert key in c0
    # корпус uploads-only (день 21): файл в выдаче — «uploads/<имя>»
    assert c0["file"] == "uploads/egg_book.txt"


# ---------- системные промпты двух рук ----------

def test_plain_prompt_has_no_kb_block(built):
    client, agent, kb, captured = built
    r = _compare(client)
    assert r.status_code == 200
    assert len(captured) == 2  # ровно два LLM-вызова
    # порядок: plain (без блока) → rag (с блоком)
    plain = captured[0]["messages"][0]["content"]
    assert "База знаний" not in plain
    assert captured[0]["messages"][1]["content"] == QUESTION


def test_rag_prompt_has_kb_block(built):
    client, agent, kb, captured = built
    r = _compare(client)
    assert r.status_code == 200
    rag = captured[1]["messages"][0]["content"]
    assert "База знаний" in rag
    # выдержка из egg_book дошла до промпта: окно выдержки (день 21)
    # центрируется на токене запроса «телефон» (первое вхождение — в
    # заглавии «Глава вторая. Инструмент и телефон»), поэтому в окно
    # попадает факт про ксилофон из палубного дуба (уникален для
    # egg_book; IPhone-строка за окном 300 символов)
    assert "палубного дуба" in rag
    assert "1987" in rag


def test_both_calls_deterministic_params(built):
    client, agent, kb, captured = built
    r = _compare(client)
    assert r.status_code == 200
    assert len(captured) == 2
    model = agent.get_config()["model"]
    for p in captured:
        assert p["temperature"] == 0
        assert p["max_tokens"] == 1024
        assert p["model"] == model


def test_system_prompt_is_bare(built):
    """Обе руки — голый config.system_prompt (rag-рука + kb_block);
    маркеров профиля/памяти/инвариантов НЕТ (сравнение — не чат)."""
    client, agent, kb, captured = built
    r = _compare(client)
    assert r.status_code == 200
    base = agent.get_config()["system_prompt"]
    kb_block = r.json()["kb_block"]
    assert captured[0]["messages"][0]["content"] == base
    assert captured[1]["messages"][0]["content"] == base + kb_block
    for marker in ("Профиль", "Память", "Инварианты"):
        for p in captured:
            assert marker not in p["messages"][0]["content"]


# ---------- настройки и ошибки ----------

def test_ignores_settings_rag(built):
    """Явное сравнение: тумблер settings['rag']=off НЕ отключает ни одну
    руку — ОБА ответа приходят."""
    client, agent, kb, captured = built
    s = kb.update_settings({"rag": False})
    assert s["rag"] is False
    r = _compare(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["answer_plain"].strip()
    assert body["answer_rag"].strip()
    assert "База знаний" in captured[1]["messages"][0]["content"]


def test_empty_question_400(built):
    client, agent, kb, captured = built
    for bad in ("", "   ", None, 123):
        r = client.post("/api/rag/compare", json={"question": bad})
        assert r.status_code == 400, r.text
        assert isinstance(r.json()["detail"], str)
        assert r.json()["detail"]
    r = client.post("/api/rag/compare", json={})
    assert r.status_code == 400
    assert r.json()["detail"]
    assert captured == []  # LLM не вызывался


def test_no_index_404(tmp_path):
    kb = _make_kb(tmp_path)  # загрузка есть, индекс НЕ собран
    captured = []
    agent = _make_agent(tmp_path, kb, captured)
    client = _client(agent, kb)
    r = _compare(client)
    assert r.status_code == 404
    assert "Индекс не построен" in r.json()["detail"]
    assert captured == []  # LLM не вызывался


def test_rag_llm_error_isolated(tmp_path):
    """Сбой LLM на rag-руке не роняет plain-руку: 200, plain цела,
    rag-ответ — НЕПУСТОЕ строка-ошибка (фронтенд рендерит текст)."""
    kb = _make_kb(tmp_path)
    kb.build("structural", HashEmbedder())
    captured = []
    agent = _make_agent(tmp_path, kb, captured, fail_rag=True)
    client = _client(agent, kb)
    r = _compare(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["answer_plain"].strip()  # plain цела
    assert isinstance(body["answer_rag"], str)
    assert body["answer_rag"].strip()  # не пусто — текст ошибки
    assert "Ошибка" in body["answer_rag"]
    assert len(captured) == 2  # обе руки пытались
