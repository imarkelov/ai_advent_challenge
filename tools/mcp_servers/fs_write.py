"""MCP-сервер "fs_write" (день 34): один тул — write_file.

Создание/изменение файла проекта с гарантией «всё или ничего»:
  - запись содержит ПОЛНОЕ новое содержимое файла;
  - перед записью делается бэкап оригинала (BACKUP_DIR/<run-id>/<path>);
  - атомарная запись tmp + os.replace;
  - необязательный expected_old — защита от записи поверх неожиданной
    версии (если текущий текст не совпадает — ошибка, файл не тронут).
Только stdlib.
Запуск: python tools/mcp_servers/fs_write.py
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
    "name": "write_file",
    "description": ("Создать или изменить файл проекта: content — полное "
                    "новое содержимое. Перед записью сохраняется бэкап, "
                    "запись атомарная. Возвращает diff и статус "
                    "(created/updated/unchanged)."),
    "input_schema": {"type": "object", "properties": {
        "path": {"type": "string", "description": "путь файла"},
        "content": {"type": "string", "description": "полное новое содержимое"},
        "expected_old": {"type": "string",
                         "description": "ожидаемый текущий текст (защита)"}},
        "required": ["path", "content"]},
}


def _backup(full: str, old: str) -> str:
    run_id = os.environ.get("FS_RUN_ID") or "manual"
    backup_dir = os.environ.get("FS_BACKUP_DIR") or os.path.join(
        sandbox.workspace_root(), ".devassist", "backup", run_id)
    os.makedirs(backup_dir, exist_ok=True)
    target = os.path.join(backup_dir, sandbox.rel(full))
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="") as fh:
        fh.write(old)
    return target


def call(args: dict) -> tuple:
    args = args or {}
    path = args.get("path")
    content = args.get("content")
    if not isinstance(content, str):
        return {"error": "не задан content (полное содержимое файла)"}, True
    try:
        full = sandbox.resolve(path or "", must_exist=False)
    except sandbox.SandboxError as exc:
        return {"error": str(exc)}, True
    exists = os.path.isfile(full)
    old = ""
    if exists:
        with open(full, encoding="utf-8", errors="replace", newline="") as fh:
            old = fh.read()
    expected = args.get("expected_old")
    if isinstance(expected, str) and exists and old != expected:
        return {"error": "файл изменился с момента чтения — перечитайте "
                         "и повторите", "path": path}, True
    if old == content:
        return {"path": path, "status": "unchanged", "diff": ""}, False
    # бэкап ДО записи всегда (для нового файла — пустой): diff_files и
    # откат опираются на каталог прогона, а не на факт «файл был»
    backup = _backup(full, old)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    tmp = full + ".devassist.tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(content)
    os.replace(tmp, full)
    diff = "".join(difflib.unified_diff(
        old.splitlines(True), content.splitlines(True),
        fromfile="a/" + path, tofile="b/" + path))
    return {"path": path, "status": "updated" if exists else "created",
            "lines": len(content.splitlines()), "backup": backup
            and sandbox.rel(backup), "diff": diff}, False


if __name__ == "__main__":
    run_server("fs_write", TOOL, call)
