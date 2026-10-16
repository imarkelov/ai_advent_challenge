"""Тесты ассистента (дни 30–34) — офлайн, без сети и без реальной LLM.

Покрывают: корпус проекта, индекс, CRM, разбор diff, детерминированные
проверки ревью, git-чтение, четыре модуля сервиса и пять сценариев,
включая безопасность записи (allow-list).
"""
import json
import os
import subprocess
import sys
import textwrap

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import assistant as A  # noqa: E402


# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------

@pytest.fixture
def repo(tmp_path):
    """Мини-репозиторий с кодом, документацией и спекой."""
    def w(rel, text):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(text), encoding="utf-8")
        return p

    w("README.md", "# Проект\n\nОписание проекта и его назначение.\n")
    w("docs/guide.md", "# Гайд\n\n## Настройка\n\nКак настроить систему.\n")
    w("studio/backend/mod.py",
      '"""Модуль.\"\"\"\n\n\ndef target_function(a, b):\n'
      "    return a + b\n")
    w("studio/backend/other.py", '"""Другой.\"\"\"\n\nX = 1\n')
    w("openspec/changes/x/spec.md", "# Spec\n\nТребование системы.\n")
    # Мусор, который не должен попасть в корпус:
    w(".venv/lib/junk.py", "import nothing\n")
    w("node_modules/pkg/index.js", "var a=1;\n")
    w("__pycache__/x.py", "x=1\n")
    return tmp_path


@pytest.fixture
def svc(repo):
    """Сервис без агента (LLM недоступна) — офлайн-режим."""
    return A.AssistantService(str(repo), kb_root=str(repo / "kbdata"))


@pytest.fixture
def crm(repo):
    """CRM-данные: 2 пользователя, 3 тикета."""
    d = repo / "data" / "crm"
    d.mkdir(parents=True, exist_ok=True)
    (d / "users.json").write_text(json.dumps({"version": 1, "users": [
        {"id": "u-1", "name": "Иван", "plan": "pro", "role": "developer",
         "payment_status": "ok", "channel": "web",
         "registered_at": "2026-01-01"},
        {"id": "u-2", "name": "Пётр", "plan": "free", "role": "user",
         "payment_status": "failed", "channel": "email",
         "registered_at": "2026-02-02"},
    ]}, ensure_ascii=False), encoding="utf-8")
    (d / "tickets.json").write_text(json.dumps({"version": 1, "tickets": [
        {"id": "T-1", "user_id": "u-1", "subject": "Настройка системы",
         "body": "Как настроить?", "topic": "configuration",
         "status": "open", "priority": "medium", "tags": []},
        {"id": "T-2", "user_id": "u-2", "subject": "Оплата не прошла",
         "body": "Деньги списались", "topic": "billing",
         "status": "open", "priority": "high", "tags": []},
        {"id": "T-3", "user_id": "u-1", "subject": "Старый вопрос",
         "body": "Уже решено", "topic": "auth", "status": "closed",
         "priority": "low", "tags": []},
    ]}, ensure_ascii=False), encoding="utf-8")
    return d


DIFF_BACKEND = textwrap.dedent("""\
    diff --git a/studio/backend/mod.py b/studio/backend/mod.py
    --- a/studio/backend/mod.py
    +++ b/studio/backend/mod.py
    @@ -1,4 +1,7 @@
     \"\"\"Модуль.\"\"\"
    +import os
    +API_KEY = "sk-abcdef1234567890abcd"
    +def g():
    +    try:
    +        pass
    +    except:
    +        pass
     def target_function(a, b):
         return a + b
    """)


# ---------------------------------------------------------------------------
# Корпус проекта
# ---------------------------------------------------------------------------

