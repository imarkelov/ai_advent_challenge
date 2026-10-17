# -*- coding: utf-8 -*-
"""E2E дня 32 — автоматическое AI-ревью PR (пайплайн, как в GitHub Actions).

Задание: пайплайн запускается на PR, ассистент получает diff, использует
RAG (документация + код) и возвращает текст ревью с тремя блоками:
потенциальные баги, архитектурные проблемы, рекомендации.

Скрипт проверяет пайплайн ТАК, как его запускает Action:

* CLI — реальный подпроцесс (`python studio/backend/cli.py ...`);
* рабочее дерево — СБОРНЫЙ git-репозиторий: база `main` + ветка PR,
  diff берётся `git diff main...HEAD`, как `git diff origin/<base>...HEAD`
  в CI. Рабочий репозиторий разработчика не затрагивается;
* модель в Part A — фейковый OpenAI-совместимый сервер, в Part B —
  реальная Ollama.

Часть A (offline, MUST PASS):
  A1  `pr-review check` — предполётная проверка (RAG/LLM/git/PR)
  A2  `index` — индекс корпуса построен, RAG доступен
  A3  diff файлом → изменённые файлы с типом (added/modified/...)
  A4  три блока ревью с моделью (баги/архитектура/рекомендации)
  A5  модель видела: промпт агента, дифф, находки и RAG-фрагменты
  A6  RAG реально находит документацию проекта (docs/USERS.md)
  A7  детерминированные проверки: секрет, bare_except, missing_test
  A8  diff из git (`--base main`) — путь CI
  A9  diff из stdin (`--diff -`) — пайп `git diff | cli`
  A10 `--fail-on-high` → код 1 (блокирующий режим)
  A11 без ключа модели — детерминированный отчёт, код 0
  A12 модель ответила ошибкой — честная деградация, не падение
  A13 `--annotations` — workflow-команды на строки diff
  A14 `--step-summary` — сводка для $GITHUB_STEP_SUMMARY
  A15 `--out` + `--json-out` — артефакты Action (markdown + JSON)
  A16 `--comment`: создаёт комментарий, повторно — обновляет (PATCH)
  A17 PR из форка: diff через API (`--github-pr`); без доступа — код 1
      без traceback
  A18 пустой diff → код 1 без traceback
  A19 файлы пайплайна: триггер `pull_request`, шаги check/index/run,
      форки и права, секрет модели
  A20 промпт агента `agents/pr-review.cli.md` задаёт три раздела
  A21 markdown-отчёт: файлы, блоки, RAG

Часть B (live, SKIP без Ollama):
  B1  зонд Ollama
  B2  модель через `LLM_*` (локальная Ollama) отвечает
  B3  ревью модели: баги / архитектура / рекомендации
  B4  ревью ссылается на файлы из diff/RAG

Запуск из корня репозитория:
    python scripts/e2e_day32.py [--part-a-only]
Код возврата: 0 — успех (Part A весь PASS, Part B без FAIL), 1 — провал.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(REPO, "studio", "backend")
CLI = os.path.join(BACKEND, "cli.py")

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(REPO, ".env"))
except Exception:                                   # noqa: BLE001
    pass

OLLAMA = os.environ.get("OLLAMA_BASE_URL",
                        "http://localhost:11434/v1").rstrip("/")
OLLAMA_NATIVE = OLLAMA[:-3] if OLLAMA.endswith("/v1") else OLLAMA
# Модель для Part B: локальная Ollama из .env или первая чат-модель.
LIVE_MODEL = os.environ.get("DEV_ASSISTANT_MODEL") or ""

# --- инфраструктура отчёта (паттерн e2e_day24…31) ---

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


# --- сборный репозиторий ---

CORPUS = ["README.md", "docs", "agents", "openspec", "data/crm",
          "studio/backend", "studio/frontend/src", "scripts"]

PR_DIFF = """\
diff --git a/studio/backend/users_api.py b/studio/backend/users_api.py
new file mode 100644
--- /dev/null
+++ b/studio/backend/users_api.py
@@ -0,0 +1,18 @@
+\"\"\"Пользователи: REST-слой.\"\"\"
+from fastapi import FastAPI
+
+app = FastAPI()
+
+API_KEY = "sk-live-1234567890abcdefghijkl"
+
+
+@app.get("/users/{user_id}")
+def get_user(user_id: str):
+    try:
+        return {"id": user_id}
+    except:
+        return None
+
+
+def delete_user(user_id: str):
+    return {"deleted": user_id}
"""

DOC_BASE = ("# Пользователи\n\nМодуль `users_api` отдаёт профиль по REST.\n"
            "Удаление доступно только администратору через сервис "
            "`users_service`.\n")


def _git(root: str, *args: str) -> str:
    p = subprocess.run(["git"] + list(args), cwd=root, capture_output=True,
                       text=True)
    if p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {p.stderr.strip()}")
    return p.stdout


def make_repo(root: str) -> None:
    """Корпус проекта + git-история base(main) → PR(feature/users)."""
    for rel in CORPUS:
        src, dst = os.path.join(REPO, rel), os.path.join(root, rel)
        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns(
                                "__pycache__", "node_modules", "dist"))
        elif os.path.isfile(src):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
    os.makedirs(os.path.join(root, "data", "kb"), exist_ok=True)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "e2e@example.com")
    _git(root, "config", "user.name", "E2E")
    with open(os.path.join(root, "docs", "USERS.md"), "w",
              encoding="utf-8") as f:
        f.write(DOC_BASE)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "база")
    _git(root, "checkout", "-qb", "feature/users")
    with open(os.path.join(root, "docs", "USERS.md"), "w",
              encoding="utf-8") as f:
        f.write(DOC_BASE.replace("Модуль `users_api` отдаёт профиль по REST.",
                                 "Модуль `users_api` отдаёт профиль по REST "
                                 "и удаляет пользователя."))
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "docs: users api")


def cli(root: str, args: list, stdin: str | None = None,
        env_extra: dict | None = None, timeout: int = 600) -> tuple:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("LLM_", "GITHUB_", "OLLAMA_", "REVIEW_"))}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(env_extra or {})
    proc = subprocess.run([sys.executable, CLI, "--repo", root] + args,
                          cwd=BACKEND, capture_output=True, text=True,
                          input=stdin, env=env, timeout=timeout)
    return proc.returncode, proc.stdout, proc.stderr


# --- фейковые сервисы: модель и GitHub ---

ANSWER = """\
## Потенциальные баги
- `studio/backend/users_api.py:8` — секрет в коде, утечёт в образ.
- `studio/backend/users_api.py:14` — `except:` глотает `KeyboardInterrupt`.

