"""День 35 — CLI ассистента: терминальный вход в те же модули.

Тонкая обёртка над `AssistantService` (дни 30–34): CLI **не дублирует**
логику RAG, ревью, CRM и сценариев — он разбирает аргументы, типизирует
параметры, вызывает сервис и форматирует вывод. Один источник правды —
`assistant.py`, поэтому терминал и веб не расходятся в поведении.

Инструкции агентов берутся из `agents/*.md` (день 35) — файлов
Markdown с frontmatter: правка промпта не требует правки Python.

Запуск (из каталога бэкенда — как остальные модули репозитория; у
пакета `studio/` нет __init__.py, поэтому `python -m studio.cli` из корня
не работает):

    cd studio/backend
    python cli.py ask "как устроен шлюз?"
    python cli.py agents list
    python cli.py review --base master --out review.md
    python cli.py support T-5003
    python cli.py scenario find_usages --param symbol=compress_messages

Из корня репозитория:

    .venv/bin/python studio/backend/cli.py status

Коды возврата: 0 — успех, 1 — ошибка (пригодно для `&&` и CI).

Коды возврата: 0 — успех, 1 — ошибка (пригодно для `&&` и CI).
"""
import argparse
import json
import os
import sys

try:                                    # пакет: studio.backend.cli
    from .assistant import AssistantError, AssistantService, ScenarioError
    from .agents import AgentError, AgentRegistry
    from .agent import StudioAgent
    from . import pr_review
except ImportError:                     # flat: backend-каталог в sys.path
    from assistant import AssistantError, AssistantService, ScenarioError
    from agents import AgentError, AgentRegistry
    from agent import StudioAgent
    import pr_review

# Каталог агентов по умолчанию — `<repo>/agents` (три уровня вверх от
# studio/backend/cli.py). Модуль живёт в studio/backend, а не в studio/:
# у пакета `studio/` нет __init__.py, поэтому запуск идёт из каталога
# бэкенда — тем же способом, что и остальные модули репозитория
# (`python cli.py`, `uvicorn main:app --app-dir studio/backend`).
_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))
AGENTS_DIRNAME = "agents"

# Агент по умолчанию для каждой команды (FR-5.2).
DEFAULT_AGENT = {
    "ask": "project-qa",
    "review": "code-review",
    "support": "support",
    "scenario": "file-ops",
}

# Строковые значения → Python-типы. Критично для `dry_run`: в Python
# непустая строка истинна, поэтому `--param dry_run=false` без типизации
# означал бы True и ЗАПИСАЛ бы файлы вместо dry-run.
_TRUE = ("true", "1", "yes", "on", "да")
_FALSE = ("false", "0", "no", "off", "нет")
_NONE = ("none", "null", "nil", "")


class CLIError(Exception):
    """Ошибка CLI (RU-сообщение, выводится в stderr)."""


def coerce(value):
    """Строку из argv → типизированное значение.

    `true/false` → bool, `none/null/''` → None, числа → int/float,
    остальное → строка. Без этого `dry_run=false` был бы истиной и
    сценарий записал бы файлы (см. `_TRUE`/`_FALSE`).
    """
    if not isinstance(value, str):
        return value
    v = value.strip()
    low = v.lower()
    if low in _TRUE:
        return True
    if low in _FALSE:
        return False
    if low in _NONE:
        return None
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    return value


def parse_params(pairs) -> dict:
    """`["k=v", ...]` → `{k: coerced(v)}` с понятными ошибками."""
    out: dict = {}
    for item in pairs or []:
        if "=" not in item:
            raise CLIError(
                f"Параметр {item!r} без '=' — ожидается формат `ключ=значение`")
        key, _, raw = item.partition("=")
        key = key.strip()
        if not key:
            raise CLIError(f"Параметр {item!r} с пустым ключом")
        out[key] = coerce(raw)
    return out


# ---------------------------------------------------------------------------
# Вывод
# ---------------------------------------------------------------------------

def _hr(char: str = "─", width: int = 72) -> str:
    return char * width


