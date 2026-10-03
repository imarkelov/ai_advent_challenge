"""Тесты kb.py (день 21): чанкеры, эмбеддеры, KnowledgeBase. Офлайн,
tmp_path, без сети (APIEmbedder — только httpx.MockTransport)."""
import json
import math
import os
import re
import sqlite3

import httpx
import pytest

import kb as kb_module
from kb import (APIEmbedder, APIReranker, CorpusDoc, EMBED_MODEL,
                FixedChunker, HashEmbedder, KBError, KnowledgeBase,
                RERANK_MODEL, StructuredChunker, cosine)


# ---------- мини-корпус (tmp repo) ----------

def _make_repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "docs" / "superpowers").mkdir(parents=True)
    (repo / "studio" / "backend").mkdir(parents=True)
    kb = repo / "data" / "kb"
    (kb / "uploads").mkdir(parents=True)
    # Доки репозитория + код бэкенда существуют в repo_root, но в корпус
    # НЕ входят (корпус = только загрузки, uploads-only — исправление
    # после релиза дня 21); в fixture остаются как «отвлекающий» репо.
    (repo / "README.md").write_text(
        "# Мини-README\n\n"
        "Проект Студии. Корпус знаний.\n\n"
        "## Раздел\n\n"
        "Обычный текст раздела.\n", encoding="utf-8")
    (repo / "docs" / "superpowers" / "guide.md").write_text(
        "# Гид\n\n"
        "Атомарная запись: атомарность атомарности через os.replace.\n\n"
        "## Детали\n\n"
        "Детали про атомарность и tmp-файл.\n", encoding="utf-8")
    (repo / "studio" / "backend" / "mod.py").write_text(
        "# кодовый файл\n"
        "def zaglushka():\n"
        '    return "zaglushka zaglushki"\n', encoding="utf-8")
    # Корпус — только загрузки («+ Добавить файл» → data/kb/uploads/)
    (kb / "uploads" / "music.md").write_text(
        "# Инструменты\n\n"
        "Здесь про ксилофон: ксилофон ксилофон ксилофон.\n\n"
        "## Раздел\n\n"
        "Звук ксилофона возникает при ударе по пластинам.\n",
        encoding="utf-8")
    (kb / "uploads" / "notes.txt").write_text(
        "Заметки: заметки заметок.\n", encoding="utf-8")
    return {"repo": str(repo), "kb": str(kb)}


@pytest.fixture
def env(tmp_path):
    return _make_repo(tmp_path)


@pytest.fixture
def kb(env):
    return KnowledgeBase(env["kb"], env["repo"])


# ---------- FixedChunker ----------

def test_fixed_chunker_sizes_and_overlap():
    doc = CorpusDoc("f.txt", "ab" * 1250, "docs")  # 2500 символов
    ch = FixedChunker(1200, 200).chunk(doc)
    assert len(ch) == 3
    assert len(ch[0].text) == 1200
    assert len(ch[1].text) == 1200
    assert len(ch[2].text) == 500
    assert ch[1].text[:200] == ch[0].text[-200:]  # overlap 200 символов
    assert all(c.section == "whole file" for c in ch)
    assert [c.chunk_index for c in ch] == [0, 1, 2]
    assert ch[0].file == "f.txt" and ch[0].source == "docs"


def test_fixed_chunker_short_text_one_chunk():
    ch = FixedChunker().chunk(CorpusDoc("s.md", "коротко", "docs"))
    assert len(ch) == 1
    assert ch[0].text == "коротко" and ch[0].section == "whole file"


def test_fixed_chunker_empty_text():
    assert FixedChunker().chunk(CorpusDoc("e.md", "", "docs")) == []


def test_fixed_chunker_bad_params():
    with pytest.raises(ValueError):
        FixedChunker(0, 0)
    with pytest.raises(ValueError):
        FixedChunker(100, 100)


# ---------- StructuredChunker ----------

def test_structured_md_sections():
    text = ("Вводное без заголовка.\n"
            "# Раздел А\nТекст А.\n"
            "## Подраздел Б\nТекст Б.\n")
    ch = StructuredChunker().chunk(CorpusDoc("d.md", text, "docs"))
    assert [c.section for c in ch] == [
        "(без заголовка)", "Раздел А", "Подраздел Б"]
    assert ch[0].text == "Вводное без заголовка."
    assert ch[1].text == "# Раздел А\nТекст А."
    assert ch[2].text == "## Подраздел Б\nТекст Б."


def test_structured_long_section_subchunks():
    body = "длинный текст большой секции. " * 200  # > 2400 символов
    text = f"# Большая\n{body}\n# Малая\nмало текста\n"
    ch = StructuredChunker().chunk(CorpusDoc("d.md", text, "docs"))
    big = [c for c in ch if c.section == "Большая"]
    assert len(big) > 1
    assert all(len(c.text) <= 1200 for c in big)
    assert all(c.section == "Большая" for c in big)  # секция сохраняется
    assert ch[-1].section == "Малая"
    assert [c.chunk_index for c in ch] == list(range(len(ch)))


def test_structured_md_no_headings_whole_file():
    text = "просто текст\nбез заголовков\n"
    ch = StructuredChunker().chunk(CorpusDoc("d.md", text, "docs"))
    assert len(ch) == 1
    assert ch[0].section == "whole file" and ch[0].text == text


def test_structured_code_file_one_chunk():
    ch = StructuredChunker().chunk(CorpusDoc("m.py", "print(1)\n", "code"))
    assert len(ch) == 1
    assert ch[0].section == "whole file" and ch[0].file == "m.py"


def test_structured_long_code_subchunks():
    text = "x = 1\n" * 700  # 4900 > 2400
    ch = StructuredChunker().chunk(CorpusDoc("big.py", text, "code"))
    assert len(ch) > 1
    assert all(c.section == "whole file" for c in ch)
    assert all(len(c.text) <= 1200 for c in ch)


