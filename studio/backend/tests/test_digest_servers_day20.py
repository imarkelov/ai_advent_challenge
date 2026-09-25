"""Серверы digest_make / digest_read (день 20). Без сети: collector
патчится in-process; локальный файл — в tmp; GitHub — патчится."""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
STUDIO = os.path.join(REPO, "studio")
SERVERS = os.path.join(REPO, "studio", "mcp_servers")
sys.path.insert(0, SERVERS)
sys.path.insert(0, STUDIO)  # collector.py живёт в studio/
import collector  # noqa: E402
import digest_make  # noqa: E402
import digest_read  # noqa: E402


def rpc(server_file, messages, env=None):
    e = dict(os.environ)
    if env:
        e.update(env)
    data = "\n".join(json.dumps(m, ensure_ascii=False) for m in messages) + "\n"
    p = subprocess.run([sys.executable, os.path.join(SERVERS, server_file)],
                       input=data, capture_output=True, text=True,
                       timeout=60, cwd=REPO, env=e)
    assert p.returncode == 0, p.stderr
    return [json.loads(l) for l in p.stdout.splitlines() if l.strip()]


def test_stdio_tools_list(tmp_path):
    env = {"DIGEST_DATA_DIR": str(tmp_path)}
    for fname, tool in (("digest_make.py", "make_digest"),
                        ("digest_read.py", "get_latest_digest")):
        out = rpc(fname, [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}], env=env)
        assert [t["name"] for t in out[1]["result"]["tools"]] == [tool]


def test_make_digest_collects_and_saves(tmp_path, monkeypatch):
    monkeypatch.setenv("DIGEST_DATA_DIR", str(tmp_path))
    digest = {"id": "digest-x", "generated_at": "2026-09-25T00:00:00Z",
              "weather": {"city": "Самара"}, "news": {}, "summary": "сводка"}
    monkeypatch.setattr(collector, "collect_digest",
                        lambda city=..., fetch_weather=None,
                        fetch_news=None, sources=None, **kw: digest)
    saved = {}
    monkeypatch.setattr(collector, "save_digest",
                        lambda d, data_dir=None: saved.setdefault("d", d))
    payload, is_err = digest_make.call({"city": "Самара"})
    assert is_err is False
    assert payload["id"] == "digest-x"
    assert saved["d"] is digest


def test_read_local(tmp_path, monkeypatch):
    d = {"id": "digest-l", "generated_at": "2026-09-25T01:00:00Z"}
    (tmp_path / "last-digest.json").write_text(
        json.dumps(d, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("DIGEST_DATA_DIR", str(tmp_path))
    payload, is_err = digest_read.call({})
    assert is_err is False
    assert payload == {"source": "local",
                       "generated_at": "2026-09-25T01:00:00Z",
                       "digest": d}


def test_read_github_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("DIGEST_DATA_DIR", str(tmp_path))  # пусто
    d = {"id": "digest-g", "generated_at": "2026-09-25T02:00:00Z"}
    monkeypatch.setattr(digest_read, "read_github", lambda: d)
    payload, is_err = digest_read.call({})
    assert is_err is False
    assert payload["source"] == "github"
    assert payload["digest"]["id"] == "digest-g"


def test_read_both_fail_is_error(tmp_path, monkeypatch):
    monkeypatch.setenv("DIGEST_DATA_DIR", str(tmp_path))  # пусто

    def boom():
        raise OSError("нет сети")
    monkeypatch.setattr(digest_read, "read_github", boom)
    payload, is_err = digest_read.call({})
    assert is_err is True
    assert "Дайджест недоступен" in payload["error"]
