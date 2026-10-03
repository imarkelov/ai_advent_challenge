"""Тесты дня 21: роуты /api/kb/* и RAG/toggle agent-loop ве.

Офлайн: tmp-корпус (mini-repo), tmp-каталог БЗ, httpx.MockTransport для
LLM, fake stdio-процесс MCP. Реальный data/kb в тестах не трогается.
"""
import json
import os

import httpx
import pytest
from fastapi.testclient import TestClient

from agent import StudioAgent
from conftest import delta_chunk, sse_body, usage_chunk
from kb import HashEmbedder, KnowledgeBase
from mcp import MCPRegistry
from memory import MemoryStore
from tests.test_mcp import make_fake_launcher

BASE = "https://mock.local/v1"


def _llm_handler():
    """Fake-LLM: non-stream (авто-название) — JSON; stream — 2 дельты + usage."""
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if "stream" not in payload:
            return httpx.Response(200, json={"choices": [
                {"message": {"content": "Название"}}]})
        body = sse_body([delta_chunk("Прив"), delta_chunk("ет"),
                         usage_chunk(), "[DONE]"])
        return httpx.Response(200, content=body.encode("utf-8"))
    return handler


def _mini_repo(tmp_path) -> str:
    """Мини-репозиторий: README + 2 md-дока. Доки репозитория в корпус
    НЕ входят (uploads-only, исправление после релиза дня 21) — остаются
    как «отвлекающий» репо."""
    repo = tmp_path / "repo"
    (repo / "docs" / "superpowers").mkdir(parents=True)
    (repo / "README.md").write_text(
        "Проект Студии. Корпус знаний.\n", encoding="utf-8")
    (repo / "docs" / "superpowers" / "music.md").write_text(
        "# Инструменты\n\nКсилофон — ударный музыкальный инструмент.\n\n"
        "## Ксилофон\nЗвук ксилофона возникает при ударе по пластинам.\n",
        encoding="utf-8")
    (repo / "docs" / "superpowers" / "weather.md").write_text(
        "# Погода\n\nЗимой бывает холодно.\n", encoding="utf-8")
    return str(repo)


def _upload_music(kb: KnowledgeBase) -> None:
    """Факт про ксилофон — в загрузке (корпус uploads-only: единственный
    источник корпуса — data/kb/uploads, «+ Добавить файл»)."""
    with open(os.path.join(kb.kb_dir, "uploads", "music.md"), "w",
              encoding="utf-8") as f:
        f.write("# Инструменты\n\nКсилофон — ударный музыкальный "
                "инструмент.\n\n"
                "## Ксилофон\nЗвук ксилофона возникает при ударе "
                "по пластинам.\n")


def _upload_weather(kb: KnowledgeBase) -> None:
    """Вторая загрузка (в тесте, где нужен запас чанков ≥ 3)."""
    with open(os.path.join(kb.kb_dir, "uploads", "weather.txt"), "w",
              encoding="utf-8") as f:
        f.write("# Погода\n\nЗимой бывает холодно.\n")


@pytest.fixture
def kb_env(tmp_path):
    """(agent, kb): agent — MockTransport, kb — tmp-каталог (корпус —
    только загрузки в kb_dir/uploads; доки _mini_repo в корпус не
    входят)."""
    kb = KnowledgeBase(str(tmp_path / "kb"), _mini_repo(tmp_path))
    d = tmp_path / "data"
    d.mkdir()
    agent = StudioAgent(str(d), base_url=BASE, api_key="test-key",
                        client=httpx.Client(transport=httpx.MockTransport(
                            _llm_handler())),
                        kb=kb)
    return agent, kb


@pytest.fixture
def client(kb_env):
    from main import create_app
    agent, kb = kb_env
    return TestClient(create_app(agent, kb))


# ---------- /api/kb/stats ----------

def test_kb_stats_404_without_index(client):
    r = client.get("/api/kb/stats")
    assert r.status_code == 404
    assert r.json()["detail"] == "Индекс не построен"


