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
