"""День 19: pipeline_tools (реальный subprocess MCP) + пятый дефолт
реестра. Офлайн: search/summarize/saveToFile — только локальные файлы
(tmp-каталоги), сеть не используется."""
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))  # studio/backend/
sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")))  # studio/
sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "mcp_servers")))

import pytest  # noqa: E402

import pdf_writer  # noqa: E402
from mcp import MCPClient, MCPRegistry  # noqa: E402
from memory import MemoryStore  # noqa: E402
from test_mcp import make_fake_launcher  # noqa: E402

PIPELINE_PATH = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "mcp_servers",
    "pipeline_tools.py"))

SUM_TEXT = ("Мастерская строки. Мастерская строки и расчёта. "
            "Погода сегодня дождливая. Расчёт завершён. "
            "Финальный отчёт готов к отправке.")


def _digest(title, summary, city, gen, news):
    return {"id": "digest-test-" + title, "generated_at": gen,
            "weather": {"city": city, "temp_c": 20.0, "feels_like_c": 18.0,
                        "description": "Ясно", "wind_ms": 2.0},
            "news": news, "summary": summary}


def _seed_search_dir(tmp_path):
    """2 JSON-файла по схеме collector.py (кириллица, разные
    generated_at): last-digest.json (d1) + history.json (d1, d2)."""
    d = tmp_path / "search"
    d.mkdir()
    d1 = _digest("MCP", "Сводка MCP: погода Самара, новости.", "Самара",
                 "2026-09-20T06:00:00Z",
                 {"vc.ru": [{"title": "Запуск MCP-сервера",
                             "url": "https://vc.ru/mcp-1"}],
                  "habr": [{"title": "Кириллица в PDF",
                            "url": "https://habr.com/pdf-ru"}]})
    d2 = _digest("LLM", "Сводка LLM: новая модель.", "Москва",
                 "2026-09-21T06:00:00Z",
                 {"tproger": [{"title": "Модель дня",
                               "url": "https://tproger.ru/model"}]})
    (d / "last-digest.json").write_text(
        json.dumps(d1, ensure_ascii=False), encoding="utf-8")
    (d / "history.json").write_text(
        json.dumps([d1, d2], ensure_ascii=False), encoding="utf-8")
    return d


def _client(tmp_path, search_dir):
    """Клиент на реальный stdio-процесс pipeline_tools.py (env -> tmp)."""
    env = {"PIPELINE_SEARCH_DIR": str(search_dir),
           "PIPELINE_OUT_DIR": str(tmp_path / "out")}
    server = {"id": "mcp_pl", "name": "Pipeline Tools", "type": "stdio",
              "command": [sys.executable, PIPELINE_PATH], "url": "",
              "env": env, "enabled": True}
    return MCPClient(server, timeout=90)


def test_pl_connect_tools_exactly_3(tmp_path):
    _seed_search_dir(tmp_path)
    c = _client(tmp_path, tmp_path / "search")
    try:
        tools = c.connect()
        assert [t["name"] for t in tools] == ["search", "summarize",
                                              "saveToFile"]
        assert all(t["input_schema"]["type"] == "object" for t in tools)
        assert all(t["description"] for t in tools)
    finally:
        c.close()


def test_pl_search_hit_sorted_desc(tmp_path):
    _seed_search_dir(tmp_path)
    c = _client(tmp_path, tmp_path / "search")
    c.connect()
    try:
        r = c.call_tool("search", {"query": "сводка"})
        assert r["isError"] is False
        p = json.loads(r["content"][0]["text"])
        assert p["query"] == "сводка"
        # d1 (сводка) в 2 файлах + d2 (сводка) в history.json = 3
        assert p["count"] == 3
        assert len(p["matches"]) == 3
        # сортировка: свежие (generated_at desc) в начале
        assert p["matches"][0]["generated_at"] == "2026-09-21T06:00:00Z"
        assert p["matches"][-1]["generated_at"] == "2026-09-20T06:00:00Z"
        assert p["matches"][0]["title"] == "digest-test-LLM"
        assert p["matches"][0]["city"] == "Москва"
        assert {"title", "summary", "city", "generated_at", "file"} <= set(
            p["matches"][0])
    finally:
        c.close()