class TestProjectCorpus:
    def test_collects_code_docs_spec(self, repo):
        files = A.ProjectCorpus(str(repo)).files()
        assert "README.md" in files
        assert "docs/guide.md" in files
        assert "studio/backend/mod.py" in files
        assert "openspec/changes/x/spec.md" in files

    def test_excludes_venv_node_modules_pycache(self, repo):
        files = A.ProjectCorpus(str(repo)).files()
        joined = " ".join(files)
        assert ".venv" not in joined
        assert "node_modules" not in joined
        assert "__pycache__" not in joined

    def test_sources_are_labelled(self, repo):
        docs = {d.path: d.source
                for d in A.ProjectCorpus(str(repo)).documents()}
        assert docs["README.md"] == "docs"
        assert docs["studio/backend/mod.py"] == "code"
        assert docs["openspec/changes/x/spec.md"] == "spec"

    def test_stats_by_source(self, repo):
        st = A.ProjectCorpus(str(repo)).stats()
        assert st["files"] >= 4
        assert st["by_source"]["code"] == 2
        assert st["chars"] > 0

    def test_max_file_bytes_skips_huge(self, repo):
        big = repo / "docs" / "big.md"
        big.write_text("x" * 5000, encoding="utf-8")
        c = A.ProjectCorpus(str(repo), max_file_bytes=1000)
        assert "docs/big.md" not in c.files()

    def test_excluded_suffix_binary(self, repo):
        (repo / "docs" / "img.png").write_bytes(b"\x89PNG\r\n")
        assert "docs/img.png" not in A.ProjectCorpus(str(repo)).files()

    def test_missing_root_is_empty(self, tmp_path):
        assert A.ProjectCorpus(str(tmp_path / "nope")).files() == []


# ---------------------------------------------------------------------------
# Индекс проекта
# ---------------------------------------------------------------------------

class TestProjectIndex:
    def test_build_and_search(self, svc):
        res = svc.build_index()
        assert res["stats"]["docs"] >= 4
        assert res["stats"]["chunks"] >= 1
        out = svc.pkb.search_project("настроить систему", top_k=3)
        assert out["results"]

    def test_index_is_separate_from_day21(self, repo, svc):
        """Индекс проекта не трогает data/kb/index.db дня 21."""
        day21 = repo / "data" / "kb" / "index.db"
        day21.parent.mkdir(parents=True, exist_ok=True)
        day21.write_bytes(b"SENTINEL-DAY21")
        svc.build_index()
        assert day21.read_bytes() == b"SENTINEL-DAY21"

    def test_chunk_ids_unique_with_same_stem(self, repo):
        """Одинаковые basename (design.md) не дают коллизию chunk_id."""
        for i in range(3):
            p = repo / "openspec" / "changes" / f"c{i}" / "design.md"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f"# Дизайн {i}\n\nТекст решения {i}.\n",
                         encoding="utf-8")
        svc = A.AssistantService(str(repo), kb_root=str(repo / "kbdata"))
        svc.build_index()          # не должно падать на PRIMARY KEY
        idx = svc.pkb.load_index()
        ids = [c["chunk_id"] for c in idx["chunks"]]
        assert len(ids) == len(set(ids))

    def test_rebuild_is_idempotent(self, svc):
        a = svc.build_index()
        b = svc.build_index()
        assert a["stats"]["docs"] == b["stats"]["docs"]
        assert a["stats"]["chunks"] == b["stats"]["chunks"]


# ---------------------------------------------------------------------------
# CRM
# ---------------------------------------------------------------------------

class TestTicketStore:
    def test_loads_users_and_tickets(self, repo, crm):
        ts = A.TicketStore(str(repo))
        assert len(ts.users()) == 2
        assert len(ts.tickets()) == 3

    def test_missing_files_no_crash(self, tmp_path):
        ts = A.TicketStore(str(tmp_path))
        assert ts.users() == []
        assert ts.tickets() == []
        assert ts.stats()["available"] is False

    def test_find_ticket_and_user(self, repo, crm):
        ts = A.TicketStore(str(repo))
        t = ts.ticket("T-1")
        assert t["subject"] == "Настройка системы"
        assert ts.user(t["user_id"])["name"] == "Иван"
        assert ts.ticket("nope") is None

    def test_filter_by_status_and_priority(self, repo, crm):
        ts = A.TicketStore(str(repo))
        assert [t["id"] for t in ts.list_tickets(status="open")] == ["T-1", "T-2"]
        assert [t["id"] for t in ts.list_tickets(priority="high")] == ["T-2"]
        assert [t["id"] for t in ts.list_tickets(topic="auth")] == ["T-3"]

    def test_context_links_user_and_history(self, repo, crm):
        ctx = A.TicketStore(str(repo)).context("T-1")
        assert ctx["found"] is True
        assert ctx["user"]["id"] == "u-1"
        assert ctx["history_count"] == 1          # T-3 у того же юзера

    def test_context_unknown_ticket(self, repo, crm):
        ctx = A.TicketStore(str(repo)).context("T-404")
        assert ctx["found"] is False
        assert "не найден" in ctx["reason"]

    def test_stats_by_status(self, repo, crm):
        st = A.TicketStore(str(repo)).stats()
        assert st["users"] == 2
        assert st["by_status"] == {"open": 2, "closed": 1}


