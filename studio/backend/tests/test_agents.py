"""Тесты реестра агентов (день 35) — офлайн, без сети.

Покрывают: разбор frontmatter без YAML-пакета, валидацию обязательных
полей и `execution`, выборку с деградацией на другое исполнение, кэш по
mtime, устойчивость к пустому/битому каталогу и — отдельно — реальные
файлы `agents/*.md` репозитория.
"""
import os
import sys
import textwrap
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agents as G  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
REAL_AGENTS = os.path.join(REPO_ROOT, "agents")

VALID = textwrap.dedent("""\
    ---
    name: demo
    execution: cli
    description: Демонстрационный агент
    tools: [git, diff]
    temperature: 0.3
    ---
    Ты — демо-агент. Отвечай кратко.
    """)


# ---------------------------------------------------------------------------
# Разбор frontmatter
# ---------------------------------------------------------------------------

class TestParseFrontmatter:
    def test_splits_meta_and_body(self):
        meta, body = G.parse_frontmatter(VALID, "x.md")
        assert meta["name"] == "demo"
        assert meta["execution"] == "cli"
        assert meta["temperature"] == 0.3
        assert body.startswith("Ты — демо-агент")

    def test_inline_list(self):
        meta, _ = G.parse_frontmatter(VALID, "x.md")
        assert meta["tools"] == ["git", "diff"]

    def test_empty_inline_list(self):
        meta, _ = G.parse_frontmatter("---\ntools: []\n---\nтело\n", "x")
        assert meta["tools"] == []

    def test_quoted_values(self):
        meta, _ = G.parse_frontmatter(
            '---\nname: "a b"\ndescription: \'c d\'\n---\nтело\n', "x")
        assert meta["name"] == "a b"
        assert meta["description"] == "c d"

    def test_bool_and_int_values(self):
        meta, _ = G.parse_frontmatter(
            "---\nflag: true\nn: 5\nf: 1.5\n---\nтело\n", "x")
        assert meta["flag"] is True
        assert meta["n"] == 5
        assert meta["f"] == 1.5

    def test_comments_and_blank_lines_skipped(self):
        meta, _ = G.parse_frontmatter(
            "---\n# комментарий\n\nname: x\n---\nтело\n", "x")
        assert meta == {"name": "x"}

    def test_missing_block_raises(self):
        with pytest.raises(G.AgentError) as e:
            G.parse_frontmatter("просто текст без frontmatter", "f.md")
        assert "frontmatter" in str(e.value)
        assert "f.md" in str(e.value)

    def test_empty_file_raises(self):
        with pytest.raises(G.AgentError):
            G.parse_frontmatter("", "f.md")
        with pytest.raises(G.AgentError):
            G.parse_frontmatter("   \n  ", "f.md")

    def test_line_without_colon_raises(self):
        with pytest.raises(G.AgentError) as e:
            G.parse_frontmatter("---\nname x\n---\nтело\n", "f.md")
        assert "строк" in str(e.value)

    def test_bom_is_tolerated(self):
        meta, body = G.parse_frontmatter("\ufeff" + VALID, "x.md")
        assert meta["name"] == "demo"
        assert body


# ---------------------------------------------------------------------------
# Валидация агента
# ---------------------------------------------------------------------------

class TestAgentFromMd:
    def test_valid_spec(self):
        s = G.agent_from_md(VALID, "agents/demo.cli.md")
        assert s.name == "demo"
        assert s.execution == "cli"
        assert s.tools == ["git", "diff"]
        assert s.temperature == 0.3
        assert s.fallback is False
        assert s.system_prompt

    def test_defaults_for_optional_fields(self):
        s = G.agent_from_md(
            "---\nname: a\nexecution: cli\ndescription: d\n---\nтело\n", "x")
        assert s.tools == []
        assert s.temperature == G.DEFAULT_TEMPERATURE

    @pytest.mark.parametrize("missing", ["name", "execution", "description"])
    def test_required_fields(self, missing):
        lines = {"name": "name: a", "execution": "execution: cli",
                 "description": "description: d"}
        lines.pop(missing)
        text = "---\n" + "\n".join(lines.values()) + "\n---\nтело\n"
        with pytest.raises(G.AgentError) as e:
            G.agent_from_md(text, "f.md")
        assert missing in str(e.value)

    def test_empty_body_raises(self):
        with pytest.raises(G.AgentError) as e:
            G.agent_from_md(
                "---\nname: a\nexecution: cli\ndescription: d\n---\n\n", "f")
        assert "тело" in str(e.value)

    def test_bad_execution_raises(self):
        with pytest.raises(G.AgentError) as e:
            G.agent_from_md(
                "---\nname: a\nexecution: web\ndescription: d\n---\nт\n", "f")
        assert "execution" in str(e.value)

    def test_execution_is_case_insensitive(self):
        s = G.agent_from_md(
            "---\nname: a\nexecution: CLI\ndescription: d\n---\nт\n", "f")
        assert s.execution == "cli"

    def test_bad_temperature_raises(self):
        with pytest.raises(G.AgentError) as e:
            G.agent_from_md(
                "---\nname: a\nexecution: cli\ndescription: d\n"
                "temperature: горячо\n---\nт\n", "f")
        assert "temperature" in str(e.value)

    def test_tools_as_comma_string(self):
        s = G.agent_from_md(
            "---\nname: a\nexecution: cli\ndescription: d\n"
            "tools: git, diff\n---\nт\n", "f")
        assert s.tools == ["git", "diff"]

    def test_to_dict_hides_prompt_by_default(self):
        s = G.agent_from_md(VALID, "/x/demo.cli.md")
        d = s.to_dict()
        assert "system_prompt" not in d
        assert d["file"] == "demo.cli.md"
        assert s.to_dict(with_prompt=True)["system_prompt"]


