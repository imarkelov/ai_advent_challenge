"""Дни 30–34 — единый AI-ассистент разработчика и поддержки.

Связывает уже построенные слои Студии в один инструмент:

* **А. Вопросы по проекту** (`ask_project`) — RAG по документации и
  коду проекта (README, `docs/`, `openspec/`, `studio/backend/**`) с
  цитатами и источниками;
* **Б. Ревью изменений** (`review_diff`) — разбор unified diff,
  детерминированные проверки (баги/стиль/безопасность/покрытие
  тестами) и сопоставление с документацией; отчёт работает **без
  LLM**;
* **В. Поддержка** (`support_answer`) — ответ по тикету с
  персонализацией из CRM (`data/crm/users.json`,
  `data/crm/tickets.json`), контекст виден в ответе;
* **Г. Автономные файловые сценарии** (`run_scenario`) — 5 сценариев
  (`find_usages`, `update_docs`, `generate`, `check_invariants`,
  `diff_report`), задаваемых **целью**, а не пошаговой инструкцией,
  с записью только в allow-list путей проекта.

Принципы (см. `openspec/changes/day30-34-dev-assistant/design.md`):

1. **Индекс проекта отдельный** (`data/kb/project.db`) — загрузки
   пользователя дня 21 (`data/kb/index.db`) не смешиваются с кодом и
   документацией проекта.
2. **Факты собирает код, LLM только формулирует.** Ревью и поиск
   использований детерминированы и работают офлайн; LLM повышает
   качество текста, но не является условием работоспособности.
3. **Никаких git-мутаций.** Читаем git (`status`/`diff`/`log`), пишем
   файлы в рабочее дерево; коммит — за человеком.
4. **Запись только в allow-list** (`README.md`, `docs/`, `openspec/`,
   `CHANGELOG.md`), с проверкой выхода за пределы проекта.

Модуль импортируется без побочных эффектов: сеть и диск при импорте
не трогаются.
"""
import difflib
import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

try:                                    # пакет: studio.backend.assistant
    from . import kb as kb_mod
    from .kb import (CorpusDoc, HashEmbedder, KnowledgeBase, KBError,
                     read_json)
except ImportError:                     # flat: backend-каталог в sys.path
    import kb as kb_mod
    from kb import (CorpusDoc, HashEmbedder, KnowledgeBase, KBError,
                    read_json)

# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

PROJECT_INDEX_NAME = "project.db"      # отдельно от index.db дня 21
PROJECT_KB_SUBDIR = "project"          # data/kb/project/ (свой kb_dir)
MAX_FILE_BYTES = 400_000               # файл больше — в корпус не берём
MAX_LINE_LEN = 100                     # порог длинной строки в ревью
MAX_DIFF_CHARS = 400_000               # cap на разбираемый diff

# Каталоги, которые НИКОГДА не попадают в корпус проекта.
EXCLUDE_DIRS = {
    ".git", ".venv", ".venv-mlx", "node_modules", "__pycache__",
    ".pytest_cache", ".idea", ".vscode", "dist", "build", ".omo",
    ".mypy_cache", ".ruff_cache", "data", ".next", "coverage",
}
EXCLUDE_SUFFIXES = {
    ".db", ".sqlite", ".sqlite3", ".pyc", ".pyo", ".so", ".dylib",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip",
    ".gz", ".tar", ".lock", ".woff", ".woff2", ".ttf", ".otf", ".map",
    ".min.js", ".min.css", ".log", ".bin", ".onnx", ".safetensors",
}

# Явные правила корпуса проекта: (относительный путь или каталог, источник).
CORPUS_RULES = [
    ("README.md", "docs"),
    ("RELEASE.md", "docs"),
    ("LINKS.md", "docs"),
    ("docs", "docs"),
    ("openspec", "spec"),
    ("studio/backend", "code"),
    ("studio/frontend/src", "code"),
    ("scripts", "code"),
]

CORPUS_EXTS = {".md", ".py", ".ts", ".tsx", ".js", ".jsx", ".txt",
               ".json", ".yaml", ".yml", ".css"}

# Пути, в которые сценариям разрешено писать (относительно корня проекта).
WRITE_ALLOWLIST = ("README.md", "CHANGELOG.md", "docs/", "openspec/")

# Маркеры в тексте, размечающие автогенерируемые секции (идемпотентность).
AUTOGEN_BEGIN = "<!-- assistant:autogen:begin -->"
AUTOGEN_END = "<!-- assistant:autogen:end -->"


class AssistantError(Exception):
    """Ошибка ассистента (RU-сообщение)."""


class ScenarioError(AssistantError):
    """Ошибка сценария (неизвестный сценарий, запись вне allow-list)."""


# ---------------------------------------------------------------------------
# Корпус проекта
# ---------------------------------------------------------------------------

def _is_excluded(rel: str) -> bool:
    """Путь исключён из корпуса (каталог из EXCLUDE_DIRS или расширение)."""
    parts = rel.replace(os.sep, "/").split("/")
    if any(p in EXCLUDE_DIRS for p in parts[:-1]):
        return True
    low = rel.lower()
    for suf in EXCLUDE_SUFFIXES:
        if low.endswith(suf):
            return True
    return False


class ProjectCorpus:
    """Документы проекта для RAG: README/RELEASE/LINKS, `docs/`,
    `openspec/`, код бэкенда, фронтенда и скриптов.

    Отличие от `KnowledgeBase.corpus_files()` (только загрузки
    пользователя) — здесь корпус собирается по явным правилам с
    жёсткими исключениями (`.venv`, `node_modules`, `.git`, `data/`,
    бинарники) и лимитом размера файла.
    """

    def __init__(self, repo_root: str, rules=None,
                 max_file_bytes: int = MAX_FILE_BYTES):
        self.repo_root = os.path.abspath(repo_root)
        self.rules = list(rules if rules is not None else CORPUS_RULES)
        self.max_file_bytes = max_file_bytes

    def _read(self, path: str) -> str | None:
        """Чтение файла: utf-8 → cp1251 → utf-8/replace (как kb._read_file).
        Слишком большой файл или ошибка ввода-вывода — None."""
        try:
            if os.path.getsize(path) > self.max_file_bytes:
                return None
            with open(path, "rb") as f:
                raw = f.read()
        except OSError:
            return None
        for enc in ("utf-8", "cp1251"):
            try:
                return raw.decode(enc).replace("\r\n", "\n")
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")

    def files(self) -> list[str]:
        """Относительные пути файлов корпуса (отсортированы)."""
        out: list[str] = []
        for rel, _source in self.rules:
            full = os.path.join(self.repo_root, rel)
            if os.path.isfile(full):
                if not _is_excluded(rel):
                    out.append(rel)
                continue
            if not os.path.isdir(full):
                continue
            for dirpath, dirnames, filenames in os.walk(full):
                # Обрезаем исключённые каталоги прямо в обходе — иначе
                # .venv с десятками тысяч файлов всё равно обойдётся.
                dirnames[:] = sorted(d for d in dirnames
                                     if d not in EXCLUDE_DIRS)
                for fn in sorted(filenames):
                    ext = os.path.splitext(fn)[1].lower()
                    if ext not in CORPUS_EXTS:
                        continue
                    frel = os.path.relpath(os.path.join(dirpath, fn),
                                           self.repo_root)
                    frel = frel.replace(os.sep, "/")
                    if _is_excluded(frel):
                        continue
                    # Лимит размера применяется и здесь: `files()` —
                    # публичный список корпуса, и он должен совпадать с
                    # тем, что реально попадёт в индекс (`documents`).
                    try:
                        if os.path.getsize(os.path.join(dirpath, fn)) \
                                > self.max_file_bytes:
                            continue
                    except OSError:
                        continue
                    out.append(frel)
        return sorted(set(out))

    def documents(self) -> list[CorpusDoc]:
        """Документы корпуса с источниками (docs / code / spec)."""
        src_of = {}
        for rel, source in self.rules:
            src_of[rel.replace(os.sep, "/")] = source

        def source_for(rel: str) -> str:
            if rel in src_of:
                return src_of[rel]
            for rule_rel, source in self.rules:
                r = rule_rel.replace(os.sep, "/").rstrip("/")
                if rel == r or rel.startswith(r + "/"):
                    return source
            return "code"

        docs: list[CorpusDoc] = []
        for rel in self.files():
            text = self._read(os.path.join(self.repo_root, rel))
            if text is None or not text.strip():
                continue
            docs.append(CorpusDoc(rel, text, source_for(rel)))
        return docs

    def stats(self) -> dict:
        """Сводка корпуса: файлов, символов, разбивка по источникам."""
        docs = self.documents()
        by_source: dict[str, int] = {}
        for d in docs:
            by_source[d.source] = by_source.get(d.source, 0) + 1
        return {"files": len(docs),
                "chars": sum(len(d.text) for d in docs),
                "by_source": by_source}