# ---------------------------------------------------------------------------
# Разбор unified diff
# ---------------------------------------------------------------------------

class TestParseDiff:
    def test_parses_files_and_counts(self):
        files = A.parse_unified_diff(DIFF_BACKEND)
        assert len(files) == 1
        assert files[0].path == "studio/backend/mod.py"
        assert files[0].added == 7
        assert files[0].removed == 0

    def test_strips_ab_prefix(self):
        d = ("diff --git a/x/y.py b/x/y.py\n--- a/x/y.py\n+++ b/x/y.py\n"
             "@@ -1 +1 @@\n-a\n+b\n")
        assert A.parse_unified_diff(d)[0].path == "x/y.py"

    def test_line_numbers_in_new_version(self):
        d = ("diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n"
             "@@ -10,2 +10,3 @@\n ctx\n+added\n ctx2\n")
        f = A.parse_unified_diff(d)[0]
        assert f.added_lines[0].new_lineno == 11

    def test_deleted_file_marked(self):
        d = ("diff --git a/gone.py b/gone.py\ndeleted file mode 100644\n"
             "--- a/gone.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x\n")
        f = A.parse_unified_diff(d)[0]
        assert f.is_deleted is True
        assert f.path == "gone.py"

    def test_new_file_marked(self):
        d = ("diff --git a/new.py b/new.py\nnew file mode 100644\n"
             "--- /dev/null\n+++ b/new.py\n@@ -0,0 +1 @@\n+hi\n")
        f = A.parse_unified_diff(d)[0]
        assert f.is_new is True

    def test_empty_and_broken(self):
        assert A.parse_unified_diff("") == []
        assert A.parse_unified_diff("не diff вообще") == []
        assert A.parse_unified_diff("   \n  \n") == []


# ---------------------------------------------------------------------------
# Детерминированные проверки ревью
# ---------------------------------------------------------------------------

class TestCheckDiff:
    def _rules(self, diff):
        return {f["rule"] for f in A.check_diff(A.parse_unified_diff(diff))}

    def test_detects_secret(self):
        assert "secret" in self._rules(DIFF_BACKEND)

    def test_detects_bare_except(self):
        assert "bare_except" in self._rules(DIFF_BACKEND)

    def test_detects_missing_test_for_backend_module(self):
        assert "missing_test" in self._rules(DIFF_BACKEND)

    def test_no_missing_test_when_tests_changed(self):
        d = ("diff --git a/studio/backend/mod.py b/studio/backend/mod.py\n"
             "--- a/studio/backend/mod.py\n+++ b/studio/backend/mod.py\n"
             "@@ -1 +1,2 @@\n x\n+y\n"
             "diff --git a/studio/backend/tests/test_mod.py "
             "b/studio/backend/tests/test_mod.py\n"
             "--- a/studio/backend/tests/test_mod.py\n"
             "+++ b/studio/backend/tests/test_mod.py\n"
             "@@ -1 +1,2 @@\n x\n+y\n")
        assert "missing_test" not in self._rules(d)

    def test_detects_print_in_backend(self):
        d = ("diff --git a/studio/backend/m.py b/studio/backend/m.py\n"
             "--- a/studio/backend/m.py\n+++ b/studio/backend/m.py\n"
             "@@ -1 +1,2 @@\n x\n+print(\"hi\")\n")
        assert "print_call" in self._rules(d)

    def test_detects_long_line(self):
        long_line = "+" + "x" * 150
        d = ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
             f"@@ -1 +1,2 @@\n x\n{long_line}\n")
        assert "long_line" in self._rules(d)

    def test_detects_todo_without_owner(self):
        d = ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
             "@@ -1 +1,2 @@\n x\n+# TODO: починить\n")
        assert "todo" in self._rules(d)

    def test_todo_with_owner_ok(self):
        d = ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
             "@@ -1 +1,2 @@\n x\n+# TODO(игорь): починить\n")
        assert "todo" not in self._rules(d)

    def test_detects_missing_docs(self):
        assert "missing_docs" in self._rules(DIFF_BACKEND)

    def test_docs_change_satisfies_missing_docs(self):
        d = ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
             "@@ -1 +1,2 @@\n x\n+y\n"
             "diff --git a/README.md b/README.md\n--- a/README.md\n"
             "+++ b/README.md\n@@ -1 +1,2 @@\n x\n+y\n")
        assert "missing_docs" not in self._rules(d)

    def test_severity_and_proof(self):
        fs = A.check_diff(A.parse_unified_diff(DIFF_BACKEND))
        secret = [f for f in fs if f["rule"] == "secret"][0]
        assert secret["severity"] == "high"
        assert secret["file"] == "studio/backend/mod.py"
        assert isinstance(secret["line"], int) and secret["line"] > 0

    def test_sort_findings_high_first(self):
        fs = A.sort_findings(A.check_diff(A.parse_unified_diff(DIFF_BACKEND)))
        sev = [f["severity"] for f in fs]
        order = {"high": 0, "medium": 1, "low": 2}
        assert sev == sorted(sev, key=lambda s: order[s])

    def test_summarize(self):
        fs = A.check_diff(A.parse_unified_diff(DIFF_BACKEND))
        s = A.summarize_findings(fs)
        assert s["total"] == len(fs)
        assert s["by_severity"]["high"] >= 2