def test_pl_search_returns_ready_text_field(tmp_path):
    """День 19: result search contains ready-made field text (multi-line,
    includes the titles of the matched entries) — designed to be copied
    directly into summarize.text / saveToFile.content; count/matches are
    unchanged."""
    _seed_search_dir(tmp_path)
    c = _client(tmp_path, tmp_path / "search")
    c.connect()
    try:
        r = c.call_tool("search", {"query": "сводка"})
        assert r["isError"] is False
        p = json.loads(r["content"][0]["text"])
        # count/matches — как раньше
        assert p["count"] == 3
        assert len(p["matches"]) == 3
        # новое поле text: непустая строка, по строке на совпадение
        assert isinstance(p["text"], str) and p["text"].strip()
        assert len(p["text"].splitlines()) == 3
        # строки содержат заголовки найденных записей и города
        assert "digest-test-LLM" in p["text"]
        assert "digest-test-MCP" in p["text"]
        assert "Москва" in p["text"]
        assert "Самара" in p["text"]
    finally:
        c.close()


def test_pl_search_no_match_is_error(tmp_path):
    _seed_search_dir(tmp_path)
    c = _client(tmp_path, tmp_path / "search")
    c.connect()
    try:
        r = c.call_tool("search", {"query": "zzz-нет-такого-abc"})
        assert r["isError"] is True
        p = json.loads(r["content"][0]["text"])
        assert "no matches" in p["error"]
    finally:
        c.close()


def test_pl_search_empty_dir_is_error(tmp_path):
    (tmp_path / "empty").mkdir()
    c = _client(tmp_path, tmp_path / "empty")
    c.connect()
    try:
        r = c.call_tool("search", {"query": "что угодно"})
        assert r["isError"] is True
    finally:
        c.close()


def test_pl_summarize_points_subset(tmp_path):
    _seed_search_dir(tmp_path)
    c = _client(tmp_path, tmp_path / "search")
    c.connect()
    try:
        r = c.call_tool("summarize", {"text": SUM_TEXT, "max_points": 3})
        assert r["isError"] is False
        p = json.loads(r["content"][0]["text"])
        assert p["input_chars"] == len(SUM_TEXT)
        assert p["output_chars"] == len(p["summary"])
        assert len(p["points"]) == 3
        # топ-по-частоте: «Мастерская строки и расчёта.» (мастерская+строки x2)
        assert p["points"][0] == "Мастерская строки и расчёта."
        assert p["summary"].startswith("• ")
        for pt in p["points"]:
            assert pt in SUM_TEXT  # подмножество исходных предложений
            assert pt in p["summary"]
    finally:
        c.close()


def test_pl_summarize_empty_is_error(tmp_path):
    _seed_search_dir(tmp_path)
    c = _client(tmp_path, tmp_path / "search")
    c.connect()
    try:
        r = c.call_tool("summarize", {"text": "   "})
        assert r["isError"] is True
    finally:
        c.close()


def test_pl_save_md_exact_content(tmp_path):
    _seed_search_dir(tmp_path)
    c = _client(tmp_path, tmp_path / "search")
    c.connect()
    try:
        content = "Привет, мир!\nВторая строка."
        r = c.call_tool("saveToFile", {"filename": "note.md",
                                       "content": content, "format": "md"})
        assert r["isError"] is False
        p = json.loads(r["content"][0]["text"])
        assert p["format"] == "md"
        assert p["size"] == len(content.encode("utf-8"))
        assert p["path"].startswith(os.path.abspath(str(tmp_path / "out")))
        with open(p["path"], encoding="utf-8") as f:
            assert f.read() == content
    finally:
        c.close()