# ---------- HashEmbedder ----------

def test_hash_embedder_deterministic():
    e = HashEmbedder()
    a = e.embed(["тестовый текст для детерминизма"])[0]
    b = e.embed(["тестовый текст для детерминизма"])[0]
    assert a == b  # два embed -> равные векторы


def test_hash_embedder_dim_and_l2_norm():
    e = HashEmbedder()
    v = e.embed(["нормальный текст для вектора"])[0]
    assert len(v) == 256
    norm = math.sqrt(sum(x * x for x in v))
    assert abs(norm - 1.0) < 1e-9


def test_hash_embedder_different_texts_different_vectors():
    e = HashEmbedder()
    a, b = e.embed(["первый короткий", "совсем другой длинный текст"])[0:2]
    assert a != b


def test_hash_embedder_cosine_same_vector():
    e = HashEmbedder()
    v = e.embed(["одинаковый текст"])[0]
    assert abs(cosine(v, v) - 1.0) < 1e-9


def test_cosine_zero_vector():
    assert cosine([0.0, 0.0], [1.0, 0.0]) == 0.0
    assert cosine([1.0, 0.0], [0.0]) == 0.0  # разная длина -> 0.0


# ---------- APIEmbedder (MockTransport, без сети) ----------

def _api_handler(calls, dim=4, status=200):
    def handler(request):
        body = json.loads(request.content)
        calls.append(body["input"])
        if status != 200:
            return httpx.Response(status)
        return httpx.Response(200, json={
            "model": EMBED_MODEL,
            "data": [{"index": i, "object": "embedding",
                      "embedding": [round(0.1 * (i + 1), 4)] * dim}
                     for i in range(len(body["input"]))]})
    return handler


def test_api_embedder_batching_30_texts_two_requests():
    calls = []
    client = httpx.Client(transport=httpx.MockTransport(_api_handler(calls)))
    emb = APIEmbedder("http://fake.local/v1", "key", client=client)
    vecs = emb.embed([f"текст {i}" for i in range(30)])
    assert len(vecs) == 30
    assert len(calls) == 2  # 30 текстов = 2 батча
    assert len(calls[0]) == 16
    assert len(calls[1]) == 14


def test_api_embedder_dim_detection_and_meta():
    calls = []
    client = httpx.Client(transport=httpx.MockTransport(_api_handler(calls)))
    emb = APIEmbedder("http://fake.local/v1", "key", client=client)
    assert emb.dim is None
    vecs = emb.embed(["один текст"])
    assert emb.dim == 4  # dim узнаётся с первого ответа
    assert len(vecs[0]) == 4
    assert emb.name == "api"
    assert emb.model == EMBED_MODEL


def test_api_embedder_http_500_raises_kberror():
    calls = []
    client = httpx.Client(
        transport=httpx.MockTransport(_api_handler(calls, status=500)))
    emb = APIEmbedder("http://fake.local/v1", "key", client=client)
    with pytest.raises(KBError):
        emb.embed(["один"])


def test_api_embedder_on_progress_per_batch():
    """on_progress(done, total) — после каждого батча, cumulative."""
    calls = []
    client = httpx.Client(transport=httpx.MockTransport(_api_handler(calls)))
    emb = APIEmbedder("http://fake.local/v1", "key", client=client)
    steps = []
    # 35 текстов → батчи 16/16/3
    vecs = emb.embed([f"текст {i}" for i in range(35)],
                     on_progress=lambda d, t: steps.append((d, t)))
    assert steps == [(16, 35), (32, 35), (35, 35)]
    assert len(vecs) == 35
    # без колбэка — как раньше
    assert len(emb.embed(["x", "y"])) == 2


def test_hash_embedder_on_progress_final_call():
    emb = HashEmbedder()
    steps = []
    vecs = emb.embed(["один", "два"], on_progress=lambda d, t: steps.append((d, t)))
    assert steps == [(2, 2)]
    assert len(vecs) == 2


# ---------- APIReranker (MockTransport, без сети) ----------

def _rerank_handler(calls, status=200, payload=None):
    """Фейк /rerank: results[] в ПЕРЕВЕРНУТОМ порядке — проверка re-index
    по полю index в исходный порядок текстов. score = (i+1)/100."""
    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        if status != 200:
            return httpx.Response(status)
        if payload is not None:
            return httpx.Response(200, json=payload)
        docs = body["documents"]
        rows = [{"index": i, "document": {"text": docs[i]},
                 "relevance_score": round((i + 1) / 100.0, 4)}
                for i in range(len(docs) - 1, -1, -1)]
        return httpx.Response(200, json={"id": "score-x",
                                         "model": RERANK_MODEL,
                                         "usage": {}, "results": rows})
    return handler


def test_api_reranker_reindex_and_meta():
    calls = []
    client = httpx.Client(transport=httpx.MockTransport(_rerank_handler(calls)))
    rr = APIReranker("http://fake.local/v1", "key", client=client)
    scores = rr.rerank("запрос", ["а", "б", "в"])
    # несмотря на перевёрнутый ответ — scores в исходном порядке текстов
    assert scores == [0.01, 0.02, 0.03]
    b = calls[0]
    assert b["model"] == RERANK_MODEL
    assert b["query"] == "запрос"
    assert b["documents"] == ["а", "б", "в"]
    assert rr.name == "api" and rr.model == RERANK_MODEL


def test_api_reranker_batching_35_texts_three_requests():
    calls = []
    client = httpx.Client(transport=httpx.MockTransport(_rerank_handler(calls)))
    rr = APIReranker("http://fake.local/v1", "key", client=client)
    scores = rr.rerank("q", [f"д {i}" for i in range(35)])
    assert len(scores) == 35
    assert len(calls) == 3  # 35 = 16 + 16 + 3
    assert len(calls[0]["documents"]) == 16
    assert len(calls[1]["documents"]) == 16
    assert len(calls[2]["documents"]) == 3


