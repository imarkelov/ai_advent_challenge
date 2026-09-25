"""Серверы weather и news (день 20): handlers in-process + stdio-протокол
на реальных subprocess. Без сети: fetch'еры патчатся."""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
STUDIO = os.path.join(REPO, "studio")
SERVERS = os.path.join(REPO, "studio", "mcp_servers")
sys.path.insert(0, SERVERS)
sys.path.insert(0, STUDIO)  # collector.py живёт в studio/
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
