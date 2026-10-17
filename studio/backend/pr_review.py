"""День 32 — автоматизация ревью PR: пайплайн, где ассистент разбирает PR.

Один модуль отвечает за шаги, которые в CI раньше делал человек:

1. **Получить diff и изменённые файлы** — unified diff из файла,
   stdin, `git` (локально) или API GitHub (для `pull_request` из форка,
   где код ревьюируемой ветки недоступен в рабочем дереве);
   разбор — `assistant.parse_unified_diff` (уже отлажен на днях 30–34),
   а изменённые файлы с типом изменения (added/modified/renamed/deleted)
   собираются здесь — `DiffFile` разборщика тип переименования не
   хранит.
2. **RAG по документации и коду** — дифф разрезается на окна по хункам;
   по терминам окна ищутся релевантные чанки корпуса проекта
   (`AssistantService.pkb` — README, `docs/`, `openspec/`, код бэкенда и
   фронтенда), запросы к индексу НЕ ходят в сеть. Промпты агентов
   (`agents/*.md`) ищутся по имени файла — в них записаны инварианты
   проекта; правила дней 30–34 (`assistant.INVARIANTS`,
   `check_diff`) — по именам функции и правила.
3. **Детерминированные проверки** — `assistant.check_diff`
   (secret/bare_except/missing_test/missing_docs/…) плюс инварианты
   проекта, сработавшие именно в добавленных строках диффа.
4. **Ревью от модели** — три обещанных блока: потенциальные баги,
   архитектурные проблемы, рекомендации. Ответ модели парсится в
   секции; нераспознанный ответ не теряется, а идёт в «Рекомендации».
5. **Отчёт** — markdown (комментарий в PR, артефакт, вывод в терминал),
   JSON (машинная обработка) и сводка для `$GITHUB_STEP_SUMMARY`.

Принципы те же, что в `assistant.py`:

* **факты собирает код, LLM только формулирует** — без модели отчёт
  всё равно содержит changelog, находки, инварианты и рекомендации;
* **никаких мутаций** — модуль читает git и сеть (только GET/POST
  моделей и комментариев), но не коммитит и не пушит;
* **деградация наблюдаемая** — причина недоступности LLM попадает в
  `llm_error` отчёта и в аннотацию GitHub, а не глотается.

Импорт без побочных эффектов: сеть и диск не трогаются до вызова.
"""
import json
import os
import re

try:                                    # пакет: studio.backend.pr_review
    from . import assistant as A
    from .assistant import AssistantError
except ImportError:                     # flat: backend-каталог в sys.path
    import assistant as A
    from assistant import AssistantError

# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

SECTION_BUGS = "bugs"
SECTION_ARCH = "architecture"
SECTION_RECS = "recommendations"

SECTION_TITLES = {
    SECTION_BUGS: "Потенциальные баги",
    SECTION_ARCH: "Архитектурные проблемы",
    SECTION_RECS: "Рекомендации",
}
SECTION_ORDER = (SECTION_BUGS, SECTION_ARCH, SECTION_RECS)

# Пределы, чтобы один огромный PR не уронил пайплайн ни таймаутом,
# ни переполнением контекста модели.
MAX_FILES = 40                  # файлов в сводке диффа
MAX_LINES_PER_SIDE = 120        # строк +/- на файл в сводке
MAX_CONTEXT_CHARS = 5_000       # окно диффа для поиска по корпусу
MAX_RAG_QUERY_TERMS = 14        # терминов на окно
MAX_RAG_SNIPPETS = 8            # фрагментов корпуса в промпте
# Потолок промпта. Найден живой проверкой: у Ollama окно по умолчанию
# 8192 токена, и большой PR упирался в HTTP 400 «exceeds the available
# context size». Порог измерен на реальной модели, а не угадан: русский
# текст и код в qwen3 дают ~0,9–2,2 токена на символ, поэтому 26 000
# символов по умолчанию — это ~6–7 тыс. токенов, запас под ответ есть.
# Если окно модели больше — поднять лимит: REVIEW_MAX_PROMPT_CHARS.
DEFAULT_MAX_PROMPT_CHARS = 26_000
# Приоритет частей промпта: при нехватке места режутся последние —
# сначала хвост диффа, потом фрагменты RAG. Всё усечение видно в
# отчёте (поле `prompt`), чтобы не было «тихого» уменьшения контекста.
_PROMPT_SHARE = {"meta": 0.06, "findings": 0.14, "diff": 0.44,
                 "rag": 0.26, "agents": 0.10}
MAX_LLM_ISSUES_PER_SECTION = 8

# Изменённые файлы, которые не участвуют в RAG и в проверке покрытия
# тестами, но остаются в сводке.
VENDORED_PREFIXES = ("node_modules/", ".venv/", "dist/", "build/",
                     "data/", "rag_upload/", "rag_export/", "studio/frontend/dist/")
LOCK_FILES = ("package-lock.json", "pnpm-lock.yaml", "yarn.lock",
              "poetry.lock", "uv.lock", "requirements.lock")

# Тип изменения файла как его понимает GitHub: added/modified/renamed/
# deleted. `DiffFile` разборщика хранит is_new/is_deleted, а «renamed»
# выводится по заголовку `rename from/to` — здесь это одна таблица.
_GITHUB_STATUS = {"added": "added", "modified": "modified",
                  "renamed": "renamed", "deleted": "deleted",
                  "copied": "copied", "changed": "modified"}

_RENAME_FROM_RE = re.compile(r"^(?:rename|copy) from (.+)$")
_RENAME_TO_RE = re.compile(r"^(?:rename|copy) to (.+)$")

# Как модель должна размечать секции — просим и заголовки, и XML-теги:
# разные модели охотнее соблюдают одно из двух.
_INSTRUCTION = """Готовое ревью должно состоять ровно из трёх разделов:
1. «## Потенциальные баги» — реальные дефекты, меняющие поведение;
   каждый пункт: расположение (файл:строка из диффа) — в чём баг —
   как воспроизвести/исправлять.
2. «## Архитектурные проблемы» — слои и границы модулей, дублирование,
   связанность, согласованность с ADR/спеками и существующим кодом
   (опирайтесь на фрагменты корпуса и найденные инварианты).
3. «## Рекомендации» — конкретные действия: тесты, документация,
   интеграция, упрощение; по возможности с учётом уже найденных
   детерминированных проблем.

Правила: ссылайтесь на файлы в форме `путь:строка`; опирайтесь ТОЛЬКО на
дифф, фрагменты корпуса и список находок; если про раздел нечего сказать
— так и напишите одной строкой («Потенциальных багов не вижу — …»).
Не добавляйте строки-разделители (`---`) и горизонтальные линейки:
разделы отчёта оформляет пайплайн, а не текст ответа."""


