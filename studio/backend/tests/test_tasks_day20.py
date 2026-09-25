"""File-backed задачи (день 20): store + серверы task_create/task_get,
включая КРОСС-ПРОЦЕСНЫЙ тест (создание в одном subprocess, чтение в
другом). Без сети."""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
STUDIO = os.path.join(REPO, "studio")
SERVERS = os.path.join(REPO, "studio", "mcp_servers")
sys.path.insert(0, SERVERS)
sys.path.insert(0, STUDIO)  # паттерн test_weather_news_day20.py
import _tasks_store  # noqa: E402
import task_create  # noqa: E402
import task_get  # noqa: E402


def call_server(server_file, tool_name, arguments, env_file):
    """Один tools/call через реальный subprocess."""
    m = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": tool_name, "arguments": arguments}}
    data = (json.dumps({"jsonrpc": "2.0", "id": 0, "method":
                        "initialize"}, ensure_ascii=False) + "\n"
            + json.dumps(m, ensure_ascii=False) + "\n")
    e = dict(os.environ)
    e["TASKS_FILE"] = env_file
    p = subprocess.run([sys.executable, os.path.join(SERVERS, server_file)],
                       input=data, capture_output=True, text=True,
                       timeout=60, cwd=REPO, env=e)
    assert p.returncode == 0, p.stderr
    lines = [json.loads(l) for l in p.stdout.splitlines() if l.strip()]
    return lines[-1]["result"]


def test_store_seeds_on_missing_file(tmp_path, monkeypatch):
    f = str(tmp_path / "tasks.json")
    monkeypatch.setenv("TASKS_FILE", f)
    data = _tasks_store.load()
    assert set(data["tasks"]) == {"TASK-42", "TASK-7"}
    assert data["tasks"]["TASK-42"]["status"] == "in_progress"
    assert data["tasks"]["TASK-42"]["assignee"] == "migor"
    assert data["tasks"]["TASK-7"]["status"] == "done"
    assert os.path.exists(f)  # сид записан на диск


def test_store_next_id(tmp_path, monkeypatch):
    monkeypatch.setenv("TASKS_FILE", str(tmp_path / "tasks.json"))
    data = _tasks_store.load()
    assert _tasks_store.next_id(data) == "TASK-43"
    data["tasks"]["TASK-43"] = {"id": "TASK-43"}
    _tasks_store.save(data)
    assert _tasks_store.next_id(_tasks_store.load()) == "TASK-44"


def test_store_corrupt_file_reseeds(tmp_path, monkeypatch):
    f = tmp_path / "tasks.json"
    f.write_text("не json", encoding="utf-8")
    monkeypatch.setenv("TASKS_FILE", str(f))
    data = _tasks_store.load()
    assert set(data["tasks"]) == {"TASK-42", "TASK-7"}


def test_create_task_requires_title():
    payload, is_err = task_create.call({})
    assert is_err is True
    assert "title" in payload["error"]


def test_create_task_shape():
    payload, is_err = task_create.call({"title": "Новая", "description": "о"})
    assert is_err is False
    assert payload == {"id": "TASK-43", "title": "Новая",
                       "description": "о", "status": "todo",
                       "assignee": None}


def test_get_task_not_found():
    payload, is_err = task_get.call({"task_id": "TASK-999"})
    assert is_err is True
    assert payload["error"] == "Задача не найдена: TASK-999"


def test_cross_process_create_then_get(tmp_path, monkeypatch):
    f = str(tmp_path / "tasks.json")
    monkeypatch.setenv("TASKS_FILE", f)
    r = call_server("task_create.py", "create_task",
                    {"title": "E2E кросс-процесс"}, f)
    assert "isError" not in r
    created = json.loads(r["content"][0]["text"])
    assert created["id"] == "TASK-43"
    r2 = call_server("task_get.py", "get_task_details",
                     {"task_id": created["id"]}, f)
    assert "isError" not in r2
    assert json.loads(r2["content"][0]["text"])["title"] == "E2E кросс-процесс"