# ---------------------------------------------------------------------------
# Реестр
# ---------------------------------------------------------------------------

def _write(dirpath, filename, text):
    p = os.path.join(str(dirpath), filename)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return p


class TestAgentRegistry:
    def test_loads_and_lists(self, tmp_path):
        _write(tmp_path, "a.cli.md", VALID.replace("demo", "a"))
        _write(tmp_path, "b.api.md", VALID.replace("demo", "b")
               .replace("cli", "api"))
        r = G.AgentRegistry(str(tmp_path))
        assert r.names() == ["a", "b"]
        assert len(r.list()) == 2
        assert r.errors() == []

    def test_get_exact_execution(self, tmp_path):
        _write(tmp_path, "a.cli.md", VALID.replace("demo", "a"))
        _write(tmp_path, "a.api.md", VALID.replace("demo", "a")
               .replace("cli", "api"))
        r = G.AgentRegistry(str(tmp_path))
        assert r.get("a", "cli").execution == "cli"
        assert r.get("a", "api").execution == "api"
        assert r.get("a", "cli").fallback is False

    def test_fallback_to_other_execution_is_flagged(self, tmp_path):
        """Нет .cli.md — берём .api.md, но ЯВНО помечаем подмену."""
        _write(tmp_path, "a.api.md", VALID.replace("demo", "a")
               .replace("cli", "api"))
        r = G.AgentRegistry(str(tmp_path))
        s = r.get("a", "cli")
        assert s.execution == "api"
        assert s.fallback is True
        assert s.to_dict()["fallback"] is True

    def test_unknown_agent_lists_available(self, tmp_path):
        _write(tmp_path, "a.cli.md", VALID.replace("demo", "a"))
        r = G.AgentRegistry(str(tmp_path))
        with pytest.raises(G.AgentError) as e:
            r.get("неттакого", "cli")
        assert "не найден" in str(e.value)
        assert "a" in str(e.value)

    def test_empty_name_raises(self, tmp_path):
        r = G.AgentRegistry(str(tmp_path))
        with pytest.raises(G.AgentError):
            r.get("  ", "cli")

    def test_missing_dir_is_not_fatal(self, tmp_path):
        r = G.AgentRegistry(str(tmp_path / "nope"))
        assert r.load() == []
        assert r.list() == []
        assert r.errors() == []

    def test_broken_file_reported_not_fatal(self, tmp_path):
        _write(tmp_path, "good.cli.md", VALID.replace("demo", "good"))
        _write(tmp_path, "bad.cli.md", "нет frontmatter вообще")
        r = G.AgentRegistry(str(tmp_path))
        assert r.names() == ["good"]          # валидный загрузился
        errs = r.errors()
        assert len(errs) == 1
        assert "bad.cli.md" in errs[0]

    def test_no_frontmatter_file_reported(self, tmp_path):
        _write(tmp_path, "x.cli.md", "---\nname: x\n---\nтело\n")
        r = G.AgentRegistry(str(tmp_path))
        assert r.errors()                       # нет execution/description

    def test_non_md_files_ignored(self, tmp_path):
        _write(tmp_path, "readme.txt", "не агент")
        _write(tmp_path, "a.cli.md", VALID.replace("demo", "a"))
        assert G.AgentRegistry(str(tmp_path)).names() == ["a"]

    def test_cache_invalidated_on_change(self, tmp_path):
        """Правка md подхватывается без пересоздания реестра."""
        p = _write(tmp_path, "a.cli.md", VALID.replace("demo", "a"))
        r = G.AgentRegistry(str(tmp_path))
        first = r.get("a", "cli").description
        assert first == "Демонстрационный агент"
        # mtime имеет секундное разрешение на части ФС — сдвигаем явно.
        time.sleep(0.01)
        with open(p, "w", encoding="utf-8") as f:
            f.write(VALID.replace("demo", "a")
                    .replace("Демонстрационный агент", "Новое описание"))
        os.utime(p, (time.time() + 1, time.time() + 1))
        assert r.get("a", "cli").description == "Новое описание"

    def test_prompt_for_returns_tuple(self, tmp_path):
        _write(tmp_path, "a.cli.md", VALID.replace("demo", "a"))
        r = G.AgentRegistry(str(tmp_path))
        prompt, fb = r.prompt_for("a", "cli")
        assert prompt.startswith("Ты — демо-агент")
        assert fb is False

    def test_import_has_no_side_effects(self):
        """Импорт модуля не читает диск: реестр читает только load()."""
        src = open(G.__file__, encoding="utf-8").read()
        # На уровне модуля нет вызовов listdir/open — только в методах.
        head = src.split("class AgentRegistry")[0]
        assert "os.listdir" not in head