class PRError(AssistantError):
    """Ошибка пайплайна ревью PR (сообщение для человека)."""


# ---------------------------------------------------------------------------
# Изменённые файлы и сводка диффа
# ---------------------------------------------------------------------------

def _rename_map(diff: str) -> dict:
    """`{новый путь: старый путь}` из заголовков `rename from/to`.

    Переименование видно только в заголовках блока диффа, а не в
    `DiffFile` разборщика, поэтому собирается отдельным проходом.
    """
    out: dict[str, str] = {}
    old = ""
    for raw in (diff or "").splitlines():
        if raw.startswith("diff --git "):
            old = ""
            continue
        m = _RENAME_FROM_RE.match(raw)
        if m:
            old = A._strip_ab(m.group(1))
            continue
        m = _RENAME_TO_RE.match(raw)
        if m and old:
            out[A._strip_ab(m.group(1))] = old
            old = ""
    return out


def build_changed_files(diff: str) -> list[dict]:
    """Изменённые файлы из unified diff: путь, статус, +/- строк.

    Статус — `added` / `modified` / `renamed` / `deleted`, как его
    понимает GitHub. Переименование раскрывается парой
    `old_path → path`, чтобы ревьюер видел обе стороны.
    """
    renames = _rename_map(diff)
    numstat = _numstat(diff)
    out: list[dict] = []
    seen = set()
    for f in A.parse_unified_diff(diff):
        path = f.path
        if not path or path in seen:
            continue
        seen.add(path)
        added, removed = numstat.get(path, (f.added, f.removed))
        if f.is_new:
            status = "added"
        elif f.is_deleted:
            status = "deleted"
        elif path in renames:
            status = "renamed"
        else:
            status = "modified"
        out.append({"path": path, "status": status, "added": added,
                    "removed": removed, "old_path": renames.get(path)})
    return out


def _numstat(diff: str) -> dict:
    """`{path: (added, removed)}` из `diff --git` + `---`/`+++` заголовков.

    Отдельная лёгкая функция: `git diff --numstat` дал бы это сразу, но
    diff может прийти из API GitHub или из файла — заголовков достаточно.
    """
    out: dict[str, tuple] = {}
    path = None
    added = removed = 0

    def flush():
        if path is not None:
            out[path] = (added, removed)

    for raw in (diff or "").splitlines():
        if raw.startswith("diff --git "):
            flush()
            m = re.match(r"^diff --git a/(.+?) b/(.+)$", raw)
            path, added, removed = (A._strip_ab(m.group(2)) if m else None,
                                    0, 0)
        elif path is not None and raw.startswith("+++ "):
            p = A._strip_ab(raw[4:])
            if p != "/dev/null" and raw[4:] != "/dev/null":
                path = p
        elif path is not None and raw.startswith("+") \
                and not raw.startswith("+++ "):
            added += 1
        elif path is not None and raw.startswith("-") \
                and not raw.startswith("--- "):
            removed += 1
    flush()
    return out


def diff_file_paths(diff: str) -> list[str]:
    """Пути файлов из diff — для проверки «что мы ревьюим» без разбора."""
    return [p for p in _numstat(diff)]


def _changed_with_code(files: list) -> list:
    """Файлы, у которых есть строки изменений (бинарники не в промпт)."""
    return [f for f in files if f.added_lines or f.removed_lines]


def build_diff_summary(files: list, budget: int | None = None) -> tuple:
    """Текстовая сводка диффа для промпта → (текст, статистика).

    Файлы берутся в порядке появления в диффе до исчерпания `budget`
    символов (None — без предела); внутри файла строки обрезаются
    `MAX_LINES_PER_SIDE`. Возврат `(текст, {files, total_files,
    truncated_lines, budget_exhausted})` — статистика идёт в отчёт и
    показывает, был ли контекст урезан.
    """
    with_code = _changed_with_code(files)
    parts: list[str] = []
    used = 0
    cut_lines = 0
    shown = 0
    exhausted = False
    for f in with_code:
        if budget is not None and used >= budget:
            exhausted = True
            break
        status = ("added" if f.is_new else
                  "deleted" if f.is_deleted else "modified")
        extra = ""
        if f.is_new and len(f.added_lines) > MAX_LINES_PER_SIDE:
            extra = (f" (новый файл целиком, {len(f.added_lines)} строк — "
                     "приведён фрагмент)")
        head = f"- {f.path} ({status}, +{f.added}/-{f.removed}){extra}"
        plus = [d.text for d in f.added_lines]
        minus = [d.text for d in f.removed_lines]
        body = ["```diff"]
        for t in minus[:MAX_LINES_PER_SIDE]:
            body.append("- " + t)
        if len(minus) > MAX_LINES_PER_SIDE:
            body.append(f"- … ещё {len(minus) - MAX_LINES_PER_SIDE} "
                        "удалённых строк")
            cut_lines += 1
        for t in plus[:MAX_LINES_PER_SIDE]:
            body.append("+ " + t)
        if len(plus) > MAX_LINES_PER_SIDE:
            body.append(f"+ … ещё {len(plus) - MAX_LINES_PER_SIDE} "
                        "добавленных строк")
            cut_lines += 1
        body.append("```")
        piece = head + "\n" + "\n".join(body)
        if budget is not None and used + len(piece) > budget and parts:
            exhausted = True
            break
        parts.append(piece)
        used += len(piece)
        shown += 1
    hidden = len(with_code) - shown
    if hidden > 0:
        parts.append(f"… и ещё {hidden} файл(ов) с изменениями "
                     "не поместились в контекст — см. таблицу изменённых "
                     "файлов в отчёте")
    text = "\n".join(parts) if parts else "(нет строк с изменениями)"
    return text, {"files": shown, "total_files": len(with_code),
                  "truncated_lines": cut_lines,
                  "budget_exhausted": exhausted}


# ---------------------------------------------------------------------------
# RAG по документации и коду
# ---------------------------------------------------------------------------

# Служебные слова запроса: стоп-слова индекса (kb._STOPWORDS) плюс
# ключевые слова Python/JS — в диффе их сотни, и они не различают
# фрагменты («self», «return» есть в каждом модуле).
_STOPWORD_QUERY = set(getattr(A.kb_mod, "_STOPWORDS", ())) | {
    "self", "cls", "none", "true", "false", "return", "import", "from",
    "def", "class", "with", "print", "async", "await", "lambda", "pass",
    "raise", "yield", "const", "let", "var", "function", "export",
    "default", "type", "interface", "string", "number", "boolean",
    "else", "elif", "then", "that", "this",
}
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")