def render_ask(res: dict) -> str:
    """Вопрос по проекту: ответ + источники с путями."""
    lines = []
    if not res.get("found"):
        lines.append(res.get("answer", "Ничего не найдено."))
        return "\n".join(lines)
    lines.append(res["answer"])
    if not res.get("llm") and res.get("llm_error"):
        lines.append(f"\n[!] LLM недоступна: {res['llm_error']}")
        lines.append("    Ответ собран из найденных фрагментов "
                     "(без генерации).")
    srcs = res.get("sources") or []
    if srcs:
        lines.append("\n" + _hr())
        lines.append(f"Источники ({len(srcs)})")
        for i, s in enumerate(srcs, 1):
            score = s.get("score")
            tail = f" · {score:.3f}" if isinstance(score, (int, float)) else ""
            lines.append(f"  [{i}] {s.get('file')}"
                         f"{' · ' + s['section'] if s.get('section') else ''}"
                         f"{tail}")
    return "\n".join(lines)


def render_review(res: dict) -> str:
    """Ревью: сводка, находки с `файл:строка`, рекомендации."""
    s = res.get("summary", {})
    by = s.get("by_severity", {})
    lines = [f"Ревью ({res.get('source')})",
             f"  файлов: {s.get('files', 0)}, "
             f"+{s.get('added', 0)}/−{s.get('removed', 0)}",
             f"  находок: {s.get('findings', 0)} "
             f"(критично {by.get('high', 0)}, средне {by.get('medium', 0)}, "
             f"низко {by.get('low', 0)})"]

    findings = res.get("findings") or []
    if findings:
        lines.append("")
        lines.append(_hr())
        sev_ru = {"high": "критично", "medium": "средне", "low": "низко"}
        for f in findings:
            loc = f.get("file") or "—"
            if f.get("line"):
                loc += f":{f['line']}"
            lines.append(f"  [{sev_ru.get(f.get('severity'), '?')}] "
                         f"{loc}\n      {f.get('message')}")
    recs = res.get("recommendations") or []
    if recs:
        lines.append("")
        lines.append(_hr())
        lines.append("Рекомендации")
        for r in recs:
            lines.append(f"  • {r.get('advice')}")
    if res.get("llm_note"):
        lines.append("")
        lines.append(_hr())
        lines.append("Замечания модели")
        lines.append(res["llm_note"])
    elif res.get("llm_error"):
        lines.append("")
        lines.append(f"[!] LLM недоступна: {res['llm_error']} "
                     "(отчёт построен детерминированно)")
    return "\n".join(lines)


def render_support(res: dict) -> str:
    """Поддержка: ответ + применённые данные CRM."""
    ctx = res.get("context") or {}
    lines = []
    if res.get("found"):
        u = ctx.get("user") or {}
        t = ctx.get("ticket") or {}
        lines.append(f"Тикет {res.get('ticket_id')}: {t.get('topic', '—')}, "
                     f"приоритет {t.get('priority', '—')}")
        if u:
            lines.append(f"Пользователь: {u.get('name')} "
                         f"(план {u.get('plan')}, оплата "
                         f"{u.get('payment_status')})")
    else:
        lines.append(f"Тикет {res.get('ticket_id')} не найден в CRM — "
                     "ответ общий, без персонализации")
    lines.append("")
    lines.append(res.get("answer", ""))
    applied = ctx.get("applied") or []
    if applied:
        lines.append("")
        lines.append(_hr())
        lines.append("Учтено из CRM")
        for a in applied:
            lines.append(f"  • {a}")
    if not res.get("llm") and res.get("llm_error"):
        lines.append("")
        lines.append(f"[!] LLM недоступна: {res['llm_error']}")
    return "\n".join(lines)


