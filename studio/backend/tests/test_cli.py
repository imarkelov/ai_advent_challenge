"""Тесты CLI ассистента (день 35) — офлайн, без сети и без LLM.

Проверяют: типизацию параметров, разбор аргументов, форматирование
вывода, коды возврата, `--json`/`--out`, выбор агента и — отдельно —
что CLI не дублирует логику (тонкая обёртка над AssistantService).

`main(argv)` вызывается напрямую (быстро); e2e_day32 дополнительно
проверяет CLI как реальный подпроцесс.
"""
import json
import os
import sys

import pytest

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

import cli as C  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(BACKEND))
REAL_AGENTS = os.path.join(REPO_ROOT, "agents")


# ---------------------------------------------------------------------------
# Типизация параметров
# ---------------------------------------------------------------------------

class TestCoerce:
    def test_booleans(self):
        for v in ("true", "TRUE", "1", "yes", "on", "да"):
            assert C.coerce(v) is True, v
        for v in ("false", "FALSE", "0", "no", "off", "нет"):
            assert C.coerce(v) is False, v

    def test_none(self):
        for v in ("none", "null", "nil", "", "  "):
            assert C.coerce(v) is None, v

    def test_numbers(self):
        assert C.coerce("42") == 42
        assert C.coerce("-7") == -7
        assert C.coerce("1.5") == 1.5

    def test_plain_strings(self):
        assert C.coerce("symbol") == "symbol"
        assert C.coerce("docs/adr") == "docs/adr"

    def test_non_string_passthrough(self):
        assert C.coerce(5) == 5
        assert C.coerce(None) is None

    def test_dry_run_false_is_actually_false(self):
        """Ключевой случай: строка «false» НЕ должна остаться истинной.

        Без типизации `dry_run="false"` — непустая строка, то есть True,
        и сценарий молча пропустил бы запись, которую пользователь явно
        запросил (отчитавшись об успехе).
        """
        assert bool("false") is True          # ловушка, которую убираем
        assert C.coerce("false") is False     # и её снятие


class TestParseParams:
    def test_parses_pairs(self):
        assert C.parse_params(["a=1", "b=x", "c=true"]) == {
            "a": 1, "b": "x", "c": True}

    def test_empty(self):
        assert C.parse_params(None) == {}
        assert C.parse_params([]) == {}

    def test_missing_equals_raises(self):
        with pytest.raises(C.CLIError) as e:
            C.parse_params(["broken"])
        assert "=" in str(e.value)

    def test_empty_key_raises(self):
        with pytest.raises(C.CLIError):
            C.parse_params(["=value"])

    def test_value_with_equals_kept(self):
        assert C.parse_params(["q=a=b"]) == {"q": "a=b"}


# ---------------------------------------------------------------------------
# Парсер аргументов
# ---------------------------------------------------------------------------

class TestParser:
    def test_all_commands_present(self):
        p = C.build_parser()
        acts = [a for a in p._subparsers._group_actions[0].choices]
        for cmd in ("agents", "ask", "review", "tickets", "support",
                    "scenarios", "scenario", "index", "status", "git"):
            assert cmd in acts, cmd

    def test_ask_parses(self):
        a = C.build_parser().parse_args(["ask", "вопрос?", "--top-k", "3"])
        assert a.command == "ask"
        assert a.question == "вопрос?"
        assert a.top_k == 3

    def test_review_defaults(self):
        a = C.build_parser().parse_args(["review"])
        assert a.diff is None and a.base is None
        assert a.no_llm is False

    def test_scenario_repeatable_param(self):
        a = C.build_parser().parse_args(
            ["scenario", "generate", "--param", "kind=adr",
             "--param", "dry_run=true"])
        assert a.param == ["kind=adr", "dry_run=true"]

    def test_agents_show(self):
        a = C.build_parser().parse_args(
            ["agents", "show", "code-review", "--prompt"])
        assert a.agents_action == "show"
        assert a.name == "code-review"
        assert a.prompt is True

    def test_unknown_command_exits(self):
        with pytest.raises(SystemExit):
            C.build_parser().parse_args(["nope"])


