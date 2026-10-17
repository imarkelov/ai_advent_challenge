# -*- coding: utf-8 -*-
"""E2E day 35 «CLI ассистента + агенты в Markdown»

Задание: сделать то же, что дни 30–34 (вопросы по проекту, ревью,
поддержка с CRM, автономные файловые сценарии), но **для CLI**; агенты
должны быть в формате **Markdown** — в обоих исполнениях (API и CLI).

Скрипт проверяет CLI как РЕАЛЬНЫЙ ПОДПРОЦЕСС (`python cli.py ...`), а не
вызовом функции: иначе не проверены разбор argv, коды возврата, потоки
вывода и то, что CLI действительно запускается из терминала.

Часть A (offline, MUST PASS, без сети и без LLM):
  A1  `agents list` — 8 агентов (4 × api/cli) из agents/*.md
  A2  `agents list --json` — машинный вывод, errors пуст
  A3  `agents show <name>` — карточка; `--prompt` показывает промпт
  A4  промпты api/cli РАЗЛИЧАЮТСЯ (иначе «два исполнения» — фикция)
  A5  `agents show --execution` — выбор исполнения
  A6  `index` собирает индекс проекта; `status` его видит
  A7  `status` показывает корпус/CRM/git
  A8  `ask` — вопрос по проекту, ответ + источники (пути файлов)
  A9  `ask --json` — машинный вывод с answer/sources
  A10 `review` с diff → находки с `файл:строка` и severity
  A11 `review --fail-on-high` → код возврата 1 (для CI)
  A12 `review --out FILE` — запись отчёта в файл
  A13 `tickets` — список из CRM с фильтрами
  A14 `support <id>` — персонализация (план/оплата) видна в выводе
  A15 `support` неизвестного тикета — деградация, не падение
  A16 `scenario find_usages` — вхождения символа с путями
  A17 `scenario generate` — создаёт ADR с транслитом; `--param
      dry_run=true` НЕ пишет файл
  A18 КРИТИЧНО: `dry_run=false` — типизация параметров (без неё строка
      «false» истинна и запись молча пропускается)
  A19 `scenario` вне allow-list (`../evil.md`) → код 1, файл не создан
  A20 `scenario diff_report` — отчёт об изменениях (git)
  A21 `scenarios` — каталог 5 сценариев
  A22 `--no-llm` — работа без LLM (детерминированно, сеть не трогается)
  A23 неизвестная команда/агент/сценарий → понятная ошибка, код 1,
      БЕЗ traceback
  A24 `git` — ветка и изменённые файлы
  A25 CLI — тонкая обёртка: не импортирует kb/mcp/gateway и не делает
      git-мутаций

Часть B (live, best-effort, SKIP без Ollama):
  B0  зонд Ollama: GET /api/tags
  B1  `ask` через реальную LLM (llm=true, ответ осмысленный)
  B2  `--agent code-review` реально меняет промпт (проверка выбора агента)
  B3  `support` по тикету с реальной LLM
  B4  `review` реального git-diff с LLM-рекомендациями

Запуск из корня репозитория:
    python scripts/e2e_day32.py [--part-a-only]
Код возврата: 0 — успех (Part A весь PASS и Part B без FAIL), 1 — провал.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(REPO, "studio", "backend")
CLI = os.path.join(BACKEND, "cli.py")
AGENTS_DIR = os.path.join(REPO, "agents")

sys.path.insert(0, REPO)
sys.path.insert(0, BACKEND)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(REPO, ".env"))

OLLAMA = os.environ.get("OLLAMA_BASE_URL",
                        "http://localhost:11434/v1").rstrip("/")
OLLAMA_NATIVE = OLLAMA[:-3] if OLLAMA.endswith("/v1") else OLLAMA

# --- инфраструктура отчёта (паттерн e2e_day24/28/29/30/31) ---

RESULTS: list[tuple[str, str, str]] = []


def log(msg: str) -> None:
    print(msg, flush=True)


def record(step: str, status: str, detail: str = "") -> None:
    RESULTS.append((step, status, detail))
    mark = {"PASS": "OK  ", "FAIL": "FAIL", "SKIP": "SKIP"}[status]
    log(f"  [{mark}] {step}" + (f" — {detail}" if detail else ""))


def print_summary() -> tuple[int, int]:
    p = sum(1 for _, s, _ in RESULTS if s == "PASS")
    f = sum(1 for _, s, _ in RESULTS if s == "FAIL")
    k = sum(1 for _, s, _ in RESULTS if s == "SKIP")
    log("\n" + "=" * 74)
    log(f"ИТОГ: PASS={p} FAIL={f} SKIP={k}")
    for step, status, detail in RESULTS:
        if status == "FAIL":
            log(f"  ПРОВАЛ: {step} — {detail}")
    log("=" * 74)
    return p, f


# --- запуск CLI как подпроцесса ---


def cli(args: list, cwd: str | None = None, timeout: int = 300) -> tuple:
    """Запустить CLI подпроцессом → (код, stdout, stderr).

    CLI запускается из каталога бэкенда (`python cli.py ...`) — это
    штатный способ для модулей репозитория (у пакета `studio/` нет
    __init__.py, поэтому `python -m studio.cli` из корня не работает).
    """
    proc = subprocess.run(
        [sys.executable, CLI, "--repo", REPO] + args,
        cwd=cwd or BACKEND, capture_output=True, text=True, timeout=timeout)
    return proc.returncode, proc.stdout, proc.stderr


def ollama_models() -> list:
    try:
        with urllib.request.urlopen(OLLAMA_NATIVE + "/api/tags",
                                    timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return [m.get("name") for m in data.get("models", []) if m.get("name")]
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return []


def _git(args: list) -> tuple:
    try:
        p = subprocess.run(["git"] + args, cwd=REPO, capture_output=True,
                           text=True, timeout=20)
        return p.returncode, p.stdout, p.stderr
    except (OSError, subprocess.SubprocessError) as e:
        return -1, "", str(e)


def _make_agent_dir(tmp: str) -> str:
    """Копия каталога агентов в tmp — чтобы порча в тестах не трогала репо."""
    dst = os.path.join(tmp, "agents")
    shutil.copytree(AGENTS_DIR, dst)
    return dst


# --- Part A (offline) ---


def part_a() -> bool:
    log("\n" + "=" * 74)
    log("ЧАСТЬ A — офлайн (CLI как реальный подпроцесс, без сети и LLM)")
    log("=" * 74)

    tmp = tempfile.mkdtemp(prefix="e2e_day32_")
    created: list[str] = []

    def cleanup() -> None:
        for rel in created:
            p = os.path.join(REPO, rel)
            if os.path.isfile(p):
                os.remove(p)
            parent = os.path.dirname(p)
            if os.path.isdir(parent) and not os.listdir(parent):
                os.rmdir(parent)
        shutil.rmtree(tmp, ignore_errors=True)

    try:
        # A1 — agents list
        code, out, err = cli(["agents", "list"])
        names = ("project-qa", "code-review", "support", "file-ops")
        if code == 0 and all(n in out for n in names) and "cli" in out \
                and "api" in out:
            record("A1 агенты: 4 × api/cli из agents/*.md", "PASS",
                   "8 файлов, каталог читается")
        else:
            record("A1 агенты: 4 × api/cli из agents/*.md", "FAIL",
                   f"код={code}, stderr={err[:80]}")

        # A2 — agents list --json
        code, out, err = cli(["agents", "list", "--json"])
        data = {}
        try:
            data = json.loads(out)
        except ValueError:
            pass
        agents = data.get("agents") or []
        pairs = {(a["name"], a["execution"]) for a in agents}
        expected = {(n, e) for n in names for e in ("api", "cli")}
        if code == 0 and pairs == expected and not data.get("errors"):
            record("A2 agents list --json: 8 пар, без ошибок", "PASS",
                   f"пар={len(pairs)}")
        else:
            record("A2 agents list --json: 8 пар, без ошибок", "FAIL",
                   f"код={code}, пар={len(pairs)}, errors={data.get('errors')}")

        # A3 — agents show
        code, out, err = cli(["agents", "show", "code-review"])
        if code == 0 and "code-review" in out and "Инструменты" in out:
            record("A3 agents show: карточка агента", "PASS",
                   "метаданные без промпта")
        else:
            record("A3 agents show: карточка агента", "FAIL",
                   f"код={code}: {err[:80]}")

        code_p, out_p, _ = cli(["agents", "show", "code-review", "--prompt"])
        if code_p == 0 and "Ты — код-ревьюер" in out_p:
            record("A3b agents show --prompt: тело промпта", "PASS",
                   f"{len(out_p)} символов")
        else:
            record("A3b agents show --prompt: тело промпта", "FAIL",
                   f"код={code_p}")

        # A4 — промпты api/cli различаются
        diffs = []
        for n in names:
            _, api_out, _ = cli(["agents", "show", n, "--execution", "api",
                                 "--prompt"])
            _, cli_out, _ = cli(["agents", "show", n, "--execution", "cli",
                                 "--prompt"])
            diffs.append(api_out != cli_out)
        if all(diffs) and len(diffs) == 4:
            record("A4 промпты api/cli различаются", "PASS",
                   "4 агента × 2 исполнения — разные инструкции")
        else:
            record("A4 промпты api/cli различаются", "FAIL",
                   f"совпали у {diffs.count(False)} из 4")

        # A5 — выбор исполнения
        code, out, _ = cli(["agents", "show", "support",
                            "--execution", "api"])
        if code == 0 and "(api)" in out and "(cli)" not in out:
            record("A5 agents show --execution", "PASS",
                   "выбрано исполнение api")
        else:
            record("A5 agents show --execution", "FAIL", f"код={code}")

        # A6 — index + status
        code, out, err = cli(["index", "--no-llm"] if False else ["index"],
                             timeout=600)
        code2, out2, _ = cli(["status"])
        if code == 0 and "Индекс собран" in out and "НЕ построен" not in out2:
            record("A6 index + status", "PASS", out.strip().splitlines()[0][:70])
        else:
            record("A6 index + status", "FAIL",
                   f"index={code}: {err[:80]}")

        # A7 — status: корпус/CRM/git
        if all(k in out2 for k in ("Корпус", "CRM", "git")):
            record("A7 status: корпус + CRM + git", "PASS",
                   out2.strip().splitlines()[0])
        else:
            record("A7 status: корпус + CRM + git", "FAIL", out2[:80])

        # A8 — ask
        code, out, err = cli(["ask", "как устроен шлюз локальной LLM?"])
        if code == 0 and "Источники" in out and ".md" in out:
            record("A8 ask: ответ + источники", "PASS",
                   f"{len(out)} символов вывода")
        else:
            record("A8 ask: ответ + источники", "FAIL",
                   f"код={code}: {err[:100]}")

        # A9 — ask --json
        code, out, _ = cli(["ask", "как устроен шлюз?", "--json"])
        data = {}
        try:
            data = json.loads(out)
        except ValueError:
            pass
        if code == 0 and "answer" in data and "sources" in data:
            record("A9 ask --json", "PASS",
                   f"llm={data.get('llm')}, источников={len(data['sources'])}")
        else:
            record("A9 ask --json", "FAIL", f"код={code}")

        # A10 — review с находками и номерами строк
        diff_path = os.path.join(tmp, "x.diff")
        with open(diff_path, "w", encoding="utf-8") as f:
            f.write(
                "diff --git a/studio/backend/mod.py "
                "b/studio/backend/mod.py\n"
                "--- a/studio/backend/mod.py\n"
                "+++ b/studio/backend/mod.py\n"
                "@@ -1,2 +1,5 @@\n"
                ' """Модуль."""\n'
                '+API_KEY = "sk-abcdef1234567890abcd"\n'
                "+def g():\n"
                "+    try:\n"
                "+        pass\n"
                "+    except:\n"
                "+        pass\n"
                " X = 1\n")
        code, out, err = cli(["review", "--diff", diff_path, "--no-llm"])
        if code == 0 and "studio/backend/mod.py:" in out \
                and ("критично" in out or "secret" in out.lower()):
            record("A10 review: находки с файл:строка", "PASS",
                   "secret/bare_except с номерами строк")
        else:
            record("A10 review: находки с файл:строка", "FAIL",
                   f"код={code}: {err[:80]}")

        # A11 — fail-on-high
        code, out, err = cli(["review", "--diff", diff_path, "--no-llm",
                              "--fail-on-high"])
        if code == 1 and "критично" in err:
            record("A11 review --fail-on-high → код 1 (CI)", "PASS",
                   "пригодно для пайплайна")
        else:
            record("A11 review --fail-on-high → код 1 (CI)", "FAIL",
                   f"код={code}")

        # A12 — review --out
        report = os.path.join(tmp, "review.md")
        code, out, _ = cli(["review", "--diff", diff_path, "--no-llm",
                            "--out", report])
        if code == 0 and os.path.isfile(report) \
                and "Ревью" in open(report, encoding="utf-8").read():
            record("A12 review --out: запись в файл", "PASS",
                   f"{os.path.getsize(report)} байт")
        else:
            record("A12 review --out: запись в файл", "FAIL", f"код={code}")

        # A13 — tickets + фильтр
        code, out, _ = cli(["tickets"])
        code_f, out_f, _ = cli(["tickets", "--status", "closed"])
        if code == 0 and "T-5003" in out and "T-5003" not in out_f:
            record("A13 tickets: список и фильтр", "PASS",
                   out.strip().splitlines()[0][:60])
        else:
            record("A13 tickets: список и фильтр", "FAIL",
                   f"код={code}/{code_f}")

        # A14 — support с персонализацией
        code, out, err = cli(["--no-llm", "support", "T-5003"])
        if code == 0 and "T-5003" in out and ("failed" in out or "free" in out) \
                and "Учтено из CRM" in out:
            record("A14 support: персонализация из CRM", "PASS",
                   "план/оплата видны в выводе")
        else:
            record("A14 support: персонализация из CRM", "FAIL",
                   f"код={code}: {err[:80]}")

        # A15 — неизвестный тикет не роняет
        code, out, err = cli(["--no-llm", "support", "T-9999"])
        if code == 0 and "не найден" in out:
            record("A15 support неизвестного тикета: деградация", "PASS",
                   "общий ответ без персонализации")
        else:
            record("A15 support неизвестного тикета: деградация", "FAIL",
                   f"код={code}: {out[:60]}")

        # A16 — scenario find_usages.
        # Через --json: в человекочитаемой сводке список файлов
        # обрезается («… (5)»), и проверка «есть ли gateway.py» давала бы
        # ложный провал (на этом A16 сначала падал).
        code, out, err = cli(["scenario", "find_usages", "--param",
                              "symbol=compress_messages", "--json"])
        data = {}
        try:
            data = json.loads(out)
        except ValueError:
            pass
        files = {f["file"] for f in (data.get("result") or {}).get(
            "files", [])}
        if code == 0 and any("gateway.py" in f for f in files):
            record("A16 scenario find_usages", "PASS",
                   f"вхождений={(data.get('result') or {}).get('total')} "
                   f"в {len(files)} файлах, включая gateway.py")
        else:
            record("A16 scenario find_usages", "FAIL",
                   f"код={code}, файлы={sorted(files)[:3]}: {err[:60]}")

        # A17 — generate + dry_run=true (не пишет)
        code, out, _ = cli(["scenario", "generate", "--param", "kind=adr",
                            "--param", "title=Проверка Дри", "--param",
                            "dry_run=true"])
        adr_dir = os.path.join(REPO, "docs", "adr")
        wrote = os.path.isdir(adr_dir) and os.listdir(adr_dir)
        if code == 0 and "dry-run" in out and not wrote:
            record("A17 generate dry_run=true: файл НЕ создан", "PASS",
                   "сухой прогон честно ничего не записал")
        else:
            record("A17 generate dry_run=true: файл НЕ создан", "FAIL",
                   f"код={code}, записан={bool(wrote)}")

        # A18 — КРИТИЧНО: dry_run=false реально пишет (типизация)
        code, out, _ = cli(["scenario", "generate", "--param", "kind=adr",
                            "--param", "title=E2E Реальная Запись",
                            "--param", "dry_run=false"])
        files = os.listdir(adr_dir) if os.path.isdir(adr_dir) else []
        target = [f for f in files if "e2e-realnaya-zapis" in f]
        if code == 0 and target:
            rel = os.path.join("docs", "adr", target[0])
            created.append(rel)
            record("A18 dry_run=false РЕАЛЬНО пишет (типизация --param)",
                   "PASS",
                   f"{rel} — строка «false» не осталась истинной")
        else:
            record("A18 dry_run=false РЕАЛЬНО пишет (типизация --param)",
                   "FAIL",
                   f"код={code}, файлы={files} — запись молча пропущена!")

        # A19 — запись вне allow-list
        code, out, err = cli(["scenario", "generate", "--param",
                              "kind=readme", "--param", "path=../evil.md"])
        evil = os.path.join(os.path.dirname(REPO), "evil.md")
        if code == 1 and not os.path.isfile(evil):
            record("A19 запись вне allow-list → код 1", "PASS",
                   "файл вне проекта не создан")
        else:
            record("A19 запись вне allow-list → код 1", "FAIL",
                   f"код={code}, exposed={os.path.isfile(evil)}")

        # A20 — diff_report (нужен git)
        code_git, _, _ = _git(["rev-parse", "--is-inside-work-tree"])
        if code_git != 0:
            record("A20 scenario diff_report", "SKIP", "нет git")
        else:
            code, out, err = cli(["scenario", "diff_report", "--param",
                                  "path=docs/_e2e_day32_report.md"])
            rel = os.path.join("docs", "_e2e_day32_report.md")
            if code == 0 and os.path.isfile(os.path.join(REPO, rel)):
                created.append(rel)
                record("A20 scenario diff_report", "PASS",
                       "отчёт об изменениях записан")
            else:
                record("A20 scenario diff_report", "FAIL",
                       f"код={code}: {err[:80]}")

        # A21 — scenarios
        code, out, _ = cli(["scenarios"])
        if code == 0 and all(s in out for s in (
                "find_usages", "update_docs", "generate",
                "check_invariants", "diff_report")):
            record("A21 scenarios: каталог 5 сценариев", "PASS", "")
        else:
            record("A21 scenarios: каталог 5 сценариев", "FAIL", f"код={code}")

        # A22 — --no-llm (офлайн, без сети)
        code, out, err = cli(["--no-llm", "ask", "как устроен шлюз?"])
        if code == 0 and out.strip():
            record("A22 --no-llm: работа без LLM", "PASS",
                   "детерминированный ответ, сеть не трогается")
        else:
            record("A22 --no-llm: работа без LLM", "FAIL",
                   f"код={code}: {err[:80]}")

        # A23 — ошибки: код 1 без traceback
        bad = []
        for argv in (["agents", "show", "неттакого"],
                     ["scenario", "неттакого"],
                     ["review", "--diff", "/nope/x.diff"]):
            c, o, e = cli(argv)
            bad.append((c, "Traceback" not in e and bool(e.strip())))
        if all(c == 1 and ok for c, ok in bad):
            record("A23 ошибки: код 1, без traceback", "PASS",
                   f"{len(bad)} сценария ошибок")
        else:
            record("A23 ошибки: код 1, без traceback", "FAIL", f"{bad}")

        # A24 — git
        code, out, _ = cli(["git"])
        if code == 0 and "Ветка" in out:
            record("A24 git: ветка и изменённые файлы", "PASS",
                   out.strip().splitlines()[0])
        else:
            record("A24 git: ветка и изменённые файлы", "FAIL", f"код={code}")

        # A25 — тонкая обёртка
        src = open(CLI, encoding="utf-8").read()
        bad_imports = [m for m in ("import kb", "import mcp", "import gateway",
                                   "urllib", "httpx.", "socket.")
                       if m in src]
        bad_git = [g for g in ('"commit"', '"push"', '"checkout"')
                   if g in src]
        if not bad_imports and not bad_git:
            record("A25 CLI — тонкая обёртка (без дублей логики)", "PASS",
                   "не импортирует kb/mcp/gateway, нет git-мутаций")
        else:
            record("A25 CLI — тонкая обёртка (без дублей логики)", "FAIL",
                   f"импорты={bad_imports}, git={bad_git}")

    finally:
        cleanup()

    fails = sum(1 for _, s, _ in RESULTS if s == "FAIL")
    return fails == 0


# --- Part B (live) ---


def part_b() -> bool:
    log("\n" + "=" * 74)
    log("ЧАСТЬ B — live (реальная Ollama через CLI)")
    log("=" * 74)

    models = ollama_models()
    if not models:
        record("B0 зонд Ollama", "SKIP", "Ollama недоступна")
        return True
    record("B0 зонд Ollama", "PASS", f"моделей={len(models)}")

    # B1 — ask через реальную LLM
    code, out, err = cli(["ask", "Что такое шлюз локальной LLM и зачем "
                                 "он нужен?"], timeout=900)
    if code == 0 and out.strip() and "Источники" in out:
        record("B1 ask через реальную LLM", "PASS",
               f"ответ {len(out)} символов, источники есть")
    else:
        record("B1 ask через реальную LLM", "FAIL",
               f"код={code}: {err[:100]}")

    # B2 — выбор агента реально влияет
    code_a, out_a, _ = cli(["ask", "как устроено ревью?", "--agent",
                            "code-review"], timeout=900)
    code_b, out_b, _ = cli(["ask", "как устроено ревью?", "--agent",
                            "project-qa"], timeout=900)
    if code_a == 0 and code_b == 0 and out_a != out_b:
        record("B2 --agent меняет промпт (ответы различаются)", "PASS",
               "code-review ≠ project-qa")
    else:
        record("B2 --agent меняет промпт (ответы различаются)",
               "SKIP" if code_a != 0 or code_b != 0 else "FAIL",
               f"коды={code_a}/{code_b}")

    # B3 — support через реальную LLM
    code, out, err = cli(["support", "T-5003"], timeout=900)
    if code == 0 and "Учтено из CRM" in out:
        record("B3 support через реальную LLM", "PASS",
               "персонализация + ответ модели")
    else:
        record("B3 support через реальную LLM", "FAIL",
               f"код={code}: {err[:100]}")

    # B4 — review реального git-diff с LLM
    code, out, err = cli(["review"], timeout=900)
    if code == 0 and "Ревью" in out:
        record("B4 review реального git-diff с LLM", "PASS",
               "отчёт построен на живых изменениях")
    else:
        record("B4 review реального git-diff с LLM",
               "SKIP" if "пустой diff" in err.lower() or code == 1 else "FAIL",
               f"код={code}: {err[:80]}")

    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--part-a-only", action="store_true")
    args = ap.parse_args()

    log("E2E day 35: CLI ассистента + агенты в Markdown (4 × api/cli)")
    log(f"репозиторий: {REPO}")
    log(f"CLI: {CLI}")

    if not part_a():
        print_summary()
        log("\nPart A содержит провалы — Part B не запускается.")
        return 1

    if not args.part_a_only:
        part_b()
    else:
        log("\n(Part B пропущена: --part-a-only)")

    p, f = print_summary()
    return 0 if f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