def test_api_reranker_truncates_doc_to_max_chars():
    calls = []
    client = httpx.Client(transport=httpx.MockTransport(_rerank_handler(calls)))
    rr = APIReranker("http://fake.local/v1", "key", client=client)
    rr.rerank("q", ["x" * 5000, "short"])
    assert calls[0]["documents"][0] == "x" * 1024
    assert calls[0]["documents"][1] == "short"


def test_api_reranker_http_500_raises_kberror():
    calls = []
    client = httpx.Client(
        transport=httpx.MockTransport(_rerank_handler(calls, status=500)))
    rr = APIReranker("http://fake.local/v1", "key", client=client)
    with pytest.raises(KBError):
        rr.rerank("q", ["один"])


def test_api_reranker_bad_payload_raises_kberror():
    # нет results[]
    c1 = httpx.Client(transport=httpx.MockTransport(
        _rerank_handler([], payload={"id": "x"})))
    with pytest.raises(KBError):
        APIReranker("http://fake.local/v1", "key", client=c1).rerank("q", ["a"])
    # элемент без relevance_score
    c2 = httpx.Client(transport=httpx.MockTransport(
        _rerank_handler([],
                        payload={"results": [{"index": 0, "document": {}}]})))
    with pytest.raises(KBError):
        APIReranker("http://fake.local/v1", "key", client=c2).rerank("q", ["a"])
    # 2 результата на 1 документ
    c3 = httpx.Client(transport=httpx.MockTransport(
        _rerank_handler([], payload={"results": [
            {"index": 0, "document": {}, "relevance_score": 0.5},
            {"index": 1, "document": {}, "relevance_score": 0.4}]})))
    with pytest.raises(KBError):
        APIReranker("http://fake.local/v1", "key", client=c3).rerank("q", ["a"])


# ---------- KnowledgeBase: корпус ----------

def test_corpus_files_uploads_only(env, kb):
    """Корпус = ТОЛЬКО загрузки (uploads/<имя>, source "upload").
    Доки репозитория (README/docs/superpowers) и код бэкенда — даже
    существующие в repo_root — в корпус не входят: индексироваться
    могут только файлы из «+ Добавить файл»."""
    docs = kb.corpus_files()
    by = {d.path: d for d in docs}
    # путь загрузки — фиксированный «uploads/<имя>» (не relpath от репо)
    assert set(by) == {"uploads/music.md", "uploads/notes.txt"}
    for d in docs:
        assert d.source == "upload"
    # доки/код репозитория на диске есть, но в корпусе их НЕТ
    for absent in ("README.md", "docs/superpowers/guide.md",
                   "studio/backend/mod.py"):
        assert absent not in by
        assert os.path.exists(
            os.path.join(env["repo"], *absent.split("/")))


def test_corpus_files_skips_bad_upload_ext(env, kb):
    with open(os.path.join(env["kb"], "uploads", "img.png"), "wb") as f:
        f.write(b"\x89PNG")
    paths = [d.path for d in kb.corpus_files()]
    assert not any(p.endswith(".png") for p in paths)


# ---------- KnowledgeBase: build ----------

def test_build_structural_index_on_disk(kb):
    res = kb.build("structural", HashEmbedder())
    assert res["strategy"] == "structural"
    assert set(res) == {"stats", "comparison", "strategy", "mode", "added"}
    assert res["mode"] == "full"
    # index.db на диске (SQLite); чтение — через store/load_index
    db_path = os.path.join(kb.kb_dir, "index.db")
    assert os.path.exists(db_path)
    idx = kb.load_index()
    assert idx is not None
    assert idx["version"] == 1
    assert idx["strategy"] == "structural"
    assert idx["embedder"] == "hash"
    assert idx["dim"] == 256
    assert idx["built_at"]
    for k in ("docs", "files", "chunks", "total_chars", "build_ms",
              "corpus_words"):
        assert k in idx["stats"]
    assert idx["stats"]["docs"] == 2  # только загрузки (uploads-only)
    assert set(idx["comparison"]) == {"fixed", "structural"}
    for side in ("fixed", "structural"):
        c = idx["comparison"][side]
        assert set(c) == {"chunks", "avg_chars", "max_chars", "hit_at_3",
                          "precision_at_3", "mrr"}
        assert 0.0 <= c["hit_at_3"] <= 1.0
        assert 0.0 <= c["precision_at_3"] <= 1.0
        assert 0.0 <= c["mrr"] <= 1.0
    # chunk_id: {file_stem}-{strategy}-{i:04d}
    ids = [c["chunk_id"] for c in idx["chunks"]]
    assert "music-structural-0000" in ids
    assert all(re.fullmatch(r"[A-Za-z0-9_.\-]+-structural-\d{4}", i)
               for i in ids)
    # векторы: dim 256 (BLOB float32: исходные значения округлены до
    # 6 знаков, остаток — точность float32)
    for c in idx["chunks"]:
        assert len(c["vector"]) == 256
        for x in c["vector"]:
            assert abs(x - round(x, 6)) <= 1e-5
        assert c["chars"] == len(c["text"])


def test_build_fixed_and_rebuild_overwrites(kb):
    kb.build("fixed", HashEmbedder())
    fresh = KnowledgeBase(kb.kb_dir, kb.repo_root).load_index()
    assert fresh["strategy"] == "fixed"
    res = kb.build("structural", HashEmbedder())
    idx = kb.load_index()
    assert idx["strategy"] == "structural"
    assert idx["stats"]["chunks"] == res["stats"]["chunks"]
    assert all("-structural-" in c["chunk_id"] for c in idx["chunks"])


