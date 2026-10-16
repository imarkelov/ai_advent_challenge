"""Тесты дней 30–34: роуты ассистента (RAG, ревью, поддержка, сценарии).

Офлайн: tmp-каталог (реальный `studio/data` и `data/kb` не трогаются),
TestClient как клиент. Сервис инжектится через `create_app(assistant=…)`.
"""
import json

import pytest
from fastapi.testclient import TestClient

import main as main_mod
from assistant import AssistantService
from agent import StudioAgent
from kb import KnowledgeBase

DIFF = (
    "diff --git a/studio/backend/mod.py b/studio/backend/mod.py\n"
    "--- a/studio/backend/mod.py\n"
    "+++ b/studio/backend/mod.py\n"
    "@@ -1,2 +1,4 @@\n"
    ' """Модуль."""\n'
    "+SECRET_TOKEN = \"sk-abcdef1234567890abcd\"\n"
    "+print(\"no tag\")\n"
    " x = 1\n"
)


@pytest.fixture
def repo(tmp_path):
    """Мини-проект: документация + код + CRM."""
    def w(rel, text):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    w("README.md", "# Проект\n\nНазначение: обучение и демонстрация.\n")
    w("docs/guide.md", "# Гайд\n\n## Настройка\n\nКак настроить систему.\n")
    w("studio/backend/mod.py",
      '"""Модуль."""\n\n\ndef target_function(a, b):\n    return a + b\n')
    w("studio/backend/tests/test_mod.py", '"""Тесты."""\n')
    w("data/crm/users.json", json.dumps({"version": 1, "users": [
        {"id": "u-1", "name": "Иван", "plan": "pro", "role": "developer",
         "payment_status": "ok", "channel": "web",
         "registered_at": "2026-01-01"}]}, ensure_ascii=False))
    w("data/crm/tickets.json", json.dumps({"version": 1, "tickets": [
        {"id": "T-1", "user_id": "u-1", "subject": "Настройка системы",
         "body": "Как настроить систему?", "topic": "configuration",
         "status": "open", "priority": "medium", "tags": []}]},
        ensure_ascii=False))
    return tmp_path


@pytest.fixture
def client(repo):
    """TestClient с ассистентом без LLM (офлайн, детерминированно)."""
    agent = StudioAgent(str(repo / "studio-data"))
    assistant = AssistantService(str(repo), agent=None,
                                 kb_root=str(repo / "kbdata"))
    app = main_mod.create_app(agent=agent,
                              kb=KnowledgeBase(str(repo / "kb")),
                              assistant=assistant)
    return TestClient(app)


# ---------------------------------------------------------------------------
# status / index
# ---------------------------------------------------------------------------

class TestStatusRoutes:
    def test_status_before_index(self, client):
        r = client.get("/api/assistant/status")
        assert r.status_code == 200
        d = r.json()
        assert d["indexed"] is False
        assert d["corpus"]["files"] >= 3
        assert d["crm"]["tickets"] == 1

    def test_index_builds(self, client):
        r = client.post("/api/assistant/index", json={})
        assert r.status_code == 200
        d = r.json()
        assert d["stats"]["docs"] >= 3
        assert d["stats"]["chunks"] >= 1

    def test_index_bad_strategy(self, client):
        r = client.post("/api/assistant/index", json={"strategy": "nope"})
        assert r.status_code == 400
        assert "strategy" in r.json()["detail"]

    def test_status_after_index(self, client):
        client.post("/api/assistant/index", json={})
        d = client.get("/api/assistant/status").json()
        assert d["indexed"] is True
        assert d["documents"] >= 3


# ---------------------------------------------------------------------------
# ask
# ---------------------------------------------------------------------------

class TestAskRoute:
    def test_ask_requires_index(self, client):
        r = client.post("/api/assistant/ask", json={"question": "что тут?"})
        assert r.status_code == 409
        assert "индекс" in r.json()["detail"].lower()

    def test_ask_requires_question(self, client):
        assert client.post("/api/assistant/ask", json={}).status_code == 400
        assert client.post("/api/assistant/ask",
                           json={"question": "  "}).status_code == 400

    def test_ask_returns_answer_and_sources(self, client):
        client.post("/api/assistant/index", json={})
        r = client.post("/api/assistant/ask",
                        json={"question": "как настроить систему?"})
        assert r.status_code == 200
        d = r.json()
        assert d["found"] is True
        assert d["sources"]
        assert d["llm"] is False            # агента нет — офлайн
        for s in d["sources"]:
            assert "file" in s and "quote" in s

    def test_ask_top_k_clamped(self, client):
        client.post("/api/assistant/index", json={})
        r = client.post("/api/assistant/ask",
                        json={"question": "система", "top_k": 999})
        assert r.status_code == 200
        assert len(r.json()["sources"]) <= 20


# ---------------------------------------------------------------------------
# review
# ---------------------------------------------------------------------------

class TestReviewRoute:
    def test_review_text_diff(self, client):
        r = client.post("/api/assistant/review", json={"diff": DIFF})
        assert r.status_code == 200
        d = r.json()
        assert d["source"] == "text"
        assert d["summary"]["files"] == 1
        rules = {f["rule"] for f in d["findings"]}
        assert "secret" in rules
        assert "print_call" in rules
        assert d["recommendations"]

    def test_review_empty_diff_400(self, client):
        assert client.post("/api/assistant/review",
                           json={"diff": ""}).status_code == 400
        assert client.post("/api/assistant/review",
                           json={"diff": "мусор"}).status_code == 400

    def test_review_bad_types_400(self, client):
        assert client.post("/api/assistant/review",
                           json={"diff": 123}).status_code == 400
        assert client.post("/api/assistant/review",
                           json={"base": 5}).status_code == 400

    def test_review_no_changes_400(self, client):
        """В tmp-репозитории нет git — и нет diff → 400, а не пустой отчёт."""
        r = client.post("/api/assistant/review", json={})
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# tickets / support
# ---------------------------------------------------------------------------