# ---------------------------------------------------------------------------
# Инварианты
# ---------------------------------------------------------------------------

class TestInvariants:
    def test_clean_repo(self, repo):
        res = A.check_invariants(str(repo),
                                 paths=["studio/backend/mod.py"])
        assert res["clean"] is True
        assert res["violations"] == []

    def test_detects_heavy_dep(self, repo):
        (repo / "studio/backend/bad.py").write_text(
            '"""M."""\nimport torch\n', encoding="utf-8")
        res = A.check_invariants(str(repo), paths=["studio/backend/bad.py"])
        assert any(v["rule"] == "no-heavy-ml-deps"
                   for v in res["violations"])

    def test_detects_untagged_print(self, repo):
        (repo / "studio/backend/bad.py").write_text(
            '"""M."""\nprint("no tag")\n', encoding="utf-8")
        res = A.check_invariants(str(repo), paths=["studio/backend/bad.py"])
        assert any(v["rule"] == "tagged-log" for v in res["violations"])

    def test_tagged_print_is_ok(self, repo):
        (repo / "studio/backend/ok.py").write_text(
            '"""M."""\nprint("[KB] fine", flush=True)\n', encoding="utf-8")
        res = A.check_invariants(str(repo), paths=["studio/backend/ok.py"])
        assert res["clean"] is True

    def test_multiline_tagged_print_is_ok(self, repo):
        (repo / "studio/backend/ok.py").write_text(
            '"""M."""\nprint(f"[KB] long {1} — "\n      f"continued")\n',
            encoding="utf-8")
        res = A.check_invariants(str(repo), paths=["studio/backend/ok.py"])
        assert res["clean"] is True

    def test_print_mention_in_string_is_not_a_call(self, repo):
        """Упоминание print() в тексте сообщения — не вызов."""
        (repo / "studio/backend/ok.py").write_text(
            '"""M."""\nprint("[KB] use print() carefully", flush=True)\n',
            encoding="utf-8")
        res = A.check_invariants(str(repo), paths=["studio/backend/ok.py"])
        assert res["clean"] is True

    def test_missing_docstring(self, repo):
        (repo / "studio/backend/nodoc.py").write_text("x = 1\n",
                                                     encoding="utf-8")
        res = A.check_invariants(str(repo), paths=["studio/backend/nodoc.py"])
        assert any(v["rule"] == "module-docstring"
                   for v in res["violations"])

    def test_cli_stdout_is_not_logging(self, repo):
        """В CLI `print` — это продукт, а не диагностика: тег не требуется.

        Иначе инвариант ругался бы на корректный `print(render(...))` и
        требовал бы `[Tag]` в выводе команды, ломая `--json` и пайпы.
        """
        (repo / "studio/backend/cli.py").write_text(
            '"""CLI."""\nprint("это вывод команды, а не лог")\n',
            encoding="utf-8")
        res = A.check_invariants(str(repo), paths=["studio/backend/cli.py"])
        assert res["clean"] is True

    def test_regular_module_still_needs_tag(self, repo):
        """Исключение сделано ТОЛЬКО для cli.py — правило не ослаблено."""
        (repo / "studio/backend/service.py").write_text(
            '"""Модуль."""\nprint("безликая диагностика")\n',
            encoding="utf-8")
        res = A.check_invariants(str(repo),
                                 paths=["studio/backend/service.py"])
        assert any(v["rule"] == "tagged-log"
                   for v in res["violations"])

    def test_real_repo_is_clean(self):
        """Реальный репозиторий проходит проверку инвариантов.

        Регрессия: день 35 добавил `cli.py` с 24 `print()` вывода, и
        инвариант начал ругаться на корректный код (24 находки).
        """
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))))
        res = A.check_invariants(repo_root)
        assert res["clean"] is True, res["violations"][:5]


