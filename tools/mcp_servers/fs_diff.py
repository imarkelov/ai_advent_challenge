"""MCP-сервер "fs_diff" (день 34): один тул — diff_files.

Список изменений проекта: unified diff текущих файлов против бэкапов
последнего прогона (BACKUP_DIR/<run-id>). Работает и с одним файлом.
Возврат: changed (added/modified), files[path, status, diff], статистика.
Только stdlib.
Запуск: python tools/mcp_servers/fs_diff.py
"""
import difflib
import os
import sys

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = os.path.dirname(SERVER_DIR)
REPO_DIR = os.path.dirname(TOOLS_DIR)
for _p in (SERVER_DIR, TOOLS_DIR, REPO_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import _fs_sandbox as sandbox  # noqa: E402
from studio.mcp_servers._mcp_base import run_server  # noqa: E402

TOOL = {
    "name": "diff_files",
    "description": ("Подготовить diff / список изменений: текущие файлы "
                    "против бэкапа последней записи (или против указанного "
                    "эталона). Без path — diff всех файлов прогона."),
    "input_schema": {"type": "object", "properties": {
        "path": {"type": "string", "description": "один файл (опционально)"},
        "run_id": {"type": "string", "description": "id прогона (бэкапа)"}},
    },
}


def _backup_dir(run_id) -> str:
    rid = run_id or os.environ.get("FS_RUN_ID") or "manual"
    return os.environ.get("FS_BACKUP_DIR") or os.path.join(
        sandbox.workspace_root(), ".devassist", "backup", str(rid))


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def call(args: dict) -> tuple:
    args = args or {}
    root = sandbox.workspace_root()
    base = _backup_dir(args.get("run_id"))
    if not os.path.isdir(base):
        return {"error": "нет бэкапа: " + sandbox.rel(base)}, True
    targets = []
    if args.get("path"):
        targets = [args["path"]]
    else:
        for dirpath, _, filenames in os.walk(base):
            for name in filenames:
                full = os.path.join(dirpath, name)
                targets.append(os.path.relpath(full, base).replace(os.sep, "/"))
    files = []
    for relpath in sorted(targets):
        try:
            sandbox.resolve(relpath, must_exist=False)
        except sandbox.SandboxError:
            continue
        old = _read(os.path.join(base, relpath.replace("/", os.sep)))
        new = _read(os.path.join(root, relpath.replace("/", os.sep)))
        if old == new:
            continue
        files.append({
            "path": relpath, "status": "modified" if old else "added",
            "diff": "".join(difflib.unified_diff(
                old.splitlines(True), new.splitlines(True),
                fromfile="a/" + relpath, tofile="b/" + relpath)),
        })
    return {"changed": len(files), "files": files}, False


if __name__ == "__main__":
    run_server("fs_diff", TOOL, call)
