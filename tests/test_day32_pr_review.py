"""Тесты дня 32 — автоматизация ревью PR (CLI-пайплайн, без веба).

Проверяют ОБЕЩАНИЕ задания целиком:

1. получения diff и изменённых файлов (файл, stdin, git, API GitHub);
2. RAG по документации и коду (фрагменты корпуса попадают в промпт);
3. ревью от модели с тремя блоками: потенциальные баги,
   архитектурные проблемы, рекомендации;
4. запуск из GitHub Action (тип триггера `pull_request`, вызов CLI,
   публикация комментария, аннотации, exit-коды).

CLI вызывается как подпроцесс (`python studio/backend/cli.py ...`) в
СБОРОЧНОМ репозитории: порча индекса/диффа в тестах не должна касаться
рабочего дерева разработчика. Сеть не используется: модель — заглушка
через фейковый OpenAI-совместимый сервер и записи в `.studio/`.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(REPO, "studio", "backend")
CLI = os.path.join(BACKEND, "cli.py")
# Корень репозитория — раньше backend: в нём есть `agent.py` (день 1),
# и при обратном порядке flat-импорт `from agent import StudioAgent`
# попадает в него вместо `studio/backend/agent.py`.
sys.path.insert(0, BACKEND)
sys.path.insert(0, REPO)

import assistant as A          # noqa: E402
import pr_review as P          # noqa: E402

# Корпус, который копируется в сборный репозиторий: агенты и документы,
# по которым проверяется RAG. Код бэкенда не нужен — CLI берётся из
# рабочего дерева и получает сборный корень через `--repo`.
CORPUS = ["README.md", "docs", "agents", "openspec", "data/crm",
          "studio/backend", "studio/frontend/src", "scripts"]


# ---------------------------------------------------------------------------
# Сборный репозиторий и helpers
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def sandbox(tmp_path_factory):
    """Сборный репозиторий: корпус проекта + git-история base → PR."""
    root = tmp_path_factory.mktemp("day32_repo")
    for rel in CORPUS:
        src = os.path.join(REPO, rel)
        dst = root / rel
        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns(
                                "__pycache__", "node_modules", "dist"))
        elif os.path.isfile(src):
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    (root / "data" / "kb").mkdir(parents=True, exist_ok=True)

    def git(*args):
        return subprocess.run(["git"] + list(args), cwd=root,
                              capture_output=True, text=True, check=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    # База: документация и «существующий» модуль.
    (root / "docs" / "USERS.md").write_text(
        "# Пользователи\n\nМодуль `users_api` отдаёт профиль по REST.\n"
        "Удаление доступно только администратору через сервис "
        "`users_service`.\n", encoding="utf-8")
    (root / "docs" / "ARCH.md").write_text(
        "# Архитектура\n\nСлои: API (`studio/backend/main.py`) → сервис → "
        "клиент БД. Модуль не вызывает базу напрямую.\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "база")
    git("checkout", "-qb", "feature/users")
    (root / "docs" / "USERS.md").write_text(
        "# Пользователи\n\nМодуль `users_api` отдаёт профиль по REST и "
        "удаляет пользователя.\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-qm", "docs: users api")
    return root


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

EMPTY_INDEX_DIFF = PR_DIFF.replace(
    'API_KEY = "sk-live-1234567890abcdefghijkl"', "API_KEY = 'ok'")


def _env(root, **extra):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("LLM_", "GITHUB_", "OLLAMA_",
                                "REVIEW_", "OPENAI_"))}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(extra)
    return env


def cli(root, *args, stdin: str | None = None, env: dict | None = None,
        timeout: int = 600):
    """CLI подпроцессом против сборного корня → (код, stdout, stderr)."""
    proc = subprocess.run(
        [sys.executable, CLI, "--repo", str(root)] + list(args),
        cwd=os.path.dirname(CLI), capture_output=True, text=True,
        input=stdin, env=env or _env(root), timeout=timeout)
    return proc.returncode, proc.stdout, proc.stderr


@pytest.fixture(scope="module")
def indexed(sandbox):
    """Репозиторий с построенным индексом корпуса (RAG доступен)."""
    code, out, err = cli(sandbox, "index")
    assert code == 0, err
    assert "Индекс собран" in out
    return sandbox


@pytest.fixture()
def diff_file(tmp_path):
    p = tmp_path / "pr.diff"
    p.write_text(PR_DIFF, encoding="utf-8")
    return str(p)


# ---------------------------------------------------------------------------
# Фейковый OpenAI-совместимый сервер (модель в тестах — без сети наружу)
# ---------------------------------------------------------------------------

ANSWER = textwrap.dedent("""\
    ## Потенциальные баги
    - `studio/backend/users_api.py:8` — секрет в коде, утечёт в образ.
    - `studio/backend/users_api.py:14` — `except:` глотает `KeyboardInterrupt`.

    ## Архитектурные проблемы
    - `studio/backend/users_api.py` — REST-слой пишет в ответ напрямую,
      мимо сервиса `users_service`, описанного в `docs/USERS.md`.

    ## Рекомендации
    - Вынесите `API_KEY` в `.env` и добавьте `.env.example`.
    - Добавьте `tests/test_users_api.py` на оба эндпоинта.
    """)


class FakeLLM:
    """Мини-сервер `/v1/chat/completions`: копит запросы, отдаёт ANSWER."""

    def __init__(self, answer: str = ANSWER, status: int = 200):
        self.requests: list[dict] = []
        self.answer = answer
        self.status = status
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n).decode("utf-8"))
                outer.requests.append({
                    "path": self.path, "body": body,
                    "auth": self.headers.get("Authorization")})
                if outer.status != 200:
                    self.send_response(outer.status)
                    self.end_headers()
                    self.wfile.write(b'{"error": "boom"}')
                    return
                payload = {"choices": [{
                    "message": {"role": "assistant",
                                "content": outer.answer},
                    "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 10}}
                raw = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *a):      # без шума в pytest -s
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture()
def fake_llm():
    srv = FakeLLM()
    yield srv
    srv.close()


class FakeGitHub:
    """Мини-REST API GitHub: комментарии PR (GET/POST/PATCH)."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.created: list[dict] = []
        self.updated: list[dict] = []
        self.comments: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _send(self, code, payload):
                raw = json.dumps(payload).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):  # noqa: N802
                outer.calls.append(("GET", self.path))
                self._send(200, outer.comments)

            def do_POST(self):  # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n).decode("utf-8")
                auth = self.headers.get("Authorization")
                outer.calls.append(("POST", self.path))
                outer.created.append({"path": self.path, "body": body,
                                      "auth": auth})
                c = {"id": 100 + len(outer.created),
                     "body": json.loads(body)["body"]}
                outer.comments.append(c)
                self._send(201, c)

            def do_PATCH(self):  # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n).decode("utf-8")
                outer.calls.append(("PATCH", self.path))
                outer.updated.append({"path": self.path, "body": body})
                cid = int(self.path.rstrip("/").rsplit("/", 1)[-1])
                for c in outer.comments:
                    if c["id"] == cid:
                        c["body"] = json.loads(body)["body"]
                        self._send(200, c)
                        return
                self._send(404, {"message": "not found"})

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever,
                         daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


