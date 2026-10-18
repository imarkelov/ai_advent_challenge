"""Сессия ассистента поддержки (день 33): тикет + RAG + ответ с источниками.

Один ход:
  1. Контекст. Тикет и пользователь подтягиваются через MCP (`support.crm`):
     при выборе тикета CLI сам вызывает `get_ticket`/`get_user`, а если не
     передано — модель может вызвать их в tool-loop (tools в payload).
  2. RAG. Запрос = вопрос оператора + тема/переписка/факты тикета (тикет
     помогает найти нужные статьи FAQ без лишних уточнений).
  3. Ответ. System-промпт: роль поддержки, данные тикета, выдержки из базы
     со ссылками [n]; правила: не выдумывать, честно «в базе нет», шаги по
     тикету и следующий шаг оператора.
  4. Источники. Всегда возвращаются найденные статьи (id/заголовок/счёт),
     отдельно — процитированные моделью ([n]) и неиспользованные.
  5. Следующий шаг (next_action) детерминированно выводится из семантики
     тикета (эскалация/обещание SLA/нет ответа) — оператору есть что нажать.

Модель инжектируется (ChatClient или fake в тестах) — тесты офлайн.
"""
from __future__ import annotations

import json

from support.llm import LLMError
from support.retrieval import (
    SupportKB,
    _is_generic as _generic,
    _tokenize as question_terms,
    extract_cited,
)

NO_KB_ANSWER = (
    "В базе знаний поддержки нет статьи, которая отвечает на этот вопрос, "
    "поэтому я не буду догадываться. Передаю обращение дежурному инженеру "
    "поддержки с ссылкой на тикет; уточните, пожалуйста, что именно вы "
    "сделали перед ошибкой."
)

DONT_KNOW_TEXT = NO_KB_ANSWER

SYSTEM_PROMPT = """Ты — ассистент службы поддержки продукта «Студия».
Ты помогаешь оператору ответить клиенту по конкретному тикету.

Правила:
1. Отвечай на русском, кратко и по делу (5–8 предложений, нумерованные шаги).
2. Опирайся на данные тикета и на выдержки из базы знаний ниже. Если в базе
   знаний нет ответа — так и скажи и предложи эскалацию, не выдумывай.
3. Ссылайся на выдержки маркерами [1], [2], … (номера — как в списке
   «Источники»). Не ссылайся на номера, которых нет в списке.
4. Учитывай контекст: кто клиент, тариф, что уже пробовали, что уже ответили.
5. В конце дай оператору «Следующий шаг: …» — одно конкретное действие
   (что проверить, кому эскалировать, какой срок обещать).
"""


def build_ticket_brief(ticket: dict, user: dict | None) -> str:
    """Текстовый блок о тикете для system-промпта."""
    lines = [f"Тикет: {ticket.get('id')} — {ticket.get('subject')}",
             f"Статус: {ticket.get('status')} · приоритет: {ticket.get('priority')}",
             f"Создан: {ticket.get('created')} · обновлён: {ticket.get('updated')}",
             f"Канал: {ticket.get('channel')}"]
    if user:
        lines.append(
            f"Клиент: {user.get('name')} ({user.get('id')}), "
            f"{user.get('company')}, тариф {user.get('plan')}, "
            f"статус аккаунта {user.get('account_status')}")
        history = user.get("history") or []
        if history:
            lines.append("История обращений клиента: " + "; ".join(
                f"{h.get('date')}: {h.get('summary')}" for h in history[:4]))
        if user.get("notes"):
            lines.append("Заметка поддержки: " + str(user["notes"]))
    facts = ticket.get("facts") or []
    if facts:
        lines.append("Факты тикета: " + "; ".join(str(f) for f in facts))
    if ticket.get("sync_status"):
        lines.append("Состояние аккаунта: " + json.dumps(
            ticket["sync_status"], ensure_ascii=False))
    messages = ticket.get("messages") or []
    if messages:
        lines.append("Переписка:")
        for m in messages[-6:]:
            who = "клиент" if m.get("author") == "user" else "поддержка"
            lines.append(f"  [{m.get('ts')}] {who}: {m.get('text')}")
    return "\n".join(lines)