def render_tickets(tickets: list, stats: dict) -> str:
    """Список тикетов таблицей."""
    lines = [f"Тикетов: {stats.get('tickets', 0)} "
             f"(пользователей {stats.get('users', 0)})"]
    by = stats.get("by_status") or {}
    if by:
        lines.append("По статусам: "
                     + ", ".join(f"{k} {v}" for k, v in sorted(by.items())))
    if not tickets:
        lines.append("\n(тикетов по фильтру нет)")
        return "\n".join(lines)
    lines.append("")
    width = max((len(t.get("subject", "")) for t in tickets), default=10)
    width = min(width, 46)
    for t in tickets:
        lines.append(f"  {t.get('id'):<8} {t.get('priority'):<8} "
                     f"{t.get('subject', '')[:width]:<{width}} "
                     f"[{t.get('topic')}]")
    return "\n".join(lines)


def render_scenario(res: dict) -> str:
    """Результат сценария: сводка, изменённые файлы, diff."""
    lines = [res.get("summary", "")]
    files = res.get("changed_files") or []
    if files:
        lines.append("")
        lines.append("Изменённые файлы")
        for f in files:
            lines.append(f"  • {f}")
        if not res.get("dry_run"):
            lines.append("")
            lines.append("Проверьте изменения и закоммитьте сами:")
            lines.append("  git diff && git add -p && git commit")
    diff = res.get("diff") or ""
    if diff:
        lines.append("")
        lines.append(_hr())
        lines.append(diff.rstrip())
    return "\n".join(lines)


def render_agents(agents: list, errors: list | None = None) -> str:
    """Каталог агентов."""
    lines = [f"Агентов: {len(agents)}"]
    if agents:
        lines.append("")
        width = max((len(a["name"]) for a in agents), default=10)
        for a in agents:
            lines.append(f"  {a['name']:<{width}}  {a['execution']:<4}  "
                         f"{a['description']}")
    if errors:
        lines.append("")
        lines.append("[!] Файлы с ошибками:")
        for e in errors:
            lines.append(f"  • {e}")
    return "\n".join(lines)


def render_agent(spec, with_prompt: bool = False) -> str:
    """Карточка одного агента."""
    lines = [f"Агент: {spec.name} ({spec.execution})",
             f"Файл: {os.path.basename(spec.path)}",
             f"Описание: {spec.description}",
             f"Инструменты: {', '.join(spec.tools) or '(нет)'}",
             f"temperature: {spec.temperature}"]
    if spec.fallback:
        lines.append("[!] Запрошенного исполнения нет — использовано "
                     "другое (fallback)")
    if with_prompt:
        lines.append("")
        lines.append(_hr())
        lines.append(spec.system_prompt)
    return "\n".join(lines)


def render_status(st: dict) -> str:
    """Состояние ассистента."""
    idx = (f"индекс: {st.get('documents')} док. / {st.get('chunks')} чанков "
           f"({st.get('embedder')})" if st.get("indexed")
           else "индекс НЕ построен (запустите: cli.py index)")
    crm = st.get("crm") or {}
    git = st.get("git") or {}
    corpus = st.get("corpus") or {}
    return "\n".join([
        f"Корпус проекта: {corpus.get('files', 0)} файл(ов)",
        idx,
        f"LLM: {'подключена' if st.get('llm_available') else 'недоступна'}"
        " (без неё ответы строятся детерминированно)",
        f"CRM: {crm.get('tickets', 0)} тикет(ов), "
        f"{crm.get('users', 0)} польз.",
        f"git: {git.get('branch') or '—'}, изменений {git.get('changed', 0)}"
        if git.get("available") else "git: недоступен",
    ])