def test_kb_index_build_hash(tmp_path, kb_env, client):
    agent, kb = kb_env
    _upload_music(kb)
    r = client.post("/api/kb/index",
                    json={"strategy": "structural", "embedder": "hash"})
    assert r.status_code == 200
    body = r.json()
    assert body["strategy"] == "structural"
    assert body["stats"]["chunks"] > 0
    assert body["stats"]["docs"] == 1  # только загрузка music.md
    assert "fixed" in body["comparison"] and "structural" in body["comparison"]
    # index.db на диске (SQLite)
    assert os.path.exists(os.path.join(kb.kb_dir, "index.db"))
    idx = kb.load_index()
    assert idx is not None
    assert idx["strategy"] == "structural"
    assert idx["embedder"] == "hash"
    assert idx["dim"] == 256


def test_kb_stats_after_build(kb_env, client):
    agent, kb = kb_env
    _upload_music(kb)
    assert client.post("/api/kb/index",
                       json={"strategy": "fixed", "embedder": "hash"}
                       ).status_code == 200
    r = client.get("/api/kb/stats")
    assert r.status_code == 200
    b = r.json()
    assert b["exists"] is True
    assert b["strategy"] == "fixed"
    assert b["embedder"] == "hash"
    assert "built_at" in b
    assert "docs" in b["stats"]
    assert "hit_at_3" in b["comparison"]["fixed"]
    # файлы индекса — только загрузки (доки репозитория не входят)
    assert b["files"] == ["uploads/music.md"]


def test_kb_stats_reranker_key_flag(kb_env, client, monkeypatch):
    """stats.reranker_key_configured — от env GPUSTACK_KEY_RERANK."""
    agent, kb = kb_env
    client.post("/api/kb/index",
                json={"strategy": "fixed", "embedder": "hash"})
    monkeypatch.delenv("GPUSTACK_KEY_RERANK", raising=False)
    assert client.get("/api/kb/stats").json()[
        "reranker_key_configured"] is False
    monkeypatch.setenv("GPUSTACK_KEY_RERANK", "k")
    assert client.get("/api/kb/stats").json()[
        "reranker_key_configured"] is True


def test_kb_stats_lists_uploads_before_reindex(kb_env, client):
    """Загрузка видна в stats.uploads сразу после upload (до пересборки
    индекса), в files — только после «Индексировать»."""
    assert client.post("/api/kb/index",
                       json={"strategy": "fixed", "embedder": "hash"}
                       ).status_code == 200
    assert client.get("/api/kb/stats").json()["uploads"] == []
    r = client.post("/api/kb/upload",
                    files={"file": ("note.txt", b"hello kb", "text/plain")})
    assert r.status_code == 200
    b = client.get("/api/kb/stats").json()
    assert b["uploads"] == [{"name": "note.txt", "size": 8}]
    # файл в загрузках, но ещё не в индексе
    assert "uploads/note.txt" not in b["files"]
    # пересборка — файл попадает в индекс
    assert client.post("/api/kb/index",
                       json={"strategy": "fixed", "embedder": "hash"}
                       ).status_code == 200
    b2 = client.get("/api/kb/stats").json()
    assert "uploads/note.txt" in b2["files"]
    # после пересборки pending пуст
    assert b2["pending_files"] == []


def test_kb_stats_pending_files_before_reindex(kb_env, client):
    """Корпус без индекса в индексе → pending_files; после — пусто."""
    client.post("/api/kb/index",
                json={"strategy": "fixed", "embedder": "hash"})
    client.post("/api/kb/upload",
                files={"file": ("note.txt", b"hello kb", "text/plain")})
    b = client.get("/api/kb/stats").json()
    assert b["pending_files"] == ["uploads/note.txt"]
    client.post("/api/kb/index",
                json={"strategy": "fixed", "embedder": "hash"})
    assert client.get("/api/kb/stats").json()["pending_files"] == []


# ---------- /api/kb/uploads ----------

def test_kb_uploads_empty_without_index(client):
    """GET /api/kb/uploads — 200 с пустым списком, даже без индекса
    (stats при этом 404 — контракт не меняется)."""
    r = client.get("/api/kb/uploads")
    assert r.status_code == 200
    assert r.json() == {"uploads": []}
    assert client.get("/api/kb/stats").status_code == 404


def test_kb_uploads_lists_file_after_upload_without_index(kb_env, client):
    """Суть фикса: загруженный файл виден в /api/kb/uploads БЕЗ сборки
    индекса; stats остаётся 404 (индекс не построен)."""
    r = client.post("/api/kb/upload",
                    files={"file": ("note.txt", b"hello kb", "text/plain")})
    assert r.status_code == 200
    b = client.get("/api/kb/uploads").json()
    assert b == {"uploads": [{"name": "note.txt", "size": 8}]}
    # контракт stats не изменился: без index.db — 404
    assert client.get("/api/kb/stats").status_code == 404