class ProjectKnowledgeBase(KnowledgeBase):
    """`KnowledgeBase` с корпусом проекта вместо загрузок пользователя.

    Переиспользует весь пайплайн дня 21 (chunking, эмбеддеры, SQLite-
    индекс, гибридный поиск) — переопределены `corpus_files` и
    `_chunks_out`. Индекс лежит в отдельном каталоге
    (`data/kb/project/`), поэтому `data/kb/index.db` дня 21 не
    затрагивается.
    """

    def __init__(self, repo_root: str, kb_root: str | None = None):
        self.repo_root = os.path.abspath(repo_root)
        if kb_root is None:
            kb_root = os.path.join(self.repo_root, "data", "kb")
        proj_dir = os.path.join(kb_root, PROJECT_KB_SUBDIR)
        super().__init__(proj_dir, repo_root=self.repo_root)
        self.corpus = ProjectCorpus(self.repo_root)

    def corpus_files(self) -> list[CorpusDoc]:
        return self.corpus.documents()

    def _chunks_out(self, chunks, vecs, strategy: str) -> list:
        """Сериализация чанков с УНИКАЛЬНЫМ chunk_id по полному пути.

        `kb.py` строит `chunk_id` из basename-stem
        (`{stem}-{strategy}-{i:04d}`). Для корпуса дня 21 (плоские
        имена в `uploads/`) это безопасно, но в корпусе проекта есть
        `openspec/**/design.md` (18 файлов с одинаковым stem) — ID
        совпадают и `INSERT` падает на PRIMARY KEY. Поэтому здесь ID
        строится из пути целиком (слэши → `_`), что уникально по
        построению. `kb.py` не меняется: поведение дня 21 не
        затрагивается.
        """
        counters: dict[str, int] = {}
        out = []
        for c, v in zip(chunks, vecs):
            i = counters.get(c.file, 0)
            counters[c.file] = i + 1
            slug = re.sub(r"[^A-Za-z0-9]+", "_", c.file).strip("_")
            out.append({
                "chunk_id": f"{slug}-{strategy}-{i:04d}",
                "source": c.source, "file": c.file, "section": c.section,
                "chars": len(c.text), "text": c.text,
                "vector": [round(x, 6) for x in v],
                "terms": dict(sorted(kb_mod._term_freq(c.text).items())),
            })
        return out

    def build_project(self, strategy: str = "structural",
                      embedder=None, mode: str = "full") -> dict:
        """Собрать индекс проекта (по умолчанию hash-эмбеддер — офлайн)."""
        if embedder is None:
            embedder = HashEmbedder()
        return self.build(strategy, embedder, mode=mode)

    def search_project(self, query: str, top_k: int = 5,
                       recall: int = 30) -> dict:
        """Гибридный поиск по корпусу проекта с цитатами."""
        results = self.search_rag(query, recall=recall, top_k=top_k)
        return results


# ---------------------------------------------------------------------------
# CRM: тикеты и пользователи
# ---------------------------------------------------------------------------

class TicketStore:
    """Источник тикетов и пользователей (`data/crm/*.json`).

    Устойчив к отсутствию файлов: пустой каталог — не исключение, а
    пустой список (ответ поддержки деградирует до общего ответа по
    базе знаний с `found: false`).
    """

    def __init__(self, repo_root: str, crm_dir: str | None = None):
        self.repo_root = os.path.abspath(repo_root)
        self.crm_dir = crm_dir or os.path.join(self.repo_root, "data", "crm")

    def _load(self, name: str, key: str) -> list:
        doc = read_json(os.path.join(self.crm_dir, name), None)
        if not isinstance(doc, dict):
            return []
        items = doc.get(key)
        return items if isinstance(items, list) else []

    def users(self) -> list:
        return [u for u in self._load("users.json", "users")
                if isinstance(u, dict)]

    def tickets(self) -> list:
        return [t for t in self._load("tickets.json", "tickets")
                if isinstance(t, dict)]

    def user(self, user_id: str) -> dict | None:
        for u in self.users():
            if u.get("id") == user_id:
                return u
        return None

    def ticket(self, ticket_id: str) -> dict | None:
        for t in self.tickets():
            if t.get("id") == ticket_id:
                return t
        return None

    def list_tickets(self, status: str | None = None,
                     priority: str | None = None,
                     topic: str | None = None) -> list:
        """Список тикетов с фильтрами (None — без фильтра)."""
        out = []
        for t in self.tickets():
            if status and t.get("status") != status:
                continue
            if priority and t.get("priority") != priority:
                continue
            if topic and t.get("topic") != topic:
                continue
            out.append(t)
        return out

    def context(self, ticket_id: str) -> dict:
        """Контекст для персонализации: тикет + пользователь + его
        история обращений. `found: false`, если тикета нет."""
        t = self.ticket(ticket_id)
        if t is None:
            return {"found": False, "ticket_id": ticket_id,
                    "reason": f"Тикет {ticket_id} не найден в CRM"}
        user = self.user(t.get("user_id") or "")
        history = [x for x in self.tickets()
                   if x.get("user_id") == (t.get("user_id") or "")
                   and x.get("id") != t.get("id")]
        return {"found": True, "ticket": t, "user": user,
                "history": history, "history_count": len(history)}

    def stats(self) -> dict:
        tickets = self.tickets()
        by_status: dict[str, int] = {}
        for t in tickets:
            s = t.get("status") or "unknown"
            by_status[s] = by_status.get(s, 0) + 1
        return {"users": len(self.users()), "tickets": len(tickets),
                "by_status": by_status, "available": bool(tickets)}


# ---------------------------------------------------------------------------
# Git (только чтение)
# ---------------------------------------------------------------------------

class GitReader:
    """Чтение git-контекста: ветка, изменённые файлы, diff, лог.

    Только чтение — мутаций (commit/push/checkout) здесь нет
    сознательно: ассистент готовит изменения, решение о коммите
    остаётся за человеком. Отсутствие git или репозитория не роняет
    вызывающий код: `available: false`.
    """

    def __init__(self, repo_root: str, timeout: float = 20.0):
        self.repo_root = os.path.abspath(repo_root)
        self.timeout = timeout

    def _run(self, args: list) -> tuple:
        """(код возврата, stdout, stderr); git недоступен → (-1, "", err)."""
        try:
            p = subprocess.run(["git"] + args, cwd=self.repo_root,
                               capture_output=True, text=True,
                               timeout=self.timeout)
            return p.returncode, p.stdout, p.stderr
        except (OSError, subprocess.SubprocessError) as e:
            return -1, "", str(e)

    def available(self) -> bool:
        code, out, _ = self._run(["rev-parse", "--is-inside-work-tree"])
        return code == 0 and out.strip() == "true"

    def branch(self) -> str | None:
        code, out, _ = self._run(["rev-parse", "--abbrev-ref", "HEAD"])
        return out.strip() if code == 0 else None

    def changed_files(self) -> dict:
        """Изменённые/неотслеживаемые файлы рабочего дерева."""
        code, out, _ = self._run(["status", "--porcelain"])
        if code != 0:
            return {"tracked": [], "untracked": []}
        tracked, untracked = [], []
        for line in out.splitlines():
            if not line.strip():
                continue
            status, _, path = line[:2], line[2:3], line[3:].strip()
            (untracked if status.strip() == "??" else tracked).append(path)
        return {"tracked": tracked, "untracked": untracked}

    def diff(self, staged: bool = False) -> str:
        args = ["diff"]
        if staged:
            args.append("--cached")
        code, out, _ = self._run(args)
        return out if code == 0 else ""

    def diff_branch(self, base: str) -> str:
        """diff текущей ветки относительно `base` (двухточечный)."""
        code, out, _ = self._run(["diff", f"{base}...HEAD"])
        return out if code == 0 else ""

    def log(self, limit: int = 20) -> list:
        code, out, _ = self._run(["log", f"-{int(limit)}",
                                  "--pretty=format:%h|%ad|%s",
                                  "--date=short"])
        if code != 0:
            return []
        commits = []
        for line in out.splitlines():
            parts = line.split("|", 2)
            if len(parts) == 3:
                commits.append({"hash": parts[0], "date": parts[1],
                                "subject": parts[2]})
        return commits

    def context(self) -> dict:
        """Полный git-контекст (для UI и сценариев); безопасен без git."""
        if not self.available():
            return {"available": False, "branch": None, "tracked": [],
                    "untracked": [], "diff": "", "diff_lines": 0}
        d = self.diff()
        ch = self.changed_files()
        return {"available": True, "branch": self.branch(),
                "tracked": ch["tracked"], "untracked": ch["untracked"],
                "diff": d, "diff_lines": len(d.splitlines()),
                "repo_root": self.repo_root}


# ---------------------------------------------------------------------------
# Разбор unified diff
# ---------------------------------------------------------------------------

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_DIFF_GIT_RE = re.compile(r"^diff --git a/(.+?) b/(.+)$")