# ---------------------------------------------------------------------------
# Реальные агенты репозитория
# ---------------------------------------------------------------------------

class TestRealAgents:
    """Файлы agents/*.md в репозитории — часть поставки, их надо проверять."""

    def test_all_eight_files_present(self):
        expected = {f"{n}.{e}.md"
                    for n in ("project-qa", "code-review", "support",
                              "file-ops")
                    for e in ("api", "cli")}
        # День 32 добавил агента `pr-review` (только CLI: его вызывают
        # из GitHub Action, веб-вкладки у него нет) — это осознанное
        # расширение поставки, а не нарушение «4 × 2».
        expected |= {"pr-review.cli.md"}
        actual = {f for f in os.listdir(REAL_AGENTS) if f.endswith(".md")}
        assert actual == expected

    def test_registry_loads_without_errors(self):
        r = G.AgentRegistry(REAL_AGENTS)
        assert r.errors() == []
        assert len(r.load()) == 9
        assert r.names() == ["code-review", "file-ops", "pr-review",
                             "project-qa", "support"]

    @pytest.mark.parametrize("name", ["project-qa", "code-review",
                                      "support", "file-ops"])
    @pytest.mark.parametrize("execution", ["api", "cli"])
    def test_each_agent_resolves_without_fallback(self, name, execution):
        s = G.AgentRegistry(REAL_AGENTS).get(name, execution=execution)
        assert s.name == name
        assert s.execution == execution
        assert s.fallback is False
        assert len(s.system_prompt) > 100
        assert s.description
        assert s.tools

    def test_api_and_cli_prompts_differ(self):
        """Исполнения различаются содержимым, а не только именем файла."""
        r = G.AgentRegistry(REAL_AGENTS)
        for name in r.names():
            if name == "pr-review":
                # День 32: агент только CLI (fallback на cli-исполнение
                # помечен `fallback`, чтобы не выдавать его за api).
                assert r.get(name, "api").fallback is True
                continue
            api = r.get(name, "api").system_prompt
            cli = r.get(name, "cli").system_prompt
            assert api != cli, f"{name}: промпты api/cli совпадают"

    def test_cli_prompts_mention_terminal_constraints(self):
        """CLI-промпты учитывают терминал (главная причина разделения)."""
        r = G.AgentRegistry(REAL_AGENTS)
        for name in r.names():
            if name == "pr-review":
                continue          # канал — комментарий PR/CI, не терминал
            p = r.get(name, "cli").system_prompt.lower()
            assert "html" in p or "терминал" in p, name

    def test_no_html_instructions_in_cli(self):
        """CLI-промпт ПРЕДПИСЫВАЕТ markdown и запрещает HTML.

        Проверяем именно предписание, а не факт упоминания слова: строка
        «НЕ используй HTML» — это правильный запрет, и наивная проверка
        подстроки «используй html» ловила бы её как нарушение (на этом
        тест сначала падал).
        """
        r = G.AgentRegistry(REAL_AGENTS)
        negative = ("не используй html", "не используйте html",
                    "без html", "не используй теги")
        for name in r.names():
            p = r.get(name, "cli").system_prompt.lower()
            for neg in negative:
                # каждое вхождение «...html» должно быть частью запрета
                idx = p.find("html")
                if idx != -1:
                    window = p[max(0, idx - 40):idx + 20]
                    assert ("не " in window or "без " in window
                            or "не использ" in window), \
                        f"{name}: HTML упомянут без запрета: ...{window}..."
