"""MCP-сервер "digest_read" (день 20): один тул — get_latest_digest.

Последний дайджест: локальный data/digests/last-digest.json -> фолбэк
GitHub API (репозиторий, env DIGEST_GITHUB_REPO). Результат:
{source: local|github, generated_at, digest}. Только stdlib.
Запуск: python studio/mcp_servers/digest_read.py
"""
import base64
import json
import os
import sys
import urllib.request

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
STUDIO_DIR = os.path.dirname(SERVER_DIR)
for _p in (SERVER_DIR, STUDIO_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import collector  # noqa: E402
from _mcp_base import run_server  # noqa: E402

TOOL = {
    "name": "get_latest_digest",
    "description": ("Последний дайджест: локальный "
                    "data/digests/last-digest.json, фолбэк — GitHub API. "
                    "Возвращает {source: local|github, generated_at, "
                    "digest}."),
    "input_schema": {"type": "object", "properties": {}, "required": []},
}


def read_github() -> dict:
    """last-digest.json из GitHub (base64 в meta.contents)."""
    repo = (os.environ.get("DIGEST_GITHUB_REPO")
            or "imarkelov/ai_advent_challenge")
    url = ("https://api.github.com/repos/" + repo
           + "/contents/data/digests/last-digest.json")
    req = urllib.request.Request(
        url, headers={"User-Agent": collector.USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as resp:
        meta = json.loads(resp.read().decode("utf-8"))
    return json.loads(base64.b64decode(meta["content"]))


def call(args: dict) -> tuple:
    try:
        path = os.path.join(collector.resolve_data_dir(),
                            "last-digest.json")
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return {"source": "local", "generated_at": d.get("generated_at"),
                "digest": d}, False
    except Exception:
        pass
    try:
        d = read_github()
        return {"source": "github", "generated_at": d.get("generated_at"),
                "digest": d}, False
    except Exception as e:
        return {"error": "Дайджест недоступен: " + str(e)}, True


if __name__ == "__main__":
    run_server("digest_read", TOOL, call)