def _diff_windows(files: list, context_chars: int = MAX_CONTEXT_CHARS) -> list[str]:
    """Дифф → окна поиска по хункам (добавленные и удалённые строки).

    Окна склеиваются до `context_chars`: один запрос на окно — это
    шум, запрос на весь большой PR — размытый поиск. Пропускаются
    удалённые файлы и вендорный код.
    """
    chunks: list[str] = []
    for f in files:
        if f.is_deleted or _skip_for_rag(f.path):
            continue
        text = "\n".join([d.text for d in f.added_lines]
                         + [d.text for d in f.removed_lines])
        if text.strip():
            chunks.append((f.path, text))
    windows, cur, size = [], [], 0
    for path, text in chunks:
        piece = f"{path}\n{text}"
        if cur and size + len(piece) > context_chars:
            windows.append("\n\n".join(cur))
            cur, size = [], 0
        cur.append(piece)
        size += len(piece)
    if cur:
        windows.append("\n\n".join(cur))
    return windows or [""]


def _skip_for_rag(path: str) -> bool:
    p = path.replace("\\", "/")
    return (p in LOCK_FILES or p.endswith(".lock")
            or any(p.startswith(pref) for pref in VENDORED_PREFIXES))


def rag_query_terms(text: str, limit: int = MAX_RAG_QUERY_TERMS) -> list[str]:
    """Термины запроса из окна диффа: идентификаторы, пути, слова.

    В диффе много служебного (`def`, `self`, `return`) — их отсекают
    стоп-слова и длина; остаются имена функций, модулей и термины
    предметной области — по ним индекс и ищет документацию.
    """
    counts: dict[str, int] = {}
    for m in _IDENT_RE.finditer(text):
        word = m.group(0)
        low = word.lower()
        if low in _STOPWORD_QUERY or len(low) < 4:
            continue
        counts[word] = counts.get(word, 0) + 1
    ranked = sorted(counts, key=lambda w: (-counts[w], w))
    out: list[str] = []
    seen = set()
    for w in ranked:
        key = w.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(w)
        if len(out) >= limit:
            break
    return out


def retrieve_context(pkb, files: list, top_k_per_window: int = 4) -> list[dict]:
    """RAG: фрагменты корпуса, релевантные изменённому коду.

    Возвращает список `{file, section, score, text, why}` — `why` это
    путь изменённого файла, «из-за которого» фрагмент попал в контекст:
    без него модель не знает, с чем сопоставляется документация.
    Индекс не построен или пуст — пустой список (деградация, не падение).
    """
    try:
        if pkb.load_index() is None:
            return []
    except Exception:                               # noqa: BLE001
        return []
    out: list[dict] = []
    keys = set()
    for f in files:
        if f.is_deleted or _skip_for_rag(f.path):
            continue
        # Первый сигнал — имя модуля (kb.py ↔ KB, gateway.py ↔ шлюз), но
        # только для файлов с кодом: по одному слову вида «new»/«readme»
        # индекс возвращал случайные фрагменты (шум в промпте).
        stem = os.path.splitext(os.path.basename(f.path))[0]
        has_code = bool(f.added_lines or f.removed_lines)
        queries = [stem] if has_code and len(stem) >= 4 else []
        added = "\n".join(d.text for d in f.added_lines)
        queries += [" ".join(rag_query_terms(added))] * (1 if added else 0)
        for q in queries:
            if not q.strip():
                continue
            try:
                res = pkb.search_project(q, top_k=top_k_per_window)
            except Exception:                       # noqa: BLE001
                continue
            for h in res.get("results") or []:
                key = (h.get("file"), h.get("section"))
                if key in keys:
                    continue
                keys.add(key)
                out.append({"file": h.get("file"),
                            "section": h.get("section"),
                            "score": round(float(h.get("score") or 0), 6),
                            "text": (h.get("text") or "")[:1200],
                            "why": f.path, "query": q})
    out.sort(key=lambda x: -x["score"])
    return out[:MAX_RAG_SNIPPETS]


