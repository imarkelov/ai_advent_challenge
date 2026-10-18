"""CLI ассистента для работы с файлами проекта (день 34).

Запуск:
    python -m devassist                     # REPL, LLM + файловые MCP-тулы
    python -m devassist --offline           # без LLM (детерминированный план)
    python -m devassist --demo [--offline]  # скриптовый прогон для видео
    python -m devassist "найди все места использования CRMClient"

Цель — одна фраза («найди все места, где используется X», «обнови
документацию по X», «сгенерируй changelog», «проверь соответствие файлов
правилам», «подготовь diff»). Ассистент сам читает/ищет/пишет файлы.

Команды REPL:
    /diff       список изменений прогона (unified diff)
    /trace      журнал файловых операций
    /files      файлы, изменённые в прогоне
    /help /exit
"""
from __future__ import annotations

import argparse
import os
import sys

try:
    sys.stdout.reconfigure(errors="replace", line_buffering=True)
except (AttributeError, OSError, ValueError):
    pass

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from devassist.agent import DevAgent, format_trace_line  # noqa: E402
from devassist.bridge import FileTools  # noqa: E402

DEMO_DIFF = [
    "Подготовь diff: покажи все изменения, которые ты сделал в этом прогоне",
]
DEMO_GOALS = [
    "Найди все места, где используется «CRMClient» в коде",
    "Обнови документацию на основе того, где используется CRMClient",
    "Сгенерируй changelog по изменениям в проекте",
    "Проверь соответствие файлов проекта правилам и инвариантам",
]


def _out(text: str = "") -> None:
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        print(text.encode(enc, "replace").decode(enc, "replace"), flush=True)


def _input(prompt: str) -> str:
    sys.stdout.write(prompt)
    sys.stdout.flush()
    line = sys.stdin.readline()
    if not line:
        raise EOFError
    enc = getattr(sys.stdin, "encoding", None)
    if enc and enc.lower() not in ("utf-8", "utf8"):
        try:
            line = line.encode(enc, "replace").decode("utf-8", "replace")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return line


def make_llm(args, env: dict):
    if args.offline:
        return None
    from support.llm import ChatClient, key_for_model
    model = args.model or env.get("DEVASSIST_MODEL") or "deepseek-v4-flash"
    return ChatClient(base_url=args.base_url or env.get("GPUSTACK_BASE_URL"),
                      model=model,
                      api_key=env.get("DEVASSIST_API_KEY")
                      or key_for_model(model, env)
                      or os.environ.get("DEVASSIST_API_KEY", ""))


def render_goal(agent: DevAgent, goal: str, show_trace: bool = True) -> dict:
    before = len(agent.trace)
    _out("› " + goal)
    try:
        result = agent.run(goal)
    except Exception as exc:  # noqa: BLE001 — CLI не падает
        _out("  ! ошибка: %s" % exc)
        return {"ok": False, "answer": ""}
    if show_trace:
        _out()
        _out("  Файловые операции:")
        new = agent.trace[before:]
        if not new:
            _out("    (нет)")
        for rec in new:
            _out("    " + format_trace_line(rec))
    _out()
    _out("Итог:")
    for line in agent.final_answer().splitlines():
        _out("  " + line)
    _out()
    if agent.changes:
        _out("Изменённые файлы:")
        for ch in agent.changes:
            _out("  %-8s %s" % (ch["status"], ch["path"]))
    else:
        _out("Файлы не изменялись.")
    return result


def cmd_diff(agent: DevAgent) -> None:
    rec = agent.diff()
    data = rec["payload"]
    if not rec["ok"]:
        _out("diff недоступен: " + str(data.get("error")))
        return
    if not data.get("files"):
        _out("изменений нет (файлы совпадают с бэкапом прогона)")
        return
    _out("Diff прогона %s: %d файлов" % (agent.run_id, data["changed"]))
    for f in data["files"]:
        _out("  %-8s %s" % (f["status"], f["path"]))
    _out()
    for f in data["files"]:
        _out(f["diff"])


def cmd_trace(agent: DevAgent) -> None:
    if not agent.trace:
        _out("операций не было")
        return
    for rec in agent.trace:
        _out(format_trace_line(rec))


def _help() -> None:
    _out("/diff     unified diff изменений прогона\n"
         "/files    изменённые файлы\n"
         "/trace    журнал файловых операций\n"
         "/help /exit")


def run_demo(agent: DevAgent, offline: bool) -> int:
    _out("=== ДЕМО: ассистент для работы с файлами (день 34) ===")
    _out("модель: %s · инструменты: %s · прогон: %s" % (
        "offline (без LLM)" if offline else agent.llm.model,
        "MCP stdio" if agent.tools.connected else "прямой вызов", agent.run_id))
    _out()
    report = {}
    for goal in DEMO_GOALS:
        report = render_goal(agent, goal)
        _out()
    render_goal(agent, DEMO_DIFF[0])
    _out()
    _out("› /diff")
    cmd_diff(agent)
    _out()
    _out("быстрый просмотр: git diff --stat")
    _out()
    _out("=== ДЕМО завершено ===")
    return 0 if report.get("ok", True) else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="devassist",
                                description="Ассистент для работы с файлами "
                                            "проекта (день 34)")
    p.add_argument("goal", nargs="*", help="цель (одна фраза)")
    p.add_argument("--offline", action="store_true",
                   help="без LLM: детерминированный план над теми же тулами")
    p.add_argument("--demo", action="store_true", help="скриптовый прогон")
    p.add_argument("--model", default=None)
    p.add_argument("--base-url", default=None)
    p.add_argument("--run-id", default=None,
                   help="id прогона (каталог бэкапов)")
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(errors="replace")
            except (OSError, ValueError):
                pass
    args = build_parser().parse_args(argv)
    from support.llm import load_env_file
    env = load_env_file(os.path.join(REPO_ROOT, ".env"))
    for key, value in env.items():
        os.environ.setdefault(key, value)

    # DevAgent сам поднимает MCP-серверы с env текущего прогона (FS_RUN_ID)
    tools = FileTools()
    agent = DevAgent(llm=make_llm(args, env), tools=tools,
                     run_id=args.run_id)
    _out("Ассистент для работы с файлами · файловые MCP-инструменты")
    _out("  read_file · search_files · write_file · diff_files — %s" % (
        "MCP (stdio) подключены" if tools.connected else
        "прямой вызов (MCP недоступен: %s)" % tools.error))
    _out("  бэкапы прогона: .devassist/backup/%s/" % agent.run_id)
    try:
        if args.demo:
            return run_demo(agent, args.offline)
        if args.goal:
            result = render_goal(agent, " ".join(args.goal))
            return 0 if result.get("ok") else 1
        _out("Опишите цель одной фразой (пример: «найди все места, где "
             "используется CRMClient»).")
        _out("Команды: /diff /files /trace /help /exit")
        while True:
            try:
                line = _input("\n› ").strip()
            except (EOFError, KeyboardInterrupt):
                _out()
                break
            if not line:
                continue
            if line.startswith("/"):
                cmd, _, _arg = line.partition(" ")
                cmd = cmd.lower()
                if cmd in ("/exit", "/quit", "/q"):
                    break
                if cmd == "/help":
                    _help()
                elif cmd == "/diff":
                    cmd_diff(agent)
                elif cmd == "/files":
                    _out("\n".join("%s %s" % (c["status"], c["path"])
                                   for c in agent.changes) or "изменений нет")
                elif cmd == "/trace":
                    cmd_trace(agent)
                else:
                    _out("неизвестная команда: %s (см. /help)" % cmd)
                continue
            render_goal(agent, line)
    finally:
        tools.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