def test_kb_uploads_after_wipe_empty(kb_env, client):
    """После wipe список загрузок пуст (200, не 404)."""
    agent, kb = kb_env
    client.post("/api/kb/upload",
                files={"file": ("note.txt", b"hello kb", "text/plain")})
    assert client.delete("/api/kb").status_code == 200
    r = client.get("/api/kb/uploads")
    assert r.status_code == 200
    assert r.json() == {"uploads": []}


# ---------- /api/kb/index: mode (полная / инкрементальная) ----------

def test_kb_build_incremental_adds_only_new_files(kb_env, client):
    """mode auto при совпадении strategy+embedder: индексировать ТОЛЬКО
    новые файлы (merge), mode=incremental, added=число новых."""
    r1 = client.post("/api/kb/index",
                     json={"strategy": "fixed", "embedder": "hash"})
    chunks1 = r1.json()["stats"]["chunks"]
    assert r1.json()["mode"] == "full"

    # новый файл → инкрементальная сборка
    client.post("/api/kb/upload",
                files={"file": ("note.txt", b"hello kb world", "text/plain")})
    r2 = client.post("/api/kb/index",
                     json={"strategy": "fixed", "embedder": "hash"})
    assert r2.status_code == 200
    b2 = r2.json()
    assert b2["mode"] == "incremental"
    assert b2["added"] == 1
    assert b2["stats"]["chunks"] > chunks1
    b = client.get("/api/kb/stats").json()
    assert "uploads/note.txt" in b["files"]
    # поиск по новому файлу работает
    r = client.get("/api/kb/search", params={"q": "hello kb world", "k": 3})
    assert r.status_code == 200
    assert r.json()["results"][0]["file"] == "uploads/note.txt"

    # новых файлов нет → added=0, чанки не растут
    r3 = client.post("/api/kb/index",
                     json={"strategy": "fixed", "embedder": "hash"})
    assert r3.json()["mode"] == "incremental"
    assert r3.json()["added"] == 0
    assert r3.json()["stats"]["chunks"] == b2["stats"]["chunks"]


def test_kb_build_strategy_or_embedder_change_forces_full(kb_env, client):
    """Смена strategy (или embedder) — несовпадение с индексом → full."""
    client.post("/api/kb/index",
                json={"strategy": "fixed", "embedder": "hash"})
    r = client.post("/api/kb/index",
                    json={"strategy": "structural", "embedder": "hash"})
    assert r.json()["mode"] == "full"
    r = client.post("/api/kb/index",
                    json={"strategy": "structural", "embedder": "hash",
                          "mode": "incremental"})
    # явный incremental при совпадении — инкремент (added=0)
    assert r.json()["mode"] == "incremental"
    assert r.json()["added"] == 0


def test_kb_build_bad_mode_400(client):
    r = client.post("/api/kb/index",
                    json={"strategy": "fixed", "embedder": "hash",
                          "mode": "nope"})
    assert r.status_code == 400
    assert "mode" in r.json()["detail"]


def test_kb_index_409_while_running(kb_env, client):
    agent, kb = kb_env
    client.post("/api/kb/index",
                json={"strategy": "fixed", "embedder": "hash"})
    # эмуляция идущей сборки → 409
    kb._progress = {"running": True, "phase": "embedding",
                    "done": 1, "total": 10}
    r = client.post("/api/kb/index",
                    json={"strategy": "fixed", "embedder": "hash"})
    assert r.status_code == 409
    assert "уже идёт" in r.json()["detail"]


def test_kb_build_status_route(kb_env, client):
    """GET /api/kb/build-status: вне сборки running=False, после сборки
    phase=done."""
    r = client.get("/api/kb/build-status")
    assert r.status_code == 200
    assert r.json() == {"running": False, "phase": "idle",
                        "done": 0, "total": 0}
    client.post("/api/kb/index",
                json={"strategy": "fixed", "embedder": "hash"})
    st = client.get("/api/kb/build-status").json()
    assert st["running"] is False
    assert st["phase"] == "done"
    assert st["total"] > 0


# ---------- /api/kb/search ----------