def prompt_agent_snippets(pkb, files: list, limit: int = 3) -> list[dict]:
    """Промпты агентов `agents/<name>.cli.md`, изменённые в PR или
    связанные с изменёнными файлами.

    Промпт агента — это тоже документация проекта (день 35), а рубрика
    ревью должна видеть, что PR меняет инструкцию модели: это
    архитектурное изменение, даже если код не тронут.
    """
    paths = []
    for f in files:
        p = f.path.replace("\\", "/")
        if p.startswith(A.AGENTS_DIRNAME + "/") and p.endswith(".md"):
            paths.append(p)
    # Плюс агенты, чьё имя встречается в изменённых файлах (правка
    # cli.py должна подсказать агенту, как устроен код-ревью).
    root = getattr(pkb, "repo_root", None)
    if not root:
        return []
    agents_dir = os.path.join(root, A.AGENTS_DIRNAME)
    if not os.path.isdir(agents_dir):
        return []
    names = {os.path.splitext(os.path.basename(p))[0].split(".")[0]
             for p in paths}
    changed_text = " ".join(f.path for f in files)
    out = []
    for fn in sorted(os.listdir(agents_dir)):
        if not fn.endswith(".md"):
            continue
        name = fn.split(".")[0]
        rel = f"{A.AGENTS_DIRNAME}/{fn}"
        if rel not in paths and name not in names \
                and name not in changed_text and len(out) >= limit:
            continue
        try:
            with open(os.path.join(agents_dir, fn), encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            continue
        out.append({"file": rel, "text": text[:1500],
                    "changed": rel in paths})
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# Детерминированные проверки (поверх check_diff)
# ---------------------------------------------------------------------------

def check_invariants_in_diff(files: list) -> list[dict]:
    """Инварианты проекта, сработавшие в ДОБАВЛЕННЫХ строках диффа.

    `assistant.check_invariants` проверяет файлы рабочего дерева целиком;
    для ревью PR нужно «что нового нарушил этот PR», поэтому правила
    применяются к виртуальному файлу из добавленных строк. Нарушения,
    унаследованные из базы, в PR не попадают — иначе ревью шумит.
    """
    findings = []
    for f in files:
        if f.is_deleted or not f.added_lines:
            continue
        text = "\n".join(d.text for d in f.added_lines)
        for code, desc, fn in A.INVARIANTS:
            try:
                hits = fn(f.path, text) or []
            except Exception:                       # noqa: BLE001
                continue
            if hits:
                findings.append({"rule": f"invariant:{code}",
                                 "severity": "medium",
                                 "message": (f"Инвариант проекта «{desc}» "
                                             "нарушен в изменённых строках"),
                                 "file": f.path, "line": None,
                                 "snippet": "; ".join(str(h)[:80]
                                                      for h in hits[:2])})
    return findings


def check_global(files: list) -> list[dict]:
    """Проверки, которые `check_diff` не делает: инварианты PR целиком."""
    out = []
    changed = {f.path for f in files}
    # Новая зависимость — повод проверить, что она нужна и объявлена.
    deps = [f.path for f in files if f.path.endswith(("requirements.txt",
                                                     "package.json",
                                                     "pyproject.toml"))]
    if deps and not any(p.endswith((".md",)) for p in changed):
        out.append({"rule": "deps_without_docs", "severity": "low",
                    "message": "Зависимости изменены без заметки в "
                               "документации — опишите, зачем нужна "
                               "новая зависимость (ADR/README)",
                    "file": deps[0], "line": None, "snippet": ""})
    # Смена пайплайна/CI — отдельный уровень внимания.
    ci = [f.path for f in files if f.path.startswith(".github/workflows/")]
    if ci:
        out.append({"rule": "ci_changed", "severity": "low",
                    "message": "Изменён CI-пайплайн — проверьте, что "
                               "секреты не попадают в логи и что новый "
                               "шаг не запускается на форках без "
                               "отдельного флага",
                    "file": ci[0], "line": None, "snippet": ""})
    return out


# ---------------------------------------------------------------------------
# Ревью моделью: промпт и разбор ответа
# ---------------------------------------------------------------------------

_SECTION_PATTERNS = {
    SECTION_BUGS: re.compile(
        r"(?im)^[ \t]{0,3}(?:#{1,6}[ \t]*)?(?:\*\*)?"
        r"(?:потенциальн\w*[ \t]+)?(?:баг\w*|дефект\w*)(?:\*\*)?[ \t]*:?[ \t]*$"),
    SECTION_ARCH: re.compile(
        r"(?im)^[ \t]{0,3}(?:#{1,6}[ \t]*)?(?:\*\*)?"
        r"архитектурн\w*(?:[ \t]+(?:проблем\w*|вопрос\w*|замечани\w*|"
        r"риск\w*))?(?:\*\*)?[ \t]*:?[ \t]*$"),
    SECTION_RECS: re.compile(
        r"(?im)^[ \t]{0,3}(?:#{1,6}[ \t]*)?(?:\*\*)?"
        r"(?:общие[ \t]+)?(?:рекомендаци\w*|совет\w*|предложени\w*|"
        r"что[ \t]+улучшить)(?:\*\*)?[ \t]*:?[ \t]*$"),
}
_TAG_RE = {
    SECTION_BUGS: re.compile(r"<bugs>(.*?)</bugs>", re.S | re.I),
    SECTION_ARCH: re.compile(r"<architecture>(.*?)</architecture>",
                             re.S | re.I),
    SECTION_RECS: re.compile(r"<recommendations>(.*?)</recommendations>",
                             re.S | re.I),
}
# Пункт, которым модель говорит «нечего сказать»: это не замечание, и в
# отчёте раздел должен остаться пустым (иначе «Багов не вижу» висит как
# найденный баг). Шаблон требует, чтобы отказ стоял в начале фразы:
# иначе «Индекс пустой, запрос не нужен» был бы принят за отказ.
_EMPTY_ITEM_RE = re.compile(
    r"^(?:[-–—*•]|\d+[.)])?\s*"
    r"(?:(?:потенциальных?|архитектурных?|других|дополнительных?|"
    r"явных?|критических?|серьёзных?|очевидных?)\s+)*"
    r"(?:баг\w*|проблем\w*|дефект\w*|замечани\w*|нарушени\w*|ошиб\w*)"
    r"\s+(?:\w+\s+){0,3}?"
    r"(?:не\s+(?:вижу|обнаружен\w*|найден\w*|выявлен\w*|выбрал\w*)"
    r"|нет\b|отсутств\w*)"
    r"|^(?:нет|не\s+вижу|не\s+найдено|не\s+обнаружено|не\s+выявлено)\b",
    re.I)

_BULLET_RE = re.compile(r"^[ \t]{0,3}([-*+•]|\d+[.)])[ \t]+(\S.*)$")


def parse_llm_review(text: str) -> dict:
    """Ответ модели → `{bugs|architecture|recommendations: [пункт, …]}`.

    Понимает три формы: XML-теги (`<bugs>…</bugs>`), markdown-заголовки
    («## Потенциальные баги») и — если формат не соблюдён вовсе — весь
    текст уходит в «Рекомендации». Ответ не теряется ни в одном случае:
    потерянный ответ хуже лишнего пункта.

    Заголовки ищутся только как отдельная строка целиком: иначе пункт
    «2. Архитектурная проблема: …» открыл бы секцию архитектуры.
    """
    out = {k: [] for k in SECTION_ORDER}
    text = (text or "").strip()
    if not text:
        return out

    # 1) XML-теги — самый однозначный формат.
    tagged = {k: _TAG_RE[k].search(text) for k in SECTION_ORDER}
    if any(tagged.values()):
        for k, m in tagged.items():
            if m:
                out[k] = _split_items(m.group(1))
        return _clean_sections(out)

    # 2) Markdown-заголовки: секция начинается с заголовка и идёт до
    # следующего заголовка в порядке появления.
    marks = []
    for k, rx in _SECTION_PATTERNS.items():
        for m in rx.finditer(text):
            marks.append((m.start(), m.end(), k))
    marks.sort()
    if marks:
        for i, (pos, end, k) in enumerate(marks):
            stop = marks[i + 1][0] if i + 1 < len(marks) else len(text)
            out[k].extend(_split_items(text[end:stop]))
        return _clean_sections(out)

    # 3) Формат не распознан — не теряем текст.
    out[SECTION_RECS] = _split_items(text)
    return _clean_sections(out)


def _split_items(block: str) -> list[str]:
    """Блок текста → пункты: список или строки.

    Немаркированная строка продолжает предыдущий пункт (модель часто
    переносит пункт на вторую строку), отдельный маркер без текста
    (`2.`) — не пункт, а хвост форматирования.
    """
    lines = [ln.rstrip() for ln in (block or "").splitlines()]
    has_bullets = any(_BULLET_RE.match(ln) for ln in lines)
    # Маркер без текста закрывает предыдущий пункт (иначе «2.» склеится
    # с текстом пункта 1 — так проверено в тесте).
    has_bare = any(re.fullmatch(r"\s*(?:[-*+•]|\d+[.)])\s*", ln)
                  for ln in lines)
    items: list[str] = []
    cur: list[str] = []

    def flush():
        if cur:
            items.append(" ".join(cur).strip())
        cur.clear()

    for ln in lines:
        if has_bare and re.fullmatch(r"\s*(?:[-*+•]|\d+[.)])\s*", ln):
            flush()
            continue
        if not ln.strip():
            flush()
            continue
        m = _BULLET_RE.match(ln)
        if m:
            flush()
            cur.append(m.group(2))
        else:
            cur.append(ln.strip())
    flush()
    if not has_bullets:
        items = [ln.strip() for ln in lines if ln.strip()]
    return [x for x in (_clean_item(i) for i in items) if x]


# Хвостовой разделитель, который модель оставила в пункте («… сбоем. - ---»):
# в комментарии PR это выглядит как битый markdown.
# Хвост пункта: одиночный или повторённый маркер (`-`, `---`, `—`) на
# конце. Тире внутри пункта («а — б») не трогается: правка режет только
# хвост, после которого ничего нет.
_DANGLING_SEP_RE = re.compile(r"(?:[ \t]*[-–—*_]{1,}[ \t]*|[ \t]*#+[ \t]*)+$")


def _clean_item(text: str) -> str:
    """Убрать хвостовые разделители/решётки из пункта, схлопнуть пробелы."""
    prev = None
    while prev != text:
        prev = text
        text = _DANGLING_SEP_RE.sub("", text).strip()
    return re.sub(r"\s+", " ", text).strip()


def _clean_sections(sections: dict) -> dict:
    """Убрать пустые и «нечего сказать» пункты, обрезать их число."""
    out = {}
    for k in SECTION_ORDER:
        items = []
        for it in sections.get(k) or []:
            it = it.strip()
            if len(it) < 2:
                continue
            if k != SECTION_RECS and _EMPTY_ITEM_RE.search(it):
                continue
            items.append(it)
        out[k] = items[:MAX_LLM_ISSUES_PER_SECTION]
    return out


def max_prompt_chars() -> int:
    """Потолок промпта ревью: env `REVIEW_MAX_PROMPT_CHARS` или дефолт."""
    try:
        value = int(os.environ.get("REVIEW_MAX_PROMPT_CHARS") or 0)
    except ValueError:
        value = 0
    return value if value >= 6_000 else DEFAULT_MAX_PROMPT_CHARS


def _clip(text: str, limit: int) -> tuple:
    """(текст не длиннее limit, сколько символов отрезано)."""
    if len(text) <= limit:
        return text, 0
    return text[:max(0, limit)].rsplit("\n", 1)[0] + "\n… (обрезано)", \
        len(text) - limit


def build_review_prompt(service, pr: dict, files: list, findings: list,
                        context: list, agent_snippets: list,
                        max_chars: int | None = None) -> tuple:
    """(system, user, agent_name, stats) — ровно то, что видит модель.

    Системный промпт — из `agents/pr-review.cli.md` (день 32, fallback
    `code-review`). Пользовательский собирается по частям с бюджетом:
    матаданные PR, дифф, находки, фрагменты корпуса, промпты агентов.
    Каждая часть урезается в своей доле бюджета, поэтому огромный PR и
    40 находок вместе всё равно влезают в окно модели. Усечение
    попадает в `stats` (и в отчёт), а не пропадает молча.
    """
    system, agent_name, _note = load_agent_prompt(service)
    budget = max_chars or max_prompt_chars()
    parts: list[str] = []
    stats = {"budget": budget, "truncated": {}, "diff": {}}

    meta = "\n".join(["## PR", json.dumps(pr, ensure_ascii=False,
                                           indent=2)])
    meta, cut = _clip(meta, int(budget * _PROMPT_SHARE["meta"]))
    parts.append(meta)
    if cut:
        stats["truncated"]["pr"] = cut

    diff_text, diff_stats = build_diff_summary(
        files, budget=int(budget * _PROMPT_SHARE["diff"]))
    stats["diff"] = diff_stats
    parts += ["", "## Изменённые файлы и дифф", diff_text]

    if findings:
        block = ["## Детерминированные находки (уже проверены кодом)"]
        for f in findings[:30]:
            loc = f.get("file") or "—"
            if f.get("line"):
                loc += f":{f['line']}"
            block.append(f"- [{f.get('severity')}] {f.get('rule')} {loc} — "
                         f"{f.get('message')}")
        if len(findings) > 30:
            block.append(f"… и ещё {len(findings) - 30} находок "
                         "(все — в отчёте)")
        text, cut = _clip("\n".join(block),
                          int(budget * _PROMPT_SHARE["findings"]))
        parts += ["", text]
        if cut:
            stats["truncated"]["findings"] = cut

    if context:
        block = ["## Фрагменты корпуса проекта (документация и код), "
                 "релевантные изменённым файлам"]
        for i, c in enumerate(context, 1):
            block.append(f"[{i}] {c['file']}"
                         + (f" :: {c['section']}" if c.get("section") else "")
                         + f" (связано с {c['why']})\n"
                         + (c.get("text") or "")[:1000])
        text, cut = _clip("\n".join(block), int(budget * _PROMPT_SHARE["rag"]))
        parts += ["", text]
        if cut:
            stats["truncated"]["rag"] = cut
            stats["rag_shown"] = sum(1 for line in text.splitlines()
                                     if line.startswith("["))
    else:
        stats["rag_shown"] = 0

    if agent_snippets:
        block = ["## Промпты агентов проекта (agents/*.md)"]
        for sn in agent_snippets:
            mark = " [изменён в PR]" if sn.get("changed") else ""
            block.append(f"### {sn['file']}{mark}\n"
                         + (sn.get("text") or "")[:1200])
        text, cut = _clip("\n".join(block),
                          int(budget * _PROMPT_SHARE["agents"]))
        parts += ["", text]
        if cut:
            stats["truncated"]["agents"] = cut

    parts += ["", "## Задача", _INSTRUCTION]
    user = "\n".join(parts)
    stats["user_chars"] = len(user)
    stats["system_chars"] = len(system)
    return system, user, agent_name, stats


def load_agent_prompt(service) -> tuple:
    """(system_prompt, agent_name, note) для ревью PR.

    Основной агент — `pr-review` (день 32). Его может не быть в
    старых ветках/форках — тогда берём `code-review`, и это видно в
    отчёте (`agent`/`agent_note`), а не молча.
    """
    reg = getattr(service, "agents", None)
    if reg is not None:
        for name in ("pr-review", A.AGENT_FOR_REVIEW):
            try:
                prompt, fallback = reg.prompt_for(name, execution="cli")
            except A.AgentError:
                continue
            note = ("нет cli-инструкции агента — использовано другое "
                    "исполнение" if fallback else "")
            return prompt or "", name, note
    try:
        prompt, note = service.agent_prompt(A.AGENT_FOR_REVIEW)
        return prompt or "", A.AGENT_FOR_REVIEW, note or ""
    except Exception:                               # noqa: BLE001
        return "", "—", "промпт агента не загружен (agents/*.md)"


# ---------------------------------------------------------------------------
# GitHub: API для диффа форков и публикация комментария
# ---------------------------------------------------------------------------

class GitHubClient:
    """Тонкий клиент REST API GitHub — только то, что нужно ревью.

    Используется, когда PR пришёл из форка: код его ветки в рабочем
    дереве CI недоступен, а diff и изменённые файлы отдаёт API.
    Публикация комментария — идемпотентная: повторный прогон обновляет
    прежний комментарий с маркером `MARKER`, а не плодит новые.
    """

    MARKER = "<!-- ai-pr-review -->"

    def __init__(self, token: str | None = None, api_url: str | None = None,
                 timeout: float = 30.0):
        self.token = token or os.environ.get("GITHUB_TOKEN") or ""
        self.api_url = (api_url or os.environ.get("GITHUB_API_URL")
                        or "https://api.github.com").rstrip("/")
        self.timeout = timeout

    def _request(self, method: str, path: str, body=None,
                 accept: str = "application/vnd.github+json"):
        import urllib.error
        import urllib.request
        url = self.api_url + path
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Accept", accept)
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        req.add_header("User-Agent", "ai-advent-pr-review")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return resp.status, raw
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, OSError) as e:
            raise PRError(f"GitHub API недоступен: {e}") from e

    def pull_files(self, repo: str, number: int) -> list:
        """Изменённые файлы PR (`GET /repos/{repo}/pulls/{n}/files`)."""
        out, page = [], 1
        while True:
            status, raw = self._request(
                "GET", f"/repos/{repo}/pulls/{number}/files?per_page=100"
                       f"&page={page}")
            if status != 200:
                raise PRError(f"GitHub: не удалось получить файлы PR "
                              f"(HTTP {status}): {raw[:200]}")
            try:
                batch = json.loads(raw)
            except ValueError as e:
                raise PRError(f"GitHub: некорректный JSON: {e}") from e
            out.extend(batch)
            if len(batch) < 100 or page >= 10:
                return out
            page += 1

    def pull_diff(self, repo: str, number: int) -> str:
        """Unified diff PR (`Accept: application/vnd.github.v3.diff`).

        Именно этот источник работает для форков: ветка PR в рабочем
        дереве отсутствует.
        """
        status, raw = self._request(
            "GET", f"/repos/{repo}/pulls/{number}",
            accept="application/vnd.github.v3.diff")
        if status != 200:
            raise PRError(f"GitHub: не удалось получить diff PR "
                          f"(HTTP {status}): {raw[:200]}")
        return raw

    def list_comments(self, repo: str, number: int) -> list:
        status, raw = self._request(
            "GET", f"/repos/{repo}/issues/{number}/comments?per_page=100")
        if status != 200:
            raise PRError(f"GitHub: не удалось прочитать комментарии "
                          f"(HTTP {status}): {raw[:200]}")
        try:
            return json.loads(raw)
        except ValueError:
            return []

    def upsert_comment(self, repo: str, number: int, body: str) -> dict:
        """Создать комментарий или обновить прежний с нашим маркером."""
        payload = body if self.MARKER in body else f"{self.MARKER}\n{body}"
        for c in self.list_comments(repo, number):
            if self.MARKER in (c.get("body") or ""):
                status, raw = self._request(
                    "PATCH", f"/repos/{repo}/issues/comments/{c['id']}",
                    {"body": payload})
                if status != 200:
                    raise PRError(f"GitHub: не удалось обновить "
                                  f"комментарий (HTTP {status})")
                try:
                    return json.loads(raw)
                except ValueError:
                    return {"id": c["id"]}
        status, raw = self._request(
            "POST", f"/repos/{repo}/issues/{number}/comments",
            {"body": payload})
        if status not in (200, 201):
            raise PRError(f"GitHub: не удалось создать комментарий "
                          f"(HTTP {status}): {raw[:200]}")
        try:
            return json.loads(raw)
        except ValueError:
            return {}


