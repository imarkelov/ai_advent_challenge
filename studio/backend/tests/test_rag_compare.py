"""Тесты дня 22 (T4) + дня 23 (задача 5): POST /api/rag/compare —
ответ на вопрос 4 способами (plain / rag / rag+filter / rag+rewrite)
в одном вызове.

Офлайн: tmp-каталог БЗ (egg_book.txt + HashEmbedder, uploads-only),
tmp-данные агента, httpx.MockTransport fake-LLM, который ЗАХВАТЫВАЕТ
каждый non-stream payload (проверки системного промпта и параметров
детерминированности). Реальные data/kb и GPustack не используются.

Контракт ответа (фронтенд зафиксирован, commit a73ddcc) —
аддитивно расширен в день 23: старые поля {answer_plain, answer_rag,
kb_block, chunks, rag_context} НЕ ТРОГАТЬ (e2e_day22 — регрессия);
добавлены answer_rag_filter / answer_rag_rewrite / chunks_rag_filter /
chunks_rag_rewrite / rag_context_rag_filter / rag_context_rag_rewrite /
rewritten_query / rewrite_applied. chunks — та же форма, что у
GET /api/kb/search (chunk_id/source/file/section/score/text,
+rerank_score/stage1_rank при реранке).

Порядок non-stream LLM-вызовов (контракт захвата): plain → rag →
rag+filter → rewrite → rag+rewrite (rewrite-вызов — T=0/max_tokens=200;
руки — T=0/max_tokens=1024).

Форма ошибки руки (LLM-сбой): НЕ отдельное поле, а НЕПУСТОЕ
человекочитаемое строка-ошибка в самом ответе руки — фронтенд
рендерит ответ как текст.
"""
import json
import os

import httpx
import pytest
from fastapi.testclient import TestClient

from agent import REWRITE_QUERY_PROMPT, StudioAgent
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
    assert len(captured) == 5  # день 23: 4 руки + rewrite-вызов
    # порядок: plain (без блока) → rag (с блоком) → …
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
    assert len(captured) == 5
    model = agent.get_config()["model"]
    # 4 руки (plain/rag/filter/rewrite-arm) — T=0, max_tokens=1024
    for p in captured[:3] + captured[4:]:
        assert p["temperature"] == 0
        assert p["max_tokens"] == 1024
        assert p["model"] == model
    # rewrite-вызов (задача 3) — T=0, max_tokens=200
    assert captured[3]["temperature"] == 0
    assert captured[3]["max_tokens"] == 200
    assert captured[3]["model"] == model


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
    assert len(captured) == 5  # все 5 вызовов пытались


# ---------- день 23: 4 армы (plain / rag / rag+filter / rag+rewrite) ----------

MULTI = ("# Раздел А\n\nТекст про яблоко и банан. Яблоко зелёное.\n"
         "# Раздел Б\n\nТекст про грушу и абрикос. Груша сладкая.\n"
         "# Раздел В\n\nТекст про мандарин и лимон. Лимон кислый.\n")
Q_APPLE = "что про яблоко"
Q_PEAR = "что про грушу"


def _make_multi_kb(tmp_path) -> KnowledgeBase:
    """Tmp-БЗ: 3-чанковый корпус (multi.md — 3 markdown-секции)."""
    kb = KnowledgeBase(str(tmp_path / "kb"), str(tmp_path))
    up = os.path.join(kb.kb_dir, "uploads")
    os.makedirs(up, exist_ok=True)
    with open(os.path.join(up, "multi.md"), "w", encoding="utf-8") as f:
        f.write(MULTI)
    return kb


def _scored_reranker(monkeypatch, queries_seen: list | None = None):
    """Fake APIReranker (паттерн test_kb_api): позиционные scores
    [0.99, 0.5, 0.1, 0.05]; опционально — захват запросов."""
    import kb as kb_module

    class ScoredReranker:
        def __init__(self, base_url, api_key, client=None):
            pass

        def rerank(self, query, texts):
            if queries_seen is not None:
                queries_seen.append(query)
            return [0.99, 0.5, 0.1, 0.05][:len(texts)]

    monkeypatch.setenv("GPUSTACK_KEY_RERANK", "fake-key")
    monkeypatch.setattr(kb_module, "APIReranker", ScoredReranker)


def _make_agent_with(tmp_path, kb: KnowledgeBase,
                     handler) -> StudioAgent:
    d = tmp_path / "data"
    d.mkdir(exist_ok=True)
    return StudioAgent(str(d), base_url=BASE, api_key="test-key",
                       client=httpx.Client(
                           transport=httpx.MockTransport(handler)),
                       kb=kb)


