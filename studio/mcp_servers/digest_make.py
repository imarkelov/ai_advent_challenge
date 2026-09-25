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