def github_context(env=None) -> dict | None:
    """Метаданные PR из окружения GitHub Actions (`GITHUB_EVENT_PATH`).

    None вне Actions. Ошибки чтения события не роняют ревью: это
    украшение заголовка, а не факт для проверок.
    """
    env = env or os.environ
    path = env.get("GITHUB_EVENT_PATH")
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            event = json.load(f)
    except (OSError, ValueError):
        return None
    pr = event.get("pull_request") or {}
    base = (pr.get("base") or {})
    head = (pr.get("head") or {})
    if not pr:
        return None
    return {
        "repository": env.get("GITHUB_REPOSITORY") or (event.get("repository")
                                                       or {}).get("full_name"),
        "number": pr.get("number") or event.get("number"),
        "title": pr.get("title"),
        "url": pr.get("html_url"),
        "base_ref": base.get("ref"), "base_sha": base.get("sha"),
        "head_ref": head.get("ref"), "head_sha": head.get("sha"),
        "from_fork": bool((head.get("repo") or {}).get("fork"))
        if head.get("repo") else (base.get("repo") is None),
        "draft": bool(pr.get("draft")),
        "additions": pr.get("additions"), "deletions": pr.get("deletions"),
        "changed_files": pr.get("changed_files"),
    }


# ---------------------------------------------------------------------------
# Пайплайн и отчёт
# ---------------------------------------------------------------------------