def build_kb_block(results: list) -> str:
    """Выдержки из базы знаний со ссылками [n] (пусто — блока нет)."""
    if not results:
        return ""
    parts = ["", "База знаний (источники для ссылок [n]):"]
    for i, r in enumerate(results, 1):
        parts.append(f"[{i}] {r['id']} — {r['title']}\n{r['text']}")
    return "\n".join(parts)


def ticket_query(ticket: dict, question: str) -> str:
    """Поисковый запрос = вопрос + тема/факты тикета (RAG с контекстом)."""
    bits = [question, str(ticket.get("subject") or "")]
    bits.extend(str(f) for f in (ticket.get("facts") or [])[:4])
    messages = ticket.get("messages") or []
    if messages:
        bits.append(str(messages[-1].get("text") or ""))
    return " ".join(b for b in bits if b).strip()


def derive_next_action(ticket: dict, results: list) -> str:
    """Следующий шаг оператора из семантики тикета (детерминированно)."""
    priority = str(ticket.get("priority") or "").lower()
    status = str(ticket.get("status") or "").lower()
    sync = ticket.get("sync_status") or {}
    facts = " ".join(str(f) for f in ticket.get("facts") or []).lower()
    text = " ".join(str(m.get("text") or "") for m in ticket.get("messages") or []).lower()

    if not results:
        return ("эскалировать в инженерную поддержку: в базе знаний нет ответа, "
                "передать тикет дежурному инженеру с пометкой " + (priority or "normal"))
    if "2fa" in sync or "2fa" in facts or "код" in text or "смс" in text:
        return ("проверить доставку SMS через провайдера и выдать резервный код "
                "2FA (10 минут); если не поможет — сброс 2FA администратором "
                "аккаунта, эскалация с приоритетом high")
    if "refund" in sync or "возврат" in text or "списан" in text:
        return ("проверить заявку на возврат: если срок 3–5 рабочих дней ещё не "
                "истёк — сообщить клиенту номер заявки и ожидаемую дату")
    if "allowlist" in facts or "401" in text:
        return ("добавить IP раннеров CI в allowlist сервисного ключа или выдать "
                "ключ продуктивного контура с нужным scope, проверить запрос")
    if status in ("resolved", "closed"):
        return "закрыть обращение и предложить оценку качества ответа"
    if priority in ("high", "critical"):
        return "ответить в SLA-срок и при отсутствии решения эскалировать тимлиду"
    return "уточнить у клиента шаги воспроизведения и вернуться с решением"


