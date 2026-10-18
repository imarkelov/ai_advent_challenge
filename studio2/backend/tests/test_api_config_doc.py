import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from config import DEFAULT_CONFIG
from main import create_app

REPO_DOC = Path(__file__).resolve().parents[2] / "doc"


def _client(tmp_path):
    shutil.copytree(REPO_DOC, tmp_path / "doc")
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    for role in cfg["roles"]:
        cfg["roles"][role]["provider"] = "fake"
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / "config.json").write_text(
        json.dumps(cfg, ensure_ascii=False), encoding="utf-8"
    )
    return TestClient(create_app(doc_dir=str(tmp_path / "doc"),
                                 data_dir=str(tmp_path / "data")))


def test_health(tmp_path):
    assert _client(tmp_path).get("/api/health").json()["status"] == "ok"


def test_config_masks_keys(tmp_path):
    body = _client(tmp_path).get("/api/config").json()
    assert set(body["roles"]) == {"qa-lead", "researcher", "testwriter",
                                  "debugger", "reviewer"}
    assert "api_key" not in body["providers"]["ollama"]
    assert "api_key_masked" in body["providers"]["ollama"]


def test_config_update_role(tmp_path):
    client = _client(tmp_path)
    response = client.post("/api/config", json={"roles": {"researcher": {"temperature": 0.5}}})
    assert response.status_code == 200
    assert client.get("/api/config").json()["roles"]["researcher"]["temperature"] == 0.5


def test_config_rejects_same_reviewer_model(tmp_path):
    client = _client(tmp_path)
    writer_model = client.get("/api/config").json()["roles"]["testwriter"]["model"]
    response = client.post("/api/config", json={"roles": {"reviewer": {"model": writer_model}}})
    assert response.status_code == 400
    assert "отличаться" in response.json()["detail"]


def test_models_fake(tmp_path):
    body = _client(tmp_path).get("/api/models", params={"provider": "fake"}).json()
    assert body["models"] == ["fake-1"]


def test_model_test_endpoint(tmp_path):
    body = _client(tmp_path).post("/api/models/test", json={"role": "reviewer"}).json()
    assert body["ok"] is True


def test_doc_tree_and_crud(tmp_path):
    client = _client(tmp_path)
    names = {node["name"] for node in client.get("/api/doc/tree").json()["tree"]}
    assert {"kb", "tasks", "agents", "templates"} <= names
    assert "TC-4217" in client.get("/api/doc/file", params={"path": "tasks/TC-4217.md"}).json()["content"]
    saved = client.post("/api/doc/file", json={"path": "kb/notes.md", "content": "# N\n"})
    assert saved.status_code == 200
    assert client.get("/api/doc/file", params={"path": "kb/notes.md"}).json()["content"] == "# N\n"
    assert client.delete("/api/doc/file", params={"path": "kb/notes.md"}).status_code == 200


def test_doc_missing_file_returns_404(tmp_path):
    client = _client(tmp_path)
    assert client.get("/api/doc/file", params={"path": "kb/none.md"}).status_code == 404


def test_doc_write_outside_roots_returns_400(tmp_path):
    client = _client(tmp_path)
    assert client.post("/api/doc/file", json={"path": "runs/x.md", "content": "x"}).status_code == 400


def test_upload_and_search(tmp_path):
    client = _client(tmp_path)
    upload = client.post("/api/doc/upload", data={"dir": "tasks"},
                         files={"file": ("TC-9999.md", b"# TC-9999\n", "text/markdown")})
    assert upload.json()["path"] == "tasks/TC-9999.md"
    assert client.post("/api/rag/reindex").json()["documents"] >= 10
    hits = client.get("/api/rag/search", params={"q": "адрес", "limit": 3}).json()["hits"]
    assert hits


def test_tasks_endpoint(tmp_path):
    tasks = _client(tmp_path).get("/api/tasks").json()["tasks"]
    assert any(task["id"] == "TC-4217" for task in tasks)