def load_diff(diff_text: str | None = None, base: str | None = None,
              git=None) -> tuple:
    """Дифф PR из источника → (текст, source).

    Порядок: явный текст → `git diff base...HEAD` → рабочее дерево.
    Пустой переданный текст — ошибка вызывающего (как в
    `AssistantService.review_diff`): молча подменять его git-изменениями
    нельзя, иначе на PR «без изменений» ревьюится чужой рабочий стол.
    """
    if diff_text is not None:
        if not diff_text.strip():
            raise PRError("Передан пустой diff — ревьюить нечего")
        return diff_text, "text"
    if git is None:
        raise PRError("Нет источника diff: передайте diff/base или git")
    if base:
        text = git.diff_branch(base)
        if not text.strip():
            raise PRError(f"Пустой diff: ветка `{base}` не отличается от "
                          "HEAD (проверьте fetch-depth и base)")
        return text, f"git:{base}...HEAD"
    text = git.diff()
    if not text.strip():
        raise PRError("Пустой diff: в рабочем дереве нет изменений")
    return text, "git:working-tree"


def run_pr_review(service, diff_text: str | None = None,
                  base: str | None = None, pr: dict | None = None,
                  with_llm: bool = True, top_k_rag: int = 4) -> dict:
    """Полный пайплайн ревью PR: diff → RAG → проверки → LLM → отчёт.

    Возвращает один словарь — он же JSON-отчёт:
    `{pr, source, changed_files, summary, findings, invariants,
      corpus_context, agent_context, review{bugs,architecture,
      recommendations}, llm, llm_error, agent, agent_note}`.
    """
    text, source = load_diff(diff_text, base, git=service.git)
    files = A.parse_unified_diff(text)
    if not files:
        raise PRError("Не удалось разобрать diff: ожидается формат unified "
                      "diff (git diff)")

    changed = build_changed_files(text)

    findings = A.sort_findings(A.check_diff(files))
    invariants = check_invariants_in_diff(files)
    # Инварианты — дальше только в отчёте (они не входят в счёт
    # `check_diff`, чтобы веб-панель дня 30 показывала те же цифры).
    findings += check_global(files)
    findings = A.sort_findings(findings + invariants)
    summary = A.summarize_findings(findings)

    context = retrieve_context(service.pkb, files, top_k_per_window=top_k_rag)
    agent_ctx = prompt_agent_snippets(service.pkb, files)

    pr_meta = dict(pr or {})
    pr_meta.setdefault("files", len(changed))
    pr_meta.setdefault("added", sum(c["added"] for c in changed))
    pr_meta.setdefault("removed", sum(c["removed"] for c in changed))

    review = {k: [] for k in SECTION_ORDER}
    llm_used = False
    llm_error = None
    agent_name = "pr-review"
    agent_note = ""
    prompt_stats = None
    if with_llm and service.agent is not None:
        system, user, agent_name, prompt_stats = build_review_prompt(
            service, pr_meta, files, findings, context, agent_ctx)
        if not system.strip():
            llm_error = "системный промпт агента не загружен (agents/*.md)"
        else:
            raw = service._llm(system, user, max_tokens=1400)
            if raw:
                review = parse_llm_review(raw)
                llm_used = True
            else:
                llm_error = service._llm_error or "модель не ответила"
    elif with_llm:
        llm_error = ("LLM не подключена (нет конфигурации модели) — "
                     "отчёт построен детерминированно")
    else:
        llm_error = "LLM отключена флагом --no-llm"

    recs = review[SECTION_RECS]
    if not llm_used:
        # Без модели рекомендации берём из детерминированного слоя.
        recs = recs or [r.get("advice") for r in
                        A.AssistantService._review_recommendations(findings)]
        review[SECTION_RECS] = [r for r in recs if r]

    changed_summary = [{"path": c["path"], "status": c["status"],
                        "added": c["added"], "removed": c["removed"],
                        "old_path": c.get("old_path")}
                       for c in changed]
    return {
        "pr": pr_meta,
        "source": source,
        "agent": agent_name,
        "agent_note": agent_note,
        "changed_files": changed_summary,
        "summary": {"files": len(changed),
                    "added": sum(c["added"] for c in changed),
                    "removed": sum(c["removed"] for c in changed),
                    "findings": summary["total"],
                    "by_severity": summary["by_severity"]},
        "findings": findings,
        "corpus_context": context,
        "agent_context": agent_ctx,
        "review": review,
        "prompt": prompt_stats,
        "llm": llm_used,
        "llm_error": None if llm_used else llm_error,
    }