def test_build_bad_strategy(kb):
    with pytest.raises(ValueError):
        kb.build("weird", HashEmbedder())


def test_build_metrics_gold_queries_on_minicorpus(kb, monkeypatch):
    # GOLD_QUERIES на мини-корпус: ксилофон живёт в загрузке music.md
    monkeypatch.setattr(kb_module, "GOLD_QUERIES",
                        [("ксилофон", "uploads/music.md")])
    res = kb.build("structural", HashEmbedder())
    for side in ("fixed", "structural"):
        m = res["comparison"][side]
        assert m["hit_at_3"] == 1.0
        assert m["mrr"] == 1.0


def test_build_metrics_zero_when_no_gold_file_in_corpus(kb):
    """Реальные GOLD_QUERIES (gold-файлы — доки репозитория) на
    uploads-only корпусе: пригодно 0 запросов → метрики нулевые,
    статистика чанков считается как обычно."""
    res = kb.build("structural", HashEmbedder())
    for side in ("fixed", "structural"):
        m = res["comparison"][side]
        assert m["chunks"] > 0
        assert m["hit_at_3"] == 0.0
        assert m["precision_at_3"] == 0.0
        assert m["mrr"] == 0.0
    assert res["stats"]["chunks"] > 0


# ---------- индекс-хранилище (SQLite, data/kb/index.db) ----------

def _sample_vector(dim: int) -> list[float]:
    """Детерминированный вектор: значения 0..1, округлены до 6 знаков
    (как в _chunks_out)."""
    return [round((i * 0.0137 + 0.25) % 1.0, 6) for i in range(dim)]


def test_vector_blob_roundtrip_dim256():
    vec = _sample_vector(256)
    blob = kb_module._pack_vector(vec)
    assert isinstance(blob, bytes) and len(blob) == 4 * 256
    back = kb_module._unpack_vector(blob, 256)
    assert len(back) == 256
    for a, b in zip(vec, back):
        assert abs(a - b) <= 1e-6  # float32 — допустимое округление


def test_vector_blob_roundtrip_dim4096():
    vec = _sample_vector(4096)
    blob = kb_module._pack_vector(vec)
    assert len(blob) == 4 * 4096
    back = kb_module._unpack_vector(blob, 4096)
    assert len(back) == 4096
    for a, b in zip(vec, back):
        assert abs(a - b) <= 1e-6


def test_vector_blob_bad_dim_raises():
    blob = kb_module._pack_vector([0.1, 0.2, 0.3])
    with pytest.raises(ValueError):
        kb_module._unpack_vector(blob, 4)


def test_store_load_returns_json_doc_shape(kb):
    """load_index (через store) — та же форма, что был JSON-документ:
    {version, strategy, embedder, model, dim, built_at, stats,
    comparison, chunks[]} — поиск/agent/UI без изменений."""
    kb.build("structural", HashEmbedder())
    doc = kb.load_index()
    assert doc is not None
    assert set(doc) == {"version", "strategy", "embedder", "model", "dim",
                        "built_at", "stats", "comparison", "chunks"}
    assert doc["strategy"] == "structural"
    assert doc["embedder"] == "hash"
    assert doc["model"] is None
    assert doc["dim"] == 256
    assert doc["chunks"]
    for c in doc["chunks"]:
        assert set(c) == {"chunk_id", "source", "file", "section", "chars",
                          "text", "vector", "terms"}
        assert len(c["vector"]) == 256
        assert isinstance(c["terms"], dict)
    # порядок чанков — как при записи (music.md раньше notes.txt)
    assert doc["chunks"][0]["file"] == "uploads/music.md"


def test_store_roundtrip_api_model_string(kb):
    """model api-эмбеддера — обычная строка в meta (не JSON):
    round-trip сохраняется (регресс: json.loads от обычной строки
    ломал load → None на реальном api-индексе)."""
    kb.build("structural", HashEmbedder())
    doc = kb.load_index()
    doc["embedder"] = "api"
    doc["model"] = EMBED_MODEL
    kb._store.replace(doc)
    back = kb.load_index()
    assert back is not None
    assert back["embedder"] == "api"
    assert back["model"] == EMBED_MODEL
    assert back["chunks"]


def test_store_corrupt_db_returns_none(tmp_path):
    k2 = KnowledgeBase(str(tmp_path / "kb"), str(tmp_path / "repo"))
    with open(k2._store.path, "wb") as f:
        f.write(b"not a sqlite database")
    assert k2.load_index() is None
    with pytest.raises(KBError, match="Индекс не построен"):
        k2.search("что угодно")


def test_legacy_json_auto_migrated_on_build(kb):
    """index.json (legacy) есть, index.db нет → при первой сборке
    авто-миграция в SQLite одной транзакцией, json удаляется; поиск
    после миграции работает; миграция идемпотентна."""
    kb.build("structural", HashEmbedder())
    doc = kb.load_index()
    kb._store.remove()
    legacy = os.path.join(kb.kb_dir, "index.json")
    kb_module.atomic_write_json(legacy, doc)
    assert os.path.exists(legacy) and not kb._store.exists()

    kb.build("structural", HashEmbedder())
    assert not os.path.exists(legacy)      # legacy-json удалён
    assert kb._store.exists()              # index.db создан
    assert kb.load_index() is not None

    # идемпотентность: повторная сборка — без json, db жив
    res2 = kb.build("structural", HashEmbedder())
    assert res2["mode"] == "incremental" and res2["added"] == 0
    assert not os.path.exists(legacy) and kb._store.exists()

    # поиск после миграции
    r = kb.search("ксилофон", k=3)
    assert r and r[0]["file"] == "uploads/music.md"


