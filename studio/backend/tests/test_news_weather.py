"""День 18: collector (офлайн, injected-фетчеры) + news_weather (реальный
subprocess) + четвёртый дефолт реестра. Сеть в офлайн-тестах НЕ
используется (live-инструменты — e2e Part B)."""
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))  # studio/backend/

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")))  # studio/

import collector  # noqa: E402

from mcp import MCPClient, MCPRegistry  # noqa: E402
from memory import MemoryStore  # noqa: E402
from test_mcp import make_fake_launcher  # noqa: E402

NEWS_WEATHER_PATH = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "mcp_servers", "news_weather.py"))

FIXED_NOW = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc)
FAKE_WEATHER = {"city": "Самара", "temp_c": 21.4, "feels_like_c": 19.8,
                "description": "Пасмурно", "wind_ms": 3.1}
FAKE_NEWS = {
    "vc.ru": [{"title": "vc-%d" % i, "url": "https://vc.ru/%d" % i}
              for i in range(3)],
    "habr": [{"title": "habr-%d" % i, "url": "https://habr.com/%d" % i}
             for i in range(3)],
    "tproger": [{"title": "tp-%d" % i, "url": "https://tproger.ru/%d" % i}
                for i in range(3)],
}


def _fake_fetch_news(source):
    return list(FAKE_NEWS[source])


def _digest(**kw):
    kw.setdefault("now", FIXED_NOW)
    kw.setdefault("fetch_weather", lambda city: dict(FAKE_WEATHER))
    kw.setdefault("fetch_news", _fake_fetch_news)
    return collector.collect_digest(**kw)


# ---------- collector: collect_digest ----------

def test_collect_digest_schema():
    d = _digest()
    assert d["id"] == "digest-20260923-1200"
    assert d["generated_at"] == "2026-09-23T12:00:00Z"
    assert d["weather"] == FAKE_WEATHER
    assert set(d["news"]) == {"vc.ru", "habr", "tproger"}
    assert all(len(v) == 3 for v in d["news"].values())
    assert "Самара" in d["summary"]
    assert "9" in d["summary"]  # 3 sources x 3 новости


def test_collect_digest_top5_and_dedup():
    many = [{"title": "t%d" % i, "url": "https://x/%d" % (i % 3)}
            for i in range(10)]  # 10 записей, 3 уникальных URL
    d = collector.collect_digest(now=FIXED_NOW,
                                 fetch_weather=lambda city: dict(FAKE_WEATHER),
                                 fetch_news=lambda src: list(many))
    for v in d["news"].values():
        assert len(v) == 3  # дедуп: 10 -> 3 уникальных (< TOP_N=5)
        assert len({i["url"] for i in v}) == len(v)


def test_collect_digest_top5_caps():
    many = [{"title": "t%d" % i, "url": "https://x/%d" % i} for i in range(10)]
    d = collector.collect_digest(now=FIXED_NOW,
                                 fetch_weather=lambda city: dict(FAKE_WEATHER),
                                 fetch_news=lambda src: list(many))
    for v in d["news"].values():
        assert len(v) == 5  # 10 уникальных -> top-5


def test_collect_digest_partial_news_failure():
    def flaky(source):
        if source == "habr":
            raise RuntimeError("timeout")
        return list(FAKE_NEWS[source])
    d = collector.collect_digest(now=FIXED_NOW,
                                 fetch_weather=lambda city: dict(FAKE_WEATHER),
                                 fetch_news=flaky)
    assert isinstance(d["news"]["habr"], dict)
    assert "timeout" in d["news"]["habr"]["error"]
    assert len(d["news"]["vc.ru"]) == 3  # остальные живы


def test_collect_digest_weather_failure():
    def boom(city):
        raise RuntimeError("no net")
    d = collector.collect_digest(now=FIXED_NOW,
                                 fetch_weather=boom,
                                 fetch_news=_fake_fetch_news)
    assert "no net" in d["weather"]["error"]
    assert len(d["news"]["vc.ru"]) == 3  # новости при погоде-сбое
    assert "погода недоступна" in d["summary"]


# ---------- collector: save_digest ----------

def test_save_digest_writes_files(tmp_path):
    d = _digest()
    collector.save_digest(d, str(tmp_path))
    last = json.loads((tmp_path / "last-digest.json")
                      .read_text(encoding="utf-8"))
    assert last["id"] == d["id"]
    hist = json.loads((tmp_path / "history.json").read_text(encoding="utf-8"))
    assert len(hist) == 1
    assert hist[0]["id"] == d["id"]


def test_save_digest_history_cap_96(tmp_path):
    # 97 записей с шагом 6ч от 2026-09-01T00:00Z -> кап 96, старейшая
    # (i=0: 20260901-0000) вытеснена
    for i in range(97):
        now = (datetime(2026, 9, 1, tzinfo=timezone.utc)
               + timedelta(hours=6 * i))
        collector.save_digest(
            collector.collect_digest(
                now=now,
                fetch_weather=lambda city: dict(FAKE_WEATHER),
                fetch_news=_fake_fetch_news),
            str(tmp_path))
    hist = json.loads((tmp_path / "history.json").read_text(encoding="utf-8"))
    assert len(hist) == 96
    assert hist[0]["id"] == "digest-20260901-0600"
    assert hist[-1]["id"] == "digest-20260925-0000"


def test_save_digest_corrupt_history_recovered(tmp_path):
    (tmp_path / "history.json").write_text("{битый json", encoding="utf-8")
    d = _digest()
    collector.save_digest(d, str(tmp_path))  # не падает
    hist = json.loads((tmp_path / "history.json").read_text(encoding="utf-8"))
    assert hist == [d]


# ---------- collector: RSS / реальные фетчеры ----------

RSS_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item><title>Первая</title><link>https://a/1</link></item>
  <item><title>Вторая</title><link>https://a/2</link></item>
  <item><title>Дубль</title><link>https://a/1</link></item>
  <item><title>Без ссылки</title></item>
  <item><title>Третья</title><link>https://a/3</link></item>
</channel></rss>"""


def test_parse_rss_items_basic():
    items = collector._parse_rss_items(RSS_FIXTURE)
    assert [i["url"] for i in items] == ["https://a/1", "https://a/2",
                                         "https://a/1", "https://a/3"]
    assert items[0]["title"] == "Первая"


def test_parse_rss_corrupt_xml_empty():
    assert collector._parse_rss_items("<rss><item><title>") == []
    assert collector._parse_rss_items("") == []


def test_dedup_top_cap_and_order():
    items = [{"title": "t%d" % i, "url": "https://x/%d" % i} for i in range(7)]
    out = collector._dedup_top(items, 5)
    assert len(out) == 5
    assert out[0]["url"] == "https://x/0"  # порядок фида сохранён


def test_default_news_sources_registered():
    assert set(collector.NEWS_FEEDS) == {"vc.ru", "habr", "tproger"}


def test_wmo_codes_have_common_entries():
    assert collector.WMO_CODES[0] == "Ясно"
    assert collector.WMO_CODES[61] == "Небольшой дождь"
    assert collector.WMO_CODES[95] == "Гроза"


# live-тесты: сеть, по умолчанию пропускаются (-m "not live")
import pytest  # noqa: E402


@pytest.mark.live
def test_live_fetch_weather():
    w = collector.fetch_weather("Самара")
    assert w["city"]
    assert isinstance(w["temp_c"], (int, float))


@pytest.mark.live
def test_live_fetch_news():
    items = collector.fetch_news("vc.ru")
    assert 1 <= len(items) <= collector.TOP_N
    assert all(i["title"] and i["url"] for i in items)
