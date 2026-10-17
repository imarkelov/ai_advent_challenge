"""Тесты для Задания 31 дня: CLI-ассистент разработчика с RAG и MCP.

Проверяют:
  1. MCP-сервер studio/mcp_servers/git_branch.py (git_branch, git_files, git_diff).
  2. RAG по документации проекта (README, docs/, openspec/ схемы и API).
  3. Команду /help и работу CLI-ассистента dev_assistant.py (RAG + Git-контекст).
"""
import io
import json
import os
import sys
import unittest
from unittest.mock import patch

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import dev_assistant as DA
from studio.mcp_servers import git_branch as GB


class TestMCPGitBranchServer(unittest.TestCase):
    """Тестирование MCP-сервера git_branch.py."""

    def test_mcp_tools_list(self):
        """Сервер объявляет 3 инструмента: git_branch, git_files, git_diff."""
        tool_names = [t["name"] for t in GB.TOOLS]
        self.assertIn("git_branch", tool_names)
        self.assertIn("git_files", tool_names)
        self.assertIn("git_diff", tool_names)

    def test_call_git_branch(self):
        """Инструмент git_branch возвращает имя текущей ветки."""
        payload, is_err = GB.call_git_branch({})
        self.assertFalse(is_err)
        self.assertIn("branch", payload)
        self.assertTrue(len(payload["branch"]) > 0)

    def test_call_git_files(self):
        """Инструмент git_files возвращает списки tracked и untracked."""
        payload, is_err = GB.call_git_files({})
        self.assertFalse(is_err)
        self.assertIn("tracked", payload)
        self.assertIn("untracked", payload)
        self.assertIn("total", payload)
        self.assertIsInstance(payload["total"], int)

    def test_call_git_diff(self):
        """Инструмент git_diff возвращает diff рабочего дерева."""
        payload, is_err = GB.call_git_diff({"max_lines": 50})
        self.assertFalse(is_err)
        self.assertIn("diff", payload)
        self.assertIn("lines", payload)
        self.assertIn("truncated", payload)

    def test_mcp_handle_initialize_and_call(self):
        """Протокол JSON-RPC: initialize, tools/list, tools/call."""
        # initialize
        init_res = GB.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        self.assertEqual(init_res["id"], 1)
        self.assertIn("serverInfo", init_res["result"])

        # tools/list
        list_res = GB.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual(len(list_res["result"]["tools"]), 3)

        # tools/call git_branch
        call_res = GB.handle({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "git_branch", "arguments": {}}
        })
        self.assertNotIn("isError", call_res["result"])
        text = call_res["result"]["content"][0]["text"]
        data = json.loads(text)
        self.assertIn("branch", data)


class TestProjectRAG(unittest.TestCase):
    """Тестирование подключения RAG к документации и схемам."""

    def setUp(self):
        self.rag = DA.ProjectRAG(REPO_ROOT)

    def test_corpus_includes_readme_docs_openspec(self):
        """Корпус содержит README, файлы docs/ и openspec/."""
        files = self.rag.corpus.files()
        has_readme = any("README.md" in f for f in files)
        has_docs = any(f.startswith("docs/") for f in files)
        has_openspec = any(f.startswith("openspec/") for f in files)

        self.assertTrue(has_readme, "Корпус должен содержать README.md")
        self.assertTrue(has_docs, "Корпус должен содержать docs/")
        self.assertTrue(has_openspec, "Корпус должен содержать openspec/")

    def test_rag_search_project_structure(self):
        """Поиск по структуре проекта находит релевантные фрагменты."""
        hits = self.rag.search("шлюз gateway api", top_k=3)
        self.assertTrue(len(hits) > 0)
        top = hits[0]
        self.assertIn("file", top)
        self.assertIn("text", top)
        self.assertGreater(top["score"], 0)