# ---------------------------------------------------------------------------
# main(): команды без сервиса
# ---------------------------------------------------------------------------

@pytest.fixture
def repo(tmp_path):
    """Мини-проект: корпус + CRM, чтобы CLI работал офлайн."""
    def w(rel, text):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    w("README.md", "# Проект\n\nОписание проекта.\n")
    w("docs/guide.md", "# Гайд\n\n## Настройка\n\nКак настроить.\n")
    w("studio/backend/mod.py", '"""Модуль."""\n\nX = 1\n')
    w("agents/demo.cli.md",
      "---\nname: demo\nexecution: cli\ndescription: Демо\n"
      "tools: [git]\ntemperature: 0.2\n---\nТы — демо-агент.\n")
    w("data/crm/users.json", json.dumps({"version": 1, "users": [
        {"id": "u-1", "name": "Иван", "plan": "pro", "role": "developer",
         "payment_status": "ok", "channel": "web"}]}, ensure_ascii=False))
    w("data/crm/tickets.json", json.dumps({"version": 1, "tickets": [
        {"id": "T-1", "user_id": "u-1", "subject": "Настройка",
         "body": "Как настроить?", "topic": "configuration",
         "status": "open", "priority": "medium"}]}, ensure_ascii=False))
    (tmp_path / "docs" / "adr").mkdir(parents=True, exist_ok=True)
    return tmp_path