# ---------------------------------------------------------------------------
# 1. Diff и изменённые файлы
# ---------------------------------------------------------------------------

class TestDiffAndChangedFiles:
    def test_changed_files_statuses(self):
        diff = textwrap.dedent("""\
            diff --git a/a.py b/a.py
            new file mode 100644
            --- /dev/null
            +++ b/a.py
            @@ -0,0 +1 @@
            +x = 1
            diff --git a/old.md b/new.md
            similarity index 90%
            rename from old.md
            rename to new.md
            --- a/old.md
            +++ b/new.md
            @@ -1 +1,2 @@
             # t
            +2
            diff --git a/gone.txt b/gone.txt
            deleted file mode 100644
            --- a/gone.txt
            +++ /dev/null
            @@ -1 +0,0 @@
            -bye
            """)
        files = P.build_changed_files(diff)
        by = {f["path"]: f for f in files}
        assert by["a.py"]["status"] == "added"
        assert by["new.md"]["status"] == "renamed"
        assert by["new.md"]["old_path"] == "old.md"
        assert by["gone.txt"]["status"] == "deleted"
        assert by["new.md"]["added"] == 1
        assert P.build_changed_files("") == []

    def test_load_diff_empty_text_is_error(self, sandbox):
        with pytest.raises(P.PRError):
            P.load_diff("   ")

    def test_load_diff_git_base(self, sandbox):
        svc = A.AssistantService(str(sandbox),
                                 kb_root=str(sandbox / "none-kb"))
        text, source = P.load_diff(None, "main", git=svc.git)
        assert source == "git:main...HEAD"
        assert "docs/USERS.md" in text

    def test_load_diff_empty_base_explained(self, sandbox):
        svc = A.AssistantService(str(sandbox),
                                 kb_root=str(sandbox / "none-kb"))
        with pytest.raises(P.PRError) as e:
            P.load_diff(None, "feature/users", git=svc.git)
        assert "пустой diff" in str(e.value).lower()