def render_git(ctx: dict) -> str:
    """Git-контекст."""
    if not ctx.get("available"):
        return "git недоступен (нет репозитория или git не установлен)"
    changed = list(ctx.get("tracked") or []) + list(ctx.get("untracked") or [])
    lines = [f"Ветка: {ctx.get('branch')}",
             f"Изменённых файлов: {len(changed)}",
             f"Строк diff: {ctx.get('diff_lines', 0)}"]
    if changed:
        lines.append("")
        for f in changed[:40]:
            lines.append(f"  {f}")
        if len(changed) > 40:
            lines.append(f"  … и ещё {len(changed) - 40}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Сборка сервиса
# ---------------------------------------------------------------------------

def build_service(repo_root: str | None = None, with_agent: bool = True):
    """Собрать AssistantService так же, как это делает HTTP-путь.

    `with_agent=False` — офлайн-режим (тесты, нет Ollama): модули
    работают детерминированно, LLM не вызывается.
    """
    root = os.path.abspath(repo_root or DEFAULT_REPO_ROOT)
    agent = None
    if with_agent:
        # StudioAgent читает studio/data (как в main.py) — конфиг, журнал.
        data_dir = os.path.join(root, "studio", "data")
        try:
            agent = StudioAgent(data_dir)
        except Exception:                   # noqa: BLE001
            agent = None                     # без LLM — деградация
    return AssistantService(root, agent=agent)


def build_registry(repo_root: str | None = None) -> AgentRegistry:
    """Реестр агентов репозитория."""
    root = os.path.abspath(repo_root or DEFAULT_REPO_ROOT)
    return AgentRegistry(os.path.join(root, AGENTS_DIRNAME))


# ---------------------------------------------------------------------------
# Команды
# ---------------------------------------------------------------------------

def _agent_prompt(registry: AgentRegistry, name: str | None,
                  command: str, execution: str = "cli") -> tuple:
    """(системный промпт, пометка о fallback) для команды."""
    agent_name = name or DEFAULT_AGENT.get(command)
    if not agent_name:
        return None, None
    try:
        return registry.prompt_for(agent_name, execution=execution)
    except AgentError as e:
        # Агент не найден — команда всё равно должна работать:
        # предупреждаем и используем встроенный промпт.
        return None, str(e)


def cmd_agents(args, svc, reg) -> int:
    if args.agents_action == "show":
        try:
            spec = reg.get(args.name, execution=args.execution)
        except AgentError as e:
            raise CLIError(str(e))
        out = (spec.to_dict(with_prompt=True) if args.json
               else render_agent(spec, with_prompt=args.prompt))
        print(json.dumps(out, ensure_ascii=False, indent=2)
              if args.json else out)
        return 0
    agents, errors = reg.list(), reg.errors()
    if args.json:
        print(json.dumps({"agents": agents, "errors": errors},
                         ensure_ascii=False, indent=2))
    else:
        print(render_agents(agents, errors))
    return 0


def cmd_index(args, svc, reg) -> int:
    res = svc.build_index(strategy=args.strategy)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        st = res.get("stats", {})
        print(f"Индекс собран ({res.get('mode')}, {res.get('strategy')}): "
              f"{st.get('docs')} документов, {st.get('chunks')} чанков")
    return 0


def cmd_status(args, svc, reg) -> int:
    st = svc.status()
    print(json.dumps(st, ensure_ascii=False, indent=2) if args.json
          else render_status(st))
    return 0


def cmd_git(args, svc, reg) -> int:
    ctx = svc.git.context()
    if args.json:
        print(json.dumps({k: v for k, v in ctx.items() if k != "diff"},
                         ensure_ascii=False, indent=2))
    else:
        print(render_git(ctx))
    return 0


def cmd_ask(args, svc, reg) -> int:
    prompt, warn = _agent_prompt(reg, args.agent, "ask")
    if warn:
        print(f"[!] {warn}", file=sys.stderr)
    res = svc.ask_project(args.question, top_k=args.top_k,
                          system_prompt=prompt)
    out = json.dumps(res, ensure_ascii=False, indent=2) if args.json \
        else render_ask(res)
    _emit(out, args)
    return 0


def cmd_review(args, svc, reg) -> int:
    prompt, warn = _agent_prompt(reg, args.agent, "review")
    if warn:
        print(f"[!] {warn}", file=sys.stderr)
    diff = None
    if args.diff:
        if not os.path.isfile(args.diff):
            raise CLIError(f"Файл diff не найден: {args.diff}")
        with open(args.diff, encoding="utf-8") as f:
            diff = f.read()
    res = svc.review_diff(diff=diff, base=args.base,
                          with_llm=not (args.no_llm or svc.agent is None),
                          system_prompt=prompt)
    out = json.dumps(res, ensure_ascii=False, indent=2) if args.json \
        else render_review(res)
    _emit(out, args)
    # Код возврата 1, если есть критичные находки: удобно для CI.
    if args.fail_on_high and any(
            f.get("severity") == "high" for f in res.get("findings", [])):
        print("[!] Есть находки уровня «критично»", file=sys.stderr)
        return 1
    return 0


def cmd_tickets(args, svc, reg) -> int:
    tickets = svc.tickets.list_tickets(status=args.status,
                                       priority=args.priority,
                                       topic=args.topic)
    stats = svc.tickets.stats()
    if args.json:
        print(json.dumps({"tickets": tickets, "stats": stats},
                         ensure_ascii=False, indent=2))
    else:
        print(render_tickets(tickets, stats))
    return 0


def cmd_support(args, svc, reg) -> int:
    prompt, warn = _agent_prompt(reg, args.agent, "support")
    if warn:
        print(f"[!] {warn}", file=sys.stderr)

    question = args.question
    # Неизвестный тикет без вопроса искать не по чему: сервис честно
    # бросает AssistantError («нечего обрабатывать»). В терминале это
    # выглядит как отказ на ровном месте, поэтому подставляем id тикета
    # как текст запроса — тогда вернётся общий ответ по базе знаний с
    # явной пометкой, что тикет не найден.
    if not question and not svc.tickets.ticket(args.ticket_id):
        question = args.ticket_id
        print(f"[i] Тикет {args.ticket_id} не найден в CRM — "
              "отвечаю по базе знаний без персонализации",
              file=sys.stderr)

    res = svc.support_answer(args.ticket_id, question=question,
                             system_prompt=prompt)
    out = json.dumps(res, ensure_ascii=False, indent=2) if args.json \
        else render_support(res)
    _emit(out, args)
    return 0


def cmd_scenarios(args, svc, reg) -> int:
    items = svc.scenarios()
    if args.json:
        print(json.dumps({"scenarios": items}, ensure_ascii=False, indent=2))
    else:
        print(f"Сценариев: {len(items)}")
        print("")
        for s in items:
            params = ", ".join(s["params"]) or "—"
            print(f"  {s['name']:<18} {s['title']}")
            print(f"  {'':<18} параметры: {params}")
    return 0


def cmd_scenario(args, svc, reg) -> int:
    params = parse_params(args.param)
    res = svc.run_scenario(args.name, **params)
    out = json.dumps(res, ensure_ascii=False, indent=2) if args.json \
        else render_scenario(res)
    _emit(out, args)
    return 0


def _flag(args, name: str) -> bool:
    """Значение флага, даже если у подкоманды его нет (общий `--no-llm`)."""
    return bool(getattr(args, name, False))


def _read_text_source(path: str, what: str) -> str:
    """Прочитать файл по пути; `-` — stdin (пайп в CI: `git diff | cli`)."""
    if path == "-":
        return sys.stdin.read()
    if not os.path.isfile(path):
        raise CLIError(f"{what} не найден: {path}")
    with open(path, encoding="utf-8") as f:
        return f.read()


def _rule(text: str, width: int = 74) -> str:
    return f"# {text[:width - 2]} ".ljust(width, "=")


def cmd_pr_review(args, svc, reg) -> int:
    """Первый шаг Action'а: что доступно пайплайну (RAG, LLM, git, PR)."""
    github = pr_review.github_context()
    with_llm = not (_flag(args, "no_llm")
                    or _flag(args, "global_no_llm") or svc.agent is None)
    pf = pr_review.preflight(svc, github=github, diff_text=args.diff,
                             with_llm=with_llm)
    if args.json:
        print(json.dumps(pf, ensure_ascii=False, indent=2))
    else:
        print(pr_review.render_preflight(pf))
    # Без индекса RAG молчит, но детерминированный отчёт остаётся
    # полезным — это предупреждение, а не провал пайплайна.
    return 0


def cmd_pr_review_run(args, svc, reg) -> int:
    """Полное AI-ревью PR: diff → RAG → проверки → модель → отчёт."""
    diff_text = None
    if args.diff:
        diff_text = _read_text_source(args.diff, "Файл diff")
    if not diff_text and args.github_pr:
        # Форк: ветки PR в рабочем дереве нет — diff и файлы отдаёт API.
        repo, _, num = args.github_pr.rpartition("#")
        if not repo or not num.isdigit():
            raise CLIError("--github-pr ожидается в формате owner/repo#123")
        diff_text = pr_review.GitHubClient().pull_diff(repo, int(num))
    with_llm = not (_flag(args, "no_llm")
                    or _flag(args, "global_no_llm") or svc.agent is None)

    pr = pr_review.github_context() or {}
    if args.title:
        pr["title"] = args.title
    if not diff_text and args.base:
        pr.setdefault("base_ref", args.base)
    res = pr_review.run_pr_review(svc, diff_text=diff_text, base=args.base,
                                  pr=pr, with_llm=with_llm,
                                  top_k_rag=args.top_k)

    # $GITHUB_STEP_SUMMARY: сводка видна прямо на странице запуска.
    if args.step_summary:
        with open(args.step_summary, "a", encoding="utf-8") as f:
            f.write(pr_review.render_step_summary(res) + "\n")
    # Короткая сводка в PR (с доп. флагом) и workflow-аннотации.
    if args.comment:
        repo, num = args.comment.rsplit("#", 1)
        client = pr_review.GitHubClient()
        client.upsert_comment(repo, int(num),
                              pr_review.render_markdown(res))
        print(f"Комментарий в {args.comment}: обновлён/создан",
              file=sys.stderr)
    if args.annotations:
        for line in pr_review.annotations(res):
            print(line)

    markdown = pr_review.render_markdown(res)
    # --out (markdown) и --json-out (данные) пишутся раздельно: в CI
    # нужны оба — артефакт для человека, JSON для машинной проверки.
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(markdown.rstrip() + "\n")
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=2)
    if args.out or args.json_out:
        # В файлы — и НЕ в stdout: журнал CI остаётся коротким, полный
        # отчёт лежит в артефакте и комментарии.
        written = ", ".join(x for x in (args.out, args.json_out) if x)
        print(_rule("AI-ревью PR") + "\n"
              + pr_review.render_step_summary(res) + "\n"
              + f"Полный отчёт: {written}")
    else:
        print(json.dumps(res, ensure_ascii=False, indent=2) if args.json
              else markdown)

    if args.fail_on_high and any(
            f.get("severity") == "high" for f in res.get("findings", [])):
        print("[!] Есть находки уровня «критично»", file=sys.stderr)
        return 1
    return 0


