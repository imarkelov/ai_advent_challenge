#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""День 31 — CLI-ассистент разработчика с RAG и MCP (автономный инструмент).

Возможности:
  1. RAG по документации и коду проекта (README, docs/, openspec/, бэкенд,
     фронтенд, скрипты): гибридный поиск — BM25 + векторы локальной Ollama
     (bge-m3) с отсечением нерелевантного; индекс сам пересобирается при
     изменении корпуса.
  2. Автономность: если информации о проекте нет, агент сам исследует репозиторий,
     создаёт/дополняет документацию (README, папка docs/) и сохраняет её в RAG,
     после чего подмешивает в ответ.
  3. MCP: подключение к проекту через studio/mcp_servers/git_branch.py
     (получение ветки, списка файлов, diff).
  4. Создание файлов: агент умеет создавать и обновлять файлы проекта
     по запросу пользователя (через диалог или команду create).
  5. Прямой диалог: можно писать вопросы сразу без префикса help.
  6. Команды: clear, help, git, files, diff, create, status, search, docs, exit.
  7. Автодополнение команд по клавише Tab.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import sys
import urllib.request
from collections import Counter
from typing import Any

try:
    import readline
except ImportError:
    readline = None

# Корень репозитория — каталог, где лежит dev_assistant.py
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

# Автоматический перезапуск под .venv / Python 3.10+, если запущен системный python 3.9
if sys.version_info < (3, 10):
    candidates = [
        os.path.join(REPO_ROOT, ".venv", "bin", "python"),
        os.path.join(REPO_ROOT, ".venv", "bin", "python3"),
        "/opt/homebrew/bin/python3.14",
        "/opt/homebrew/bin/python3",
    ]
    for cand in candidates:
        if os.path.isfile(cand) and os.access(cand, os.X_OK):
            os.execv(cand, [cand] + sys.argv)
    sys.stderr.write(
        f"Ошибка: требуется Python 3.10+ (текущая версия: {sys.version.split()[0]}).\n"
        f"Используйте .venv/bin/python или установите Python 3.10+\n"
    )
    sys.exit(1)

MCP_SERVER = os.path.join(REPO_ROOT, "studio", "mcp_servers", "git_branch.py")
RAG_CACHE_FILE = os.path.join(REPO_ROOT, ".dev_assistant_rag.json")


# --- ANSI Цвета ---

