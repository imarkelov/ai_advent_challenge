"""Сборщик дайджеста (день 18): погода + новости -> один dict (JSON).

Только stdlib. Единый источник сбора дайджеста: дёргает и MCP-сервер
news_weather.py, и GitHub Actions workflow (CLI:
`python studio/collector.py --out DIR`).

Публичный API:
    collect_digest(city, now, fetch_weather, fetch_news, sources) -> dict
    build_summary(digest) -> str
    fetch_weather(city) -> dict          (дефолт: Open-Meteo, без ключа)
    fetch_news(source) -> list[dict]     (дефолт: RSS, top-5, дедуп)
    save_digest(digest, data_dir=None) -> None
    main(argv=None) -> int               (CLI)
"""
import json
import os
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

DEFAULT_CITY = "Самара"
TOP_N = 5
HISTORY_CAP = 96
REQUEST_TIMEOUT = 15
USER_AGENT = "ai-advent-challenge/18 (digest collector)"

NEWS_FEEDS = {
    "vc.ru": "https://vc.ru/rss",
    "habr": "https://habr.com/ru/rss/news/?fl=ru",
    "tproger": "https://www.tproger.ru/feed/",
}

# WMO weather code -> RU-описание (подмножество)
WMO_CODES = {
    0: "Ясно", 1: "Преимущественно ясно", 2: "Переменная облачность",
    3: "Пасмурно", 45: "Туман", 48: "Изморозь",
    51: "Лёгкая морось", 53: "Морось", 55: "Сильная морось",
    61: "Небольшой дождь", 63: "Дождь", 65: "Сильный дождь",
    66: "Ледяной дождь", 67: "Сильный ледяной дождь",
    71: "Небольшой снег", 73: "Снег", 75: "Сильный снег",
    77: "Снежная крупа",
    80: "Небольшие ливни", 81: "Ливни", 82: "Сильные ливни",
    85: "Снежные заряды", 86: "Сильные снежные заряды",
    95: "Гроза", 96: "Гроза с градом", 99: "Сильная гроза с градом",
}


def _http_get(url, timeout=REQUEST_TIMEOUT):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _default_fetch_weather(city=DEFAULT_CITY, timeout=REQUEST_TIMEOUT):
    """Open-Meteo (без API-ключа): geocoding + current weather.
    Возвращает {city, temp_c, feels_like_c, description, wind_ms}.
    Сбой - исключение (вызывающий оборачивает в {"error": ...})."""
    q = urllib.parse.quote(str(city))
    geo = json.loads(_http_get(
        "https://geocoding-api.open-meteo.com/v1/search?name=" + q
        + "&count=1&language=ru&format=json", timeout).decode("utf-8"))
    results = (geo or {}).get("results") or []
    if not results:
        raise ValueError("Город не найден: " + str(city))
    loc = results[0]
    wx = json.loads(_http_get(
        "https://api.open-meteo.com/v1/forecast?latitude="
        + str(loc["latitude"]) + "&longitude=" + str(loc["longitude"])
        + "&current=temperature_2m,apparent_temperature,weather_code,"
        "wind_speed_10m&timezone=auto", timeout).decode("utf-8"))
    cur = (wx or {}).get("current") or {}
    code = cur.get("weather_code")
    return {
        "city": loc.get("name") or str(city),
        "temp_c": cur.get("temperature_2m"),
        "feels_like_c": cur.get("apparent_temperature"),
        "description": WMO_CODES.get(code, "код " + str(code)),
        "wind_ms": cur.get("wind_speed_10m"),
    }


