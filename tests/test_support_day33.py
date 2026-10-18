"""День 33: ассистент поддержки (RAG по FAQ/документации + CRM через MCP).

Офлайн-юниты и интеграционные сценарии:
  - данные CRM/KB и MCP-серверы (stdio, те же строки, что у дня 20);
  - гибридный поиск (BM25 полей + хэш-вектор, порог, «не знаю»);
  - MCP-мост (реестр инструментов, tool-loop вызовы, офлайн-фолбэк);
  - сессия: контекст тикета в промпте, источники/цитаты, next_action,
    честное «не знаю» без LLM-вызова;
  - CLI: офлайн-демо печатает MCP-контекст, источники и следующий шаг.

Модель фейковая (ScriptedLLM), сеть не нужна.
"""
import json
import os
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "tools", "mcp_servers"))


from tools import _support_store as support_store  # noqa: E402
from support.crm import CRMClient  # noqa: E402
from support.llm import (  # noqa: E402
    ChatClient,
    LLMError,
    key_for_model,
    load_env_file,
    normalize_tool_calls,
)
from support.retrieval import (  # noqa: E402
    SupportKB,
    extract_cited,
    format_sources,
)
from support.session import (  # noqa: E402
    NO_KB_ANSWER,
    SupportSession,
    build_kb_block,
    build_ticket_brief,
    derive_next_action,
    ticket_query,
)


# ---------- фикстуры ----------

SERVERS = os.path.join(REPO, "tools", "mcp_servers")