def _strip_ab(path: str) -> str:
    """Убрать префикс `a/` или `b/` из git-пути в diff.

    Без этого `studio/backend/main.py` приезжает как
    `b/studio/backend/main.py`, и все проверки по префиксу пути
    (`_backend_module_of`, allow-list записи) молча не срабатывают —
    ревью перестаёт видеть изменённые модули бэкенда.
    """
    p = path.strip()
    if p in ("/dev/null", ""):
        return p
    if p.startswith(("a/", "b/")):
        return p[2:]
    return p


@dataclass
class DiffLine:
    """Строка diff: тип (add/del/ctx), текст, номер в новой версии."""
    kind: str
    text: str
    new_lineno: int | None
    old_lineno: int | None


@dataclass
class DiffFile:
    """Изменённый файл: путь, добавленные/удалённые строки."""
    path: str
    old_path: str = ""
    added: int = 0
    removed: int = 0
    is_new: bool = False
    is_deleted: bool = False
    added_lines: list = field(default_factory=list)   # DiffLine
    removed_lines: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"path": self.path, "added": self.added,
                "removed": self.removed, "is_new": self.is_new,
                "is_deleted": self.is_deleted}


def parse_unified_diff(text: str) -> list:
    """Разобрать unified diff в список `DiffFile`.

    Понимает `diff --git`, `+++`/`---`, `new file mode`, `deleted file
    mode` и ханки `@@`. Номера строк считаются по разделам ханков:
    добавленные строки получают номер в новой версии, удалённые — в
    старой (важно для доказательств в отчёте ревью).

    Битый или пустой diff не роняет разбор: всё, что не распознано,
    пропускается. Пустой результат — вызывающий код решает, ошибка это
    или нет.
    """
    if not text or not text.strip():
        return []
    if len(text) > MAX_DIFF_CHARS:
        text = text[:MAX_DIFF_CHARS]

    files: list[DiffFile] = []
    cur: DiffFile | None = None
    new_no = old_no = 0

    for raw in text.splitlines():
        if raw.startswith("diff --git "):
            # Канонический путь — из заголовка `diff --git a/X b/Y`:
            # для переименований/удалений `+++` может быть /dev/null.
            m = _DIFF_GIT_RE.match(raw)
            path = _strip_ab(m.group(2)) if m else ""
            cur = DiffFile(path=path) if path else None
            if cur is not None:
                files.append(cur)
            continue
        if raw.startswith("new file mode"):
            if cur:
                cur.is_new = True
            continue
        if raw.startswith("deleted file mode"):
            if cur:
                cur.is_deleted = True
            continue
        if raw.startswith("--- "):
            path = _strip_ab(raw[4:])
            if cur is None:
                cur = DiffFile(path=path)
                files.append(cur)
            cur.old_path = path
            continue
        if raw.startswith("+++ "):
            path = _strip_ab(raw[4:])
            if cur is None:
                cur = DiffFile(path=path)
                files.append(cur)
            # "+++ /dev/null" — файл удалён; путь берём из "---".
            if path != "/dev/null":
                cur.path = path
            continue
        m = _HUNK_RE.match(raw)
        if m:
            old_no = int(m.group(1))
            new_no = int(m.group(3))
            continue
        if cur is None or not raw:
            continue
        tag, body = raw[0], raw[1:]
        if tag == "+":
            cur.added += 1
            cur.added_lines.append(DiffLine("add", body, new_no, None))
            new_no += 1
        elif tag == "-":
            cur.removed += 1
            cur.removed_lines.append(DiffLine("del", body, None, old_no))
            old_no += 1
        elif tag == " ":
            new_no += 1
            old_no += 1
        # "\ No newline at end of file" и прочее — игнорируем
    return files


# ---------------------------------------------------------------------------
# Детерминированные проверки ревью
# ---------------------------------------------------------------------------

_SECRET_RE = re.compile(
    r"(?i)\b(api[_-]?key|secret|passwd|password|token|bearer)\b\s*[:=]\s*"
    r"['\"]?([A-Za-z0-9_\-]{12,})")
_ASSIGN_KEY_RE = re.compile(r"['\"]?(sk|pk|ghp|xox[baprs])[-_][A-Za-z0-9]{16,}")
_TODO_RE = re.compile(r"\b(TODO|FIXME|XXX)\b")
_TODO_OWNER_RE = re.compile(r"\b(TODO|FIXME|XXX)\s*\(([^)]+)\)")
_BARE_EXCEPT_RE = re.compile(r"^\s*except\s*:")
_PRINT_RE = re.compile(r"(?<![\w.])print\s*\(")


def _backend_module_of(path: str) -> str | None:
    """`studio/backend/foo.py` → `foo` (только модули, не тесты)."""
    p = path.replace("\\", "/")
    if not p.startswith("studio/backend/") or not p.endswith(".py"):
        return None
    name = p[len("studio/backend/"):-3]
    if "/" in name or name.startswith("test_") or name == "conftest":
        return None
    return name