# ---------------------------------------------------------------------------
# 2. RAG по документации и коду
# ---------------------------------------------------------------------------

class TestRag:
    def test_terms_from_diff(self):
        terms = P.rag_query_terms("def compress_messages(self): "
                                  "gateway.compress_messages(x)")
        assert terms[0] == "compress_messages"
        assert "self" not in terms

    def test_retrieve_context_hits_corpus(self, indexed):
        svc = A.AssistantService(str(indexed))
        files = A.parse_unified_diff(PR_DIFF)
        ctx = P.retrieve_context(svc.pkb, files)
        assert ctx, "RAG не вернул ни одного фрагмента"
        assert all(c["file"] and c["why"] for c in ctx)
        # Документация проекта о пользователях — в контексте: именно она
        # говорит, что удаление идёт через сервис.
        assert any("USERS.md" in c["file"] or "ARCH.md" in c["file"]
                   for c in ctx)

    def test_retrieve_context_without_index_is_empty(self, sandbox):
        svc = A.AssistantService(str(sandbox),
                                 kb_root=str(sandbox / "no-index"))
        ctx = P.retrieve_context(svc.pkb, A.parse_unified_diff(PR_DIFF))
        assert ctx == []

    def test_agent_prompts_in_context(self, indexed):
        svc = A.AssistantService(str(indexed))
        snippets = P.prompt_agent_snippets(svc.pkb,
                                           A.parse_unified_diff(PR_DIFF))
        assert snippets
        assert all(s["file"].startswith("agents/") for s in snippets)
        assert all(s["text"].strip() for s in snippets)

    def test_prompt_contains_rag_fragments(self, indexed):
        svc = A.AssistantService(str(indexed))
        files = A.parse_unified_diff(PR_DIFF)
        ctx = P.retrieve_context(svc.pkb, files)
        system, user, agent, stats = P.build_review_prompt(
            svc, {"number": 1}, files, [], ctx, [])
        assert system.startswith("Ты — старший инженер")
        assert agent == "pr-review"
        assert "Фрагменты корпуса проекта" in user
        assert ctx[0]["file"] in user
        assert stats["user_chars"] <= stats["budget"]


# ---------------------------------------------------------------------------
# 3. Детерминированные проверки и разбор ответа модели
# ---------------------------------------------------------------------------