def test_kb_search_returns_relevant_chunk(kb_env, client):
    agent, kb = kb_env
    _upload_music(kb)
    client.post("/api/kb/index",
                json={"strategy": "structural", "embedder": "hash"})
    r = client.get("/api/kb/search", params={"q": "что про ксилофон", "k": 3})
    assert r.status_code == 200
    results = r.json()["results"]
    assert 1 <= len(results) <= 3
    assert results[0]["file"] == "uploads/music.md"
    assert "ксилофон" in results[0]["text"].lower()
    assert set(results[0]) >= {"chunk_id", "source", "file", "section",
                               "score", "text"}


def test_kb_search_without_index_404(client):
    r = client.get("/api/kb/search", params={"q": "ксилофон"})
    assert r.status_code == 404
    assert r.json()["detail"] == "Индекс не построен"


def test_kb_search_empty_q_400(client):
    assert client.get("/api/kb/search",
                      params={"q": "   "}).status_code == 400


def test_kb_search_two_stage_shape(kb_env, client):
    """Двухэтапный поиск (реранкер off): ответ {results, recall_total,
    reranked}; reranked=False, полей реранка нет; recall_total — кандидаты
    этапа 1 (не меньше выбранного топ-K)."""
    agent, kb = kb_env
    _upload_music(kb)
    client.post("/api/kb/index",
                json={"strategy": "structural", "embedder": "hash"})
    r = client.get("/api/kb/search", params={"q": "что про ксилофон", "k": 3})
    assert r.status_code == 200
    b = r.json()
    assert set(b) == {"results", "recall_total", "reranked"}
    assert b["reranked"] is False
    assert b["recall_total"] >= 1
    assert len(b["results"]) <= 3
    assert b["recall_total"] >= len(b["results"])
    for x in b["results"]:
        assert "rerank_score" not in x and "stage1_rank" not in x


def test_kb_search_rerank_enabled(kb_env, client, monkeypatch):
    """reranker=api + ключ + fake APIReranker: reranked=True, у результатов
    rerank_score/stage1_rank, порядок по rerank_score desc (fake инвертирует
    этап 1 — top-1 = худший по этапу 1)."""
    import kb as kb_module

    class FakeReranker:
        def __init__(self, base_url, api_key, client=None):
            pass

        def rerank(self, query, texts):
            # score растёт по позиции: последний чанк этапа 1 — лучший
            return [round((i + 1) / 100.0, 6) for i in range(len(texts))]

    monkeypatch.setenv("GPUSTACK_KEY_RERANK", "fake-key")
    monkeypatch.setattr(kb_module, "APIReranker", FakeReranker)
    agent, kb = kb_env
    _upload_music(kb)
    _upload_weather(kb)  # 3 чанка → top-3 заполняется
    client.post("/api/kb/index",
                json={"strategy": "structural", "embedder": "hash"})
    client.post("/api/kb/settings", json={"reranker": "api", "rag_recall": 5})
    r = client.get("/api/kb/search", params={"q": "что про ксилофон", "k": 3})
    assert r.status_code == 200
    b = r.json()
    assert b["reranked"] is True
    assert len(b["results"]) == 3
    for x in b["results"]:
        assert x["reranked"] is True
        assert "rerank_score" in x and "stage1_rank" in x
    scores = [x["rerank_score"] for x in b["results"]]
    assert scores == sorted(scores, reverse=True)
    # инверсия этапа 1: top-1 — последний кандидат этапа 1
    assert b["results"][0]["stage1_rank"] == b["recall_total"]


def test_kb_search_min_score_from_settings(kb_env, client, monkeypatch):
    """min_score из настроек: fake scores [0.99, 0.5, 0.1] + min_score=0.6
    → ровно 1 результат (0.99), аддитивно filtered=True, dropped=2."""
    import kb as kb_module

    class ScoredReranker:
        def __init__(self, base_url, api_key, client=None):
            pass

        def rerank(self, query, texts):
            return [0.99, 0.5, 0.1][:len(texts)]

    monkeypatch.setenv("GPUSTACK_KEY_RERANK", "fake-key")
    monkeypatch.setattr(kb_module, "APIReranker", ScoredReranker)
    agent, kb = kb_env
    _upload_music(kb)
    _upload_weather(kb)  # 3 чанка → scores [0.99, 0.5, 0.1]
    client.post("/api/kb/index",
                json={"strategy": "structural", "embedder": "hash"})
    client.post("/api/kb/settings",
                json={"reranker": "api", "rag_recall": 5, "min_score": 0.6})
    r = client.get("/api/kb/search", params={"q": "что про ксилофон", "k": 3})
    assert r.status_code == 200
    b = r.json()
    assert b["reranked"] is True
    assert b["filtered"] is True
    assert b["dropped"] == 2
    assert len(b["results"]) == 1
    assert b["results"][0]["rerank_score"] == 0.99
    # старые поля не тронуты
    assert {"results", "recall_total", "reranked"} <= set(b)


