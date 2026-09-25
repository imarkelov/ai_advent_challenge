"""MCP-сервер digest_search (день 20) — stdio, один тул: search.

Port из pipeline_tools.py дня 19, алгоритм без изменений: локальный
case-insensitive поиск по дайджестам (data/digests/*.json, схема
collector.py), до 20 совпадений (MATCH_CAP), свежие сначала
(generated_at desc), готовое поле text для копирования в summarize /
saveToFile.

Только stdlib. Env читается в момент вызова:
    PIPELINE_SEARCH_DIR — каталог дайджестов (дефолт <repo>/data/digests)

Запуск: python studio/mcp_servers/digest_search.py
"""
import json
import os
import sys

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)
from _mcp_base import run_server  # noqa: E402

# <repo> = на два уровня выше каталога сервера (studio/mcp_servers -> repo)
REPO_ROOT = os.path.dirname(os.path.dirname(SERVER_DIR))

MATCH_CAP = 20
_ENTRY_FIELDS = ("title", "summary", "snippet", "city")


def _search_dir():
    return (os.environ.get("PIPELINE_SEARCH_DIR")
            or os.path.join(REPO_ROOT, "data", "digests"))


# ---------- search ----------

def _digests_from(data):
    """JSON файла дайджестов (схема collector.py) -> список дайджестов.
    Один dict (last-digest.json) или список (history.json)."""
    if isinstance(data, dict):
        if "generated_at" in data or "news" in data:
            return [data]
        return []
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)
                and ("generated_at" in d or "news" in d)]
    return []


def _flatten_entries(digest: dict) -> list:
    """Дайджест -> записи {title, summary, snippet, city, generated_at}:
    одна по сводке (summary+city) и по одной на новость (title+url)."""
    gen = str(digest.get("generated_at") or "")
    weather = digest.get("weather")
    city = str(weather.get("city") or "") if isinstance(weather, dict) else ""
    entries = [{"title": str(digest.get("id") or "Дайджест"),
                "summary": str(digest.get("summary") or ""),
                "snippet": "", "city": city, "generated_at": gen}]
    news = digest.get("news")
    if isinstance(news, dict):
        for _src, items in news.items():
            if not isinstance(items, list):
                continue
            for it in items:
                if not isinstance(it, dict):
                    continue
                entries.append({
                    "title": str(it.get("title") or ""), "summary": "",
                    "snippet": str(it.get("url") or ""), "city": city,
                    "generated_at": gen})
    return entries


def _matches_text(matches: list) -> str:
    """Готовый текст совпадений: по одной строке на запись — непустые
    части [title, summary, snippet] склеены ' — '; непустой city
    дописан как ' [city]'; пустые строки пропускаются. Предназначен,
    чтобы модель скопировала его как есть в summarize.text /
    saveToFile.content."""
    lines = []
    for m in matches:
        parts = [m[k] for k in ("title", "summary", "snippet") if m.get(k)]
        line = " — ".join(parts)
        if m.get("city"):
            line += " [" + m["city"] + "]"
        if line.strip():
            lines.append(line.strip())
    return "\n".join(lines)


def call(args: dict) -> tuple:
    q = (args or {}).get("query")
    if not isinstance(q, str) or not q.strip():
        return {"error": "search: аргумент 'query' (строка) обязателен"}, True
    q = q.strip()
    d = _search_dir()
    if not os.path.isdir(d):
        return {"error": "search: каталог дайджестов не найден: " + d}, True
    ql = q.lower()
    matches = []
    for fn in sorted(os.listdir(d)):
        if not fn.lower().endswith(".json"):
            continue
        try:
            with open(os.path.join(d, fn), encoding="utf-8") as f:
                data = json.load(f)
        except (ValueError, OSError):
            continue
        for dg in _digests_from(data):
            for e in _flatten_entries(dg):
                hay = " ".join(str(e[k] or "") for k in _ENTRY_FIELDS).lower()
                if ql in hay:
                    m = {k: e[k] for k in _ENTRY_FIELDS}
                    m["generated_at"] = e["generated_at"]
                    m["file"] = fn
                    matches.append(m)
    if not matches:
        return {"error": "no matches for '%s'" % q}, True
    matches.sort(key=lambda m: (m["generated_at"], m["title"], m["file"]),
                 reverse=True)
    return {"query": q, "count": len(matches),
            "matches": matches[:MATCH_CAP],
            "text": _matches_text(matches[:MATCH_CAP])}, False


TOOL = {
    "name": "search",
    "description": ("Поиск по сохранённым дайджестам (data/digests/*.json, "
                    "схема collector.py): case-insensitive substring по "
                    "title+summary+snippet+city. До 20 совпадений, свежие "
                    "сначала (generated_at desc). Результат также содержит "
                    "готовое поле text (многострочное: по записи строка "
                    "«title — summary — snippet [city]») — скопируй его "
                    "как есть в summarize.text / saveToFile.content."),
    "input_schema": {"type": "object", "properties": {
        "query": {"type": "string",
                  "description": "Поисковый запрос (substring)"}},
        "required": ["query"]},
}


if __name__ == "__main__":
    run_server("digest_search", TOOL, call)