class SupportSession:
    """Состояние одного обращения + ход «вопрос -> ответ по тикету»."""

    def __init__(self, llm, crm, kb: SupportKB, ticket_id: str | None = None,
                 max_tool_rounds: int = 4) -> None:
        self.llm = llm
        self.crm = crm
        self.kb = kb
        self.ticket_id = ticket_id
        self.ticket: dict | None = None
        self.user: dict | None = None
        self.context_source: str = "none"
        self.history: list[dict] = []
        self.max_tool_rounds = max_tool_rounds
        self.tool_trace: list[dict] = []

    # --- контекст ---

    def open_ticket(self, ticket_id: str) -> dict:
        """Загрузить тикет + пользователя через MCP. Возврат — тикет."""
        self.ticket_id = ticket_id
        ticket, err = self.crm.get_ticket(ticket_id)
        if err:
            self.ticket = None
            self.user = None
            self.context_source = "none"
            raise ValueError(str(ticket.get("error")
                                 if isinstance(ticket, dict) else ticket))
        self.ticket = ticket
        self.context_source = getattr(self.crm, "backend", "mcp")
        user_id = self.ticket.get("user_id")
        self.user = None
        if user_id:
            user, uerr = self.crm.get_user(str(user_id))
            if not uerr:
                self.user = user
        self.tool_trace.append({
            "tool": "get_ticket", "arguments": {"ticket_id": ticket_id},
            "ok": True, "source": self.context_source})
        if self.user:
            self.tool_trace.append({
                "tool": "get_user", "arguments": {"user_id": user_id},
                "ok": True, "source": self.context_source})
        return self.ticket

    # --- ход ---

    def _system_prompt(self) -> str:
        parts = [SYSTEM_PROMPT]
        if self.ticket:
            parts.append("КОНТЕКСТ ТИКЕТА:\n" + build_ticket_brief(
                self.ticket, self.user))
        else:
            parts.append("Тикет не выбран: ответь общими статьями базы знаний "
                         "и попроси оператора указать тикет.")
        return "\n\n".join(parts)

    def _retrieve(self, question: str) -> dict:
        """RAG: вопрос с небольшим весом контекста тикета (0.3) — тема и
        факты обращения помогают домыслить недосказанное, но не
        перетягивают вопрос на предмет тикета (вопрос вне базы остаётся
        вопросом вне базы). Тикет нужен, когда вопрос сам «пуст»
        (приветствие/«что ответить клиенту?») — тогда весит наравне."""
        if not self.ticket:
            return self.kb.search(question, top_k=3, min_score=0.35)
        q_terms = {t for t in question_terms(question) if not _generic(t)}
        if q_terms and self.kb.search(question, top_k=1,
                                      min_score=0.35)["no_match"]:
            # Сам вопрос не отвечается базой: такие слова из тикета
            # («двойное списание») подмешивать нельзя — иначе FAQ тикета
            # «отвечает» на вопрос вне базы. Контекст помогает только
            # домыслить недосказанное, когда вопрос уже релевантен.
            return self.kb.search(question, top_k=3, min_score=0.35)
        contextual_query = ticket_query(self.ticket, question)
        weight = 1.0 if not q_terms else 0.3
        rag = self.kb.search(contextual_query, top_k=5, min_score=0.35,
                             weights=weight)
        return rag

    def ask(self, question: str) -> dict:
        """Ход: retrieval -> LLM (tool-loop) -> ответ + источники.

        Возврат: {question, answer, sources, cited, uncited, next_action,
        tools, dont_know, ticket_id, source}.
        """
        rag = self._retrieve(question)
        results = rag["results"]
        rag_record = {"query": rag["query"],
                      "sources": [{"id": r["id"], "title": r["title"],
                                   "score": r["norm_score"]} for r in results],
                      "no_match": rag["no_match"]}

        if not results:
            # Честное «не знаю»: без LLM (no-LLM turn, паттерн дня 24).
            answer = NO_KB_ANSWER
            rec = {
                "question": question, "answer": answer,
                "sources": [], "cited": [], "uncited": [],
                "next_action": derive_next_action(self.ticket or {}, []),
                "tools": list(self.tool_trace), "dont_know": True,
                "ticket_id": self.ticket_id, "rag": rag_record,
                "source": self.context_source,
            }
            self.history.append(rec)
            return rec

        messages = [
            {"role": "system", "content": self._system_prompt()
             + build_kb_block(results)},
            *self.history_messages(),
            {"role": "user", "content": question},
        ]
        answer, tools_used = self._complete_with_tools(messages)
        answer = (answer or "").strip()
        cited, uncited = extract_cited(results, answer)
        rec = {
            "question": question, "answer": answer,
            "sources": [{"id": r["id"], "title": r["title"],
                         "score": r["norm_score"], "rank": r["rank"]}
                        for r in results],
            "cited": [c["id"] for c in cited],
            "uncited": [u["id"] for u in uncited],
            "next_action": derive_next_action(self.ticket or {}, results),
            "tools": list(self.tool_trace) + tools_used,
            "dont_know": False, "ticket_id": self.ticket_id,
            "rag": rag_record, "source": self.context_source,
        }
        self.history.append(rec)
        return rec

    def history_messages(self) -> list:
        """Прошлые пары вопроса/ответа (коротко, для контекста диалога)."""
        out = []
        for rec in self.history[-4:]:
            out.append({"role": "user", "content": rec["question"]})
            out.append({"role": "assistant", "content": rec["answer"]})
        return out

    def _ask_llm(self, messages: list, tools: list | None) -> dict:
        """Вызов модели с одним повтором, если reasoning съел весь лимит.

        reasoning-модели (deepseek-v4-flash) иногда тратят max_tokens
        целиком на рассуждение и возвращают пустой content
        (empty=True). Повтор с удвоенным лимитом возвращает ответ;
        второй пустой ответ — уже ошибка.
        """
        resp = self.llm.chat(messages, tools=tools)
        if not resp.get("empty"):
            return resp
        resp = self.llm.chat(messages, tools=tools, max_tokens=2800)
        if resp.get("empty") and not (resp.get("content") or "").strip():
            raise LLMError(
                "модель израсходовала лимит на reasoning и вернула пустой ответ")
        return resp

    def _complete_with_tools(self, messages: list) -> tuple[str, list]:
        """LLM-вызов с tool-loop: модель может запросить тикет/пользователя."""
        tools = self.crm.tool_definitions()
        used: list[dict] = []
        partial = ""
        for _ in range(max(1, self.max_tool_rounds)):
            resp = self._ask_llm(messages, tools)
            calls = resp.get("tool_calls") or []
            if not calls:
                return resp.get("content") or "", used
            # Только последний ответ модели остаётся в очереди: этот
            # assistant-блок пересобирается ниже, иначе дубли (некоторые
            # шлюзы возвращают tool_calls прямо в content).
            messages.pop()
            partial = (resp.get("content") or "").strip() or partial
            # assistant-сообщение с tool_calls (OpenAI-формат)
            messages.append({
                "role": "assistant", "content": resp.get("content") or "",
                "tool_calls": [{"id": c["id"], "type": "function",
                                "function": {"name": c["name"],
                                             "arguments": json.dumps(
                                                 c["arguments"],
                                                 ensure_ascii=False)}}
                               for c in calls],
            })
            for call in calls:
                payload, is_err = self.crm.call_tool(call["name"],
                                                     call["arguments"])
                used.append({"tool": call["name"],
                             "arguments": call["arguments"],
                             "ok": not is_err})
                messages.append({
                    "role": "tool", "tool_call_id": call["id"],
                    "name": call["name"],
                    "content": json.dumps(payload, ensure_ascii=False),
                })
        # Лимит раундов: один финальный вызов БЕЗ инструментов; пустой
        # ответ — лучший текст, что модель уже успела написать.
        resp = self._ask_llm(messages, None)
        content = (resp.get("content") or "").strip()
        return (content or partial), used


    # --- стрим для CLI ---

    def stage(self, question: str) -> dict:
        """Retrieval-этап (без LLM): что нашли и какой был запрос.

        Возврат: {query, results, no_match, answer_ready}.
        answer_ready=True — готовый no-LLM ответ (нет статей).
        """
        rag = self._retrieve(question)
        return {"query": rag["query"], "results": rag["results"],
                "no_match": rag["no_match"],
                "answer_ready": NO_KB_ANSWER if rag["no_match"] else None}

    def answer_messages(self, question: str, results: list) -> list:
        """Готовые messages для LLM (после stage)."""
        return [
            {"role": "system", "content": self._system_prompt()
             + build_kb_block(results)},
            *self.history_messages(),
            {"role": "user", "content": question},
        ]

    def finalize(self, question: str, results: list, answer: str,
                 tools_used: list | None = None) -> dict:
        """Завершить ход вручную (CLI стримил ответ сам)."""
        answer = (answer or "").strip()
        cited, uncited = extract_cited(results, answer)
        rec = {
            "question": question, "answer": answer,
            "sources": [{"id": r["id"], "title": r["title"],
                         "score": r["norm_score"], "rank": r["rank"]}
                        for r in results],
            "cited": [c["id"] for c in cited],
            "uncited": [u["id"] for u in uncited],
            "next_action": derive_next_action(self.ticket or {}, results),
            "tools": list(self.tool_trace) + (tools_used or []),
            "dont_know": False, "ticket_id": self.ticket_id,
            "rag": {"query": ticket_query(self.ticket, question)
                    if self.ticket else question,
                    "sources": [{"id": r["id"], "title": r["title"],
                                 "score": r["norm_score"]} for r in results],
                    "no_match": False},
            "source": self.context_source,
        }
        self.history.append(rec)
        return rec
