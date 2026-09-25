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
    "testing": re.compile(r"тест|\bqa\b|\btest\b",
                          re.IGNORECASE | re.UNICODE),
    "ai": re.compile(r"\b(ai|llm|gpt|ml)\b|\bии\b|нейросет|"
                     r"machine learning|искусственный интеллект",
                     re.IGNORECASE | re.UNICODE),
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
