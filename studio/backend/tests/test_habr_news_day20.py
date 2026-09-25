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
