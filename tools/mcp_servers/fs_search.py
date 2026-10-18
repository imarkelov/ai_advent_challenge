"""MCP-сервер "fs_search" (день 34): один тул — search_files.

Поиск по нескольким файлам проекта сразу: regex (или литерал) по содержимому
+ фильтр по glob имени/пути. Возврат: совпадения с file:line:text и сводка
по файлам (сколько совпадений в каждом). Только stdlib.
Запуск: python tools/mcp_servers/fs_search.py
"""
import os
import re
import sys

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = os.path.dirname(SERVER_DIR)
REPO_DIR = os.path.dirname(TOOLS_DIR)
for _p in (SERVER_DIR, TOOLS_DIR, REPO_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import _fs_sandbox as sandbox  # noqa: E402
from studio.mcp_servers._mcp_base import run_server  # noqa: E402

MAX_HITS = 400
SNIPPET = 200

TOOL = {
    "name": "search_files",
    "description": ("Grep по файлам проекта: найти все места использования "
                    "(регулярка, без учёта регистра) в нескольких файлах. "
                    "Фильтр glob ('*.py', 'support/*'). Возвращает "
                    "matches[file,line,text] и by_file — сводку."),
    "input_schema": {"type": "object", "properties": {
        "pattern": {"type": "string", "description": "регулярное выражение"},
        "glob": {"type": "string", "description": "фильтр файлов (*.py)"},
        "path": {"type": "string", "description": "каталог поиска (support/)"},
        "ignore_case": {"type": "boolean"},
        "max_hits": {"type": "integer"}},
        "required": ["pattern"]},
}


def call(args: dict) -> tuple:
    args = args or {}
    pattern = args.get("pattern")
    if not isinstance(pattern, str) or not pattern.strip():
        return {"error": "не задан pattern"}, True
    flags = re.IGNORECASE if args.get("ignore_case", True) else 0
    try:
        rx = re.compile(pattern, flags)
    except re.error as exc:
        return {"error": "плохой regex: " + str(exc)}, True
    root = sandbox.workspace_root()
    base = root
    if args.get("path"):
        try:
            base = sandbox.resolve(args["path"], must_exist=False)
        except sandbox.SandboxError as exc:
            return {"error": str(exc)}, True
    limit = args.get("max_hits")
    limit = limit if isinstance(limit, int) and limit > 0 else MAX_HITS
    glob = (args.get("glob") or "").strip()
    matches = []
    by_file = {}
    scanned = 0
    for relpath in sandbox.walk_files(base):
        if not sandbox.match_glob(relpath, glob):
            continue
        scanned += 1
        try:
            text = sandbox.read_text(os.path.join(root, relpath))
        except (sandbox.SandboxError, OSError):
            continue
        for no, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                by_file[relpath] = by_file.get(relpath, 0) + 1
                if len(matches) < limit:
                    matches.append({"file": relpath, "line": no,
                                    "text": line.strip()[:SNIPPET]})
    return {"pattern": pattern, "glob": glob or "*",
            "files_scanned": scanned, "files_matched": len(by_file),
            "total_matches": sum(by_file.values()), "truncated":
            sum(by_file.values()) > len(matches),
            "by_file": dict(sorted(by_file.items(),
                                   key=lambda kv: -kv[1])),
            "matches": matches}, False


if __name__ == "__main__":
    run_server("fs_search", TOOL, call)