# ---------------------------------------------------------------------------
# Отчёт
# ---------------------------------------------------------------------------

_SEV_RU = {"high": "критично", "medium": "средне", "low": "низко"}
_STATUS_RU = {"added": "добавлен", "modified": "изменён",
              "deleted": "удалён", "renamed": "переименован",
              "copied": "копия"}


def _signature(res: dict) -> str:
    """Одна строка «что ревьюили» — для заголовка и терминала."""
    pr = res.get("pr") or {}
    s = res.get("summary") or {}
    num = pr.get("number")
    prefix = f"PR #{num} — " if num else ""
    return (f"{prefix}{s.get('files', 0)} файл(ов), "
            f"+{s.get('added', 0)}/−{s.get('removed', 0)}, "
            f"находок {s.get('findings', 0)}")


def render_markdown(res: dict) -> str:
    """Отчёт в markdown: комментарий в PR, артефакт, терминал."""
    pr = res.get("pr") or {}
    s = res.get("summary") or {}
    by = s.get("by_severity") or {}
    lines = []
    title_num = f"PR #{pr['number']}" if pr.get("number") else "Ревью PR"
    lines.append(f"## 🤖 AI-ревью: {title_num}")
    if pr.get("title"):
        lines.append(f"**{pr['title']}**")
    lines.append("")
    lines.append(f"Изменено **{s.get('files', 0)}** файл(ов): "
                 f"+{s.get('added', 0)} / −{s.get('removed', 0)}. "
                 f"Детерминированных находок: **{s.get('findings', 0)}** "
                 f"(критично {by.get('high', 0)}, средне "
                 f"{by.get('medium', 0)}, низко {by.get('low', 0)}).")
    llm_line = ("🤖 Разделы 1–3 сформулировала модель "
                f"`{res.get('agent')}`." if res.get("llm")
                else f"⚠️ Ревью моделью недоступно: {res.get('llm_error')}. "
                     "Отчёт построен детерминированно (факты и "
                     "рекомендации по правилам проекта).")
    lines.append("")
    lines.append(llm_line)
    if res.get("agent_note"):
        lines.append(f"> {res['agent_note']}")

    # 0. Изменённые файлы — с ними начинается любой разбор PR.
    changed = res.get("changed_files") or []
    lines += ["", "### Изменённые файлы", ""]
    lines.append("| Файл | Статус | + | − |")
    lines.append("|---|---|---|---|")
    for c in changed[:MAX_FILES]:
        path = c["path"]
        if c.get("old_path"):
            path = f"{c['old_path']} → {path}"
        lines.append(f"| `{path}` | {_STATUS_RU.get(c['status'], c['status'])} "
                     f"| {c.get('added', 0)} | {c.get('removed', 0)} |")
    if len(changed) > MAX_FILES:
        lines.append(f"| … ещё {len(changed) - MAX_FILES} файл(ов) | | | |")

    # 1–3. Три обещанных блока.
    review = res.get("review") or {}
    for k in SECTION_ORDER:
        items = review.get(k) or []
        lines += ["", f"### {SECTION_TITLES[k]}", ""]
        if items:
            lines += [f"- {it}" for it in items]
        else:
            empty = {
                SECTION_BUGS: "Потенциальных багов не выявлено: "
                              "детерминированные проверки и модель "
                              "ничего не нашли.",
                SECTION_ARCH: "Архитектурных проблем не выявлено.",
                SECTION_RECS: "Дополнительных рекомендаций нет — "
                              "см. находки ниже.",
            }[k]
            lines.append(empty)
        if not res.get("llm") and k in (SECTION_BUGS, SECTION_ARCH):
            lines.append("")
            lines.append("_Раздел без модели не строится: модель "
                         "недоступна, факты ниже собраны правилами._")

    # Детерминированные находки — доказательная база.
    findings = res.get("findings") or []
    lines += ["", "### Проверки проекта (детерминированно)", ""]
    if findings:
        for f in findings[:40]:
            loc = f.get("file") or "—"
            if f.get("line"):
                loc += f":{f['line']}"
            lines.append(f"- [{_SEV_RU.get(f.get('severity'), '?')}] "
                         f"`{loc}` — {f.get('message')}")
        if len(findings) > 40:
            lines.append(f"- … ещё {len(findings) - 40} находк(и)")
    else:
        lines.append("Нарушений правил в изменённых строках нет.")

    # RAG: чем модель опиралась на документацию и код.
    ctx = res.get("corpus_context") or []
    lines += ["", "<details><summary>RAG: документация и код, на которые "
                  "опиралось ревью</summary>", ""]
    if ctx:
        for c in ctx:
            lines.append(f"- `{c['file']}`"
                         + (f" :: {c['section']}" if c.get("section") else "")
                         + f" — связано с `{c['why']}`")
    else:
        lines.append("Индекс корпуса не построен или фрагментов не "
                     "нашлось.")
    if res.get("agent_context"):
        lines += ["", "Промпты агентов проекта: "
                  + ", ".join(f"`{a['file']}`"
                              + (" (изменён в PR)" if a.get("changed")
                                 else "")
                              for a in res["agent_context"])]
    lines += ["", "</details>", ""]
    lines.append("---")
    lines.append(f"<sub>AI PR Review · источник diff: `{res.get('source')}` · "
                 "ассистент: RAG по корпусу проекта doc+code · "
                 "нарушения правил проверяет код, формулирует модель</sub>")
    return "\n".join(lines)