def test_incremental_build_inserts_only_new_file_chunks(kb):
    """Инкрементальная сборка — только INSERT чанков нового файла:
    старые строки НЕ переписываются (мутированный старый чанк в db
    переживает сборку), meta (stats/built_at) обновляется."""
    kb.build("structural", HashEmbedder())
    con = sqlite3.connect(kb._store.path)
    old_id = con.execute(
        "SELECT chunk_id FROM chunks "
        "WHERE file='uploads/music.md' LIMIT 1").fetchone()[0]
    # sentinel chars=24 != len(text) — переписал бы только full-rewrite
    con.execute("UPDATE chunks SET text='МУТАЦИЯ старая строка', "
                "chars=24 WHERE chunk_id=?", (old_id,))
    con.commit()
    con.close()

    with open(os.path.join(kb.kb_dir, "uploads", "extra.txt"), "w",
              encoding="utf-8") as f:
        f.write("Дополнительные заметки extra.\n")
    res = kb.build("structural", HashEmbedder())
    assert res["mode"] == "incremental" and res["added"] == 1

    idx = kb.load_index()
    old = next(c for c in idx["chunks"] if c["chunk_id"] == old_id)
    assert old["text"] == "МУТАЦИЯ старая строка"  # строка не тронута
    assert old["chars"] == 24                      # и chars не пересчитан
    assert any(c["file"] == "uploads/extra.txt"
               for c in idx["chunks"])
    # meta обновлено: stats считает оба файла
    assert idx["stats"]["chunks"] == res["stats"]["chunks"]
    assert idx["stats"]["files"] == 3
    assert idx["stats"]["chunks"] == len(idx["chunks"])


def test_delete_upload_removes_only_that_file_chunks(kb):
    """DELETE чанков — только чужого файла: чанки остальных файлов
    не меняются, stats пересчитан по оставшимся."""
    kb.build("structural", HashEmbedder())
    idx = kb.load_index()
    notes_ids = {c["chunk_id"] for c in idx["chunks"]
                 if c["file"] == "uploads/notes.txt"}
    music_before = [c for c in idx["chunks"]
                    if c["file"] == "uploads/music.md"]
    assert notes_ids and music_before

    res = kb.delete_upload("notes.txt")
    assert res["ok"] is True and res["file"] == "notes.txt"
    assert res["chunks_removed"] == len(notes_ids)

    idx2 = kb.load_index()
    assert not any(c["chunk_id"] in notes_ids for c in idx2["chunks"])
    assert [c for c in idx2["chunks"]
            if c["file"] == "uploads/music.md"] == music_before
    assert idx2["stats"]["chunks"] == len(idx2["chunks"])
    assert idx2["stats"]["files"] == 1


# ---------- KnowledgeBase: search ----------

def test_search_relevant_chunk_top3(kb):
    kb.build("structural", HashEmbedder())
    res = kb.search("ксилофон")
    assert len(res) == 3  # в мини-корпусе 3 чанка (k=5 не раздвинет)
    assert res[0]["file"] == "uploads/music.md"
    assert set(res[0]) == {"chunk_id", "source", "file", "section",
                           "score", "text"}
    assert res[0]["score"] == round(res[0]["score"], 6)
    assert all(res[i]["score"] >= res[i + 1]["score"]
               for i in range(len(res) - 1))


def test_search_k_param(kb):
    kb.build("structural", HashEmbedder())
    assert len(kb.search("ксилофон", k=2)) == 2


def test_search_no_index_raises(env):
    k2 = KnowledgeBase(env["kb"], env["repo"])
    with pytest.raises(KBError, match="Индекс не построен"):
        k2.search("что угодно")


def test_search_api_embedder_missing_key(kb, monkeypatch):
    """api-индекс + нет ключа + старый формат (без "terms") — ни одна
    нога гибридного поиска не работает → KBError."""
    kb.build("structural", HashEmbedder())
    idx = kb.load_index()
    idx["embedder"] = "api"
    for c in idx["chunks"]:
        c.pop("terms", None)
    kb._store.replace(idx)
    monkeypatch.delenv("GPUSTACK_KEY_EMBED", raising=False)
    with pytest.raises(KBError, match="GPUSTACK_KEY_EMBED"):
        kb.search("ксилофон")


def test_search_api_embedder_missing_key_lexical_fallback(
        kb, monkeypatch, capsys):
    """api-индекс + нет ключа + "terms" есть — BM25 fallback, не падает."""
    kb.build("structural", HashEmbedder())
    idx = kb.load_index()
    idx["embedder"] = "api"
    kb._store.replace(idx)
    monkeypatch.delenv("GPUSTACK_KEY_EMBED", raising=False)
    res = kb.search("ксилофон")
    assert res and res[0]["file"] == "uploads/music.md"
    assert "fallback" in capsys.readouterr().out


# ---------- гибридный поиск: токенизация, BM25, RRF ----------

def test_tokenize_basic():
    # IPhone — латиница (как в пасхалке); кириллическая И — отдельный
    # валидный токен-символ, но в строке нет
    assert kb_module._tokenize("IPhone 17Promax, телефон! ёлка a b") == \
        ["iphone", "17promax", "телефон", "елка"]
    assert kb_module._tokenize("a b c") == []


def test_build_stores_terms(kb):
    kb.build("structural", HashEmbedder())
    idx = kb.load_index()
    assert idx["chunks"]
    assert all(c.get("terms") for c in idx["chunks"])
    c = next(c for c in idx["chunks"] if "ксилофон" in c["text"])
    assert c["terms"].get("ксилофон", 0) >= 1


def test_bm25_rare_term_top(kb):
    kb.build("structural", HashEmbedder())
    idx = kb.load_index()
    scores = kb_module._bm25_scores("ксилофон", idx["chunks"])
    assert scores
    top = max(scores, key=lambda i: scores[i])
    assert "ксилофон" in idx["chunks"][top]["text"]


def test_bm25_no_terms_empty():
    assert kb_module._bm25_scores(
        "что угодно", [{"text": "текст"}, {}]) == {}


