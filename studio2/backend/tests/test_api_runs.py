import concurrent.futures
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from config import DEFAULT_CONFIG
from events import EventBus
from llm import LlmClient
from main import create_app
from pipeline.lead import PendingGate, QaLead
from store import RunStore

REPO_DOC = Path(__file__).resolve().parents[2] / "doc"


def _start(lead, run_id):
    with pytest.raises(PendingGate):
        lead.start(run_id)


@pytest.fixture()
def env(tmp_path):
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
    store = RunStore(str(tmp_path / "data"))
    app = create_app(doc_dir=str(tmp_path / "doc"), data_dir=str(tmp_path / "data"),
                     llm=LlmClient(cfg))
    app.state.lead_factory = lambda: QaLead(
        str(tmp_path / "doc"), str(tmp_path / "data"), LlmClient(cfg), app.state.bus,
        store, config=cfg,
    )
    return TestClient(app), tmp_path, repo


def test_create_run_returns_id(env):
    client, _, repo = env
    response = client.post("/api/runs", json={
        "task_id": "TC-4217", "repo_path": repo, "branch": "develop",
    })
    assert response.status_code == 201
    assert response.json()["run"]["id"]


def test_create_run_validates_task(env):
    client, _, repo = env
    assert client.post("/api/runs", json={
        "task_id": "TC-9999", "repo_path": repo, "branch": "develop",
    }).status_code == 400


def test_runs_listing(env):
    client, _, repo = env
    client.post("/api/runs", json={"task_id": "TC-4217", "repo_path": repo, "branch": "b"})
    runs = client.get("/api/runs").json()["runs"]
    assert runs and runs[0]["task_id"] == "TC-4217"


def test_unknown_run_returns_404(env):
    client, _, _repo = env
    assert client.get("/api/runs/nope").status_code == 404


def test_skeleton_read_and_write(env):
    client, _, repo = env
    run_id = client.post("/api/runs", json={
        "task_id": "TC-4217", "repo_path": repo, "branch": "b",
    }).json()["run"]["id"]
    _start(client.app.state.lead_factory(), run_id)
    body = client.get(f"/api/runs/{run_id}/skeleton").json()
    assert "Каркас" in body["content"]
    updated = body["content"] + "\n## Правка человека\n- уточнить тег\n"
    assert client.put(f"/api/runs/{run_id}/skeleton",
                      json={"content": updated}).status_code == 200
    assert "Правка человека" in client.get(f"/api/runs/{run_id}/skeleton").json()["content"]


def test_resume_blocked_without_gates(env):
    client, _, repo = env
    run_id = client.post("/api/runs", json={
        "task_id": "TC-4217", "repo_path": repo, "branch": "b",
    }).json()["run"]["id"]
    _start(client.app.state.lead_factory(), run_id)
    response = client.post(f"/api/runs/{run_id}/resume")
    assert response.status_code == 400
    assert "запрещён" in response.json()["detail"]


def test_gates_then_resume_completes(env):
    client, tmp_path, repo = env
    run_id = client.post("/api/runs", json={
        "task_id": "TC-4217", "repo_path": repo, "branch": "b",
    }).json()["run"]["id"]
    _start(client.app.state.lead_factory(), run_id)
    assert client.post(f"/api/runs/{run_id}/approve-skeleton").status_code == 200
    assert client.post(f"/api/runs/{run_id}/execution-choice",
                       json={"choice": "generated"}).status_code == 200
    assert client.post(f"/api/runs/{run_id}/resume").status_code == 200
    run = client.get(f"/api/runs/{run_id}").json()["run"]
    assert run["status"] == "done"
    assert run["verdict"] == "green"


def test_execution_choice_rejects_unknown(env):
    client, _, repo = env
    run_id = client.post("/api/runs", json={
        "task_id": "TC-4217", "repo_path": repo, "branch": "b",
    }).json()["run"]["id"]
    response = client.post(f"/api/runs/{run_id}/execution-choice", json={"choice": "magic"})
    assert response.status_code == 400


def test_cancel_run(env):
    client, _, repo = env
    run_id = client.post("/api/runs", json={
        "task_id": "TC-4217", "repo_path": repo, "branch": "b",
    }).json()["run"]["id"]
    assert client.post(f"/api/runs/{run_id}/cancel").status_code == 200
    assert client.get(f"/api/runs/{run_id}").json()["run"]["status"] == "cancelled"


def test_answer_unknown_question_returns_404(env):
    client, _, _repo = env
    response = client.post("/api/runs/nope/answer", json={"question_id": "q", "text": "x"})
    assert response.status_code == 404


def test_stream_emits_sse_frames(env):
    client, _, repo = env
    run_id = client.post("/api/runs", json={
        "task_id": "TC-4217", "repo_path": repo, "branch": "b",
    }).json()["run"]["id"]
    app = client.app
    bus = app.state.bus
    lead = app.state.lead_factory()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(_start, lead, run_id).result()
        bus.publish(run_id, {"type": "skeleton_ready", "path": "skeleton.md"})
        bus.close(run_id)
        with client.stream("GET", f"/api/runs/{run_id}/stream") as response:
            lines = [line for line in response.iter_lines() if line.startswith("data:")]
    assert lines
    frames = [json.loads(line[len("data: "):]) for line in lines]
    assert all(line.startswith("data: ") for line in lines)
    assert frames[-1]["type"] == "skeleton_ready"