## Архитектурные проблемы
- `studio/backend/users_api.py` — REST-слой пишет напрямую, мимо
  `users_service` из `docs/USERS.md`.

## Рекомендации
- Вынесите `API_KEY` в `.env`.
- Добавьте `tests/test_users_api.py`.
"""


class FakeLLM:
    def __init__(self):
        self.requests: list[dict] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n).decode("utf-8"))
                outer.requests.append({"path": self.path, "body": body,
                                       "auth": self.headers.get(
                                           "Authorization")})
                raw = json.dumps({
                    "choices": [{"message": {"content": ANSWER},
                                 "finish_reason": "stop"}],
                }).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"
        threading.Thread(target=self.server.serve_forever,
                         daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class FakeGitHub:
    """REST GitHub: diff PR (для форков) и комментарии PR."""

    MARKER = "<!-- ai-pr-review -->"

    def __init__(self):
        self.calls: list[tuple] = []
        self.comments: list[dict] = []
        self.created = 0
        self.updated = 0
        outer = self

        class H(BaseHTTPRequestHandler):
            def _send(self, code, payload):
                raw = (payload if isinstance(payload, str)
                       else json.dumps(payload)).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):  # noqa: N802
                outer.calls.append(("GET", self.path))
                if re.search(r"/pulls/\d+$", self.path) \
                        and "diff" in (self.headers.get("Accept") or ""):
                    self._send(200, PR_DIFF)
                elif "/comments" in self.path:
                    self._send(200, outer.comments)
                else:
                    self._send(200, {})

            def do_POST(self):  # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n).decode("utf-8"))
                outer.calls.append(("POST", self.path))
                outer.created += 1
                c = {"id": 500 + outer.created, "body": body["body"]}
                outer.comments.append(c)
                self._send(201, c)

            def do_PATCH(self):  # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n).decode("utf-8"))
                outer.calls.append(("PATCH", self.path))
                outer.updated += 1
                self._send(200, {"id": 500, "body": body["body"]})

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever,
                         daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def _error_server() -> str:
    """OpenAI-совместимый сервер, который всегда отвечает HTTP 500."""
    class H(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            n = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(n)
            body = b'{"error": "boom"}'
            self.send_response(500)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_port}/v1"


def _json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


# --- Part A (offline) ---


def part_a(tmp: str) -> bool:
    log("\n" + "=" * 74)
    log("ЧАСТЬ A — офлайн: пайплайн ревью PR как в GitHub Action")
    log("=" * 74)

    repo = os.path.join(tmp, "repo")
    os.makedirs(repo)
    make_repo(repo)
    llm, gh = FakeLLM(), FakeGitHub()
    diff_path = os.path.join(tmp, "pr.diff")
    with open(diff_path, "w", encoding="utf-8") as f:
        f.write(PR_DIFF)
    env_llm = {"LLM_MODEL": "fake/reviewer", "LLM_BASE_URL": llm.url,
               "LLM_API_KEY": "k"}
    env_gh = {"GITHUB_API_URL": gh.url, "GITHUB_TOKEN": "e2e"}
    try:
        # A1 — предполётная проверка
        code, out, err = cli(repo, ["--no-llm", "pr-review", "check"])
        if code == 0 and "RAG" in out and "git: доступен" in out:
            record("A1 pr-review check: RAG/LLM/git/PR", "PASS",
                   out.strip().splitlines()[0][:60])
        else:
            record("A1 pr-review check: RAG/LLM/git/PR", "FAIL",
                   f"код={code}: {err[:100]}")

        # A2 — индекс
        code, out, err = cli(repo, ["index"])
        if code == 0 and "Индекс собран" in out:
            record("A2 index: индекс корпуса построен", "PASS",
                   out.strip().splitlines()[0][:60])
        else:
            record("A2 index: индекс корпуса построен", "FAIL",
                   f"код={code}: {err[:100]}")

        # A3 — изменённые файлы и статусы
        j3 = os.path.join(tmp, "r3.json")
        code, _, err = cli(repo, ["--no-llm", "pr-review", "run", "--diff",
                                  diff_path, "--json-out", j3])
        files3 = _json(j3).get("changed_files") or []
        if code == 0 and files3 and files3[0]["status"] == "added" \
                and files3[0]["path"].endswith("users_api.py"):
            record("A3 diff → изменённые файлы с типом", "PASS",
                   f"{files3[0]['path']} ({files3[0]['status']}, "
                   f"+{files3[0]['added']})")
        else:
            record("A3 diff → изменённые файлы с типом", "FAIL",
                   f"код={code}: {err[:100]}")

        # A4–A7 — модель: три блока, вход и выход
        md4, j4 = os.path.join(tmp, "r4.md"), os.path.join(tmp, "r4.json")
        code, _, err = cli(repo, ["pr-review", "run", "--diff", diff_path,
                                  "--out", md4, "--json-out", j4],
                           env_extra=env_llm, timeout=900)
        d4 = _json(j4)
        rv = d4.get("review") or {}
        if code == 0 and d4.get("llm") and rv.get("bugs") \
                and rv.get("architecture") and rv.get("recommendations"):
            record("A4 модель: баги / архитектура / рекомендации", "PASS",
                   f"{len(rv['bugs'])}/{len(rv['architecture'])}/"
                   f"{len(rv['recommendations'])} пунктов")
        else:
            record("A4 модель: баги / архитектура / рекомендации", "FAIL",
                   f"код={code}, llm={d4.get('llm')}, "
                   f"err={d4.get('llm_error')}")

        req = (llm.requests or [{}])[-1].get("body") or {}
        msgs = req.get("messages") or []
        system = msgs[0]["content"] if msgs else ""
        user = msgs[1]["content"] if len(msgs) > 1 else ""
        seen = {
            "промпт": "старший инженер" in system.lower(),
            "дифф": "users_api.py" in user and "except:" in user,
            "находки": "Детерминированные находки" in user,
            "RAG": "Фрагменты корпуса проекта" in user,
        }
        if all(seen.values()):
            record("A5 модель видит: промпт + дифф + находки + RAG", "PASS",
                   ", ".join(seen))
        else:
            record("A5 модель видит: промпт + дифф + находки + RAG", "FAIL",
                   f"{seen}, модель {req.get('model')}")

        ctx = d4.get("corpus_context") or []
        hit = [c for c in ctx if "USERS.md" in (c.get("file") or "")]
        if hit:
            record("A6 RAG находит документацию проекта", "PASS",
                   f"{hit[0]['file']} :: {hit[0]['section']} "
                   f"(из-за {hit[0]['why']})")
        else:
            record("A6 RAG находит документацию проекта", "FAIL",
                   f"файлы: {[c.get('file') for c in ctx][:4]}")

        rules = {f.get("rule") for f in (d4.get("findings") or [])}
        need = {"secret", "bare_except", "missing_test"}
        if need <= rules:
            by = d4["summary"]["by_severity"]
            record("A7 проверки: секрет, bare_except, missing_test", "PASS",
                   f"находок {d4['summary']['findings']} "
                   f"(критично {by['high']})")
        else:
            record("A7 проверки: секрет, bare_except, missing_test", "FAIL",
                   f"нет {sorted(need - rules)}")

        # A8 — diff из git (путь CI)
        j8 = os.path.join(tmp, "r8.json")
        code, _, err = cli(repo, ["--no-llm", "pr-review", "run", "--base",
                                  "main", "--json-out", j8])
        d8 = _json(j8)
        if code == 0 and d8.get("source") == "git:main...HEAD" \
                and any(f["path"] == "docs/USERS.md"
                        for f in d8.get("changed_files", [])):
            record("A8 diff из git (--base main)", "PASS", d8.get("source"))
        else:
            record("A8 diff из git (--base main)", "FAIL",
                   f"код={code}: {err[:100]}")

        # A9 — diff из stdin
        j9 = os.path.join(tmp, "r9.json")
        code, _, _err = cli(repo, ["--no-llm", "pr-review", "run", "--diff",
                                   "-", "--json-out", j9], stdin=PR_DIFF)
        if code == 0 and _json(j9).get("source") == "text":
            record("A9 diff из stdin (пайп)", "PASS", "source=text")
        else:
            record("A9 diff из stdin (пайп)", "FAIL", f"код={code}")

        # A10 — fail-on-high
        code, _, err = cli(repo, ["--no-llm", "pr-review", "run", "--diff",
                                  diff_path, "--fail-on-high"])
        if code == 1 and "критично" in err:
            record("A10 --fail-on-high → код 1", "PASS", "блокирующий режим")
        else:
            record("A10 --fail-on-high → код 1", "FAIL", f"код={code}")

        # A11 — без ключа модели
        j11, m11 = os.path.join(tmp, "r11.json"), os.path.join(tmp, "r11.md")
        code, _, _err = cli(repo, ["pr-review", "run", "--diff", diff_path,
                                   "--out", m11, "--json-out", j11])
        d11, text11 = _json(j11), _read(m11)
        titles = ("### Потенциальные баги", "### Архитектурные проблемы",
                  "### Рекомендации")
        if code == 0 and d11.get("llm") is False and d11.get("llm_error") \
                and all(t in text11 for t in titles):
            record("A11 без модели: отчёт и 3 блока, код 0", "PASS",
                   f"причина: {str(d11['llm_error'])[:45]}")
        else:
            record("A11 без модели: отчёт и 3 блока, код 0", "FAIL",
                   f"код={code}, llm={d11.get('llm')}")

        # A12 — модель ответила ошибкой
        j12 = os.path.join(tmp, "r12.json")
        code, _, _err = cli(repo, ["pr-review", "run", "--diff", diff_path,
                                   "--json-out", j12],
                            env_extra={"LLM_MODEL": "fake/x",
                                       "LLM_BASE_URL": _error_server()})
        d12 = _json(j12)
        if code == 0 and d12.get("llm") is False \
                and "HTTP 500" in str(d12.get("llm_error")):
            record("A12 ошибка модели → деградация, не падение", "PASS",
                   d12.get("llm_error"))
        else:
            record("A12 ошибка модели → деградация, не падение", "FAIL",
                   f"код={code}: {d12.get('llm_error')}")

        # A13 — аннотации
        code, out, _err = cli(repo, ["--no-llm", "pr-review", "run", "--diff",
                                     diff_path, "--annotations", "--out",
                                     os.path.join(tmp, "a13.md")])
        if code == 0 and "::error file=studio/backend/users_api.py" in out \
                and "::warning" in out:
            record("A13 --annotations: ошибки/предупреждения на строки",
                   "PASS", "workflow-команды GitHub")
        else:
            record("A13 --annotations: ошибки/предупреждения на строки",
                   "FAIL", f"код={code}")

        # A14 — step summary
        summary = os.path.join(tmp, "summary.md")
        code, _, _err = cli(repo, ["--no-llm", "pr-review", "run", "--diff",
                                   diff_path, "--step-summary", summary,
                                   "--out", os.path.join(tmp, "a14.md")])
        body = _read(summary)
        if code == 0 and "AI-ревью" in body and "Потенциальные баги" in body:
            record("A14 --step-summary: сводка в Summary запуска", "PASS",
                   f"{len(body)} символов")
        else:
            record("A14 --step-summary: сводка в Summary запуска", "FAIL",
                   f"код={code}")

        # A15 — артефакты Action
        art_md = os.path.join(tmp, "art.md")
        art_js = os.path.join(tmp, "art.json")
        cli(repo, ["--no-llm", "pr-review", "run", "--diff", diff_path,
                   "--out", art_md, "--json-out", art_js])
        if os.path.isfile(art_md) and os.path.isfile(art_js) \
                and _json(art_js).get("changed_files"):
            record("A15 --out + --json-out: артефакты Action", "PASS",
                   f"markdown {os.path.getsize(art_md)} Б, "
                   f"JSON {os.path.getsize(art_js)} Б")
        else:
            record("A15 --out + --json-out: артефакты Action", "FAIL", "")

        # A16 — комментарий в PR (создание + обновление)
        code, _, err = cli(repo, ["--no-llm", "pr-review", "run", "--diff",
                                  diff_path, "--comment", "o/r#42", "--out",
                                  os.path.join(tmp, "a16.md")],
                           env_extra=env_gh)
        first_ok = (code == 0 and gh.created == 1 and gh.comments
                    and FakeGitHub.MARKER in gh.comments[0]["body"]
                    and "Потенциальные баги" in gh.comments[0]["body"])
        cli(repo, ["--no-llm", "pr-review", "run", "--diff", diff_path,
                   "--comment", "o/r#42", "--out",
                   os.path.join(tmp, "a16.md")], env_extra=env_gh)
        if first_ok and gh.created == 1 and gh.updated == 1:
            record("A16 --comment: создаёт, повторно обновляет", "PASS",
                   "идемпотентный комментарий в PR")
        else:
            record("A16 --comment: создаёт, повторно обновляет", "FAIL",
                   f"created={gh.created}, updated={gh.updated}: {err[:80]}")

        # A17 — PR из форка: diff через API
        j17 = os.path.join(tmp, "r17.json")
        code, _, err = cli(repo, ["--no-llm", "pr-review", "run",
                                  "--github-pr", "o/r#7", "--json-out", j17],
                           env_extra=env_gh)
        changed17 = _json(j17).get("changed_files") or []
        if code == 0 and changed17 \
                and changed17[0]["path"].endswith("users_api.py"):
            record("A17 PR из форка: diff через API GitHub", "PASS",
                   "ветка форка в рабочем дереве не нужна")
        else:
            record("A17 PR из форка: diff через API GitHub", "FAIL",
                   f"код={code}: {err[:100]}")
        code, _, err = cli(repo, ["--no-llm", "pr-review", "run",
                                  "--github-pr", "o/r#7"],
                           env_extra={"GITHUB_API_URL": "http://127.0.0.1:1"})
        if code == 1 and "Traceback" not in err and "GitHub" in err:
            record("A17b API GitHub недоступен → код 1 без traceback",
                   "PASS", err.strip().splitlines()[-1][:60])
        else:
            record("A17b API GitHub недоступен → код 1 без traceback",
                   "FAIL", f"код={code}: {err[:100]}")

        # A18 — пустой diff
        empty = os.path.join(tmp, "empty.diff")
        open(empty, "w", encoding="utf-8").close()
        code, _, err = cli(repo, ["pr-review", "run", "--diff", empty])
        if code == 1 and "пустой diff" in err.lower() \
                and "Traceback" not in err:
            record("A18 пустой diff → код 1 без traceback", "PASS",
                   err.strip()[:60])
        else:
            record("A18 пустой diff → код 1 без traceback", "FAIL",
                   f"код={code}: {err[:100]}")

        # A19 — файлы пайплайна
        wf = _read(os.path.join(REPO, ".github", "workflows", "ai-review.yml"))
        act = _read(os.path.join(REPO, ".github", "actions", "ai-pr-review",
                                 "action.yml"))
        checks = {
            "pull_request": bool(re.search(r"^\s+pull_request:", wf, re.M)),
            "check": "cli.py pr-review check" in act,
            "index": "cli.py index" in act,
            "run": "cli.py pr-review run" in act,
            "форк": "--github-pr" in act,
            "права": "pull-requests: write" in wf,
            "секрет": "LLM_API_KEY" in wf,
        }
        if all(checks.values()):
            record("A19 Action: триггер PR, check→index→run, форки", "PASS",
                   ", ".join(checks))
        else:
            record("A19 Action: триггер PR, check→index→run, форки", "FAIL",
                   f"нет: {[k for k, v in checks.items() if not v]}")

        # A20 — промпт агента
        prompt = _read(os.path.join(REPO, "agents", "pr-review.cli.md"))
        if all(t in prompt for t in ("Потенциальные баги",
                                     "Архитектурные проблемы",
                                     "Рекомендации")) \
                and "не выдумывай" in prompt.lower():
            record("A20 agents/pr-review.cli.md: три раздела", "PASS",
                   f"агент {d4.get('agent')}")
        else:
            record("A20 agents/pr-review.cli.md: три раздела", "FAIL", "")

        # A21 — markdown-отчёт для комментария PR
        text4 = _read(md4)
        if "Изменённые файлы" in text4 and "| Файл | Статус |" in text4 \
                and "RAG: документация и код" in text4:
            record("A21 markdown-отчёт: файлы, блоки, RAG", "PASS",
                   f"{len(text4)} символов")
        else:
            record("A21 markdown-отчёт: файлы, блоки, RAG", "FAIL",
                   text4[:120])
    finally:
        llm.close()
        gh.close()

    return not any(s == "FAIL" for _, s, _ in RESULTS)


# --- Part B (live) ---


def ollama_models() -> tuple[list, list]:
    """(все модели, чат-модели).

    Эмбеддер (bge-m3, nomic-embed-text) в Ollama тоже отдаёт 400 на
    `/chat/completions` — для ревью нужна модель с возможностью
    `completion` (её отдаёт `/api/tags` в `capabilities`). Найденная
    ошибкой: первая модель в списке была bge-m3, и Part B падал на неё.
    """
    try:
        with urllib.request.urlopen(OLLAMA_NATIVE + "/api/tags",
                                    timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return [], []
    models = data.get("models", [])
    names = [m.get("name") for m in models if m.get("name")]
    chat = [m.get("name") for m in models if m.get("name")
            and "completion" in (m.get("capabilities") or ["completion"])
            and "embedding" not in (m.get("capabilities") or [])]
    return names, chat


def part_b(tmp: str) -> None:
    log("\n" + "=" * 74)
    log("ЧАСТЬ B — live: ревью PR реальной моделью Ollama")
    log("=" * 74)

    models, chat = ollama_models()
    if not models:
        record("B1 зонд Ollama", "SKIP", "Ollama недоступна")
        return
    record("B1 зонд Ollama", "PASS",
           f"моделей={len(models)}, чат-моделей={len(chat)}")
    if not chat:
        record("B2 модель для ревью", "SKIP", "нет чат-моделей в Ollama")
        return

    model = LIVE_MODEL if LIVE_MODEL in chat else chat[0]
    log(f"  модель для Part B: {model}")
    env = {"LLM_MODEL": model, "LLM_BACKEND": "ollama",
           "LLM_BASE_URL": OLLAMA, "LLM_API_KEY": "ollama"}
    # Отдельный репозиторий: Part A уже строил свой и оставил индекс.
    repo = os.path.join(tmp, "repo-live")
    os.makedirs(repo)
    make_repo(repo)
    cli(repo, ["index"])
    diff_path = os.path.join(tmp, "pr.diff")
    with open(diff_path, "w", encoding="utf-8") as f:
        f.write(PR_DIFF)
    out = os.path.join(tmp, "live.json")
    code, _, err = cli(repo, ["pr-review", "run", "--diff", diff_path,
                              "--json-out", out], env_extra=env,
                       timeout=1800)
    data = _json(out)
    rv = data.get("review") or {}
    if code == 0 and data.get("llm"):
        record("B2 модель через LLM_* (локальная Ollama)", "PASS",
               f"модель {model}, промпт "
               f"{data.get('prompt', {}).get('user_chars')} символов")
    else:
        record("B2 модель через LLM_* (локальная Ollama)", "FAIL",
               f"код={code}: {data.get('llm_error') or err[:120]}")
        return

    filled = [k for k in ("bugs", "architecture", "recommendations")
              if rv.get(k)]
    if len(filled) == 3:
        record("B3 ревью модели: все три блока заполнены", "PASS",
               f"{len(rv['bugs'])} багов, {len(rv['architecture'])} "
               f"архитектурных, {len(rv['recommendations'])} рекомендаций")
    elif len(filled) == 2:
        record("B3 ревью модели: два блока из трёх", "PASS",
               f"непустые: {filled} — модель могла честно не найти багов")
    else:
        record("B3 ревью модели: баги / архитектура / рекомендации", "FAIL",
               f"непустые: {filled}")

    joined = " ".join(rv.get("bugs", []) + rv.get("architecture", [])
                      + rv.get("recommendations", []))
    if re.search(r"users_api\.py|docs/", joined):
        record("B4 ревью ссылается на файлы из diff/RAG", "PASS",
               f"RAG-фрагментов: {len(data.get('corpus_context') or [])}")
    else:
        record("B4 ревью ссылается на файлы из diff/RAG", "FAIL",
               joined[:120])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--part-a-only", action="store_true")
    args = ap.parse_args()

    log("E2E день 32: автоматическое AI-ревью PR (пайплайн GitHub Actions)")
    log(f"репозиторий: {REPO}")
    log(f"CLI: {CLI}")

    tmp = tempfile.mkdtemp(prefix="e2e_day32_")
    try:
        if not part_a(tmp):
            print_summary()
            log("\nPart A содержит провалы — Part B не запускается.")
            return 1
        if not args.part_a_only:
            part_b(tmp)
        else:
            log("\n(Part B пропущена: --part-a-only)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    _, f = print_summary()
    return 0 if f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