def test_bm25_stopwords_ignored():
    # частые «была/какая» (чанк 0, tf 5+2) не обгоняют редкий токен
    # «телефон» (чанк 1) — stopwords из запроса отбрасываются
    chunks = [
        {"terms": {"была": 5, "какая": 2, "заметки": 1}},
        {"terms": {"телефон": 1, "герой": 1}},
    ]
    s = kb_module._bm25_scores("какая модель телефона была", chunks)
    assert s and max(s, key=lambda i: s[i]) == 1
    assert s.get(0) in (None, 0)


def test_bm25_all_stopwords_empty():
    assert kb_module._bm25_scores(
        "была какая", [{"terms": {"была": 1, "какая": 1}}]) == {}


def test_rrf_fusion():
    """Чанк top-1 в обоих рангах выигрывает; зеркальные позиции равны."""
    f = kb_module._rrf([[0, 1, 2], [0, 2, 1]])
    assert max(f, key=f.get) == 0
    assert f[1] == f[2]


def test_search_hybrid_hidden_fact_inflection(kb):
    """Скрытый факт в «книге»: вопрос в род. падеже («...телефона...»)
    находит чанк с им. падежем («телефон») — BM25 substring-матч;
    top-1 — чанк со скрытой строкой."""
    up = os.path.join(kb.kb_dir, "uploads")
    os.makedirs(up, exist_ok=True)
    book = ("Жил-был герой. " * 30
            + "У героя был телефон Zubravichka-9000, на который он делал "
              "много фото. "
            + "Герой шёл по улице. " * 30)
    with open(os.path.join(up, "book.txt"), "w", encoding="utf-8") as f:
        f.write(book)
    kb.build("structural", HashEmbedder())
    res = kb.search("какая модель телефона была у героя", k=5)
    assert "Zubravichka-9000" in res[0]["text"]


def test_incremental_upgrades_terms(kb):
    """Старый индекс (без "terms") апгрейдится incremental-сборкой:
    terms появляются, векторы не меняются, добавлений нет."""
    kb.build("structural", HashEmbedder())
    idx = kb.load_index()
    old_vec = idx["chunks"][0]["vector"]
    for c in idx["chunks"]:
        c.pop("terms", None)
    kb._store.replace(idx)
    res = kb.build("structural", HashEmbedder())
    assert res["mode"] == "incremental" and res["added"] == 0
    idx2 = kb.load_index()
    assert all(c.get("terms") for c in idx2["chunks"])
    assert idx2["chunks"][0]["vector"] == old_vec


# ---------- KnowledgeBase: search_rag (двухэтапный) ----------

class _FakeReranker:
    """Фейк-реранкер: score = (i+1)/100 по позиции в списке — инвертирует
    порядок этапа 1 (худший stage1 становится top). fail=True — KBError."""
    name = "fake"
    model = "fake"

    def __init__(self, fail=False):
        self.fail = fail
        self.calls = 0

    def rerank(self, query, texts):
        self.calls += 1
        if self.fail:
            raise KBError("сбой фейк-реранкера")
        return [round((i + 1) / 100.0, 6) for i in range(len(texts))]


def test_search_rag_off_hybrid_only(kb):
    kb.build("structural", HashEmbedder())
    res = kb.search_rag("ксилофон", recall=5, top_k=3, reranker_mode="off")
    assert res["reranked"] is False
    assert res["recall_total"] == 3  # в мини-корпусе 3 чанка
    assert len(res["results"]) == 3
    # полей реранка в результатах нет
    for r in res["results"]:
        assert "rerank_score" not in r and "stage1_rank" not in r
        assert "reranked" not in r


def test_search_rag_rerank_reorders(kb):
    kb.build("structural", HashEmbedder())
    fake = _FakeReranker()
    res = kb.search_rag("ксилофон", recall=5, top_k=5,
                        reranker_mode="api", reranker=fake)
    assert fake.calls == 1
    assert res["reranked"] is True
    assert res["recall_total"] == 3  # в мини-корпусе 3 чанка
    assert len(res["results"]) == 3
    for r in res["results"]:
        assert r["reranked"] is True
        assert "rerank_score" in r and "stage1_rank" in r
    # фейк ставит на top худший по этапу 1 (stage1_rank=3) — инверсия
    assert res["results"][0]["stage1_rank"] == 3
    assert res["results"][-1]["stage1_rank"] == 1
    scores = [r["rerank_score"] for r in res["results"]]
    assert scores == sorted(scores, reverse=True)


def test_search_rag_top_k_truncates(kb):
    kb.build("structural", HashEmbedder())
    res = kb.search_rag("ксилофон", recall=5, top_k=2, reranker_mode="off")
    assert res["recall_total"] == 3  # в мини-корпусе 3 чанка
    assert len(res["results"]) == 2


def test_search_rag_api_no_key_degrades(kb, monkeypatch, capsys):
    """mode api + нет ключа GPUSTACK_KEY_RERANK — деградация на этап 1."""
    kb.build("structural", HashEmbedder())
    monkeypatch.delenv("GPUSTACK_KEY_RERANK", raising=False)
    res = kb.search_rag("ксилофон", recall=5, top_k=3, reranker_mode="api")
    assert res["reranked"] is False
    assert len(res["results"]) == 3
    assert "ключ не настроен" in capsys.readouterr().out


def test_search_rag_api_error_degrades(kb, capsys):
    """сбой реранкера (KBError) — деградация на этап 1, поиск не ломается."""
    kb.build("structural", HashEmbedder())
    fake = _FakeReranker(fail=True)
    res = kb.search_rag("ксилофон", recall=5, top_k=3,
                        reranker_mode="api", reranker=fake)
    assert fake.calls == 1
    assert res["reranked"] is False
    assert len(res["results"]) == 3
    assert "Reranker сбой" in capsys.readouterr().out