def _emit(text: str, args) -> None:
    """Вывод результата: в файл (`--out`) или в stdout."""
    out_path = getattr(args, "out", None)
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text.rstrip() + "\n")
        print(f"Записано: {out_path}")
    else:
        print(text)


# ---------------------------------------------------------------------------
# Парсер
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Парсер аргументов: подкоманды и общие флаги."""
    p = argparse.ArgumentParser(
        prog="cli.py",
        description="Ассистент разработчика и поддержки (терминальный "
                    "вход в те же модули, что и веб-Студия).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Примеры:\n"
               "  cli.py agents list\n"
               '  cli.py ask "как устроен шлюз локальной LLM?"\n'
               "  cli.py review --base master --out review.md\n"
               "  cli.py support T-5003\n"
               "  cli.py scenario find_usages --param "
               "symbol=compress_messages\n"
               "  cli.py pr-review check\n"
               "  git diff origin/master...HEAD | cli.py pr-review run "
               "--diff - --out review.md\n")
    p.add_argument("--repo", help="корень репозитория (по умолчанию — "
                                  "определяется от файла)")
    p.add_argument("--debug", action="store_true",
                   help="показать traceback при ошибке")
    p.add_argument("--no-llm", action="store_true", dest="global_no_llm",
                   help="без LLM: только детерминированные ответы "
                        "(не трогает сеть)")
    sub = p.add_subparsers(dest="command", metavar="КОМАНДА")

    def common(sp, out=False):
        sp.add_argument("--json", action="store_true",
                        help="машинный вывод (JSON)")
        if out:
            sp.add_argument("--out", metavar="FILE",
                            help="записать вывод в файл")

    # agents
    pa = sub.add_parser("agents", help="каталог агентов из agents/*.md")
    sub_a = pa.add_subparsers(dest="agents_action", metavar="ДЕЙСТВИЕ")
    pl = sub_a.add_parser("list", help="список агентов")
    common(pl)
    ps = sub_a.add_parser("show", help="карточка агента")
    ps.add_argument("name", help="имя агента")
    ps.add_argument("--execution", default="cli", choices=["api", "cli"])
    ps.add_argument("--prompt", action="store_true",
                    help="показать системный промпт")
    common(ps)

    # ask
    pk = sub.add_parser("ask", help="вопрос по проекту (RAG)")
    pk.add_argument("question", help="текст вопроса")
    pk.add_argument("--agent", help="агент из agents/*.md")
    pk.add_argument("--top-k", type=int, default=5, dest="top_k")
    common(pk, out=True)

    # review
    pr = sub.add_parser("review", help="ревью изменений")
    pr.add_argument("--diff", help="файл с unified diff")
    pr.add_argument("--base", help="ветка для сравнения (git diff base...HEAD)")
    pr.add_argument("--agent", help="агент из agents/*.md")
    pr.add_argument("--no-llm", action="store_true", dest="no_llm",
                    help="без LLM (только детерминированные находки)")
    pr.add_argument("--fail-on-high", action="store_true",
                    dest="fail_on_high",
                    help="код возврата 1 при критичных находках (для CI)")
    common(pr, out=True)

    # tickets
    pt = sub.add_parser("tickets", help="тикеты из CRM")
    pt.add_argument("--status")
    pt.add_argument("--priority")
    pt.add_argument("--topic")
    common(pt)

    # support
    psu = sub.add_parser("support", help="ответ по тикету (с учётом CRM)")
    psu.add_argument("ticket_id", help="id тикета, например T-5003")
    psu.add_argument("--question", help="уточняющий вопрос")
    psu.add_argument("--agent", help="агент из agents/*.md")
    common(psu, out=True)

    # scenarios
    psc = sub.add_parser("scenarios", help="каталог сценариев")
    common(psc)

    # scenario
    psc2 = sub.add_parser("scenario", help="запуск сценария")
    psc2.add_argument("name", help="имя сценария")
    psc2.add_argument("--param", action="append", metavar="K=V",
                      help="параметр сценария (можно несколько)")
    common(psc2, out=True)

    # index / status / git
    pi = sub.add_parser("index", help="собрать индекс проекта")
    pi.add_argument("--strategy", default="structural",
                    choices=["fixed", "structural"])
    common(pi)
    cn = sub.add_parser("status", help="состояние ассистента")
    common(cn)
    pg = sub.add_parser("git", help="git-контекст")
    common(pg)

    # pr-review (день 32): пайплайн автоматического ревью PR.
    ppr = sub.add_parser(
        "pr-review",
        help="AI-ревью PR: diff → RAG (документация + код) → баги, "
             "архитектура, рекомендации")
    sub_pr = ppr.add_subparsers(dest="pr_action", metavar="ДЕЙСТВИЕ")
    prun = sub_pr.add_parser(
        "run", help="построить ревью PR (markdown или --json)")
    prun.add_argument("--diff", metavar="FILE|-",
                      help="unified diff из файла или stdin (`-`)")
    prun.add_argument("--base",
                      help="ветка для сравнения (git diff base...HEAD)")
    prun.add_argument("--github-pr", metavar="OWNER/REPO#N",
                      help="взять diff из API GitHub (для PR из форка)")
    prun.add_argument("--title", help="заголовок PR для отчёта")
    prun.add_argument("--top-k", type=int, default=4, dest="top_k",
                      help="фрагментов RAG на изменённый файл (по умолчанию 4)")
    prun.add_argument("--fail-on-high", action="store_true",
                      dest="fail_on_high",
                      help="код возврата 1 при критичных находках")
    prun.add_argument("--annotations", action="store_true",
                      help="workflow-команды GitHub ::warning/::error "
                           "(аннотации на строки diff)")
    prun.add_argument("--comment", metavar="OWNER/REPO#N",
                      help="опубликовать отчёт комментарием в PR "
                           "(повторный прогон обновляет старый)")
    prun.add_argument("--json-out", metavar="FILE", dest="json_out",
                      help="записать данные отчёта в JSON (для CI)")
    prun.add_argument("--step-summary", metavar="FILE", dest="step_summary",
                      help="дописать сводку в $GITHUB_STEP_SUMMARY")
    common(prun, out=True)
    pchk = sub_pr.add_parser("check",
                             help="что доступно пайплайну (RAG, LLM, git, PR)")
    pchk.add_argument("--diff", metavar="FILE", help="файл diff (если передан)")
    pchk.add_argument("--no-llm", action="store_true", dest="no_llm",
                      help="считать LLM отключённой")
    common(pchk)
    return p


HANDLERS = {
    "agents": cmd_agents,
    "index": cmd_index,
    "status": cmd_status,
    "git": cmd_git,
    "ask": cmd_ask,
    "review": cmd_review,
    "tickets": cmd_tickets,
    "support": cmd_support,
    "scenarios": cmd_scenarios,
    "scenario": cmd_scenario,
    "pr-review": cmd_pr_review,
}

# Действия `pr-review`: check (предполётная проверка) и run (ревью).
PR_ACTIONS = {
    "run": cmd_pr_review_run,
    "check": cmd_pr_review,
}


def main(argv=None) -> int:
    """Точка входа CLI. Возвращает код возврата (0 — успех, 1 — ошибка)."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0
    if args.command == "agents" and not getattr(args, "agents_action", None):
        parser.parse_args(["agents", "list", "--help"])
        return 0

    try:
        reg = build_registry(getattr(args, "repo", None))
        # `agents` не требует сервиса: не создаём StudioAgent зря.
        svc = None
        if args.command != "agents":
            # --no-llm (глобальный) отключает LLM целиком: ответы строятся
            # детерминированно и сеть не трогается вовсе. Нужно для тестов,
            # офлайн-работы и когда Ollama недоступна — иначе первый же
            # вопрос уходит в сетевой зонд моделей и ждёт таймаут.
            svc = build_service(getattr(args, "repo", None),
                                with_agent=not getattr(args,
                                                       "global_no_llm",
                                                       False))
        if args.command == "pr-review":
            action = getattr(args, "pr_action", None) or "run"
            if action not in PR_ACTIONS:
                raise CLIError(f"Неизвестное действие pr-review: {action}")
            return PR_ACTIONS[action](args, svc, reg)
        return HANDLERS[args.command](args, svc, reg)
    except (CLIError, AssistantError, ScenarioError, AgentError,
            pr_review.PRError) as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        if getattr(args, "debug", False):
            raise
        return 1
    except KeyboardInterrupt:
        print("Прервано пользователем", file=sys.stderr)
        return 1
    except Exception as e:                  # noqa: BLE001
        # Без --debug traceback не показываем: CLI должен давать понятную
        # ошибку, а не стек (он бесполезен в пайпе и CI-логе).
        print(f"Непредвиденная ошибка: {type(e).__name__}: {e}",
              file=sys.stderr)
        if getattr(args, "debug", False):
            raise
        return 1


if __name__ == "__main__":
    sys.exit(main())