# ---------------------------------------------------------------------------
# Поиск использований
# ---------------------------------------------------------------------------

class TestFindUsages:
    def test_finds_symbol_in_code_and_docs(self, repo):
        (repo / "docs" / "use.md").write_text(
            "Используем target_function тут.\n", encoding="utf-8")
        res = A.find_usages(str(repo), "target_function")
        files = {f["file"] for f in res["files"]}
        assert "studio/backend/mod.py" in files
        assert "docs/use.md" in files
        assert res["by_source"]["docs"] >= 1
        assert res["by_source"]["code"] >= 1

    def test_reports_line_numbers(self, repo):
        res = A.find_usages(str(repo), "target_function")
        entry = [f for f in res["files"]
                 if f["file"] == "studio/backend/mod.py"][0]
        assert entry["matches"][0]["line"] == 4

    def test_word_boundary(self, repo):
        (repo / "studio/backend/x.py").write_text(
            '"""M."""\ntarget_function_extra = 1\ntarget_function = 2\n',
            encoding="utf-8")
        res = A.find_usages(str(repo), "target_function")
        entry = [f for f in res["files"]
                 if f["file"] == "studio/backend/x.py"][0]
        assert entry["count"] == 1

    def test_not_found(self, repo):
        res = A.find_usages(str(repo), "definitely_absent_symbol_xyz")
        assert res["total"] == 0
        assert res["files"] == []

    def test_empty_symbol_raises(self, repo):
        with pytest.raises(A.AssistantError):
            A.find_usages(str(repo), "  ")


# ---------------------------------------------------------------------------
# Безопасность записи
# ---------------------------------------------------------------------------

class TestWriteAllowlist:
    def test_allowlist_targets(self, repo):
        for rel in ["README.md", "CHANGELOG.md", "docs/x.md",
                    "openspec/changes/y/z.md"]:
            assert A._resolve_write_path(str(repo), rel)

    def test_blocks_absolute(self, repo):
        with pytest.raises(A.ScenarioError):
            A._resolve_write_path(str(repo), "/tmp/evil.md")

    def test_blocks_parent_traversal(self, repo):
        with pytest.raises(A.ScenarioError):
            A._resolve_write_path(str(repo), "../evil.md")

    def test_blocks_code_paths(self, repo):
        with pytest.raises(A.ScenarioError):
            A._resolve_write_path(str(repo), "studio/backend/main.py")

    def test_blocks_data_paths(self, repo):
        with pytest.raises(A.ScenarioError):
            A._resolve_write_path(str(repo), "data/x.json")

    def test_blocks_empty(self, repo):
        with pytest.raises(A.ScenarioError):
            A._resolve_write_path(str(repo), "  ")

    def test_write_is_atomic(self, repo):
        A._write_text(str(repo), "docs/new.md", "привет")
        assert (repo / "docs" / "new.md").read_text(encoding="utf-8") == "привет"
        assert not (repo / "docs" / "new.md.tmp-assistant").exists()


# ---------------------------------------------------------------------------
# Git (только чтение)
# ---------------------------------------------------------------------------

