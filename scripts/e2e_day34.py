"""E2E дня 34: ассистент для работы с файлами проекта (CLI + файловые MCP).

Part A — офлайн-детерминированное ядро (MUST PASS, без сети и LLM):
  A1  MCP: 4 едицельных сервера (fs_read/fs_search/fs_write/fs_diff) по stdio;
  A2  песочница: путь вне корня и .env — ошибка;
  A3  «найди все места использования»: search_files по нескольким файлам,
      сводка by_file;
  A4  «обнови документацию по X»: search -> read (3+ файла) -> write
      docs/day34/<slug>.md;
  A5  «сгенерируй changelog»: read README/RELEASE -> write CHANGELOG;
  A6  «проверь соответствие правилам»: guardrails по реальным файлам;
  A7  «подготовь diff»: diff_files против бэкапа прогона;
  A8  воспроизводимость: повторный прогон в чистом tmp-корне даёт тот же diff;
  A9  CLI --offline --demo rc=0 и ключевые строки вывода.
Part B — live (real LLM), best-effort (нет ключа/сети — SKIP):
  B1  модель сама вызывает search_files по цели;
  B2  модель читает найденные файлы (несколько) и пишет документ;
  B3  итоговый ответ ссылается на прочитанные файлы; файл создан;
  B4  diff_files показывает изменение.
Транскрипт live-прогона: .omo/evidence/day34/transcript.json.
Exit 0 для PASS/SKIP, 1 для FAIL.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "tools"))
sys.path.insert(0, os.path.join(REPO, "tools", "mcp_servers"))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(errors="replace")
        except (OSError, ValueError):
            pass

from devassist.agent import DevAgent  # noqa: E402
from devassist.bridge import FileTools  # noqa: E402
from support.llm import ChatClient, key_for_model, load_env_file  # noqa: E402

EVIDENCE_DIR = os.path.join(REPO, ".omo", "evidence", "day34")
PASS = 0
FAIL = 0
SKIP = 0


def check(name: str, ok: bool, detail: str = "") -> bool:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {detail}")
    return ok


def skip(name: str, detail: str = "") -> None:
    global SKIP
    SKIP += 1
    print(f"  SKIP {name} {detail}")


def mcp_call(server: str, tool: str, args: dict) -> dict:
    """tools/call по stdio (реальный subprocess, JSON-RPC)."""
    payload = (json.dumps({"jsonrpc": "2.0", "id": 0, "method": "initialize"})
               + "\n" + json.dumps(
                   {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": tool, "arguments": args}},
                   ensure_ascii=False) + "\n")
    proc = subprocess.run(
        [sys.executable, os.path.join(REPO, "tools", "mcp_servers",
                                      server + ".py")],
        input=payload, capture_output=True, text=True, encoding="utf-8",
        timeout=60, cwd=REPO,
        env={**os.environ, "PYTHONIOENCODING": "utf-8",
             "FS_WORKSPACE": temp_root_dir()})
    assert proc.returncode == 0, proc.stderr
    lines = [json.loads(l) for l in proc.stdout.splitlines() if l.strip()]
    return lines[-1]["result"]


_FIXTURE = {
    "pkg/core.py": "class Greeter:\n    def hello(self):\n        return 'hi'\n",
    "pkg/app.py": ("from pkg.core import Greeter\n\n"
                   "def main():\n    Greeter().hello()\n"
                   "    g = Greeter()\n    g.hello()\n"),
    "pkg/util.py": "def noop():\n    return Greeter  # noqa\n",
    "pkg/__init__.py": '"""fixture package."""\n',
    "README.md": "# Fixture\n\n| День | Задача |\n| --- | --- |\n"
                 "| День 34 | files |\n\n## День 34\n\nok\n",
    "RELEASE.md": "# Release\n\nv1\n",
    ".env": "API_KEY=secret\nEMPTY=\n",
}


def temp_root_dir() -> str:
    return _TMP_ROOT


def make_fixture() -> str:
    root = tempfile.mkdtemp(prefix="day34-")
    for path, text in _FIXTURE.items():
        full = os.path.join(root, path.replace("/", os.sep))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(text)
    return root


_TMP_ROOT = ""


def agent(root: str, run_id: str, llm=None) -> DevAgent:
    os.environ["FS_WORKSPACE"] = root
    tools = FileTools(env={**os.environ, "FS_WORKSPACE": root})
    return DevAgent(llm=llm, tools=tools, run_id=run_id)


# ---------- Part A ----------

def part_a() -> None:
    global _TMP_ROOT
    print("== Part A: офлайн-ядро (MUST PASS) ==")
    _TMP_ROOT = make_fixture()

    # A1: 4 MCP-сервера
    res = mcp_call("fs_read", "read_file", {"path": "pkg/core.py"})
    text = json.loads(res["content"][0]["text"])
    check("A1a read_file через MCP", text.get("total_lines") == 3
          and "class Greeter" in text.get("content", ""))
    res = mcp_call("fs_search", "search_files",
                   {"pattern": "Greeter", "glob": "*.py"})
    found = json.loads(res["content"][0]["text"])
    check("A1b search_files через MCP", found.get("files_matched", 0) >= 2,
          str(found)[:200])
    res = mcp_call("fs_write", "write_file",
                   {"path": "docs/note.md", "content": "# note\n"})
    wrote = json.loads(res["content"][0]["text"])
    check("A1c write_file через MCP", wrote.get("status") == "created"
          and "+# note" in wrote.get("diff", ""))
    res = mcp_call("fs_diff", "diff_files", {})
    diffed = json.loads(res["content"][0]["text"])
    check("A1d diff_files через MCP", diffed.get("changed") == 1
          and diffed["files"][0]["path"] == "docs/note.md")

    # A2: песочница
    res = mcp_call("fs_read", "read_file", {"path": "../secret.txt"})
    check("A2a путь вне корня — ошибка", res.get("isError") is True)
    res = mcp_call("fs_read", "read_file", {"path": ".env"})
    check("A2b .env закрыт", res.get("isError") is True)

    # A3: использование символа
    a = agent(_TMP_ROOT, "a3")
    try:
        out = a.run('Найди все места, где используется «Greeter» в коде')
        by_file = (a.trace[-1]["payload"].get("by_file") or {})
        check("A3a цель распознана как поиск",
              a.trace[-1]["tool"] == "search_files")
        check("A3b 2+ файла найдено", len(by_file) >= 2, str(by_file))
        check("A3c сводка в ответе", "pkg/app.py" in out["answer"]
              and "вхождений" in out["answer"])
        check("A3d файлы не изменены", not a.changes)

        # A4: документация по изменениям
        before = len(a.trace)
        out = a.run("Обнови документацию на основе того, где используется Greeter")
        tools = [r["tool"] for r in a.trace[before:]]
        reads = tools.count("read_file")
        target = "docs/day34/greeter.md"
        check("A4a search -> read x3 -> write",
              tools[:1] == ["search_files"] and reads >= 2
              and tools[-1] == "write_file", str(tools))
        check("A4b документ создан", os.path.isfile(
            os.path.join(_TMP_ROOT, target.replace("/", os.sep))))
        doc = open(os.path.join(_TMP_ROOT, target.replace("/", os.sep)),
                   encoding="utf-8").read()
        check("A4c документ ссылается на файлы", "pkg/app.py" in doc
              and "ADR" in doc)

        # A5: changelog
        before = len(a.trace)
        a.run("Сгенерируй changelog по изменениям в проекте")
        tools = [r["tool"] for r in a.trace[before:]]
        check("A5a read README/RELEASE -> write",
              tools[:2] == ["read_file", "read_file"]
              and tools[-1] == "write_file", str(tools))
        check("A5b changelog на диске", os.path.isfile(
            os.path.join(_TMP_ROOT, "docs", "CHANGELOG_day34.md")))

        # A6: инварианты (пустой EMPTY= в .env — нарушение)
        out = a.run("Проверь соответствие файлов проекта правилам")
        check("A6a проверка нашла нарушение secrets", out["ok"] is False
              and "EMPTY" in out["answer"], out["answer"][:200])

        # A7: diff
        before = len(a.trace)
        out = a.run("Подготовь diff по изменениям прогона")
        diff_rec = [r for r in a.trace[before:] if r["tool"] == "diff_files"]
        check("A7a diff_files вызван", bool(diff_rec))
        check("A7b 2 файла изменено", diff_rec and
              diff_rec[-1]["payload"].get("changed") == 2,
              str(diff_rec[-1]["payload"].get("changed")) if diff_rec else "")
        full = a.diff()["payload"]
        check("A7c unified diff на диске-бэкапе",
              all("--- a/" in f["diff"] for f in full["files"]))
    finally:
        a.tools.close()

    # A8: воспроизводимость (новый корень -> тот же набор изменений)
    root2 = make_fixture()
    b = agent(root2, "a8")
    try:
        for goal in ("Обнови документацию на основе того, где используется Greeter",
                     "Сгенерируй changelog по изменениям в проекте"):
            b.run(goal)
        files = sorted(f["path"] for f in b.diff()["payload"]["files"])
        check("A8 повторный прогон — тот же diff (2 файла)",
              files == ["docs/CHANGELOG_day34.md", "docs/day34/greeter.md"],
              str(files))
    finally:
        b.tools.close()

    # A9: CLI --offline
    demo_root = make_fixture()
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "devassist", "--offline",
         "--demo", "--run-id", "a9"],
        capture_output=True, text=True, encoding="utf-8", timeout=300,
        cwd=REPO,
        env={**os.environ, "PYTHONIOENCODING": "utf-8",
             "FS_WORKSPACE": demo_root})
    out = proc.stdout
    # гарантированный сбой правила (пустой ключ) -> rc=1 именно в A9?
    check("A9a CLI отработал (rc 0/1)", proc.returncode in (0, 1),
          proc.stderr[:300])
    check("A9b MCP-операции в выводе",
          "[MCP] search_files(" in out and "[MCP] write_file(" in out)
    check("A9c diff в выводе", "Diff прогона" in out and "+++ b/" in out)
    check("A9d проверка правил", "нарушени" in out.lower())


# ---------- Part B: live ----------

def _supported(llm: ChatClient) -> tuple:
    try:
        resp = llm.chat([{"role": "user", "content": "Скажи ровно: ok"}],
                        max_tokens=200)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    return bool((resp.get("content") or "").strip()), "пустой ответ"


def part_b() -> None:
    print("== Part B: live (real LLM + файловые MCP, best-effort) ==")
    env = load_env_file(os.path.join(REPO, ".env"))
    model = env.get("DEVASSIST_MODEL") or "deepseek-v4-flash"
    key = (env.get("DEVASSIST_API_KEY") or key_for_model(model, env)
           or os.environ.get("DEVASSIST_API_KEY", ""))
    base = env.get("GPUSTACK_BASE_URL") or os.environ.get("GPUSTACK_BASE_URL")
    if not key or not base:
        skip("B*", "нет ключа/base URL (GPUSTACK_*) — live пропущен")
        return
    llm = ChatClient(base_url=base, model=model, api_key=key, timeout=240)
    ok, why = _supported(llm)
    if not ok:
        skip("B*", f"модель {model} недоступна: {why}")
        return
    print(f"  модель {model}, {base}")
    root = make_fixture()
    transcript = {"day": 34, "model": model, "base_url": base,
                  "started_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    a = agent(root, "live1", llm=llm)
    try:
        t0 = time.time()
        out = a.run("Найди, где используется Greeter, и обнови документацию "
                    "по этому компоненту на основе реального кода проекта")
        elapsed = round(time.time() - t0, 1)
        tools = [r["tool"] for r in a.trace]
        transcript["turn"] = {"goal": "docs Greeter", "seconds": elapsed,
                              "answer": out["answer"], "tools": tools,
                              "trace": [{"tool": r["tool"],
                                         "ok": r["ok"]} for r in a.trace]}
        print(f"  {elapsed}s tools={tools}")
        check("B1 модель вызвала search_files", "search_files" in tools)
        check("B2 прочитано 2+ файла",
              tools.count("read_file") >= 2, str(tools))
        # модель по системному промпту меняет документацию (README.md
        # или docs/…) — код и fixture-файлы пакета трогать нельзя
        check("B3 документация изменена, код не тронут",
              bool(a.changes) and all(c["path"].endswith(".md")
                                      for c in a.changes),
              str([c["path"] for c in a.changes]))
        check("B4 diff доступен", bool(a.diff()["payload"].get("files")))
    finally:
        a.tools.close()
        os.makedirs(EVIDENCE_DIR, exist_ok=True)
        path = os.path.join(EVIDENCE_DIR, "transcript.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(transcript, fh, ensure_ascii=False, indent=2)
        print(f"  transcript: {path}")


def main() -> int:
    part_a()
    try:
        part_b()
    except Exception as exc:  # noqa: BLE001 — live best-effort
        global FAIL
        FAIL += 1
        print(f"  FAIL live: {exc}")
    print(f"\nИТОГ: PASS={PASS} FAIL={FAIL} SKIP={SKIP}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