def call_server(server_file, tool_name, arguments):
    """tools/call через реальный subprocess (как тесты дня 20)."""
    data = (json.dumps({"jsonrpc": "2.0", "id": 0, "method": "initialize"})
            + "\n"
            + json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                          "params": {"name": tool_name,
                                     "arguments": arguments}},
                         ensure_ascii=False) + "\n")
    p = subprocess.run([sys.executable, os.path.join(SERVERS, server_file)],
                       input=data, capture_output=True, text=True,
                       encoding="utf-8", timeout=60, cwd=REPO,
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert p.returncode == 0, p.stderr
    lines = [json.loads(l) for l in p.stdout.splitlines() if l.strip()]
    return lines[-1]["result"]


@pytest.fixture()
def kb():
    return SupportKB(support_store.load_kb())


@pytest.fixture()
def crm_file(tmp_path, monkeypatch):
    """CRM во временном файле (не трогаем repo-данные) + env для серверов."""
    crm = {"users": {"U-9": {"id": "U-9", "name": "Тест Тестов",
                             "company": "Тест-Трейд", "plan": "Старт",
                             "account_status": "active", "history": [],
                             "notes": ""}},
           "tickets": {"TCK-9": {"id": "TCK-9", "user_id": "U-9",
                                 "subject": "Не приходит код", "status": "open",
                                 "priority": "high",
                                 "created": "2026-10-01T00:00:00Z",
                                 "updated": "2026-10-01T00:00:00Z",
                                 "channel": "chat",
                                 "messages": [{"ts": "2026-10-01T00:00:00Z",
                                               "author": "user",
                                               "text": "SMS с кодом не приходит"}],
                                 "facts": ["2FA по SMS включена"],
                                 "sync_status": {"2fa": "enabled"}}}}
    path = tmp_path / "crm.json"
    path.write_text(json.dumps(crm, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("SUPPORT_CRM_FILE", str(path))
    return str(path)


class ScriptedLLM:
    """Фейковая модель: очередь ответов + запись payload'ов."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, messages, tools=None, temperature=0.2, max_tokens=900):
        self.calls.append({"messages": messages, "tools": tools,
                           "max_tokens": max_tokens})
        if not self.replies:
            return {"content": "", "tool_calls": [], "usage": {}}
        reply = self.replies.pop(0)
        if isinstance(reply, str):
            reply = {"content": reply, "tool_calls": [], "usage": {}}
        return reply


class FakeCRM:
    """CRM без subprocess: тот же контракт, что у CRMClient."""

    backend = "mcp"

    def __init__(self, ticket, user):
        self.ticket = ticket
        self.user = user
        self.calls = []

    def get_ticket(self, ticket_id):
        self.calls.append(("get_ticket", ticket_id))
        if ticket_id != self.ticket["id"]:
            return {"error": "Тикет не найден: " + ticket_id}, True
        return self.ticket, False

    def get_user(self, user_id):
        self.calls.append(("get_user", user_id))
        if user_id != self.user["id"]:
            return {"error": "Пользователь не найден: " + user_id}, True
        return self.user, False

    @staticmethod
    def tool_definitions():
        return CRMClient.tool_definitions()

    def call_tool(self, name, arguments):
        if name == "get_ticket":
            return self.get_ticket(str(arguments.get("ticket_id") or ""))
        if name == "get_user":
            return self.get_user(str(arguments.get("user_id") or ""))
        return {"error": "Неизвестный инструмент: " + str(name)}, True


def fake_ticket():
    return {"id": "TCK-4101", "user_id": "U-101",
            "subject": "Не работает вход в систему",
            "status": "in_progress", "priority": "high",
            "created": "2026-10-16T09:12:00Z",
            "updated": "2026-10-17T18:40:00Z", "channel": "email",
            "messages": [{"ts": "2026-10-16T09:12:00Z", "author": "user",
                          "text": "Не приходит SMS с кодом второй день"}],
            "facts": ["2FA включена по SMS 2026-09-02",
                      "входы падают на втором шаге 2FA"],
            "sync_status": {"2fa": "enabled"}}


def fake_user():
    return {"id": "U-101", "name": "Анна Смирнова",
            "company": "Заповедник-онлайн", "plan": "Бизнес",
            "account_status": "active",
            "history": [{"date": "2026-09-02", "ticket": "TCK-4100",
                         "summary": "Включила 2FA по SMS"}],
            "notes": "Клиент ключевой"}


# ---------- данные ----------

def test_crm_seed_has_users_and_tickets():
    data = support_store.load_crm()
    assert "U-101" in data["users"]
    assert {"TCK-4101", "TCK-4200", "TCK-4300"} <= set(data["tickets"])
    ticket = data["tickets"]["TCK-4101"]
    assert ticket["user_id"] == "U-101"
    assert ticket["messages"] and ticket["facts"]


def test_kb_seed_articles_well_formed():
    articles = support_store.load_kb()
    assert len(articles) >= 8
    ids = [a["id"] for a in articles]
    assert len(ids) == len(set(ids))
    for a in articles:
        assert a["title"] and a["text"] and isinstance(a["tags"], list)


def test_store_corrupt_files_are_safe(tmp_path, monkeypatch):
    crm = tmp_path / "crm.json"
    crm.write_text("{битый", encoding="utf-8")
    kb = tmp_path / "kb.json"
    kb.write_text("[1, 2", encoding="utf-8")
    monkeypatch.setenv("SUPPORT_CRM_FILE", str(crm))
    monkeypatch.setenv("SUPPORT_KB_FILE", str(kb))
    assert support_store.load_crm() == {"users": {}, "tickets": {}}
    assert support_store.load_kb() == []


# ---------- MCP-серверы ----------

def test_mcp_get_ticket_by_id():
    result = call_server("crm_get_ticket.py", "get_ticket",
                         {"ticket_id": "TCK-4101"})
    payload = json.loads(result["content"][0]["text"])
    assert payload["id"] == "TCK-4101"
    assert payload["subject"] == "Не работает вход в систему"
    assert not result.get("isError")


def test_mcp_get_user_by_id():
    result = call_server("crm_get_user.py", "get_user", {"user_id": "U-101"})
    payload = json.loads(result["content"][0]["text"])
    assert payload["name"] == "Анна Смирнова"
    assert payload["plan"] == "Бизнес"


def test_mcp_missing_ticket_is_error():
    result = call_server("crm_get_ticket.py", "get_ticket",
                         {"ticket_id": "TCK-404"})
    assert result.get("isError") is True
    assert "не найден" in result["content"][0]["text"]


def test_mcp_missing_argument_is_error():
    result = call_server("crm_get_user.py", "get_user", {})
    assert result.get("isError") is True


def test_mcp_client_real_stdio_roundtrip():
    crm = CRMClient(timeout=30)
    try:
        assert crm.connect() is True
        assert crm.backend == "mcp"
        ticket, err = crm.get_ticket("TCK-4200")
        assert not err and ticket["subject"].startswith("Двойное списание")
        user, err = crm.get_user("U-102")
        assert not err and user["company"] == "Молот-Принт"
        missing, err = crm.get_ticket("TCK-404")
        assert err and "не найден" in missing["error"]
    finally:
        crm.close()


def test_crm_tool_definitions_match_dispatcher():
    names = [t["function"]["name"] for t in CRMClient.tool_definitions()]
    assert names == ["get_ticket", "get_user"]
    fake = FakeCRM(fake_ticket(), fake_user())
    payload, err = fake.call_tool("get_ticket", {"ticket_id": "TCK-4101"})
    assert not err and payload["id"] == "TCK-4101"
    payload, err = fake.call_tool("no_such", {})
    assert err and "Неизвестный" in payload["error"]


def test_crm_offline_fallback_reads_file(crm_file, monkeypatch):
    crm = CRMClient()
    # без connect() — офлайн-фолбэк из файла (как при сбое MCP)
    assert crm.connected is False
    ticket, err = crm.get_ticket("TCK-9")
    assert not err and ticket["user_id"] == "U-9"
    user, err = crm.get_user("U-9")
    assert not err and user["name"] == "Тест Тестов"


# ---------- retrieval ----------

def test_search_auth_question_finds_auth_articles(kb):
    res = kb.search("Почему не работает авторизация?", top_k=3, min_score=0.35)
    ids = [r["id"] for r in res["results"]]
    assert "FAQ-AUTH-01" in ids and "FAQ-AUTH-02" in ids
    assert res["results"][0]["norm_score"] == 1.0
    assert res["results"][0]["source"] == res["results"][0]["id"]


@pytest.mark.parametrize("query,expected", [
    ("почему не приходит код", "FAQ-AUTH-01"),
    ("двойное списание", "FAQ-BILL-01"),
    ("где взять акт", "FAQ-BILL-02"),
    ("401 api", "FAQ-AUTH-04"),
    ("как поменять тариф", "FAQ-BILL-03"),
    ("что умеет студия", "DOC-PROJECT-01"),
])
def test_search_top_hit_is_relevant(kb, query, expected):
    res = kb.search(query, top_k=3, min_score=0.35)
    assert res["results"][0]["id"] == expected


def test_search_out_of_scope_is_no_match(kb):
    for q in ("какой у вас офис в Москве", "Сколько стоит билет на Марс?"):
        res = kb.search(q, top_k=3, min_score=0.35)
        assert res["no_match"] is True and res["results"] == []


def test_search_generic_word_alone_does_not_match(kb):
    # «работает» — служебное слово поддержки: одно оно не включает совпадение
    res = kb.search("работает", top_k=3, min_score=0.35)
    assert res["no_match"] is True


def test_min_score_threshold_filters(kb):
    loose = kb.search("резервный код", top_k=5, min_score=0.0)
    strict = kb.search("резервный код", top_k=5, min_score=0.9)
    assert len(strict["results"]) <= len(loose["results"])
    assert all(r["norm_score"] >= 0.9 for r in strict["results"])


def test_format_sources_numbering(kb):
    res = kb.search("почему не приходит код", top_k=1, min_score=0.35)
    text = format_sources(res["results"])
    assert text.startswith("[1] FAQ-AUTH-01 — ")


def test_extract_cited_markers():
    results = [{"id": "A"}, {"id": "B"}, {"id": "C"}]
    cited, uncited = extract_cited(results, "см. [1] и [3]")
    assert [r["id"] for r in cited] == ["A", "C"]
    assert [r["id"] for r in uncited] == ["B"]
    cited, uncited = extract_cited(results, "без ссылок")
    assert cited == [] and len(uncited) == 3


# ---------- промпт/контекст ----------

def test_ticket_brief_contains_user_and_facts():
    brief = build_ticket_brief(fake_ticket(), fake_user())
    assert "TCK-4101" in brief
    assert "Анна Смирнова" in brief and "Бизнес" in brief
    assert "2FA включена по SMS" in brief
    assert "клиент: Не приходит SMS" in brief


def test_ticket_brief_without_user():
    brief = build_ticket_brief(fake_ticket(), None)
    assert "Клиент:" not in brief
    assert "TCK-4101" in brief


def test_kb_block_numbered_articles():
    results = [{"id": "X-1", "title": "Заголовок", "text": "Текст статьи"}]
    block = build_kb_block(results)
    assert "[1] X-1 — Заголовок" in block and "Текст статьи" in block
    assert build_kb_block([]) == ""


def test_ticket_query_includes_subject_and_facts():
    q = ticket_query(fake_ticket(), "Почему?")
    assert q.startswith("Почему?")
    assert "Не работает вход" in q and "2FA" in q


# ---------- next_action ----------

def test_next_action_no_sources_escalates():
    action = derive_next_action(fake_ticket(), [])
    assert "эскалировать" in action and "high" in action


def test_next_action_2fa_ticket():
    assert "резервный код" in derive_next_action(fake_ticket(), [{"id": "x"}])


def test_next_action_refund_ticket():
    ticket = {"priority": "medium", "status": "in_progress",
              "messages": [{"text": "двойное списание"}],
              "facts": [], "sync_status": {"refund": "processing"}}
    assert "возврат" in derive_next_action(ticket, [{"id": "x"}])


# ---------- сессия ----------

def test_session_open_ticket_uses_mcp_client(kb):
    crm = FakeCRM(fake_ticket(), fake_user())
    session = SupportSession(ScriptedLLM([]), crm, kb)
    ticket = session.open_ticket("TCK-4101")
    assert ticket["id"] == "TCK-4101"
    assert session.user["name"] == "Анна Смирнова"
    assert session.context_source == "mcp"
    assert crm.calls == [("get_ticket", "TCK-4101"), ("get_user", "U-101")]
    assert [t["tool"] for t in session.tool_trace] == ["get_ticket",
                                                       "get_user"]


def test_session_open_missing_ticket_raises(kb):
    session = SupportSession(ScriptedLLM([]), FakeCRM(fake_ticket(), fake_user()), kb)
    with pytest.raises(ValueError):
        session.open_ticket("TCK-404")


def test_session_ask_uses_ticket_context_and_citations(kb):
    reply = ("По [1] проблема на втором шаге 2FA: код не доставляется. "
             "Следующий шаг: выдать резервный код.")
    llm = ScriptedLLM([reply])
    session = SupportSession(llm, FakeCRM(fake_ticket(), fake_user()), kb)
    session.open_ticket("TCK-4101")
    rec = session.ask("Почему не работает авторизация?")
    assert rec["answer"] == reply
    assert rec["sources"], "источники обязательны"
    assert rec["cited"] == [rec["sources"][0]["id"]]  # ответ ссылается на [1]
    system = llm.calls[0]["messages"][0]["content"]
    assert "КОНТЕКСТ ТИКЕТА" in system and "TCK-4101" in system
    assert "База знаний" in system and "[1]" in system
    assert llm.calls[0]["tools"], "tools для tool-loop переданы"
    assert rec["tools"][0]["tool"] == "get_ticket"


def test_session_no_match_answers_without_llm(kb):
    llm = ScriptedLLM([])
    session = SupportSession(llm, FakeCRM(fake_ticket(), fake_user()), kb)
    session.open_ticket("TCK-4101")
    rec = session.ask("Сколько стоит билет на Марс?")
    assert rec["dont_know"] is True
    assert rec["answer"] == NO_KB_ANSWER
    assert rec["sources"] == []
    assert llm.calls == []  # без LLM-вызова (паттерн дня 24)
    assert "эскалировать" in rec["next_action"]


def test_session_tool_loop_model_requests_ticket(kb):
    """Модель сама вызывает get_ticket/get_user (tools в payload)."""
    crm = FakeCRM(fake_ticket(), fake_user())
    llm = ScriptedLLM([
        {"content": "", "usage": {}, "tool_calls": [
            {"id": "c1", "name": "get_ticket",
             "arguments": {"ticket_id": "TCK-4101"}},
            {"id": "c2", "name": "get_user",
             "arguments": {"user_id": "U-101"}},
        ]},
        {"content": "Ответ по контексту [1].", "tool_calls": [], "usage": {}},
    ])
    session = SupportSession(llm, crm, kb)  # тикет заранее НЕ открыт
    rec = session.ask("Почему не работает авторизация?")
    assert rec["answer"] == "Ответ по контексту [1]."
    assert crm.calls == [("get_ticket", "TCK-4101"), ("get_user", "U-101")]
    # второй раунд: assistant tool_calls + role=tool с JSON-ответом
    second = llm.calls[1]["messages"]
    roles = [m["role"] for m in second]
    assert "assistant" in roles and roles.count("tool") == 2
    tool_msgs = [m for m in second if m["role"] == "tool"]
    assert json.loads(tool_msgs[0]["content"])["id"] == "TCK-4101"
    assert tool_msgs[0]["tool_call_id"] == "c1"


def test_session_tool_loop_cap(kb):
    """Модель бесконечно просит инструменты — цикл ограничен финальным
    вызовом (без новых инструментов), ложных вызовов не набегает."""
    loop_reply = {"content": "", "usage": {}, "tool_calls": [
        {"id": "x", "name": "get_ticket",
         "arguments": {"ticket_id": "TCK-4101"}}]}
    final = {"content": "готово", "tool_calls": [], "usage": {}}
    llm = ScriptedLLM([dict(loop_reply) for _ in range(2)] + [final])
    session = SupportSession(llm, FakeCRM(fake_ticket(), fake_user()), kb,
                             max_tool_rounds=2)
    rec = session.ask("Почему не работает авторизация?")
    assert len(llm.calls) == 3  # 2 раунда + 1 финальный вызов
    assert llm.calls[-1]["tools"] is None  # финал — без инструментов
    assert rec["answer"] != ""


def test_session_tool_loop_cap_keeps_partial_text(kb):
    """Если финальная модель молчит, но текст был — он не теряется."""
    loop_reply = {"content": "черновик [1]", "usage": {}, "tool_calls": [
        {"id": "x", "name": "get_ticket",
         "arguments": {"ticket_id": "TCK-4101"}}]}
    llm = ScriptedLLM([dict(loop_reply)] + [{"content": "", "tool_calls": []}])
    session = SupportSession(llm, FakeCRM(fake_ticket(), fake_user()), kb,
                             max_tool_rounds=1)
    rec = session.ask("Почему не работает авторизация?")
    assert "черновик" in rec["answer"]


def test_session_retries_when_reasoning_eats_budget(kb):
    """Пустой ответ из-за reasoning (empty=True) -> повтор с большим лимитом."""
    llm = ScriptedLLM([
        {"content": "", "tool_calls": [], "usage": {}, "empty": True},
        {"content": "Ответ после повтора [1]", "tool_calls": [], "usage": {}},
    ])
    session = SupportSession(llm, FakeCRM(fake_ticket(), fake_user()), kb)
    session.open_ticket("TCK-4101")
    rec = session.ask("Почему не работает авторизация?")
    assert rec["answer"] == "Ответ после повтора [1]"
    assert len(llm.calls) == 2
    assert llm.calls[1]["max_tokens"] > llm.calls[0]["max_tokens"]


def test_session_double_empty_raises_llm_error(kb):
    llm = ScriptedLLM([
        {"content": "", "tool_calls": [], "usage": {}, "empty": True},
        {"content": "", "tool_calls": [], "usage": {}, "empty": True},
    ])
    session = SupportSession(llm, FakeCRM(fake_ticket(), fake_user()), kb)
    session.open_ticket("TCK-4101")
    with pytest.raises(LLMError):
        session.ask("Почему не работает авторизация?")


def test_session_history_is_passed_to_next_turn(kb):
    llm = ScriptedLLM(["первый ответ [1]", "второй ответ [1]"])
    session = SupportSession(llm, FakeCRM(fake_ticket(), fake_user()), kb)
    session.open_ticket("TCK-4101")
    session.ask("Почему не работает авторизация?")
    session.ask("А если SMS совсем не доставляется?")
    second = [m["role"] for m in llm.calls[1]["messages"]]
    assert second[:2] == ["system", "user"]
    assert second.count("assistant") == 1  # предыдущий ответ в истории


def test_session_stage_and_finalize_for_streaming(kb):
    session = SupportSession(ScriptedLLM([]), FakeCRM(fake_ticket(), fake_user()), kb)
    session.open_ticket("TCK-4101")
    stage = session.stage("Почему не работает авторизация?")
    assert not stage["no_match"] and stage["answer_ready"] is None
    messages = session.answer_messages("Почему не работает авторизация?",
                                       stage["results"])
    assert messages[-1] == {"role": "user",
                            "content": "Почему не работает авторизация?"}
    rec = session.finalize("Почему не работает авторизация?",
                           stage["results"], "ответ [1]")
    assert rec["cited"] == [stage["results"][0]["id"]]
    assert session.history[-1] is rec


# ---------- LLM-клиент ----------

def test_normalize_tool_calls_from_json_string():
    raw = [{"id": "c1", "function": {"name": "get_ticket",
                                     "arguments": '{"ticket_id": "TCK-1"}'}},
           {"function": {"name": "get_user", "arguments": "битый json"}},
           {"function": {}}]
    calls = normalize_tool_calls(raw)
    assert calls[0] == {"id": "c1", "name": "get_ticket",
                        "arguments": {"ticket_id": "TCK-1"}}
    assert calls[1]["arguments"] == {}
    assert len(calls) == 2


def test_key_for_model_selects_per_model_key(monkeypatch):
    env = {"GPUSTACK_KEY_DEEPSEEK": "ds", "GPUSTACK_KEY_GLM": "glm",
           "GPUSTACK_API_KEY": "common"}
    assert key_for_model("deepseek-v4-flash", env) == "ds"
    assert key_for_model("glm-5.3-flash", env) == "glm"
    assert key_for_model("gpt-4o", env) == "common"


def test_load_env_file(tmp_path):
    path = tmp_path / ".env"
    path.write_text("# x\nA=1\nB='two'\n\nC=\"3\"\nбитый\n", encoding="utf-8")
    assert load_env_file(str(path)) == {"A": "1", "B": "two", "C": "3"}


def test_client_chat_parses_tool_calls(monkeypatch):
    client = ChatClient(api_key="k")

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"choices": [{"message": {
                "content": None,
                "tool_calls": [{"id": "c1", "function": {
                    "name": "get_ticket",
                    "arguments": '{"ticket_id": "TCK-4101"}'}}]}}],
                "usage": {"total_tokens": 10}}).encode()

    monkeypatch.setattr(ChatClient, "_request", lambda self, payload: Resp())
    out = client.chat([{"role": "user", "content": "x"}],
                      tools=CRMClient.tool_definitions())
    assert out["tool_calls"][0]["name"] == "get_ticket"
    assert out["tool_calls"][0]["arguments"] == {"ticket_id": "TCK-4101"}


def test_client_stream_skips_garbage(monkeypatch):
    client = ChatClient(api_key="k")
    lines = ['data: {"choices":[{"delta":{"content":"При"}}]}'.encode("utf-8"),
             b'', b'data: not-json', b'data: [DONE]']

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def __iter__(self):
            return iter(lines)

    monkeypatch.setattr(ChatClient, "_request", lambda self, payload: Resp())
    assert client.stream_text([{"role": "user", "content": "x"}]) == "При"


def test_client_http_error_is_llm_error(monkeypatch):
    def boom(self, payload):
        raise LLMError("LLM вернула HTTP 401: нет доступа")

    monkeypatch.setattr(ChatClient, "_request", boom)
    with pytest.raises(LLMError):
        ChatClient(api_key="k").chat([{"role": "user", "content": "x"}])


# ---------- CLI ----------

def test_cli_offline_demo_end_to_end(tmp_path, capsys, monkeypatch):
    from support import cli
    # CRM-тикет с 2FA во временном файле; KB — repo-данные
    crm = {"users": {"U-1": {"id": "U-1", "name": "Анна", "company": "К",
                             "plan": "Бизнес", "account_status": "active",
                             "history": [], "notes": ""}},
           "tickets": {"TCK-4101": {
               "id": "TCK-4101", "user_id": "U-1",
               "subject": "Не работает вход в систему", "status": "in_progress",
               "priority": "high", "created": "-", "updated": "-",
               "channel": "email",
               "messages": [{"ts": "-", "author": "user",
                             "text": "SMS с кодом не приходит"}],
               "facts": ["2FA по SMS"],
               "sync_status": {"2fa": "enabled"}}}}
    path = tmp_path / "crm.json"
    path.write_text(json.dumps(crm, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("SUPPORT_CRM_FILE", str(path))

    rc = cli.main(["--offline", "--demo"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "crm_get_ticket, crm_get_user — подключены" in out
    assert "TCK-4101" in out and "Анна" in out
    assert "Почему не работает авторизация?" in out
    assert "Источники:" in out and "FAQ-AUTH-01" in out
    assert "Следующий шаг:" in out
    assert "совпадений в базе знаний нет" in out  # вопрос вне базы
