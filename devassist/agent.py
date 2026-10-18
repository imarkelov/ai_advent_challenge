"""Агент-ассистент для работы с файлами проекта (день 34).

Задача задаётся НА УРОВНЕ ЦЕЛИ («найди все места, где используется CRMClient»,
«обнови документацию под изменения», «сгенерируй changelog»), а не
«открой файл X». Агент сам решает, какие файлы прочитать/найти, и сам
инициирует изменения через файловые MCP-инструменты:

    read_file / search_files    — чтение и поиск по нескольким файлам;
    write_file                  — создание/изменение (бэкап + атомарная
                                  запись + unified diff);
    diff_files                  — diff/список изменений прогона.

Два режима:
  LLM (по умолчанию)  — /chat/completions с tools, tool-loop до TOOL_LOOP_CAP
                        итераций; модель видит результат каждой операции
                        (файл:строка, diff) и продолжает сама;
  offline (--offline) — детерминированный планировщик тех же инструментов
                        (без сети и без модели) для воспроизводимости:

    цель про использование символа -> search_files(pattern) + сводка;
    «проверь … правила»            -> guardrails.check_all() по файлам;
    «changelog / список изменений» -> read_file(README/RELEASE) ->
                                      write_file(docs/CHANGELOG_day34.md);
    «обнови документацию … X»      -> search_files(X) ->
        read_file(найденные .py) -> write_file(docs/day34/<slug>.md):
                          карта использования + скелет ADR.

Каждая операция пишется в журнал (printed CLI): [MCP] tool(args) -> ok.
Бэкапы — в .devassist/backup/<run_id>/ (повторный прогон диффится
инструментом diff_files).
"""
from __future__ import annotations

import difflib
import json
import os
import re
import time

import tools._fs_sandbox as sandbox
from devassist import guardrails
from devassist.bridge import FileTools

SYSTEM_PROMPT = """Ты — инженерный ассистент, работающий с файлами проекта.

Правило: не пересказывай файлы по памяти — читай их инструментами.
Тебе дают цель, а не пошаговую инструкцию. Сам решай, какие файлы искать
(search_files), что читать (read_file) и что менять (write_file — полное
новое содержимое файла). После изменений покажи diff (diff_files) и краткое
резюме: что изменено, в каких файлах, почему.

Ограничения:
- Один путь читай ОДИН раз; не повторяй read_file без необходимости;
- в search_files.path передавай только каталог, не файл; поиск по
  документации — search_files(path=".", glob="*.md");
- максимум 10 вызовов инструментов на цель — затем сразу write_file;
- paths — относительные от корня репозитория; скрытые каталоги и .env закрыты;
- меняй документацию в docs/ или README.md; код правь только если цель явно
  требует изменения кода;
- не выдумывай факты: ссылайся на файл:строку из результата инструментов;
- ответ на русском, кратко.
"""

MAX_TOOL_RESULT = 6000
TOOL_LOOP_CAP = 12
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _extract_symbol(goal: str) -> str:
    """Символ из цели: «CRMClient», 'CRMClient' или CamelCase/ACRONYM в тексте."""
    match = re.search(r"[«\"'`]([^»\"'`]+)[»\"'`]", goal)
    if match:
        return match.group(1).strip()
    match = re.search(r"\b([A-Z][A-Za-z0-9_]{2,})\b", goal)
    return match.group(1) if match else ""


def slug(text: str, limit: int = 40) -> str:
    out = re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁ]+", "-", (text or "").strip().lower())
    return out.strip("-")[:limit] or "task"


