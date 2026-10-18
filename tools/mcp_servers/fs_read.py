"""MCP-сервер "fs_read" (день 34): один тул — read_file.

Чтение файла проекта (UTF-8, внутри рабочей области FS_WORKSPACE).
Возврат: path, lines, total_lines, content (или срез 1-based from..to).
Только stdlib.
Запуск: python tools/mcp_servers/fs_read.py
"""
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
    "name": "read_file",
    "description": ("Прочитать файл проекта: content, number of lines. "
                    "Относительный путь от корня репозитория. Для больших "
                    "файлов — срез from_line/to_line (1-based)."),
    "input_schema": {"type": "object", "properties": {
        "path": {"type": "string", "description": "путь файла (support/cli.py)"},
        "from_line": {"type": "integer", "description": "первая строка (1-based)"},
        "to_line": {"type": "integer", "description": "последняя строка (вкл.)"}},
        "required": ["path"]},
}


def call(args: dict) -> tuple:
    args = args or {}
    try:
        text = sandbox.read_text(args.get("path") or "")
    except sandbox.SandboxError as exc:
        return {"error": str(exc)}, True
    lines = text.splitlines()
    frm = args.get("from_line")
    to = args.get("to_line")
    if isinstance(frm, int) or isinstance(to, int):
        start = max(1, int(frm or 1))
        end = int(to) if isinstance(to, int) else len(lines)
        chunk = lines[start - 1:end]
        return {"path": args["path"], "from_line": start,
                "to_line": min(end, len(lines)), "total_lines": len(lines),
                "lines": len(chunk), "content": "\n".join(chunk)}, False
    return {"path": args["path"], "total_lines": len(lines),
            "lines": len(lines), "content": text}, False


if __name__ == "__main__":
    run_server("fs_read", TOOL, call)