def run(argv, capsys):
    """Запустить CLI, вернуть (код, stdout, stderr)."""
    code = C.main(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture
def repo_argv(repo):
    """Базовые аргументы, привязанные к tmp-репозиторию."""
    return ["--repo", str(repo), "--no-llm"]


class TestAgentsCommands:
    def test_list(self, repo, repo_argv, capsys):
        code, out, _ = run(repo_argv + ["agents", "list"], capsys)
        assert code == 0
        assert "demo" in out
        assert "cli" in out

    def test_list_json(self, repo, repo_argv, capsys):
        code, out, _ = run(repo_argv + ["agents", "list", "--json"], capsys)
        assert code == 0
        data = json.loads(out)
        assert data["agents"][0]["name"] == "demo"
        assert data["errors"] == []

    def test_show(self, repo, repo_argv, capsys):
        code, out, _ = run(
            repo_argv + ["agents", "show", "demo"], capsys)
        assert code == 0
        assert "demo" in out
        assert "Ты — демо-агент" not in out      # промпт скрыт по умолчанию

    def test_show_with_prompt(self, repo, repo_argv, capsys):
        code, out, _ = run(
            repo_argv + ["agents", "show", "demo", "--prompt"], capsys)
        assert code == 0
        assert "Ты — демо-агент" in out

    def test_show_unknown_returns_1(self, repo, repo_argv, capsys):
        code, out, err = run(
            repo_argv + ["agents", "show", "неттакого"], capsys)
        assert code == 1
        assert "не найден" in err
        assert "Traceback" not in err            # без traceback


class TestStatusAndGit:
    def test_status_runs_without_index(self, repo, repo_argv, capsys):
        code, out, _ = run(repo_argv + ["status"], capsys)
        assert code == 0
        assert "Корпус" in out
        assert "НЕ построен" in out

    def test_status_json(self, repo, repo_argv, capsys):
        code, out, _ = run(repo_argv + ["status", "--json"], capsys)
        assert code == 0
        assert "corpus" in json.loads(out)

    def test_git_without_repo(self, repo, repo_argv, capsys):
        code, out, _ = run(repo_argv + ["git"], capsys)
        assert code == 0
        assert "недоступен" in out


class TestIndexCommand:
    def test_index_builds(self, repo, repo_argv, capsys):
        code, out, _ = run(repo_argv + ["index"], capsys)
        assert code == 0
        assert "Индекс собран" in out

    def test_index_bad_strategy_exits_2(self, repo, repo_argv):
        with pytest.raises(SystemExit) as e:
            C.main(repo_argv + ["index", "--strategy", "nope"])
        assert e.value.code == 2


class TestTicketsCommand:
    def test_lists_tickets(self, repo, repo_argv, capsys):
        code, out, _ = run(repo_argv + ["tickets"], capsys)
        assert code == 0
        assert "T-1" in out
        assert "Настройка" in out

    def test_filter_matches(self, repo, repo_argv, capsys):
        code, out, _ = run(
            repo_argv + ["tickets", "--status", "open"], capsys)
        assert code == 0 and "T-1" in out

    def test_filter_excludes(self, repo, repo_argv, capsys):
        code, out, _ = run(
            repo_argv + ["tickets", "--status", "closed"], capsys)
        assert code == 0
        assert "T-1" not in out
        assert "нет" in out

    def test_tickets_json(self, repo, repo_argv, capsys):
        code, out, _ = run(repo_argv + ["tickets", "--json"], capsys)
        assert json.loads(out)["tickets"][0]["id"] == "T-1"


class TestScenariosCommands:
    def test_scenarios_list(self, repo, repo_argv, capsys):
        code, out, _ = run(repo_argv + ["scenarios"], capsys)
        assert code == 0
        for n in ("find_usages", "update_docs", "generate",
                  "check_invariants", "diff_report"):
            assert n in out

    def test_find_usages(self, repo, repo_argv, capsys):
        code, out, _ = run(
            repo_argv + ["scenario", "find_usages",
                         "--param", "symbol=X"], capsys)
        assert code == 0
        assert "studio/backend/mod.py" in out

    def test_unknown_scenario_returns_1(self, repo, repo_argv, capsys):
        code, out, err = run(
            repo_argv + ["scenario", "nope"], capsys)
        assert code == 1
        assert "Неизвестный сценарий" in err

    def test_unknown_param_does_not_crash(self, repo, repo_argv, capsys):
        """Лишний параметр игнорируется (`**_` в сигнатуре сценария)."""
        code, _, _ = run(
            repo_argv + ["scenario", "check_invariants",
                         "--param", "лишний=1"], capsys)
        assert code == 0

    def test_dry_run_true_writes_nothing(self, repo, repo_argv, capsys):
        code, out, _ = run(
            repo_argv + ["scenario", "generate", "--param", "kind=adr",
                         "--param", "title=Test", "--param", "dry_run=true"],
            capsys)
        assert code == 0
        assert "dry-run" in out
        assert not os.listdir(str(repo / "docs" / "adr"))

    def test_dry_run_false_actually_writes(self, repo, repo_argv, capsys):
        """`dry_run=false` — это ЗАПРОС ЗАПИСИ, и он должен записать.

        Ловушка: без типизации строка «false» истинна, `not dry_run`
        даёт False, и сценарий молча ничего не сделал бы, отчитавшись
        об успехе.
        """
        code, out, _ = run(
            repo_argv + ["scenario", "generate", "--param", "kind=adr",
                         "--param", "title=Real", "--param",
                         "dry_run=false"], capsys)
        assert code == 0
        assert os.listdir(str(repo / "docs" / "adr"))

    def test_generate_out_writes_report(self, repo, repo_argv, capsys):
        """`--out` записывает вывод сценария в файл."""
        target = str(repo / "report.txt")
        code, _, _ = run(
            repo_argv + ["scenario", "generate", "--param", "kind=adr",
                         "--param", "title=Отчёт", "--json",
                         "--out", target], capsys)
        assert code == 0
        assert os.path.isfile(target)

    def test_diff_report_without_git_returns_1(self, repo, repo_argv,
                                               capsys):
        """`diff_report` требует git — в tmp-каталоге его нет → код 1.

        Ожидаемое поведение: сценарий не выдумывает изменения там, где
        не может их прочитать.
        """
        code, _, err = run(
            repo_argv + ["scenario", "diff_report",
                         "--param", "path=docs/r.md"], capsys)
        assert code == 1
        assert "git" in err.lower()

    def test_write_outside_allowlist_returns_1(self, repo, repo_argv,
                                               capsys):
        code, _, err = run(
            repo_argv + ["scenario", "generate", "--param", "kind=readme",
                         "--param", "path=../evil.md"], capsys)
        assert code == 1
        assert "Ошибка" in err
        assert not os.path.exists(str(repo.parent / "evil.md"))


class TestAskCommand:
    def test_ask_requires_index(self, repo, repo_argv, capsys):
        code, _, err = run(repo_argv + ["ask", "что тут?"], capsys)
        assert code == 1
        assert "индекс" in err.lower()

    def test_ask_after_index(self, repo, repo_argv, capsys):
        C.main(repo_argv + ["index"])
        capsys.readouterr()
        code, out, _ = run(repo_argv + ["ask", "настройка"], capsys)
        assert code == 0
        assert out.strip()

    def test_ask_json(self, repo, repo_argv, capsys):
        C.main(repo_argv + ["index"])
        capsys.readouterr()
        code, out, _ = run(
            repo_argv + ["ask", "настройка", "--json"], capsys)
        data = json.loads(out)
        assert "answer" in data and "sources" in data


class TestReviewCommand:
    DIFF = (
        "diff --git a/studio/backend/mod.py b/studio/backend/mod.py\n"
        "--- a/studio/backend/mod.py\n"
        "+++ b/studio/backend/mod.py\n"
        "@@ -1,2 +1,4 @@\n"
        ' """Модуль."""\n'
        "+SECRET = \"sk-abcdef1234567890abcd\"\n"
        "+print(\"no tag\")\n"
        " X = 1\n")

    def test_review_from_file(self, repo, repo_argv, tmp_path, capsys):
        d = tmp_path / "x.diff"
        d.write_text(self.DIFF, encoding="utf-8")
        code, out, _ = run(
            repo_argv + ["review", "--diff", str(d), "--no-llm"], capsys)
        assert code == 0
        assert "secret" in out.lower() or "критично" in out.lower()
        assert "studio/backend/mod.py:" in out      # файл:строка

    def test_review_missing_file_returns_1(self, repo, repo_argv, capsys):
        code, _, err = run(
            repo_argv + ["review", "--diff", "/nope/x.diff"], capsys)
        assert code == 1
        assert "не найден" in err

    def test_review_json(self, repo, repo_argv, tmp_path, capsys):
        d = tmp_path / "x.diff"
        d.write_text(self.DIFF, encoding="utf-8")
        code, out, _ = run(
            repo_argv + ["review", "--diff", str(d), "--no-llm", "--json"],
            capsys)
        data = json.loads(out)
        assert data["summary"]["files"] == 1
        assert any(f["rule"] == "secret" for f in data["findings"])

    def test_fail_on_high_returns_1(self, repo, repo_argv, tmp_path, capsys):
        d = tmp_path / "x.diff"
        d.write_text(self.DIFF, encoding="utf-8")
        code, _, err = run(
            repo_argv + ["review", "--diff", str(d), "--no-llm",
                         "--fail-on-high"], capsys)
        assert code == 1
        assert "критично" in err

    def test_review_out_writes_file(self, repo, repo_argv, tmp_path, capsys):
        d = tmp_path / "x.diff"
        d.write_text(self.DIFF, encoding="utf-8")
        target = tmp_path / "review.md"
        code, out, _ = run(
            repo_argv + ["review", "--diff", str(d), "--no-llm",
                         "--out", str(target)], capsys)
        assert code == 0
        assert "Записано" in out
        assert "Ревью" in target.read_text(encoding="utf-8")


class TestSupportCommand:
    def test_support_personalized(self, repo, repo_argv, capsys):
        C.main(repo_argv + ["index"])
        capsys.readouterr()
        code, out, _ = run(repo_argv + ["support", "T-1"], capsys)
        assert code == 0
        assert "T-1" in out
        assert "pro" in out                      # план применён
        assert "Учтено из CRM" in out

    def test_support_unknown_ticket_ok(self, repo, repo_argv, capsys):
        code, out, _ = run(repo_argv + ["support", "T-404"], capsys)
        assert code == 0
        assert "не найден" in out

    def test_support_no_ticket_arg_exits_2(self, repo, repo_argv):
        with pytest.raises(SystemExit) as e:
            C.main(repo_argv + ["support"])
        assert e.value.code == 2


class TestAgentSelection:
    def test_default_agent_used(self, repo, repo_argv, capsys, monkeypatch):
        """`ask` без --agent берёт project-qa; промпт уходит в сервис."""
        C.main(repo_argv + ["index"])
        capsys.readouterr()
        captured = {}
        real = C.AssistantService.ask_project

        def spy(self, question, top_k=5, system_prompt=None):
            captured["prompt"] = system_prompt
            return real(self, question, top_k=top_k,
                        system_prompt=system_prompt)

        monkeypatch.setattr(C.AssistantService, "ask_project", spy)
        run(repo_argv + ["ask", "настройка"], capsys)
        assert captured["prompt"] is None        # агента project-qa нет в tmp

    def test_explicit_agent_prompt_passed(self, repo, repo_argv, capsys,
                                          monkeypatch):
        C.main(repo_argv + ["index"])
        capsys.readouterr()
        captured = {}
        real = C.AssistantService.ask_project

        def spy(self, question, top_k=5, system_prompt=None):
            captured["prompt"] = system_prompt
            return real(self, question, top_k=top_k,
                        system_prompt=system_prompt)

        monkeypatch.setattr(C.AssistantService, "ask_project", spy)
        run(repo_argv + ["ask", "настройка", "--agent", "demo"], capsys)
        assert captured["prompt"] == "Ты — демо-агент."

    def test_missing_agent_warns_but_runs(self, repo, repo_argv, capsys):
        C.main(repo_argv + ["index"])
        capsys.readouterr()
        code, out, err = run(
            repo_argv + ["ask", "настройка", "--agent", "неттакого"], capsys)
        assert code == 0                          # команда работает
        assert "не найден" in err                 # но предупреждает


class TestThinWrapper:
    """CLI не должен дублировать логику ассистента (принцип «обёртка»)."""

    def test_cli_does_not_import_low_level_modules(self):
        src = open(C.__file__, encoding="utf-8").read()
        for mod in ("import kb", "import mcp", "import gateway",
                    "from kb ", "from mcp ", "from gateway "):
            assert mod not in src, f"CLI импортирует {mod!r}"

    def test_cli_has_no_network_calls(self):
        src = open(C.__file__, encoding="utf-8").read()
        for bad in ("urllib", "requests.", "httpx.", "socket."):
            assert bad not in src, f"CLI использует сеть напрямую: {bad}"

    def test_cli_has_no_git_mutations(self):
        src = open(C.__file__, encoding="utf-8").read()
        for bad in ('"commit"', '"push"', '"checkout"'):
            assert bad not in src


class TestRealAgentsViaCli:
    def test_real_repo_agents_list(self, capsys):
        code, out, _ = run(["--repo", REPO_ROOT, "agents", "list"], capsys)
        assert code == 0
        for n in ("project-qa", "code-review", "support", "file-ops"):
            assert n in out

    def test_real_agents_have_cli_and_api(self, capsys):
        code, out, _ = run(
            ["--repo", REPO_ROOT, "agents", "list", "--json"], capsys)
        data = json.loads(out)
        pairs = {(a["name"], a["execution"]) for a in data["agents"]}
        for n in ("project-qa", "code-review", "support", "file-ops"):
            assert (n, "cli") in pairs
            assert (n, "api") in pairs
