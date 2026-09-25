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