def render_step_summary(res: dict) -> str:
    """Короткая markdown-сводка для `$GITHUB_STEP_SUMMARY`."""
    s = res.get("summary") or {}
    by = s.get("by_severity") or {}
    lines = [f"### AI-ревью: {_signature(res)}", "",
             "| блок | пунктов |", "|---|---|"]
    for k in SECTION_ORDER:
        lines.append(f"| {SECTION_TITLES[k]} | {len((res.get('review') or {})
                                                   .get(k) or [])} |")
    lines.append(f"| Детерминированные находки | {s.get('findings', 0)} "
                 f"(критично {by.get('high', 0)}) |")
    lines.append("")
    lines.append("LLM: " + ("разделы сформулировала модель"
                            if res.get("llm")
                            else f"недоступна — {res.get('llm_error')}"))
    return "\n".join(lines)


def annotations(res: dict, max_items: int = 20) -> list[str]:
    """Workflow-команды `::warning file=…,line=…` для аннотаций PR.

    Находки уровня high — `error`, остальные — `warning`: вкладка
    «Files changed» показывает их на саму строку диффа.
    """
    out = []
    for f in (res.get("findings") or [])[:max_items]:
        level = "error" if f.get("severity") == "high" else "warning"
        msg = f.get("message", "").replace("\n", " ")
        loc = f.get("file") or ""
        props = f"file={loc}" if loc else ""
        if f.get("line"):
            props += f",line={f['line']}"
        out.append(f"::{level} {props}::{msg}" if props
                   else f"::{level}::{msg}")
    if not res.get("llm") and res.get("llm_error"):
        out.append(f"::notice::AI-ревью без модели: {res['llm_error']}")
    return out


# ---------------------------------------------------------------------------
# Предполётная проверка (что проверено и откуда)
# ---------------------------------------------------------------------------

def preflight(service, github: dict | None = None, diff_text: str | None = None,
              with_llm: bool = True) -> dict:
    """Что доступно пайплайну: индекс, модель, git, GitHub, источник diff.

    Печатается первым шагом Action'а: если ревью «пустое», по этому
    отчёту сразу видно почему — нет индекса, нет секрета, нет модели.
    """
    idx = None
    try:
        idx = service.pkb.load_index()
    except Exception:                               # noqa: BLE001
        idx = None
    stats = (idx or {}).get("stats") or {}
    llm_ok = service.agent is not None and with_llm
    llm_model = None
    if service.agent is not None:
        try:
            llm_model = service.agent.get_config().get("model")
        except Exception:                           # noqa: BLE001
            llm_model = None
    out = {
        "rag": {"indexed": idx is not None,
                "documents": stats.get("docs", 0),
                "chunks": stats.get("chunks", 0),
                "embedder": (idx or {}).get("embedder"),
                "strategy": (idx or {}).get("strategy")},
        "llm": {"enabled": llm_ok, "model": llm_model,
                "backend": os.environ.get("LLM_BACKEND") or "auto"},
        "git": {"available": service.git.available()},
        "github": {"context": github,
                   "token": bool(os.environ.get("GITHUB_TOKEN"))},
        "diff_source": ("text" if diff_text is not None else
                        "git" if service.git.available() else None),
        "agents": [a.get("name") + "." + a.get("execution")
                   for a in service.agents.list()],
        "cheap_model": bool(os.environ.get("CHEAP_MODEL")),
    }
    return out


def render_preflight(pf: dict) -> str:
    """Человекочитаемая версия preflight — для лога и summary."""
    rag = pf["rag"]
    lines = []
    if rag["indexed"]:
        lines.append(f"✅ RAG: индекс проекта — {rag['documents']} док., "
                     f"{rag['chunks']} чанков ({rag['embedder']})")
    else:
        lines.append("❌ RAG: индекс проекта не построен — ревью без "
                     "документации (запустите cli.py index)")
    llm = pf["llm"]
    if llm["enabled"]:
        lines.append(f"✅ LLM: модель `{llm['model']}` (backend "
                     f"{llm['backend']})")
    else:
        lines.append("⚠️ LLM: недоступна — детерминированный отчёт")
    lines.append(f"{'✅' if pf['git']['available'] else '⚠️'} git: "
                 f"{'доступен' if pf['git']['available'] else 'нет'}")
    gh = pf["github"]
    if gh.get("context"):
        c = gh["context"]
        lines.append(f"✅ GitHub: PR #{c.get('number')} "
                     f"{c.get('base_ref')} ← {c.get('head_ref')}"
                     + (" (форк — diff через API)" if c.get("from_fork")
                        else ""))
    else:
        lines.append("ℹ️ GitHub: событие PR не найдено (локальный запуск)")
    return "\n".join(lines)