class TestChecks:
    def test_invariants_only_in_added_lines(self, sandbox):
        # Нарушение инварианта «print в backend» — в ДОБАВЛЕННЫХ строках.
        diff = textwrap.dedent("""\
            diff --git a/studio/backend/q.py b/studio/backend/q.py
            --- a/studio/backend/q.py
            +++ b/studio/backend/q.py
            @@ -1 +1,2 @@
             X = 1
            +print("debug")
            """)
        found = P.check_invariants_in_diff(A.parse_unified_diff(diff))
        assert any(f["rule"].startswith("invariant:") for f in found)

    def test_global_checks_flags_ci_change(self):
        diff = ("diff --git a/.github/workflows/x.yml b/.github/workflows/x.yml"
                "\n--- a/.github/workflows/x.yml\n"
                "+++ b/.github/workflows/x.yml\n@@ -1 +1,2 @@\n a\n+b\n")
        rules = {f["rule"] for f in
                 P.check_global(A.parse_unified_diff(diff))}
        assert "ci_changed" in rules

    def test_parse_markdown_sections(self):
        got = P.parse_llm_review(ANSWER)
        assert len(got[P.SECTION_BUGS]) == 2
        assert len(got[P.SECTION_ARCH]) == 1
        assert len(got[P.SECTION_RECS]) == 2

    def test_parse_xml_sections(self):
        got = P.parse_llm_review(
            "<bugs>\n- b1\n</bugs>\n<architecture>\n- a1\n</architecture>\n"
            "<recommendations>\n- r1\n</recommendations>")
        assert got == {P.SECTION_BUGS: ["b1"],
                       P.SECTION_ARCH: ["a1"],
                       P.SECTION_RECS: ["r1"]}

    def test_parse_unformatted_answer_not_lost(self):
        got = P.parse_llm_review("Мне кажется, код нормальный. - ---")
        assert got[P.SECTION_RECS] == ["Мне кажется, код нормальный."]
        assert got[P.SECTION_BUGS] == []

    def test_parse_drops_empty_markers(self):
        got = P.parse_llm_review(
            "## Потенциальные баги\nПотенциальных багов не вижу.\n"
            "## Рекомендации\n- добавьте тест на пустой список\n")
        assert got[P.SECTION_BUGS] == []
        assert got[P.SECTION_RECS] == ["добавьте тест на пустой список"]

    def test_clean_item_keeps_inner_dash(self):
        assert P._clean_item("`a:1` — сделай x. - ---") == "`a:1` — сделай x."
        assert P._clean_item("а — б") == "а — б"


# ---------------------------------------------------------------------------
# Готовый дифф для CLI-тестов
# ---------------------------------------------------------------------------

