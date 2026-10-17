"""MCP-сервер "git_branch" (день 31): инструменты git-контекста проекта.

Три инструмента:
  - git_branch: текущая ветка репозитория
  - git_files:  изменённые файлы рабочего дерева (git status --porcelain)
  - git_diff:   unified diff рабочего дерева или staged-изменений

Только stdlib (без внешних зависимостей). Только чтение: мутаций нет.
Запуск: python studio/mcp_servers/git_branch.py
"""
import json
import os
import subprocess
import sys

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
STUDIO_DIR = os.path.dirname(SERVER_DIR)
for _p in (SERVER_DIR, STUDIO_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from _mcp_base import make_server  # noqa: E402

# Корень репозитория — три уровня вверх от studio/mcp_servers/
REPO_ROOT = os.path.dirname(os.path.dirname(SERVER_DIR))

# Windows: stdout/stderr пайпов могут быть cp1251.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except Exception:
        pass

PROTOCOL = "2024-11-05"

# --- Три инструмента (tools) ---

TOOLS = [
    {
        "name": "git_branch",
        "description": ("Текущая git-ветка репозитория проекта. "
                        "Возвращает имя ветки или ошибку, если git недоступен."),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "git_files",
        "description": ("Список изменённых и неотслеживаемых файлов рабочего дерева. "
                        "Возвращает tracked (изменённые) и untracked (новые) файлы."),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "git_diff",
        "description": ("Unified diff рабочего дерева или staged-изменений. "
                        "staged=true — только проиндексированные; по умолчанию — всё."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "staged": {
                    "type": "boolean",
                    "description": "Если true — только staged (git diff --cached)",
                },
                "max_lines": {
                    "type": "integer",
                    "description": "Максимум строк diff (по умолчанию 500)",
                },
            },
            "required": [],
        },
    },
]


def _git(*args: str) -> tuple[int, str, str]:
    """Запустить git-команду; вернуть (код, stdout, stderr)."""
    try:
        p = subprocess.run(
            ["git"] + list(args),
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=15,
        )
        return p.returncode, p.stdout, p.stderr
    except (OSError, subprocess.SubprocessError) as e:
        return -1, "", str(e)


def call_git_branch(_args: dict) -> tuple[dict, bool]:
    code, out, err = _git("rev-parse", "--abbrev-ref", "HEAD")
    if code != 0:
        return {"error": f"git недоступен: {err.strip()}"}, True
    return {"branch": out.strip()}, False


def call_git_files(_args: dict) -> tuple[dict, bool]:
    code, out, err = _git("status", "--porcelain")
    if code != 0:
        return {"error": f"git status не удался: {err.strip()}"}, True
    tracked, untracked = [], []
    for line in out.splitlines():
        if not line.strip():
            continue
        status, path = line[:2], line[3:].strip()
        (untracked if status.strip() == "??" else tracked).append(path)
    return {"tracked": tracked, "untracked": untracked,
            "total": len(tracked) + len(untracked)}, False


def call_git_diff(args: dict) -> tuple[dict, bool]:
    staged = bool(args.get("staged", False))
    max_lines = int(args.get("max_lines", 500))
    cmd = ["diff"]
    if staged:
        cmd.append("--cached")
    code, out, err = _git(*cmd)
    if code != 0:
        return {"error": f"git diff не удался: {err.strip()}"}, True
    lines = out.splitlines()
    truncated = len(lines) > max_lines
    if truncated:
        lines = lines[:max_lines]
    return {
        "diff": "\n".join(lines),
        "lines": len(lines),
        "truncated": truncated,
        "staged": staged,
    }, False


# --- Мультитул-сервер (3 инструмента, 1 процесс) ---

HANDLERS = {
    "git_branch": call_git_branch,
    "git_files": call_git_files,
    "git_diff": call_git_diff,
}


def _ok(m, result):
    return {"jsonrpc": "2.0", "id": m.get("id"), "result": result}


def handle(m):
    """JSON-RPC обработчик для мультитул-сервера."""
    if "id" not in m:
        return None
    method = m.get("method")
    if method == "initialize":
        return _ok(m, {
            "protocolVersion": PROTOCOL,
            "serverInfo": {"name": "git_context", "version": "1.0"},
            "capabilities": {"tools": {}},
        })
    if method == "tools/list":
        return _ok(m, {"tools": TOOLS})
    if method == "tools/call":
        params = m.get("params") or {}
        tool_name = params.get("name")
        handler = HANDLERS.get(tool_name)
        if not handler:
            return _ok(m, {
                "content": [{"type": "text", "text": json.dumps(
                    {"error": f"Инструмент «{tool_name}» не найден"},
                    ensure_ascii=False)}],
                "isError": True,
            })
        try:
            payload, is_err = handler(params.get("arguments") or {})
        except Exception as e:
            payload, is_err = {"error": f"Внутренняя ошибка: {e}"}, True
        result = {"content": [{"type": "text", "text": json.dumps(
            payload, ensure_ascii=False, indent=2)}]}
        if is_err:
            result["isError"] = True
        return _ok(m, result)
    return {"jsonrpc": "2.0", "id": m.get("id"),
            "error": {"code": -32601,
                      "message": f"Неизвестный метод: {method}"}}


def run_server():
    """stdio-цикл: JSON-строка из stdin → JSON-строка в stdout."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            m = json.loads(line)
        except ValueError:
            sys.stdout.write(json.dumps(
                {"jsonrpc": "2.0", "id": None,
                 "error": {"code": -32700, "message": "Parse error"}},
                ensure_ascii=False) + "\n")
            sys.stdout.flush()
            continue
        resp = handle(m)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    run_server()