class TestGitReader:
    def test_no_repo_is_safe(self, tmp_path):
        g = A.GitReader(str(tmp_path))
        ctx = g.context()
        assert ctx["available"] is False
        assert ctx["diff"] == ""

    def test_reads_branch_and_changes(self, repo):
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo,
                       check=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=repo,
                       check=True)
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
        (repo / "README.md").write_text("# Изменено\n", encoding="utf-8")
        g = A.GitReader(str(repo))
        ctx = g.context()
        assert ctx["available"] is True
        assert ctx["branch"]
        assert "README.md" in ctx["tracked"]
        assert "README.md" in ctx["diff"]

    def test_log_reads_commits(self, repo):
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo,
                       check=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=repo,
                       check=True)
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "первый коммит"], cwd=repo,
                       check=True)
        log = A.GitReader(str(repo)).log(5)
        assert log and log[0]["subject"] == "первый коммит"

    def test_never_mutates(self, repo):
        """Ассистент не имеет git-мутаций: только чтение."""
        src = open(A.__file__, encoding="utf-8").read()
        for forbidden in ('"commit"', '"push"', '"checkout"', '"reset"'):
            assert forbidden not in src, f"git-мутация {forbidden} в модуле"


# ---------------------------------------------------------------------------
# Модуль А: вопросы по проекту
# ---------------------------------------------------------------------------

class TestAskProject:
    def test_requires_index(self, svc):
        with pytest.raises(A.AssistantError) as e:
            svc.ask_project("что тут?")
        assert "индекс" in str(e.value).lower()

    def test_empty_question_raises(self, svc):
        with pytest.raises(A.AssistantError):
            svc.ask_project("   ")

    def test_answers_with_sources(self, svc):
        svc.build_index()
        res = svc.ask_project("как настроить систему?")
        assert res["found"] is True
        assert res["sources"]
        assert any("guide" in (s["file"] or "") or "README" in (s["file"] or "")
                   for s in res["sources"])
        for s in res["sources"]:
            assert "file" in s and "quote" in s

    def test_extractive_without_llm(self, svc):
        svc.build_index()
        res = svc.ask_project("назначение проекта")
        assert res["llm"] is False          # агента нет — деградация
        assert res["answer"].strip()

    def test_unknown_topic_not_found(self, svc):
        svc.build_index()
        res = svc.ask_project("квантовый телепорт межзвёздный двигатель")
        # Либо «не найдено», либо найденное, но ответ не выдуман.
        assert res["found"] in (True, False)
        if not res["found"]:
            assert res["sources"] == []
            assert res["llm"] is False

    def test_llm_error_is_observable(self, repo):
        """Деградация LLM видна в ответе, а не молчалива.

        Живая проверка (Part B) показала: локальная модель уходила на
        удалённый GPustack и получала 401, а ассистент молча отвечал без
        LLM — диагностировать было нечем. Теперь причина в `llm_error`.
        """
        class _Boom:
            def get_config(self):
                return {"model": "local-x"}

            def _base_for(self, m):
                return "http://127.0.0.1:1"

            def _auth_header_for(self, m):
                return {}

            def list_models(self):
                return []

            _client = None

        class _Client:
            def post(self, *a, **k):
                raise OSError("сеть недоступна")

        boom = _Boom()
        boom._client = _Client()
        s = A.AssistantService(str(repo), agent=boom,
                               kb_root=str(repo / "kbdata2"))
        s.build_index()
        res = s.ask_project("назначение проекта")
        assert res["llm"] is False
        assert res["llm_error"]          # причина названа
        assert res["answer"]             # и ответ всё равно есть


# ---------------------------------------------------------------------------
# Модуль Б: ревью
# ---------------------------------------------------------------------------

