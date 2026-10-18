import shutil
from pathlib import Path

import pytest

from rag import RagIndex

REPO_DOC = Path(__file__).resolve().parents[2] / "doc"


@pytest.fixture()
def doc_dir(tmp_path):
    shutil.copytree(REPO_DOC, tmp_path / "doc")
    return tmp_path / "doc"


def test_reindex_counts_markdown_files(doc_dir):
    assert RagIndex(str(doc_dir)).reindex() >= 10


def test_search_finds_task(doc_dir):
    index = RagIndex(str(doc_dir))
    index.reindex()
    hits = index.search("Добавление адреса доставки", limit=3)
    assert hits
    assert any("TC-4217" in hit["path"] for hit in hits)


def test_search_finds_patterns(doc_dir):
    index = RagIndex(str(doc_dir))
    index.reindex()
    hits = index.search("withTestTag", limit=5)
    assert any("patterns" in hit["path"] for hit in hits)


def test_snippet_is_not_empty(doc_dir):
    index = RagIndex(str(doc_dir))
    index.reindex()
    assert index.search("AcceptButton", limit=3)[0]["snippet"]


def test_reindex_clears_stale_flag(doc_dir):
    index = RagIndex(str(doc_dir))
    index.reindex()
    target = doc_dir / "kb" / "principles.md"
    target.write_text(target.read_text(encoding="utf-8") + "\nновый факт\n", encoding="utf-8")
    assert index.stale(str(target)) is True
    index.reindex()
    assert index.stale(str(target)) is False


def test_stats_reports_documents(doc_dir):
    index = RagIndex(str(doc_dir))
    index.reindex()
    stats = index.stats()
    assert stats["documents"] >= 10
    assert stats["updated_at"]


def test_empty_query_returns_nothing(doc_dir):
    index = RagIndex(str(doc_dir))
    index.reindex()
    assert index.search("", limit=3) == []