def test_search_rag_no_index_raises(env):
    k2 = KnowledgeBase(env["kb"], env["repo"])
    with pytest.raises(KBError, match="Индекс не построен"):
        k2.search_rag("что угодно", recall=5, top_k=3, reranker_mode="off")


# ---------- search_rag: min_score (день 23) ----------

class _ScoredReranker:
    """Фейк-реранкер с фиксированными scores (детерминированные пороги)."""
    def __init__(self, scores):
        self.scores = list(scores)
        self.calls = 0

    def rerank(self, query, texts):
        self.calls += 1
        assert len(texts) == len(self.scores)
        return self.scores


def test_search_rag_min_score_filters_reranked(kb):
    """fake scores [0.99, 0.5, 0.1], min_score=0.6 → ровно 1 (0.99)."""
    kb.build("structural", HashEmbedder())
    fake = _ScoredReranker([0.99, 0.5, 0.1])
    res = kb.search_rag("ксилофон", recall=5, top_k=5,
                        reranker_mode="api", reranker=fake, min_score=0.6)
    assert res["filtered"] is True
    assert res["dropped"] == 2
    assert len(res["results"]) == 1
    assert res["results"][0]["rerank_score"] == 0.99


def test_search_rag_min_score_drops_all(kb):
    """min_score=0.999 → 0 результатов, dropped=3."""
    kb.build("structural", HashEmbedder())
    fake = _ScoredReranker([0.99, 0.5, 0.1])
    res = kb.search_rag("ксилофон", recall=5, top_k=5,
                        reranker_mode="api", reranker=fake, min_score=0.999)
    assert res["filtered"] is True
    assert res["dropped"] == 3
    assert res["results"] == []


def test_search_rag_min_score_zero_byte_identical(kb):
    """min_score=0.0 — ответ идентичен вызову без фильтра."""
    kb.build("structural", HashEmbedder())
    base = kb.search_rag("ксилофон", recall=5, top_k=3, reranker_mode="off")
    zero = kb.search_rag("ксилофон", recall=5, top_k=3, reranker_mode="off",
                         min_score=0.0)
    assert zero == base  # байт-в-байт: поля filtered/dropped не добавляются
    assert "filtered" not in zero and "dropped" not in zero


def test_search_rag_min_score_relative_best_always_passes(kb):
    """реранкер off — относительный режим: лучший результат ВСЕГДА
    проходит даже min_score=0.9999 (score/best = 1.0 >= 0.9999)."""
    kb.build("structural", HashEmbedder())
    base = kb.search_rag("ксилофон", recall=5, top_k=3, reranker_mode="off")
    res = kb.search_rag("ксилофон", recall=5, top_k=3, reranker_mode="off",
                        min_score=0.9999)
    assert res["filtered"] is True
    assert len(base["results"]) == 3  # в мини-корпусе 3 чанка
    assert len(res["results"]) >= 1
    best = max(r["score"] for r in base["results"])
    # лучший по этапу 1 на месте: RRF-счёт самого лучшего не отфильтрован
    assert any(r["score"] == best for r in res["results"])
    assert res["results"][0]["score"] >= 0.9999 * best


def test_search_rag_min_score_best_zero_guard(kb):
    """все scores этапа 1 = 0 → пустой результат, деления нет."""
    kb.build("structural", HashEmbedder())
    kb.search = lambda query, k=5: [{"chunk_id": "c-0", "source": "upload",
                                      "file": "f", "section": "s",
                                      "score": 0.0, "text": "текст"}]
    res = kb.search_rag("что угодно", recall=5, top_k=3,
                        reranker_mode="off", min_score=0.5)
    assert res["filtered"] is True
    assert res["dropped"] == 1
    assert res["results"] == []


def test_search_rag_min_score_empty_stage1_no_crash(kb):
    """stage1 пуст (векторная нога деградировала, BM25 — 0 матчей) +
    min_score>0 + реранкер off — без исключения (защита от
    max() по пустому списку), пустой результат."""
    kb.build("structural", HashEmbedder())
    kb.search = lambda query, k=5: []
    res = kb.search_rag("что угодно", recall=5, top_k=3,
                        reranker_mode="off", min_score=0.5)
    assert res["results"] == []
    assert res["recall_total"] == 0
    # ранний возврат (пустой stage1) срабатывает ДО блока фильтра
    assert "filtered" not in res and "dropped" not in res


def test_search_rag_min_score_ge_semantics_edge(kb):
    """edge: min_score=1.0, doc с rerank_score=1.0 → остаётся (>=)."""
    kb.build("structural", HashEmbedder())
    fake = _ScoredReranker([1.0, 0.2, 0.1])
    res = kb.search_rag("ксилофон", recall=5, top_k=5,
                        reranker_mode="api", reranker=fake, min_score=1.0)
    assert res["filtered"] is True
    assert res["dropped"] == 2
    assert len(res["results"]) == 1
    assert res["results"][0]["rerank_score"] == 1.0


# ---------- KnowledgeBase: settings ----------

def test_settings_defaults_no_write(env):
    s = KnowledgeBase(env["kb"], env["repo"])
    assert s.settings() == {"agent_loop": True, "rag": True,
                            "reranker": "off", "rag_recall": 50,
                            "rag_top_k": 3, "min_score": 0.0,
                            "strategy": "structural", "embedder": "hash"}
    # дефолты файлом НЕ пишутся
    assert not os.path.exists(os.path.join(env["kb"], "settings.json"))


def test_update_settings_valid_and_persist(kb):
    s = kb.update_settings({"rag_top_k": 5, "strategy": "fixed",
                            "rag": False})
    assert s["rag_top_k"] == 5 and s["strategy"] == "fixed"
    assert s["rag"] is False and s["agent_loop"] is True
    p = os.path.join(kb.kb_dir, "settings.json")
    with open(p, encoding="utf-8") as f:  # файл — валидный JSON
        raw = json.load(f)
    assert raw == s