def _is_code_file(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in (".py", ".ts", ".tsx",
                                                 ".js", ".jsx")


def _is_doc_file(path: str) -> bool:
    p = path.replace("\\", "/")
    return (p.endswith(".md") or p.startswith("docs/")
            or p.startswith("openspec/") or p.endswith(".rst"))


def check_diff(files: list, max_line_len: int = MAX_LINE_LEN) -> list:
    """Детерминированные находки по разобранному diff.

    Каждая находка: `rule`, `severity`, `message`, `file`, `line`,
    `snippet`. Номера строк — из diff (новая версия для добавленных
    строк), поэтому находку можно проверить глазами.

    Правила: bare_except, print_call, long_line, secret, todo,
    missing_test, missing_docs.
    """
    findings: list[dict] = []
    changed_paths = {f.path for f in files}

    def add(rule, severity, message, path, line, snippet=""):
        findings.append({"rule": rule, "severity": severity,
                         "message": message, "file": path, "line": line,
                         "snippet": snippet.strip()[:200]})

    for f in files:
        if f.is_deleted:
            continue
        for dl in f.added_lines:
            text = dl.text
            if len(text) > max_line_len:
                add("long_line", "low",
                    f"Строка длиннее {max_line_len} символов "
                    f"({len(text)})", f.path, dl.new_lineno, text)
            if _BARE_EXCEPT_RE.match(text):
                add("bare_except", "high",
                    "`except:` без типа перехватывает всё, включая "
                    "KeyboardInterrupt/SystemExit — укажите исключение",
                    f.path, dl.new_lineno, text)
            if f.path.endswith(".py") and _PRINT_RE.search(text):
                add("print_call", "low",
                    "`print()` в коде бэкенда — используйте логгер "
                    "проекта ([KB]/[MCP]-теги)", f.path, dl.new_lineno,
                    text)
            m = _SECRET_RE.search(text) or _ASSIGN_KEY_RE.search(text)
            if m:
                add("secret", "high",
                    "Похоже на секрет/токен в коде — вынесите в "
                    "переменную окружения (.env)", f.path, dl.new_lineno,
                    text)
            tm = _TODO_RE.search(text)
            if tm and not _TODO_OWNER_RE.search(text):
                add("todo", "low",
                    f"`{tm.group(1)}` без указания автора — "
                    "оформляйте как TODO(имя): что сделать", f.path,
                    dl.new_lineno, text)

        # Изменён модуль бэкенда — ожидаем правку его тестов.
        mod = _backend_module_of(f.path)
        if mod:
            test_path = f"studio/backend/tests/test_{mod}.py"
            if test_path not in changed_paths:
                add("missing_test", "medium",
                    f"Модуль `{mod}` изменён без правки "
                    f"`tests/test_{mod}.py` — тесты не обновлены",
                    f.path, None, "")

    # Изменён код — ожидаем изменение документации (агрегированная находка).
    code_files = [f.path for f in files
                  if _is_code_file(f.path) and not f.is_deleted]
    doc_changed = any(_is_doc_file(p) for p in changed_paths)
    if code_files and not doc_changed:
        add("missing_docs", "medium",
            f"Изменён код ({len(code_files)} файл(ов)) без изменения "
            "документации — обновите README/docs", code_files[0], None,
            "")

    return findings


_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def sort_findings(findings: list) -> list:
    """Находки: сначала high, затем по файлу и строке (стабильно)."""
    return sorted(findings, key=lambda x: (
        _SEVERITY_ORDER.get(x.get("severity", "low"), 3),
        x.get("file") or "", x.get("line") or 0))


def summarize_findings(findings: list) -> dict:
    """Сводка находок по severity."""
    by = {"high": 0, "medium": 0, "low": 0}
    for f in findings:
        s = f.get("severity", "low")
        by[s] = by.get(s, 0) + 1
    return {"total": len(findings), "by_severity": by}


# ---------------------------------------------------------------------------
# Инварианты проекта (для сценария check_invariants)
# ---------------------------------------------------------------------------

# Правила-инварианты: (код, описание, функция(rel, text) -> list[строка]).

_STR_RE = re.compile(r'"""(?:.|\n)*?"""|\'\'\'(?:.|\n)*?\'\'\''
                     r'|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'')


def _code_only(text: str) -> str:
    """Текст без строковых литералов и комментариев.

    Нужно, чтобы инварианты не срабатывали на УПОМИНАНИЕ кода в тексте:
    `print()` внутри строки-сообщения — это не вызов print, а строка.
    Без этого детектор даёт ложные срабатывания (проверено на
    `assistant.py`: строка `"`print()` в коде бэкенда..."` помечалась
    как вызов print). Литералы заменяются пробелами той же длины,
    чтобы номера строк и позиции не сместились.
    """
    def blank(m: re.Match) -> str:
        return re.sub(r"[^\n]", " ", m.group(0))
    out = _STR_RE.sub(blank, text)
    return re.sub(r"#[^\n]*", lambda m: " " * len(m.group(0)), out)


def _inv_no_torch(rel: str, text: str) -> list:
    """ML-зависимости не должны появляться в бэкенде: проект живёт на
    stdlib + FastAPI/httpx (NFR-1 дня 30 и дней 30–34)."""
    bad = []
    if not rel.startswith("studio/backend/"):
        return bad
    for i, line in enumerate(_code_only(text).splitlines(), 1):
        s = line.strip()
        if s.startswith(("import torch", "from torch", "import tensorflow",
                         "from tensorflow", "import transformers")):
            bad.append(f"строка {i}: тяжёлая ML-зависимость ({s[:60]})")
    return bad


# Тег лога проекта: print("[Tag] ...") — принятый в репозитории стиль
# (дни 16–30: [KB], [MCP Init], [LLM Decision], [Agent]).
_LOG_TAG_RE = re.compile(r"""^\s*f?["']\s*\[[A-Za-zА-Яа-я0-9 _-]+\]""")
# Начало вызова print: `print(` или `print (` (не метод `obj.print(`).
_PRINT_CALL_RE = re.compile(r"(?<![\w.])print\s*\(")


def _first_print_arg(text: str, start: int) -> str:
    """Первый аргумент вызова print, начиная с позиции `start` (после
    `print(`), по тексту с сохранёнными литералами.

    Возвращает текст первого аргумента (для `print("[KB] …", flush=True)`
    это `"[KB] …"`). Сканирование учитывает вложенные скобки и кавычки,
    чтобы многострочные вызовы читались целиком. Ограничение — 4 строки,
    чтобы битый файл не съел весь текст.
    """
    depth = 0
    quote = None
    buf: list[str] = []
    lines = 0
    i = start
    while i < len(text):
        ch = text[i]
        if ch == "\n":
            lines += 1
            if lines > 4:
                break
        if quote:
            buf.append(ch)
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "\"'":
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            if depth == 0:
                break
            depth -= 1
        elif ch == "," and depth == 0:
            break
        buf.append(ch)
        i += 1
    return "".join(buf).strip()


def _inv_tagged_log(rel: str, text: str) -> list:
    """Диагностика в бэкенде идёт через тегированный print (`[Tag] …`).

    Стиль репозитория — не `logging`, а `print("[Tag] …", flush=True)`
    (дни 16–30). Поэтому инвариант требует именно тег, а не запрещает
    print целиком: безликий `print("error")` в библиотеке найти в логах
    невозможно, а `print("[KB] …")` — фильтруется.

    Проверяется ПЕРВЫЙ АРГУМЕНТ вызова print по исходному тексту
    (в литералах и живёт тег). Поэтому:
    * `print("[KB] …", flush=True)` — ок, в т.ч. многострочный;
    * `print("error")` — нарушение;
    * упоминание `print()` внутри строки — НЕ вызов и не проверяется
      (позиция вызова берётся по тексту без литералов, см. `_code_only`).

    **Исключение — CLI (`cli.py`).** Там stdout не лог, а САМ ПРОДУКТ:
    `print(render_review(...))` — это вывод команды, который читает
    человек и парсит пайп. Требовать на него `[Tag]` нельзя: тег попал
    бы в вывод и сломал его (и `--json`, и `| grep`). Поэтому файлы-точки
    входа CLI из правила исключены — иначе инвариант ругался бы на
    корректный код, и доверие к проверке потерялось бы.
    """
    bad = []
    if not rel.startswith("studio/backend/") or "/tests/" in rel:
        return bad
    if os.path.basename(rel) == "cli.py":
        return bad
    code = _code_only(text)
    lines = text.splitlines()
    for i, line in enumerate(code.splitlines()):
        for m in _PRINT_CALL_RE.finditer(line):
            # Позиция вызова в исходном тексте: смещение строки.
            offset = sum(len(x) + 1 for x in lines[:i]) + m.end()
            arg = _first_print_arg(text, offset)
            if not _LOG_TAG_RE.match(arg):
                bad.append(f"строка {i + 1}: print() без тега — "
                           'оформляйте как print("[Tag] …", flush=True)')
    return bad


def _inv_docstring(rel: str, text: str) -> list:
    """Публичный модуль бэкенда обязан иметь docstring модуля."""
    bad = []
    if not _backend_module_of(rel):
        return bad
    head = text.lstrip()
    if not (head.startswith('"""') or head.startswith("'''")):
        bad.append("нет docstring модуля")
    return bad


INVARIANTS = [
    ("no-heavy-ml-deps", "Бэкенд без тяжёлых ML-зависимостей",
     _inv_no_torch),
    ("tagged-log", "Диагностика через тегированный print ([Tag] …)",
     _inv_tagged_log),
    ("module-docstring", "У публичных модулей бэкенда есть docstring",
     _inv_docstring),
]


def check_invariants(repo_root: str, paths: list | None = None) -> dict:
    """Проверить файлы проекта на инварианты.

    `paths` — список относительных путей (None = весь корпус кода
    бэкенда). Возврат: `checks` (по правилу) и `violations`.
    """
    root = os.path.abspath(repo_root)
    if paths is None:
        corpus = ProjectCorpus(root)
        paths = [p for p in corpus.files()
                 if p.startswith("studio/backend/")]
    violations = []
    for rule, desc, fn in INVARIANTS:
        for rel in paths:
            full = os.path.join(root, rel)
            if not os.path.isfile(full):
                continue
            try:
                with open(full, encoding="utf-8") as fh:
                    text = fh.read()
            except (OSError, UnicodeDecodeError):
                continue
            for detail in fn(rel.replace(os.sep, "/"), text):
                violations.append({"rule": rule, "description": desc,
                                   "file": rel.replace(os.sep, "/"),
                                   "detail": detail})
    return {"checked": len(paths), "violations": violations,
            "clean": not violations,
            "rules": [{"rule": r, "description": d}
                      for r, d, _ in INVARIANTS]}


# ---------------------------------------------------------------------------
# Поиск использований символа
# ---------------------------------------------------------------------------

def find_usages(repo_root: str, symbol: str,
                paths: list | None = None) -> dict:
    """Найти места использования `symbol` в коде и документации.

    Поиск идёт по корпусу проекта (`ProjectCorpus`), совпадения
    группируются по файлу с номерами строк. Совпадение — вхождение
    символа как отдельного слова (для `foo.bar` и `foo(` — тоже),
    чтобы `search` не матчился внутри `searcher`.
    """
    if not symbol or not symbol.strip():
        raise AssistantError("Не указан символ для поиска")
    symbol = symbol.strip()
    root = os.path.abspath(repo_root)
    corpus = ProjectCorpus(root)
    files = paths if paths is not None else corpus.files()
    pattern = re.compile(r"(?<![\w.])" + re.escape(symbol) + r"(?![\w])")

    hits: dict[str, list] = {}
    by_source: dict[str, int] = {}
    total = 0
    for rel in files:
        rel = rel.replace(os.sep, "/")
        text = corpus._read(os.path.join(root, rel))
        if text is None:
            continue
        lines = []
        for i, line in enumerate(text.splitlines(), 1):
            if pattern.search(line):
                lines.append({"line": i, "text": line.strip()[:200]})
        if lines:
            hits[rel] = lines
            total += len(lines)
            src = "docs" if _is_doc_file(rel) else "code"
            by_source[src] = by_source.get(src, 0) + len(lines)
    return {"symbol": symbol, "total": total,
            "files": [{"file": k, "count": len(v), "matches": v}
                      for k, v in sorted(hits.items())],
            "file_count": len(hits), "by_source": by_source}


# ---------------------------------------------------------------------------
# Запись файлов (allow-list)
# ---------------------------------------------------------------------------

def _resolve_write_path(repo_root: str, rel: str) -> str:
    """Проверить путь записи и вернуть абсолютный.

    Разрешены только пути из `WRITE_ALLOWLIST` внутри проекта:
    абсолютные пути, выход через `..` и симлинки наружу отвергаются
    (`ScenarioError`) — сценарий не должен уметь писать куда угодно.
    """
    if not rel or not rel.strip():
        raise ScenarioError("Не указан путь для записи")
    rel = rel.strip().replace("\\", "/")
    if os.path.isabs(rel):
        raise ScenarioError(
            f"Абсолютный путь запрещён: {rel!r} (запись только внутри "
            "проекта, относительно корня)")
    norm = os.path.normpath(rel).replace(os.sep, "/")
    if norm.startswith("../") or norm == "..":
        raise ScenarioError(f"Выход за пределы проекта запрещён: {rel!r}")

    root = os.path.abspath(repo_root)
    allowed = any(norm == a.rstrip("/") or norm.startswith(a)
                  for a in WRITE_ALLOWLIST)
    if not allowed:
        raise ScenarioError(
            f"Запись в {norm!r} запрещена: разрешено только "
            f"{', '.join(WRITE_ALLOWLIST)}")

    full = os.path.abspath(os.path.join(root, norm))
    if not (full == root or full.startswith(root + os.sep)):
        raise ScenarioError(f"Путь вне проекта: {rel!r}")
    # Симлинк-эскейп: реальный путь существующего родителя — внутри проекта.
    parent = os.path.dirname(full) or root
    real_parent = os.path.realpath(parent)
    real_root = os.path.realpath(root)
    if not (real_parent == real_root
            or real_parent.startswith(real_root + os.sep)):
        raise ScenarioError(f"Путь ведёт за пределы проекта: {rel!r}")
    return full


def _write_text(repo_root: str, rel: str, text: str) -> str:
    """Атомарная запись текста в разрешённый путь проекта."""
    full = _resolve_write_path(repo_root, rel)
    os.makedirs(os.path.dirname(full) or repo_root, exist_ok=True)
    tmp = full + ".tmp-assistant"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, full)
    return full


def _unified_diff(old: str, new: str, rel: str) -> str:
    """unified diff двух версий текста (для отчёта сценария)."""
    return "".join(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=f"a/{rel}", tofile=f"b/{rel}"))


