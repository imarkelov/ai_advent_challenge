import shutil
from pathlib import Path

import pytest

from docstore import DocPathError, DocStore

REPO_DOC = Path(__file__).resolve().parents[2] / "doc"


@pytest.fixture()
def store(tmp_path):
    shutil.copytree(REPO_DOC, tmp_path / "doc")
    return DocStore(str(tmp_path / "doc"))


def test_tree_lists_roots(store):
    names = {node["name"] for node in store.tree()}
    assert {"kb", "tasks", "agents", "templates"} <= names


def test_read_known_file(store):
    assert "TC-4217" in store.read("tasks/TC-4217.md")


def test_write_and_read_back(store):
    store.write("kb/notes.md", "# Заметки\n")
    assert store.read("kb/notes.md") == "# Заметки\n"


def test_write_rejects_traversal(store):
    with pytest.raises(DocPathError):
        store.write("../escape.md", "x")


def test_write_rejects_runs_dir(store):
    with pytest.raises(DocPathError):
        store.write("runs/1/test.md", "x")


def test_upload_creates_file(store):
    rel = store.upload("kb", "screen.md", "# Экран\n".encode("utf-8"))
    assert rel == "kb/screen.md"
    assert store.read(rel) == "# Экран\n"


def test_delete_allowed_for_kb(store):
    store.write("kb/tmp.md", "x")
    store.delete("kb/tmp.md")
    with pytest.raises(DocPathError):
        store.read("kb/tmp.md")


def test_delete_rejects_agents(store):
    with pytest.raises(DocPathError):
        store.delete("agents/reviewer.md")