def test_kb_search_min_score_zero_no_extra_fields(kb_env, client):
    """min_score=0.0 (дефолт и явный) — ответ БЕЗ filtered/dropped."""
    agent, kb = kb_env
    _upload_music(kb)
    client.post("/api/kb/index",
                json={"strategy": "structural", "embedder": "hash"})
    r = client.get("/api/kb/search", params={"q": "что про ксилофон", "k": 3})
    assert r.status_code == 200
    assert set(r.json()) == {"results", "recall_total", "reranked"}
    client.post("/api/kb/settings", json={"min_score": 0.0})
    r = client.get("/api/kb/search", params={"q": "что про ксилофон", "k": 3})
    assert r.status_code == 200
    assert set(r.json()) == {"results", "recall_total", "reranked"}


# ---------- /api/kb/settings ----------

def test_kb_settings_defaults(client):
    r = client.get("/api/kb/settings")
    assert r.status_code == 200
    assert r.json() == {"agent_loop": True, "rag": True,
                        "reranker": "off", "rag_recall": 50, "rag_top_k": 3,
                        "min_score": 0.0,
                        "strategy": "structural", "embedder": "hash"}
    # дефолт min_score — именно 0.0
    assert r.json()["min_score"] == 0.0


def test_kb_settings_post_persists(client, kb_env):
    agent, kb = kb_env
    r = client.post("/api/kb/settings", json={"rag_top_k": 7, "rag": False})
    assert r.status_code == 200
    assert r.json()["rag_top_k"] == 7
    assert r.json()["rag"] is False
    # пережил перезапись: GET читает settings.json
    got = client.get("/api/kb/settings").json()
    assert got["rag_top_k"] == 7 and got["rag"] is False


def test_kb_settings_invalid_400(client, kb_env):
    agent, kb = kb_env
    r = client.post("/api/kb/settings", json={"rag_top_k": 11})
    assert r.status_code == 400
    assert r.json()["detail"]
    # значение не изменилось
    assert client.get("/api/kb/settings").json()["rag_top_k"] == 3


def test_kb_settings_reranker_recall_invalid_400(client, kb_env):
    agent, kb = kb_env
    # reranker — только off|api
    r = client.post("/api/kb/settings", json={"reranker": "nope"})
    assert r.status_code == 400
    assert "reranker" in r.json()["detail"]
    # rag_recall — целое 1..200
    for bad in (0, 201, "50", True):
        r = client.post("/api/kb/settings", json={"rag_recall": bad})
        assert r.status_code == 400, bad
    assert "rag_recall" in r.json()["detail"]
    # значения не изменились
    s = client.get("/api/kb/settings").json()
    assert s["reranker"] == "off" and s["rag_recall"] == 50


def test_kb_settings_reranker_recall_valid(client, kb_env):
    agent, kb = kb_env
    r = client.post("/api/kb/settings", json={"reranker": "api",
                                              "rag_recall": 120})
    assert r.status_code == 200
    assert r.json()["reranker"] == "api"
    assert r.json()["rag_recall"] == 120
    # пережил перезапись
    got = client.get("/api/kb/settings").json()
    assert got["reranker"] == "api" and got["rag_recall"] == 120


def test_kb_settings_min_score_valid(client, kb_env):
    agent, kb = kb_env
    r = client.post("/api/kb/settings", json={"min_score": 0.5})
    assert r.status_code == 200
    assert r.json()["min_score"] == 0.5
    assert client.get("/api/kb/settings").json()["min_score"] == 0.5
    # границы диапазона: 0 и 1 — валидны
    assert client.post("/api/kb/settings",
                       json={"min_score": 0}).status_code == 200
    assert client.get("/api/kb/settings").json()["min_score"] == 0.0
    assert client.post("/api/kb/settings",
                       json={"min_score": 1}).status_code == 200
    assert client.get("/api/kb/settings").json()["min_score"] == 1.0