def _replace_autogen_section(text: str, body: str) -> tuple:
    """Заменить содержимое между маркерами AUTOGEN_* (идемпотентно).

    Возврат: (новый текст, был ли маркер). Если маркеров нет — блок
    добавляется в конец.
    """
    if AUTOGEN_BEGIN in text and AUTOGEN_END in text:
        pre = text.split(AUTOGEN_BEGIN)[0]
        post = text.split(AUTOGEN_END, 1)[1]
        new = (pre + AUTOGEN_BEGIN + "\n" + body.rstrip() + "\n"
               + AUTOGEN_END + post)
        return new, True
    sep = "" if text.endswith("\n\n") or not text else "\n"
    new = (text + sep + AUTOGEN_BEGIN + "\n" + body.rstrip() + "\n"
           + AUTOGEN_END + "\n")
    return new, False


# ---------------------------------------------------------------------------
# Сервис ассистента
# ---------------------------------------------------------------------------

# Промпт ответа по проекту: отвечать только по найденному контексту.
_ASK_SYSTEM = (
    "Ты — ассистент разработчика в проекте «Студия». Отвечай ТОЛЬКО на "
    "основе приведённого контекста из документации и кода проекта. "
    "Если в контексте нет ответа — скажи прямо, что данных нет, и "
    "предложи, где искать. Ссылайся на файлы по именам. Отвечай "
    "кратко и по делу, на русском."
)

_SUPPORT_SYSTEM = (
    "Ты — агент поддержки продукта «Студия». Отвечай вежливо и по "
    "делу, опираясь на контекст базы знаний. Учитывай данные "
    "пользователя из CRM: план и статус оплаты влияют на тон и "
    "предлагаемые шаги, роль — на глубину технических деталей. "
    "Не выдумывай факты о продукте, которых нет в контексте. "
    "Отвечай на русском."
)

_REVIEW_SYSTEM = (
    "Ты — код-ревьюер. По приведённым детерминированным находкам и "
    "сводке изменений сформулируй краткие рекомендации по улучшению. "
    "Не выдумывай новые проблемы и номера строк — опирайся только на "
    "список находок. Максимум 5 пунктов, на русском."
)


# Транслитерация RU→EN для слагов имён файлов. Без неё русский
# заголовок ADR («Выбор базы данных») терял все символы при
# ASCII-фильтрации и давал бесполезное `adr.md` — файлы затирали друг
# друга и по имени нельзя было понять содержание.
_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def slugify(text: str, fallback: str = "doc") -> str:
    """Слаг для имени файла: транслит кириллицы + ASCII-фильтр.

    `slugify("Выбор базы данных")` → `vybor-bazy-dannyh`. Если после
    фильтрации ничего не осталось — `fallback`.
    """
    low = (text or "").lower()
    translit = "".join(_TRANSLIT.get(ch, ch) for ch in low)
    slug = re.sub(r"[^a-z0-9]+", "-", translit).strip("-")
    return slug or fallback


