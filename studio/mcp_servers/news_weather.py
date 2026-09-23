"""MCP-сервер "Новости и погода" (день 18) — stdio, newline JSON-RPC 2.0.

Четвёртый stdio MCP-сервер AI Studio. Даёт модели 4 инструмента:
  get_weather          — погода (Open-Meteo, без ключа)
  get_news             — RU-tech новости (vc.ru / habr / tproger, RSS)
  make_digest          — собрать дайджест и сохранить (last + history)
  get_latest_digest    — свежий дайджест: локальный файл, фолбэк GitHub API

Только stdlib. Делает реальные сетевые вызовы (urllib) при tools/call.
Единый сборщик — studio/collector.py (его же дёргает GitHub Actions
workflow; сервер и воркфлоу пишут в один и тот же data/digests/).

Запуск: python studio/mcp_servers/news_weather.py

Env:
  DIGEST_DATA_DIR      — каталог данных (дефолт <repo>/data/digests)
  DIGEST_GITHUB_REPO   — repo для GitHub-API фолбэка
                         (дефолт imarkelov/ai_advent_challenge)
"""
import base64
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))  # studio/ -> import collector
import collector  # noqa: E402

GITHUB_REPO = os.environ.get("DIGEST_GITHUB_REPO",
                             "imarkelov/ai_advent_challenge")
GITHUB_API = ("https://api.github.com/repos/" + GITHUB_REPO
              + "/contents/data/digests/last-digest.json")

TOOLS = [
    {"name": "get_weather",
     "description": ("Текущая погода в городе (по умолчанию Самара): "
                     "температура, ощущаемая, описание, ветер. "
                     "Источник: Open-Meteo (без API-ключа)."),
     "inputSchema": {"type": "object", "properties": {
         "city": {"type": "string",
                  "description": "Город (дефолт: Самара)"}},
         "required": []}},
    {"name": "get_news",
     "description": ("Свежие RU-tech новости из vc.ru, habr и tproger "
                     "(RSS, top-5 на source, дедуп по URL)."),
     "inputSchema": {"type": "object", "properties": {
         "sources": {"type": "array", "items": {"type": "string"},
                     "description": ("Подмножество: vc.ru, habr, tproger "
                                     "(дефолт — все)")}},
         "required": []}},
    {"name": "make_digest",
     "description": ("Собрать дайджест (погода + новости + summary), "
                     "сохранить в data/digests/ (last-digest.json и "
                     "history.json, кап 96). Используется и GitHub "
                     "Actions воркфлоу."),
     "inputSchema": {"type": "object", "properties": {
         "city": {"type": "string",
                  "description": "Город для погоды (дефолт: Самара)"}},
         "required": []}},
    {"name": "get_latest_digest",
     "description": ("Свежий сохранённый дайджест. Локальный "
                     "last-digest.json; если его нет — GitHub API. "
                     "Ответ: {source: local|github, generated_at, digest}."),
     "inputSchema": {"type": "object", "properties": {},
                     "required": []}},
]


def _ok_result(payload: dict) -> dict:
    """Успешный результат tools/call: текст = pretty-JSON."""
    return {"content": [{"type": "text",
                         "text": json.dumps(payload, ensure_ascii=False,
                                            indent=2)}],
            "isError": False}


def _error_result(message: str) -> dict:
    """Ошибка tools/call: текст = JSON {"error": ...}, isError: True."""
    return _ok_result({"error": message}) | {"isError": True}


def _call_get_weather(arguments: dict) -> dict:
    city = (arguments or {}).get("city") or collector.DEFAULT_CITY
    try:
        return _ok_result(collector.fetch_weather(city))
    except Exception as e:
        return _error_result("Погода недоступна: " + str(e))


def _call_get_news(arguments: dict) -> dict:
    sources = (arguments or {}).get("sources") or list(collector.NEWS_FEEDS)
    if not isinstance(sources, list):
        sources = list(collector.NEWS_FEEDS)
    out = {}
    for src in sources:
        if src not in collector.NEWS_FEEDS:
            out[src] = {"error": "Неизвестный source: " + str(src)}
            continue
        try:
            out[src] = collector.fetch_news(src)
        except Exception as e:
            out[src] = {"error": str(e)}
    return _ok_result(out)


def _call_make_digest(arguments: dict) -> dict:
    city = (arguments or {}).get("city") or collector.DEFAULT_CITY
    try:
        digest = collector.collect_digest(city=city)
        collector.save_digest(digest)
        return _ok_result(digest)
    except Exception as e:
        return _error_result("Не удалось собрать/сохранить дайджест: "
                             + str(e))


def _read_local_digest() -> dict | None:
    path = os.path.join(collector.resolve_data_dir(), "last-digest.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _read_github_digest() -> dict:
    import urllib.request
    req = urllib.request.Request(
        GITHUB_API, headers={"User-Agent": collector.USER_AGENT,
                             "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req,
                                timeout=collector.REQUEST_TIMEOUT) as r:
        data = json.loads(r.read().decode("utf-8"))
    return json.loads(base64.b64decode(data["content"]).decode("utf-8"))


def _call_get_latest_digest(arguments: dict) -> dict:
    try:
        digest = _read_local_digest()
        if digest is not None:
            return _ok_result({"source": "local",
                               "generated_at": digest.get("generated_at"),
                               "digest": digest})
    except Exception:
        pass  # локальный файл бит/нет -> пробовать GitHub
    try:
        digest = _read_github_digest()
        return _ok_result({"source": "github",
                           "generated_at": digest.get("generated_at"),
                           "digest": digest})
    except Exception as e:
        return _error_result("Дайджест недоступен: локальный файл "
                             "отсутствует, GitHub API: " + str(e))


CALL_HANDLERS = {
    "get_weather": _call_get_weather,
    "get_news": _call_get_news,
    "make_digest": _call_make_digest,
    "get_latest_digest": _call_get_latest_digest,
}


def _call_tool(params: dict) -> dict:
    name = (params or {}).get("name")
    handler = CALL_HANDLERS.get(name)
    if handler is None:
        return _error_result("Инструмент «%s» не найден" % name)
    try:
        return handler((params or {}).get("arguments") or {})
    except Exception as e:
        return _error_result("Внутренняя ошибка: " + str(e))


def main() -> None:
    """Цикл: по одной JSON-строке из stdin, ответ — строка в stdout.
    Notification (без id) не получает ответа."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if "id" not in m:  # notification — без ответа
            continue
        if m.get("method") == "initialize":
            result = {"protocolVersion": "2024-11-05", "capabilities": {},
                      "serverInfo": {"name": "news-weather",
                                     "version": "1.0"}}
        elif m.get("method") == "tools/list":
            result = {"tools": TOOLS}
        elif m.get("method") == "tools/call":
            result = _call_tool(m.get("params"))
        else:
            sys.stdout.write(json.dumps(
                {"jsonrpc": "2.0", "id": m["id"],
                 "error": {"code": -32601, "message": "метод не найден"}},
                ensure_ascii=False) + "\n")
            sys.stdout.flush()
            continue
        sys.stdout.write(json.dumps(
            {"jsonrpc": "2.0", "id": m["id"], "result": result},
            ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
