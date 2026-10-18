"""CLI ассистента поддержки (день 33): интерактивный REPL.

Запуск:
    python -m support
    python -m support --ticket TCK-4101
    python -m support --offline          # без LLM (только RAG+MCP, детерминизм)
    python -m support --demo             # скриптовый прогон для записи видео

Команды:
    /tickets            список тикетов
    /ticket TCK-4101    открыть тикет (контекст через MCP)
    /sources            источники последнего ответа
    /tools              журнал MCP-вызовов
    /help, /exit        помощь, выход

Ответ печатается по шагам: MCP-контекст -> найденные статьи (RAG) ->
стриминг ответа модели -> источники и следующий шаг оператора.
"""
from __future__ import annotations

import argparse
import os
import sys

try:
    sys.stdout.reconfigure(errors="replace", line_buffering=True)
except (AttributeError, OSError, ValueError):
    pass

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from support.crm import CRMClient  # noqa: E402
from support.llm import ChatClient, LLMError, key_for_model, load_env_file  # noqa: E402
from support.retrieval import SupportKB, format_sources  # noqa: E402
from support.session import SupportSession  # noqa: E402
from tools._support_store import load_crm, load_kb  # noqa: E402

DEMO_TICKET = "TCK-4101"
DEMO_QUESTIONS = [
    "Почему не работает авторизация?",
    "Что ответить клиенту? Какие шаги предложить?",
    "А если SMS совсем не доставляется?",
]
DEMO_OUT_OF_SCOPE = "Сколько стоит билет на Марс?"


def _out(text: str = "") -> None:
    """Печать в UTF-8, если консоль её умеет (иначе — нефатально)."""
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        print(text.encode(enc, "replace").decode(enc, "replace"), flush=True)


def _input(prompt: str) -> str:
    """Чтение строки без падения на cp1251 (кириллица из pipe/консоли)."""
    sys.stdout.write(prompt)
    sys.stdout.flush()
    line = sys.stdin.readline()
    if not line:
        raise EOFError
    enc = getattr(sys.stdin, "encoding", None)
    if enc and enc.lower() not in ("utf-8", "utf8"):
        try:
            line = line.encode(enc, "replace").decode("utf-8", "replace")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return line


# ---------- офлайн-режим (без LLM) ----------


class OfflineLLM:
    """Фейковая модель для --offline/e2e: честный детерминированный ответ.

    Не скрывает RAG: цитирует ВСЕ найденные источники [n] и пересказывает
    первый шаг из лучшей статьи — CLI/UI ведут себя так же, как с моделью."""

    def __init__(self) -> None:
        self.calls: list = []

    def chat(self, messages, tools=None, temperature=0.2, max_tokens=900):
        self.calls.append({"messages": messages, "tools": tools})
        system = messages[0]["content"] if messages else ""
        user = next((m["content"] for m in reversed(messages)
                     if m.get("role") == "user"), "")
        system_l = system.lower()
        if "в базе знаний нет" in system_l or "источники для ссылок" not in system_l:
            return {"content": "", "tool_calls": [], "usage": {}}
        # Номера источников и их заголовки из system-блока
        refs = []
        for line in system.splitlines():
            line = line.strip()
            if line.startswith("[") and "] " in line and " — " in line:
                refs.append(line.split("] ", 1)[1].split(" — ", 1)[0])
        markers = " ".join(f"[{i}]" for i in range(1, len(refs) + 1))
        first_step = ""
        in_article = False
        for part in system.split("\n"):
            stripped = part.strip()
            if stripped.startswith("[1] "):
                in_article = True
                continue
            if in_article and stripped.startswith("[") and "] " in stripped:
                in_article = False  # следующая статья
            if in_article and stripped[:2] in ("1.", "• "):
                first_step = stripped
                break
        answer = (
            f"По тикету и базе знаний: вопрос «{user}» разбирается статьёй "
            f"{refs[0] if refs else 'базы'} {markers}. "
            + (f"Первый шаг: {first_step} " if first_step else "")
            + "Следующий шаг: выполнить шаги статьи по порядку, при отсутствии "
              "эффекта — эскалация.")
        return {"content": answer, "tool_calls": [], "usage": {}}


# ---------- рендер хода ----------


def render_context(session: SupportSession) -> None:
    t = session.ticket
    if not t:
        _out("  (тикет не выбран — ответ по базе знаний)")
        return
    u = session.user or {}
    src = ("MCP" if session.context_source == "mcp"
           else "JSON (MCP недоступен)")
    _out(f"  [контекст: {src}] {t.get('id')} · {t.get('subject')} · "
         f"{t.get('status')}/{t.get('priority')}")
    if u:
        _out(f"  клиент: {u.get('name')} · {u.get('company')} · "
             f"тариф {u.get('plan')}")