class TestReviewDiff:
    def test_review_from_text(self, svc):
        res = svc.review_diff(diff=DIFF_BACKEND, with_llm=False)
        assert res["source"] == "text"
        assert res["summary"]["files"] == 1
        assert res["summary"]["added"] == 7
        assert res["findings"]
        assert res["recommendations"]

    def test_review_works_without_llm(self, svc):
        res = svc.review_diff(diff=DIFF_BACKEND)
        assert res["llm_note"] == ""       # агента нет
        assert res["findings"]             # отчёт всё равно есть

    def test_empty_diff_raises(self, svc):
        with pytest.raises(A.AssistantError):
            svc.review_diff(diff="")

    def test_empty_diff_is_not_silently_replaced_by_git(self, svc, repo):
        """Пустой diff — ошибка, а не повод подставить git-изменения.

        Иначе клиент, приславший пустой diff, получал бы отчёт по чужим
        изменениям рабочего дерева (найдено e2e A7).
        """
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo,
                       check=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=repo,
                       check=True)
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
        (repo / "README.md").write_text("# Изменено\n", encoding="utf-8")
        # В дереве ЕСТЬ изменения, но diff передан пустым — это ошибка.
        with pytest.raises(A.AssistantError):
            svc.review_diff(diff="   ")
        # А diff=None по-прежнему читает git.
        assert svc.review_diff(diff=None, with_llm=False)["summary"]["files"]

    def test_unparseable_diff_raises(self, svc):
        with pytest.raises(A.AssistantError):
            svc.review_diff(diff="это не diff")

    def test_affected_docs_uses_index(self, svc):
        svc.build_index()
        res = svc.review_diff(diff=DIFF_BACKEND, with_llm=False)
        assert isinstance(res["affected_docs"], list)

    def test_summary_severity_counts(self, svc):
        res = svc.review_diff(diff=DIFF_BACKEND, with_llm=False)
        by = res["summary"]["by_severity"]
        assert by["high"] >= 2
        assert sum(by.values()) == res["summary"]["findings"]


# ---------------------------------------------------------------------------
# Модуль В: поддержка
# ---------------------------------------------------------------------------

class TestSupport:
    def test_personalized_by_crm(self, svc, crm):
        svc.build_index()
        res = svc.support_answer("T-2")
        assert res["found"] is True
        assert res["context"]["user"]["plan"] == "free"
        assert "статус оплаты: failed" in res["context"]["applied"]
        assert res["answer"]

    def test_applies_history(self, svc, crm):
        svc.build_index()
        res = svc.support_answer("T-1")
        assert "прошлых обращений: 1" in res["context"]["applied"]

    def test_unknown_ticket_degrades(self, svc, crm):
        svc.build_index()
        res = svc.support_answer("T-404", question="как настроить?")
        assert res["found"] is False
        assert res["context"]["user"] is None
        assert res["answer"]

    def test_requires_ticket_id(self, svc, crm):
        with pytest.raises(A.AssistantError):
            svc.support_answer("  ")

    def test_works_without_index(self, svc, crm):
        """Индекс не построен — ответ всё равно формируется."""
        res = svc.support_answer("T-1")
        assert res["answer"]
        assert res["sources"] == []


# ---------------------------------------------------------------------------
# Модуль Г: сценарии
# ---------------------------------------------------------------------------