def test_kb_settings_min_score_invalid_400(client, kb_env):
    agent, kb = kb_env
    # bool (ловушка isinstance(True, int)), строка, вне диапазона — 400 RU
    for bad in (True, False, "0.5", 1.5, -0.1):
        r = client.post("/api/kb/settings", json={"min_score": bad})
        assert r.status_code == 400, bad
        assert "min_score" in r.json()["detail"]
    # значение не изменилось
    assert client.get("/api/kb/settings").json()["min_score"] == 0.0


# ---------- /api/kb/index: валидация ----------

def test_kb_index_bad_strategy_400(client):
    r = client.post("/api/kb/index",
                    json={"strategy": "nope", "embedder": "hash"})
    assert r.status_code == 400
    assert r.json()["detail"]
    # индекс не появился
    assert client.get("/api/kb/stats").status_code == 404


def test_kb_index_bad_embedder_400(client):
    r = client.post("/api/kb/index",
                    json={"strategy": "fixed", "embedder": "nope"})
    assert r.status_code == 400


def test_kb_index_api_embedder_no_key_400(client, monkeypatch):
    monkeypatch.delenv("GPUSTACK_KEY_EMBED", raising=False)
    r = client.post("/api/kb/index",
                    json={"strategy": "fixed", "embedder": "api"})
    assert r.status_code == 400
    assert "GPUSTACK_KEY_EMBED" in r.json()["detail"]


# ---------- /api/kb/upload ----------

def test_kb_upload_txt(kb_env, client):
    agent, kb = kb_env
    r = client.post("/api/kb/upload",
                    files={"file": ("note.txt", b"hello kb", "text/plain")})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "file": "note.txt", "size": 8}
    p = os.path.join(kb.kb_dir, "uploads", "note.txt")
    assert os.path.exists(p)
    with open(p, "rb") as f:
        assert f.read() == b"hello kb"


def test_kb_upload_exe_400(kb_env, client):
    agent, kb = kb_env
    r = client.post("/api/kb/upload",
                    files={"file": ("evil.exe", b"MZ",
                                    "application/octet-stream")})
    assert r.status_code == 400
    assert "Неподдерживаемый формат" in r.json()["detail"]
    assert not os.path.exists(os.path.join(kb.kb_dir, "uploads", "evil.exe"))


# ---------- /api/kb: удаление файла и очистка базы ----------

def test_kb_delete_upload_removes_file_and_chunks(kb_env, client):
    """DELETE /api/kb/uploads/{name}: файл с диска, чанки из индекса,
    stats пересчитаны (files/chunks), pending_files снова содержит файл
    до пересборки — нет, файла нет в корпусе: pending пуст."""
    agent, kb = kb_env
    client.post("/api/kb/index",
                json={"strategy": "fixed", "embedder": "hash"})
    client.post("/api/kb/upload",
                files={"file": ("note.txt", b"hello kb world",
                                "text/plain")})
    client.post("/api/kb/index",
                json={"strategy": "fixed", "embedder": "hash"})
    b = client.get("/api/kb/stats").json()
    assert "uploads/note.txt" in b["files"]
    chunks_before = b["stats"]["chunks"]

    r = client.delete("/api/kb/uploads/note.txt")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "file": "note.txt",
                        "chunks_removed": 1}
    # файл с диска, чанки из индекса, stats пересчитаны
    assert not os.path.exists(os.path.join(kb.kb_dir, "uploads",
                                           "note.txt"))
    b2 = client.get("/api/kb/stats").json()
    assert "uploads/note.txt" not in b2["files"]
    assert b2["stats"]["chunks"] == chunks_before - 1
    assert b2["pending_files"] == []


def test_kb_delete_upload_404_missing(kb_env, client):
    r = client.delete("/api/kb/uploads/nope.txt")
    assert r.status_code == 404
    assert "не найден" in r.json()["detail"]


def test_kb_delete_upload_traversal_400(kb_env, client):
    agent, kb = kb_env
    # settings.json на диске (гард: traversal не должен его тронуть)
    assert client.post("/api/kb/settings",
                       json={"rag_top_k": 4}).status_code == 200
    # unit: basename-гард
    with pytest.raises(ValueError):
        kb.delete_upload("../settings.json")
    # API: закодированный traversal не проходит
    r = client.delete("/api/kb/uploads/..%2Fevil.txt")
    assert r.status_code in (400, 404, 405)
    assert os.path.exists(os.path.join(kb.kb_dir, "settings.json"))


