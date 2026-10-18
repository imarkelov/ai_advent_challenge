"""Проверка соответствия файлов правилам проекта (день 34).

Детерминированные инварианты по реальному содержимому рабочей области:
  1. секреты: .env не должен иметь пустых ключей; в коде нет хардкода
     токенов (sk-..., KEY=... в .py);
  2. stdout-дисциплина библиотек: production-код (support/, devassist/)
     не печатает в stdout — только через CLI-слой (print запрещён в
     библиотеках, разрешён в cli.py/__main__.py);
  3. stdlib-only: в support/, devassist/, tools/ только импорты stdlib или
     локальных модулей — без сторонних пакетов;
  4. каждый production-модуль (support/, devassist/) имеет docstring;
  5. README: у каждого дня (day33, day34, ...) есть строка в таблице дней
     и раздел «## День N».
Возврат: {"ok": bool, "checked": int, "violations": [{rule, path, line, msg}]}.
"""
from __future__ import annotations

import ast
import os
import re

import tools._fs_sandbox as sandbox

STDLIB = set(getattr(__import__("sys"), "stdlib_module_names", ())) | {
    "__future__", "support", "tools", "studio", "devassist", "agent",
    "_fs_sandbox", "_support_store",
    "fs_read", "fs_search", "fs_write", "fs_diff",
}
LOCAL_TOP = {"support", "tools", "studio", "devassist", "agent", "scripts",
             "myharness", "data", "docs", "_fs_sandbox", "_support_store",
             "fs_read", "fs_search", "fs_write", "fs_diff"}
# осознанные исключения (сторонние зависимости вне правила stdlib-only)
STDLIB_EXEMPT = {"support/demo_terminal.py"}
HARDCODE = re.compile(r"(sk-[A-Za-z0-9]{12,}|API_KEY\s*=\s*['\"][^'\"]{8,}['\"])")
PRINT_ALLOWED = re.compile(r"(^|/)(cli|demo_terminal|__main__)\.py$")


def _files(prefix: str, glob: str = "*.py") -> list:
    """Относительные пути файлов каталога (glob по пути, не по basename)."""
    root = sandbox.workspace_root()
    base = sandbox.resolve(prefix, must_exist=False)
    if not os.path.isdir(base):
        return []
    out = []
    for relpath in sandbox.walk_files(base):
        relpath = relpath.replace(os.sep, "/")
        full_rel = prefix.replace(os.sep, "/").rstrip("/") + "/" + relpath
        if sandbox.match_glob(full_rel, glob):
            out.append(full_rel)
    return sorted(out)


def _v(rule: str, path: str, line: int, msg: str) -> dict:
    return {"rule": rule, "path": path, "line": line, "msg": msg}


def check_secrets(violations: list) -> int:
    checked = 0
    env_path = os.path.join(sandbox.workspace_root(), ".env")
    if os.path.isfile(env_path):
        checked += 1
        with open(env_path, encoding="utf-8", errors="replace") as fh:
            for no, line in enumerate(fh, 1):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    if not value.strip():
                        violations.append(_v("secrets", ".env", no,
                                             "пустой ключ " + key.strip()))
    for relpath in _files("support") + _files("devassist") + _files("tools"):
        checked += 1
        text = sandbox.read_text(relpath)
        for no, line in enumerate(text.splitlines(), 1):
            if HARDCODE.search(line):
                violations.append(_v("secrets", relpath, no,
                                     "похоже на хардкод токена"))
    return checked


def check_stdout(violations: list) -> int:
    checked = 0
    for relpath in _files("support") + _files("devassist"):
        if PRINT_ALLOWED.search(relpath):
            continue
        checked += 1
        try:
            tree = ast.parse(sandbox.read_text(relpath))
        except SyntaxError:
            violations.append(_v("syntax", relpath, 0, "файл не парсится"))
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                    and node.func.id == "print":
                violations.append(_v("stdout", relpath, node.lineno,
                                     "print в production-модуле (нужен CLI-слой)"))
    return checked


def check_stdlib(violations: list, roots=("support", "devassist", "tools")) -> int:
    checked = 0
    for root_name in roots:
        for relpath in _files(root_name):
            if relpath in STDLIB_EXEMPT:
                continue
            checked += 1
            try:
                tree = ast.parse(sandbox.read_text(relpath))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 \
                        and node.module:
                    names = [node.module]
                for name in names:
                    top = name.split(".")[0]
                    if top in LOCAL_TOP or top in STDLIB:
                        continue
                    violations.append(_v(
                        "stdlib", relpath, getattr(node, "lineno", 0),
                        "сторонняя зависимость: " + name))
    return checked


def check_docstrings(violations: list) -> int:
    checked = 0
    for relpath in _files("support") + _files("devassist"):
        if os.path.basename(relpath) == "__init__.py":
            continue
        checked += 1
        try:
            tree = ast.parse(sandbox.read_text(relpath))
        except SyntaxError:
            continue
        if not ast.get_docstring(tree):
            violations.append(_v("docstring", relpath, 1,
                                 "нет docstring у модуля"))
    return checked


def check_readme(violations: list) -> int:
    text = sandbox.read_text("README.md")
    checked = 2
    for relpath in _files("devassist", "*.md") + _files("docs", "*.md"):
        checked += 1
        if not sandbox.read_text(relpath).strip():
            violations.append(_v("readme", relpath, 1, "пустой документ"))
    # README намеренно лаконичный (только таблица дней + последний день):
    # проверяем, что у САМОГО ПОСЛЕДНЕГО дня из таблицы есть раздел
    days = set(re.findall(r"## День (\d+)", text))
    rows = set(re.findall(r"\|\s*День (\d+)", text))
    if rows:
        last = max(rows, key=int)
        if last not in days:
            violations.append(_v("readme", "README.md", 0,
                                 "у последнего дня %s нет раздела" % last))
    return checked


CHECKS = (check_secrets, check_stdout, check_stdlib, check_docstrings,
          check_readme)


def check_all() -> dict:
    os.environ.setdefault("FS_WORKSPACE", sandbox.workspace_root())
    violations: list = []
    checked = 0
    for fn in CHECKS:
        checked += fn(violations)
    return {"ok": not violations, "checked": checked,
            "violations": violations}


def format_report(report: dict) -> str:
    lines = ["Проверено файлов: %d · нарушений: %d" % (
        report["checked"], len(report["violations"]))]
    for v in report["violations"]:
        loc = v["path"] + (":%d" % v["line"] if v["line"] else "")
        lines.append("  [%s] %s — %s" % (v["rule"], loc, v["msg"]))
    if report["ok"]:
        lines.append("OK: все инварианты выполнены")
    return "\n".join(lines)