class AssistantService:
    """Единая точка входа ассистента: 4 модуля + 5 сценариев.

    `agent` — экземпляр `StudioAgent` (даёт доступ к LLM через
    `_base_for`, конфигу и httpx-клиенту). Если `agent is None` или LLM
    недоступна, все модули работают в **деградированном, но полезном**
    режиме без модели (extractive-ответ, детерминированное ревью).
    """

    def __init__(self, repo_root: str, agent=None, kb_root: str | None = None,
                 crm_dir: str | None = None):
        self.repo_root = os.path.abspath(repo_root)
        self.agent = agent
        self.pkb = ProjectKnowledgeBase(self.repo_root, kb_root=kb_root)
        self.tickets = TicketStore(self.repo_root, crm_dir=crm_dir)
        self.git = GitReader(self.repo_root)
        # Причина последней неудачи LLM (None — вызов не делался/успешен):
        # нужна, чтобы деградация была наблюдаемой, а не молчаливой.
        self._llm_error: str | None = None

    # ---------- состояние ----------

    def status(self) -> dict:
        """Состояние ассистента: индекс, LLM, CRM, git."""
        idx = self.pkb.load_index()
        stats = (idx or {}).get("stats") or {}
        git_ctx = self.git.context()
        return {
            "indexed": idx is not None,
            "documents": stats.get("docs", 0),
            "chunks": stats.get("chunks", 0),
            "strategy": (idx or {}).get("strategy"),
            "embedder": (idx or {}).get("embedder"),
            "built_at": (idx or {}).get("built_at"),
            "llm_available": self.agent is not None,
            "crm": self.tickets.stats(),
            "git": {"available": git_ctx["available"],
                    "branch": git_ctx["branch"],
                    "changed": len(git_ctx["tracked"])
                    + len(git_ctx["untracked"])},
            "corpus": self.pkb.corpus.stats(),
        }

    def build_index(self, strategy: str = "structural") -> dict:
        """Собрать индекс проекта (офлайн hash-эмбеддер)."""
        res = self.pkb.build_project(strategy=strategy)
        return {"mode": res.get("mode"), "strategy": res.get("strategy"),
                "added": res.get("added"), "stats": res.get("stats")}

    # ---------- LLM (опционально) ----------

    def _llm(self, system: str, user: str, max_tokens: int = 700) -> str:
        """Один вызов LLM. Любая ошибка/отсутствие модели → "" (вызывающий
        код деградирует). Ассистент не должен падать из-за сети."""
        if self.agent is None:
            return ""
        try:
            cfg = self.agent.get_config()
            model = cfg.get("model")
            if not model:
                self._llm_error = "в конфиге не задана модель"
                return ""
            self._ensure_local_model(model)
            body = {"model": model, "max_tokens": max_tokens,
                    "temperature": 0.2,
                    "messages": [{"role": "system", "content": system},
                                 {"role": "user", "content": user}]}
            resp = self.agent._client.post(
                self.agent._base_for(model) + "/chat/completions",
                headers=self.agent._auth_header_for(model),
                json=body, timeout=120)
            if resp.status_code != 200:
                # Причину СОХРАНЯЕМ, а не глотаем: иначе «LLM не ответила»
                # выглядит одинаково при 401 (нет ключа), 404 (нет модели)
                # и таймауте — диагностировать невозможно.
                self._llm_error = (f"HTTP {resp.status_code} от "
                                   f"{self.agent._base_for(model)}")
                return ""
            data = resp.json()
            choices = data.get("choices") or []
            if not choices:
                self._llm_error = "ответ без choices"
                return ""
            content = (choices[0].get("message") or {}).get("content")
            if not (content or "").strip():
                # Пустой content — типично для reasoning-моделей: бюджет
                # ушёл на размышления (день 30, находка 2).
                self._llm_error = (
                    "пустой ответ модели (finish_reason="
                    f"{choices[0].get('finish_reason')}) — вероятно, "
                    "reasoning-модель израсходовала бюджет")
                return ""
            self._llm_error = None
            return content.strip()
        except Exception as e:                      # noqa: BLE001
            self._llm_error = f"{type(e).__name__}: {e}"
            return ""

    def _ensure_local_model(self, model: str) -> None:
        """Зарегистрировать локальную модель в агенте перед вызовом.

        `agent._base_for()` отправляет на Ollama ТОЛЬКО модели из
        `_local_ids`, который наполняется в `list_models()`. Без этого
        вызова локальная модель уходит на удалённый GPustack и получает
        401 (у локальной модели нет там ключа) — вызов молча падал, и
        «ассистент не отвечает» выглядело как проблема ассистента, а не
        маршрутизации (найдено живой проверкой Part B).
        """
        if model in getattr(self.agent, "_local_ids", ()):
            return
        try:
            found = {m["id"] for m in self.agent.list_models()
                     if m.get("local")}
        except Exception:                           # noqa: BLE001
            return
        if model in found:
            self.agent._local_ids.add(model)

    # ---------- А. Вопросы по проекту ----------

    def ask_project(self, question: str, top_k: int = 5,
                    system_prompt: str | None = None) -> dict:
        """Ответ на вопрос о структуре/архитектуре по RAG (документация +
        код проекта).

        Без LLM возвращается extractive-ответ: заголовки и первые
        строки найденных фрагментов — это честно полезно и не врёт.

        `system_prompt` (день 35) — переопределение инструкции агента
        из `agents/*.md`; None — встроенный `_ASK_SYSTEM` (поведение
        HTTP-пути не меняется).

        Git-контекст (день 36): состояние репозитория (ветка, изменённые
        файлы, последние коммиты) добавляется в промпт отдельным блоком
        фактов. Без него вопрос «какая сейчас ветка?» получал выдуманный
        ответ: ветка — это состояние репозитория, а не документ, и в
        индексе её нет по определению (найдено живой проверкой).
        """
        if not question or not question.strip():
            raise AssistantError("Пустой вопрос")
        question = question.strip()
        if self.pkb.load_index() is None:
            raise AssistantError(
                "Индекс проекта не построен — вызовите POST "
                "/api/assistant/index (или сценарий индексации)")

        res = self.pkb.search_project(question, top_k=top_k)
        hits = res.get("results") or []
        sources = [{"file": h.get("file"), "section": h.get("section"),
                    "chunk_id": h.get("id"),
                    "score": round(float(h.get("score") or 0.0), 4),
                    "source": h.get("source"),
                    "quote": (h.get("text") or "")[:400]}
                   for h in hits]
        # Живое состояние репозитория — отдельно от найденных фрагментов.
        git_facts = self._git_facts()

        if not hits and not git_facts:
            return {"question": question, "answer": (
                "В документации и коде проекта нет фрагментов, "
                "отвечающих на этот вопрос."),
                "sources": [], "found": False, "llm": False}

        context = "\n\n".join(
            f"[{i}] {h.get('file')} :: {h.get('section')}\n"
            f"{(h.get('text') or '')[:1500]}"
            for i, h in enumerate(hits, 1))
        prompt = f"Вопрос: {question}\n"
        if git_facts:
            # Блок идёт ПЕРЕД документами: это факт о «сейчас», а не
            # выдержка из файла, и модель не должна искать его в чанках.
            prompt += (f"\nТекущее состояние репозитория (факты, не "
                       f"требуют поиска по документации):\n{git_facts}\n")
        prompt += f"\nКонтекст из документации и кода:\n{context or '(нет)'}"
        answer = self._llm(system_prompt or _ASK_SYSTEM, prompt)
        llm_used = bool(answer)
        if not answer:
            # Без LLM: факты о репозитории + извлечение из найденного.
            # git-факты идут ОДИН раз (в промпте они были отдельным
            # блоком, здесь — частью ответа, а не дописаны дважды).
            answer = self._extractive_answer(question, hits)
            if git_facts:
                answer = f"{git_facts}\n\n{answer}"
        out = {"question": question, "answer": answer, "sources": sources,
               "found": True, "llm": llm_used,
               "llm_error": None if llm_used else self._llm_error,
               "recall_total": res.get("recall_total", len(hits))}
        if git_facts:
            out["git_facts"] = git_facts
        return out

    def _git_facts(self) -> str:
        """Текстовая сводка состояния репозитория для промпта.

        Возвращает пустую строку, если git недоступен: вопрос про
        архитектуру не должен ломаться от отсутствия репозитория.
        """
        try:
            ctx = self.git.context()
        except Exception:                           # noqa: BLE001
            return ""
        if not ctx.get("available"):
            return ""
        lines = [f"- Текущая git-ветка: {ctx.get('branch') or '—'}"]
        tracked = list(ctx.get("tracked") or [])
        untracked = list(ctx.get("untracked") or [])
        lines.append(f"- Изменённых файлов в рабочем дереве: "
                     f"{len(tracked) + len(untracked)}")
        if tracked:
            lines.append("- Изменены: "
                         + ", ".join(f"`{p}`" for p in tracked[:10])
                         + (" …" if len(tracked) > 10 else ""))
        if untracked:
            lines.append("- Новые (не в git): "
                         + ", ".join(f"`{p}`" for p in untracked[:10])
                         + (" …" if len(untracked) > 10 else ""))
        try:
            log = self.git.log(5)
        except Exception:                           # noqa: BLE001
            log = []
        if log:
            lines.append("- Последние коммиты:")
            for c in log:
                lines.append(f"    {c.get('hash')} ({c.get('date')}) "
                             f"{c.get('subject')}")
        return "\n".join(lines)

    @staticmethod
    def _extractive_answer(question: str, hits: list) -> str:
        """Ответ без LLM: что нашлось, с указанием файлов (без выдумок)."""
        head = (f"Найдено {len(hits)} релевантных фрагментов по "
                f"запросу «{question}» (ответ без LLM — извлечение "
                "из найденного):")
        parts = [head]
        for i, h in enumerate(hits, 1):
            text = (h.get("text") or "").strip().replace("\n", " ")
            parts.append(f"{i}. {h.get('file')} · {h.get('section')} — "
                         f"{text[:300]}")
        return "\n".join(parts)

    # ---------- Б. Ревью изменений ----------

    def review_diff(self, diff: str | None = None, base: str | None = None,
                    with_llm: bool = True,
                    system_prompt: str | None = None) -> dict:
        """Ревью PR/изменений: разбор diff → детерминированные находки →
        сопоставление с документацией → отчёт.

        Источник diff: явный текст (`diff`) либо git (`base` — ветка для
        сравнения, иначе рабочее дерево). Отчёт формируется **без LLM**;
        LLM при наличии добавляет текстовые рекомендации.

        `diff=None` — «diff не передан», берём из git. Пустая строка — это
        ПЕРЕДАННЫЙ пустой diff, то есть ошибка вызывающего: молчаливая
        подмена его на git-изменения сбивает с толку (клиент присылает
        пустой diff, а получает отчёт по чужим изменениям рабочего дерева).
        """
        if diff is not None and not str(diff).strip():
            raise AssistantError(
                "Передан пустой diff: ожидается непустой unified diff "
                "(если нужно ревью изменений из git — не передавайте diff)")
        if diff is None:
            if base:
                diff = self.git.diff_branch(base)
                source = f"git:{base}...HEAD"
            else:
                diff = self.git.diff()
                source = "git:working-tree"
        else:
            source = "text"

        if not diff or not str(diff).strip():
            raise AssistantError(
                "Пустой diff: нет изменений для ревью (проверьте, что "
                "изменения есть в рабочем дереве или укажите diff/base)")

        files = parse_unified_diff(str(diff))
        if not files:
            raise AssistantError(
                "Не удалось разобрать diff: ожидается формат unified "
                "diff (git diff)")

        findings = sort_findings(check_diff(files))
        summary = summarize_findings(findings)

        # Сопоставление с документацией через индекс проекта.
        affected_docs = []
        if self.pkb.load_index() is not None:
            for f in files[:8]:
                base_name = os.path.basename(f.path)
                stem = os.path.splitext(base_name)[0]
                if not stem:
                    continue
                try:
                    res = self.pkb.search_project(stem, top_k=3)
                except KBError:
                    continue
                for h in (res.get("results") or []):
                    entry = {"file": h.get("file"),
                             "section": h.get("section"),
                             "because": f.path}
                    if entry not in affected_docs:
                        affected_docs.append(entry)

        added = sum(f.added for f in files)
        removed = sum(f.removed for f in files)
        recommendations = self._review_recommendations(findings)
        llm_note = ""
        if with_llm and findings:
            llm_note = self._llm(system_prompt or _REVIEW_SYSTEM,
                                 "Находки:\n" + "\n".join(
                                     f"- [{x['severity']}] {x['rule']}: "
                                     f"{x['message']} ({x['file']}:"
                                     f"{x['line']})" for x in findings[:20]))
        return {
            "source": source,
            "summary": {"files": len(files), "added": added,
                        "removed": removed, "findings": summary["total"],
                        "by_severity": summary["by_severity"]},
            "files": [f.to_dict() for f in files],
            "findings": findings,
            "affected_docs": affected_docs[:12],
            "recommendations": recommendations,
            "llm_note": llm_note,
            "llm_error": None if llm_note else self._llm_error,
        }

    @staticmethod
    def _review_recommendations(findings: list) -> list:
        """Рекомендации, выведенные из находок (детерминированно)."""
        seen, out = set(), []
        advice = {
            "bare_except": "Сузьте `except` до конкретных исключений и "
                           "логируйте причину.",
            "print_call": "Замените `print()` на логгер проекта, чтобы "
                          "вывод был управляемым.",
            "long_line": f"Разбейте длинные строки (>{MAX_LINE_LEN}) — "
                         "это упрощает ревью и diff.",
            "secret": "Уберите секрет из кода: переменная окружения "
                      "(.env) + запись в .env.example без значения.",
            "todo": "Оформите TODO как `TODO(имя): задача`, иначе он "
                    "потеряется.",
            "missing_test": "Добавьте/обновите тесты на изменённый "
                            "модуль — иначе регрессия не поймается.",
            "missing_docs": "Обновите документацию (README/docs) под "
                            "изменение кода.",
        }
        for f in findings:
            r = f.get("rule")
            if r in advice and r not in seen:
                seen.add(r)
                out.append({"rule": r, "advice": advice[r]})
        return out

    # ---------- В. Поддержка ----------

    def support_answer(self, ticket_id: str, question: str | None = None,
                       top_k: int = 4,
                       system_prompt: str | None = None) -> dict:
        """Ответ по тикету с персонализацией из CRM.

        Контекст CRM (план, оплата, роль, история) инжектится в промпт и
        возвращается в блоке `context` — персонализация наблюдаема.

        `system_prompt` (день 35) — инструкция агента из `agents/*.md`;
        None — встроенный `_SUPPORT_SYSTEM`.
        """
        if not ticket_id or not ticket_id.strip():
            raise AssistantError("Не указан id тикета")
        ctx = self.tickets.context(ticket_id.strip())
        ticket = ctx.get("ticket") or {}
        user = ctx.get("user") or {}

        q = (question or "").strip() or " ".join(
            x for x in [ticket.get("subject"), ticket.get("body")] if x)
        if not q:
            raise AssistantError(
                f"Тикет {ticket_id} не найден и вопрос не задан — "
                "нечего обрабатывать")

        # Поиск по базе знаний проекта/продукта (если индекс построен).
        sources = []
        kb_text = ""
        if self.pkb.load_index() is not None:
            try:
                res = self.pkb.search_project(q, top_k=top_k)
                hits = res.get("results") or []
                sources = [{"file": h.get("file"),
                            "section": h.get("section"),
                            "score": round(float(h.get("score") or 0.0), 4),
                            "quote": (h.get("text") or "")[:300]}
                           for h in hits]
                kb_text = "\n\n".join(
                    f"[{i}] {h.get('file')} :: {h.get('section')}\n"
                    f"{(h.get('text') or '')[:1200]}"
                    for i, h in enumerate(hits, 1))
            except KBError:
                kb_text = ""

        persona = self._persona_block(ctx)
        if ctx["found"]:
            user_prompt = (
                f"Тикет {ticket.get('id')}: {ticket.get('subject')}\n"
                f"Текст: {ticket.get('body')}\n"
                f"Тема: {ticket.get('topic')}, приоритет: "
                f"{ticket.get('priority')}, статус: {ticket.get('status')}\n\n"
                f"Данные пользователя:\n{persona}\n\n"
                f"Вопрос клиента: {question or ticket.get('subject')}\n\n"
                f"Контекст базы знаний:\n{kb_text or '(нет)'}")
        else:
            user_prompt = (f"Вопрос клиента: {question}\n\n"
                           f"Контекст базы знаний:\n{kb_text or '(нет)'}")

        answer = self._llm(system_prompt or _SUPPORT_SYSTEM, user_prompt,
                           max_tokens=800)
        llm_used = bool(answer)
        if not answer:
            answer = self._fallback_support(ticket, user, ctx, sources)

        return {"ticket_id": ticket_id, "found": ctx["found"],
                "answer": answer, "llm": llm_used, "sources": sources,
                "llm_error": None if llm_used else self._llm_error,
                "context": {
                    "found": ctx["found"],
                    "user": None if not user else {
                        "id": user.get("id"), "name": user.get("name"),
                        "plan": user.get("plan"), "role": user.get("role"),
                        "payment_status": user.get("payment_status"),
                        "channel": user.get("channel"),
                        "registered_at": user.get("registered_at")},
                    "ticket": None if not ticket else {
                        "id": ticket.get("id"),
                        "topic": ticket.get("topic"),
                        "status": ticket.get("status"),
                        "priority": ticket.get("priority")},
                    "history_count": ctx.get("history_count", 0),
                    "applied": self._applied_facts(ctx),
                }}

    @staticmethod
    def _applied_facts(ctx: dict) -> list:
        """Какие именно поля CRM повлияли на ответ (наблюдаемость)."""
        if not ctx.get("found"):
            return ["тикет не найден — ответ общий, без персонализации"]
        u = ctx.get("user") or {}
        facts = []
        if u.get("plan"):
            facts.append(f"план: {u['plan']}")
        if u.get("payment_status"):
            facts.append(f"статус оплаты: {u['payment_status']}")
        if u.get("role"):
            facts.append(f"роль: {u['role']}")
        if u.get("channel"):
            facts.append(f"канал: {u['channel']}")
        if ctx.get("history_count"):
            facts.append(f"прошлых обращений: {ctx['history_count']}")
        return facts

    @staticmethod
    def _persona_block(ctx: dict) -> str:
        """Текстовая выжимка CRM для промпта."""
        u = ctx.get("user") or {}
        t = ctx.get("ticket") or {}
        lines = []
        if u:
            lines.append(
                f"- имя: {u.get('name')}, план: {u.get('plan')}, "
                f"роль: {u.get('role')}, оплата: {u.get('payment_status')}, "
                f"канал: {u.get('channel')}, с нами с "
                f"{u.get('registered_at')}")
        if t:
            lines.append(f"- тема обращения: {t.get('topic')}, "
                         f"приоритет: {t.get('priority')}")
        hist = ctx.get("history") or []
        if hist:
            lines.append("- прошлые обращения: " + "; ".join(
                f"{h.get('id')} ({h.get('topic')}, {h.get('status')})"
                for h in hist[:5]))
        return "\n".join(lines) or "(нет данных)"

    @staticmethod
    def _fallback_support(ticket: dict, user: dict, ctx: dict,
                          sources: list) -> str:
        """Ответ без LLM: факты CRM + найденные фрагменты базы знаний."""
        parts = []
        if ctx.get("found"):
            name = user.get("name") or "клиент"
            parts.append(f"Обращение {ticket.get('id')} от {name}.")
            if user.get("payment_status") == "failed":
                parts.append(
                    "Платёж по подписке не прошёл — сначала проверьте "
                    "статус оплаты в биллинге, доступ восстановится "
                    "после успешного списания.")
            elif user.get("payment_status") == "pending":
                parts.append("Платёж в обработке — дождитесь подтверждения "
                             "банка.")
            if ticket.get("topic") == "auth":
                parts.append(
                    "По авторизации: проверьте папку «Спам» и "
                    "корпоративные фильтры, письмо сброса живёт 30 минут.")
            elif ticket.get("topic") == "configuration":
                parts.append(
                    "По конфигурации: параметры задаются в настройках "
                    "Студии и в .env (.env.example — образец).")
        else:
            parts.append("Тикет не найден в CRM — отвечаю по общим "
                         "правилам продукта.")
        if sources:
            parts.append("Что нашлось в базе знаний:")
            for i, s in enumerate(sources, 1):
                quote = (s.get("quote") or "").strip().replace("\n", " ")
                parts.append(f"{i}. {s.get('file')} · {s.get('section')} — "
                             f"{quote[:220]}")
        else:
            parts.append("В базе знаний нет релевантных фрагментов — "
                         "уточните вопрос или обновите индекс проекта.")
        return "\n".join(parts)

    # ---------- Г. Сценарии ----------

    def scenarios(self) -> list:
        """Каталог сценариев (для UI и API)."""
        return [
            {"name": "find_usages",
             "title": "Поиск использований компонента/API",
             "params": ["symbol"],
             "description": "Находит места использования символа по коду "
                            "и документации, группирует по файлам."},
            {"name": "update_docs",
             "title": "Обновление документации по изменениям кода",
             "params": ["path", "dry_run"],
             "description": "Читает git diff, определяет затронутые "
                            "модули и обновляет секцию README между "
                            "маркерами (идемпотентно)."},
            {"name": "generate",
             "title": "Генерация файла (README / ADR / changelog)",
             "params": ["kind", "title", "path", "dry_run"],
             "description": "Создаёт новый файл: ADR с автонумерацией, "
                            "changelog из git-лога или README-заготовку."},
            {"name": "check_invariants",
             "title": "Проверка соответствия правилам проекта",
             "params": ["paths"],
             "description": "Проверяет файлы на инварианты проекта "
                            "(зависимости, print, docstring)."},
            {"name": "diff_report",
             "title": "Подготовка diff/списка изменений",
             "params": ["path", "dry_run"],
             "description": "Готовит unified-diff отчёт по изменениям: "
                            "выводит или сохраняет в файл."},
        ]

    def run_scenario(self, name: str, **params) -> dict:
        """Запустить сценарий по имени. Неизвестный — ScenarioError."""
        handlers = {
            "find_usages": self._sc_find_usages,
            "update_docs": self._sc_update_docs,
            "generate": self._sc_generate,
            "check_invariants": self._sc_check_invariants,
            "diff_report": self._sc_diff_report,
        }
        fn = handlers.get(name)
        if fn is None:
            raise ScenarioError(
                f"Неизвестный сценарий: {name!r} "
                f"(доступно: {', '.join(sorted(handlers))})")
        t0 = time.monotonic()
        result = fn(**params)
        result["scenario"] = name
        result["duration_ms"] = int((time.monotonic() - t0) * 1000)
        return result

    def _sc_find_usages(self, symbol: str = "", **_) -> dict:
        """Сценарий: где используется компонент/API."""
        res = find_usages(self.repo_root, symbol)
        top = ", ".join(f"{f['file']} ({f['count']})"
                        for f in res["files"][:5])
        summary = (f"Символ «{res['symbol']}» найден {res['total']} раз "
                   f"в {res['file_count']} файлах"
                   + (f": {top}" if top else ""))
        return {"ok": True, "summary": summary, "result": res,
                "changed_files": [], "diff": ""}

    def _sc_update_docs(self, path: str = "README.md",
                        dry_run: bool = False, **_) -> dict:
        """Сценарий: обновить документацию по фактическим изменениям."""
        git_ctx = self.git.context()
        if not git_ctx["available"]:
            raise ScenarioError("git недоступен — нечего сопоставлять")
        changed = git_ctx["tracked"] + git_ctx["untracked"]
        code_mods = sorted({_backend_module_of(p) for p in changed
                            if _backend_module_of(p)})
        front = sorted({p.split("/src/")[-1].split("/")[0]
                        for p in changed
                        if p.startswith("studio/frontend/src/")})
        body_lines = ["", "### Изменения (сгенерировано ассистентом)", ""]
        if code_mods:
            body_lines.append("**Модули бэкенда:** "
                              + ", ".join(f"`{m}`" for m in code_mods))
        if front:
            body_lines.append("**Фронтенд:** "
                              + ", ".join(f"`{f}`" for f in front))
        doc_changes = [p for p in changed if _is_doc_file(p)]
        if doc_changes:
            body_lines.append("**Документация:** "
                              + ", ".join(f"`{p}`" for p in doc_changes[:8]))
        if len(body_lines) == 3:
            body_lines.append("Значимых изменений кода не найдено.")
        body_lines.append("")
        body_lines.append(f"_Обновлено: "
                          f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_")
        body = "\n".join(body_lines)

        full = _resolve_write_path(self.repo_root, path)
        old = ""
        if os.path.isfile(full):
            with open(full, encoding="utf-8") as f:
                old = f.read()
        new, had_marker = _replace_autogen_section(old, body)
        diff = _unified_diff(old, new, path)
        if not dry_run:
            _write_text(self.repo_root, path, new)
        return {"ok": True, "dry_run": bool(dry_run),
                "summary": (f"Секция документации в `{path}` "
                            + ("обновлена" if not dry_run else
                               "подготовлена (dry-run)")
                            + ("" if had_marker else " (создан маркер)")),
                "changed_files": [] if dry_run else [path],
                "diff": diff}

    def _sc_generate(self, kind: str = "adr", title: str = "",
                     path: str = "", dry_run: bool = False, **_) -> dict:
        """Сценарий: сгенерировать новый файл (README / ADR / changelog)."""
        kind = (kind or "adr").strip().lower()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if not title:
            title = {"adr": "Новое архитектурное решение",
                     "readme": "Новый раздел",
                     "changelog": "Изменения"}.get(kind, "Новый документ")

        if kind == "adr":
            adr_dir = os.path.join(self.repo_root, "docs", "adr")
            nums = []
            if os.path.isdir(adr_dir):
                for fn in os.listdir(adr_dir):
                    m = re.match(r"^(\d{4})-", fn)
                    if m:
                        nums.append(int(m.group(1)))
            num = (max(nums) + 1) if nums else 1
            slug = slugify(title, fallback="adr")
            rel = path or f"docs/adr/{num:04d}-{slug}.md"
            text = (f"# ADR {num:04d}: {title}\n\n"
                    f"- Дата: {today}\n- Статус: предложено\n\n"
                    "## Контекст\n\n<в чём проблема и почему решение "
                    "нужно сейчас>\n\n## Решение\n\n<что делаем>\n\n"
                    "## Последствия\n\n<что становится проще, что "
                    "сложнее, что придётся поддерживать>\n\n"
                    "## Альтернативы\n\n<что рассматривали и почему "
                    "отклонили>\n")
        elif kind == "changelog":
            rel = path or "CHANGELOG.md"
            commits = self.git.log(40)
            lines = [f"# Changelog\n", f"_Обновлено: {today}_\n"]
            if commits:
                lines.append("## Изменения\n")
                for c in commits:
                    lines.append(f"- `{c['hash']}` ({c['date']}) "
                                 f"{c['subject']}")
            else:
                lines.append("## Изменения\n\n- git-история недоступна\n")
            text = "\n".join(lines) + "\n"
        elif kind == "readme":
            rel = path or "docs/generated/README-section.md"
            text = (f"# {title}\n\n_Сгенерировано ассистентом {today}._\n\n"
                    "## Назначение\n\n<зачем этот компонент>\n\n"
                    "## Использование\n\n```bash\n# пример запуска\n```\n\n"
                    "## Ограничения\n\n<что не поддерживается>\n")
        else:
            raise ScenarioError(
                f"Неизвестный тип генерации: {kind!r} "
                "(readme|adr|changelog)")

        full = _resolve_write_path(self.repo_root, rel)
        old = ""
        if os.path.isfile(full):
            with open(full, encoding="utf-8") as f:
                old = f.read()
        diff = _unified_diff(old, text, rel)
        if not dry_run:
            _write_text(self.repo_root, rel, text)
        return {"ok": True, "dry_run": bool(dry_run),
                "summary": (f"Сгенерирован файл `{rel}` ({kind})"
                            + ("" if not dry_run else " — dry-run, "
                               "на диск не записан")),
                "changed_files": [] if dry_run else [rel],
                "diff": diff, "created": not bool(old)}

    def _sc_check_invariants(self, paths: list | None = None, **_) -> dict:
        """Сценарий: проверка файлов на правила проекта."""
        res = check_invariants(self.repo_root, paths=paths)
        if res["clean"]:
            summary = (f"Проверено {res['checked']} файл(ов) — нарушений "
                       f"инвариантов не найдено "
                       f"({len(res['rules'])} правила)")
        else:
            summary = (f"Проверено {res['checked']} файл(ов) — найдено "
                       f"{len(res['violations'])} нарушений(е)")
        return {"ok": True, "summary": summary, "result": res,
                "changed_files": [], "diff": ""}

    def _sc_diff_report(self, path: str = "", dry_run: bool = False,
                        **_) -> dict:
        """Сценарий: подготовить diff/список изменений."""
        git_ctx = self.git.context()
        if not git_ctx["available"]:
            raise ScenarioError("git недоступен — diff подготовить нельзя")
        diff = git_ctx["diff"]
        changed = git_ctx["tracked"] + git_ctx["untracked"]
        header = [f"# Отчёт об изменениях",
                  f"",
                  f"- Ветка: `{git_ctx['branch']}`",
                  f"- Изменённых файлов: {len(changed)}",
                  f"- Строк diff: {git_ctx['diff_lines']}",
                  f"- Дата: "
                  f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
                  f"", "## Файлы", ""]
        header += [f"- `{p}`" for p in changed] or ["- (нет)"]
        header += ["", "## Diff", "", "```diff", diff.rstrip(), "```", ""]
        report = "\n".join(header)

        changed_files, out_path = [], path
        if out_path and not dry_run:
            out_path = out_path if out_path.endswith(".md") else out_path
            _write_text(self.repo_root, out_path, report)
            changed_files = [out_path]
        return {"ok": True, "dry_run": bool(dry_run) or not out_path,
                "summary": (f"Отчёт об изменениях: {len(changed)} "
                            f"файл(ов), {git_ctx['diff_lines']} строк diff"
                            + (f" — сохранён в `{out_path}`"
                               if changed_files else " — возвращён без "
                               "записи")),
                "changed_files": changed_files,
                "diff": _unified_diff("", report, out_path or "diff-report.md")
                if not changed_files else "",
                "report": report}