def test_kb_wipe_clears_uploads_and_index_keeps_settings(kb_env, client):
    agent, kb = kb_env
    client.post("/api/kb/index",
                json={"strategy": "fixed", "embedder": "hash"})
    client.post("/api/kb/upload",
                files={"file": ("note.txt", b"hello kb",
                                "text/plain")})
    # settings — до wipe
    assert client.post("/api/kb/settings",
                       json={"rag_top_k": 7}).status_code == 200

    r = client.delete("/api/kb")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "uploads_removed": 1}
    # индекс удалён → stats 404, загрузки пусты
    assert client.get("/api/kb/stats").status_code == 404
    assert not os.path.exists(os.path.join(kb.kb_dir, "index.db"))
    assert not os.path.isdir(os.path.join(kb.kb_dir, "uploads")) or \
        os.listdir(os.path.join(kb.kb_dir, "uploads")) == []
    # settings пережили wipe
    s = client.get("/api/kb/settings").json()
    assert s["rag_top_k"] == 7


def test_kb_delete_and_wipe_409_while_running(kb_env, client):
    agent, kb = kb_env
    client.post("/api/kb/index",
                json={"strategy": "fixed", "embedder": "hash"})
    kb._progress = {"running": True, "phase": "embedding",
                    "done": 1, "total": 10}
    assert client.delete("/api/kb/uploads/whatever.txt").status_code == 409
    assert client.delete("/api/kb").status_code == 409


# ---------- агент: тумблер agent_loop (день 21) ----------

def _agent_with_kb(tmp_path, kb, handler=None, mcp_reg=None):
    d = tmp_path / "data"
    d.mkdir()
    agent = StudioAgent(str(d), base_url=BASE, api_key="test-key",
                        client=httpx.Client(transport=httpx.MockTransport(
                            handler or _llm_handler())),
                        mcp=mcp_reg, kb=kb)
    return agent


def _connected_fake_mcp(tmp_path):
    """MCPRegistry с fake stdio-процессом; первый сервер подключён."""
    d = tmp_path / "mcpdata"
    d.mkdir(exist_ok=True)
    reg = MCPRegistry(MemoryStore(str(d)), launcher=make_fake_launcher())
    sid = reg.servers()[0]["id"]
    assert reg.connect(sid)["status"] == "connected"
    return reg


def test_agent_loop_off_no_tools_with_connected_mcp(tmp_path):
    """agent_loop=false: tools в LLM-пейлоаде НЕТ, tool-loop не входит
    (один LLM-вызов), хотя MCP-сервер подключён."""
    kb = KnowledgeBase(str(tmp_path / "kb"), _mini_repo(tmp_path))
    reg = _connected_fake_mcp(tmp_path)
    seen = {}

    def handler(request):
        payload = json.loads(request.content)
        if "stream" not in payload:
            return httpx.Response(200, json={"choices": [
                {"message": {"content": "Название"}}]})
        seen["payload"] = payload
        body = sse_body([delta_chunk("Прив"), delta_chunk("ет"),
                         usage_chunk(), "[DONE]"])
        return httpx.Response(200, content=body.encode("utf-8"))

    agent = _agent_with_kb(tmp_path, kb, handler=handler, mcp_reg=reg)
    try:
        agent.kb.update_settings({"agent_loop": False})
        d = agent.store.new_dialogue()
        events = list(agent.ask_stream(d["id"], "привет"))
        assert events[-1]["type"] == "done"
        assert "tools" not in seen["payload"]
        # ровно один LLM-вызов (tool-loop не заходил)
        assert len(agent.requests_list()) == 1
    finally:
        reg.close_all()