def render_sources(tools: list) -> None:
    calls = [t for t in tools if t.get("tool")]
    if not calls:
        return
    for c in calls:
        args = ", ".join(f"{k}={v}" for k, v in (c.get("arguments") or {}).items())
        _out(f"  [MCP] {c['tool']}({args}) -> "
             f"{'ok' if c.get('ok') else 'ошибка'}")


def _md_lite(text: str) -> str:
    """Мини-markdown для консоли: **жирный** -> жирный, `код` -> код."""
    return (text or "").replace("**", "").replace("`", "")


def run_turn(session: SupportSession, question: str, llm, offline: bool,
             show_tools: bool = True) -> dict:
    """Один ход: stage -> LLM (стрим/офлайн) -> finalize -> рендер."""
    session.tool_trace = []  # журнал MCP-вызовов — только этого хода
    stage = session.stage(question)
    if stage["no_match"]:
        rec = session.finalize(question, [], stage["answer_ready"])
        rec["dont_know"] = True
        _out("  [RAG] совпадений в базе знаний нет — честное «не знаю» без LLM")
        if session.ticket:
            _out("  [MCP] тикет уже загружен, дополнительных вызовов не нужно")
        _out()
        _out("Ответ:")
        _out(_md_lite(rec["answer"]))
        _out()
        _out("Следующий шаг: " + rec["next_action"])
        return rec

    _out(f"  [RAG] запрос: {stage['query'][:110]}")
    for i, r in enumerate(stage["results"], 1):
        _out(f"  [RAG] [{i}] {r['id']} — {r['title']} "
             f"(score {r['norm_score']:.2f})")

    messages = session.answer_messages(question, stage["results"])
    _out()
    _out("Ответ:")
    answer_parts: list[str] = []
    if offline:
        resp = llm.chat(messages)
        chunk = resp.get("content") or ""
        answer_parts.append(chunk)
        _out(_md_lite(chunk))
    else:
        try:
            for delta in llm.stream(messages):
                answer_parts.append(delta)
                sys.stdout.write(_md_lite(delta))
                sys.stdout.flush()
        except LLMError as exc:
            _out()
            _out(f"  ! LLM ошибка: {exc}")
            _out("  (офлайн-фолбэк: RAG-источники и следующий шаг ниже)")
        if not "".join(answer_parts).strip():
            # reasoning съел лимит / стрим пустой: один не-стримовый повтор
            try:
                rest = llm.chat(messages, max_tokens=2800).get("content")
            except LLMError as exc:
                _out(f"  ! повтор не удался: {exc}")
                rest = None
            if rest:
                answer_parts = [rest]
                _out(_md_lite(rest))
    _out()
    rec = session.finalize(question, stage["results"],
                           "".join(answer_parts))
    _out()
    _out("Источники:")
    for line in format_sources(stage["results"]).splitlines():
        _out("  " + line)
    if rec["cited"]:
        _out("  процитированы: " + ", ".join(rec["cited"]))
    if rec["uncited"]:
        _out("  не использованы: " + ", ".join(rec["uncited"]))
    _out("Следующий шаг: " + rec["next_action"])
    if show_tools:
        render_sources(rec["tools"])
    return rec


# ---------- команды ----------


def cmd_tickets(session: SupportSession) -> None:
    tickets = load_crm()["tickets"]
    src = "MCP" if session.context_source == "mcp" else "JSON"
    _out(f"Тикеты ({src}):")
    for tid, t in sorted(tickets.items()):
        mark = "*" if tid == session.ticket_id else " "
        _out(f" {mark} {tid}  {t.get('status'):<11} {t.get('priority'):<8} "
             f"{t.get('subject')}")


def cmd_ticket(session: SupportSession, arg: str) -> None:
    if not arg:
        _out("использование: /ticket TCK-4101   (список — /tickets)")
        return
    try:
        session.open_ticket(arg.strip())
    except ValueError as exc:
        _out(f"✗ {exc}")
        return
    _out("Тикет открыт:")
    render_context(session)
    render_sources(session.tool_trace)


def cmd_sources(session: SupportSession) -> None:
    if not session.history:
        _out("ответов ещё не было")
        return
    rec = session.history[-1]
    if not rec["sources"]:
        _out("источников нет: база знаний не дала совпадений")
        return
    for s in rec["sources"]:
        _out(f"  [{s['rank']}] {s['id']} — {s['title']}  "
             f"(score {s['score']:.2f})")


