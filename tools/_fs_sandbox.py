"""Песочница файловых MCP-инструментов (день 34).

Все операции с файлами — только внутри корня рабочей области (env
FS_WORKSPACE, по умолчанию текущий каталог сервера = корень репозитория).
Защита: пути нормализуются, выходящие за корень — ошибка; скрытые каталоги
(.git, .omo, node_modules, __pycache__) не читаются и не пишутся.
Только stdlib — используется и MCP-серверами (tools/mcp_servers/fs_*.py),
и devassist для валидации.
"""
import fnmatch
import os

DENY_DIRS = (".git", ".omo", ".opencode", ".superpowers", ".playwright-mcp",
             ".idea", ".kotlin", "node_modules", "__pycache__", ".pytest_cache")
DENY_FILES = (".env",)
TEXT_SUFFIXES = (".py", ".md", ".json", ".txt", ".toml", ".cfg", ".ini",
                 ".yml", ".yaml", ".html", ".js", ".ts", ".tsx", ".jsx",
                 ".css", ".sql", ".sh", ".ps1", ".kt", ".gradle", ".xml",
                 ".gitignore", ".mjs", ".cjs")
MAX_READ_BYTES = 1_000_000


class SandboxError(ValueError):
    """Путь вне рабочей области / запрещённый путь."""


def workspace_root() -> str:
    return os.path.abspath(os.environ.get("FS_WORKSPACE")
                           or os.getcwd())


def resolve(path: str, must_exist: bool = True) -> str:
    """Относительный путь -> абсолютный внутри корня. Иначе SandboxError."""
    if not isinstance(path, str) or not path.strip():
        raise SandboxError("не задан путь")
    root = workspace_root()
    full = os.path.abspath(os.path.join(root, path.strip()))
    if full != root and not full.startswith(root + os.sep):
        raise SandboxError("путь вне рабочей области: " + path)
    rel = os.path.relpath(full, root)
    parts = [] if rel == "." else rel.split(os.sep)
    for part in parts:
        if part in DENY_DIRS:
            raise SandboxError("запрещённый каталог: " + part)
    if parts and parts[-1] in DENY_FILES:
        raise SandboxError("запрещённый файл: " + parts[-1])
    if must_exist and not os.path.isfile(full):
        raise SandboxError("файл не найден: " + path)
    return full


def rel(path: str) -> str:
    """Абсолютный -> относительный (для вывода)."""
    return os.path.relpath(path, workspace_root()).replace(os.sep, "/")


def is_text_file(path: str) -> bool:
    name = os.path.basename(path)
    return name.startswith(".") or name.lower().endswith(TEXT_SUFFIXES)


def walk_files(root: str) -> list:
    """Относительные пути текстовых файлов рабочей области (без deny-каталогов)."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in DENY_DIRS)
        for name in sorted(filenames):
            if name in DENY_FILES:
                continue
            full = os.path.join(dirpath, name)
            if is_text_file(full):
                out.append(os.path.relpath(full, root).replace(os.sep, "/"))
    return out


def match_glob(relpath: str, pattern: str) -> bool:
    """glob по относительному пути: '*.py' — по имени, 'support/*' — по пути."""
    if not pattern:
        return True
    if "/" not in pattern:
        return fnmatch.fnmatch(os.path.basename(relpath), pattern)
    return fnmatch.fnmatch(relpath, pattern)


def read_text(path: str) -> str:
    full = resolve(path)
    if os.path.getsize(full) > MAX_READ_BYTES:
        raise SandboxError("файл больше %d байт: %s" % (MAX_READ_BYTES, path))
    with open(full, encoding="utf-8", errors="replace") as fh:
        return fh.read()