def test_agent_loop_on_tools_present_with_connected_mcp(tmp_path):
    """agent_loop=true (дефолт): tools в LLM-пейлоаде ЕСТЬ
    (регрессия дня 17/20, явно с подключённым MCP-сервером)."""
    kb = KnowledgeBase(str(tmp_path / "kb"), _mini_repo(tmp_path))
    reg = _connected_fake_mcp(tmp_path)
    seen = {}

    def handler(request):
        payload = json.loads(request.content)
        if "stream" not in payload:
            return httpx.Response(200, json={"choices": [
                {"message": {"content": "Название"}}]})
        seen["payload"] = payload
        body = sse_body([delta_chunk("Прив"), delta_chunk("ет"),
                         usage_chunk(), "[DONE]"])
        return httpx.Response(200, content=body.encode("utf-8"))

    agent = _agent_with_kb(tmp_path, kb, handler=handler, mcp_reg=reg)
    try:
        d = agent.store.new_dialogue()
        events = list(agent.ask_stream(d["id"], "привет"))
        assert events[-1]["type"] == "done"
        assert "tools" in seen["payload"]
        names = [t["function"]["name"] for t in seen["payload"]["tools"]]
        assert any(n.endswith("__mock_echo") for n in names)
    finally:
        reg.close_all()


# ---------- агент: RAG-блок (день 21) ----------

def test_rag_block_in_system_prompt_with_hash_index(tmp_path):
    """RAG включён (дефолт) + hash-индекс: в system-промпт уходит блок
    «База знаний» с релевантной выдержкой про ксилофон."""
    kb = KnowledgeBase(str(tmp_path / "kb"), _mini_repo(tmp_path))
    _upload_music(kb)
    kb.build("structural", HashEmbedder())
    seen = {}

    def handler(request):
        payload = json.loads(request.content)
        if "stream" not in payload:
            return httpx.Response(200, json={"choices": [
                {"message": {"content": "Название"}}]})
        seen["system"] = payload["messages"][0]["content"]
        body = sse_body([delta_chunk("Прив"), delta_chunk("ет"),
                         usage_chunk(), "[DONE]"])
        return httpx.Response(200, content=body.encode("utf-8"))

    agent = _agent_with_kb(tmp_path, kb, handler=handler)
    d = agent.store.new_dialogue()
    events = list(agent.ask_stream(d["id"], "что про ксилофон?"))
    assert events[-1]["type"] == "done"
    assert "База знаний" in seen["system"]
    assert "ксилофон" in seen["system"].lower()
    assert "music.md" in seen["system"]  # источник (file) в блоке


def test_rag_off_no_kb_block(tmp_path):
    """RAG выключен: блок «База знаний» в system-промпт НЕ уходит
    (хотя индекс построен)."""
    kb = KnowledgeBase(str(tmp_path / "kb"), _mini_repo(tmp_path))
    kb.build("structural", HashEmbedder())
    kb.update_settings({"rag": False})
    seen = {}

    def handler(request):
        payload = json.loads(request.content)
        if "stream" not in payload:
            return httpx.Response(200, json={"choices": [
                {"message": {"content": "Название"}}]})
        seen["system"] = payload["messages"][0]["content"]
        body = sse_body([delta_chunk("Прив"), delta_chunk("ет"),
                         usage_chunk(), "[DONE]"])
        return httpx.Response(200, content=body.encode("utf-8"))

    agent = _agent_with_kb(tmp_path, kb, handler=handler)
    d = agent.store.new_dialogue()
    events = list(agent.ask_stream(d["id"], "что про ксилофон?"))
    assert events[-1]["type"] == "done"
    assert "База знаний" not in seen["system"]


def test_rag_on_but_no_index_chat_unchanged(tmp_path):
    """RAG включён, но индекс не построен: чат работает, блок нет,
    падения нет (build_kb_block тихо возвращает пусто)."""
    kb = KnowledgeBase(str(tmp_path / "kb"), _mini_repo(tmp_path))
    seen = {}

    def handler(request):
        payload = json.loads(request.content)
        if "stream" not in payload:
            return httpx.Response(200, json={"choices": [
                {"message": {"content": "Название"}}]})
        seen["system"] = payload["messages"][0]["content"]
        body = sse_body([delta_chunk("Прив"), delta_chunk("ет"),
                         usage_chunk(), "[DONE]"])
        return httpx.Response(200, content=body.encode("utf-8"))

    agent = _agent_with_kb(tmp_path, kb, handler=handler)
    d = agent.store.new_dialogue()
    events = list(agent.ask_stream(d["id"], "что про ксилофон?"))
    assert events[-1]["type"] == "done"
    assert events[-1]["answer"] == "Привет"
    assert "База знаний" not in seen["system"]
