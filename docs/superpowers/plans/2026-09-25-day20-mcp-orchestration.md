# День 20: Orchestration MCP — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 10 едицельных локальных MCP-серверов (1 сервер = 1 тул) + агентская маршрутизация (always-префикс `{server}__{tool}` + каталог серверов в system-промпте) + длинный e2e-флоу из 10 вызовов через все 10 серверов.

**Architecture:** Каждый локальный MCP-сервер — отдельный python-процесс (stdio JSON-RPC 2.0, только stdlib) на общем каркасе `_mcp_base.py`. Агент (`agent.py`) отдаёт модели ВСЕГДА префиксованные имена инструментов (`{slug(сервер)}__{тул}`) и каталог «сервер: инструменты» в system-промпте; имя, которое модель видит, — то же, что сохраняется в диалоге (routing proof). Кап tool-loop: 15 (env `TOOL_LOOP_CAP`). Реестр: 12 дефолтов (Firecrawl, Git + 10 локальных) с миграцией старых мультитул-серверов.

**Tech Stack:** Python 3 stdlib (серверы, e2e), FastAPI/httpx (бэкенд, без новых зависимостей), React 19 + Vitest (фронтенд, без новых зависимостей).

**Spec:** `docs/superpowers/specs/2026-09-25-day20-mcp-orchestration-design.md`

## Global Constraints

- Ветка: `day20-mcp-orchestration` (создана; от `day19-mcp-pipeline`).
- 1 сервер = ровно 1 тул; функций-дублей между серверами нет (spec §4.1).
- Только stdlib в `studio/mcp_servers/*` — никаких новых pip/npm-зависимостей.
- Протокол: stdio newline JSON-RPC 2.0, protocolVersion `2024-11-05`, serverInfo `{name: "<slug-сервера>", version: "1.0"}`.
- Slug: `re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")`.
- Имя инструмента для LLM — ВСЕГДА `{slug}__{тул}` (никогда без префикса), даже если коллизий нет.
- Сохраняемые в диалоге имена (assistant `tool_calls` и `role: "tool"` `name`) = LLM-имя (префиксованное); в MCP-вызов уходит реальное имя тула.
- Кап tool-loop: 15 итераций, env `TOOL_LOOP_CAP` (читается на каждый ход).
- Дефолты реестра: ровно 12 (Firecrawl, Git + 10 локальных).
- E2E порт: **8104** (8101/8102/8103 — дни 17/18/19).
- Все пользовательские тексты и ошибки — на русском.
- Тесты бэкенда — офлайн (tmp_path + monkeypatch + реальные subprocess серверов); Part A e2e — детерминированный, MUST PASS без сети.
- Запуск тестов: из `studio/backend` — `python -m pytest -q`.

### Имена серверов и инструментов (канонический список)

| # | сервер (имя в реестре = slug) | файл | тул | аргументы |
|---|---|---|---|---|
| 1 | `weather` | `weather.py` | `get_weather` | `city?` (default Самара) |
| 2 | `news` | `news.py` | `get_news` | `sources?` (массив; default все: vc.ru, habr, tproger) |
| 3 | `digest_make` | `digest_make.py` | `make_digest` | `city?` |
| 4 | `digest_read` | `digest_read.py` | `get_latest_digest` | — |
| 5 | `task_create` | `task_create.py` | `create_task` | `title` (required), `description?` |
| 6 | `task_get` | `task_get.py` | `get_task_details` | `task_id` (required) |
| 7 | `digest_search` | `digest_search.py` | `search` | `query` (required) |
| 8 | `digest_summarize` | `digest_summarize.py` | `summarize` | `text` (required), `max_points?`=8 (1..20) |
| 9 | `file_save` | `file_save.py` | `saveToFile` | `filename`, `content` (required), `format?`=md (md\|txt\|json\|pdf) |
| 10 | `habr_news` | `habr_news.py` | `get_habr_news` | `topics?` (testing\|ai, default оба), `limit?`=10 |

LLM-имена: `weather__get_weather`, `news__get_news`, `digest_make__make_digest`, `digest_read__get_latest_digest`, `task_create__create_task`, `task_get__get_task_details`, `digest_search__search`, `digest_summarize__summarize`, `file_save__saveToFile`, `habr_news__get_habr_news`.

### Файлы (сводка)

- Create: `studio/mcp_servers/_mcp_base.py`, `weather.py`, `news.py`, `digest_make.py`, `digest_read.py`, `_tasks_store.py`, `task_create.py`, `task_get.py`, `digest_search.py`, `digest_summarize.py`, `file_save.py`, `habr_news.py`
- Create (тесты): `studio/backend/tests/test_mcp_base.py`, `test_weather_news_day20.py`, `test_digest_servers_day20.py`, `test_tasks_day20.py`, `test_pipeline_split_day20.py`, `test_habr_news_day20.py`, `test_registry_day20.py`, `test_agent_day20.py`
- Modify: `studio/backend/mcp.py`, `studio/backend/agent.py`, `studio/frontend/src/components/ChatPanel.tsx`
- Modify (тесты): `studio/backend/tests/test_mcp.py`, `test_agent.py`, `studio/frontend/tests/chat-panel.test.tsx`
- Modify (e2e): `scripts/e2e_day17.py`, `scripts/e2e_day18.py`, `scripts/e2e_day19.py`
- Create: `scripts/e2e_day20.py`
- Delete (после Task 9): `studio/mcp_servers/news_weather.py`, `task_manager.py`, `pipeline_tools.py` + их старые тест-файлы
- Docs: `openspec/changes/day20-mcp-orchestration/` (структура как у `openspec/changes/day19-mcp-pipeline/`), `README.md`

---

### Task 1: Общий каркас `_mcp_base.py`

**Files:**
- Create: `studio/mcp_servers/_mcp_base.py`
- Test: `studio/backend/tests/test_mcp_base.py`

**Interfaces:**
- Consumes: ничего.
- Produces: `make_server(server_name: str, tool: dict, call_handler) -> fn(dict) -> dict|None`; `run_server(server_name, tool, call_handler) -> None`; контракт `call_handler(args: dict) -> (payload: dict, is_error: bool)`. Все серверы дня 20 строятся на нём.

- [ ] **Step 1: Write the failing tests**

`studio/backend/tests/test_mcp_base.py`:

```python
"""Тесты общего каркаса MCP-серверов (день 20)."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(REPO, "studio", "mcp_servers"))
import _mcp_base  # noqa: E402


def _srv(handler=None):
    tool = {"name": "do_thing", "description": "d",
            "input_schema": {"type": "object"}}
    if handler is None:
        handler = lambda args: ({"echo": args}, False)
    return _mcp_base.make_server("test-server", tool, handler)


def test_initialize_protocol_and_serverinfo():
    r = _srv()({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert r["result"]["protocolVersion"] == "2024-11-05"
    assert r["result"]["serverInfo"] == {"name": "test-server",
                                         "version": "1.0"}


def test_tools_list_single_tool():
    r = _srv()({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert [t["name"] for t in r["result"]["tools"]] == ["do_thing"]


def test_tools_call_ok_returns_json_text():
    r = _srv()({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "do_thing", "arguments": {"x": 1}}})
    assert "isError" not in r["result"]
    assert json.loads(r["result"]["content"][0]["text"]) == {"echo": {"x": 1}}


def test_tools_call_error_flag():
    tool = {"name": "t", "description": "", "input_schema": {"type": "object"}}
    h = _mcp_base.make_server("s", tool, lambda a: ({"error": "нет"}, True))
    r = h({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
           "params": {"name": "t", "arguments": {}}})
    assert r["result"]["isError"] is True
    assert json.loads(r["result"]["content"][0]["text"]) == {"error": "нет"}


def test_unknown_tool_name_is_error():
    r = _srv()({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                "params": {"name": "other", "arguments": {}}})
    assert r["result"]["isError"] is True


def test_unknown_method_32601():
    r = _srv()({"jsonrpc": "2.0", "id": 6, "method": "nope"})
    assert r["error"]["code"] == -32601


def test_notification_no_response():
    assert _srv()({"jsonrpc": "2.0",
                   "method": "notifications/initialized"}) is None


def test_handler_exception_wrapped_is_error():
    tool = {"name": "t", "description": "", "input_schema": {"type": "object"}}

    def boom(args):
        raise RuntimeError("упало")

    h = _mcp_base.make_server("s", tool, boom)
    r = h({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
           "params": {"name": "t", "arguments": {}}})
    assert r["result"]["isError"] is True
    assert "Внутренняя ошибка" in r["result"]["content"][0]["text"]
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `cd studio/backend && python -m pytest tests/test_mcp_base.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named '_mcp_base'`

- [ ] **Step 3: Implement `_mcp_base.py`**

`studio/mcp_servers/_mcp_base.py`:

```python
"""Общий каркас едицельных MCP-серверов (день 20).

stdio JSON-RPC 2.0 (protocolVersion 2024-11-05), один тул на сервер.
Паттерн task_manager.py (день 17). Только stdlib.

Структура сервера:
    TOOL = {"name": ..., "description": ..., "input_schema": {...}}

    def call(args: dict) -> tuple:      # (payload: dict, is_error: bool)
        ...

    if __name__ == "__main__":
        run_server("<имя-сервера>", TOOL, call)
"""
import json
import sys

PROTOCOL = "2024-11-05"


def _ok(m, result):
    return {"jsonrpc": "2.0", "id": m.get("id"), "result": result}


def make_server(server_name, tool, call_handler):
    """Возвращает fn(m: dict) -> dict | None (JSON-RPC-ответ; None —
    на notification). call_handler(arguments: dict) -> (payload, is_error)."""

    def handle(m):
        if "id" not in m:  # notification — без ответа
            return None
        method = m.get("method")
        if method == "initialize":
            return _ok(m, {"protocolVersion": PROTOCOL,
                           "serverInfo": {"name": server_name,
                                          "version": "1.0"},
                           "capabilities": {"tools": {}}})
        if method == "tools/list":
            return _ok(m, {"tools": [tool]})
        if method == "tools/call":
            params = m.get("params") or {}
            if params.get("name") != tool["name"]:
                return _ok(m, {"content": [{"type": "text", "text":
                            json.dumps({"error": "Инструмент «%s» не найден"
                                          % params.get("name")},
                                       ensure_ascii=False)}],
                              "isError": True})
            try:
                payload, is_err = call_handler(params.get("arguments") or {})
            except Exception as e:
                payload, is_err = {"error": "Внутренняя ошибка: " + str(e)}, True
            result = {"content": [{"type": "text", "text":
                                   json.dumps(payload, ensure_ascii=False,
                                              indent=2)}]}
            if is_err:
                result["isError"] = True
            return _ok(m, result)
        return {"jsonrpc": "2.0", "id": m.get("id"),
                "error": {"code": -32601,
                          "message": "Неизвестный метод: " + str(method)}}

    return handle


def run_server(server_name, tool, call_handler) -> None:
    """stdio-цикл: одна JSON-строка из stdin -> одна JSON-строка в stdout."""
    handle = make_server(server_name, tool, call_handler)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            m = json.loads(line)
        except ValueError:
            sys.stdout.write(json.dumps(
                {"jsonrpc": "2.0", "id": None,
                 "error": {"code": -32700, "message": "Parse error"}},
                ensure_ascii=False) + "\n")
            sys.stdout.flush()
            continue
        resp = handle(m)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `cd studio/backend && python -m pytest tests/test_mcp_base.py -q`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add studio/mcp_servers/_mcp_base.py studio/backend/tests/test_mcp_base.py
git commit -m "feat(day20): _mcp_base — shared single-tool stdio MCP server skeleton"
```

---

### Task 2: Серверы `weather` + `news`

**Files:**
- Create: `studio/mcp_servers/weather.py`, `studio/mcp_servers/news.py`
- Test: `studio/backend/tests/test_weather_news_day20.py`

**Interfaces:**
- Consumes: `_mcp_base.run_server`; `collector.fetch_weather(city)`, `collector.fetch_news(source)`, `collector.NEWS_FEEDS`, `collector.DEFAULT_CITY` (публичные алиасы, патч-абельны).
- Produces: два stdio-процесса с тулами `get_weather` / `get_news` (схемы из канонической таблицы).

- [ ] **Step 1: Write the failing tests**

`studio/backend/tests/test_weather_news_day20.py`:

```python
"""Серверы weather и news (день 20): handlers in-process + stdio-протокол
на реальных subprocess. Без сети: fetch'еры патчатся."""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
SERVERS = os.path.join(REPO, "studio", "mcp_servers")
sys.path.insert(0, SERVERS)
import collector  # noqa: E402
import news  # noqa: E402
import weather  # noqa: E402


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