class TestDevAssistantCLI(unittest.TestCase):
    """Тестирование CLI-ассистента и команды /help."""

    def setUp(self):
        DA.C.disable()  # Отключаем цвета для чистой проверки строк
        self.cli = DA.DevAssistantCLI(REPO_ROOT)

    def tearDown(self):
        self.cli.mcp.close()

    def test_cmd_help_with_question(self):
        """Команда /help <вопрос> выводит ответ с RAG и контекстом Git."""
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            self.cli.cmd_help("как устроена архитектура проекта?")
        output = captured.getvalue()
        self.assertIn("Ответ ассистента", output)
        self.assertIn("Ветка Git", output)
        self.assertIn("Источники RAG", output)

    def test_cmd_help_menu(self):
        """Команда /help без аргументов показывает меню."""
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            self.cli.cmd_help_menu()
        output = captured.getvalue()
        self.assertIn("/help", output)
        self.assertIn("/git", output)
        self.assertIn("/diff", output)

    def test_cmd_git(self):
        """Команда /git выводит текущую ветку через MCP."""
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            self.cli.cmd_git()
        output = captured.getvalue()
        self.assertIn("Ветка:", output)
        self.assertIn("через MCP", output)

    def test_cmd_files(self):
        """Команда /files выводит список файлов через MCP."""
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            self.cli.cmd_files()
        output = captured.getvalue()
        self.assertIn("Изменённые файлы", output)
        self.assertIn("через MCP", output)

    def test_cmd_status(self):
        """Команда /status выводит состояние компонентов."""
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            self.cli.cmd_status()
        output = captured.getvalue()
        self.assertIn("Корпус RAG:", output)
        self.assertIn("MCP-сервер:", output)
        self.assertIn("Git:", output)

    def test_cmd_clear(self):
        """Команда clear очищает экран терминала и выводит баннер."""
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            self.cli.cmd_clear()
        output = captured.getvalue()
        self.assertIn("CLI Ассистент разработчика", output)

    def test_help_without_slash_shows_menu(self):
        """Команда 'help' (без слеша) показывает список доступных команд."""
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            self.cli.execute_command("help")
        output = captured.getvalue()
        self.assertIn("Список доступных команд:", output)
        self.assertIn("clear", output)
        self.assertIn("git", output)

    def test_dialog_without_help_prefix(self):
        """Прямой диалог: вопрос без команды help вызывает поиск по проекту."""
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            self.cli.execute_command("как запустить проект?")
        output = captured.getvalue()
        self.assertIn("Ответ ассистента", output)
        self.assertIn("Ветка Git", output)

    def test_create_file_safely(self):
        """Создание файла и добавление в RAG."""
        rel_path = "docs/test_sample_created.md"
        ok, full = self.cli.create_file(rel_path, "# Тест\nФайл создан ассистентом.\n")
        try:
            self.assertTrue(ok)
            self.assertTrue(os.path.isfile(full))
        finally:
            if os.path.isfile(full):
                os.remove(full)

    def test_tab_autocomplete_list(self):
        """Список команд для Tab автодополнения включает все основные команды."""
        self.assertIn("/help", DA.AUTOCOMPLETE_COMMANDS)
        self.assertIn("help", DA.AUTOCOMPLETE_COMMANDS)
        self.assertIn("/clear", DA.AUTOCOMPLETE_COMMANDS)
        self.assertIn("clear", DA.AUTOCOMPLETE_COMMANDS)
        self.assertIn("git", DA.AUTOCOMPLETE_COMMANDS)
        self.assertIn("status", DA.AUTOCOMPLETE_COMMANDS)

    def test_auto_collect_and_save_docs_when_missing(self):
        """Если информации в проекте нет, агент сам собирает её, пишет в docs/ и добавляет в RAG."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Создаём проект без документации
            os.makedirs(os.path.join(tmp_dir, "src"), exist_ok=True)
            with open(os.path.join(tmp_dir, "src", "app.py"), "w") as f:
                f.write("def run():\n    print('Hello World')\n")
            with open(os.path.join(tmp_dir, "requirements.txt"), "w") as f:
                f.write("fastapi\nuvicorn\n")

            cli = DA.DevAssistantCLI(tmp_dir)
            self.assertFalse(cli.rag.has_documents())

            # Запускаем сбор информации при запросе
            collected = cli.ensure_project_knowledge("как устроен проект?")
            self.assertTrue(collected)

            # Проверяем, что файл документации создался на диске
            doc_file = os.path.join(tmp_dir, "docs", "PROJECT_STRUCTURE.md")
            self.assertTrue(os.path.isfile(doc_file))

            # И RAG теперь содержит созданную документацию
            self.assertTrue(cli.rag.has_documents())
            hits = cli.rag.search("проект", top_k=2)
            self.assertGreater(len(hits), 0)

    def test_execute_command_dispatch(self):
        """Диспетчеризация команд в CLI (со слешем и без)."""
        self.assertTrue(self.cli.execute_command("/status"))
        self.assertTrue(self.cli.execute_command("status"))
        self.assertTrue(self.cli.execute_command("clear"))
        self.assertFalse(self.cli.execute_command("/exit"))
        self.assertFalse(self.cli.execute_command("exit"))


if __name__ == "__main__":
    unittest.main()