def test_compare_4_arms_shape(built):
    """4 армы в ответе + все старые (day22) поля present."""
    client, agent, kb, _ = built
    r = _compare(client)
    assert r.status_code == 200, r.text
    body = r.json()
    old = ("answer_plain", "answer_rag", "kb_block", "chunks",
           "rag_context")
    new = ("answer_rag_filter", "answer_rag_rewrite",
           "chunks_rag_filter", "chunks_rag_rewrite",
           "rag_context_rag_filter", "rag_context_rag_rewrite",
           "rewritten_query", "rewrite_applied")
    for k in old + new:
        assert k in body, k
    for k in ("answer_rag_filter", "answer_rag_rewrite"):
        assert isinstance(body[k], str) and body[k].strip()
    assert isinstance(body["chunks_rag_filter"], list)
    assert isinstance(body["chunks_rag_rewrite"], list)
    assert isinstance(body["rewritten_query"], str)
    assert isinstance(body["rewrite_applied"], bool)


def test_compare_llm_call_order_day23(built):
    """Порядок non-stream LLM-вызовов: plain → rag → filter →
    rewrite → rewrite-arm; rewrite-вызов — T=0/max_tokens=200, руки —
    1024; rewrite-arm отвечает на ОРИГИНАЛЬНЫЙ вопрос."""
    client, agent, kb, captured = built
    r = _compare(client)
    assert r.status_code == 200
    assert len(captured) == 5
    # rewrite-вызов: голый REWRITE_QUERY_PROMPT, user = оригинал
    assert captured[3]["messages"][0]["content"] == REWRITE_QUERY_PROMPT
    assert captured[3]["messages"][1]["content"] == QUESTION
    # rewrite-arm: голый промпт (+блок), user = ОРИГИНАЛЬНЫЙ вопрос
    assert captured[4]["messages"][1]["content"] == QUESTION
    assert captured[4]["max_tokens"] == 1024
    assert captured[3]["max_tokens"] == 200


def test_compare_day22_contract_fixed_payload(built):
    """e2e_day22-контракт: старые 5 полей при фиксированном fake-payload
    — byte-идентичны форме дня 22 (аддитивное расширение не трогает
    старые поля)."""
    client, agent, kb, _ = built
    r = _compare(client)
    assert r.status_code == 200
    b = r.json()
    snap = {k: b[k] for k in ("answer_plain", "answer_rag", "kb_block",
                              "chunks", "rag_context")}
    # фиксированный fake-LLM: детерминированные ответы
    assert snap["answer_plain"] == "Детерминированный ответ."
    assert snap["answer_rag"] == "Детерминированный ответ."
    assert snap["kb_block"].startswith("\n\nБаза знаний")
    assert len(snap["chunks"]) == 1
    c0 = snap["chunks"][0]
    for key in ("chunk_id", "source", "file", "section", "score", "text"):
        assert key in c0
    assert c0["file"] == "uploads/egg_book.txt"
    assert set(snap["rag_context"]) == {"recall_total", "reranked",
                                        "chunks"}
    assert snap["rag_context"]["reranked"] is False
    assert len(snap["rag_context"]["chunks"]) == 1


def test_compare_body_min_score_filters_only_filter_arm(tmp_path,
                                                        monkeypatch):
    """Body min_score=0.8: rag+filter — 1 чанк (0.99); plain/rag НЕ
    фильтруются — chunks идентичны run БЕЗ body-override."""
    _scored_reranker(monkeypatch)
    kb = _make_multi_kb(tmp_path)
    kb.build("structural", HashEmbedder())
    captured = []
    agent = _make_agent(tmp_path, kb, captured)
    client = _client(agent, kb)
    kb.update_settings({"reranker": "api", "rag_recall": 5})
    base = client.post("/api/rag/compare",
                       json={"question": Q_APPLE}).json()  # без override
    assert len(base["chunks"]) == 3
    r = client.post("/api/rag/compare",
                    json={"question": Q_APPLE, "min_score": 0.8})
    assert r.status_code == 200, r.text
    b = r.json()
    assert len(b["chunks_rag_filter"]) == 1
    assert b["chunks_rag_filter"][0]["rerank_score"] == 0.99
    # plain/rag армы не фильтруются
    assert len(b["chunks"]) == 3
    # идентичны run без body-override
    assert [c["chunk_id"] for c in b["chunks"]] == \
        [c["chunk_id"] for c in base["chunks"]]
    # filter-арма тоже ответила (200, не «Ошибка:»)
    assert b["answer_rag_filter"].strip()
    assert not b["answer_rag_filter"].startswith("Ошибка:")


def test_compare_body_min_score_invalid_400(built):
    """min_score в body: bool ПЕРВЫЙ (True — ловушка isinstance),
    строка, вне диапазона — 400 RU; LLM не вызывается."""
    client, agent, kb, captured = built
    for bad in (True, False, "0.5", 1.5, -0.1):
        r = client.post("/api/rag/compare",
                        json={"question": QUESTION, "min_score": bad})
        assert r.status_code == 400, bad
        assert r.json()["detail"] == "min_score должен быть числом от 0 до 1"
    assert captured == []  # LLM не вызывался