class DevAgent:
    """Файловый ассистент: LLM tool-loop или детерминированный офлайн-план."""

    def __init__(self, llm=None, tools: FileTools | None = None,
                 run_id: str | None = None) -> None:
        self.llm = llm
        self.tools = tools or FileTools()
        self.trace: list = []
        self.changes: list = []  # {"path", "status", "diff"}
        self.last_answer = ""
        self.run_id = run_id or time.strftime("%Y%m%d-%H%M%S")
        os.environ["FS_RUN_ID"] = self.run_id
        self.tools._env = dict(os.environ)  # MCP-серверы видят id прогона
        self.tools.connect()  # каталог бэкапов = текущий прогон

    # --- инфраструктура ---

    def _call(self, name: str, args: dict) -> dict:
        payload, is_error = self.tools.call_tool(name, args)
        rec = {"tool": name, "arguments": args, "ok": not is_error,
               "payload": payload}
        self.trace.append(rec)
        if name == "write_file" and not is_error and payload.get("status") in (
                "created", "updated"):
            self.changes.append({"path": payload.get("path"),
                                 "status": payload.get("status"),
                                 "diff": payload.get("diff") or ""})
        return rec

    def read_file(self, path: str, from_line=None, to_line=None) -> dict:
        args = {"path": path}
        if from_line:
            args["from_line"] = from_line
        if to_line:
            args["to_line"] = to_line
        return self._call("read_file", args)

    def search(self, pattern: str, glob: str = "", path: str = "") -> dict:
        args = {"pattern": pattern}
        if glob:
            args["glob"] = glob
        if path:
            args["path"] = path
        return self._call("search_files", args)

    def write_file(self, path: str, content: str) -> dict:
        return self._call("write_file", {"path": path, "content": content})

    def diff(self, path: str = "") -> dict:
        return self._call("diff_files", {"path": path} if path else {})

    # --- LLM tool-loop ---

    def _llm_messages(self, goal: str) -> list:
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "Цель: " + goal},
        ]

    @staticmethod
    def _tool_result_text(payload) -> str:
        text = json.dumps(payload, ensure_ascii=False)
        return text[:MAX_TOOL_RESULT] + ("…[обрезано]" if len(text)
                                         > MAX_TOOL_RESULT else "")

    def run_llm(self, goal: str) -> dict:
        messages = self._llm_messages(goal)
        defs = self.tools.tool_definitions()
        steps = 0
        while steps < TOOL_LOOP_CAP:
            steps += 1
            try:
                resp = self.llm.chat(messages, tools=defs, temperature=0.1,
                                     max_tokens=2200)
            except Exception as exc:  # LLMError и т.п.
                return {"ok": False, "answer": "LLM ошибка: %s" % exc,
                        "steps": steps}
            calls = resp.get("tool_calls") or []
            if not calls:
                self.last_answer = resp.get("content") or ""
                break
            messages.append({"role": "assistant", "content": resp.get("content")
                             or "", "tool_calls": [
                                 {"id": c["id"], "type": "function",
                                  "function": {"name": c["name"],
                                               "arguments": json.dumps(
                                                   c["arguments"],
                                                   ensure_ascii=False)}}
                                 for c in calls]})
            for call in calls:
                rec = self._call(call["name"], call["arguments"])
                messages.append({"role": "tool", "tool_call_id": call["id"],
                                 "name": call["name"],
                                 "content": self._tool_result_text(rec["payload"])})
        else:
            try:
                resp = self.llm.chat(messages + [{
                    "role": "user", "content": "Лимит инструментов исчерпан — "
                    "дай итоговое резюме по сделанному."}], max_tokens=1200)
                self.last_answer = resp.get("content") or self.last_answer
            except Exception:
                pass
        return {"ok": True, "answer": self.last_answer, "steps": steps}

    # --- офлайн-планировщик (детерминированный) ---

    def plan_offline(self, goal: str) -> dict:
        low = goal.lower()
        if "diff" in low:
            return self._plan_diff()
        for kw in ("changelog", "changelog-файл", "список изменений"):
            if kw in low:
                return self._plan_changelog(goal)
        if "документ" in low:
            symbol = _extract_symbol(goal)
            if not symbol:
                return {"ok": False, "answer": "нужен символ/тема для документации"}
            return self._plan_docs(goal, symbol)
        if "прав" in low or "инвариант" in low or "соответств" in low:
            return self._plan_check()
        symbol = _extract_symbol(goal)
        if "использ" in low or "найди" in low:
            if not symbol:
                return {"ok": False, "answer": "нужен символ для поиска"}
            return self._plan_usage(symbol)
        return {"ok": False, "answer": "офлайн-план не распознал цель; "
                                       "запустите с LLM (без --offline)"}

    def _answer_text(self, plan: dict, summary: str) -> dict:
        self.last_answer = summary
        return {"ok": plan.get("ok", True), "answer": summary,
                "steps": len(self.trace)}

    def _plan_usage(self, symbol: str) -> dict:
        rec = self.search(re.escape(symbol), glob="*.py")
        data = rec["payload"]
        if not rec["ok"]:
            return self._answer_text(rec, "поиск не удался: "
                                     + str(data.get("error")))
        lines = ["Использование «%s»: файлов %d, вхождений %d." % (
            symbol, data.get("files_matched", 0), data.get("total_matches", 0))]
        for f, n in (data.get("by_file") or {}).items():
            lines.append("  %-34s %2d" % (f, n))
        for m in (data.get("matches") or [])[:8]:
            lines.append("  %s:%d  %s" % (m["file"], m["line"], m["text"]))
        return self._answer_text(rec, "\n".join(lines))

    def _plan_diff(self) -> dict:
        rec = self.diff()
        data = rec["payload"]
        if not rec["ok"]:
            return {"ok": True, "answer": "Изменений нет: " + str(
                data.get("error")), "steps": len(self.trace)}
        files = data.get("files") or []
        if not files:
            return {"ok": True, "answer": "Изменений с начала прогона нет.",
                    "steps": len(self.trace)}
        lines = ["Изменено файлов: %d." % data["changed"]]
        for f in files:
            changed = [ln for ln in f["diff"].splitlines()
                       if ln[:1] in "+-" and not ln.startswith(("+++", "---"))]
            lines.append("  %-9s %s (+%d/-%d)" % (
                f["status"], f["path"],
                sum(1 for ln in changed if ln[0] == "+"),
                sum(1 for ln in changed if ln[0] == "-")))
        return {"ok": True, "answer": "\n".join(lines), "steps": len(self.trace)}

    def _plan_check(self) -> dict:
        # правило стартует от корня: корень рабочей области = корень репо
        os.environ.setdefault("FS_WORKSPACE", REPO_ROOT)
        report = guardrails.check_all()
        self.last_answer = guardrails.format_report(report)
        return {"ok": report["ok"], "answer": self.last_answer, "steps": 0}

    def _plan_changelog(self, goal: str) -> dict:
        sources = [p for p in ("README.md", "RELEASE.md") if os.path.isfile(
            os.path.join(sandbox.workspace_root(), p))]
        read = {p: self.read_file(p, to_line=80) for p in sources}
        today = time.strftime("%Y-%m-%d")
        title = "CHANGELOG · день 34: ассистент для работы с файлами"
        body = [
            "# " + title, "",
            "_Сгенерировано ассистентом (devassist) %s по цели: «%s»._" % (
                today, goal), "",
            "## Источники", "",
        ] + ["- %s — %d строк" % (
            p, read[p]["payload"].get("total_lines", 0) if read[p]["ok"] else 0)
             for p in sources] + [
            "- Цель: `devassist plan_changelog` (детерминированный генератор)",
        ] + [
            "", "## Изменения", "",
            "### Added", "",
            "- `devassist/` — CLI-ассистент с файловыми инструментами через MCP",
            "- `tools/mcp_servers/fs_read|fs_search|fs_write|fs_diff.py` — "
            "едицельные stdio MCP-серверы",
            "- `scripts/e2e_day34.py` — воспроизводимая проверка (офлайн-ядро "
            "+ live)", "",
            "### Changed", "",
            "- README.md — раздел «День 34»", "",
        ]
        last = self.changes[-1] if self.changes else None
        target = "docs/CHANGELOG_day34.md"
        if last and last["path"].startswith("docs/CHANGELOG"):
            target = last["path"]
        self.write_file(target, "\n".join(body) + "\n")
        summary = ("Прочитаны %s, сгенерирован %s (%d строк)." % (
            ", ".join(sources), target, len(body)))
        return {"ok": True, "answer": summary, "steps": len(self.trace)}

    def _plan_docs(self, goal: str, symbol: str) -> dict:
        search = self.search(re.escape(symbol), glob="*.py")
        files = list((search["payload"].get("by_file") or {}).keys())
        snippets = []
        for path in files[:3]:
            rec = self.read_file(path)
            if not rec["ok"]:
                continue
            content = rec["payload"].get("content") or ""
            hits = [ln.strip() for ln in content.splitlines()
                    if symbol.lower() in ln.lower()][:3]
            snippets.append((path, rec["payload"].get("total_lines", 0), hits))
        name = slug(symbol)
        target = "docs/day34/%s.md" % name
        doc = [
            "# Документация: %s" % symbol, "",
            "_Сгенерировано ассистентом по цели: «%s»._" % goal, "",
            "## Где используется", "",
            "| Файл | Строк | Найдено вхождений |", "| --- | --- | --- |",
        ]
        for path, total, hits in snippets:
            doc.append("| `%s` | %d | %d |" % (
                path, total, (search["payload"].get("by_file") or {}).get(path, 0)))
        doc += ["", "## Что делает", ""]
        for path, _, hits in snippets:
            for hit in hits:
                doc.append("- `%s`: `%s`" % (path, hit[:120]))
        doc += ["", "## ADR (заготовка)", "",
                "- **Контекст:** %s используется в %d файлах." % (
                    symbol, len(files)),
                "- **Решение:** описать контракт и точки расширения.",
                "- **Последствия:** изменение требует синхронного обновления "
                "документов в `docs/day34/`.", ""]
        self.write_file(target, "\n".join(doc) + "\n")
        summary = ("Найдено %d файлов с «%s», прочитано %d, сгенерирован "
                   "%s." % (len(files), symbol, len(snippets), target))
        return {"ok": True, "answer": summary, "steps": len(self.trace)}

    # --- единая точка ---

    def run(self, goal: str) -> dict:
        if self.llm is None:
            return self.plan_offline(goal)
        return self.run_llm(goal)

    def changes_report(self) -> str:
        if not self.changes:
            return "Изменений нет."
        chunks = []
        for ch in self.changes:
            chunks.append("%s: %s" % (ch["status"], ch["path"]))
        chunks.append("")
        chunks.append("".join(ch["diff"] for ch in self.changes))

    def final_answer(self) -> str:
        """Текст итога (сводку файлов CLI печатает сам)."""
        return self.last_answer or "(модель не дала текста)"
        return "\n".join(chunks)


def _trace_arg(value) -> str:
    text = str(value)
    if len(text) > 48:
        return text.splitlines()[0][:48] + "…"
    return text


def format_trace_line(rec: dict) -> str:
    args = ", ".join("%s=%s" % (k, _trace_arg(v))
                     for k, v in (rec.get("arguments") or {}).items())
    if rec["tool"] == "read_file":
        p = rec["payload"]
        res = "%d строк" % p.get("total_lines", 0) if rec["ok"] \
            else "ошибка"
    elif rec["tool"] in ("search_files", "diff_files"):
        p = rec["payload"]
        res = ("%s файлов, %s вхождений" % (p.get("files_matched"),
                                            p.get("total_matches"))
               if rec["tool"] == "search_files" else
               "%s файлов изменено" % p.get("changed")) if rec["ok"] else "ошибка"
    else:
        res = (rec["payload"].get("status") if rec["ok"] else "ошибка")
    return "[MCP] %s(%s) -> %s" % (rec["tool"], args, res)


def diff_since(path: str, original: str, current: str) -> str:
    return "".join(difflib.unified_diff(
        original.splitlines(True), current.splitlines(True),
        fromfile="a/" + path, tofile="b/" + path))