def _write_pr(sandbox) -> str:
    """Ветка feature/users с новым модулем → файл diff для CLI."""
    path = sandbox / "pr.diff"
    path.write_text(PR_DIFF, encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# 4. CLI: отчёт из diff-файла (офлайн, без модели)
# ---------------------------------------------------------------------------

class TestCliOffline:
    def test_run_writes_markdown_and_json(self, indexed, diff_file, tmp_path):
        md, js = tmp_path / "review.md", tmp_path / "review.json"
        code, out, err = cli(indexed, "pr-review", "run", "--diff",
                             diff_file, "--out", str(md), "--json-out",
                             str(js))
        assert code == 0, err
        assert md.exists() and js.exists()
        text = md.read_text(encoding="utf-8")
        # Изменённые файлы — таблицей; три обещанных блока — всегда.
        assert "users_api.py" in text and "изменённые файлы" in text.lower()
        for title in ("Потенциальные баги", "Архитектурные проблемы",
                      "Рекомендации"):
            assert f"### {title}" in text
        # Офлайн (LLM отключена) — честно и с рекомендациями из правил.
        assert "модель" in text.lower()
        data = json.loads(js.read_text(encoding="utf-8"))
        assert data["changed_files"][0]["path"].endswith("users_api.py")
        assert data["llm"] is False
        assert data["summary"]["by_severity"]["high"] >= 2
        assert data["review"]["recommendations"]
        assert data["corpus_context"], "RAG-контекст пуст при построенном индексе"

    def test_run_fail_on_high(self, indexed, diff_file):
        code, _out, err = cli(indexed, "--no-llm", "pr-review", "run",
                              "--diff", diff_file, "--fail-on-high")
        assert code == 1
        assert "критично" in err

    def test_run_from_stdin(self, indexed, tmp_path):
        out = tmp_path / "r.json"
        code, _out, err = cli(indexed, "--no-llm", "pr-review", "run",
                              "--diff", "-", "--json-out", str(out),
                              stdin=PR_DIFF)
        assert code == 0, err
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["source"] == "text"

    def test_run_from_git_base(self, indexed, tmp_path):
        out = tmp_path / "r.json"
        code, _out, err = cli(indexed, "--no-llm", "pr-review", "run",
                              "--base", "main", "--json-out", str(out))
        assert code == 0, err
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["source"] == "git:main...HEAD"
        assert any(f["path"] == "docs/USERS.md"
                   for f in data["changed_files"])

    def test_annotations_and_step_summary(self, indexed, diff_file, tmp_path):
        summary = tmp_path / "summary.md"
        summary.write_text("", encoding="utf-8")
        code, out, err = cli(indexed, "--no-llm", "pr-review", "run",
                             "--diff", diff_file, "--annotations",
                             "--step-summary", str(summary),
                             "--out", str(tmp_path / "r.md"))
        assert code == 0, err
        # Номера строк — из hunk нового файла: 1 — докстринг, 6 — секрет.
        assert "::error file=studio/backend/users_api.py,line=6::" in out
        assert "::warning" in out
        body = summary.read_text(encoding="utf-8")
        assert "AI-ревью" in body and "Потенциальные баги" in body

    def test_check_preflight(self, indexed):
        code, out, err = cli(indexed, "--no-llm", "pr-review", "check")
        assert code == 0, err
        assert "RAG" in out and "индекс проекта" in out
        assert "LLM" in out

    def test_empty_diff_is_error_without_traceback(self, indexed, tmp_path):
        empty = tmp_path / "empty.diff"
        empty.write_text("", encoding="utf-8")
        code, _out, err = cli(indexed, "pr-review", "run", "--diff",
                              str(empty))
        assert code == 1
        assert "пустой diff" in err.lower()
        assert "Traceback" not in err

    def test_missing_diff_file(self, indexed):
        code, _out, err = cli(indexed, "pr-review", "run", "--diff",
                              "/nope/x.diff")
        assert code == 1 and "не найден" in err and "Traceback" not in err


# ---------------------------------------------------------------------------
# 5. CLI + модель: три блока приходят от OpenAI-совместимого сервера
# ---------------------------------------------------------------------------

class TestCliWithModel:
    def test_three_sections_from_model(self, indexed, diff_file, fake_llm,
                                       tmp_path):
        out = tmp_path / "r.json"
        md = tmp_path / "r.md"
        code, _out, err = cli(
            indexed, "pr-review", "run", "--diff", diff_file,
            "--out", str(md), "--json-out", str(out),
            env=_env(indexed, LLM_MODEL="fake/reviewer",
                     LLM_BASE_URL=fake_llm.url, LLM_API_KEY="test-key"))
        assert code == 0, err
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["llm"] is True, data.get("llm_error")
        assert data["review"]["bugs"]
        assert data["review"]["architecture"]
        assert data["review"]["recommendations"]
        assert "llm gpt" not in " ".join(data["review"]["recommendations"])

        # Что ушло в модель: промпт агента + дифф + RAG-фрагменты + находки.
        assert len(fake_llm.requests) == 1
        req = fake_llm.requests[0]
        assert req["path"] == "/v1/chat/completions"
        assert req["auth"] == "Bearer test-key"
        assert req["body"]["model"] == "fake/reviewer"
        system = req["body"]["messages"][0]["content"]
        user = req["body"]["messages"][1]["content"]
        assert "старший инженер" in system
        assert "users_api.py" in user
        assert "Детерминированные находки" in user
        assert "Фрагменты корпуса проекта" in user
        assert data["corpus_context"][0]["file"] in user
        text = md.read_text(encoding="utf-8")
        assert "Потенциальные баги" in text and "секрет в коде" in text

    def test_model_failure_degrades_observably(self, indexed, diff_file,
                                               tmp_path):
        srv = FakeLLM(status=500)
        try:
            out = tmp_path / "r.json"
            code, _o, err = cli(
                indexed, "pr-review", "run", "--diff", diff_file,
                "--json-out", str(out),
                env=_env(indexed, LLM_MODEL="fake/x",
                         LLM_BASE_URL=srv.url))
            assert code == 0, err
            data = json.loads(out.read_text(encoding="utf-8"))
            assert data["llm"] is False
            assert "HTTP 500" in data["llm_error"]
            assert data["review"]["recommendations"]   # отчёт всё равно полезен
        finally:
            srv.close()


# ---------------------------------------------------------------------------
# 6. GitHub: контекст события, diff из API, идемпотентный комментарий
# ---------------------------------------------------------------------------

class TestGitHub:
    def test_github_context_from_event(self, tmp_path):
        event = {
            "pull_request": {
                "number": 42, "title": "Добавить users",
                "draft": False, "additions": 18, "deletions": 0,
                "changed_files": 1,
                "base": {"ref": "main", "sha": "aaa",
                         "repo": {"fork": False}},
                "head": {"ref": "feature/users", "sha": "bbb",
                         "repo": {"fork": True}},
            },
            "repository": {"full_name": "owner/repo"},
        }
        path = tmp_path / "event.json"
        path.write_text(json.dumps(event), encoding="utf-8")
        ctx = P.github_context({"GITHUB_EVENT_PATH": str(path),
                                "GITHUB_REPOSITORY": "owner/repo"})
        assert ctx["number"] == 42
        assert ctx["base_ref"] == "main"
        assert ctx["head_ref"] == "feature/users"
        assert ctx["from_fork"] is True
        assert P.github_context({}) is None

    def test_github_pr_diff_and_upsert_comment(self):
        calls = []

        class FakeGitHub(P.GitHubClient):
            def _request(self, method, path, body=None, **kw):
                calls.append((method, path, body))
                if method == "GET" and "/files" in path:
                    return 200, json.dumps([{"filename": "a.py"}])
                if method == "GET" and path.endswith("/pulls/7"):
                    return 200, "diff --git a/a.py b/a.py\n"
                if method == "GET" and "/comments" in path:
                    return 200, json.dumps([{"id": 1, "body": "прочее"},
                                            {"id": 9, "body":
                                             P.GitHubClient.MARKER + "old"}])
                if method == "PATCH":
                    return 200, json.dumps({"id": 9})
                return 200, "{}"

        gh = FakeGitHub(token="t")
        assert gh.pull_diff("o/r", 7).startswith("diff --git")
        assert gh.pull_files("o/r", 7)[0]["filename"] == "a.py"
        res = gh.upsert_comment("o/r", 7, "новый отчёт")
        assert res["id"] == 9                      # обновили, а не создали
        assert calls[-1][0] == "PATCH"
        assert calls[-1][1] == "/repos/o/r/issues/comments/9"
        assert P.GitHubClient.MARKER in calls[-1][2]["body"]

    def test_upsert_creates_when_no_marker(self):
        calls = []

        class FakeGitHub(P.GitHubClient):
            def _request(self, method, path, body=None, **kw):
                calls.append((method, path))
                if path.endswith("/comments?per_page=100"):
                    return 200, "[]"
                return 201, '{"id": 5}'

        FakeGitHub(token="t").upsert_comment("o/r", 7, "текст")
        assert calls[-1] == ("POST", "/repos/o/r/issues/7/comments")

    def test_comment_flag_publishes_after_review(self, indexed, diff_file,
                                                tmp_path):
        """`--comment` публикует отчёт через API GitHub (идемпотентно).

        Проверяется на HTTP-уровне фейковым GitHub-сервером: CLI читает
        комментарии PR, не находит маркер и создаёт новый — с
        markdown-отчётом внутри. Второй прогон обновляет его (PATCH).
        """
        srv = FakeGitHub()
        try:
            env = _env(indexed, GITHUB_API_URL=srv.url,
                       GITHUB_TOKEN="tkn")
            code, _o, err = cli(indexed, "--no-llm", "pr-review", "run",
                                "--diff", diff_file, "--comment",
                                "owner/repo#42", "--out",
                                str(tmp_path / "r.md"), env=env)
            assert code == 0, err
            assert srv.created, srv.calls
            body = json.loads(srv.created[0]["body"])["body"]
            assert "Потенциальные баги" in body
            assert P.GitHubClient.MARKER in body
            assert srv.created[0]["auth"] == "Bearer tkn"

            # Повторный прогон: тот же отчёт обновляется, а не плодится.
            code, _o, err = cli(indexed, "--no-llm", "pr-review", "run",
                                "--diff", diff_file, "--comment",
                                "owner/repo#42", "--out",
                                str(tmp_path / "r.md"), env=env)
            assert code == 0, err
            assert len(srv.created) == 1
            assert srv.updated, "повторный прогон не обновил комментарий"
        finally:
            srv.close()


# ---------------------------------------------------------------------------
# 7. Пайплайн GitHub Action (файлы в репозитории)
# ---------------------------------------------------------------------------

WORKFLOW = os.path.join(REPO, ".github", "workflows", "ai-review.yml")
ACTION = os.path.join(REPO, ".github", "actions", "ai-pr-review",
                      "action.yml")


def _yaml_keys(path: str) -> str:
    return open(path, encoding="utf-8").read()


class TestWorkflow:
    def test_workflow_triggers_on_pull_request(self):
        text = _yaml_keys(WORKFLOW)
        assert re.search(r"^on:\s*$", text, re.M)
        assert re.search(r"^\s+pull_request:", text, re.M)
        for t in ("opened", "synchronize", "reopened"):
            assert t in text
        assert "permissions:" in text and "pull-requests: write" in text

    def test_workflow_runs_the_pipeline(self):
        text = _yaml_keys(WORKFLOW)
        assert "./.github/actions/ai-pr-review" in text
        assert "LLM_API_KEY" in text            # модель — из секрета
        wf = _yaml_keys(ACTION)
        assert "cli.py pr-review check" in wf
        assert "cli.py index" in wf             # RAG-индекс в CI
        assert "cli.py pr-review run" in wf
        assert "--annotations" in wf and "--step-summary" in wf
        assert "--comment" in wf
        assert "fail-on-high" in wf

    def test_action_handles_forks_without_write(self):
        """PR из форка: diff через API, без комментария пайплайн не падает."""
        text = _yaml_keys(ACTION)
        assert "--github-pr" in text
        assert "PR_FORK" in text
        # Комментарий — только при наличии номера PR и права записи.
        assert "PR_NUMBER:+--comment" in text

    def test_deterministic_mode_without_key(self, indexed, diff_file,
                                            tmp_path):
        """Тот же шаг Action без LLM_API_KEY: отчёт есть, exit 0."""
        out = tmp_path / "r.json"
        code, _o, err = cli(indexed, "pr-review", "run", "--diff",
                            diff_file, "--json-out", str(out),
                            env=_env(indexed))
        assert code == 0, err
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["llm"] is False and data["changed_files"]


class TestAgentPrompt:
    def test_pr_review_agent_registered(self, indexed):
        code, out, _err = cli(indexed, "agents", "list", "--json")
        assert code == 0
        agents = json.loads(out)["agents"]
        pair = [(a["name"], a["execution"]) for a in agents]
        assert ("pr-review", "cli") in pair
        assert not json.loads(out)["errors"]

    def test_prompt_asks_for_three_sections(self):
        text = open(os.path.join(REPO, "agents", "pr-review.cli.md"),
                    encoding="utf-8").read()
        for title in ("Потенциальные баги", "Архитектурные проблемы",
                      "Рекомендации"):
            assert title in text
        assert "не выдумывай" in text.lower()