def test_compare_min_score_06_filter_counts(tmp_path, monkeypatch):
    """fake-reranker [0.99, 0.5, 0.1] + min_score=0.6:
    chunks_rag_filter len==1, chunks (rag-арма) len==3."""
    _scored_reranker(monkeypatch)
    kb = _make_multi_kb(tmp_path)
    kb.build("structural", HashEmbedder())
    captured = []
    agent = _make_agent(tmp_path, kb, captured)
    client = _client(agent, kb)
    kb.update_settings({"reranker": "api", "rag_recall": 5})
    r = client.post("/api/rag/compare",
                    json={"question": Q_APPLE, "min_score": 0.6})
    assert r.status_code == 200, r.text
    b = r.json()
    assert len(b["chunks_rag_filter"]) == 1
    assert len(b["chunks"]) == 3


def test_compare_rewrite_scripted(tmp_path, monkeypatch):
    """fake-LLM rewrite-scripted: rewritten_query == scripted,
    rewrite_applied=True, retrieval — на перефразе (spy-reranker видит
    scripted query), а ANSWER-LLM — на оригинальном вопросе."""
    queries_seen: list = []
    _scored_reranker(monkeypatch, queries_seen=queries_seen)
    REWRITTEN = "что про грушу"
    kb = _make_multi_kb(tmp_path)
    kb.build("structural", HashEmbedder())
    captured = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if "stream" in payload:
            return httpx.Response(200, content=sse_body(
                [delta_chunk("Ок"), usage_chunk(), "[DONE]"]
            ).encode("utf-8"))
        system = (payload.get("messages") or [{}])[0].get("content", "")
        captured.append(payload)
        if system == REWRITE_QUERY_PROMPT:
            return httpx.Response(200, json={"choices": [
                {"message": {"content": REWRITTEN}}]})
        return httpx.Response(200, json={"choices": [
            {"message": {"content": "Детерминированный ответ."}}]})

    agent = _make_agent_with(tmp_path, kb, handler)
    client = _client(agent, kb)
    kb.update_settings({"reranker": "api", "rag_recall": 5})
    r = client.post("/api/rag/compare", json={"question": Q_APPLE})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["rewrite_applied"] is True
    assert b["rewritten_query"] == REWRITTEN
    # retrieval на перефразе: rag/filter — оригинал, rewrite-арма —
    # scripted query
    assert queries_seen == [Q_APPLE, Q_APPLE, REWRITTEN]
    # top-k по перефразу отличается от top-k по оригиналу
    assert [c["chunk_id"] for c in b["chunks_rag_rewrite"]] != \
        [c["chunk_id"] for c in b["chunks"]]
    # ANSWER-LLM отвечает на оригинальный вопрос
    assert captured[4]["messages"][1]["content"] == Q_APPLE
    assert b["answer_rag_rewrite"].strip()
    assert not b["answer_rag_rewrite"].startswith("Ошибка:")


def test_compare_rewrite_raise_graceful(tmp_path):
    """Rewrite-LLM упал: rewrite_applied=False, rewritten_query ==
    оригинал, rag+rewrite-арма всё равно отвечает (НЕ «Ошибка:»),
    retrieval — как у rag-руки."""
    kb = _make_kb(tmp_path)
    kb.build("structural", HashEmbedder())
    captured = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if "stream" in payload:
            return httpx.Response(200, content=sse_body(
                [delta_chunk("Ок"), usage_chunk(), "[DONE]"]
            ).encode("utf-8"))
        system = (payload.get("messages") or [{}])[0].get("content", "")
        captured.append(payload)
        if system == REWRITE_QUERY_PROMPT:
            raise httpx.ConnectError("rewrite LLM down")
        return httpx.Response(200, json={"choices": [
            {"message": {"content": "Детерминированный ответ."}}]})

    agent = _make_agent_with(tmp_path, kb, handler)
    client = _client(agent, kb)
    r = _compare(client)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["rewrite_applied"] is False
    assert b["rewritten_query"] == QUESTION
    assert b["answer_rag_rewrite"].strip()
    assert not b["answer_rag_rewrite"].startswith("Ошибка:")
    # фолбэк-реtrieval: как у rag-руки
    assert [c["chunk_id"] for c in b["chunks_rag_rewrite"]] == \
        [c["chunk_id"] for c in b["chunks"]]
    assert len(captured) == 5  # все руки дозвонились


def test_compare_all_filtered_graceful(tmp_path, monkeypatch):
    """min_score=0.999 (fake max=0.99): 200, chunks_rag_filter == [],
    answer_rag_filter — непустая строка, НЕ «Ошибка:» (ответ без
    контекста)."""
    _scored_reranker(monkeypatch)
    kb = _make_multi_kb(tmp_path)
    kb.build("structural", HashEmbedder())
    captured = []
    agent = _make_agent(tmp_path, kb, captured)
    client = _client(agent, kb)
    kb.update_settings({"reranker": "api", "rag_recall": 5})
    r = client.post("/api/rag/compare",
                    json={"question": Q_APPLE, "min_score": 0.999})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["chunks_rag_filter"] == []
    assert b["rag_context_rag_filter"] is None
    assert isinstance(b["answer_rag_filter"], str)
    assert b["answer_rag_filter"].strip()
    assert not b["answer_rag_filter"].startswith("Ошибка:")
    # другие армы не пострадали
    assert len(b["chunks"]) == 3
    assert b["answer_rag"].strip()