class TestTicketRoutes:
    def test_list_tickets(self, client):
        r = client.get("/api/assistant/tickets")
        assert r.status_code == 200
        d = r.json()
        assert len(d["tickets"]) == 1
        assert d["stats"]["tickets"] == 1

    def test_list_filtered(self, client):
        assert client.get("/api/assistant/tickets",
                          params={"status": "open"}).json()["tickets"]
        assert not client.get("/api/assistant/tickets",
                              params={"status": "closed"}).json()["tickets"]

    def test_get_one_ticket(self, client):
        r = client.get("/api/assistant/tickets/T-1")
        assert r.status_code == 200
        assert r.json()["user"]["id"] == "u-1"

    def test_get_unknown_ticket_404(self, client):
        r = client.get("/api/assistant/tickets/T-404")
        assert r.status_code == 404

    def test_support_personalized(self, client):
        client.post("/api/assistant/index", json={})
        r = client.post("/api/assistant/support", json={"ticket_id": "T-1"})
        assert r.status_code == 200
        d = r.json()
        assert d["found"] is True
        assert d["context"]["user"]["plan"] == "pro"
        assert d["context"]["applied"]
        assert d["answer"]

    def test_support_requires_ticket_id(self, client):
        assert client.post("/api/assistant/support",
                           json={}).status_code == 400
        assert client.post("/api/assistant/support",
                           json={"ticket_id": " "}).status_code == 400

    def test_support_unknown_ticket_degrades(self, client):
        r = client.post("/api/assistant/support",
                        json={"ticket_id": "T-404",
                              "question": "как настроить?"})
        assert r.status_code == 200
        assert r.json()["found"] is False


# ---------------------------------------------------------------------------
# scenarios
# ---------------------------------------------------------------------------

class TestScenarioRoutes:
    def test_list_scenarios(self, client):
        r = client.get("/api/assistant/scenarios")
        assert r.status_code == 200
        names = [s["name"] for s in r.json()["scenarios"]]
        assert names == ["find_usages", "update_docs", "generate",
                         "check_invariants", "diff_report"]

    def test_run_find_usages(self, client):
        r = client.post("/api/assistant/scenario",
                        json={"name": "find_usages",
                              "params": {"symbol": "target_function"}})
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True
        assert d["result"]["total"] >= 1

    def test_run_check_invariants(self, client):
        r = client.post("/api/assistant/scenario",
                        json={"name": "check_invariants"})
        assert r.status_code == 200
        assert r.json()["scenario"] == "check_invariants"

    def test_run_generate_dry_run(self, client):
        r = client.post("/api/assistant/scenario",
                        json={"name": "generate",
                              "params": {"kind": "adr", "title": "Решение",
                                         "dry_run": True}})
        assert r.status_code == 200
        d = r.json()
        assert d["changed_files"] == []
        assert d["diff"]

    def test_run_generate_writes(self, client, repo):
        r = client.post("/api/assistant/scenario",
                        json={"name": "generate",
                              "params": {"kind": "adr", "title": "Решение"}})
        assert r.status_code == 200
        assert r.json()["changed_files"] == ["docs/adr/0001-reshenie.md"]
        assert (repo / "docs" / "adr" / "0001-reshenie.md").exists()

    def test_write_outside_project_400(self, client):
        for bad in ["/tmp/evil.md", "../evil.md", "studio/backend/x.py"]:
            r = client.post("/api/assistant/scenario",
                            json={"name": "generate",
                                  "params": {"kind": "readme", "path": bad}})
            assert r.status_code == 400, bad

    def test_unknown_scenario_400(self, client):
        r = client.post("/api/assistant/scenario", json={"name": "nope"})
        assert r.status_code == 400
        assert "Неизвестный сценарий" in r.json()["detail"]

    def test_missing_name_400(self, client):
        assert client.post("/api/assistant/scenario",
                           json={}).status_code == 400

    def test_bad_params_type_400(self, client):
        r = client.post("/api/assistant/scenario",
                        json={"name": "find_usages", "params": "x"})
        assert r.status_code == 400

    def test_update_docs_without_git_400(self, client):
        r = client.post("/api/assistant/scenario",
                        json={"name": "update_docs"})
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# git
# ---------------------------------------------------------------------------

class TestGitRoute:
    def test_git_context_no_repo(self, client):
        r = client.get("/api/assistant/git")
        assert r.status_code == 200
        d = r.json()
        assert d["available"] is False      # tmp-каталог не git-репозиторий
        assert d["diff"] == ""


# ---------------------------------------------------------------------------
# Изоляция: ассистент не ломает существующие роуты
# ---------------------------------------------------------------------------

class TestNoRegression:
    def test_existing_routes_still_work(self, client):
        """Ассистент не ломает ранее существовавшие роуты.

        Проверяются только офлайн-роуты: `/api/models` ходит в сеть за
        списком моделей и в офлайн-тестах висит на таймауте (проверено:
        прогон без него — 1 с вместо 65 с). Наличие роутов, требующих
        сети, покрыто их собственными тестами.
        """
        assert client.get("/api/tuning/profiles").status_code == 200
        assert client.get("/api/invariants").status_code == 200
        assert client.get("/api/mcp/servers").status_code == 200
