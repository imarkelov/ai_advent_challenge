"""E2E дня 33: ассистент поддержки (RAG по FAQ/документации + CRM через MCP).

Part A — офлайн-детерминированное ядро (MUST PASS, без сети и LLM):
  A1  MCP: оба сервера отвечают по stdio (tools/list + tools/call);
  A2  MCP-мост: тикет и пользователь подтягиваются инструментами;
  A3  RAG: вопросы по FAQ/документации находят ожидаемые статьи;
  A4  «не знаю»: вопрос вне базы -> no_match без LLM-вызова;
  A5  Ответ с контекстом тикета: system-промпт содержит тикет/клиента/факты,
      источники и цитаты [n], следующий шаг оператора;
  A6  CLI --offline: печать MCP-контекста, источников, «не знаю».
Part B — live (real LLM + real MCP), best-effort (нет ключа/сети — SKIP):
  B1  «Почему не работает авторизация?» на тикете TCK-4101 — ответ
      ссылается на данные тикета и базу знаний;
  B2  второй тикет (двойное списание) — другой контекст, другие источники;
  B3  честный отказ по вопросу вне базы (без выдумки);
  B4  CLI стримящийся ход (реальный LLM через CLI).
Транскрипт live-прогона: .omo/evidence/day33/transcript.json.
Exit 0 для PASS/SKIP, 1 для FAIL.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "tests"))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(errors="replace")
        except (OSError, ValueError):
            pass

from support.crm import CRMClient  # noqa: E402
from support.llm import ChatClient, key_for_model, load_env_file  # noqa: E402
from support.retrieval import SupportKB  # noqa: E402
from support.session import SupportSession  # noqa: E402
from tools._support_store import load_kb  # noqa: E402

EVIDENCE_DIR = os.path.join(REPO, ".omo", "evidence", "day33")
PASS = 0
FAIL = 0
SKIP = 0
WARN = 0


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


def warn(name: str, detail: str = "") -> None:
    global WARN
    WARN += 1
    print(f"  WARN {name} {detail}")


# ---------- Part A: офлайн ----------

RAG_CASES = [
    ("Почему не работает авторизация?", {"FAQ-AUTH-01", "FAQ-AUTH-02"}),
    ("не приходит смс с кодом", {"FAQ-AUTH-01"}),
    ("двойное списание", {"FAQ-BILL-01"}),
    ("где скачать счёт", {"FAQ-BILL-02"}),
    ("401 unauthorized api", {"FAQ-AUTH-04"}),
    ("как поменять тариф", {"FAQ-BILL-03"}),
    ("что умеет студия", {"DOC-PROJECT-01"}),
]


class ScriptedLLM:
    """Фейковая LLM для A5: детерминированный ответ с цитатой [1]."""

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, tools=None, temperature=0.2, max_tokens=900):
        self.calls += 1
        return {"content": "Ответ по [1]: выполнить шаги статьи и эскалировать "
                           "при отсутствии эффекта.",
                "tool_calls": [], "usage": {}}


def mcp_call(server: str, tool: str, args: dict) -> dict:
    """tools/call по stdio (реальный subprocess, JSON-RPC)."""
    data = (json.dumps({"jsonrpc": "2.0", "id": 0, "method": "initialize"})
            + "\n"
            + json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                          "params": {"name": tool, "arguments": args}},
                         ensure_ascii=False) + "\n")
    proc = subprocess.run(
        [sys.executable, os.path.join(REPO, "tools", "mcp_servers",
                                      server + ".py")],
        input=data, capture_output=True, text=True, encoding="utf-8",
        timeout=60, cwd=REPO,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert proc.returncode == 0, proc.stderr
    lines = [json.loads(l) for l in proc.stdout.splitlines() if l.strip()]
    return lines[-1]["result"]


def part_a() -> None:
    print("== Part A: офлайн-ядро (MUST PASS) ==")

    # A1: MCP-серверы
    result = mcp_call("crm_get_ticket", "get_ticket", {"ticket_id": "TCK-4101"})
    ticket = json.loads(result["content"][0]["text"])
    check("A1a get_ticket через MCP", ticket.get("id") == "TCK-4101"
          and ticket.get("user_id") == "U-101")
    result = mcp_call("crm_get_user", "get_user", {"user_id": "U-101"})
    user = json.loads(result["content"][0]["text"])
    check("A1b get_user через MCP", user.get("name") == "Анна Смирнова"
          and user.get("plan") == "Бизнес")
    missing = mcp_call("crm_get_ticket", "get_ticket", {"ticket_id": "TCK-404"})
    check("A1c неизвестный тикет — isError", missing.get("isError") is True)

    # A2: MCP-мост клиента
    crm = CRMClient()
    try:
        connected = crm.connect()
        check("A2a MCP-мост подключён", connected and crm.backend == "mcp")
        t2, err = crm.get_ticket("TCK-4200")
        check("A2b тикет загружен мостом", not err and
              t2["subject"].startswith("Двойное списание"))
        u2, err = crm.get_user("U-102")
        check("A2c пользователь загружен мостом", not err and
              u2["company"] == "Молот-Принт")

        # A3: RAG
        kb = SupportKB(load_kb())
        for query, expected in RAG_CASES:
            res = kb.search(query, top_k=3, min_score=0.35)
            got = {r["id"] for r in res["results"]}
            check(f"A3 {query!r}", bool(got & expected),
                  f"ожидалось {expected}, получено {got}")
        no_match = kb.search("Сколько стоит билет на Марс?", top_k=3,
                             min_score=0.35)
        check("A3f вопрос вне базы — no_match", no_match["no_match"] is True)

        # A4/A5: сессия
        llm = ScriptedLLM()
        session = SupportSession(llm, crm, kb)
        session.open_ticket("TCK-4101")
        rec = session.ask("Сколько стоит билет на Марс?")
        check("A4 «не знаю» без LLM-вызова", rec["dont_know"] is True
              and llm.calls == 0)
        rec = session.ask("Почему не работает авторизация?")
        system = ""
        # system-промпт последнего вызова недоступен у ScriptedLLM — соберём
        # его напрямую из сессии (та же функция, что шлётся в LLM).
        rag = session._retrieve("Почему не работает авторизация?")
        prompt = (session._system_prompt()
                  + __import__("support.session", fromlist=["x"])
                  .build_kb_block(rag["results"]))
        check("A5a тикет в промпте", "TCK-4101" in prompt
              and "Анна Смирнова" in prompt and "2FA" in prompt)
        check("A5b база знаний в промпте", "[1] FAQ-AUTH" in prompt)
        check("A5c источники в ответе", len(rec["sources"]) >= 1 and
              rec["sources"][0]["id"].startswith("FAQ-AUTH"))
        check("A5d цитаты распознаны", rec["cited"] and
              rec["cited"][0] == rec["sources"][0]["id"])
        check("A5e следующий шаг оператора", "резервный код" in
              rec["next_action"])
        check("A5f MCP-вызовы в журнале", [t["tool"] for t in rec["tools"]][:2]
              == ["get_ticket", "get_user"])
    finally:
        crm.close()

    # A6: CLI --offline
    proc = subprocess.run([sys.executable, "-X", "utf8", "-m", "support",
                           "--offline", "--demo"],
                          capture_output=True, text=True, encoding="utf-8",
                          timeout=300, cwd=REPO,
                          env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    out = proc.stdout
    check("A6a CLI --offline rc=0", proc.returncode == 0, proc.stderr[:300])
    check("A6b CLI: MCP-контекст", "crm_get_ticket, crm_get_user — подключены"
          in out and "[MCP] get_ticket(ticket_id=TCK-4101) -> ok" in out)
    check("A6c CLI: RAG-источники", "[RAG] [1] FAQ-AUTH-01" in out)
    check("A6d CLI: «не знаю»", "совпадений в базе знаний нет" in out)
    check("A6e CLI: следующий шаг", "Следующий шаг:" in out)


# ---------- Part B: live ----------

def _supported_model(env: dict, llm: ChatClient) -> tuple[bool, str]:
    """Проверка реального вызова модели (короткий пробный запрос)."""
    try:
        resp = llm.chat([{"role": "user", "content": "Скажи ровно: ok"}],
                        max_tokens=200)
    except Exception as exc:  # LLMError / сеть
        return False, str(exc)
    return bool((resp.get("content") or "").strip()), "пустой ответ"


def part_b() -> None:
    print("== Part B: live (real LLM + MCP, best-effort) ==")
    env = load_env_file(os.path.join(REPO, ".env"))
    model = env.get("SUPPORT_MODEL") or "deepseek-v4-flash"
    key = (env.get("SUPPORT_API_KEY") or key_for_model(model, env)
           or os.environ.get("SUPPORT_API_KEY", ""))
    base = env.get("GPUSTACK_BASE_URL") or os.environ.get("GPUSTACK_BASE_URL")
    if not key or not base:
        skip("B*", "нет ключа/base URL (GPUSTACK_*) — live пропущен")
        return
    llm = ChatClient(base_url=base, model=model, api_key=key, timeout=240)
    ok, why = _supported_model(env, llm)
    if not ok:
        skip("B*", f"модель {model} недоступна: {why}")
        return
    print(f"  модель {model}, {base}")

    crm = CRMClient()
    transcript = {"day": 33, "model": model, "base_url": base,
                  "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                  "turns": []}
    try:
        if not crm.connect():
            skip("B*", f"MCP не подключился: {crm.error}")
            return
        kb = SupportKB(load_kb())

        # B1: главный сценарий задания
        s = SupportSession(llm, crm, kb)
        s.open_ticket("TCK-4101")
        t0 = time.time()
        r = s.ask("Почему не работает авторизация?")
        answer = (r["answer"] or "").lower()
        transcript["turns"].append({"name": "B1", "ticket": "TCK-4101",
                                    "question": r["question"],
                                    "answer": r["answer"],
                                    "sources": r["sources"],
                                    "cited": r["cited"],
                                    "tools": r["tools"],
                                    "next_action": r["next_action"],
                                    "seconds": round(time.time() - t0, 1)})
        print(f"  B1 {time.time() - t0:.1f}s: {r['answer'][:120]!r}")
        check("B1a ответ непуст", bool(r["answer"].strip()))
        check("B1b источники RAG", any(x["id"].startswith("FAQ-AUTH")
                                       for x in r["sources"]))
        check("B1c учтён контекст тикета",
              any(w in answer for w in ("sms", "код", "2fa", "второй фактор")),
              "нет отсылки к 2FA/SMS из тикета")
        check("B1d цитаты [n]", bool(r["cited"]),
              "модель не сослалась на источники (поведение модели)")
        check("B1e журнал MCP", [t["tool"] for t in r["tools"]][:2]
              == ["get_ticket", "get_user"])

        # B2: другой тикет — другой контекст
        s2 = SupportSession(llm, crm, kb)
        s2.open_ticket("TCK-4200")
        r2 = s2.ask("Клиент просит вернуть лишнюю оплату — что делать?")
        answer2 = (r2["answer"] or "").lower()
        transcript["turns"].append({"name": "B2", "ticket": "TCK-4200",
                                    "question": r2["question"],
                                    "answer": r2["answer"],
                                    "sources": r2["sources"],
                                    "cited": r2["cited"]})
        print(f"  B2: {r2['answer'][:120]!r}")
        check("B2a ответ непуст", bool(r2["answer"].strip()))
        check("B2b другой тикет в контексте", "двойное списание" in
              f"{r2['answer']} {r2['next_action']}".lower()
              or "возврат" in answer2)
        check("B2c источники про оплату", any(
            x["id"].startswith("FAQ-BILL") for x in r2["sources"]))

        # B3: честный отказ вне базы
        r3 = s2.ask("Сколько стоит билет на Марс?")
        transcript["turns"].append({"name": "B3", "question": r3["question"],
                                    "answer": r3["answer"],
                                    "dont_know": r3["dont_know"]})
        print(f"  B3: {r3['answer'][:120]!r}")
        check("B3 честный «не знаю» вне базы", r3["dont_know"] is True)

        # B4: CLI со стримингом (реальная модель)
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", "-m", "support",
             "--ticket", "TCK-4300"],
            input="Почему API отвечает 401?\n/exit\n",
            capture_output=True, text=True, encoding="utf-8", timeout=300,
            cwd=REPO, env={**os.environ, "PYTHONIOENCODING": "utf-8",
                           "SUPPORT_MODEL": model})
        out = proc.stdout
        transcript["cli"] = {"rc": proc.returncode, "stdout": out[-3000:]}
        check("B4a CLI rc=0", proc.returncode == 0, proc.stderr[:300])
        check("B4b CLI: MCP-контекст", "TCK-4300" in out)
        check("B4c CLI: ответ с источниками", "Источники:" in out
              and "FAQ-AUTH-04" in out)
    finally:
        crm.close()
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
    print(f"\nИТОГ: PASS={PASS} FAIL={FAIL} SKIP={SKIP} WARN={WARN}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