def _parse_rss_items(xml_text):
    """RSS 2.0 -> [{title, url}] в порядке фида; битый XML -> []."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    items = []
    for node in root.iter("item"):
        title = (node.findtext("title") or "").strip()
        url = (node.findtext("link") or "").strip()
        if title and url:
            items.append({"title": title, "url": url})
    return items


def _dedup_top(items, n=TOP_N):
    seen = set()
    out = []
    for it in items:
        if not isinstance(it, dict) or not it.get("url") or it["url"] in seen:
            continue
        seen.add(it["url"])
        out.append(it)
        if len(out) >= n:
            break
    return out


def _default_fetch_news(source, timeout=REQUEST_TIMEOUT):
    """RSS одного source: top TOP_N, дедуп по URL."""
    url = NEWS_FEEDS[source]
    xml_text = _http_get(url, timeout).decode("utf-8", "replace")
    return _dedup_top(_parse_rss_items(xml_text))


# Публичные имена (для сервера/e2e); collect_digest фолбэкает на них же
# через globals(), чтобы их можно было патчить из тестов.
fetch_weather = _default_fetch_weather
fetch_news = _default_fetch_news


def default_data_dir():
    """<repo>/data/digests (корень репозитория = родитель studio/)."""
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "digests")


def resolve_data_dir():
    """Env DIGEST_DATA_DIR или дефолт (офлайн-тесты переопределяют)."""
    return os.environ.get("DIGEST_DATA_DIR") or default_data_dir()


def _atomic_write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def save_digest(digest, data_dir=None):
    """Атомарно: last-digest.json (перезапись) + history.json
    (массив полных дайджестов, кап HISTORY_CAP - старейшие вытесняются)."""
    dd = data_dir or resolve_data_dir()
    os.makedirs(dd, exist_ok=True)
    _atomic_write_json(os.path.join(dd, "last-digest.json"), digest)
    hist_path = os.path.join(dd, "history.json")
    history = []
    if os.path.exists(hist_path):
        try:
            with open(hist_path, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, list):
                history = loaded
        except (ValueError, OSError):
            history = []
    history.append(digest)
    _atomic_write_json(hist_path, history[-HISTORY_CAP:])


def build_digest_id(now):
    return "digest-" + now.astimezone(timezone.utc).strftime("%Y%m%d-%H%M")


def build_summary(digest):
    """Детерминированная строка: город, погода, число новостей по sources."""
    w = digest.get("weather") or {}
    if isinstance(w, dict) and "error" in w:
        wx = "погода недоступна (" + w["error"] + ")"
    else:
        wx = (str(w.get("city")) + " " + str(w.get("temp_c")) + "°C, "
              + str(w.get("description")))
    counts, total = [], 0
    for src in NEWS_FEEDS:
        val = (digest.get("news") or {}).get(src)
        if isinstance(val, list):
            total += len(val)
            counts.append(src + " " + str(len(val)))
        elif isinstance(val, dict):
            counts.append(src + " error")
    return ("Сводка " + str(digest.get("id")) + ": " + wx
            + "; новостей: " + str(total) + " (" + ", ".join(counts) + ").")


def collect_digest(city=DEFAULT_CITY, now=None, fetch_weather=None,
                   fetch_news=None, sources=None):
    """Собрать дайджест {id, generated_at, weather, news, summary}.
    Фетчеры инжестируются (офлайн-тесты). Сбой одного source ->
    {"error": ...} в news.<source>; сбой погоды -> weather = {"error": ...}.
    Исключений не бросает."""
    now = now or datetime.now(timezone.utc)
    # Глобальные имена fetch_weather/fetch_news (публичные aliases) — их
    # можно патчить из тестов; _default_* не читаются напрямую.
    fw = globals()["fetch_weather"] if fetch_weather is None else fetch_weather
    fn = globals()["fetch_news"] if fetch_news is None else fetch_news
    srcs = list(sources) if sources else list(NEWS_FEEDS)
    try:
        weather = fw(city)
    except Exception as e:
        weather = {"error": str(e)}
    news = {}
    for src in srcs:
        try:
            items = fn(src)
            if not isinstance(items, list):
                items = []
            news[src] = _dedup_top(items)  # top-N + дедуп — независимо
            # от фетчера (инжект/реальный)
        except Exception as e:
            news[src] = {"error": str(e)}
    digest = {
        "id": build_digest_id(now),
        "generated_at": now.astimezone(timezone.utc)
                        .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "weather": weather,
        "news": news,
    }
    digest["summary"] = build_summary(digest)
    return digest


def main(argv=None):
    """CLI (GitHub Actions): собрать и сохранить дайджест.
    `python studio/collector.py --out DIR [--city City]`
    Печатает JSON дайджеста; сбой фетчей не меняет exit code (0)."""
    import argparse
    p = argparse.ArgumentParser(description="Собрать дайджест (день 18)")
    p.add_argument("--out", default=None,
                   help="Каталог данных (дефолт: $DIGEST_DATA_DIR или "
                        "<repo>/data/digests)")
    p.add_argument("--city", default=DEFAULT_CITY)
    a = p.parse_args(argv)
    digest = collect_digest(city=a.city)  # реальные fetch_weather/fetch_news
    save_digest(digest, a.out)
    print(json.dumps(digest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