class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    GREEN = "\033[32m"
    CYAN = "\033[36m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    MAGENTA = "\033[35m"
    BLUE = "\033[34m"

    @classmethod
    def disable(cls):
        for attr in ("RESET", "BOLD", "DIM", "GREEN", "CYAN", "YELLOW",
                     "RED", "MAGENTA", "BLUE"):
            setattr(cls, attr, "")


if not sys.stdout.isatty():
    C.disable()


# --- MCP-клиент (stdio JSON-RPC) ---

class MCPGitClient:
    """Клиент для MCP-сервера git_branch.py (stdio JSON-RPC)."""

    def __init__(self, python: str, server_path: str):
        self._python = python
        self._server = server_path
        self._proc: subprocess.Popen | None = None
        self._next_id = 1

    def is_available(self) -> bool:
        return os.path.isfile(self._server)

    def _start(self):
        if self._proc and self._proc.poll() is None:
            return
        if not self.is_available():
            raise RuntimeError(f"MCP-сервер не найден: {self._server}")

        self._proc = subprocess.Popen(
            [self._python, self._server],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self._request("initialize", {
            "protocolVersion": "2024-11-05",
            "clientInfo": {"name": "dev_assistant_cli", "version": "1.0"},
            "capabilities": {},
        })
        self._notify("notifications/initialized", {})

    def _request(self, method: str, params: dict) -> dict:
        req_id = self._next_id
        self._next_id += 1
        msg = json.dumps({"jsonrpc": "2.0", "id": req_id,
                          "method": method, "params": params},
                         ensure_ascii=False) + "\n"
        self._proc.stdin.write(msg)
        self._proc.stdin.flush()
        line = self._proc.stdout.readline()
        if not line:
            raise RuntimeError("MCP-сервер закрыл канал связи")
        resp = json.loads(line)
        if "error" in resp:
            raise RuntimeError(f"MCP error: {resp['error']}")
        return resp.get("result", {})

    def _notify(self, method: str, params: dict):
        msg = json.dumps({"jsonrpc": "2.0",
                          "method": method, "params": params},
                         ensure_ascii=False) + "\n"
        self._proc.stdin.write(msg)
        self._proc.stdin.flush()

    def list_tools(self) -> list[dict]:
        self._start()
        res = self._request("tools/list", {})
        return res.get("tools", [])

    def call_tool(self, name: str, args: dict | None = None) -> dict:
        self._start()
        res = self._request("tools/call", {
            "name": name,
            "arguments": args or {},
        })
        content = res.get("content", [])
        if not content:
            return {}
        raw_text = content[0].get("text", "{}")
        try:
            return json.loads(raw_text)
        except Exception:
            return {"raw": raw_text}

    def close(self):
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.stdin.close()
                self._proc.wait(timeout=2)
            except Exception:
                self._proc.kill()


# --- Локальный эмбеддер (Ollama) ---

# Модель векторной ноги RAG. bge-m3 хорошо различает русские запросы;
# nomic-embed-text (дефолт дня 28) на этом корпусе ставил на первое место
# почти случайные чанки (cos 0.75 для любого раздела — «всё похоже на всё»),
# поэтому брать его для ответов на вопросы нельзя.
EMBED_MODEL_DEFAULT = "bge-m3"
#: Окно контекста, если Ollama не сообщила своё (`num_ctx` по умолчанию).
DEFAULT_NUM_CTX = 8192
EMBED_PREFIX = "search_document: "        # префиксы — только для nomic
EMBED_BATCH = 16
EMBED_TIMEOUT = 120


class OllamaEmbedder:
    """Эмбеддинги из локальной Ollama (POST /api/embed).

    stdlib-only (urllib) — как и весь автономный ассистент. Любой сбой
    (нет Ollama, нет модели) → None с человекочитаемой причиной: векторная
    нога поиска — улучшение, а не жёсткая зависимость, лексическая (BM25)
    всегда работает.
    """

    def __init__(self, base_url: str = "", model: str = "",
                 timeout: float = EMBED_TIMEOUT):
        base = (base_url or os.environ.get("OLLAMA_URL")
                or os.environ.get("OLLAMA_BASE_URL") or "http://localhost:11434")
        self.base_url = base.rstrip("/").removesuffix("/v1")
        self.model = model or os.environ.get("DEV_ASSISTANT_EMBED_MODEL",
                                             EMBED_MODEL_DEFAULT)
        self.timeout = timeout
        self.dim: int | None = None
        self.last_error: str = ""
        # nomic-* требует префиксы "search_document: / search_query: ",
        # bge-m3 — нет (префикс только шумит).
        self.prefixed = "nomic" in self.model

    def is_available(self) -> bool:
        """Модель есть в `ollama list` (без загрузки её в память)."""
        try:
            with urllib.request.urlopen(
                    f"{self.base_url}/api/tags", timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            self.last_error = f"Ollama недоступна ({self.base_url}): {e}"
            return False
        names = [m.get("name", "") for m in data.get("models", [])]
        base = self.model.split(":")[0]
        if any(n == self.model or n.split(":")[0] == base for n in names):
            return True
        self.last_error = (f"в Ollama нет модели эмбеддера «{self.model}» "
                           f"(ollama pull {self.model})")
        return False

    def _batch(self, texts: list[str], prefix: str) -> list[list[float]]:
        payload = {
            "model": self.model,
            "input": [prefix + t[:6000] for t in texts],
        }
        req = urllib.request.Request(
            f"{self.base_url}/api/embed",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        vecs = data.get("embeddings")
        if not isinstance(vecs, list) or len(vecs) != len(texts):
            raise RuntimeError("Ollama /api/embed: пустой или битый ответ")
        return [[float(x) for x in v] for v in vecs]

    def embed_documents(self, texts: list[str], on_progress=None) -> list[list[float]]:
        """Векторы документов; сбой — RuntimeError (индекс не пишется
        частично: вызывающий решает, оставить ли BM25)."""
        out: list[list[float]] = []
        prefix = EMBED_PREFIX if self.prefixed else ""
        for i in range(0, len(texts), EMBED_BATCH):
            out.extend(self._batch(texts[i:i + EMBED_BATCH], prefix))
            if on_progress is not None:
                on_progress(len(out), len(texts))
        if out:
            self.dim = len(out[0])
        return out

    def embed_query(self, text: str) -> list[float]:
        prefix = "search_query: " if self.prefixed else ""
        return self._batch([text], prefix)[0]


# --- Встроенный автономный RAG (гибрид: BM25 + вектор) ---

RAG_FORMAT_VERSION = 2      # 2 — гибридный индекс с векторами
RRF_K = 60                  # Reciprocal Rank Fusion
COARSE_CHARS = 600          # сколько символов чанка кладётся в вектор
COARSE_LINES = 6            # ...и сколько первых содержательных строк
MAX_CHUNK_CHARS = 2400      # длинная секция режется с перекрытием
CHUNK_OVERLAP = 200


def _normalize(vec: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / n for x in vec]


def _dot(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


_TRANSLIT = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
})


# Нестандартная, но общепринятая в русском языке транслитерация
# иноязычных терминов: с → s|c|sh-sc, х → h|kh, щ → sch|shch...
_TRANSLIT_VARIANTS = ((("дж", "d"), ("j", "dzh")), (("ц", "c"), ("c", "ts")),
                      (("ч", "ch"), ("ch",)), (("ш", "sh"), ("sh",)),
                      (("щ", "sch"), ("sch", "shch")), (("с", "s"), ("s", "c")),
                      (("х", "h"), ("h", "kh")), (("ы", "y"), ("y", "i")),
                      (("е", "e"), ("e", "ie")), (("я", "ya"), ("ya", "ia")),
                      (("ю", "yu"), ("yu", "iu")))


def _translit_variants(word: str) -> list[str]:
    """Варианты транслитерации: «песочница» → pesochnica/pesochnitsa,
    «джанго» → dzhango/dj… (проверка «джанго» ↔ «django» по общему
    корню)."""
    out = {_translit(word)}
    for (src, std), dsts in _TRANSLIT_VARIANTS:
        if dsts == (std,):
            continue
        if src in word:
            out.add(_translit(word.replace(src, std)))
            for d in dsts:
                out.add(_translit(word.replace(src, d)))
    return sorted(out)


def _same_term(cyr: str, ascii_term: str) -> bool:
    """Кириллическое слово — это транслитерация того же латинского
    термина («джанго» ↔ «django», «песочница» ↔ «sandbox» — нет)."""
    a = ascii_term.lower()
    for var in _translit_variants(cyr) + [cyr]:
        if a in var or var in a:
            return True
    return False


def _translit(word: str) -> str:
    """Грубая транслитерация кириллицы в латиницу (для сверки терминов:
    «песочница» ↔ «sandbox», «джанго» ↔ «django»)."""
    return word.lower().translate(_TRANSLIT)


# Константа верхнего уровня в коде: `DEFAULT_GATEWAY_PORT = 8110`.
_CODE_CONST = re.compile(r"^[A-Z][A-Z0-9_]{3,}\s*(?::[^=\n]+)?=")


def _embedding_text(section: str, text: str) -> str:
    """Текст для эмбеддинга: заголовок секции + первые содержательные
    строки (до COARSE_CHARS).

    Заголовок («Требования», «Шлюз локальной LLM») несёт главный сигнал
    темы, а первые строки — суть. Обрезка ровно по N символов режет
    фразу посреди слова и даёт многоязычной модели огрызки («шлюз -
    новый…»), по которым она путает разделы.
    """
    head = [section.strip()]
    used = len(head[0])
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("```", "|---", "| ---")) or line == section.strip():
            continue
        head.append(line)
        used += len(line)
        if used >= COARSE_CHARS or len(head) >= COARSE_LINES:
            break
    out = "\n".join(head)
    return out[:COARSE_CHARS * 2]


class StandaloneRAG:
    """Автономный RAG-движок проекта: гибридный поиск.

    Две ноги (как в `studio/backend/kb.py`, но stdlib-only):
      * лексическая — BM25 со стоп-словами и грубым стеммингом: точные
        термины («bge-m3», «gateway.py», «find_usages»);
      * векторная — эмбеддинги Ollama (bge-m3): смысл без совпадения слов.
    Слияние — RRF (по рангам): шкалы cosine и BM25 несопоставимы.

    Индекс — файл кэша (`.dev_assistant_rag.json`) с хэшем корпуса: если
    какой-то документ появился/изменился/удалён, кэш пересобирается сам.
    Иначе агент отвечал по устаревшим чанкам — то есть не по запросу.

    Индексирует корпус проекта: README*/docs/openspec (документация),
    RELEASE/LINKS/ARCHITECTURE и код бэкенда, фронтенда и скриптов.
    """

    # Корпус: (относительный каталог / имя файла, расширения)
    CORPUS_DIRS = {
        "docs": (".md", ".txt", ".yaml", ".yml", ".json"),
        "openspec": (".md", ".yaml", ".yml"),
        "studio/backend": (".py",),
        "studio/frontend/src": (".ts", ".tsx", ".js", ".jsx", ".css"),
        "scripts": (".py", ".sh"),
    }
    CORPUS_ROOT_FILES = ("RELEASE.md", "LINKS.md", "ARCHITECTURE.md")
    #: Имя агента — документ (`agents/*.md`): на вопрос «для чего нужен
    #: агент support?» ответ должен находиться в его собственном файле.
    CORPUS_AGENTS_DIR = "agents"
    EXCLUDE_DIRS = {
        ".git", ".venv", ".venv-mlx", ".omo", "node_modules", "__pycache__",
        ".idea", ".pytest_cache", ".mypy_cache", ".ruff_cache", "dist",
        "build", "data", "coverage", ".next", ".vscode",
    }
    MAX_FILE_BYTES = 400_000

    STOPWORDS = frozenset("""
        а без бы был была были было быть в вам вас весь во вот все всё вы
        где да даже для до его ее её ей ему если есть еще ещё же за здесь и
        из или им их к как какая какое какой какие кем когда кого кому
        которая которое который которые которых куда ли либо мне мной мой
        моя моё мы на над надо нам нас наш не нет ни но ну о об он она они
        оно от очень по под при про с со так такая такое такие такой там те
        тем теми то того тож тоже той только том тот ту тут ты у уже что
        чтобы это эти этого этой этом этот эту я
        a an and are as at be been by for from has have his her he how i
        in is it its of on or our she that the their them they this to
        was we were what when where which who why will with you your
    """.split())

    def __init__(self, repo_root: str, cache_file: str | None = None,
                 k1: float = 1.5, b: float = 0.75, embedder=None):
        self.repo_root = repo_root
        self.cache_file = cache_file or os.path.join(repo_root, ".dev_assistant_rag.json")
        self.k1 = k1
        self.b = b
        self.embedder = embedder          # None — только BM25 (офлайн/тесты)
        self.embed_model = (embedder.model if embedder is not None
                            else EMBED_MODEL_DEFAULT)
        self.embed_note = ""              # почему векторной ноги нет
        self.chunks: list[dict] = []
        self.doc_lens: list[int] = []
        self.avg_doc_len = 0.0
        self.df: Counter = Counter()
        self.num_docs = 0
        self.corpus_hash = ""
        self._indexed = False
        self.load_cache()

    # ---------- корпус и чанкинг ----------

    @classmethod
    def tokenize(cls, text: str) -> list[str]:
        """Токены: нижний регистр, [a-zа-я0-9_]+, ё→е, без стоп-слов."""
        raw = re.findall(r"[a-zа-я0-9_]{2,}", text.lower().replace("ё", "е"))
        return [t for t in raw if t not in cls.STOPWORDS]

    def discover_files(self) -> list[str]:
        """Файлы корпуса: документация + код (без служебных каталогов)."""
        found: list[str] = []
        for name in os.listdir(self.repo_root) if os.path.isdir(self.repo_root) else []:
            if re.match(r"^README.*\.md$", name, re.IGNORECASE):
                found.append(name)
        for rel_dir, exts in self.CORPUS_DIRS.items():
            base = os.path.join(self.repo_root, rel_dir)
            for root, dirs, files in os.walk(base):
                dirs[:] = sorted(d for d in dirs if d not in self.EXCLUDE_DIRS)
                rel_root = os.path.relpath(root, self.repo_root).replace("\\", "/")
                for f in sorted(files):
                    rel_path = f"{rel_root}/{f}".replace("\\", "/")
                    if rel_path.startswith("<"):          # <memory>, <string>
                        continue
                    if f.endswith(exts):
                        found.append(rel_path)
        for name in self.CORPUS_ROOT_FILES:
            if os.path.isfile(os.path.join(self.repo_root, name)):
                found.append(name)
        agents_dir = os.path.join(self.repo_root, self.CORPUS_AGENTS_DIR)
        try:
            for name in sorted(os.listdir(agents_dir)):
                if name.endswith(".md"):
                    found.append(f"{self.CORPUS_AGENTS_DIR}/{name}")
        except OSError:
            pass
        return sorted(set(found))

    def _corpus_hash(self, files: list[str]) -> str:
        """Хэш корпуса: пути + размер + mtime. Изменилось — индекс устарел."""
        h = hashlib.sha256()
        for rel in files:
            try:
                st = os.stat(os.path.join(self.repo_root, rel))
            except OSError:
                h.update(f"{rel}:gone\n".encode())
                continue
            h.update(f"{rel}:{st.st_size}:{st.st_mtime_ns}\n".encode())
        return h.hexdigest()

    def _split_long(self, text: str) -> list[str]:
        """Длинная секция → под-чанки по границам строк с перекрытием."""
        if len(text) <= MAX_CHUNK_CHARS:
            return [text]
        out, start = [], 0
        while start < len(text):
            end = min(len(text), start + MAX_CHUNK_CHARS)
            if end < len(text):
                cut = text.rfind("\n", start + MAX_CHUNK_CHARS // 2, end)
                if cut != -1:
                    end = cut
            out.append(text[start:end].strip())
            if end >= len(text):
                break
            start = max(start + 1, end - CHUNK_OVERLAP)
        return [c for c in out if c]

    def chunk_file(self, rel_path: str, text: str) -> list[dict]:
        """Разбиение документа на смысловые фрагменты по заголовкам.

        Секция = заголовок + тело до следующего заголовка: заголовок
        («Установка», «Требования») несёт главный сигнал о теме и должен
        попадать и в BM25, и в эмбеддинг. Длинные секции — под-чанки.
        """
        chunks: list[dict] = []
        lines = text.splitlines()
        current_header = os.path.basename(rel_path)
        buffer: list[str] = []

        def flush():
            body = "\n".join(buffer).strip()
            if body:
                for part in self._split_long(body):
                    chunks.append({"section": current_header, "text": part})

        for line in lines:
            header_match = re.match(r"^(#{1,4})\s+(.+)$", line) if \
                rel_path.lower().endswith((".md", ".txt", ".yaml", ".yml")) \
                else None
            # Код: разделители-комментарии (`# ----`, `# ===`) — тоже
            # разбиение, иначе весь файл README-размером (gateway.py ~100 КБ)
            # становится одним чанком, в вектор попадает только докстринг
            # модуля, а `DEFAULT_GATEWAY_PORT = 8110` из середины файла
            # поиском не находится.
            sep_match = (re.match(r"^\s*(?:#|//)\s*[-=*#_]{4,}", line)
                         if header_match is None else None)
            if sep_match:
                if buffer and any(l.strip() and not l.strip().startswith(("#", "//"))
                                  for l in buffer[-40:]):
                    flush()
                    buffer = []
                    current_header = os.path.basename(rel_path)
                buffer.append(line)
                continue
            # Код без линий-разделителей (конфиги, модули с константами):
            # новая секция начинается с константы верхнего уровня —
            # «DEFAULT_GATEWAY_PORT = 8110» попадает в свой чанк, а не в
            # середину стотысячного файла.
            if (header_match is None and sep_match is None and not buffer
                    and len(line) - len(line.lstrip()) == 0
                    and _CODE_CONST.match(line)):
                buffer = [line]
                continue
            if header_match:
                flush()
                buffer = []
                current_header = header_match.group(2).strip()
            buffer.append(line)
        flush()

        return chunks or [{"section": os.path.basename(rel_path), "text": text[:MAX_CHUNK_CHARS]}]

    # ---------- индекс ----------

    def _build_bm25(self):
        self.num_docs = len(self.chunks)
        self.doc_lens = [len(d["tokens"]) for d in self.chunks]
        self.avg_doc_len = (sum(self.doc_lens) / self.num_docs) if self.num_docs else 0.0
        self.df = Counter()
        for d in self.chunks:
            for term in set(d["tokens"]):
                self.df[term] += 1

    def build_index(self, with_embeddings: bool = True, on_progress=None) -> dict:
        """Построение индекса: BM25 всегда, векторы — если есть эмбеддер."""
        if not os.path.isdir(self.repo_root):
            self.chunks = []
            self._indexed = True
            return {"docs": 0, "chunks": 0, "vectors": 0}
        files = self.discover_files()
        corpus_hash = self._corpus_hash(files)
        chunks: list[dict] = []
        for rel_path in files:
            full_path = os.path.join(self.repo_root, rel_path)
            try:
                if os.path.getsize(full_path) > self.MAX_FILE_BYTES:
                    continue
                with open(full_path, encoding="utf-8", errors="replace") as fh:
                    content = fh.read()
            except Exception:
                continue
            for idx, ch in enumerate(self.chunk_file(rel_path, content)):
                tokens = self.tokenize(ch["text"])
                if not tokens:
                    continue
                chunks.append({
                    "id": f"{rel_path}:{idx}",
                    "file": rel_path,
                    "section": ch["section"],
                    "text": ch["text"],
                    "tokens": tokens,
                    "tf": Counter(tokens),
                    "vector": None,
                })

        vectors = 0
        self.embed_note = ""
        if with_embeddings and chunks:
            if self.embedder is None:
                self.embed_note = "векторная нога отключена (нет эмбеддера)"
            elif not self.embedder.is_available():
                self.embed_note = (self.embedder.last_error
                                   or "эмбеддер Ollama недоступен")
            else:
                try:
                    inputs = [_embedding_text(d["section"], d["text"])
                              for d in chunks]
                    vecs = self.embedder.embed_documents(inputs, on_progress=on_progress)
                except Exception as e:
                    self.embed_note = f"эмбеддинги не посчитаны: {e}"
                else:
                    for d, v in zip(chunks, vecs):
                        d["vector"] = _normalize(v)
                    vectors = len(vecs)

        self.chunks = chunks
        self.corpus_hash = corpus_hash
        self._build_bm25()
        self._indexed = True
        self.save_cache()
        return {"docs": len(files), "chunks": self.num_docs, "vectors": vectors}

    def ensure_fresh(self) -> bool:
        """Пересобрать индекс, если его нет, корпус изменился или кэш
        собран без векторов, а эмбеддер теперь доступен. True — пересобран."""
        files = self.discover_files() if os.path.isdir(self.repo_root) else []
        if self.embedder is not None and not self._no_vectors() \
                and not self.embedder.is_available():
            # модель эмбеддера пропала из Ollama (индекс с ней ещё лежит):
            # ищем по готовым векторам, но не пытаемся считать новые
            self.embedder = None
            self.embed_note = "эмбеддер Ollama недоступен — поиск по готовым векторам"
        if self._indexed and files and self._corpus_hash(files) == self.corpus_hash:
            # кэш BM25-овый, а Ollama уже с моделью — досчитываем векторы
            # (иначе вопрос «почему опять отвечает неточно?»)
            if (self.chunks and not any(d.get("vector") for d in self.chunks)
                    and self.embedder is not None
                    and self.embedder.is_available()):
                self.build_index()
                return True
            return False
        self.build_index()
        return True

    # ---------- поиск ----------

    def _no_vectors(self) -> bool:
        """Индекс без векторной ноги: эмбеддера нет ИЛИ кэш собран без
        Ollama. Иначе `search` каждый раз пыталась бы считать эмбеддинг
        запроса впустую (и гейт принимал решение по пустой ноге)."""
        if self.embedder is None or not self.num_docs:
            return True
        return not any(d.get("vector") for d in self.chunks)

    def embedder_ready(self) -> bool:
        """Эмбеддер доступен И индекс содержит векторы (иначе — BM25)."""
        return not self._no_vectors()

    # Слова вопроса — не тема: «кто ты», «как устроен» совпадали с любым
    # кодом (тест-фикстуры, «как-то» в прозе) и давали «ответ не по
    # запросу». Служебные и общие слова релевантности не добавляют.
    QUERY_STOPWORDS = frozenset("""
        ты вы сам сама себе нас это вот тут там тогда сейчас может можно
        нужно надо сказать скажи расскажи объясни опиши покажи про
        привет здравствуйте спасибо пожалуйста дела
        проект проекте проекту проекта проектом программы системе
        система системе системе сервис сервиса сервисе приложение
        приложение приложении работает работают работаеть устроен
        устроено устроен устроена устройство сделано делается делает
        вообще просто именно какой каких какие зачем почему сколько
        устроено как-то како ом кот туз тост ток тик тек кет кит кут
        тыл том так кто
        """.split())

    def _bm25_scores(self, query: str) -> dict[int, float]:
        q_terms = sorted({t for t in self.tokenize(query)
                          if not self._is_stopword(t)})
        if not q_terms or not self.num_docs:
            return {}
        scores: dict[int, float] = {}
        for i, d in enumerate(self.chunks):
            score = 0.0
            doc_len = self.doc_lens[i]
            for term in q_terms:
                tf = self._match_tf(term, d["tf"])
                if not tf:
                    continue
                # df термина со словоформами: «порт» в чанке — «порты»,
                # «портов» и т.п. (иначе idf завышен: df точной формы)
                df = self._df_of(term)
                idf = max(0.1, math.log((self.num_docs - df + 0.5) / (df + 0.5) + 1.0))
                denom = tf + self.k1 * (1 - self.b + self.b * (doc_len / (self.avg_doc_len or 1.0)))
                score += idf * (tf * (self.k1 + 1)) / denom
            if score > 0:
                scores[i] = score
        return scores

    def _vector_scores(self, query: str) -> dict[int, float] | None:
        """Cosine-поиск; None — векторной ноги нет (индекс без векторов
        или Ollama отказала на запросе — поиск деградирует на BM25)."""
        if self._no_vectors():
            return None
        try:
            qv = _normalize(self.embedder.embed_query(query))
        except Exception:
            return None
        out: dict[int, float] = {}
        for i, d in enumerate(self.chunks):
            v = d.get("vector")
            if v:
                out[i] = _dot(qv, v)
        return out

    def search(self, query: str, top_k: int = 5,
               weights: tuple[float, float] = (1.0, 1.0),
               with_misses: bool = False) -> list[dict]:
        """Гибридный поиск: RRF(BM25, векторы). Возвращает фрагменты с
        полями file, section, text, score (RRF) и relevance (0..1).

        `weights` — (bm25, vector): 0 отключает ногу; уточнение (follow-up)
        смотрит лексику отдельно, потому что код и точные имена в
        эмбеддингах «серые».
        """
        if not self._indexed or not self.chunks:
            self.build_index()
        if self.num_docs == 0:
            return []
        w_bm, w_vec = weights
        bm25 = self._bm25_scores(query) if w_bm > 0 else None
        vec = self._vector_scores(query) if w_vec > 0 else None

        fused: dict[int, float] = {}
        for ranks, w in ((bm25, w_bm), (vec, w_vec)):
            if not ranks or w <= 0:
                continue
            for rank, (i, _s) in enumerate(
                    sorted(ranks.items(), key=lambda kv: kv[1], reverse=True), 1):
                fused[i] = fused.get(i, 0.0) + w * (1.0 / (RRF_K + rank))
        if with_misses:
            # полный проход: нужны все чанки, а не только совпавшие
            for i in range(self.num_docs):
                fused.setdefault(i, 0.0)

        out = []
        best_vec = max(vec.values()) if vec else 0.0
        for i, score in sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:top_k]:
            d = self.chunks[i]
            vcos = vec.get(i) if vec else None
            out.append({
                "score": score,
                "relevance": (vcos / best_vec) if (vcos and best_vec) else None,
                "cosine": vcos,
                "bm25": bm25.get(i) if bm25 else None,
                "file": d["file"],
                "section": d["section"],
                "text": d["text"],
            })
        return out

    # Окончания для грубого стемминга df (словоформы корпуса).
    _TAILS = ("а", "я", "у", "ю", "е", "и", "ы", "ой", "ей", "ов", "ев",
              "ий", "ый", "ые", "ами", "ями", "ах", "ях", "ия", "ья", "ь")

    def _df_of(self, term: str) -> int:
        """df термина с учётом словоформ: max по словарю корпуса
        (префиксные варианты + типовые окончания). Один и тот же
        показатель используют поиск и гейт — оценки не двоятся."""
        best = self.df.get(term, 0)
        for k in range(len(term) - 1, 3, -1):
            best = max(best, self.df.get(term[:k], 0))
        for tail in self._TAILS:
            cand = term + tail
            if cand not in self.df:
                continue
            n = self.df[cand]
            if len(term) >= 4 and term.endswith(("а", "я", "о", "е", "ь")):
                # «память» → «памяти»: основа — до окончания
                stem = term[:-1]
                n = max(n, self.df.get(stem, 0), self.df.get(stem + "и", 0),
                        self.df.get(stem + "ью", 0), self.df.get(stem + "я", 0))
            best = max(best, n)
        return best

    def _is_stopword(self, word: str) -> bool:
        """Стоп-слово запроса с учётом словоформы («устроена» → «устроен»)."""
        w = word.lower().replace("ё", "е")
        if w in self.QUERY_STOPWORDS:
            return True
        return any(len(w) >= 5 and len(sw) >= 5 and w[:5] == sw[:5]
                   for sw in self.QUERY_STOPWORDS)

    def _match_tf(self, q_term: str, tf: dict[str, int]) -> int:
        """Частота термина запроса в чанке: точное совпадение, затем
        грубый стемминг (общий префикс 5 символов: «шлюзу» ↔ «шлюз»,
        «настройку» ↔ «настройки», «контекста» ↔ «контексты»)."""
        n = tf.get(q_term, 0)
        if n or len(q_term) < 5:
            return n
        for u in tf:
            if len(u) >= 5 and q_term[:5] == u[:5]:
                return tf[u]
        return 0

    def document_relevance(self, query: str) -> dict[str, float]:
        """BM25 на уровне файла: max по чанкам документа.

        Для решения «о чём вообще вопрос» файл важнее чанка: длинный
        README/спека не должны «проигрывать» мелкому коду только потому,
        что каждый их чанк коротко сматчился.
        """
        out: dict[str, float] = {}
        for i, score in self._bm25_scores(query).items():
            f = self.chunks[i]["file"]
            if score > out.get(f, 0.0):
                out[f] = score
        return out

    def has_documents(self) -> bool:
        return len(self.discover_files()) > 0

    def stats(self) -> dict:
        files = self.discover_files()
        vectors = sum(1 for d in self.chunks if d.get("vector"))
        return {
            "indexed": self._indexed and self.num_docs > 0,
            "files": len(files),
            "chunks": self.num_docs,
            "vectors": vectors,
            "vocab": len(self.df),
            "avg_doc_len": round(self.avg_doc_len, 1),
            "mode": ("hybrid" if vectors else "bm25") if self.num_docs else "empty",
            "embed_model": self.embed_model,
            "embed_note": self.embed_note,
            "stale": (bool(files) and self._corpus_hash(files) != self.corpus_hash)
                     if self._indexed else bool(files),
        }

    @property
    def corpus(self):
        class _CorpusView:
            def __init__(self, parent):
                self._parent = parent
            def files(self):
                return self._parent.discover_files()
        return _CorpusView(self)

    def save_cache(self):
        data = {
            "version": RAG_FORMAT_VERSION,
            "embed_model": self.embed_model,
            "corpus_hash": self.corpus_hash,
            "corpus": [
                {"id": d["id"], "file": d["file"], "section": d["section"],
                 "text": d["text"], "vector": d.get("vector")}
                for d in self.chunks
            ],
        }
        tmp = self.cache_file + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, self.cache_file)
        except Exception:
            try:
                os.remove(tmp)
            except OSError:
                pass

    def load_cache(self) -> bool:
        """Кэш из файла. Старый формат (без версии/хэша) не принимается:
        ранний файл лежал в репозитории и описывал документы, которых уже
        нет, — поиск находил устаревшие чанки."""
        if not os.path.isfile(self.cache_file):
            return False
        try:
            with open(self.cache_file, encoding="utf-8") as f:
                data = json.load(f)
            if data.get("version") != RAG_FORMAT_VERSION:
                return False
            raw_docs = data.get("corpus", [])
            if not raw_docs:
                return False
            self.chunks = []
            for d in raw_docs:
                tokens = self.tokenize(d["text"])
                vec = d.get("vector")
                self.chunks.append({
                    "id": d["id"],
                    "file": d["file"],
                    "section": d["section"],
                    "text": d["text"],
                    "tokens": tokens,
                    "tf": Counter(tokens),
                    "vector": _normalize(vec) if vec else None,
                })
            self.corpus_hash = data.get("corpus_hash", "")
            self._build_bm25()
            self._indexed = True
            return True
        except Exception:
            return False


# Алиас для обратной совместимости
ProjectRAG = StandaloneRAG


# --- Исследователь проекта (сбор структуры) ---

class ProjectExplorer:
    """Анализирует репозиторий: дерево файлов, зависимости, стек, точку входа."""

    def __init__(self, repo_root: str):
        self.repo_root = repo_root

    def scan(self) -> dict:
        exclude = {
            ".git", ".venv", ".venv-mlx", "__pycache__", "node_modules",
            ".idea", ".pytest_cache", ".gemini", "dist", "build"
        }
        tree = []
        configs = []
        source_files = []

        for root, dirs, files in os.walk(self.repo_root):
            dirs[:] = [d for d in dirs if d not in exclude]
            rel_root = os.path.relpath(root, self.repo_root).replace("\\", "/")
            if rel_root == ".":
                rel_root = ""

            for f in files:
                if f.startswith(".") and f not in (".env.example", ".gitignore"):
                    continue
                rel_path = (f"{rel_root}/{f}" if rel_root else f).replace("\\", "/")
                tree.append(rel_path)

                if f in ("package.json", "requirements.txt", "pyproject.toml",
                         "Dockerfile", "Makefile", "docker-compose.yml"):
                    configs.append(rel_path)
                elif f.endswith((".py", ".ts", ".tsx", ".js", ".go", ".rs", ".java")):
                    source_files.append(rel_path)

        # Выжимка содержимого конфигов
        config_contents = {}
        for c in configs[:4]:
            try:
                full = os.path.join(self.repo_root, c)
                with open(full, encoding="utf-8", errors="replace") as fh:
                    config_contents[c] = fh.read(1500)
            except Exception:
                pass

        return {
            "total_files": len(tree),
            "tree_sample": tree[:60],
            "configs": config_contents,
            "source_sample": source_files[:30],
        }


# --- LLM Клиент ---

class LLMClient:
    """Клиент локальной LLM (Ollama) с автоопределением установленной модели.

    Кроме ответа держит `last_usage` — сколько токенов реально ушло в
    промпт (`prompt_eval_count`) и вышло в ответ (`eval_count`), — и
    `context_window`: окно модели из `num_ctx` Ollama (`/api/show`).
    Оба числа нужны, чтобы показать «израсходовано / осталось» для
    диалога, а не оценивать на глаз.
    """

    def __init__(self, base_url: str = "http://localhost:11434"):
        self.base_url = os.environ.get("OLLAMA_BASE_URL", base_url).rstrip("/")
        if self.base_url.endswith("/v1"):
            self.base_url = self.base_url[:-3]
        self._model: str | None = None
        self.last_usage: dict | None = None
        self._context_window: dict[str, int] = {}

    def get_model(self) -> str | None:
        if self._model:
            return self._model
        try:
            req = urllib.request.Request(f"{self.base_url}/api/tags")
            with urllib.request.urlopen(req, timeout=2) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            models = [m.get("name") for m in data.get("models", []) if m.get("name")]
            if not models:
                return None
            pref = [
                "josiefied-qwen3-8b-abliterated:latest",
                "glm-4.7-flash:q4_K_M",
                "qwen3-coder:30b",
                "huihui-qwen3.5-27b-abliterated:latest",
                "huihui-qwen3.5-35b-moe-abliterated:latest",
            ]
            for p in pref:
                if p in models:
                    self._model = p
                    return p
            self._model = models[0]
            return self._model
        except Exception:
            return None

    def is_available(self) -> bool:
        return self.get_model() is not None

    def context_window(self, model: str | None = None) -> int | None:
        """Окно контекста модели: `min(num_ctx, максимум архитектуры)`.

        Ollama показывает два числа: сколько токенов физически влезает по
        архитектуре (`*.context_length`) и сколько памяти реально выделено
        под контекст (`num_ctx` в параметрах Modelfile). Работает по
        меньшему — иначе UI обещал бы место, которого нет.
        """
        model = model or self.get_model()
        if not model:
            return None
        if model in self._context_window:
            return self._context_window[model]
        try:
            req = urllib.request.Request(
                f"{self.base_url}/api/show",
                data=json.dumps({"model": model}).encode("utf-8"),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                info = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None
        arch = next((v for k, v in (info.get("model_info") or {}).items()
                     if k.endswith(".context_length") and isinstance(v, int)), None)
        num_ctx = None
        params = info.get("parameters") or ""
        if isinstance(params, dict):
            num_ctx = params.get("num_ctx")
        else:
            for line in str(params).splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[0] == "num_ctx":
                    try:
                        num_ctx = int(float(parts[1]))
                    except ValueError:
                        num_ctx = None
        window = min(x for x in (arch, num_ctx) if x) if (arch or num_ctx) else None
        if window:
            self._context_window[model] = window
        return window

    def generate(self, system: str, user_prompt: str) -> str:
        model = self.get_model()
        if not model:
            return ""
        url = f"{self.base_url}/api/chat"
        window = self.context_window(model) or DEFAULT_NUM_CTX
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "options": {
                "temperature": 0.2,
                # окно — из модели, а не «как у всех»: modelspecific num_ctx
                "num_ctx": window,
            }
        }
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            msg = data.get("message", {})
            content = msg.get("content", "").strip()
            content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
            prompt_tokens = int(data.get("prompt_eval_count") or 0)
            answer_tokens = int(data.get("eval_count") or 0)
            # Остаток окна: окно − (промпт + ответ). Ответ занимает место в
            # окне наравне с промптом — его токены войдут в историю.
            self.last_usage = {
                "model": model,
                "prompt_tokens": prompt_tokens,
                "answer_tokens": answer_tokens,
                "total_tokens": prompt_tokens + answer_tokens,
                "context_window": window,
                "remaining": max(0, window - prompt_tokens - answer_tokens),
                "percent": round((prompt_tokens + answer_tokens) * 100.0 / window, 1)
                if window else 0.0,
            }
            return content
        except Exception:
            self.last_usage = None
            return ""


SYSTEM_PROMPT = """\
Ты — ассистент разработчика в текущем проекте.
Отвечай ТОЛЬКО на вопрос пользователя и ТОЛЬКО по предоставленному контексту (RAG-фрагменты документации и кода + состояние Git).

Жёсткие правила:
1. Каждый факт в ответе должен опираться на контекст. Номера тестов, версии, счётчики, пути и команды бери дословно из контекста — не додумывай и не «улучшай» цифры.
2. Если в контексте нет ответа на вопрос — так и скажи: «В документации и коде проекта этого нет» — и предложи, где искать (файл, каталог), но НЕ подменяй ответ соседней информацией.
3. Не пересказывай контекст целиком: используй только фрагменты, относящиеся к вопросу. Прочие разделы, дни и модули не упоминай.
4. Факты о «сейчас» (ветка, изменённые файлы) бери из блока Git, а не из документации.
5. Отвечай кратко, структурированно, по существу, на русском языке. Не перечисляй внутренние ID чанков, коэффициенты релевантности и сырые дампы. Ссылайся на файлы проекта.
6. Если пользователь просит СОЗДАТЬ, НАПИСАТЬ или ОБНОВИТЬ файл (README.md, документ в docs/, спецификацию, код, конфиг):
   - Оформи файл строго внутри блока:
```create_file:относительный_путь_к_файлу
содержимое файла
```
   - Ассистент автоматически запишет этот файл в проект и обновит RAG-индекс.
   - Напиши краткое пояснение, что было сделано.
7. Вопрос может продолжаться предыдущим: «какой порт у НЕГО по умолчанию?» — подставляй, о чём речь, из последнего вопроса диалога.
   Но факты — только из контекста; продолжение не отменяет правило 2.

Историю диалога получаешь отдельным блоком «Предыдущие вопросы» — используй её только для понимания вопроса, не как источник фактов.
"""


# --- CLI Ассистент Разработчика ---

AUTOCOMPLETE_COMMANDS = [
    "/help", "help",
    "/clear", "clear",
    "/create", "create",
    "/git", "git",
    "/files", "files",
    "/diff", "diff",
    "/search", "search",
    "/status", "status",
    "/tools", "tools",
    "/docs", "docs",
    "/index", "index",
    "/quit", "quit",
    "/exit", "exit",
]

HELP_MENU = """\
{bold}Список доступных команд:{reset}
  {cyan}help{reset} (или {cyan}/help{reset})          — показать этот список команд
  {cyan}help <вопрос>{reset}           — задать вопрос о проекте (RAG + Git + LLM)
  {cyan}create <путь> [текст]{reset}   — создать файл в проекте и добавить в RAG
  {cyan}clear{reset} (или {cyan}/clear{reset})        — очистить экран терминала
  {cyan}git{reset} (или {cyan}/git{reset})            — текущая git-ветка проекта (через MCP)
  {cyan}files{reset} (или {cyan}/files{reset})        — список изменённых файлов (через MCP)
  {cyan}diff{reset} (или {cyan}/diff{reset})          — diff рабочего дерева (через MCP)
  {cyan}status{reset} (или {cyan}/status{reset})      — статус компонентов (RAG, MCP, LLM, Git)
  {cyan}search <запрос>{reset}        — поиск по документации и схемам (RAG)
  {cyan}docs{reset}                   — список файлов документации в корпусе RAG
  {cyan}tools{reset}                  — список инструментов MCP-сервера
  {cyan}index{reset}                  — принудительно пересобрать RAG-индекс
  {cyan}exit{reset} (или {cyan}quit{reset})          — выход из ассистента

{green}💬 Диалог можно вести напрямую без ввода команд!{reset}
{dim}   Задавайте вопросы по архитектуре или просите создать файлы (например: «создай README в папке docs»).
   Если документация отсутствует — ассистент сам исследует проект, создаст её и сохранит в RAG.
   Для автодополнения команд нажмите клавишу {bold}Tab{reset}.{dim}{reset}
"""


class DevAssistantCLI:
    """CLI-ассистент разработчика дня 31."""

    HISTORY_LIMIT = 3          # сколько предыдущих вопросов учитывать

    def __init__(self, repo_root: str = REPO_ROOT):
        self.repo_root = repo_root
        self._history: list[dict] = []   # [{q, files}] — понимание follow-up
        # Векторная нога RAG — локальная Ollama (bge-m3). Нет модели —
        # поиск остаётся лексическим (BM25), а `/status` скажет почему.
        self.rag = StandaloneRAG(repo_root, embedder=OllamaEmbedder())
        self.mcp = MCPGitClient(sys.executable, MCP_SERVER)
        self.llm = LLMClient()
        self.explorer = ProjectExplorer(repo_root)

    def _setup_readline(self):
        """Настройка автодополнения команд по нажатию клавиши Tab."""
        if readline is None:
            return
        def completer(text: str, state: int):
            matches = [cmd for cmd in AUTOCOMPLETE_COMMANDS if cmd.startswith(text)]
            if state < len(matches):
                return matches[state]
            return None

        readline.set_completer(completer)
        readline.set_completer_delims(" \t\n")
        doc = getattr(readline, "__doc__", "") or ""
        if "libedit" in doc:
            readline.parse_and_bind("bind ^I rl_complete")
        else:
            readline.parse_and_bind("tab: complete")

    def run_interactive(self):
        """Интерактивный REPL."""
        self._setup_readline()
        self._print_banner()

        if not self.rag.stats()["indexed"] or self.rag.stats()["stale"]:
            st = self.rag.build_index()
            print(f"{C.DIM}Индекс RAG: {st['chunks']} фрагментов, "
                  f"{st['vectors']} векторов.{C.RESET}")

        while True:
            try:
                line = input(f"{C.BOLD}{C.CYAN}assistant>{C.RESET} ").strip()
            except (EOFError, KeyboardInterrupt):
                print(f"\n{C.DIM}Выход.{C.RESET}")
                break

            if not line:
                continue

            if not self.execute_command(line):
                break

    def execute_command(self, line: str) -> bool:
        """Выполнить команду. Возвращает False если запрошен выход."""
        parts = line.split(None, 1)
        raw_cmd = parts[0]
        cmd = raw_cmd.lower()
        arg = parts[1] if len(parts) > 1 else ""

        if cmd in ("/quit", "quit", "/exit", "exit", "/q", "q"):
            print(f"{C.DIM}До свидания!{C.RESET}")
            self.mcp.close()
            return False

        if cmd in ("/clear", "clear", "/cls", "cls"):
            self.cmd_clear()
            return True

        if cmd in ("/help", "help"):
            if arg:
                self.cmd_help(arg)
            else:
                self.cmd_help_menu()
            return True

        if cmd in ("/create", "create"):
            self.cmd_create(arg)
            return True

        if cmd in ("/git", "git"):
            self.cmd_git()
            return True
        if cmd in ("/files", "files"):
            self.cmd_files()
            return True
        if cmd in ("/diff", "diff"):
            self.cmd_diff()
            return True

        if cmd in ("/status", "status"):
            self.cmd_status()
            return True
        if cmd in ("/tools", "tools"):
            self.cmd_tools()
            return True
        if cmd in ("/docs", "docs"):
            self.cmd_docs()
            return True
        if cmd in ("/index", "index"):
            self.cmd_index()
            return True
        if cmd in ("/search", "search"):
            self.cmd_search(arg)
            return True

        if cmd.startswith("/"):
            print(f"{C.RED}Неизвестная команда: {cmd}{C.RESET}")
            print(f"Введите {C.CYAN}help{C.RESET} для просмотра списка команд.")
            return True

        # Прямой диалог
        self.cmd_help(line)
        return True

    def cmd_clear(self):
        """Очистить экран терминала."""
        sys.stdout.write("\033[H\033[2J\033[3J")
        sys.stdout.flush()
        if os.name == "posix":
            os.system("clear")
        elif os.name == "nt":
            os.system("cls")
        self._print_banner()

    def _print_banner(self):
        llm_model = self.llm.get_model()
        llm_status = f"{C.GREEN}{llm_model}{C.RESET}" if llm_model else f"{C.YELLOW}офлайн (без Ollama){C.RESET}"
        print(f"""
{C.BOLD}{C.MAGENTA}══════════════════════════════════════════════════════════{C.RESET}
{C.BOLD}        🚀 CLI Ассистент разработчика (День 31){C.RESET}
{C.DIM}   RAG (README, docs) + MCP (git) + LLM{C.RESET}
{C.BOLD}{C.MAGENTA}══════════════════════════════════════════════════════════{C.RESET}
Проект: {C.BOLD}{self.repo_root}{C.RESET}
LLM модель: {llm_status}
MCP сервер: {C.GREEN if self.mcp.is_available() else C.RED}{MCP_SERVER}{C.RESET}

{C.GREEN}💬 Задайте вопрос по проекту или попросите создать файл (например: «создай README в папке docs»){C.RESET}
{C.DIM}Команды: {C.BOLD}help{C.RESET}{C.DIM} (список команд), {C.BOLD}clear{C.RESET}{C.DIM} (очистка), {C.BOLD}create <файл>{C.RESET}{C.DIM}, {C.BOLD}Tab{C.RESET}{C.DIM} (автодополнение).{C.RESET}
""")

    def cmd_help_menu(self):
        print(HELP_MENU.format(bold=C.BOLD, reset=C.RESET, cyan=C.CYAN, green=C.GREEN, dim=C.DIM))

    def create_file(self, rel_path: str, content: str) -> tuple[bool, str]:
        """Безопасное создание файла в проекте и переиндексация RAG."""
        rel = rel_path.strip().replace("\\", "/").lstrip("/")
        if ".." in rel.split("/"):
            return False, "Выход за пределы проекта (..) запрещён"
        full = os.path.abspath(os.path.join(self.repo_root, rel))
        if not full.startswith(self.repo_root):
            return False, "Путь вне проекта запрещён"
        try:
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8") as f:
                f.write(content)
            self.rag.build_index()
            return True, full
        except Exception as e:
            return False, str(e)

    def _extract_and_create_files(self, text: str) -> list[str]:
        """Поиск блоков ```create_file:<путь> и их сохранение."""
        created = []
        pattern = re.compile(r"```create_file:([^\n]+)\n(.*?)\n```", re.DOTALL)
        for match in pattern.finditer(text):
            rel_path = match.group(1).strip()
            content = match.group(2)
            ok, msg = self.create_file(rel_path, content)
            if ok:
                created.append(rel_path)
                print(f"{C.BOLD}{C.GREEN}✓ Файл создан:{C.RESET} {rel_path} ({len(content.splitlines())} строк)")
                print(f"{C.DIM}  Добавлен в RAG-индекс проекта.{C.RESET}")
            else:
                print(f"{C.RED}✗ Ошибка создания файла {rel_path}: {msg}{C.RESET}")
        return created

    def ensure_project_knowledge(self, query: str) -> bool:
        """Если информации о проекте нет — собирает структуру, создаёт документацию и пишет в RAG."""
        has_any_docs = self.rag.has_documents()
        initial_hits = self.rag.search(query, top_k=2) if has_any_docs else []

        if has_any_docs and initial_hits:
            return False  # Информация уже есть

        print(f"{C.YELLOW}ℹ️ Информация по проекту отсутствует в RAG. Исследую проект и создаю документацию...{C.RESET}")

        # 1. Сбор информации о репозитории
        scan_data = self.explorer.scan()
        git_branch = self._get_branch_only()
        git_files_res = {}
        if self.mcp.is_available():
            try:
                git_files_res = self.mcp.call_tool("git_files")
            except Exception:
                pass

        # 2. Промпт для генерации базовой документации
        prompt = f"""\
Проанализируй структуру проекта и составь базовую документацию:
Ветка Git: {git_branch or 'master'}
Всего файлов в репозитории: {scan_data['total_files']}
Примеры файлов: {', '.join(scan_data['tree_sample'][:30])}
Конфигурационные файлы: {list(scan_data['configs'].keys())}
Примеры исходников: {', '.join(scan_data['source_sample'][:20])}

Сформируй документацию и оформи сохранение файла строго в блоке:
```create_file:docs/PROJECT_STRUCTURE.md
# Описание структуры проекта

## Назначение и стек
(кратко опиши проект)

## Структура файлов и каталогов
(опиши ключевые папки)

## Git и разработка
(опиши ветку и инструкции)
```
"""
        doc_content = self.llm.generate(SYSTEM_PROMPT, prompt)
        created = False
        if doc_content:
            created_files = self._extract_and_create_files(doc_content)
            if created_files:
                created = True

        if not created:
            # Fallback запись, если LLM офлайн или не вернула create_file блок
            fallback_text = (
                "# Описание проекта\n\n"
                f"- Всего файлов: {scan_data['total_files']}\n"
                f"- Ветка: {git_branch}\n"
                f"- Ключевые файлы: {', '.join(scan_data['source_sample'][:15])}\n\n"
                f"{doc_content or ''}\n"
            )
            self.create_file("docs/PROJECT_STRUCTURE.md", fallback_text)
            print(f"{C.GREEN}✓ Создан файл docs/PROJECT_STRUCTURE.md и добавлен в RAG{C.RESET}")

        return True

    def cmd_create(self, arg: str):
        """Команда создания файла в проекте."""
        if not arg.strip():
            print(f"{C.YELLOW}Укажите путь к файлу: create <путь> [описание]{C.RESET}")
            return
        parts = arg.split(None, 1)
        path = parts[0]
        desc = parts[1] if len(parts) > 1 else f"Создай файл {path} с подробным содержанием по архитектуре проекта."

        prompt = (f"Пользователь просит создать файл `{path}`.\n"
                  f"Требования: {desc}\n"
                  f"Сгенерируй содержимое файла и оформи его строго в блоке ```create_file:{path}\n...")
        self.cmd_help(prompt)

    def cmd_help(self, question: str):
        """Ответ на вопрос о проекте через RAG + Git + LLM."""
        print(f"\n{C.DIM}Думаю над ответом...{C.RESET}")

        # Индекс мог устареть (документ появился/изменился) — пересобираем.
        self._refresh_index_if_stale()

        # Проверяем, есть ли документация; если нет — собираем и записываем в RAG
        self.ensure_project_knowledge(question)

        # 1. Поиск в RAG + отсечение нерелевантного (порог по шкале) —
        #    иначе в промпт уходили 5 «ближайших» фрагментов на любой вопрос,
        #    и модель отвечала о том, чего в вопросе не спрашивали.
        #    Follow-up («какой порт у него?») ищется вместе с предыдущим
        #    вопросом, иначе «он» ни на что не указывает.
        search_query = self._resolve_follow_up(question)
        hits, dropped = self._retrieve(search_query, top_k=5)
        supplement: list[dict] = []
        if not hits:
            self._answer_not_found(question, dropped)
            self._remember(question, [])
            return
        # Follow-up уточняет деталь, которой в найденных фрагментах может
        # не быть: «какой порт?» нашёл описание шлюза, а сам порт — в
        # другом фрагменте тех же файлов. Добираем его отдельным блоком.
        if search_query != question:
            hits, supplement = self._supplement_follow_up(hits, question)

        # 2. Git контекст через MCP
        git_context = self._get_git_context()

        # Формируем контекст для LLM — только уверенно относящиеся к
        # вопросу фрагменты: хвост «почти нулевых» соседей заставляет
        # модель пересказывать чужие разделы
        best_cos = max((h["cosine"] for h in hits if h.get("cosine") is not None),
                       default=0.0)
        # без векторов (офлайн BM25 / тесты) cosine = None: отбор по
        # плотности здесь невозможен, гибридный порядок уже отработан
        strong = [h for h in hits
                  if h.get("cosine") is None
                  or (h["cosine"] >= self.CONTEXT_MIN_COSINE
                      and h["cosine"] >= self.CONTEXT_RELATIVE * best_cos)]
        if best_cos == 0.0:
            strong = list(hits)
        doc_chunks = []
        source_files = set()

        def add(h):
            f_path = h.get("file", "")
            if f_path:
                source_files.add(f_path)
            sec = h.get("section", "")
            txt = (h.get("text") or "").strip()[:1200]
            doc_chunks.append(f"[{len(doc_chunks) + 1}] {f_path} ({sec}):\n{txt}")

        for h in strong or hits[:1]:
            add(h)
        # follow-up: выдержка с ответом на уточнение («порт = 8110») может
        # лежать ниже порога близости — она обязана попасть в контекст,
        # но отдельным блоком «в том же файле», а не как равный хиты
        if supplement:
            doc_chunks.append("Дополнительно — фрагменты тех же файлов, "
                              "прямо упоминающие слова уточнения:")
            for h in supplement:
                if h not in (strong or hits[:1]):
                    add(h)

        doc_context = "\n\n".join(doc_chunks)

        history_block = ""
        if self._history:
            prev = "\n".join(f"- {h['q']}" for h in self._history)
            history_block = f"Предыдущие вопросы диалога (только для понимания «он/это»):\n{prev}\n\n"

        user_prompt = f"""\
Вопрос разработчика:
{question}

{history_block}Текущее состояние Git-репозитория (факты о «сейчас», не требуют поиска по документации):
{git_context or '(Git недоступен)'}

Контекст документации и кода проекта (RAG, фрагменты отсортированы по релевантности):
{doc_context}
"""

        # 3. Вызов LLM
        answer = self.llm.generate(SYSTEM_PROMPT, user_prompt)

        if answer:
            self._remember(question, hits)
            print(f"\n{C.BOLD}{C.GREEN}=== Ответ ассистента ==={C.RESET}\n")
            created_files = self._extract_and_create_files(answer)

            clean_display = re.sub(r"```create_file:[^\n]+\n.*?\n```", "", answer, flags=re.DOTALL).strip()
            if clean_display:
                print(clean_display)

            if source_files:
                sources_str = ", ".join(sorted(source_files))
                print(f"\n{C.DIM}📄 Источники RAG: {sources_str}{C.RESET}")
            br = self._get_branch_only()
            if br:
                print(f"{C.DIM}🌿 Ветка Git: {br}{C.RESET}")
            self._print_usage()
            print("")
            return

        # 4. Fallback: если LLM офлайн
        self._fallback_answer(question, hits, git_context)

    # --- отбор контекста по запросу ---

    # Гейт релевантности. Cosine bge-m3 на корпусе проекта сжат (все
    # документы «про проект»: честный ответ 0.60–0.69, посторонний вопрос
    # 0.42–0.52), поэтому решают два сигнала: «плотность» близости
    # (средний cosine по топ-15) и корпусность терминов запроса (IDF
    # BM25: «борщ» и «биткоин» о теме проекта не говорят, даже если
    # эмбеддер что-то нашёл). Дополнительно: термин-спрашивают-а-в-
    # корпусе-его-нет → вопрос посторонний.
    REL_COARSE = 0.60      # средний cosine топ-15 — тема найдена
    REL_CONSISTENT = 0.55  # топ-2 оба выше — тема устойчива
    DOC_BM25_STRONG = 6.0  # BM25 файла, при котором термин реально найден
    BM25_MIN_DOC = 4.0     # BM25 файла для «слабой близости + BM25»
    # BM25 растёт с размером корпуса (idf ~ log N): пороги домножаются
    # на «масштаб BM25» относительно корпуса проекта (~3k чанков), иначе
    # на маленьком проекте совпадение «шлюз» не дотягивало бы до порога
    BM25_REF_CHUNKS = 3000
    BM25_MIN_DENSITY = 0.47  # минимальная «плотность» для поддержки BM25
    BM25_DENSITY_ACRONYM = 0.44  # ...для аббревиатур/имён (MCP, RAG, ADR)
    CONTEXT_MIN_COSINE = 0.50  # ниже — фрагмент в промпт не идёт
    CONTEXT_RELATIVE = 0.85  # фрагмент идёт в промпт, если >= 0.85 * лучший
    IDLE_AVG_DOC_LEN = 400.0

    def _df_of(self, term: str) -> int:
        """df термина со словоформами — единая логика поиска (`rag._df_of`)."""
        return self.rag._df_of(term)

    def _idf(self, term: str) -> float:
        """Важность термина для корпуса (BM25 IDF, словоформы объединены)."""
        n = self.rag.num_docs
        if not n:
            return 0.0
        df = self._df_of(term)
        return math.log((n - df + 0.5) / (df + 0.5) + 1.0)

    def _stem_stopwords(self, word: str) -> bool:
        """Стоп-слово с учётом словоформы («устроена» → «устроен»)."""
        return self.rag._is_stopword(word)

    def _gate_terms(self, question: str) -> list[str]:
        """Содержательные термины вопроса (без служебных слов).

        Английские термины в вопросе — обычно верная фиксация темы:
        технология, имя файла или функции. Транслитерация тех же букв
        кириллицей («джанго» рядом с «django») — тот же термин: незнающие
        модели пишут русский вариант, и это не «посторонний» вопрос.
        Поэтому кириллические термины, чья транслитерация уже есть в
        запросе латиницей, из гейта выпадают.
        """
        terms = sorted({t for t in self.rag.tokenize(question)
                        if not self._stem_stopwords(t)})
        ascii_terms = [t for t in terms if t.isascii() and len(t) >= 3]
        out = []
        for t in terms:
            if not t.isascii() and any(_same_term(t, a) for a in ascii_terms):
                continue
            out.append(t)
        return out

    def _is_on_topic(self, question: str, hits: list[dict]) -> bool:
        """Относится ли вопрос к корпусу проекта.

        Нет — возвращаем пустой контекст, и модель отвечает честным «в
        документации этого нет» вместо ответа по соседнему разделу.
        """
        stats = self.rag.stats()
        if not stats.get("indexed") or not stats.get("vocab"):
            return True  # индекс пуст — честный «не найдено» придёт ниже

        terms = self._gate_terms(question)
        # «корпусный» термин — заметная доля фрагментов корпуса (>= 2%,
        # но не меньше двух): на большом проекте это десятки файлов, на
        # маленьком — пара; случайной словоформы (df 1–2) достаточно,
        # чтобы термином не считаться
        min_df = min(4, max(2, self.rag.num_docs // 50))
        corpus_terms = [t for t in terms if self._df_of(t) >= min_df]
        top_bm25 = max(self.rag.document_relevance(question).values(), default=0.0)

        # --- плотность близости (по векторной ноге, если она есть)
        vecs = [h["cosine"] for h in hits
                if h.get("cosine") is not None and h["cosine"] > 0][:15]
        bm_scale = max(0.25, self.rag.num_docs / self.BM25_REF_CHUNKS)

        # --- константы близости: «плотность» = средний cosine топ-15;
        # порог слегка адаптируется к корпусу по средней длине чанка
        # средний чанк короче «ленты дня 31» — плотность выше: на этом
        # корпусе ~0.53; пороги опускаем на небольшую поправку и не больше
        avg = stats.get("avg_doc_len") or self.IDLE_AVG_DOC_LEN
        scale = max(1.0, self.IDLE_AVG_DOC_LEN / max(avg, 1.0))
        threshold = self.REL_COARSE - min(0.05, 0.01 * (scale - 1))
        mean_top = sum(vecs) / len(vecs) if vecs else 0.0

        # --- вопрос вообще без содержательных слов («кто ты?», «привет»)
        # при этом заметной близости нет: в корпусе «про проект» любой
        # вопрос О проекте («как устроен проект?» — одни общие слова)
        # всё равно находит описание, и такой вопрос отвечается
        if not terms:
            return bool(vecs) and mean_top >= threshold - 0.055

        # --- термин спрашивают, а в корпусе его нет ни разу («Москва»,
        # «борщ»): фильтр по ascii оставлен только для аббревиатур-названий
        absent = [t for t in terms if len(t) >= 4 and not self._df_of(t)
                  and not t.isascii()]
        if absent:
            return False

        # --- совпал только длинный редкий термин в 1–2 чанках: это
        # случайная словоформа («билет…ник» в прозе), а не тема
        rare = [t for t in corpus_terms
                if len(t) >= 6 and self._idf(t) >= 1.5 and self._df_of(t) <= 2]
        if rare and len(corpus_terms) == len(rare):
            return False

        if not vecs:
            # офлайн BM25: о теме говорит сильное совпадение корпусного
            # термина в конкретном файле
            return bool(corpus_terms
                        and top_bm25 >= self.DOC_BM25_STRONG * bm_scale)

        # корпусных терминов нет («как устроен проект?» — одни общие
        # слова): в корпусе «про проект» такой вопрос найдёт описание —
        # засчитываем при заметной плотности
        if not corpus_terms:
            return mean_top >= threshold - 0.04

        if mean_top >= threshold:
            return True
        top2 = vecs[:2]
        if len(top2) == 2 and all(c >= self.REL_CONSISTENT - 0.02 for c in top2):
            return True
        # низкая плотность, но термин — реальная тема корпуса и BM25 его
        # нашёл: короткие вопросы («что такое MCP?») эмбеддер размывает
        # в общий «проектный» вектор
        dense = self.BM25_MIN_DENSITY
        if any(t.isascii() and len(t) <= 4 for t in corpus_terms):
            dense = self.BM25_DENSITY_ACRONYM
        return bool(top_bm25 >= self.BM25_MIN_DOC * bm_scale
                    and mean_top >= dense)

    def _topical(self, question: str, hits: list[dict]) -> list[dict]:
        """Фрагменты, относящиеся к вопросу. Пусто — вопрос не о проекте:
        лучше честное «в документации этого нет», чем ответ по соседнему
        разделу (чтобы модель отвечала «не найдено», а не выдумывала)."""
        return hits if self._is_on_topic(question, hits) else []

    def _retrieve(self, question: str, top_k: int = 5):
        """Гибридный поиск + отбор. Если по всему вопросу ничего не
        релевантно — ищем по самому содержательному «подвопросу»
        (после «,» / «и» / «но»), чтобы не терять многопредложный запрос.
        Возвращает (hits, сколько отброшено поиском с порогом)."""
        raw = self.rag.search(question, top_k=max(top_k * 3, 12))
        hits = self._topical(question, raw)[:top_k]
        if hits:
            return hits, max(0, len(raw) - len(hits))
        for part in re.split(r"[,;]\s+|\s+(?:и|но|потом|затем)\s+"
                             r"|\s+а\s+", question):
            part = part.strip(" ?!")
            if len(part) < 8 or part == question.strip(" ?!"):
                continue
            sub = self._topical(part, self.rag.search(part, top_k=max(top_k * 3, 12)))
            if sub:
                return sub[:top_k], 0
        return [], len(raw)

    # Местоимения/указатели уточнения — не термины темы (иначе «него»
    # случайно совпадёт с «engine» в чужом скрипте).
    _REFERRAL_STEMS = frozenset(("него", "неё", "нее", "нему", "нем", "ней",
                                 "них", "они", "его", "её", "ей", "ему",
                                 "этот", "этой", "эту", "том", "там"))

    # Местоимения и указатели, которые делают вопрос follow-up-вопросом
    # («какой порт у НЕГО?»). Словарь скоупа «о чём он» — для подстановки
    # в запрос будут отброшены.
    _REFERRAL_WORDS = re.compile(
        r"(?<![a-zа-яё])(он|она|оно|они|его|её|ее|ему|ей|них|него|неё|нее|"
        r"этот|это|эта|эту|том|там|тогда|такой|этим)(?![a-zа-яё])",
        re.IGNORECASE)

    def _resolve_follow_up(self, question: str) -> str:
        """Достраивает поисковый запрос контекстом предыдущих вопросов.

        «какой порт у него по умолчанию?» без предыдущего «как устроен
        шлюз?» ищет по слову «порт» — получаются чужие порты (e2e, 8106)
        и ответ не по запросу. Признак follow-up — местоимение/указатель
        в начале вопроса; служебные слова предыдущего вопроса не
        повторяем (они не про предмет).
        """
        if not self._history or not self._REFERRAL_WORDS.search(question):
            return question
        words = [w for w in re.findall(r"[A-Za-zА-Яа-я0-9_\-]+",
                                       self._history[-1]["q"])
                 if w.lower() not in self.rag.QUERY_STOPWORDS]
        prev = " ".join(words) or self._history[-1]["q"]
        return f"{prev} {prev} {question}"

    def _supplement_follow_up(self, hits: list[dict], question: str,
                              limit: int = 3) -> tuple[list[dict], list[dict]]:
        """Фрагменты из уже найденных файлов, которые прямо содержат
        термины уточнения («порт» → `DEFAULT_GATEWAY_PORT = 8110`).

        Почему прямой скан, а не `search`: уточнение — узкий запрос
        («порт», «лимит») по уже найденной теме, и нужен чанк, где эти
        слова есть вместе. Средний cosine у кода низкий (код — «серая
        материя» для эмбеддера), а BM25 топит ответ короткими
        совпадениями в документации.

        Ответ на уточнение — чанк, где терминов БОЛЬШЕ: общий раздел
        «шлюз и лимиты» содержит слово «порт» мимоходом, а строка
        `DEFAULT_GATEWAY_PORT = 8110` — и «порт», и «шлюз», и число.
        Поэтому приоритет — покрытие, не размер фрагмента.
        """
        terms = [t for t in self.rag.tokenize(question)
                 if not self.rag._is_stopword(t) and len(t) >= 4
                 and t not in self._REFERRAL_STEMS]
        if not terms:
            return hits, []
        # файлы ищутся по ЛЕКСИЧЕСКОЙ ноге, а не по гибридному топу:
        # «DEFAULT_GATEWAY_PORT = 8110» может не попасть в 5 гибридных
        # фрагментов (код — «серая материя» для эмбеддера), но по словам
        # «порт» и «шлюз» это очевидный кандидат
        files = {h["file"] for h in hits if h.get("file")}
        files.update(h["file"] for h in self.rag.search(
            question, top_k=8, weights=(1.0, 0.0)) if h.get("file"))
        # английские термины проекта («порт» → port) — отдельным проходом
        hints = self._hint_terms(question)
        shown = {(h["file"], (h.get("text") or "")[:300]) for h in hits}
        scored = []
        for d in self.rag.chunks:
            if d["file"] not in files:
                continue
            # у кода все чанки файла делят заголовок — сравниваем текстом
            if (d["file"], d["text"][:300]) in shown:
                continue
            covered = sum(1 for t in terms if self.rag._match_tf(t, d["tf"]))
            hinted = sum(1 for t in hints if d["tf"].get(t))
            if covered or hinted:
                # размер — только при равенстве покрытия: предпочитаем
                # фрагмент, где у слова есть пояснение, а не список
                scored.append([covered, hinted, len(d["tokens"]), d])
        return hits, self._pick_supplement(scored, limit)

    @staticmethod
    def _pick_supplement(scored: list, limit: int) -> list[dict]:
        """Верхние `limit` кандидатов (coverage, hints, размер) без
        дублей текста: у кода чанки одного файла различаются только
        текстом, а «выжимки» повторяют друг друга."""
        ranked = sorted(scored, key=lambda x: (-x[0], -x[1], -x[2]))
        out, seen = [], set()
        for _cov, _hint, _size, d in ranked:
            key = d["text"][:200]
            if key in seen:
                continue
            seen.add(key)
            out.append({"score": 0.0, "relevance": None, "cosine": None,
                        "bm25": None, "file": d["file"],
                        "section": d["section"], "text": d["text"]})
            if len(out) >= limit:
                break
        return out

    # Перевод «человеческих» терминов в термины проекта: вопросы пишут
    # по-русски, а ключевые слова в коде/докстрингах — по-английски.
    _TERM_HINTS = {
        "порт": ["port"], "лимит": ["limit", "limits", "rpm", "tpm"],
        "лимиты": ["limit", "limits"], "ключ": ["key"], "ключи": ["keys", "key"],
        "ветка": ["branch"], "запуск": ["run"], "тест": ["test", "tests"],
        "тесты": ["test", "tests"], "модель": ["model"], "модели": ["model"],
        "индекс": ["index"], "память": ["memory"], "база": ["kb", "database"],
        "хранится": ["store", "storage"], "компрессия": ["compress"],
        "сценарий": ["scenario"], "тикет": ["ticket"], "файл": ["file"],
        "конфиг": ["config", "env"], "ошибка": ["error"], "шлюз": ["gateway"],
    }

    def _hint_terms(self, question: str) -> list[str]:
        """Термины уточнения, продублированные английскими словами
        проекта: «какой порт?» ищет и `port` (DEFAULT_GATEWAY_PORT)."""
        out: list[str] = []
        for t in self.rag.tokenize(question):
            key = next((h for stem, hints in self._TERM_HINTS.items()
                        if t[:4] == stem[:4] for h in hints), None)
            if key and key not in out:
                out.append(key)
        return out[:2]

    def _remember(self, question: str, hits: list[dict]):
        self._history.append({"q": question,
                              "files": [h.get("file") for h in hits if h.get("file")]})
        del self._history[:-self.HISTORY_LIMIT]

    def _refresh_index_if_stale(self):
        try:
            self.rag.ensure_fresh()
        except Exception as e:
            print(f"{C.YELLOW}⚠ Индекс RAG не пересобран: {e}{C.RESET}")

    def _print_usage(self):
        """Строка расхода токенов: промпт/ответ и остаток окна модели.

        Числа — от Ollama (`prompt_eval_count`/`eval_count`), а не оценка
        по символам: по ним видно, сколько места в окне диалога занял
        именно этот запрос и сколько осталось до следующего сжатия
        контекста моделью.
        """
        usage = getattr(self.llm, "last_usage", None)
        if not usage:
            return
        p_tok = usage.get("prompt_tokens", 0)
        a_tok = usage.get("answer_tokens", 0)
        window = usage.get("context_window") or 0
        remaining = usage.get("remaining", 0)
        pct = usage.get("percent", 0.0)
        window_str = f"{window:,}".replace(",", " ")
        remaining_str = f"{remaining:,}".replace(",", " ")
        filled = int(min(20, round(pct / 5)))
        bar = "█" * filled + "░" * (20 - filled)
        print(f"{C.DIM}🔢 Токены: промпт {p_tok} + ответ {a_tok} = "
              f"{p_tok + a_tok} | окно {window_str} | остаток {remaining_str} "
              f"({pct:.0f}% занято) [{bar}]{C.RESET}")

    def _answer_not_found(self, question: str, dropped: int):
        """Честный отказ вместо ответа «по соседнему разделу»."""
        print(f"\n{C.BOLD}{C.GREEN}=== Ответ ассистента ==={C.RESET}\n")
        print(f"В документации и коде проекта нет информации по запросу «{question}».")
        if dropped:
            print(f"{C.DIM}Найдены только фрагменты с низкой релевантностью — "
                  f"они не передавались модели ({dropped} отброшено).{C.RESET}")
        print(f"{C.DIM}Подсказка: `search <слова из вопроса>` — найти ближайшие "
              f"разделы, `docs` — список документов в RAG.{C.RESET}")
        br = self._get_branch_only()
        if br:
            print(f"{C.DIM}🌿 Ветка Git: {br}{C.RESET}")
        print("")

    def _fallback_answer(self, question: str, hits: list[dict], git_context: str):
        """Лаконичный ответ без LLM."""
        if not hits and not git_context:
            self._answer_not_found(question, 0)
            return

        print(f"\n{C.BOLD}{C.GREEN}=== Ответ ассистента ==={C.RESET}\n")
        if hits:
            top = hits[0]
            f_path = top.get("file", "—")
            sec = top.get("section", "")
            txt = (top.get("text") or "").strip()

            print(f"По документации проекта ({f_path}, {sec}):\n")
            paragraphs = [p.strip() for p in txt.split("\n\n") if p.strip()]
            for p in paragraphs[:3]:
                print(f"  {p}\n")

            sources = sorted({h.get("file") for h in hits if h.get("file")})
            print(f"{C.DIM}📄 Источники RAG: {', '.join(sources)}{C.RESET}")

        br = self._get_branch_only()
        if br:
            print(f"{C.DIM}🌿 Ветка Git: {br}{C.RESET}")
        print("")

    def _get_branch_only(self) -> str:
        """Получить текущую ветку репозитория."""
        if self.mcp.is_available():
            try:
                res = self.mcp.call_tool("git_branch")
                br = res.get("branch")
                if br:
                    return br
            except Exception:
                pass
        try:
            p = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                               cwd=self.repo_root, capture_output=True, text=True, timeout=5)
            if p.returncode == 0:
                return p.stdout.strip()
        except Exception:
            pass
        return ""

    def _get_git_context(self) -> str:
        """Сбор git-контекста через MCP с fallback на локальный git."""
        lines = []
        branch = self._get_branch_only()
        if branch:
            lines.append(f"Текущая ветка: {branch}")

        tracked, untracked = [], []
        if self.mcp.is_available():
            try:
                f_res = self.mcp.call_tool("git_files")
                tracked = f_res.get("tracked", [])
                untracked = f_res.get("untracked", [])
            except Exception:
                pass

        if not tracked and not untracked:
            try:
                p = subprocess.run(["git", "status", "--porcelain"],
                                   cwd=self.repo_root, capture_output=True, text=True, timeout=5)
                if p.returncode == 0:
                    for l in p.stdout.splitlines():
                        if not l.strip():
                            continue
                        status, path = l[:2], l[3:].strip()
                        (untracked if status.strip() == "??" else tracked).append(path)
            except Exception:
                pass

        total = len(tracked) + len(untracked)
        lines.append(f"Изменённых файлов: {total}")
        if tracked:
            lines.append(f"  Изменены: {', '.join(tracked[:5])}" + (" …" if len(tracked) > 5 else ""))
        if untracked:
            lines.append(f"  Новые: {', '.join(untracked[:5])}" + (" …" if len(untracked) > 5 else ""))

        return "\n".join(lines)

    def cmd_git(self):
        """Получение текущей ветки через MCP."""
        if self.mcp.is_available():
            try:
                res = self.mcp.call_tool("git_branch")
                if "error" in res:
                    print(f"{C.RED}MCP git_branch ошибка: {res['error']}{C.RESET}")
                else:
                    branch = res.get("branch", "—")
                    print(f"\n{C.BOLD}Ветка:{C.RESET} {C.GREEN}{branch}{C.RESET} {C.DIM}(через MCP){C.RESET}\n")
                return
            except Exception as e:
                print(f"{C.YELLOW}MCP вызов не удался ({e}), использую fallback...{C.RESET}")

        branch = self._get_branch_only()
        print(f"\n{C.BOLD}Ветка:{C.RESET} {C.GREEN}{branch or '—'}{C.RESET}\n")

    def cmd_files(self):
        """Список файлов через MCP."""
        if self.mcp.is_available():
            try:
                res = self.mcp.call_tool("git_files")
                tracked = res.get("tracked", [])
                untracked = res.get("untracked", [])
                total = res.get("total", len(tracked) + len(untracked))
                print(f"\n{C.BOLD}Изменённые файлы ({total}):{C.RESET} {C.DIM}(через MCP){C.RESET}\n")
                if tracked:
                    print(f"  {C.YELLOW}Отслеживаемые ({len(tracked)}):{C.RESET}")
                    for f in tracked:
                        print(f"    M {f}")
                if untracked:
                    print(f"  {C.RED}Неотслеживаемые ({len(untracked)}):{C.RESET}")
                    for f in untracked:
                        print(f"    ? {f}")
                if not tracked and not untracked:
                    print(f"  {C.DIM}Рабочее дерево чистое.{C.RESET}")
                print("")
                return
            except Exception as e:
                print(f"{C.YELLOW}MCP вызов не удался ({e}), fallback...{C.RESET}")

        ch_lines = self._get_git_context()
        print(f"\n{C.BOLD}Статус файлов:{C.RESET}\n{ch_lines}\n")

    def cmd_diff(self):
        """Diff через MCP."""
        if self.mcp.is_available():
            try:
                res = self.mcp.call_tool("git_diff", {"max_lines": 100})
                diff_text = res.get("diff", "")
                lines_cnt = res.get("lines", 0)
                truncated = res.get("truncated", False)
                print(f"\n{C.BOLD}Git Diff ({lines_cnt} строк){C.RESET} "
                      f"{C.DIM}(через MCP){C.RESET}"
                      f"{' [обрезан до 100]' if truncated else ''}:\n")
                if diff_text:
                    for line in diff_text.splitlines()[:60]:
                        if line.startswith("+") and not line.startswith("+++"):
                            print(f"  {C.GREEN}{line}{C.RESET}")
                        elif line.startswith("-") and not line.startswith("---"):
                            print(f"  {C.RED}{line}{C.RESET}")
                        elif line.startswith("@@"):
                            print(f"  {C.CYAN}{line}{C.RESET}")
                        else:
                            print(f"  {C.DIM}{line}{C.RESET}")
                else:
                    print(f"  {C.DIM}Нет изменений.{C.RESET}")
                print("")
                return
            except Exception as e:
                print(f"{C.YELLOW}MCP diff не удался ({e}), fallback...{C.RESET}")

        try:
            p = subprocess.run(["git", "diff"], cwd=self.repo_root, capture_output=True, text=True, timeout=5)
            diff_text = p.stdout
            print(f"\n{C.BOLD}Git Diff:{C.RESET}\n")
            if diff_text:
                for line in diff_text.splitlines()[:60]:
                    print(f"  {line}")
            else:
                print(f"  {C.DIM}Нет изменений.{C.RESET}")
        except Exception as e:
            print(f"{C.RED}Ошибка получения diff: {e}{C.RESET}")
        print("")

    def cmd_search(self, query: str):
        """Поиск по RAG-индексу."""
        if not query.strip():
            print(f"{C.YELLOW}Укажите поисковый запрос: search <текст>{C.RESET}")
            return
        self._refresh_index_if_stale()
        hits = self.rag.search(query, top_k=5)
        print(f"\n{C.BOLD}Результаты поиска по «{query}» ({len(hits)}):{C.RESET}\n")
        for i, h in enumerate(hits, 1):
            cos = h.get("cosine")
            rel = f", cos: {cos:.2f}" if cos is not None else ""
            print(f"  {C.CYAN}[{i}]{C.RESET} {C.BOLD}{h.get('file')}{C.RESET} "
                  f"({h.get('section', '')}) [score: {h.get('score', 0):.3f}{rel}]")
            txt = (h.get("text") or "").replace("\n", " ")[:200]
            print(f"      {C.DIM}{txt}...{C.RESET}\n")

    def cmd_status(self):
        """Статус компонентов."""
        print(f"\n{C.BOLD}=== Статус ассистента ==={C.RESET}\n")

        # RAG
        st = self.rag.stats()
        if st["indexed"]:
            mode = ("гибрид BM25 + векторы" if st["mode"] == "hybrid"
                    else "только BM25 (без векторной ноги)")
            print(f"  {C.CYAN}Корпус RAG:{C.RESET} {C.GREEN}✓ активен{C.RESET} "
                  f"({st['files']} файлов, {st['chunks']} фрагментов; {mode})")
            if st["mode"] == "hybrid":
                print(f"  {C.CYAN}Эмбеддер:{C.RESET} {C.GREEN}✓ {st['embed_model']}{C.RESET} "
                      f"({st['vectors']} векторов)")
            else:
                print(f"  {C.CYAN}Эмбеддер:{C.RESET} {C.YELLOW}"
                      f"{st['embed_note'] or 'недоступен'}{C.RESET}")
            if st["stale"]:
                print(f"  {C.CYAN}Свежесть:{C.RESET} {C.YELLOW}корпус менялся — "
                      f"пересоберите index{C.RESET}")
        else:
            print(f"  {C.CYAN}Корпус RAG:{C.RESET} {C.YELLOW}не построен (запустите index){C.RESET}")

        # MCP
        if self.mcp.is_available():
            try:
                tools = self.mcp.list_tools()
                names = [t.get("name") for t in tools]
                print(f"  {C.CYAN}MCP-сервер:{C.RESET} {C.GREEN}✓ доступен{C.RESET} "
                      f"({len(tools)} инструментов: {', '.join(names)})")
            except Exception as e:
                print(f"  {C.CYAN}MCP-сервер:{C.RESET} {C.RED}ошибка: {e}{C.RESET}")
        else:
            print(f"  {C.CYAN}MCP-сервер:{C.RESET} {C.RED}файл {MCP_SERVER} не найден{C.RESET}")

        # LLM
        model = self.llm.get_model()
        if model:
            print(f"  {C.CYAN}LLM-модель:{C.RESET} {C.GREEN}✓ {model}{C.RESET}")
        else:
            print(f"  {C.CYAN}LLM-модель:{C.RESET} {C.YELLOW}офлайн (Ollama недоступна){C.RESET}")

        # Git
        br = self._get_branch_only()
        if br:
            print(f"  {C.CYAN}Git:{C.RESET} {C.GREEN}✓{C.RESET} ветка {C.BOLD}{br}{C.RESET}")
        else:
            print(f"  {C.CYAN}Git:{C.RESET} {C.RED}недоступен{C.RESET}")
        print("")

    def cmd_tools(self):
        """Список MCP инструментов."""
        try:
            tools = self.mcp.list_tools()
            print(f"\n{C.BOLD}Инструменты MCP сервера ({len(tools)}):{C.RESET}\n")
            for t in tools:
                print(f"  {C.CYAN}{t.get('name')}{C.RESET}: {t.get('description')}")
            print("")
        except Exception as e:
            print(f"{C.RED}Ошибка получения инструментов MCP: {e}{C.RESET}")

    def cmd_docs(self):
        """Список файлов документации в корпусе."""
        files = self.rag.discover_files()
        print(f"\n{C.BOLD}Документация проекта в RAG ({len(files)}):{C.RESET}\n")
        for f in files:
            print(f"  📄 {f}")
        print("")

    def cmd_index(self):
        """Пересборка индекса."""
        print(f"{C.YELLOW}Пересобираю RAG-индекс проекта...{C.RESET}")
        print(f"{C.DIM}  Векторы: {self.rag.embed_model} через Ollama "
              f"(первая сборка ~минута)...{C.RESET}")
        st = self.rag.build_index()
        print(f"{C.GREEN}✓ Индекс успешно собран: {st.get('docs')} документов, "
              f"{st.get('chunks')} фрагментов, {st.get('vectors')} векторов.{C.RESET}")
        if self.rag.embed_note:
            print(f"{C.YELLOW}  Векторная нога недоступна: {self.rag.embed_note}. "
                  f"Поиск — только BM25.{C.RESET}")
        print("")


def main():
    cli = DevAssistantCLI()
    try:
        if len(sys.argv) > 1:
            raw_cmd = " ".join(sys.argv[1:])
            cli.execute_command(raw_cmd)
        else:
            cli.run_interactive()
    finally:
        cli.mcp.close()


if __name__ == "__main__":
    main()