def test_weather_stdio_protocol():
    out = rpc("weather.py", [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}])
    assert out[0]["result"]["serverInfo"] == {"name": "weather",
                                              "version": "1.0"}
    tools = out[1]["result"]["tools"]
    assert [t["name"] for t in tools] == ["get_weather"]
    assert tools[0]["input_schema"]["properties"].get("city")


def test_news_stdio_protocol():
    out = rpc("news.py", [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}])
    assert out[0]["result"]["serverInfo"]["name"] == "news"
    assert [t["name"] for t in out[1]["result"]["tools"]] == ["get_news"]


def test_weather_call_ok(monkeypatch):
    monkeypatch.setattr(collector, "fetch_weather",
                        lambda city: {"city": city, "temp_c": 18.5,
                                      "feels_like_c": 17.0,
                                      "description": "Ясно", "wind_ms": 2.0})
    payload, is_err = weather.call({"city": "Самара"})
    assert is_err is False
    assert payload["city"] == "Самара" and payload["temp_c"] == 18.5


def test_weather_call_default_city(monkeypatch):
    seen = {}

    def fake(city):
        seen["city"] = city
        return {"city": city}

    monkeypatch.setattr(collector, "fetch_weather", fake)
    weather.call({})
    assert seen["city"] == collector.DEFAULT_CITY


def test_weather_call_network_error(monkeypatch):
    def fake(city):
        raise OSError("нет сети")
    monkeypatch.setattr(collector, "fetch_weather", fake)
    payload, is_err = weather.call({"city": "Самара"})
    assert is_err is True
    assert "Погода недоступна" in payload["error"]


def test_news_call_ok_and_unknown_source(monkeypatch):
    monkeypatch.setattr(collector, "fetch_news",
                        lambda src: [{"title": "t", "url": "u"}])
    payload, is_err = news.call({"sources": ["vc.ru", "unknown.ru"]})
    assert is_err is False
    assert payload["vc.ru"] == [{"title": "t", "url": "u"}]
    assert "Неизвестный source" in payload["unknown.ru"]["error"]


def test_news_call_fetch_error_degrades(monkeypatch):
    def fake(src):
        raise OSError("RSS упал")
    monkeypatch.setattr(collector, "fetch_news", fake)
    payload, is_err = news.call({"sources": ["vc.ru"]})
    assert is_err is False
    assert "RSS упал" in payload["vc.ru"]["error"]
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `cd studio/backend && python -m pytest tests/test_weather_news_day20.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'weather'`

- [ ] **Step 3: Implement both servers**

`studio/mcp_servers/weather.py`:

```python
"""MCP-сервер "weather" (день 20): один тул — get_weather.

Текущая погода по городу (Open-Meteo, без API-ключа). Только stdlib.
Запуск: python studio/mcp_servers/weather.py
"""
import os
import sys

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
STUDIO_DIR = os.path.dirname(SERVER_DIR)
for _p in (SERVER_DIR, STUDIO_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import collector  # noqa: E402
from _mcp_base import run_server  # noqa: E402

TOOL = {
    "name": "get_weather",
    "description": ("Текущая погода по городу (Open-Meteo, без ключа): "
                    "city, temp_c, feels_like_c, description, wind_ms."),
    "input_schema": {"type": "object", "properties": {
        "city": {"type": "string",
                 "description": "Город (дефолт: Самара)"}},
        "required": []},
}


def call(args: dict) -> tuple:
    city = (args or {}).get("city") or collector.DEFAULT_CITY
    try:
        return collector.fetch_weather(city), False
    except Exception as e:
        return {"error": "Погода недоступна: " + str(e)}, True


if __name__ == "__main__":
    run_server("weather", TOOL, call)
```

`studio/mcp_servers/news.py`:

```python
"""MCP-сервер "news" (день 20): один тул — get_news.

Топ-5 новостей RU-tech RSS (vc.ru, habr, tproger), дедуп по URL
(логика collector). Сбой одного source — {"error": ...} у этого source,
остальные не гибнут. Только stdlib.
Запуск: python studio/mcp_servers/news.py
"""
import os
import sys

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
STUDIO_DIR = os.path.dirname(SERVER_DIR)
for _p in (SERVER_DIR, STUDIO_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import collector  # noqa: E402
from _mcp_base import run_server  # noqa: E402

TOOL = {
    "name": "get_news",
    "description": ("Топ-5 новостей RU-tech RSS (vc.ru, habr, tproger), "
                    "дедуп по URL. sources — список (дефолт: все)."),
    "input_schema": {"type": "object", "properties": {
        "sources": {"type": "array", "items": {"type": "string"},
                    "description": "Источники: vc.ru, habr, tproger "
                                   "(дефолт: все)"}},
        "required": []},
}


def call(args: dict) -> tuple:
    srcs = (args or {}).get("sources") or list(collector.NEWS_FEEDS)
    if not isinstance(srcs, list) or not srcs:
        return {"error": "get_news: 'sources' — список строк"}, True
    news = {}
    for src in srcs:
        if src not in collector.NEWS_FEEDS:
            news[src] = {"error": "Неизвестный source: " + str(src)}
            continue
        try:
            news[src] = collector.fetch_news(src)
        except Exception as e:
            news[src] = {"error": str(e)}
    return news, False


if __name__ == "__main__":
    run_server("news", TOOL, call)
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `cd studio/backend && python -m pytest tests/test_weather_news_day20.py -q`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add studio/mcp_servers/weather.py studio/mcp_servers/news.py \
        studio/backend/tests/test_weather_news_day20.py
git commit -m "feat(day20): single-tool MCP servers weather + news"
```

---

### Task 3: Серверы `digest_make` + `digest_read`

**Files:**
- Create: `studio/mcp_servers/digest_make.py`, `studio/mcp_servers/digest_read.py`
- Test: `studio/backend/tests/test_digest_servers_day20.py`

**Interfaces:**
- Consumes: `_mcp_base.run_server`; `collector.collect_digest(city)`, `collector.save_digest(digest, data_dir=None)`, `collector.resolve_data_dir()` (env `DIGEST_DATA_DIR`), `collector.USER_AGENT`.
- Produces: `make_digest` (собирает + сохраняет, возвращает дайджест); `get_latest_digest` (локальный файл → фолбэк GitHub API → isError; результат `{source: local|github, generated_at, digest}`).

- [ ] **Step 1: Write the failing tests**

`studio/backend/tests/test_digest_servers_day20.py`:

```python
"""Серверы digest_make / digest_read (день 20). Без сети: collector
патчится in-process; локальный файл — в tmp; GitHub — патчится."""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
SERVERS = os.path.join(REPO, "studio", "mcp_servers")
sys.path.insert(0, SERVERS)
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
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `cd studio/backend && python -m pytest tests/test_digest_servers_day20.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'digest_make'`

- [ ] **Step 3: Implement both servers**

`studio/mcp_servers/digest_make.py`:

```python
"""MCP-сервер "digest_make" (день 20): один тул — make_digest.

Собирает дайджест (погода + новости, collector.py) и СОХРАНЯЕТ в
data/digests (last-digest.json + history.json, атомарно). Возвращает
собранное. Сбой источника — деградация в поле {"error": ...} (поведение
collector дня 18). Только stdlib.
Запуск: python studio/mcp_servers/digest_make.py
"""
import os
import sys

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
STUDIO_DIR = os.path.dirname(SERVER_DIR)
for _p in (SERVER_DIR, STUDIO_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import collector  # noqa: E402
from _mcp_base import run_server  # noqa: E402

TOOL = {
    "name": "make_digest",
    "description": ("Собрать дайджест (погода + новости vc.ru/habr/tproger) "
                    "и сохранить в data/digests (last-digest.json + "
                    "history.json). Возвращает собранный дайджест."),
    "input_schema": {"type": "object", "properties": {
        "city": {"type": "string",
                 "description": "Город для погоды (дефолт: Самара)"}},
        "required": []},
}


def call(args: dict) -> tuple:
    city = (args or {}).get("city") or collector.DEFAULT_CITY
    digest = collector.collect_digest(city=city)
    collector.save_digest(digest)
    return digest, False


if __name__ == "__main__":
    run_server("digest_make", TOOL, call)
```

`studio/mcp_servers/digest_read.py`:

```python
"""MCP-сервер "digest_read" (день 20): один тул — get_latest_digest.

Последний дайджест: локальный data/digests/last-digest.json -> фолбэк
GitHub API (репозиторий, env DIGEST_GITHUB_REPO). Результат:
{source: local|github, generated_at, digest}. Только stdlib.
Запуск: python studio/mcp_servers/digest_read.py
"""
import base64
import json
import os
import sys
import urllib.request

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
STUDIO_DIR = os.path.dirname(SERVER_DIR)
for _p in (SERVER_DIR, STUDIO_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import collector  # noqa: E402
from _mcp_base import run_server  # noqa: E402

TOOL = {
    "name": "get_latest_digest",
    "description": ("Последний дайджест: локальный "
                    "data/digests/last-digest.json, фолбэк — GitHub API. "
                    "Возвращает {source: local|github, generated_at, "
                    "digest}."),
    "input_schema": {"type": "object", "properties": {}, "required": []},
}


def read_github() -> dict:
    """last-digest.json из GitHub (base64 в meta.contents)."""
    repo = (os.environ.get("DIGEST_GITHUB_REPO")
            or "imarkelov/ai_advent_challenge")
    url = ("https://api.github.com/repos/" + repo
           + "/contents/data/digests/last-digest.json")
    req = urllib.request.Request(
        url, headers={"User-Agent": collector.USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as resp:
        meta = json.loads(resp.read().decode("utf-8"))
    return json.loads(base64.b64decode(meta["content"]))


def call(args: dict) -> tuple:
    try:
        path = os.path.join(collector.resolve_data_dir(),
                            "last-digest.json")
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return {"source": "local", "generated_at": d.get("generated_at"),
                "digest": d}, False
    except Exception:
        pass
    try:
        d = read_github()
        return {"source": "github", "generated_at": d.get("generated_at"),
                "digest": d}, False
    except Exception as e:
        return {"error": "Дайджест недоступен: " + str(e)}, True


if __name__ == "__main__":
    run_server("digest_read", TOOL, call)
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `cd studio/backend && python -m pytest tests/test_digest_servers_day20.py -q`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add studio/mcp_servers/digest_make.py studio/mcp_servers/digest_read.py \
        studio/backend/tests/test_digest_servers_day20.py
git commit -m "feat(day20): single-tool MCP servers digest_make + digest_read"
```

---

### Task 4: File-backed задачи: `_tasks_store` + `task_create` + `task_get`

**Files:**
- Create: `studio/mcp_servers/_tasks_store.py`, `studio/mcp_servers/task_create.py`, `studio/mcp_servers/task_get.py`
- Test: `studio/backend/tests/test_tasks_day20.py`

**Interfaces:**
- Consumes: `_mcp_base.run_server`.
- Produces: `_tasks_store.load() -> dict` (формат `{"tasks": {id: task}}`, seed TASK-42/TASK-7), `_tasks_store.save(data)`, `_tasks_store.next_id(data) -> "TASK-<n>"`, `_tasks_store.tasks_file()` (env `TASKS_FILE`, default `<repo>/data/tasks.json`). Серверы `task_create` / `task_get` — два процесса над одним файлом (in-memory дня 17 не работает: память не общая).

- [ ] **Step 1: Write the failing tests**

`studio/backend/tests/test_tasks_day20.py`:

```python
"""File-backed задачи (день 20): store + серверы task_create/task_get,
включая КРОСС-ПРОЦЕССНЫЙ тест (создание в одном subprocess, чтение в
другом). Без сети."""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
SERVERS = os.path.join(REPO, "studio", "mcp_servers")
sys.path.insert(0, SERVERS)
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
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `cd studio/backend && python -m pytest tests/test_tasks_day20.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named '_tasks_store'`

- [ ] **Step 3: Implement store and both servers**

`studio/mcp_servers/_tasks_store.py`:

```python
"""File-backed хранилище задач (день 20): data/tasks.json.

Общий для task_create и task_get (разные процессы — память не общая,
в отличие от in-memory дня 17). Формат: {"tasks": {task_id: task}}.
Env TASKS_FILE — путь файла (дефолт <repo>/data/tasks.json).
Атомарная запись (tmp + os.replace). Сид — задачи дня 17.
"""
import json
import os

REPO_ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))

SEED = {
    "tasks": {
        "TASK-42": {"id": "TASK-42",
                    "title": "Реализовать MCP tool-loop в агенте",
                    "description": ("LLM-driven tool-calling: tools -> "
                                    "tool_calls -> MCP -> role tool, "
                                    "цикл до 5 итераций"),
                    "status": "in_progress", "assignee": "migor",
                    "updated": "2026-09-23T10:00:00Z"},
        "TASK-7": {"id": "TASK-7",
                   "title": "Подключить Git MCP-сервер",
                   "description": ("Дефолт реестра MCP: "
                                   "npx @cyanheads/git-mcp-server"),
                   "status": "done", "assignee": "migor",
                   "updated": "2026-09-22T18:30:00Z"},
    }
}


def tasks_file() -> str:
    return (os.environ.get("TASKS_FILE")
            or os.path.join(REPO_ROOT, "data", "tasks.json"))


def _atomic_write(path: str, obj: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load() -> dict:
    """Файл -> dict; нет файла или битый JSON -> сид (и запись сида)."""
    path = tasks_file()
    d = None
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                loaded = json.load(f)
            if (isinstance(loaded, dict)
                    and isinstance(loaded.get("tasks"), dict)):
                d = loaded
        except (ValueError, OSError):
            d = None
    if d is None:
        d = json.loads(json.dumps(SEED))  # deep copy
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _atomic_write(path, d)
    return d


def save(data: dict) -> None:
    os.makedirs(os.path.dirname(tasks_file()), exist_ok=True)
    _atomic_write(tasks_file(), data)


def next_id(data: dict) -> str:
    """TASK-<max(числовые id) + 1> (алгоритм _next_task_id дня 17)."""
    mx = 0
    for k in data["tasks"]:
        if k.startswith("TASK-") and k[5:].isdigit():
            mx = max(mx, int(k[5:]))
    return "TASK-" + str(mx + 1)
```

`studio/mcp_servers/task_create.py`:

```python
"""MCP-сервер "task_create" (день 20): один тул — create_task.

Файл-хранилище data/tasks.json (env TASKS_FILE); id = TASK-<n>,
n = max(числовые id) + 1; созданная задача — форма дня 17
(status "todo", assignee null). Только stdlib.
Запуск: python studio/mcp_servers/task_create.py
"""
import os
import sys

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)
import _tasks_store  # noqa: E402
from _mcp_base import run_server  # noqa: E402

TOOL = {
    "name": "create_task",
    "description": ("Создать задачу (файл data/tasks.json). Возвращает "
                    "задачу: id (TASK-N), title, description, status "
                    "'todo', assignee null."),
    "input_schema": {"type": "object", "properties": {
        "title": {"type": "string", "description": "Название задачи"},
        "description": {"type": "string",
                        "description": "Описание задачи"}},
        "required": ["title"]},
}


def call(args: dict) -> tuple:
    title = (args or {}).get("title")
    if not isinstance(title, str) or not title.strip():
        return {"error": "Не задан обязательный аргумент: title"}, True
    data = _tasks_store.load()
    tid = _tasks_store.next_id(data)
    task = {"id": tid, "title": title.strip(),
            "description": (args or {}).get("description") or "",
            "status": "todo", "assignee": None}
    data["tasks"][tid] = task
    _tasks_store.save(data)
    return task, False


if __name__ == "__main__":
    run_server("task_create", TOOL, call)
```

`studio/mcp_servers/task_get.py`:

```python
"""MCP-сервер "task_get" (день 20): один тул — get_task_details.

Чтение задачи из data/tasks.json (env TASKS_FILE). Не найдена —
isError "Задача не найдена: <id>" (текст дня 17). Только stdlib.
Запуск: python studio/mcp_servers/task_get.py
"""
import os
import sys

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)
import _tasks_store  # noqa: E402
from _mcp_base import run_server  # noqa: E402

TOOL = {
    "name": "get_task_details",
    "description": ("Детали задачи по id (файл data/tasks.json): id, "
                    "title, description, status, assignee."),
    "input_schema": {"type": "object", "properties": {
        "task_id": {"type": "string",
                    "description": "Идентификатор задачи (TASK-N)"}},
        "required": ["task_id"]},
}


def call(args: dict) -> tuple:
    tid = (args or {}).get("task_id")
    if not isinstance(tid, str) or not tid.strip():
        return {"error": "Не задан обязательный аргумент: task_id"}, True
    tid = tid.strip()
    task = _tasks_store.load()["tasks"].get(tid)
    if task is None:
        return {"error": "Задача не найдена: " + tid}, True
    return task, False


if __name__ == "__main__":
    run_server("task_get", TOOL, call)
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `cd studio/backend && python -m pytest tests/test_tasks_day20.py -q`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add studio/mcp_servers/_tasks_store.py studio/mcp_servers/task_create.py \
        studio/mcp_servers/task_get.py studio/backend/tests/test_tasks_day20.py
git commit -m "feat(day20): file-backed tasks store + task_create/task_get servers"
```

---

### Task 5: Серверы `digest_search` + `digest_summarize` + `file_save`

**Files:**
- Create: `studio/mcp_servers/digest_search.py`, `studio/mcp_servers/digest_summarize.py`, `studio/mcp_servers/file_save.py`
- Test: `studio/backend/tests/test_pipeline_split_day20.py`

**Interfaces:**
- Consumes: `_mcp_base.run_server`; `pdf_writer.text_to_pdf(content, font_path=...) -> bytes`, `pdf_writer.PdfError` (день 19, не меняется).
- Produces: `search` (port `_call_search` из `pipeline_tools.py` — алгоритм БЕЗ ИЗМЕНЕНИЙ: `_digests_from`, `_flatten_entries`, `_matches_text`, MATCH_CAP=20, поле `text`; нет совпадений → isError `no matches for '<q>'`; env `PIPELINE_SEARCH_DIR`); `summarize` (port `_call_summarize`: STOPWORDS, `_SENT_RE`, `_WORD_RE`, `_split_sentences`, `_extractive_summary`, max_points 1..20 default 8; результат `{summary, points, input_chars, output_chars}`); `saveToFile` (port `_call_save_to_file`: basename-санитизация, `re.sub(r"[^\w.\- ]", "_")`, pdf → +`.pdf`, атомарная запись; env `PIPELINE_OUT_DIR`, `PIPELINE_FONT_PATH`; результат `{path, size, format}`).
- Source для порта: `studio/mcp_servers/pipeline_tools.py` (читается перед удалением в Task 9 — при порте код копируется дословно, меняется только обёртка в `(payload, is_err)`-контракт и заголовок).

- [ ] **Step 1: Write the failing tests**

`studio/backend/tests/test_pipeline_split_day20.py` (покрывает тот же набор, что старые day-19 тесты pipeline_tools; при выполнении Task 9 старые дубли удаляются):

```python
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
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `cd studio/backend && python -m pytest tests/test_pipeline_split_day20.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'digest_search'`

- [ ] **Step 3: Implement the three servers**

Каждый файл — шапка docstring (имя сервера, тул, env, «port из pipeline_tools.py дня 19, алгоритм без изменений»), импорты как в Task 2 (`SERVER_DIR`/`STUDIO_DIR` для file_save — нужен `pdf_writer` из `studio/mcp_servers/`; `digest_summarize` без внешних импортов).

`studio/mcp_servers/digest_search.py` — константы и функции дословно из `pipeline_tools.py`: `REPO_ROOT`, `MATCH_CAP = 20`, `_ENTRY_FIELDS`, `_search_dir()` (env `PIPELINE_SEARCH_DIR`), `_digests_from`, `_flatten_entries`, `_matches_text`; `call(args)` = тело `_call_search`, завершающееся:

```python
    if not matches:
        return {"error": "no matches for '%s'" % q}, True
    matches.sort(key=lambda m: (m["generated_at"], m["title"], m["file"]),
                 reverse=True)
    return {"query": q, "count": len(matches),
            "matches": matches[:MATCH_CAP],
            "text": _matches_text(matches[:MATCH_CAP])}, False
```

TOOL-схема: `{"name": "search", "description": <та же, что у pipeline_tools.TOLS search>, "input_schema": {"type": "object", "properties": {"query": {"type": "string", "description": "Поисковый запрос (substring)"}}, "required": ["query"]}}`; `run_server("digest_search", TOOL, call)`.

`studio/mcp_servers/digest_summarize.py` — `STOPWORDS`, `MIN_WORD = 2`, `_SENT_RE`, `_WORD_RE`, `_split_sentences`, `_extractive_summary` дословно; `call(args)` = тело `_call_summarize` с возвратом `({"summary": ..., "points": ..., "input_chars": ..., "output_chars": ...}, False)` / `({"error": ...}, True)`. TOOL: `summarize`, required `["text"]`. `run_server("digest_summarize", TOOL, call)`.

`studio/mcp_servers/file_save.py`:

```python
import json
import os
import re
import sys

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(SERVER_DIR))
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)
import pdf_writer  # noqa: E402
from _mcp_base import run_server  # noqa: E402

TOOL = {
    "name": "saveToFile",
    "description": ("Сохранить content атомарно в каталоге вывода "
                    "(data/pipeline): md|txt|json|pdf. PDF — stdlib-райтер "
                    "pdf_writer (A4, кириллица, шрифт вшит). "
                    "Возвращает path, size, format."),
    "input_schema": {"type": "object", "properties": {
        "filename": {"type": "string",
                     "description": "Имя файла (только basename)"},
        "content": {"type": "string",
                    "description": "Содержимое файла"},
        "format": {"type": "string",
                   "enum": ["md", "txt", "json", "pdf"],
                   "description": "Формат (дефолт md; pdf -> +.pdf)"}},
        "required": ["filename", "content"]},
}


def call(args: dict) -> tuple:
    a = args or {}
    filename = a.get("filename")
    content = a.get("content")
    fmt = a.get("format") or "md"
    if not isinstance(filename, str) or not filename.strip():
        return {"error": "saveToFile: 'filename' — непустая строка"}, True
    if not isinstance(content, str):
        return {"error": "saveToFile: 'content' — строка"}, True
    if fmt not in ("md", "txt", "json", "pdf"):
        return {"error": "saveToFile: 'format' — md|txt|json|pdf"}, True
    name = os.path.basename(filename.strip())
    if not name or set(name) <= {"."}:
        return {"error": "saveToFile: недопустимое имя файла: "
                         + repr(filename)}, True
    name = re.sub(r"[^\w.\- ]", "_", name)
    if fmt == "pdf" and not name.lower().endswith(".pdf"):
        name += ".pdf"
    out_dir = (os.environ.get("PIPELINE_OUT_DIR")
               or os.path.join(REPO_ROOT, "data", "pipeline"))
    os.makedirs(out_dir, exist_ok=True)
    target = os.path.join(out_dir, name)
    try:
        if fmt == "json":
            blob = json.dumps(content, ensure_ascii=False,
                              indent=2).encode("utf-8")
        elif fmt == "pdf":
            blob = pdf_writer.text_to_pdf(
                content, font_path=os.environ.get("PIPELINE_FONT_PATH")
                or None)
        else:
            blob = content.encode("utf-8")
    except pdf_writer.PdfError as e:
        return {"error": "saveToFile: PDF: " + str(e)}, True
    tmp = os.path.join(out_dir, "." + name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(blob)
    os.replace(tmp, target)
    return {"path": os.path.abspath(target), "size": len(blob),
            "format": fmt}, False


if __name__ == "__main__":
    run_server("file_save", TOOL, call)
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `cd studio/backend && python -m pytest tests/test_pipeline_split_day20.py -q`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add studio/mcp_servers/digest_search.py studio/mcp_servers/digest_summarize.py \
        studio/mcp_servers/file_save.py studio/backend/tests/test_pipeline_split_day20.py
git commit -m "feat(day20): single-tool MCP servers digest_search/digest_summarize/file_save"
```

---

### Task 6: Сервер `habr_news`

**Files:**
- Create: `studio/mcp_servers/habr_news.py`
- Test: `studio/backend/tests/test_habr_news_day20.py`

**Interfaces:**
- Consumes: `_mcp_base.run_server`; URL фида — `collector.NEWS_FEEDS["habr"]` (только URL; парсер локальный — `_parse_rss_items` collector не возвращает даты).
- Produces: `get_habr_news(topics?=[testing, ai], limit?=10)` → `{source: "habr", topics, count, items: [{title, url, published, topic}]}`; фильтр по ЗАГОЛОВКУ: `testing` = `\bтест\w*|\bqa\b` (IGNORECASE), `ai` = `\b(ai|ии|llm|gpt|ml)\b|нейросет\w*` (IGNORECASE) — word-boundary: «акции»/«студии»/«maintain» НЕ проходят; sort по `published` desc, top-limit; сбой сети → isError «Habr недоступен: …»; неизвестная тема → isError. `fetch_habr` — module-level, патч-абелен из тестов (паттерн алиасов collector).

- [ ] **Step 1: Write the failing tests**

`studio/backend/tests/test_habr_news_day20.py`:

```python
"""Сервер habr_news (день 20): RSS-парсер, фильтр тем, сортировка.
Без сети: fetch_habr патчится."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(REPO, "studio", "mcp_servers"))
import habr_news  # noqa: E402

FAKE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<item><title>Тестируем новый фреймворк</title>
<link>https://habr.com/ru/articles/1/</link>
<pubDate>Wed, 24 Sep 2026 10:00:00 +0000</pubDate></item>
<item><title>Модель ИИ для анализа кода</title>
<link>https://habr.com/ru/articles/2/</link>
<pubDate>Wed, 24 Sep 2026 12:00:00 +0000</pubDate></item>
<item><title>Акции на рекордном уровне</title>
<link>https://habr.com/ru/articles/3/</link>
<pubDate>Wed, 24 Sep 2026 14:00:00 +0000</pubDate></item>
</channel></rss>"""

FAKE_ITEMS = [
    {"title": "Тестируем новый фреймворк", "url": "u1",
     "published": "Wed, 24 Sep 2026 10:00:00 +0000"},
    {"title": "Модель ИИ для анализа кода", "url": "u2",
     "published": "Wed, 24 Sep 2026 12:00:00 +0000"},
    {"title": "Акции на рекордном уровне", "url": "u3",
     "published": "Wed, 24 Sep 2026 14:00:00 +0000"},
]


def test_parse_rss_with_pubdate():
    items = habr_news.parse_rss(FAKE_XML)
    assert len(items) == 3
    assert items[0] == {"title": "Тестируем новый фреймворк",
                        "url": "https://habr.com/ru/articles/1/",
                        "published": "Wed, 24 Sep 2026 10:00:00 +0000"}


def test_parse_rss_broken_xml_empty():
    assert habr_news.parse_rss("<rss><channel>") == []


def test_filter_ru_topics(monkeypatch):
    monkeypatch.setattr(habr_news, "fetch_habr", lambda: FAKE_ITEMS)
    payload, is_err = habr_news.call({})
    assert is_err is False
    assert payload["count"] == 2  # «Акции» отфильтрована
    by_url = {i["url"]: i for i in payload["items"]}
    assert by_url["u1"]["topic"] == "testing"
    assert by_url["u2"]["topic"] == "ai"


def test_word_boundary_guard(monkeypatch):
    items = [{"title": "Как maintain инфраструктуру", "url": "u",
              "published": ""},
             {"title": "Новые акции студии", "url": "v", "published": ""}]
    monkeypatch.setattr(habr_news, "fetch_habr", lambda: items)
    payload, is_err = habr_news.call({})
    assert is_err is False
    assert payload["count"] == 0  # maintain/акции/студии — не ИИ


def test_single_topic_and_limit(monkeypatch):
    items = [{"title": "Тесты: часть %d" % i, "url": "u%d" % i,
              "published": "2026-09-25T00:0%d:00Z" % i} for i in range(5)]
    monkeypatch.setattr(habr_news, "fetch_habr", lambda: items)
    payload, is_err = habr_news.call({"topics": ["ai"], "limit": 3})
    assert is_err is False
    assert payload["count"] == 0  # testing-заголовки не проходят ai-фильтр
    p2, _ = habr_news.call({"topics": ["testing"], "limit": 3})
    assert p2["count"] == 3
    assert [i["url"] for i in p2["items"]] == ["u4", "u3", "u2"]  # desc


def test_unknown_topic_is_error():
    payload, is_err = habr_news.call({"topics": ["crypto"]})
    assert is_err is True
    assert "Неизвестная тема" in payload["error"]


def test_network_error_is_error(monkeypatch):
    def boom():
        raise OSError("нет сети")
    monkeypatch.setattr(habr_news, "fetch_habr", boom)
    payload, is_err = habr_news.call({})
    assert is_err is True
    assert "Habr недоступен" in payload["error"]
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `cd studio/backend && python -m pytest tests/test_habr_news_day20.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'habr_news'`

- [ ] **Step 3: Implement the server**

`studio/mcp_servers/habr_news.py`:

```python
"""MCP-сервер "habr_news" (день 20): один тул — get_habr_news.

Новости Habr по темам (testing / ai): RSS-фид (URL из collector.
NEWS_FEEDS["habr"]), фильтр по ЗАГОЛОВКУ (word-boundary: «акции»,
«студии», «maintain» не проходят), top-limit по published desc.
Локальный RSS-парсер (title/link/pubDate) — collector не даёт даты.
Fetch — module-level, патч-абелен из тестов. Только stdlib.
Запуск: python studio/mcp_servers/habr_news.py
"""
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
STUDIO_DIR = os.path.dirname(SERVER_DIR)
for _p in (SERVER_DIR, STUDIO_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import collector  # noqa: E402  (только URL фида)
from _mcp_base import run_server  # noqa: E402

TOPIC_PATTERNS = {
    "testing": re.compile(r"\bтест\w*|\bqa\b", re.IGNORECASE),
    "ai": re.compile(r"\b(ai|ии|llm|gpt|ml)\b|нейросет\w*", re.IGNORECASE),
}

TOOL = {
    "name": "get_habr_news",
    "description": ("Новости Habr по темам: testing (тесты/QA) и ai "
                    "(ИИ/LLM/GPT/нейросети). Фильтр по заголовкам, "
                    "свежие (published desc), до limit. Возвращает "
                    "items: {title, url, published, topic}."),
    "input_schema": {"type": "object", "properties": {
        "topics": {"type": "array", "items": {"type": "string"},
                   "enum": ["testing", "ai"],
                   "description": "Темы (дефолт: обе)"},
        "limit": {"type": "integer",
                  "description": "Сколько пунктов (дефолт 10, макс 50)"}},
        "required": []},
}


def parse_rss(xml_text: str) -> list:
    """RSS 2.0 -> [{title, url, published}]; битый XML -> []."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    items = []
    for node in root.iter("item"):
        title = (node.findtext("title") or "").strip()
        url = (node.findtext("link") or "").strip()
        pub = (node.findtext("pubDate") or "").strip()
        if title and url:
            items.append({"title": title, "url": url, "published": pub})
    return items


def fetch_habr() -> list:
    """Фид Habr RSS. Патч-абелен из тестов (как fetch_* у collector)."""
    req = urllib.request.Request(
        collector.NEWS_FEEDS["habr"],
        headers={"User-Agent": "ai-advent-challenge/20 (habr-news)"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        xml_text = resp.read().decode("utf-8", "replace")
    return parse_rss(xml_text)


def call(args: dict) -> tuple:
    a = args or {}
    topics = a.get("topics") or ["testing", "ai"]
    if not isinstance(topics, list) or not topics:
        return {"error": "get_habr_news: 'topics' — список тем "
                         "(testing|ai)"}, True
    unknown = [t for t in topics if t not in TOPIC_PATTERNS]
    if unknown:
        return {"error": "Неизвестная тема: "
                         + ", ".join(map(str, unknown))}, True
    limit = a.get("limit")
    try:
        limit = int(limit) if limit is not None else 10
    except (TypeError, ValueError):
        limit = 10
    limit = max(1, min(limit, 50))
    try:
        items = fetch_habr()
    except Exception as e:
        return {"error": "Habr недоступен: " + str(e)}, True
    out = []
    for it in items:
        hit = [n for n in topics if TOPIC_PATTERNS[n].search(it["title"])]
        if hit:
            item = dict(it)
            item["topic"] = hit[0]
            out.append(item)
    out.sort(key=lambda x: x.get("published") or "", reverse=True)
    out = out[:limit]
    return {"source": "habr", "topics": list(topics), "count": len(out),
            "items": out}, False


if __name__ == "__main__":
    run_server("habr_news", TOOL, call)
```

Примечание по тесту `test_single_topic_and_limit`: сортировка по строке `published` desc работает для одинакового формата ISO (`2026-09-25T00:0%d:00Z`); для RSS pubDate (RFC 822) порядок строковой сортировки не гарантирует хронологию — это допустимо: фид Habr уже отсортирован по свежести, published-сортировка — только стабилизация для одинаковых дат.

- [ ] **Step 4: Run tests, verify they pass**

Run: `cd studio/backend && python -m pytest tests/test_habr_news_day20.py -q`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add studio/mcp_servers/habr_news.py studio/backend/tests/test_habr_news_day20.py
git commit -m "feat(day20): habr_news MCP server (testing/ai topics, word-boundary filter)"
```

---

### Task 7: Registry `mcp.py` — 12 дефолтов, миграция, `server_name`

**Files:**
- Modify: `studio/backend/mcp.py` (`_default_servers` :340-381, `_ensure_defaults_locked` :383-391, `tools()` :~445)
- Test: `studio/backend/tests/test_registry_day20.py`
- Modify: `studio/backend/tests/test_mcp.py` (ассерты «5 дефолтов» / старые имена — обновить под 12)

**Interfaces:**
- Consumes: `MemoryStore.mcp_servers_items() -> {sid: {name,type,command,url,env,enabled}}`, `mcp_servers_set(sid, name, type, command, url, env, enabled)`, `mcp_servers_remove(sid)` (день 16, не меняются).
- Produces: 12 дефолтов = `{"Firecrawl", "Git"} ∪ {weather, news, digest_make, digest_read, task_create, task_get, digest_search, digest_summarize, file_save, habr_news}`; миграция идемпотентна (удаляет по имени старые, добавляет по имени недостающие, custom-серверы не трогает); `tools()` возвращает `[{server, server_name, name, description, input_schema}]`.

- [ ] **Step 1: Write the failing tests**

`studio/backend/tests/test_registry_day20.py`:

```python
"""Registry (день 20): 12 дефолтов, миграция старых мультитул-серверов,
server_name в tools()."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(REPO, "studio", "backend"))
import mcp  # noqa: E402
from memory import MemoryStore  # noqa: E402

LOCAL10 = ["weather", "news", "digest_make", "digest_read", "task_create",
           "task_get", "digest_search", "digest_summarize", "file_save",
           "habr_news"]
OLD3 = ("Task Manager", "News & Weather", "Pipeline Tools")
EXPECTED12 = {"Firecrawl", "Git"} | set(LOCAL10)


def make_reg(tmp_path):
    store = MemoryStore(str(tmp_path))
    return mcp.MCPRegistry(store), store


def test_fresh_store_seeds_12_defaults(tmp_path):
    reg, _ = make_reg(tmp_path)
    servers = reg.servers()
    assert len(servers) == 12
    assert {s["name"] for s in servers} == EXPECTED12
    # локальные — stdio-команды python studio/mcp_servers/<name>.py
    by_name = {s["name"]: s for s in servers}
    assert by_name["weather"]["type"] == "stdio"
    assert "weather.py" in by_name["weather"]["command"][-1]


def test_migration_removes_old_keeps_custom_idempotent(tmp_path):
    store = MemoryStore(str(tmp_path))
    for old in OLD3:
        store.mcp_servers_set("mcp_old_" + old, old, "stdio",
                              ["python", "x.py"], "", {}, True)
    store.mcp_servers_set("mcp_keep1", "My Custom", "stdio",
                          ["python", "y.py"], "", {}, True)
    reg = mcp.MCPRegistry(store)
    first = reg.servers()
    names = {s["name"] for s in first}
    for old in OLD3:
        assert old not in names
    assert names == EXPECTED12 | {"My Custom"}
    again = {s["name"] for s in reg.servers()}  # идемпотентность
    assert again == names
    assert len(again) == 13


def test_tools_includes_server_name(tmp_path):
    reg, _ = make_reg(tmp_path)
    assert reg.tools() == []  # никто не подключён
    sid = {s["name"]: s["id"] for s in reg.servers()}["weather"]
    reg._runtime[sid] = {"status": "connected", "error": None,
                         "client": None,
                         "tools": [{"name": "get_weather",
                                    "description": "Погода",
                                    "input_schema": {"type": "object"}}]}
    tools = reg.tools()
    assert len(tools) == 1
    assert tools[0] == {"server": sid, "server_name": "weather",
                        "name": "get_weather", "description": "Погода",
                        "input_schema": {"type": "object"}}
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `cd studio/backend && python -m pytest tests/test_registry_day20.py -q`
Expected: FAIL — 12≠5, `server_name` отсутствует.

- [ ] **Step 3: Implement**

`mcp.py` — (a) после констант модуля:

```python
_LOCAL_SERVERS = ("weather", "news", "digest_make", "digest_read",
                  "task_create", "task_get", "digest_search",
                  "digest_summarize", "file_save", "habr_news")
_OLD_MULTI_TOOL = ("Task Manager", "News & Weather", "Pipeline Tools")
```

(b) тело `_default_servers` заменить:

```python
    def _default_servers(self) -> list:
        """Дефолты (день 20): Firecrawl, Git (npx, день 16) + 10
        одиночных локальных MCP-серверов (python-процессы)."""
        repo = os.path.abspath(
            os.path.join(self._store.data_dir, "..", ".."))
        defaults = [
            {"name": "Firecrawl", "type": "stdio",
             "command": ["npx", "-y", "firecrawl-mcp"],
             "url": "",
             "env": {"FIRECRAWL_API_URL": "https://firecrawl.data.lmru.tech/"},
             "enabled": True},
            {"name": "Git", "type": "stdio",
             "command": ["npx", "-y", "@cyanheads/git-mcp-server@latest"],
             "url": "",
             "env": {"MCP_TRANSPORT_TYPE": "stdio", "MCP_LOG_LEVEL": "warn",
                     "GIT_SIGN_COMMITS": "false", "GIT_BASE_DIR": repo},
             "enabled": True},
        ]
        for name in _LOCAL_SERVERS:
            defaults.append({
                "name": name, "type": "stdio",
                "command": [sys.executable,
                            os.path.join(repo, "studio", "mcp_servers",
                                         name + ".py")],
                "url": "", "env": {}, "enabled": True})
        return defaults
```

(c) тело `_ensure_defaults_locked` заменить (вызывается под `self._lock` — как и сейчас):

```python
    def _ensure_defaults_locked(self) -> None:
        """День 20: миграция реестра — удалить старые мультитул-серверы
        по имени, добавить недостающие дефолты по имени. Идемпотентно.
        Custom-серверы не трогаем. Только под self._lock."""
        items = self._store.mcp_servers_items()
        if not items:
            for d in self._default_servers():
                self._store.mcp_servers_set(
                    "mcp_" + uuid.uuid4().hex[:4], d["name"], d["type"],
                    d["command"], d["url"], d["env"], d["enabled"])
            return
        by_name = {e["name"]: sid for sid, e in items.items()}
        for old in _OLD_MULTI_TOOL:
            sid = by_name.get(old)
            if sid is None:
                continue
            r = self._runtime.pop(sid, None)
            if r is not None and r.get("client") is not None:
                r["client"].close()
            self._store.mcp_servers_remove(sid)
        for d in self._default_servers():
            if d["name"] not in by_name:
                self._store.mcp_servers_set(
                    "mcp_" + uuid.uuid4().hex[:4], d["name"], d["type"],
                    d["command"], d["url"], d["env"], d["enabled"])
```

(d) в `tools()` — строку апенда заменить:

```python
                out.append({"server": sid, "server_name": e["name"], **t})
```

и docstring: `[{server, server_name, name, description, input_schema}]`.

(e) `test_mcp.py` — точечно (якоря на момент написания плана, перепроверить grep'ом):
- :340-341 — ассерт 5 имён дефолтов → `EXPECTED12` (12).
- :365-388 — `test_registry_task_manager_default_command` (проверка дефолт-команды `task_manager.py`) — **удалить**: «Task Manager» больше не дефолт; локальные дефолт-команды покрывает `test_registry_day20.py::test_fresh_store_seeds_12_defaults`.
- :566-633 — секция «Mock Task Manager: реальный subprocess» (`_task_manager_client`, 5 тестов `test_task_manager_*`) — **НЕ трогать в этом таске** (файл `task_manager.py` живёт до Task 9, тесты зелёные); Task 9 переносит секцию на `task_get.py`.
- Остальное (connect/add/remove/lock) не трогать.

- [ ] **Step 4: Run tests, verify they pass**

Run: `cd studio/backend && python -m pytest tests/test_registry_day20.py tests/test_mcp.py -q`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add studio/backend/mcp.py studio/backend/tests/test_registry_day20.py \
        studio/backend/tests/test_mcp.py
git commit -m "feat(day20): MCP registry — 12 defaults (10 single-tool), old-server migration, server_name in tools()"
```

---

### Task 8: `agent.py` — always-префикс, slug, каталог, cap, имена LLM в истории

**Files:**
- Modify: `studio/backend/agent.py` (`import re` добавить если нет; `MCP_TOOLS_RULE` :125-148; `_mcp_slug` + `_tool_loop_cap` + `_mcp_catalog_block` (метод) новые; `_llm_tools` :445-474; `build_payload` :386-444; loop cap :~560-729; tool dispatch :671-718)
- Test: `studio/backend/tests/test_agent_day20.py`
- Modify: `studio/backend/tests/test_agent.py` (ассерты «голых» имён тулов → префиксированные; tool-loop тест → cap из env)

**Interfaces:**
- Consumes: `mcp.tools()` (теперь с `server_name` — Task 7); `mcp.call_tool(sid, real_name, args)`.
- Produces:
  - `_mcp_slug(name) -> str`: `re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "server"`.
  - `MCP_TOOLS_RULE` v2: всегда префикс `server__tool` (без логики коллизий), каталог ниже, передача данных по полям (text → text, summary → content), пример Самара-PDF.
  - `_llm_tools()`: каждый MCP-тул → `f"{slug}__{real}"`; `tool_map[llm_name] = (server_id, real_name)`; нет подключённых → `(None, {})`.
  - **Сохраняемые имена в истории = LLM-имя** (префикс): assistant `tool_calls[].function.name` и `role:"tool"` `name` = `call["name"]`; MCP-вызов — real_name из map.
  - `_tool_loop_cap()`: `max(1, int(os.environ.get("TOOL_LOOP_CAP") or 15))` — читается на каждый вызов (тесты переопределяют через monkeypatch).
  - `build_payload`: `if self.mcp.tools(): system += MCP_TOOLS_RULE + self._mcp_catalog_block()`.
  - Ошибка cap: `{"type": "error", "message": "Tool-loop: превышен лимит итераций (N)"}` (сообщение дня 17, параметризовано — spec §5.3, русские тексты).

- [ ] **Step 1: Write the failing tests**

Сначала: `grep -n "tool_calls\|name.*get_task\|Tool-loop\|def test" studio/backend/tests/test_agent.py` — найти существующий tool-loop harness (scripted tool_calls + MockTransport + fake mcp) и зеркалить его.

`studio/backend/tests/test_agent_day20.py`:

```python
"""Агент (день 20): always-префикс, каталог, cap из env,
сохраняемые имена = LLM-имена."""
import json
import os
import sys
import types

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(REPO, "studio", "backend"))
import agent as agent_mod  # noqa: E402
from agent import StudioAgent  # noqa: E402
from memory import MemoryStore  # noqa: E402

W = {"server": "mcp_1", "server_name": "weather", "name": "get_weather",
     "description": "Погода по городу", "input_schema": {"type": "object"}}
S = {"server": "mcp_2", "server_name": "digest search", "name": "search",
     "description": "d" * 200, "input_schema": {"type": "object"}}


class FakeMcp:
    def __init__(self, tools, calls=None):
        self._tools = tools
        self.calls = calls if calls is not None else []

    def tools(self):
        return self._tools

    def call_tool(self, sid, name, args):
        self.calls.append((sid, name, args))
        return {"content": [{"type": "text", "text": "ok"}]}


def test_mcp_slug():
    assert agent_mod._mcp_slug("Weather") == "weather"
    assert agent_mod._mcp_slug("News & Weather") == "news_weather"
    assert agent_mod._mcp_slug("digest-make") == "digest_make"
    assert agent_mod._mcp_slug("___") == "server"


def test_llm_tools_always_prefixed():
    fake = types.SimpleNamespace(mcp=FakeMcp([W, S]))
    tools, tool_map = StudioAgent._llm_tools(fake)
    names = [t["function"]["name"] for t in tools]
    assert names == ["weather__get_weather", "digest_search__search"]
    assert tool_map["weather__get_weather"] == ("mcp_1", "get_weather")
    assert tool_map["digest_search__search"] == ("mcp_2", "search")


def test_llm_tools_none_when_empty():
    fake = types.SimpleNamespace(mcp=FakeMcp([]))
    assert StudioAgent._llm_tools(fake) == (None, {})


def test_catalog_block():
    fake = types.SimpleNamespace(mcp=FakeMcp([W, S]))
    text = StudioAgent._mcp_catalog_block(fake)
    assert "- weather (weather): get_weather — Погода по городу" in text
    assert "digest_search__search" not in text  # каталог: real-name
    assert len([l for l in text.splitlines() if l.startswith("- ")]) == 2
    # длинное описание обрезано до 120
    assert "d" * 121 not in text


def test_tool_loop_cap_env(monkeypatch):
    monkeypatch.setenv("TOOL_LOOP_CAP", "7")
    assert agent_mod._tool_loop_cap() == 7
    monkeypatch.setenv("TOOL_LOOP_CAP", "garbage")
    assert agent_mod._tool_loop_cap() == 15
    monkeypatch.delenv("TOOL_LOOP_CAP")
    assert agent_mod._tool_loop_cap() == 15
```

Интеграционные (ask_stream) — зеркалить существующий tool-loop тест из `test_agent.py` (тот же MockTransport/факовый LLM, один tool_call `weather__get_weather`); добавить:

```python
def test_stored_names_are_llm_names_and_mcp_uses_real(tmp_path, monkeypatch):
    # — setup как в существующем tool-loop тесте test_agent.py:
    # store = MemoryStore(tmp); fake LLM отдаёт 1 tool_call
    #   {"name": "weather__get_weather", "arguments": '{"city":"Самара"}'}
    #   (выбрать имя из payload["tools"], НЕ хардкодить); mcp = FakeMcp([W])
    # agent.ask_stream(...) до конца
    # assert:
    #   assistant tool_calls[0].function.name == "weather__get_weather"
    #   tool-message name == "weather__get_weather"
    #   mcp.calls == [("mcp_1", "get_weather", {"city": "Самара"})]


def test_tool_loop_cap_hit_yields_error(tmp_path, monkeypatch):
    monkeypatch.setenv("TOOL_LOOP_CAP", "2")
    # fake LLM ВСЕГДА отдаёт tool_call (пока stage < N); mcp = FakeMcp([W])
    # assert: events[-1] == {"type": "error", "message":
    #   "Tool-loop: превышен лимит итераций (2)"}
    # и ровно 2 tool-messages в истории
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `cd studio/backend && python -m pytest tests/test_agent_day20.py -q`
Expected: FAIL — `AttributeError: module 'agent' has no attribute '_mcp_slug'` и т.п.

- [ ] **Step 3: Implement**

(a) модульный уровень (после `MCP_TOOLS_RULE`):

```python
def _mcp_slug(name: str) -> str:
    """День 20: slug сервера для префикса тула (a-z0-9_)."""
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_") \
        or "server"


def _tool_loop_cap() -> int:
    """День 20: лимит итераций tool-loop (env TOOL_LOOP_CAP, дефолт 15).
    Читаем на каждый вызов — тесты переопределяют env."""
    try:
        return max(1, int(os.environ.get("TOOL_LOOP_CAP") or 15))
    except ValueError:
        return 15
```

(b) заменить текст `MCP_TOOLS_RULE` на v2 (см. Interfaces: всегда префикс, каталог, передача данных по полям, пример).

(c) `_llm_tools` — заменить тело целиком:

```python
    def _llm_tools(self):
        """День 20: LLM-тулы MCP ВСЕГДА с префиксом {slug}__{tool}.
        Возвращает (llm_tools | None, tool_map: llm_name -> (sid, real))."""
        tools = self.mcp.tools()
        if not tools:
            return None, {}
        llm_tools, tool_map = [], {}
        for t in tools:
            slug = _mcp_slug(t.get("server_name") or t["server"])
            llm_name = "%s__%s" % (slug, t["name"])
            if llm_name in tool_map:
                continue  # защита от дубля (не должно случаться)
            tool_map[llm_name] = (t["server"], t["name"])
            llm_tools.append({
                "type": "function",
                "function": {"name": llm_name,
                             "description": t.get("description", ""),
                             "parameters": t.get("input_schema")
                             or {"type": "object", "properties": {}}}})
        return llm_tools, tool_map
```

(d) `build_payload` — блок MCP заменить:

```python
        # День 19/20: MCP — правило + каталог только когда есть
        # подключённые тулы (иначе шум в system prompt).
        if self.mcp.tools():
            system += MCP_TOOLS_RULE + self._mcp_catalog_block()
```

и новый метод:

```python
    def _mcp_catalog_block(self) -> str:
        """День 20: каталог подключённых серверов в system prompt:
        - {server_name} ({slug}): {tool} — описание (до 120 символов)."""
        by_server = {}
        for t in self.mcp.tools():
            name = t.get("server_name")
            slug = _mcp_slug(name or t["server"])
            by_server.setdefault((name or slug, slug), []).append(t)
        lines = ["\n\nКаталог MCP-серверов (сервер: доступные тулы). "
                 "Имена тулов всегда server__tool — вызывать строго как "
                 "в каталоге:"]
        for (name, slug), ts in by_server.items():
            desc = "; ".join(
                (t["name"] + " — " + (t.get("description") or "")[:120])
                for t in ts)
            lines.append("- %s (%s): %s" % (name, slug, desc))
        return "\n".join(lines)
```

(e) loop — cap per-call:

```python
        llm_tools, tool_map = self._llm_tools()
        cap = _tool_loop_cap()
        for iteration in range(cap):
            ...
            if iteration == cap - 1:
                yield {"type": "error",
                       "message": ("Tool-loop: превышен лимит итераций "
                                   "(%d)" % cap)}
                return
```

(удалить старую константу/проверку «3 итерации», если она отдельным if-ом).

(f) dispatch — сохраняемые имена = LLM-имя:

```python
            calls = []
            for call in tool_calls:
                print("[LLM Decision] " + call["name"] + " " +
                      call["arguments"], flush=True)
                mapped = tool_map.get(call["name"])
                if mapped is not None:
                    sid, real_name = mapped
                else:
                    real_name = call["name"]
                    sid = next((t["server"] for t in self.mcp.tools()),
                               "")
                calls.append((call, sid, real_name))
            self._append_message_ex(
                dialogue_id, "assistant", "".join(parts),
                tool_calls=[{"id": c["id"], "type": "function",
                              "function": {"name": c["name"],
                                           "arguments": c["arguments"]}}
                             for c, _, _ in calls])
```

а tool-message в конце цикла:

```python
                self._append_message_ex(dialogue_id, "tool", text,
                                        tool_call_id=call["id"],
                                        name=call["name"])
```

(`call["name"]` = LLM-имя; real_name остаётся внутри `calls` только для `self.mcp.call_tool(sid, real_name, args)`.)

(g) `test_agent.py`: обновить ассерты имён тулов под префикс (`get_task_details` → `task_get__get_task_details` и т.д. — имена серверов берутся из setup теста; где fake mcp отдаёт tools без `server_name` — добавить поле). Tool-loop тест: cap теперь из env — в тестах `monkeypatch.setenv("TOOL_LOOP_CAP", "3")`, если ожидание «3 итерации».

- [ ] **Step 4: Run tests, verify they pass**

Run: `cd studio/backend && python -m pytest tests/test_agent_day20.py tests/test_agent.py -q`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add studio/backend/agent.py studio/backend/tests/test_agent_day20.py \
        studio/backend/tests/test_agent.py
git commit -m "feat(day20): agent routing — always server__tool prefix, MCP catalog in system prompt, TOOL_LOOP_CAP=15, llm names stored in history"
```

---

### Task 9: E2E дней 17/18/19 под новые серверы + удаление старых

**Files:**
- Modify: `scripts/e2e_day17.py` (PORT 8101), `scripts/e2e_day18.py` (8102), `scripts/e2e_day19.py` (8103), `studio/backend/tests/test_mcp.py` (секция Mock Task Manager :566-633)
- Delete: `studio/mcp_servers/news_weather.py`, `studio/mcp_servers/task_manager.py`, `studio/mcp_servers/pipeline_tools.py`, `studio/backend/tests/test_news_weather.py`, `studio/backend/tests/test_pipeline_tools.py`

**Interfaces:**
- Consumes: всё из Tasks 1-8 (новые серверы, префиксы, cap).
- Produces: три e2e-скрипта дней 17-19, которые проходят на новых серверах; сценарии и ассерты НЕ меняются, меняются только указатели на файлы/серверы и имена тулов. Общий хелпер `pick(tools_list, suffix)` (выбор имени из `payload["tools"]` по суффиксу — добавляется в каждый скрипт).

- [ ] **Step 1: Обновить `scripts/e2e_day17.py`**

Сценарий дня 17 (модель вызывает `get_task_details` для TASK-42) сохраняется. Точечные изменения:
- docstring: «Mock Task Manager (task_manager.py, 2 тула)» → «task_get (одиночный сервер, 1 тул get_task_details)»; «tools_count == 2» → «== 1».
- Константа: `TASK_MANAGER = .../task_manager.py` → `TASK_GET = .../task_get.py`.
- Part A: `reg.add("E2E TG", "stdio", command=[sys.executable, TASK_GET], env={"TASKS_FILE": os.path.join(tmp, "tasks.json")})` → connect → `tools_count == 1`. TASKS_FILE обязателен — Part A не должен трогать реальный `data/tasks.json` (seed TASK-42/TASK-7 создаётся в tmp-файле, TASK-42 доступен).
- Хелпер:
```python
def pick(tools_list, suffix):
    """Имя тула из LLM-payload по суффиксу (день 20: всегда префикс)."""
    for t in tools_list or []:
        n = t.get("function", {}).get("name", "")
        if n == suffix or n.endswith("__" + suffix):
            return n
    raise AssertionError("tool %r not in payload tools: %r"
                         % (suffix, [t.get("function", {}).get("name")
                                     for t in (tools_list or [])]))
```
- Fake-LLM: stage 0 → `_tc(pick(payload["tools"], "get_task_details"), {"task_id": "TASK-42"}, "call_17_0")` (вместо хардкода `"get_task_details"`).
- Ассерты Part A: имя в assistant `tool_calls` и в tool-сообщении теперь `"task_get__get_task_details"` (LLM-имя хранится в истории — Task 8); содержимое (TASK-42, in_progress, migor) — как было.
- Part B: `POST /api/mcp/servers {"name": "Task Get E2E", "command": [python, TASK_GET]}` → connect `tools_count == 1` → live-чат «какая задача TASK-42?» (best-effort, без env — реальный `data/tasks.json` допустим: seed создаст TASK-42 если файла нет).
- Запуск: `python scripts/e2e_day17.py` → Part A 6/6 PASS; Part B PASS/SKIP.

- [ ] **Step 2: Обновить `scripts/e2e_day18.py`**

Сценарий (make_digest/get_latest_digest id-совпадение + in-process collector + CLI) сохраняется. Изменения:
- docstring: «news_weather (4 тула)» → «4 одиночных сервера weather/news/digest_make/digest_read»; «tools_count == 4» → «по 1 на каждый».
- Константы: `NEWS_WEATHER` → `WEATHER_PY`, `NEWS_PY`, `DIGEST_MAKE_PY`, `DIGEST_READ_PY` (пути на новые файлы).
- Part A: 4× `reg.add` + `reg.connect` → у каждого `tools_count == 1`. Env: `{"DIGEST_DATA_DIR": <tmp>/digests}` у digest_make И digest_read (обобщие tmp-каталог — id-совпадение работает; make_digest оффлайн деградирует по источникам, но id/generated_at создаёт и сохраняет).
- Ассерты: `call_tool(sid_make, "make_digest", {"city": "Самара"})` → `call_tool(sid_read, "get_latest_digest", {})` → `source == "local"`, id совпадает. In-process `import collector` ассерты (collect_digest, save_digest ×2, CLI `--out`) — без изменений.
- Part B: `POST /api/mcp/servers` (digest_read.py) → connect `tools_count == 1` → чат «Покажи последнюю сводку (дайджест)» (best-effort; модель должна вызвать `digest_read__get_latest_digest`).
- Запуск: `python scripts/e2e_day18.py` → Part A 6/6 PASS; Part B PASS/SKIP.

- [ ] **Step 3: Обновить `scripts/e2e_day19.py`**

Сценарий (цепочка search → summarize → saveToFile + передача данных + PDF) сохраняется, но становится кросс-серверной (3 процесса). Изменения:
- docstring: «pipeline_tools.py (3 тула)» → «3 одиночных сервера digest_search/digest_summarize/file_save»; «tools_count == 3» → «по 1 на каждый»; «1 MCP-процесс» → «3 MCP-процесса».
- Константы: `PIPELINE_SERVER` → `DIGEST_SEARCH_PY`, `DIGEST_SUMMARIZE_PY`, `FILE_SAVE_PY`.
- Part A: 3× `reg.add` + connect → у каждого `tools_count == 1`. Env: `{"PIPELINE_SEARCH_DIR": search_dir}` у digest_search; `{"PIPELINE_OUT_DIR": out_dir}` (+ `PIPELINE_FONT_PATH` если шрифт найден) у file_save; digest_summarize — без env.
- Direct call_tool sanity: `reg.call_tool(sid_search, "search", ...)`, `reg.call_tool(sid_sum, "summarize", ...)`, `reg.call_tool(sid_save, "saveToFile", ...)` — реальные имена тулов те же, sids разные.
- Fake-LLM: `pick(payload["tools"], "search")` / `"summarize"` / `"saveToFile"` (хелпер как в Step 1).
- Ассерты: tool-имена в истории → `["digest_search__search", "digest_summarize__summarize", "file_save__saveToFile"]`; `asst_args` — ключи с префиксом; chain-ассерты (title из search ⊂ text summarize; summary ⊂ content saveToFile) и PDF (`%PDF-1.4` + `/ToUnicode`) — без изменений.
- Part B: 3× `POST /api/mcp/servers` + connect (`tools_count == 1` каждый) → чат-запрос как был (best-effort PDF).
- Запуск: `python scripts/e2e_day19.py` → Part A 12/12 PASS; Part B PASS/SKIP.

- [ ] **Step 4: Перенести секцию Mock Task Manager в `test_mcp.py` на `task_get.py`**

- [ ] **Step 5: Удалить старые серверы и их тесты**

- [ ] **Step 6: Полный прогон**

```bash
cd studio/backend && python -m pytest -q
python scripts/e2e_day17.py; python scripts/e2e_day18.py; python scripts/e2e_day19.py
```
Expected: pytest — all passed; все три e2e Part A PASS (Part B PASS/SKIP).

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor(day20): e2e day17-19 on single-tool servers; remove task_manager/news_weather/pipeline_tools"
```

---

### Task 10: `scripts/e2e_day20.py` — 10-серверный флоу (порт 8104)

**Files:**
- Create: `scripts/e2e_day20.py`

**Interfaces:**
- Consumes: всё из Tasks 1-9.
- Produces: гибрид e2e (паттерн дня 19): **Part A** — детерминированный 10-шаговый кросс-серверный флоу через 10 реальных subprocess-серверов + fake-LLM (MUST PASS, без сети); **Part B** — live uvicorn :8104 + реальный LLM (best-effort SKIP).

- [ ] **Step 1: Каркас (дословно из e2e_day19.py)**

Скопировать из `scripts/e2e_day19.py` без изменений: хелперы `log/record/print_summary/port_open/load_dotenv/http/request_long/probe_gpustack/wait_port_free/start_server/_try_dialogues/_cleanup/stop_server/parse_sse`, SSE-примитивы `_sse/_delta_chunk/_stop_chunk/_tool_response/_tc/_last_tool_text/_E2E_USAGE`, блок `reconfigure(encoding="utf-8")`. Изменения: `PORT = 8104`, лог-префикс `[e2e-day20]`, константы:

```python
FLOW = [("task_create", "create_task"), ("weather", "get_weather"),
        ("news", "get_news"), ("digest_make", "make_digest"),
        ("digest_read", "get_latest_digest"), ("habr_news", "get_habr_news"),
        ("digest_search", "search"), ("digest_summarize", "summarize"),
        ("file_save", "saveToFile"), ("task_get", "get_task_details")]
PDF_NAME = "day20_report.pdf"
CHAT_MSG = ("Создай задачу, узнай погоду в Самаре, собери дайджест, "
            "найди в дайджестах про Самара, суммаризируй и сохрани в PDF")
```

- [ ] **Step 2: Part A**

```python
def part_a() -> bool:
    log("=== Part A: 10 серверов, 10-шаговый флоу (fake LLM, без сети) ===")
    import httpx
    sys.path.insert(0, os.path.join(REPO, "studio", "backend"))
    from agent import StudioAgent
    from mcp import MCPRegistry
    from memory import MemoryStore

    idx = len(RESULTS)
    tmp = tempfile.mkdtemp(prefix="e2e_day20_")
    digests = os.path.join(tmp, "digests")
    out_dir = os.path.join(tmp, "out")
    tasks_file = os.path.join(tmp, "tasks.json")
    for d in (digests, out_dir):
        os.makedirs(d, exist_ok=True)
    reg = None
    try:
        store = MemoryStore(tmp)
        reg = MCPRegistry(store, timeout=120, env=dict(os.environ))
        font = r"C:\Windows\Fonts\arial.ttf" if os.name == "nt" else ""
        envs = {
            "digest_make": {"DIGEST_DATA_DIR": digests},
            "digest_read": {"DIGEST_DATA_DIR": digests},
            "digest_search": {"PIPELINE_SEARCH_DIR": digests},
            "task_create": {"TASKS_FILE": tasks_file},
            "task_get": {"TASKS_FILE": tasks_file},
            "file_save": {"PIPELINE_OUT_DIR": out_dir},
        }
        if font and os.path.exists(font):
            envs["file_save"]["PIPELINE_FONT_PATH"] = font

        # A1: 10× connect, у каждого tools_count == 1
        sids = {}
        all_ok = True
        for server, _tool in FLOW:
            srv = os.path.join(REPO, "studio", "mcp_servers", server + ".py")
            rec = reg.add(server, "stdio", command=[sys.executable, srv],
                          url="", env=envs.get(server, {}), enabled=True)
            sids[server] = rec["id"]
            view = reg.connect(rec["id"])
            ok = (view.get("status") == "connected"
                  and view.get("tools_count") == 1)
            all_ok &= ok
            record(f"A: connect {server} (connected, 1 tool)",
                   "PASS" if ok else "FAIL",
                   f"status={view.get('status')} "
                   f"tools_count={view.get('tools_count')} "
                   f"error={view.get('error')}")
        if not all_ok:
            return False

        # A2: seed — детерминированный дайджест с «Самара» (search оффлайн)
        seed = {"id": "digest-20260925-0100",
                "generated_at": "2026-09-25T01:00:00Z",
                "weather": {"city": "Самара", "temp_c": 19.0,
                            "description": "Ясно", "wind_ms": 2.0},
                "news": {},
                "summary": "Погода Самара: +19.0°C, ясно, слабый ветер."}
        with open(os.path.join(digests, seed["id"] + ".json"), "w",
                  encoding="utf-8") as f:
            json.dump(seed, f, ensure_ascii=False, indent=2)
        record("A: seed digest «Самара» (оффлайн для search)", "PASS")

        # A3: fake-LLM флоу (§6) + spy на call_tool (маршрутизация)
        routed = []  # (server, real_tool, args)
        orig_call = reg.call_tool

        def spy(sid, name, args):
            server = next(s for s, i in sids.items() if i == sid)
            routed.append((server, name, args))
            return orig_call(sid, name, args)
        reg.call_tool = spy

        agent = StudioAgent(tmp, base_url="https://mock.local/v1",
                            api_key="k",
                            client=httpx.Client(
                                transport=httpx.MockTransport(
                                    _fake_llm_day20)),
                            mcp=reg)
        d = store.new_dialogue()
        store.profile_action(d["id"], "decline")
        events = list(agent.ask_stream(d["id"], CHAT_MSG))
        types = [e.get("type") for e in events]
        last = events[-1] if events else {}
        record("A: ask_stream (done, без error)",
               "PASS" if (last.get("type") == "done"
                          and "error" not in types) else "FAIL",
               f"types={types}")

        # A4: порядок — 10 tool-сообщений, имена {server}__{tool}
        msgs = agent.store.get_messages(d["id"])
        tool_msgs = [m for m in msgs if m.get("role") == "tool"]
        expected = [f"{s}__{t}" for s, t in FLOW]
        got = [m.get("name") for m in tool_msgs]
        record("A: порядок — 10 tool-msg {server}__{tool}",
               "PASS" if got == expected else "FAIL", f"got={got}")

        # A5: маршрутизация — (server, real_tool) spy == FLOW, args коррект
        route_ok = [(s, t) for s, t, _ in routed] == FLOW
        record("A: маршрутизация — 10 серверов в порядке §6",
               "PASS" if route_ok else "FAIL",
               f"routed={[(s, t) for s, t, _ in routed]}")

        # A6: передача данных между серверами (цепочка)
        asst_args = {}
        for m in msgs:
            if m.get("role") == "assistant" and m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    fn = tc.get("function", {})
                    try:
                        asst_args[fn.get("name")] = json.loads(
                            fn.get("arguments", "{}"))
                    except ValueError:
                        asst_args[fn.get("name")] = {}
        t = {e[0]: json.loads(str(m.get("content", "")))
             for e, m in zip(routed, tool_msgs)}
        handoff = (
            asst_args.get("digest_summarize__summarize", {}).get("text")
            == (t.get("digest_search") or {}).get("text")
            and asst_args.get("file_save__saveToFile", {}).get("content")
            == (t.get("digest_summarize") or {}).get("summary")
            and asst_args.get("task_get__get_task_details", {})
            .get("task_id") == (t.get("task_create") or {}).get("id")
            and (t.get("digest_read") or {}).get("id")
            == (t.get("digest_make") or {}).get("id"))
        record("A: chain — summarize.text=search.text, "
               "saveToFile.content=summarize.summary, "
               "task_get.task_id=create.id, read.id=make.id",
               "PASS" if handoff else "FAIL",
               f"search_text_len={len((t.get('digest_search') or {}).get('text', ''))}"
               f" make_id={(t.get('digest_make') or {}).get('id')!r}")

        # A7: артефакты
        pdf_path = os.path.join(out_dir, PDF_NAME)
        pdf_bytes = b""
        if os.path.exists(pdf_path):
            with open(pdf_path, "rb") as f:
                pdf_bytes = f.read()
        record(f"A: {PDF_NAME} (%PDF-1.4, /ToUnicode)",
               "PASS" if (pdf_bytes.startswith(b"%PDF-1.4")
                          and b"/ToUnicode" in pdf_bytes) else "FAIL",
               f"size={len(pdf_bytes)}")
        tasks_data = {}
        if os.path.exists(tasks_file):
            with open(tasks_file, encoding="utf-8") as f:
                tasks_data = json.load(f)
        created_id = (t.get("task_create") or {}).get("id")
        record("A: tasks.json — созданная задача на диске",
               "PASS" if created_id
               and created_id in tasks_data.get("tasks", {}) else "FAIL",
               f"created_id={created_id!r} "
               f"task_get_status={(t.get('task_get') or {}).get('status')!r}")
        last_digest = os.path.join(digests, "last-digest.json")
        ld_id = None
        if os.path.exists(last_digest):
            with open(last_digest, encoding="utf-8") as f:
                ld_id = json.load(f).get("id")
        record("A: дайджест make на диске, read.id == make.id",
               "PASS" if ld_id
               and ld_id == (t.get("digest_make") or {}).get("id")
               and (t.get("digest_read") or {}).get("id")
               == (t.get("digest_make") or {}).get("id") else "FAIL",
               f"last_digest_id={ld_id!r} "
               f"make_id={(t.get('digest_make') or {}).get('id')!r}")

        # A8: кап — зацикленный fake-LLM → error «превышен лимит (15)»
        agent2 = StudioAgent(tmp, base_url="https://mock.local/v1",
                             api_key="k",
                             client=httpx.Client(
                                 transport=httpx.MockTransport(
                                     _loop_llm_day20)),
                             mcp=reg)
        d2 = store.new_dialogue()
        store.profile_action(d2["id"], "decline")
        ev2 = list(agent2.ask_stream(d2["id"], "зацикли"))
        err = ev2[-1] if ev2 else {}
        record("A: кап — зацикленный LLM → SSE error «(15)»",
               "PASS" if (err.get("type") == "error"
                          and "(15)" in str(err.get("message", "")))
               else "FAIL", f"last={err!r}")

        failed = any(s != "PASS" for _, s, _ in RESULTS[idx:])
    except Exception as e:
        record("A: unexpected exception", "FAIL", repr(e))
        failed = True
    finally:
        if reg is not None:
            try:
                reg.close_all()
            except Exception:
                pass
        shutil.rmtree(tmp, ignore_errors=True)
    return not failed
```

Fake-LLM хендлеры (module-level, до `part_a`):

```python
def pick(tools_list, suffix):
    for t in tools_list or []:
        n = t.get("function", {}).get("name", "")
        if n == suffix or n.endswith("__" + suffix):
            return n
    raise AssertionError("tool %r not in payload tools" % suffix)


def _fake_llm_day20(request):
    """Скриптит флоу §6: stage N = N tool-сообщений в запросе.
    Данные цепочки берутся из предыдущих tool-сообщений (передача)."""
    import httpx
    payload = json.loads(request.content)
    if "stream" not in payload:
        return httpx.Response(200, json={"choices": [
            {"message": {"content": "E2E-название"}}]})
    msgs = payload.get("messages") or []
    tools = payload.get("tools") or []
    stage = sum(1 for m in msgs if m.get("role") == "tool")
    t = lambda k: json.loads(_last_tool_text(msgs)) if k == "last" else None
    if stage == 0:
        return _tool_response(_tc(pick(tools, "create_task"),
                                  {"title": "E2E day20: 10-серверный флоу",
                                   "description": "авто"},
                                  "call_20_0"))
    if stage == 1:
        return _tool_response(_tc(pick(tools, "get_weather"),
                                  {"city": "Самара"}, "call_20_1"))
    if stage == 2:
        return _tool_response(_tc(pick(tools, "get_news"), {}, "call_20_2"))
    if stage == 3:
        return _tool_response(_tc(pick(tools, "make_digest"),
                                  {"city": "Самара"}, "call_20_3"))
    if stage == 4:
        return _tool_response(_tc(pick(tools, "get_latest_digest"), {},
                                  "call_20_4"))
    if stage == 5:
        return _tool_response(_tc(pick(tools, "get_habr_news"),
                                  {"topics": ["testing", "ai"], "limit": 3},
                                  "call_20_5"))
    if stage == 6:
        return _tool_response(_tc(pick(tools, "search"),
                                  {"query": "Самара"}, "call_20_6"))
    if stage == 7:
        found = {}
        try:
            found = json.loads(_last_tool_text(msgs))
        except ValueError:
            pass
        return _tool_response(_tc(pick(tools, "summarize"),
                                  {"text": found.get("text") or "",
                                   "max_points": 3},
                                  "call_20_7"))
    if stage == 8:
        summ = {}
        try:
            summ = json.loads(_last_tool_text(msgs))
        except ValueError:
            pass
        return _tool_response(_tc(pick(tools, "saveToFile"),
                                  {"filename": PDF_NAME,
                                   "content": summ.get("summary") or "",
                                   "format": "pdf"},
                                  "call_20_8"))
    if stage == 9:
        first = [m for m in msgs if m.get("role") == "tool"][0]
        created = {}
        try:
            created = json.loads(str(first.get("content", "")))
        except ValueError:
            pass
        return _tool_response(_tc(pick(tools, "get_task_details"),
                                  {"task_id": created.get("id") or ""},
                                  "call_20_9"))
    return httpx.Response(200, content=_sse([
        _delta_chunk("Готово: 10-серверный флоу выполнен, отчёт "
                     f"{PDF_NAME} сохранён."),
        _stop_chunk(),
        "[DONE]",
    ]).encode("utf-8"))


def _loop_llm_day20(request):
    """Зацикленный LLM: всегда get_weather (для теста капа)."""
    import httpx
    payload = json.loads(request.content)
    if "stream" not in payload:
        return httpx.Response(200, json={"choices": [
            {"message": {"content": "E2E-название"}}]})
    tools = payload.get("tools") or []
    stage = sum(1 for m in (payload.get("messages") or [])
                if m.get("role") == "tool")
    return _tool_response(_tc(pick(tools, "get_weather"),
                              {"city": "Самара"},
                              "call_loop_%d" % stage))
```

Примечания:
- `t = {e[0]: json.loads(...) for e, m in zip(routed, tool_msgs)}` — keyed по серверу; работает, т.к. routed и tool_msgs в одном порядке (по 1 тулу на сервер).
- `habr_news` в Part A БЕЗ сети: `get_habr_news` вернёт isError «Habr недоступен» — это НЕ рвёт флоу (spec §7); в `t` будет `{"error": ...}` — на ассерты не влияет (habr не участвует в chain).
- `digest_make`/`news`/`weather` в Part A тоже могут деградировать (нет сети) — id дайджеста создаётся всегда (`digest_make` оффлайн сохраняет дайджест с error-полями), seed для search гарантирует `search.text` непустой.
- `ask_stream` cap: 10 итераций < 15 — влезает (spec §5.3).
- `_loop_llm_day20` при cap=15: 15 tool-вызовов + 16-й LLM-запрос не идёт (error на границе) — проверено по коду Task 8.

- [ ] **Step 3: Part B (live :8104, best-effort)**

Паттерн дня 19 `part_b()` с изменениями:
- Порт 8104, лог-префикс, `CHAT_MSG` из констант.
- MCP: НЕ `POST /api/mcp/servers` (серверы уже дефолты реестра — Task 7) — вместо этого: `GET /api/mcp/servers` → найти по именам 10 локальных → для каждого `POST /api/mcp/servers/{id}/connect` (timeout 120) → у каждого `tools_count == 1`. FAIL только на инфраструктурном сбое (сервер не поднялся / connect error не по сети).
- Чат `CHAT_MSG` (timeout 330, `request_long` с retry).
- Ассерты best-effort: `GET /api/dialogues/{id}` → tool-сообщения: `PASS` если ≥ 5 tool-msg И ≥ 3 разных сервера (префиксы `name.split("__")[0]`) И `done`; иначе `SKIP` «модель не довела 10-шаговую цепочку (best-effort)».
- Cleanup: DELETE диалога; 10 дефолтных серверов НЕ удаляем (они из реестра, не артефакт e2e); restore model; stop_server.

- [ ] **Step 4: Запуск**

```bash
python scripts/e2e_day20.py
```
Expected: Part A 20/20 PASS (10 connect + seed + ask_stream + порядок + маршрутизация + chain + PDF + tasks.json + дайджест + кап); Part B PASS/SKIP. Exit 0.

- [ ] **Step 5: Commit**

```bash
git add scripts/e2e_day20.py
git commit -m "test(day20): e2e_day20 — 10-server cross-flow (Part A deterministic + Part B live :8104)"
```

---

### Task 11: Фронтенд — бейджи «server · tool»

**Files:**
- Modify: `studio/frontend/src/components/ChatPanel.tsx` (AgentSteps :77-111)
- Modify: `studio/frontend/tests/chat-panel.test.tsx` (describe «Шаги агента» :1018)

**Interfaces:**
- Consumes: имена в истории теперь ВСЕГДА `{server}__{tool}` (Task 8) — фронтенд получает их в `tc.function.name` и `m.name`.
- Produces: бейдж тула в шапке «Шаги агента» рендерит `server · tool` (разделение по ПЕРВОМУ `__`); без `__` — как есть (регресс на внешние серверы, чьи тулы префикса не имеют, если их когда-либо подключат без префикса — не бывает, но безопасно).

- [ ] **Step 1: Write the failing tests**

В `describe('ChatPanel — «Шаги агента»…')` :1018 — обновить существующий кейс и добавить новый:
- существующий кейс (:1019): fixture-имена `get_task_details` (2 места: tool_calls и tool-сообщение) → `task_get__get_task_details`; ассерт `screen.getByText('get_task_details')` → `screen.getByText('task_get · get_task_details')` (бейдж); развёрнутый ряд шага (если кейс ниже проверяет `🔧 get_task_details`) — chip StepRow остаётся полным именем: `🔧 task_get__get_task_details` (chip НЕ форматируем — только бейджи шапки, spec §10).
- новый кейс:
```tsx
it('бейдж без префикса — как есть; несколько тулов — уникальные бейджи', async () => {
  stubDialogueFetch([
    { role: 'user', content: 'проверь' },
    {
      role: 'assistant',
      content: '',
      tool_calls: [
        { id: 'c1', type: 'function', function: { name: 'plain_tool', arguments: '{}' } },
        { id: 'c2', type: 'function', function: { name: 'digest_search__search', arguments: '{"query":"x"}' } },
      ],
    },
    { role: 'tool', content: 'r1', tool_call_id: 'c1', name: 'plain_tool' },
    { role: 'tool', content: 'r2', tool_call_id: 'c2', name: 'digest_search__search' },
    { role: 'assistant', content: 'готово', model: 'qwen3.8-27b' },
  ])
  const { container } = render(
    <StudioProvider>
      <ChatPanel />
    </StudioProvider>,
  )
  await screen.findByText('готово')
  expect(screen.getByText('plain_tool')).toBeTruthy()
  expect(screen.getByText('digest_search · search')).toBeTruthy()
  // дубль бейджей нет (уникальные имена)
  expect(container.querySelectorAll('.tool-badge')).toHaveLength(2)
})
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `cd studio/frontend && npx vitest run tests/chat-panel.test.tsx -t "Шаги агента"`
Expected: FAIL — бейдж рендерит полное имя `task_get__get_task_details`, нет `task_get · get_task_details`.

- [ ] **Step 3: Implement**

`ChatPanel.tsx` — перед `AgentSteps` :77:
```tsx
// День 20: имена MCP-тулов всегда {server}__{tool} — бейдж шапки
// «Шаги агента» рендерит их как «server · tool» (первое «__»).
function formatToolName(n: string): string {
  const i = n.indexOf('__')
  return i > 0 ? `${n.slice(0, i)} · ${n.slice(i + 2)}` : n
}
```
в шапке (:107-109):
```tsx
        {names.map((n) => (
          <span key={n} className="tool-badge" title={n}>
            {formatToolName(n)}
          </span>
        ))}
```
(`title={n}` — полное имя по ховеру. StepRow-chip :127 `🔧 ${tc.function?.name}` — без изменений.)

- [ ] **Step 4: Run tests, verify they pass**

Run: `cd studio/frontend && npx vitest run tests/chat-panel.test.tsx`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add studio/frontend/src/components/ChatPanel.tsx studio/frontend/tests/chat-panel.test.tsx
git commit -m "feat(day20): agent-steps badges render MCP tools as 'server · tool'"
```

---

### Task 12: Документация и финальная проверка

**Files:**
- Modify: `README.md` (таблица дней + секция «День 20»), `RELEASE.md`, `LINKS.md`
- Create: `openspec/changes/day20-mcp-orchestration/` — `proposal.md`, `design.md`, `tasks.md`, `specs/mcp-orchestration/spec.md` (структура 1:1 как `openspec/changes/day19-mcp-pipeline/`)

**Interfaces:**
- Consumes: фактические результаты Tasks 1-11 (числа тестов, e2e-итог, commit-хэши — взять из `git log --oneline` после Tasks 1-11).
- Produces: документация дня; зелёный полный прогон (критерий «день закрыт»).

- [ ] **Step 1: README.md**

Таблица дней — строка:
`| День 20 | [day20-mcp-orchestration](…/tree/day20-mcp-orchestration) | Orchestration MCP: 10 едицельных локальных MCP-серверов (1 сервер = 1 тул, каркас `_mcp_base.py`), always-префикс `{server}__{tool}` + каталог серверов в system-промпте, кап tool-loop 15 (`TOOL_LOOP_CAP`), реестр 12 дефолтов с миграцией старых мультитул-серверов, бейджи «server · tool», e2e_day20 — 10-шаговый кросс-серверный флоу (порт 8104) |`

Секция `## День 20: Orchestration MCP` после секции дня 19 (структура как у дня 19):
- **Что это** — разбивка мультитул-серверов на 10 едицельных (1 сервер = 1 тул, без дублей), агентская маршрутизация (always-префикс + каталог в system-промпте), длинный 10-серверный флоу.
- **Архитектура** — `_mcp_base.py` (контракт `call(args) -> (payload, is_err)`), таблица 10 серверов (из плана, канонический список), `agent.py` (префикс/slug/каталог/cap/имена LLM в истории), миграция реестра (12 дефолтов, удаление старых 3 по имени, идемпотентно), file-backed задачи (`data/tasks.json`, TASK-42/7 seed, TASK-43+), `habr_news` (word-boundary фильтры testing/ai).
- **API** — без новых роутов (`/api/mcp/*` дня 16 не меняется; изменение в `GET /api/mcp/tools` — добавлено поле `server_name`).
- **Проверка задания** — числа: бэкенд N тестов PASS (фактически после прогона), фронтенд M PASS, e2e day17/18/19 — Part A PASS, e2e_day20 — Part A 20/20 + Part B PASS/SKIP; live-проверка (если Part B прошёл — описать: модель маршрутизировала по серверам).
- **Статус** — ветка `day20-mcp-orchestration` (от `day19-mcp-pipeline`).

- [ ] **Step 2: RELEASE.md**

Блок дня 20 СВЕРХУ (как у предыдущих дней): коротко что сделано + список коммитов (хэши из `git log day19-mcp-pipeline..HEAD --oneline` — вставить ПОСЛЕ всех коммитов, т.е. шаг выполняется последним).

- [ ] **Step 3: openspec/changes/day20-mcp-orchestration/**

Структура 1:1 с `openspec/changes/day19-mcp-pipeline/` (прочитать её перед написанием):
- `proposal.md` — why/what-изменения (10 серверов, префикс, каталог, cap, миграция, бейджи, e2e) / impact.
- `design.md` — ключевые решения: 1 сервер = 1 тул; always-префикс (без логики коллизий); имена LLM = имена в истории (routing proof); slug-правило; file-backed tasks; деградация live-источников; cap 15.
- `tasks.md` — чек-лист из этого плана (12 задач, отмеченные).
- `specs/mcp-orchestration/spec.md` — delta-spec: ADDED Requirements — «Едицельные локальные MCP-серверы», «Always-префикс и каталог», «Маршрутизация длинного флоу», «Миграция реестра», «Бейджи server · tool» — каждая с сценариями (SHALL).

- [ ] **Step 4: LINKS.md + демо-видео**

- Записать демо-видео работы Студии (skill `studio-demo-video`): сценарий «найди в дайджестах про Самара, суммаризируй, сохрани в PDF» + показ бейджей «server · tool» в шапке «Шаги агента».
- `LINKS.md` — строка со ссылкой на видео (путь/URL по факту записи).

- [ ] **Step 5: Финальный прогон (критерий закрытия дня)**

```bash
cd studio/backend && python -m pytest -q
cd studio/frontend && npx vitest run && npx tsc -b && npm run build
python scripts/e2e_day17.py   # Part A MUST PASS
python scripts/e2e_day18.py   # Part A MUST PASS
python scripts/e2e_day19.py   # Part A MUST PASS
python scripts/e2e_day20.py   # Part A MUST PASS
```
Доп. проверки:
- `grep -rn "task_manager\|news_weather\|pipeline_tools" studio/ scripts/ --include="*.py" --include="*.tsx"` → только упоминания в доках/коммитах, НИ одного кодового обращения (файлы удалены в Task 9).
- `GET /api/mcp/servers` на живом dev-процессе: ровно 12 серверов, старые 3 имени отсутствуют (миграция сработала на реальных `data/mcp_servers.json`).

Expected: всё зелёное (e2e Part B — PASS/SKIP).

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "docs(day20): README/RELEASE/openspec — Orchestration MCP day"
```

---

## Self-Review (выполнить при финальном чтении плана)

1. **Покryтие spec:** каждый раздел spec (§3 серверы, §4 реестр/миграция, §5 агент, §6 флоу, §7 ошибки, §8 тесты, §9 e2e, §10 фронтенд, §11 доки) имеет задачу в плане — сверить по чек-листу выше.
2. **Имена:** каноническая таблица (начало плана) = имена в Task 7 (`_LOCAL_SERVERS`) = имена в Task 8 (каталог) = имена в Task 10 (`FLOW`) = README/openspec. Любое расхождение — баг.
3. **Дубли:** после Task 9 в репозитории нет `task_manager.py`/`news_weather.py`/`pipeline_tools.py` и их тестов; `_mcp_base.run_server` — единственный каркас.
4. **Регресс:** `test_chat_payload_has_no_mcp_tools` (день 16) зелёный — `tools` в payload только при подключённых серверах (Task 8 не меняет этого условия).
5. **Порядок задач:** 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11 → 12. Task 8 зависит от Task 7 (`server_name`), Task 9 — от 1-8 (новые серверы должны существовать ПЕРЕД удалением старых), Task 10 — от 9 (e2e дней 17-19 уже зелёные).
6. **Коммиты:** у каждой задачи свой коммит (12 коммитов + финальный docs).