def test_pl_save_json_roundtrip(tmp_path):
    _seed_search_dir(tmp_path)
    c = _client(tmp_path, tmp_path / "search")
    c.connect()
    try:
        content = '{"вложенное": "значение"}'
        r = c.call_tool("saveToFile", {"filename": "data",
                                       "content": content,
                                       "format": "json"})
        assert r["isError"] is False
        p = json.loads(r["content"][0]["text"])
        with open(p["path"], encoding="utf-8") as f:
            assert json.load(f) == content
    finally:
        c.close()


def test_pl_save_pdf_header(tmp_path, monkeypatch):
    monkeypatch.delenv("PIPELINE_FONT_PATH", raising=False)
    if pdf_writer.find_default_font() is None:
        pytest.skip("системный TTF не найден (arial/segoe/tahoma)")
    _seed_search_dir(tmp_path)
    c = _client(tmp_path, tmp_path / "search")
    c.connect()
    try:
        r = c.call_tool("saveToFile", {"filename": "doc",
                                       "content": "Кириллица в PDF.",
                                       "format": "pdf"})
        assert r["isError"] is False
        p = json.loads(r["content"][0]["text"])
        assert p["path"].endswith(".pdf")
        with open(p["path"], "rb") as f:
            blob = f.read()
        assert blob.startswith(b"%PDF-1.4")
        assert p["size"] == len(blob)
    finally:
        c.close()


def test_pl_save_path_traversal_stays_inside(tmp_path):
    _seed_search_dir(tmp_path)
    c = _client(tmp_path, tmp_path / "search")
    c.connect()
    try:
        r = c.call_tool("saveToFile", {"filename": "../../evil.txt",
                                       "content": "x"})
        assert r["isError"] is False
        p = json.loads(r["content"][0]["text"])
        out_abs = os.path.abspath(str(tmp_path / "out"))
        assert os.path.dirname(p["path"]) == out_abs
        assert (tmp_path / "out" / "evil.txt").exists()
        assert not (tmp_path / "evil.txt").exists()
        assert not (tmp_path / ".." / "evil.txt").exists()
    finally:
        c.close()


def test_pl_unknown_tool_is_error(tmp_path):
    _seed_search_dir(tmp_path)
    c = _client(tmp_path, tmp_path / "search")
    c.connect()
    try:
        r = c.call_tool("nope", {})
        assert r["isError"] is True
    finally:
        c.close()


def test_registry_pipeline_tools_default_command(tmp_path):
    # Копируем реальные pipeline_tools.py + pdf_writer.py в tmp-раскладку
    # <root>/studio/{data,mcp_servers}, чтобы путь дефолта существовал
    # (тот же приём, что test_registry_news_weather_default_command).
    (tmp_path / "studio" / "data").mkdir(parents=True)
    (tmp_path / "studio" / "mcp_servers").mkdir(parents=True)
    shutil.copyfile(PIPELINE_PATH, tmp_path / "studio" / "mcp_servers"
                    / "pipeline_tools.py")
    shutil.copyfile(os.path.join(os.path.dirname(PIPELINE_PATH),
                                 "pdf_writer.py"),
                    tmp_path / "studio" / "mcp_servers" / "pdf_writer.py")
    reg = MCPRegistry(MemoryStore(str(tmp_path / "studio" / "data")),
                      launcher=make_fake_launcher())
    try:
        servers = {s["name"]: s for s in reg.servers()}
        pl = servers["Pipeline Tools"]
        assert pl["type"] == "stdio"
        assert pl["enabled"] is True
        assert pl["env"] == {}
        assert pl["command"][0].lower().endswith(
            os.path.basename(sys.executable).lower())
        assert pl["command"][1].endswith("pipeline_tools.py")
        assert os.path.exists(pl["command"][1])
        assert [s["name"] for s in reg.servers()] == [
            "Firecrawl", "Git", "Task Manager", "News & Weather",
            "Pipeline Tools"]
    finally:
        reg.close_all()
