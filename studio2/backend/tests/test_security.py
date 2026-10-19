import json
import os
import shutil
import sys
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from config import DEFAULT_CONFIG
from docstore import DocPathError, DocStore
from mcp import McpRegistry, McpServerStore, scrubbed_env
from main import create_app
from pipeline.artifacts import RunDir
from store import RunStore

REPO_DOC = Path(__file__).resolve().parents[2] / "doc"
BAD_RUN = "..\\..\\..\\target"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.delenv("GPUSTACK_API_KEY", raising=False)
    monkeypatch.delenv("GPUSTACK_BASE_URL", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    shutil.copytree(REPO_DOC, tmp_path / "doc")
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    for role in cfg["roles"]:
        cfg["roles"][role]["provider"] = "fake"
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / "config.json").write_text(
        json.dumps(cfg, ensure_ascii=False), encoding="utf-8"
    )
    (tmp_path / "repo" / ".git").mkdir(parents=True, exist_ok=True)
    repo = str(tmp_path / "repo").replace("\\", "/")
    app = create_app(doc_dir=str(tmp_path / "doc"), data_dir=str(tmp_path / "data"))
    return TestClient(app), tmp_path, repo


@pytest.mark.parametrize("run_id", [BAD_RUN, "../target", "a/../../b", "", ".hidden"])
def test_store_rejects_bad_run_id(tmp_path, run_id):
    store = RunStore(str(tmp_path / "data"))
    with pytest.raises(ValueError, match="недопустимый идентификатор"):
        store.get_run(run_id)
    with pytest.raises(ValueError, match="недопустимый идентификатор"):
        store.update_run(run_id, stage="study")
    with pytest.raises(ValueError, match="недопустимый идентификатор"):
        store._path(run_id)


def test_store_rejects_bad_run_id_on_save(tmp_path):
    store = RunStore(str(tmp_path / "data"))
    with pytest.raises(ValueError, match="недопустимый идентификатор"):
        store.save_run({"id": BAD_RUN})


def test_rundir_rejects_bad_run_id(tmp_path):
    with pytest.raises(ValueError, match="недопустимый идентификатор"):
        RunDir(str(tmp_path), BAD_RUN)


def test_api_run_routes_reject_traversal(env):
    client, tmp_path, _repo = env
    encoded = "..%5C..%5C..%5Ctarget"
    assert client.get(f"/api/runs/{encoded}").status_code == 400
    assert client.get(f"/api/runs/{encoded}/skeleton").status_code == 400
    put = client.put(f"/api/runs/{encoded}/skeleton", json={"content": "# x"})
    assert put.status_code == 400
    assert client.post(f"/api/runs/{encoded}/approve-skeleton").status_code == 400
    assert not (tmp_path / "target.json").exists()
    assert not (tmp_path / "target" / "skeleton.md").exists()


def test_api_config_does_not_persist_env_api_key(env, monkeypatch):
    client, tmp_path, _repo = env
    monkeypatch.setenv("GPUSTACK_API_KEY", "secret-from-env")
    monkeypatch.setenv("GPUSTACK_BASE_URL", "https://env.example/v1")
    assert client.post("/api/config", json={}).status_code == 200
    stored = json.loads((tmp_path / "data" / "config.json").read_text(encoding="utf-8"))
    assert stored["providers"]["gpustack"]["api_key"] is None
    assert stored["providers"]["gpustack"]["base_url"] != "https://env.example/v1"
    raw = (tmp_path / "data" / "config.json").read_text(encoding="utf-8")
    assert "secret-from-env" not in raw


def test_api_config_keeps_explicit_key(env, monkeypatch):
    client, tmp_path, _repo = env
    monkeypatch.setenv("GPUSTACK_API_KEY", "secret-from-env")
    client.post("/api/config", json={"providers": {"gpustack": {"api_key": "typed-key"}}})
    stored = json.loads((tmp_path / "data" / "config.json").read_text(encoding="utf-8"))
    assert stored["providers"]["gpustack"]["api_key"] == "typed-key"


def test_docstore_rejects_agents_write(tmp_path):
    shutil.copytree(REPO_DOC, tmp_path / "doc")
    store = DocStore(str(tmp_path / "doc"))
    with pytest.raises(DocPathError):
        store.write("agents/reviewer.md", "---\nrole: reviewer\n---\n")
    with pytest.raises(DocPathError):
        store.write("templates/sdd.md", "# x")
    assert store.read("agents/reviewer.md")


def test_api_rejects_agents_write(env):
    client, tmp_path, _repo = env
    response = client.post("/api/doc/file",
                           json={"path": "agents/reviewer.md", "content": "hacked"})
    assert response.status_code == 400
    assert "hacked" not in (tmp_path / "doc" / "agents" / "reviewer.md").read_text(
        encoding="utf-8")


SENTINEL_SERVER = textwrap.dedent(
    """
    import json, os, sys

    def send(obj):
        sys.stdout.write(json.dumps(obj) + "\\n")
        sys.stdout.flush()

    for line in sys.stdin:
        if not line.strip():
            continue
        msg = json.loads(line)
        method = msg.get("method")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": msg["id"], "result": {
                "protocolVersion": "2024-11-05", "capabilities": {},
                "serverInfo": {"name": "env", "version": "1"}}})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": [{
                "name": "env",
                "description": json.dumps({
                    "gpustack": "GPUSTACK_API_KEY" in os.environ,
                    "ollama": "OLLAMA_KEY" in os.environ,
                    "declared": os.environ.get("DECLARED_VAR", ""),
                }),
                "inputSchema": {"type": "object"}}]}})
        elif method == "notifications/initialized":
            continue
        else:
            send({"jsonrpc": "2.0", "id": msg.get("id", 0),
                  "error": {"code": -32601, "message": "unknown"}})
    """
)


def test_mcp_env_is_scrubbed(tmp_path, monkeypatch):
    monkeypatch.setenv("GPUSTACK_API_KEY", "sentinel-gpustack")
    monkeypatch.setenv("OLLAMA_KEY", "sentinel-ollama")
    server_py = tmp_path / "env_server.py"
    server_py.write_text(SENTINEL_SERVER, encoding="utf-8")
    store = McpServerStore(str(tmp_path / "data"))
    server = store.add({
        "name": "env", "type": "stdio",
        "command": [sys.executable, str(server_py)],
        "env": {"DECLARED_VAR": "visible"},
    })
    registry = McpRegistry(store)
    try:
        tools = registry.connect(server["id"])["tools"]
    finally:
        registry.disconnect(server["id"])
    seen = json.loads(tools[0]["description"])
    assert seen["gpustack"] is False
    assert seen["ollama"] is False
    assert seen["declared"] == "visible"


def test_scrubbed_env_allowlist(monkeypatch):
    monkeypatch.setenv("GPUSTACK_API_KEY", "x")
    monkeypatch.setenv("TEMP", "C:/tmp")
    env = scrubbed_env({"EXTRA": "1"})
    assert "GPUSTACK_API_KEY" not in env
    assert env["EXTRA"] == "1"
    assert env.get("TEMP") == "C:/tmp"
    assert set(env) <= set(os.environ) | {"EXTRA"}


def test_task_id_traversal_rejected(env):
    client, tmp_path, repo = env
    (tmp_path / "doc" / "secret.md").write_text("# x", encoding="utf-8")
    response = client.post("/api/runs", json={
        "task_id": "../secret", "repo_path": repo, "branch": "develop",
    })
    assert response.status_code == 400
    assert list((tmp_path / "data" / "runs").glob("*.json")) == []