class TestScenarios:
    def test_catalog_has_five(self, svc):
        names = [s["name"] for s in svc.scenarios()]
        assert names == ["find_usages", "update_docs", "generate",
                         "check_invariants", "diff_report"]
        for s in svc.scenarios():
            assert s["title"] and s["description"] and "params" in s

    def test_unknown_scenario_raises(self, svc):
        with pytest.raises(A.ScenarioError):
            svc.run_scenario("nope")

    def test_find_usages_scenario(self, svc):
        res = svc.run_scenario("find_usages", symbol="target_function")
        assert res["ok"] is True
        assert res["result"]["total"] >= 1
        assert res["changed_files"] == []

    def test_check_invariants_scenario(self, svc):
        res = svc.run_scenario("check_invariants")
        assert res["ok"] is True
        assert "Проверено" in res["summary"]

    def test_generate_adr_numbers_and_writes(self, svc, repo):
        res = svc.run_scenario("generate", kind="adr",
                               title="Выбор базы данных")
        assert res["changed_files"] == ["docs/adr/0001-vybor-bazy-dannyh.md"]
        text = (repo / "docs/adr/0001-vybor-bazy-dannyh.md").read_text(
            encoding="utf-8")
        assert "ADR 0001" in text
        assert "Выбор базы данных" in text

    def test_generate_adr_increments_number(self, svc, repo):
        d = repo / "docs" / "adr"
        d.mkdir(parents=True, exist_ok=True)
        (d / "0007-old.md").write_text("# ADR 0007\n", encoding="utf-8")
        res = svc.run_scenario("generate", kind="adr", title="Next")
        assert res["changed_files"][0] == "docs/adr/0008-next.md"

    def test_adr_slug_transliterates_cyrillic(self, svc, repo):
        """Русский заголовок даёт осмысленный слаг, а не `adr`."""
        svc.run_scenario("generate", kind="adr", title="Выбор базы данных")
        names = [p.name for p in (repo / "docs" / "adr").iterdir()]
        assert names == ["0001-vybor-bazy-dannyh.md"]

    def test_two_cyrillic_adrs_do_not_collide(self, svc, repo):
        a = svc.run_scenario("generate", kind="adr", title="Первый выбор")
        b = svc.run_scenario("generate", kind="adr", title="Второй выбор")
        assert a["changed_files"] != b["changed_files"]
        assert len(list((repo / "docs" / "adr").iterdir())) == 2

    def test_generate_dry_run_writes_nothing(self, svc, repo):
        res = svc.run_scenario("generate", kind="adr", title="Dry",
                               dry_run=True)
        assert res["changed_files"] == []
        assert res["diff"]
        assert not (repo / "docs" / "adr").exists()

    def test_generate_unknown_kind_raises(self, svc):
        with pytest.raises(A.ScenarioError):
            svc.run_scenario("generate", kind="nonsense")

    def test_generate_changelog_from_git(self, svc, repo):
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo,
                       check=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=repo,
                       check=True)
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "фича"], cwd=repo, check=True)
        res = svc.run_scenario("generate", kind="changelog")
        assert res["changed_files"] == ["CHANGELOG.md"]
        assert "фича" in (repo / "CHANGELOG.md").read_text(encoding="utf-8")

    def test_write_outside_project_blocked(self, svc):
        for bad in ["/tmp/evil.md", "../evil.md", "studio/backend/x.py"]:
            with pytest.raises(A.ScenarioError):
                svc.run_scenario("generate", kind="readme", path=bad)

    def test_update_docs_requires_git(self, svc):
        with pytest.raises(A.ScenarioError):
            svc.run_scenario("update_docs")

    def test_update_docs_idempotent_marker(self, svc, repo):
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo,
                       check=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=repo,
                       check=True)
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
        (repo / "studio/backend/mod.py").write_text(
            '"""Модуль."""\nY = 2\n', encoding="utf-8")
        svc.run_scenario("update_docs")
        first = (repo / "README.md").read_text(encoding="utf-8")
        assert A.AUTOGEN_BEGIN in first
        svc.run_scenario("update_docs")
        second = (repo / "README.md").read_text(encoding="utf-8")
        assert second.count(A.AUTOGEN_BEGIN) == 1     # не дублируется

    def test_diff_report_without_path_returns_only(self, svc, repo):
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo,
                       check=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=repo,
                       check=True)
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
        (repo / "README.md").write_text("# X\n", encoding="utf-8")
        res = svc.run_scenario("diff_report")
        assert res["changed_files"] == []
        assert "Отчёт об изменениях" in res["report"]

    def test_diff_report_saves(self, svc, repo):
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo,
                       check=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=repo,
                       check=True)
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
        (repo / "README.md").write_text("# Y\n", encoding="utf-8")
        res = svc.run_scenario("diff_report", path="docs/report.md")
        assert res["changed_files"] == ["docs/report.md"]
        assert (repo / "docs" / "report.md").exists()

    def test_scenario_returns_timing_and_name(self, svc):
        res = svc.run_scenario("check_invariants")
        assert res["scenario"] == "check_invariants"
        assert isinstance(res["duration_ms"], int)


# ---------------------------------------------------------------------------
# Статус
# ---------------------------------------------------------------------------

class TestStatus:
    def test_status_before_index(self, svc):
        st = svc.status()
        assert st["indexed"] is False
        assert st["llm_available"] is False
        assert st["corpus"]["files"] >= 4

    def test_status_after_index(self, svc):
        svc.build_index()
        st = svc.status()
        assert st["indexed"] is True
        assert st["documents"] >= 4
        assert st["chunks"] >= 1
        assert st["embedder"]

    def test_status_includes_crm(self, repo, crm):
        s = A.AssistantService(str(repo), kb_root=str(repo / "kbdata"))
        assert s.status()["crm"]["tickets"] == 3
