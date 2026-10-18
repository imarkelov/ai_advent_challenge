"""Данные ассистента поддержки (день 33): CRM (пользователи/тикеты) и база знаний.

Ассистент — мини-сервис поддержки: RAG по FAQ/документации проекта + контекст
пользователя/тикета через MCP. Чтобы демо работало офлайн и без внешней CRM,
данные лежат в JSON:

    support_data/crm.json   — users + tickets (отдаётся MCP-серверами
                              crm_get_ticket / crm_get_user)
    support_data/kb.json    — статьи FAQ и документации для RAG

Файлы можно переопределить env: SUPPORT_CRM_FILE, SUPPORT_KB_FILE.
"""
import json
import os

DATA_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(DATA_DIR)


def crm_file() -> str:
    return (os.environ.get("SUPPORT_CRM_FILE")
            or os.path.join(DATA_DIR, "crm.json"))


def kb_file() -> str:
    return (os.environ.get("SUPPORT_KB_FILE")
            or os.path.join(DATA_DIR, "kb.json"))


def _read(path: str, default):
    """Прочитать JSON; OSError/ValueError (нет файла или битый) -> default."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def load_crm() -> dict:
    """{"users": {id: user}, "tickets": {id: ticket}} (пустые, если файла нет)."""
    data = _read(crm_file(), {})
    if not isinstance(data, dict):
        data = {}
    users = data.get("users")
    tickets = data.get("tickets")
    return {"users": users if isinstance(users, dict) else {},
            "tickets": tickets if isinstance(tickets, dict) else {}}


def load_kb() -> list:
    """Список статей [{id, title, tags[], text}]; битые записи пропускаются."""
    data = _read(kb_file(), [])
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        if not isinstance(item, dict) or not item.get("id") or not item.get("text"):
            continue
        tags = item.get("tags")
        out.append({"id": str(item["id"]),
                    "title": str(item.get("title") or item["id"]),
                    "tags": [str(t) for t in tags] if isinstance(tags, list) else [],
                    "text": str(item["text"])})
    return out