def test_update_settings_ignores_unknown_keys(kb):
    s = kb.update_settings({"bogus": 1, "rag_top_k": 4})
    assert "bogus" not in s
    assert s["rag_top_k"] == 4


def test_update_settings_invalid_values(kb):
    with pytest.raises(ValueError):
        kb.update_settings({"agent_loop": "yes"})
    with pytest.raises(ValueError):
        kb.update_settings({"rag": 1})
    with pytest.raises(ValueError):
        kb.update_settings({"rag_top_k": 11})
    with pytest.raises(ValueError):
        kb.update_settings({"rag_top_k": 0})
    with pytest.raises(ValueError):
        kb.update_settings({"rag_top_k": "5"})
    with pytest.raises(ValueError):
        kb.update_settings({"rag_top_k": True})
    with pytest.raises(ValueError):
        kb.update_settings({"strategy": "weird"})
    with pytest.raises(ValueError):
        kb.update_settings({"embedder": "weird"})
    with pytest.raises(ValueError):
        kb.update_settings({"reranker": "weird"})
    with pytest.raises(ValueError):
        kb.update_settings({"rag_recall": 201})
    with pytest.raises(ValueError):
        kb.update_settings({"rag_recall": 0})
    with pytest.raises(ValueError):
        kb.update_settings({"rag_recall": "50"})
    with pytest.raises(ValueError):
        kb.update_settings({"rag_recall": True})
    assert kb.settings() == KnowledgeBase.DEFAULT_SETTINGS  # не испорчено


def test_update_settings_min_score_valid(kb):
    s = kb.update_settings({"min_score": 0.5})
    assert s["min_score"] == 0.5
    # границы диапазона
    assert kb.update_settings({"min_score": 0})["min_score"] == 0.0
    assert kb.update_settings({"min_score": 1})["min_score"] == 1.0
    # храним float (0 → 0.0, 1 → 1.0)
    assert isinstance(kb.settings()["min_score"], float)


def test_update_settings_min_score_invalid(kb):
    # bool — ПЕРВЫЙ (ловушка isinstance(True, int)); потом тип, потом диапазон
    for bad in (True, False, "0.5", 1.5, -0.1, None, [0.5]):
        with pytest.raises(ValueError, match="min_score"):
            kb.update_settings({"min_score": bad})
    assert kb.settings() == KnowledgeBase.DEFAULT_SETTINGS  # не испорчено


def test_settings_broken_file_defaults(kb):
    p = os.path.join(kb.kb_dir, "settings.json")
    with open(p, "w", encoding="utf-8") as f:
        f.write("{битый json")
    assert kb.settings() == KnowledgeBase.DEFAULT_SETTINGS


# ---------- чтение файлов корпуса: авто-определение кодировки ----------

def test_read_file_utf8_cyrillic_roundtrip(kb, tmp_path):
    """Валидный utf-8 декодируется 1:1 (поведение не изменилось)."""
    p = os.path.join(str(tmp_path), "u8.txt")
    text = "Телефон был у бабки — проверка кириллицы.\n"
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    assert kb._read_file(p) == text


def test_read_file_cp1251_decodes_cyrillic(kb, tmp_path):
    """Windows-1251 (ANSI) — реальная кириллица, а не U+FFFD-каша
    (история: cp1251-файл дал 5826 U+FFFD, поиск по кириллице пуст)."""
    p = os.path.join(str(tmp_path), "ansi.txt")
    with open(p, "w", encoding="cp1251") as f:
        f.write("Как телфон был у бабки — старый текст в cp1251.\n")
    text = kb._read_file(p)
    assert text is not None
    assert "\ufffd" not in text
    assert "телфон" in text
    assert "бабки" in text


def test_read_file_undecodable_binary_no_raise(kb, tmp_path):
    """Бинарный мусор (не валиден ни utf-8, ни cp1251) — без исключения,
    строка (fallback errors="replace" — сборку не роняет)."""
    p = os.path.join(str(tmp_path), "bin.dat")
    with open(p, "wb") as f:
        f.write(bytes(range(0x80, 0x100)) + b"\x00\xff\xfe")
    text = kb._read_file(p)
    assert isinstance(text, str)


# ---------- _match_tf: typo-допуск (префикс) ----------

def test_match_tf_typo_prefix_match():
    """1-буквенная опечатка «телфон» не substring «телефон», но
    typo-правило (первые 5 симв. равны ИЛИ удаление одного символа,
    оба >= 5) — матчится."""
    assert kb_module._match_tf("телфон", {"телефон": 2}) == 2


def test_match_tf_typo_prefix_branch():
    """Равные первые 5 симв. (замена 5-го) — тоже матч."""
    assert kb_module._match_tf("телефх", {"телефон": 2}) == 2


def test_match_tf_typo_max_semantics():
    """Несколько typo-матчей — max tf (как в substring-ветке)."""
    assert kb_module._match_tf(
        "телфон", {"телефон": 2, "телфоны": 5}) == 5


def test_match_tf_exact_unchanged():
    assert kb_module._match_tf("телефон", {"телефон": 3}) == 3


def test_match_tf_short_terms_no_prefix():
    """Короткие термы (< 5 симв.) префикс-правило не трогает:
    «теле» — префикс «телефон», но len < 5 → 0 (как и раньше)."""
    assert kb_module._match_tf("теле", {"телефон": 2}) == 0


def test_match_tf_substring_inflection_unchanged():
    """Старое substring-поведение инфлексий сохранено."""
    assert kb_module._match_tf("телефона", {"телефон": 2}) == 2
    assert kb_module._match_tf("телефон", {"телефона": 4}) == 4
    assert kb_module._match_tf("ксилофон", {"стол": 1}) == 0
