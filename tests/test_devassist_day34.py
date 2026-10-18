"""Офлайн-тесты дня 34: файловый ассистент (без сети и LLM)."""
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (REPO, os.path.join(REPO, "tools"),
           os.path.join(REPO, "tools", "mcp_servers")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from devassist.agent import DevAgent  # noqa: E402
from devassist.bridge import FileTools  # noqa: E402

FIXTURE = {
    "pkg/core.py": "class Greeter:\n    def hello(self):\n        return 'hi'\n",
    "pkg/app.py": ("from pkg.core import Greeter\n\n"
                   "def main():\n    Greeter().hello()\n"),
    "pkg/__init__.py": '"""pkg."""\n',
    "README.md": "# Fixture\n\n| День | Задача |\n| --- | --- |\n"
                 "| День 34 | files |\n\n## День 34\n\nok\n",
    "RELEASE.md": "# Release\n\nv1\n",
    ".env": "API_KEY=secret\n",
}


@pytest.fixture()
def root(tmp_path, monkeypatch):
    for path, text in FIXTURE.items():
        full = tmp_path / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(text, encoding="utf-8")
    monkeypatch.setenv("FS_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("FS_RUN_ID", "pytest")
    return tmp_path


def make_agent(root, run_id="pytest"):
    tools = FileTools(env={**os.environ, "FS_WORKSPACE": str(root)})
    agent = DevAgent(llm=None, tools=tools, run_id=run_id)
    return agent


def test_usage_search_counts_files(root):
    agent = make_agent(root)
    try:
        out = agent.run('Найди все места, где используется «Greeter»')
        assert agent.trace[-1]["tool"] == "search_files"
        assert set(agent.trace[-1]["payload"]["by_file"]) >= {
            "pkg/core.py", "pkg/app.py"}
        assert "вхождений" in out["answer"]
        assert not agent.changes
    finally:
        agent.tools.close()


def test_docs_generation_reads_and_writes(root):
    agent = make_agent(root)
    try:
        agent.run("Обнови документацию на основе Greeter")
        tools = [r["tool"] for r in agent.trace]
        assert tools[0] == "search_files"
        assert tools[-1] == "write_file"
        assert tools.count("read_file") >= 2
        doc = (root / "docs" / "day34" / "greeter.md").read_text(encoding="utf-8")
        assert "pkg/app.py" in doc and "ADR" in doc
    finally:
        agent.tools.close()


def test_changelog_and_diff(root):
    agent = make_agent(root)
    try:
        agent.run("Сгенерируй changelog")
        assert (root / "docs" / "CHANGELOG_day34.md").is_file()
        diff = agent.diff()["payload"]
        assert diff["changed"] == 1
        assert diff["files"][0]["status"] == "added"
        out = agent.run("Подготовь diff по изменениям")
        assert "CHANGELOG" in out["answer"] or "Изменено" in out["answer"]
    finally:
        agent.tools.close()


def test_sandbox_rejects_outside_and_env(root):
    agent = make_agent(root)
    try:
        rec = agent.read_file("../outside.txt")
        assert not rec["ok"]
        rec = agent.read_file(".env")
        assert not rec["ok"]
    finally:
        agent.tools.close()


def test_write_file_is_atomic_with_backup(root):
    agent = make_agent(root)
    try:
        agent.write_file("README.md", "# Changed\n")
        payload = agent.write_file("README.md", "# Changed 2\n")["payload"]
        assert payload["status"] == "updated"
        assert payload["backup"].startswith(".devassist/backup/pytest/")
        rec = agent._call("write_file", {"path": "README.md", "content": "x",
                                         "expected_old": "not the content"})
        assert not rec["ok"]
        assert (root / "README.md").read_text(encoding="utf-8") == "# Changed 2\n"
    finally:
        agent.tools.close()


def test_guardrails_flags_stdout_in_library(root):
    lib = root / "devassist" / "lib.py"
    lib.parent.mkdir(parents=True, exist_ok=True)
    lib.write_text('"""lib."""\n\nprint("x")\n', encoding="utf-8")
    from devassist import guardrails
    report = guardrails.check_all()
    rules = {(v["rule"], v["path"]) for v in report["violations"]}
    assert ("stdout", "devassist/lib.py") in rules