def cmd_tools(session: SupportSession) -> None:
    trace = session.history[-1]["tools"] if session.history else session.tool_trace
    if not trace:
        _out("MCP-вызовов не было")
        return
    for c in trace:
        args = ", ".join(f"{k}={v}" for k, v in (c.get("arguments") or {}).items())
        _out(f"  {c['tool']}({args}) -> {'ok' if c.get('ok') else 'ошибка'}")


def _help() -> None:
    _out("/tickets            список тикетов\n"
         "/ticket TCK-4101    открыть тикет через MCP\n"
         "/sources            источники последнего ответа\n"
         "/tools              журнал MCP-вызовов\n"
         "/help /exit         помощь / выход")


# ---------- CLI ----------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="support",
                                description="Ассистент поддержки (день 33)")
    p.add_argument("--ticket", default=None, help="открыть тикет при старте")
    p.add_argument("--model", default=None, help="модель LLM (дефолт из env)")
    p.add_argument("--base-url", default=None, help="OpenAI-совместимый base URL")
    p.add_argument("--offline", action="store_true",
                   help="без LLM: RAG + MCP, детерминированные ответы")
    p.add_argument("--demo", action="store_true",
                   help="скриптовый прогон (запись демо-видео)")
    return p


def make_components(args, env: dict):
    """(llm, crm, kb) по аргументам CLI и .env."""
    crm = CRMClient()
    crm.connect()
    kb = SupportKB(load_kb())
    if args.offline:
        return OfflineLLM(), crm, kb
    model = args.model or env.get("SUPPORT_MODEL") or "deepseek-v4-flash"
    llm = ChatClient(base_url=args.base_url or env.get("GPUSTACK_BASE_URL"),
                     model=model,
                     api_key=env.get("SUPPORT_API_KEY")
                     or key_for_model(model, env)
                     or os.environ.get("SUPPORT_API_KEY", ""))
    return llm, crm, kb


def run_demo(session: SupportSession, llm, offline: bool) -> int:
    """Скриптовый прогон для демо-видео: тикеты -> тикет -> 3 вопроса + 1 вне базы."""
    _out("=== ДЕМО: ассистент поддержки (день 33) ===")
    cmd_tickets(session)
    _out()
    _out("> /ticket " + DEMO_TICKET)
    cmd_ticket(session, DEMO_TICKET)
    for q in DEMO_QUESTIONS + [DEMO_OUT_OF_SCOPE]:
        _out()
        _out(f"› {q}")
        run_turn(session, q, llm, offline)
    _out()
    _out("=== ДЕМО завершено ===")
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(errors="replace")
            except (OSError, ValueError):
                pass
    args = build_parser().parse_args(argv)
    env = load_env_file(os.path.join(REPO_ROOT, ".env"))
    for key, value in env.items():
        os.environ.setdefault(key, value)

    llm, crm, kb = make_components(args, env)
    session = SupportSession(llm, crm, kb)

    _out("Ассистент поддержки · RAG (FAQ/документация) + MCP (CRM)")
    _out(f"LLM: {'offline (без модели)' if args.offline else llm.model} · "
         f"CRM: {'MCP' if crm.connected else 'JSON (MCP недоступен: '
         + str(crm.error) + ')'}")
    if crm.connected:
        _out("MCP: crm_get_ticket, crm_get_user — подключены")
    else:
        _out("  ! MCP-серверы не поднялись — работает офлайн-фолбэк на JSON")

    try:
        if args.demo:
            return run_demo(session, llm, args.offline)
        if args.ticket:
            cmd_ticket(session, args.ticket)
        else:
            _out("Тикет не выбран (выбрать: /ticket TCK-4101 или /tickets).")
        _out("Команды: /tickets /ticket <id> /sources /tools /help /exit")
        while True:
            try:
                line = _input("\n› ").strip()
            except (EOFError, KeyboardInterrupt):
                _out()
                break
            if not line:
                continue
            if line.startswith("/"):
                parts = line.split(maxsplit=1)
                cmd = parts[0].lower()
                arg = parts[1].strip() if len(parts) > 1 else ""
                if cmd in ("/exit", "/quit", "/q"):
                    break
                if cmd == "/help":
                    _help()
                elif cmd == "/tickets":
                    cmd_tickets(session)
                elif cmd == "/ticket":
                    cmd_ticket(session, arg)
                elif cmd == "/sources":
                    cmd_sources(session)
                elif cmd == "/tools":
                    cmd_tools(session)
                else:
                    _out(f"неизвестная команда: {cmd} (см. /help)")
                continue
            if not session.ticket:
                _out("  (тикет не выбран: ответ будет по общей базе знаний)")
            run_turn(session, line, llm, args.offline)
    finally:
        crm.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
