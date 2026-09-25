"""Серверы digest_search/digest_summarize/file_save (день 20):
handlers in-process (tmp-каталоги) + stdio-протокол subprocess.
Алгоритмы — дословный port pipeline_tools.py дня 19."""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
SERVERS = os.path.join(REPO, "studio", "mcp_servers")
sys.path.insert(0, SERVERS)
import digest_search  # noqa: E402
import digest_summarize  # noqa: E402
import file_save  # noqa: E402

SEED_DIGEST = {
    "id": "digest-20260924-0600",
    "generated_at": "2026-09-24T06:00:00Z",
    "weather": {"city": "Самара", "temp_c": 18.5,
                "description": "Небольшой дождь", "wind_ms": 3.0},
    "news": {"vc.ru": [{"title": "Релиз фреймворка",
                        "url": "https://vc.ru/1"}]},
    "summary": "Погода Самара: +18.5°C, небольшой дождь.",
}


def seed_search_dir(d):
    with open(os.path.join(d, SEED_DIGEST["id"] + ".json"), "w",
              encoding="utf-8") as f:
        json.dump(SEED_DIGEST, f, ensure_ascii=False, indent=2)


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


def test_stdio_tools_list():
    for fname, tool in (("digest_search.py", "search"),
                        ("digest_summarize.py", "summarize"),
                        ("file_save.py", "saveToFile")):
        out = rpc(fname, [{"jsonrpc": "2.0", "id": 1, "method":
                           "tools/list"}])
        assert [t["name"] for t in out[0]["result"]["tools"]] == [tool]


def test_search_finds_and_returns_text(tmp_path, monkeypatch):
    seed_search_dir(str(tmp_path))
    monkeypatch.setenv("PIPELINE_SEARCH_DIR", str(tmp_path))
    payload, is_err = digest_search.call({"query": "погода"})
    assert is_err is False
    assert payload["count"] >= 1
    assert "Самара" in payload["text"]  # город в строке совпадения
    assert payload["matches"][0]["title"] == SEED_DIGEST["id"]


def test_search_no_matches_is_error(tmp_path, monkeypatch):
    seed_search_dir(str(tmp_path))
    monkeypatch.setenv("PIPELINE_SEARCH_DIR", str(tmp_path))
    payload, is_err = digest_search.call({"query": "zzz-нет-такого-zzz"})
    assert is_err is True
    assert "no matches" in payload["error"]


def test_summarize_points_and_chars():
    text = ("Сводка: в Самаре дождь. В Казани снегопад. Транспорт "
            "работает с задержками. Дождь продолжится вечером.")
    payload, is_err = digest_summarize.call({"text": text, "max_points": 2})
    assert is_err is False
    assert len(payload["points"]) == 2
    assert payload["input_chars"] == len(text)
    assert payload["summary"].startswith("• ")


def test_summarize_requires_text():
    payload, is_err = digest_summarize.call({})
    assert is_err is True


def test_save_to_file_md_and_pdf(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPELINE_OUT_DIR", str(tmp_path))
    payload, is_err = file_save.call(
        {"filename": "t.md", "content": "привет", "format": "md"})
    assert is_err is False
    assert os.path.exists(str(tmp_path / "t.md"))
    assert payload["format"] == "md" and payload["size"] > 0
    if os.name == "nt" and os.path.exists(r"C:\Windows\Fonts\arial.ttf"):
        p2, e2 = file_save.call(
            {"filename": "r.pdf", "content": "Кириллица в PDF",
             "format": "pdf"})
        assert e2 is False
        with open(p2["path"], "rb") as f:
            assert f.read(8).startswith(b"%PDF-1.4")


def test_save_to_file_traversal_sanitized(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPELINE_OUT_DIR", str(tmp_path))
    payload, is_err = file_save.call(
        {"filename": "../evil.md", "content": "x", "format": "md"})
    assert is_err is False
    assert payload["path"].startswith(os.path.abspath(str(tmp_path)))
