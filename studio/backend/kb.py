"""День 21 — база знаний: chunking, эмбеддинги, индекс.

RAG-ядро Студии: корпус — только загрузки пользователя
(data/kb/uploads, «+ Добавить файл»; доки репозитория и код бэкенда в
авто-корпус не входят — исправление после релиза дня 21),
две стратегии разбиения на чанки (фиксированная / структурная),
эмбеддинги (детерминированный hash-эмбеддер офлайн и API-эмбеддер
GPustack), индекс `index.db` (SQLite: таблицы meta+chunks, вектор —
BLOB float32, одна транзакция на сборку/удаление, авто-миграция из
legacy `index.json`) и гибридный поиск: векторная нога (косинус) + лексическая (BM25 по
термам чанка, substring-матч инфлексий) с фузией Reciprocal Rank.

Детерминизм: hash-эмбеддер строится ТОЛЬКО на hashlib.md5 (встроенный
hash() salted per-process — не годится). Тесты офлайн, без сети.
"""
import hashlib
import json
import math
import os
import re
import sqlite3
import struct
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone

DEFAULT_CHUNK_CHARS = 1200
DEFAULT_OVERLAP = 200
DEFAULT_MAX_SECTION = 2400   # structured: секция длиннее → под-чанки по FixedChunker
HASH_DIM = 256
EMBED_MODEL = "qwen3-vl-embedding-8b"
RERANK_MODEL = "qwen3-reranker-4b"
UPLOAD_EXTS = {".txt", ".md", ".py", ".js", ".ts", ".tsx", ".jsx", ".json",
               ".csv", ".html", ".css", ".yaml", ".yml"}

_API_BATCH = 16              # батч API-эмбеддера (текстов на запрос)
_RERANK_BATCH = 16           # батч API-реранкера (документов на запрос)
_RERANK_MAX_CHARS = 1024     # срез документа для cross-encoder (лимит токенов)


class KBError(Exception):
    """Ошибка базы знаний (индекс не построен, нет ключа API, сбой сети)."""


@dataclass
class CorpusDoc:
    """Документ корпуса: текст файла, путь (для загрузок — фиксированный
    «uploads/<имя>») и источник; в текущем корпусе (uploads-only) —
    всегда "upload" ("docs"/"code" — исторические значения старых
    индексов)."""
    path: str
    text: str
    source: str


@dataclass
class Chunk:
    """Чанк: текст + происхождение (source, файл, секция, индекс чанка)."""
    text: str
    source: str
    file: str
    section: str     # heading text / "whole file" / "(без заголовка)"
    chunk_index: int


def atomic_write_json(path: str, obj) -> None:
    """Атомарно записать JSON (tmp + os.replace, паттерн memory.py)."""
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def read_json(path: str, default):
    """Прочитать JSON; при OSError/ValueError (битый файл) вернуть default."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def cosine(a: list[float], b: list[float]) -> float:
    """Косинусная близость (чистый python). Нулевой вектор → 0.0."""
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


# ---------- лексическая нога гибридного поиска (BM25 + RRF) ----------

_MIN_TOKEN = 3        # токены короче — шум
_SUBSTR_MIN = 5       # substring-матч инфлексий: короткая сторона >= 5
                      # («телефон» ↔ «телефона»; короткие слова не трогаем)
_PREFIX_MIN = 5       # typo-допуск: оба терма >= 5 и первые 5 симв. равны
                      # («телфон» ↔ «телефон» — 1-буквенная опечатка)
_BM25_K1 = 1.5
_BM25_B = 0.75
_RRF_K = 60


def _tokenize(text: str) -> list[str]:
    """Токены: нижний регистр, [a-zа-я0-9]+, длина >= 3; ё → е."""
    return [t for t in re.findall(r"[a-zа-я0-9]+",
                                  text.lower().replace("ё", "е"))
            if len(t) >= _MIN_TOKEN]


def _term_freq(text: str) -> dict:
    """Частоты токенов текста (детерминированный порядок — по тексту)."""
    tf: dict[str, int] = {}
    for t in _tokenize(text):
        tf[t] = tf.get(t, 0) + 1
    return tf


def _one_del(a: str, b: str) -> bool:
    """b получается из a удалением ровно одного символа
    (детерминированно, O(len a))."""
    if len(b) != len(a) - 1:
        return False
    i = 0
    while i < len(b) and a[i] == b[i]:
        i += 1
    return a[i + 1:] == b[i:]


def _match_tf(qterm: str, terms: dict) -> int:
    """Частота query-терма в чанке: точное совпадение; иначе
    substring-матч с любым термом чанка (короткая сторона >= 5 симв. —
    грубый «стемминг» без морфологического анализатора) или
    typo-матч (оба терма >= 5 симв.): равные первые 5 симв. либо
    один терм получается из другого удалением одного символа
    («телфон» ↔ «телефон» — 1-буквенная опечатка). Несколько матчей —
    max tf (порядок terms — отсортирован, детерминированно)."""
    tf = terms.get(qterm, 0)
    if tf:
        return tf
    for u, f in terms.items():
        a, b = (qterm, u) if len(qterm) <= len(u) else (u, qterm)
        if len(a) >= _SUBSTR_MIN and a in b:
            tf = max(tf, f)
        elif (len(qterm) >= _PREFIX_MIN and len(u) >= _PREFIX_MIN
                and (qterm[:_PREFIX_MIN] == u[:_PREFIX_MIN]
                     or _one_del(qterm, u) or _one_del(u, qterm))):
            tf = max(tf, f)
    return tf


_STOPWORDS = {
    # ru: служебные слова вопросов/текста — не несут различительной
    # силы, их tf-сумма в частых чанках «съедает» редкие токены
    "а", "б", "бы", "был", "была", "было", "были", "в", "во", "все",
    "всё", "где", "его", "её", "ей", "ему", "из", "и", "или", "как",
    "какая", "какое", "какой", "каким", "какими", "какую", "каких",
    "кем", "кему", "когда", "которая", "которое", "который", "которые",
    "кто", "ли", "между", "мой", "моя", "моё", "мои", "на", "над",
    "не", "нет", "но", "о", "об", "ого", "ой", "ом", "она", "они",
    "он", "очень", "по", "под", "при", "с", "со", "такая", "такое",
    "так", "таким", "такой", "те", "тем", "твой", "твоя", "твоё",
    "ты", "у", "уже", "что", "чтоб", "чтобы", "это", "эти", "этот",
    "эта", "этом", "этого", "для", "к", "за", "от", "про", "до",
    # en
    "a", "an", "and", "at", "be", "by", "for", "from", "he", "her",
    "his", "i", "in", "is", "it", "its", "my", "of", "on", "or",
    "she", "that", "the", "this", "to", "was", "were", "what",
    "which", "who", "whom", "you",
}


def _bm25_scores(query: str, chunks: list[dict]) -> dict:
    """Okapi BM25 (k1=1.5, b=0.75) по чанкам с полем "terms"
    (dict терм → tf, строится при сборке индекса). Чанки без "terms"
    (старые индексы) не участвуют. Stopwords из запроса отбрасываются
    (иначе частые «была/какая/…» в длинных чанках суммой tf обгоняют
    редкие токены, для которых BM25 и предназначен).
    {index_чанка: score > 0}."""
    docs = [c.get("terms") for c in chunks]
    if not any(d for d in docs):
        return {}
    qterms = sorted({t for t in _tokenize(query)
                     if t not in _STOPWORDS})
    if not qterms:
        return {}
    n = len(chunks)
    per_chunk = []  # (index, {qterm: tf})
    for i, d in enumerate(docs):
        if d:
            per_chunk.append((i, {t: _match_tf(t, d) for t in qterms}))
    if not per_chunk:
        return {}
    df = {t: 0 for t in qterms}
    for _i, m in per_chunk:
        for t, f in m.items():
            if f:
                df[t] += 1
    avg_len = (sum(sum(docs[i].values()) for i, _m in per_chunk)
               / len(per_chunk)) or 1.0
    out: dict[int, float] = {}
    for i, m in per_chunk:
        dlen = sum(docs[i].values()) or 1
        s = 0.0
        for t in qterms:
            tf = m[t]
            if not tf:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            s += idf * tf * (_BM25_K1 + 1) / (
                tf + _BM25_K1 * (1 - _BM25_B + _BM25_B * dlen / avg_len))
        if s > 0:
            out[i] = s
    return out


def _rrf(rank_lists: list, k: int = _RRF_K) -> dict:
    """Reciprocal Rank Fusion: score(d) = Σ 1/(k + rank), rank от 1.
    Нормализация шкал (косинус vs BM25) не нужна — ранги безразмерны."""
    out: dict[int, float] = {}
    for ranks in rank_lists:
        for rank, i in enumerate(ranks, 1):
            out[i] = out.get(i, 0.0) + 1.0 / (k + rank)
    return out


# ---------- индекс-хранилище: SQLite (data/kb/index.db) ----------


def _pack_vector(vec: list[float]) -> bytes:
    """Вектор → BLOB: float32 little-endian (struct, stdlib)."""
    return struct.pack("<%df" % len(vec), *vec)


def _unpack_vector(blob: bytes, dim: int) -> list[float]:
    """BLOB → вектор (float32 → float). Несоответствие размера
    (коррупция) — ValueError."""
    if len(blob) != 4 * dim:
        raise ValueError(
            f"Вектор-BLOB: {len(blob)} байт, ожидаю {4 * dim}")
    return list(struct.unpack("<%df" % dim, blob))


class _IndexStore:
    """Хранилище индекса — SQLite (`data/kb/index.db`, stdlib sqlite3).

    Схема: `meta(key TEXT PRIMARY KEY, value TEXT)` — version/strategy/
    embedder/model/dim/built_at + stats и comparison (JSON-строки);
    `chunks(chunk_id TEXT PRIMARY KEY, source, file, section, chars,
    text, vector BLOB, terms TEXT)` — terms — JSON-строка (терм → tf),
    vector — float32 little-endian (округление до float32 допустимо).
    Порядок чанков — rowid (порядок вставки, как в старом index.json).
    Все записи — в одной транзакции: полная сборка/миграция — замена,
    инкрементальная — INSERT новых чанков + UPDATE meta (старые строки
    не переписываются), удаление — DELETE по файлу + UPDATE meta."""

    def __init__(self, kb_dir: str):
        self.path = os.path.join(kb_dir, "index.db")

    def exists(self) -> bool:
        return os.path.isfile(self.path)

    @staticmethod
    def _init_schema(con: "sqlite3.Connection") -> None:
        con.execute("CREATE TABLE IF NOT EXISTS meta("
                    "key TEXT PRIMARY KEY, value TEXT)")
        con.execute("CREATE TABLE IF NOT EXISTS chunks("
                    "chunk_id TEXT PRIMARY KEY, source TEXT, file TEXT, "
                    "section TEXT, chars INTEGER, text TEXT, "
                    "vector BLOB, terms TEXT)")

    @staticmethod
    def _meta_row(key: str, value) -> tuple:
        """Значение meta → строка (stats/comparison — JSON)."""
        if key in ("stats", "comparison"):
            return (key, json.dumps(value, ensure_ascii=False))
        return (key, str(value) if value is not None else "null")

    def load(self) -> dict | None:
        """Индекс как dict в форме старого JSON-документа
        ({version, strategy, embedder, model, dim, built_at, stats,
        comparison, chunks[]}), или None — db отсутствует/битый
        (те же семантики, что read_json default)."""
        if not self.exists():
            return None
        try:
            con = sqlite3.connect(self.path)
            try:
                meta = dict(con.execute("SELECT key, value FROM meta"))
                if "version" not in meta:
                    return None
                dim = int(meta["dim"]) if meta.get("dim") else None

                def _j(key: str):
                    v = meta.get(key)
                    if v is None or v == "null":
                        return None
                    return json.loads(v)

                doc = {"version": int(meta["version"]),
                       "strategy": meta["strategy"],
                       "embedder": meta["embedder"],
                       # model/strategy/embedder/built_at — обычные строки
                       # (в meta не JSON-кодируются); stats/comparison — JSON
                       "model": (None if meta.get("model") in (None, "null")
                                 else meta["model"]),
                       "dim": dim,
                       "built_at": meta.get("built_at"),
                       "stats": _j("stats"),
                       "comparison": _j("comparison"),
                       "chunks": []}
                for (cid, source, file, section, chars, text,
                     vector, terms) in con.execute(
                         "SELECT chunk_id, source, file, section, chars,"
                         " text, vector, terms FROM chunks"
                         " ORDER BY rowid"):
                    doc["chunks"].append({
                        "chunk_id": cid, "source": source, "file": file,
                        "section": section, "chars": chars, "text": text,
                        "vector": _unpack_vector(vector, dim or 0),
                        "terms": json.loads(terms) if terms else {}})
                return doc
            finally:
                con.close()
        except (sqlite3.Error, ValueError, TypeError, KeyError,
                struct.error):
            return None

    @staticmethod
    def _chunk_rows(chunks: list[dict]) -> list[tuple]:
        return [(c["chunk_id"], c["source"], c["file"], c["section"],
                 int(c["chars"]), c["text"], _pack_vector(c["vector"]),
                 json.dumps(c.get("terms") or {}, ensure_ascii=False))
                for c in chunks]

    def replace(self, doc: dict) -> None:
        """Полная замена (full-сборка/миграция): одна транзакция —
        очистить meta+chunks и записать новый индекс целиком."""
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        con = sqlite3.connect(self.path)
        try:
            with con:
                self._init_schema(con)
                con.execute("DELETE FROM meta")
                con.execute("DELETE FROM chunks")
                con.executemany(
                    "INSERT INTO meta(key, value) VALUES(?, ?)",
                    [self._meta_row(k, doc.get(k))
                     for k in ("version", "strategy", "embedder", "model",
                               "dim", "built_at", "stats", "comparison")])
                con.executemany(
                    "INSERT INTO chunks(chunk_id, source, file, section,"
                    " chars, text, vector, terms) VALUES(?, ?, ?, ?,"
                    " ?, ?, ?, ?)",
                    self._chunk_rows(doc.get("chunks", [])))
        finally:
            con.close()

    def insert_chunks(self, chunks: list[dict]) -> None:
        """Инкрементальная сборка: только INSERT новых чанков (одна
        транзакция); старые строки не трогаются."""
        if not chunks:
            return
        con = sqlite3.connect(self.path)
        try:
            with con:
                self._init_schema(con)
                con.executemany(
                    "INSERT OR REPLACE INTO chunks(chunk_id, source, file,"
                    " section, chars, text, vector, terms)"
                    " VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                    self._chunk_rows(chunks))
        finally:
            con.close()

    def update_meta(self, updates: dict) -> None:
        """UPDATE полей meta (stats/built_at после инкрементальной
        сборки или удаления) — одна транзакция."""
        con = sqlite3.connect(self.path)
        try:
            with con:
                self._init_schema(con)
                con.executemany(
                    "INSERT INTO meta(key, value) VALUES(?, ?)"
                    " ON CONFLICT(key) DO UPDATE"
                    " SET value=excluded.value",
                    [self._meta_row(k, v) for k, v in updates.items()])
        finally:
            con.close()

    def upgrade_terms(self) -> None:
        """Дополнить "terms" ТОЛЬКО у чанков, где их нет (апгрейд
        старых индексов): офлайн, дёшево, векторы не трогаются."""
        con = sqlite3.connect(self.path)
        try:
            with con:
                self._init_schema(con)
                rows = con.execute(
                    "SELECT rowid, text FROM chunks"
                    " WHERE terms IS NULL OR terms = '{}'").fetchall()
                if not rows:
                    return
                con.executemany(
                    "UPDATE chunks SET terms=? WHERE rowid=?",
                    [(json.dumps(dict(sorted(_term_freq(t).items())),
                                   ensure_ascii=False), r)
                     for r, t in rows])
        finally:
            con.close()

    def delete_file(self, file: str) -> int:
        """Удалить ВСЕ чанки файла; вернуть число удалённых."""
        con = sqlite3.connect(self.path)
        try:
            with con:
                cur = con.execute(
                    "DELETE FROM chunks WHERE file=?", (file,))
                return cur.rowcount
        finally:
            con.close()

    def chunk_summary(self) -> dict:
        """Агрегаты таблицы chunks (для stats после инкрементальной
        сборки/удаления): files/chunks/total_chars."""
        con = sqlite3.connect(self.path)
        try:
            files, chunks, total = con.execute(
                "SELECT COUNT(DISTINCT file), COUNT(*),"
                " COALESCE(SUM(chars), 0) FROM chunks").fetchone()
        finally:
            con.close()
        return {"files": files, "chunks": chunks, "total_chars": total or 0}

    def remove(self) -> None:
        """Удалить index.db (+ временные файлы sqlite, если есть)."""
        for p in (self.path, self.path + "-wal", self.path + "-shm",
                  self.path + "-journal"):
            if os.path.isfile(p):
                os.remove(p)


class FixedChunker:
    """Жёсткое разбиение по символам: чанки по chunk_chars с перекрытием
    overlap символов (хвост предыдущего чанка = голова следующего)."""

    def __init__(self, chunk_chars: int = DEFAULT_CHUNK_CHARS,
                 overlap: int = DEFAULT_OVERLAP):
        if chunk_chars <= 0:
            raise ValueError("chunk_chars должен быть > 0")
        if overlap < 0 or overlap >= chunk_chars:
            raise ValueError("overlap должен быть в [0, chunk_chars)")
        self.chunk_chars = chunk_chars
        self.overlap = overlap

    def chunk(self, doc: CorpusDoc) -> list[Chunk]:
        """Разбить текст до документа; пустой текст → []."""
        text = doc.text or ""
        if not text:
            return []
        out = []
        step = self.chunk_chars - self.overlap
        i = 0
        n = len(text)
        while i < n:
            piece = text[i:i + self.chunk_chars]
            out.append(Chunk(piece, doc.source, doc.path, "whole file",
                             len(out)))
            if i + self.chunk_chars >= n:
                break
            i += step
        return out


class StructuredChunker:
    """Структурное разбиение: .md — по заголовкам (#…####), секция =
    текст заголовка; секция длиннее max_section → под-чанки по
    FixedChunker(1200/200) с сохранением секции; текст до первого
    заголовка → "(без заголовка)"; markdown без единого заголовка →
    весь файл одним чанком ("whole file"). Не-md: весь файл = 1 чанк
    ("whole file"); очень длинный (> max_section) → под-чанки."""

    _HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")

    def __init__(self, max_section: int = DEFAULT_MAX_SECTION):
        self.max_section = max_section
        self._sub = FixedChunker(DEFAULT_CHUNK_CHARS, DEFAULT_OVERLAP)

    def chunk(self, doc: CorpusDoc) -> list[Chunk]:
        text = doc.text or ""
        if not text:
            return []
        if doc.path.lower().endswith(".md"):
            return self._chunk_md(doc, text)
        if len(text) > self.max_section:
            sub = self._sub.chunk(doc)
            for c in sub:
                c.section = "whole file"
            return sub
        return [Chunk(text, doc.source, doc.path, "whole file", 0)]

    # ---------- markdown ----------

    def _sections(self, text: str) -> list[tuple[str | None, str]]:
        """Список (заголовок or None, текст секции). Секция включает
        строку заголовка; текст до первого заголовка — (None, ...)."""
        sections: list[tuple[str | None, str]] = []
        head: str | None = None
        buf: list[str] = []
        for line in text.splitlines(keepends=True):
            m = self._HEADING.match(line)
            if m:
                if head is not None or buf:
                    sections.append((head, "".join(buf)))
                head = m.group(2).strip()
                buf = [line]
            else:
                buf.append(line)
        if head is not None or buf:
            sections.append((head, "".join(buf)))
        return sections

    def _chunk_md(self, doc: CorpusDoc, text: str) -> list[Chunk]:
        sections = self._sections(text)
        # markdown без единого заголовка → весь файл одним чанком
        if all(h is None for h, _ in sections):
            return [Chunk(text, doc.source, doc.path, "whole file", 0)]
        out: list[Chunk] = []
        for head, body in sections:
            body = body.strip("\n")
            if not body:
                continue
            section = head if head is not None else "(без заголовка)"
            if len(body) > self.max_section:
                sub = self._sub.chunk(CorpusDoc(doc.path, body, doc.source))
                for c in sub:
                    c.section = section
            else:
                sub = [Chunk(body, doc.source, doc.path, section, 0)]
            for c in sub:
                c.chunk_index = len(out)
                out.append(c)
        return out


class HashEmbedder:
    """Детерминированный офлайн-эмбеддер: char 3-gram → md5(gram) →
    bucket int = int(md5hex[:8], 16) % dim, weight += 1, затем
    L2-нормализация. ТОЛЬКО hashlib (встроенный hash() salted)."""

    name = "hash"
    model = None
    dim = HASH_DIM

    def _one(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for i in range(len(text) - 2):
            gram = text[i:i + 3]
            b = int(hashlib.md5(gram.encode("utf-8")).hexdigest()[:8], 16) \
                % self.dim
            v[b] += 1.0
        n = math.sqrt(sum(x * x for x in v))
        if n > 0:
            v = [x / n for x in v]
        return v

    def embed(self, texts: list[str],
              on_progress=None) -> list[list[float]]:
        # hash-эмбеддер мгновенный: прогресс — один финальный вызов
        vecs = [self._one(t) for t in texts]
        if on_progress is not None:
            on_progress(len(texts), len(texts))
        return vecs


class APIEmbedder:
    """Эмбеддер GPustack (OpenAI-совместимый /embeddings). POST
    {base_url}/embeddings {"model", "input": batch} — батчи по 16.
    dim узнаётся с первого ответа. client DI для MockTransport-тестов."""

    name = "api"
    model = EMBED_MODEL
    dim = None

    def __init__(self, base_url: str, api_key: str,
                 client: "httpx.Client | None" = None):
        import httpx  # локальный импорт: модуль грузится и без httpx
        self._httpx = httpx
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key
        self.dim = None
        if client is not None:
            self._client = client
            self._owns = False
        else:
            self._client = httpx.Client(timeout=120)
            self._owns = True

    def embed(self, texts: list[str],
              on_progress=None) -> list[list[float]]:
        """on_progress(done, total) — после каждого батча (прогресс
        индексации в UI)."""
        out: list[list[float]] = []
        for i in range(0, len(texts), _API_BATCH):
            batch = texts[i:i + _API_BATCH]
            try:
                r = self._client.post(
                    self.base_url + "/embeddings",
                    headers={"Authorization": "Bearer " + self.api_key,
                             "Content-Type": "application/json"},
                    json={"model": self.model, "input": batch})
                r.raise_for_status()
                payload = r.json()
            except Exception as e:  # HTTP-ошибка / сбой сети / битый ответ
                raise KBError(f"Эмбеддинг-API недоступно: {e}")
            rows = payload.get("data")
            if not isinstance(rows, list):
                raise KBError("Эмбеддинг-API: в ответе нет data[]")
            rows = sorted(rows, key=lambda d: d.get("index", 0))
            vecs = []
            for d in rows:
                emb = d.get("embedding")
                if not isinstance(emb, list):
                    raise KBError("Эмбеддинг-API: нет поля embedding")
                vecs.append([float(x) for x in emb])
            if self.dim is None and vecs:
                self.dim = len(vecs[0])
            out.extend(vecs)
            if on_progress is not None:
                on_progress(len(out), len(texts))
        return out


class APIReranker:
    """Реранкер GPustack (Jina/Cohere-совместимый /rerank, cross-encoder).
    POST {base_url}/rerank {"model", "query", "documents": batch} — батчи
    по 16, документ обрезается до _RERANK_MAX_CHARS. Ответ:
    results[] {index, document, relevance_score} — пересобирается в массив
    по исходному порядку текстов (index — позиция в батче). Сбой/некорректный
    ответ — KBError. client DI для MockTransport-тестов."""

    name = "api"
    model = RERANK_MODEL

    def __init__(self, base_url: str, api_key: str,
                 client: "httpx.Client | None" = None):
        import httpx  # локальный импорт: модуль грузится и без httpx
        self._httpx = httpx
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key
        if client is not None:
            self._client = client
            self._owns = False
        else:
            self._client = httpx.Client(timeout=120)
            self._owns = True

    def rerank(self, query: str, texts: list[str]) -> list[float]:
        """relevance_score (0..1) каждого текста, В ИСХОДНОМ ПОРЯДКЕ."""
        out: list[float] = []
        for i in range(0, len(texts), _RERANK_BATCH):
            batch = [t[:_RERANK_MAX_CHARS] for t in texts[i:i + _RERANK_BATCH]]
            try:
                r = self._client.post(
                    self.base_url + "/rerank",
                    headers={"Authorization": "Bearer " + self.api_key,
                             "Content-Type": "application/json"},
                    json={"model": self.model, "query": query,
                          "documents": batch})
                r.raise_for_status()
                payload = r.json()
            except Exception as e:  # HTTP-ошибка / сбой сети / битый ответ
                raise KBError(f"Rerank-API недоступно: {e}")
            rows = payload.get("results")
            if not isinstance(rows, list):
                raise KBError("Rerank-API: в ответе нет results[]")
            scores: dict[int, float] = {}
            for d in rows:
                if not isinstance(d, dict):
                    raise KBError("Rerank-API: битой элемент results[]")
                idx = d.get("index")
                sc = d.get("relevance_score")
                if not isinstance(idx, int) or isinstance(idx, bool):
                    raise KBError("Rerank-API: нет поля index")
                if not isinstance(sc, (int, float)) or isinstance(sc, bool):
                    raise KBError("Rerank-API: нет поля relevance_score")
                scores[idx] = float(sc)
            if len(scores) != len(batch):
                raise KBError(
                    f"Rerank-API: {len(scores)} результатов на "
                    f"{len(batch)} документов")
            out.extend(scores[i] for i in range(len(batch)))
        return out


# GOLD-запросы (query, expected_file) — gold-файлы — документы
# репозитория. Метрики hit@3 / precision@3 / mrr считаются только по
# запросам, чей expected_file есть в корпусе (см. _metrics); корпус
# uploads-only, поэтому на обычном корпусе пригодных запросов обычно 0
# → нулевые метрики (механизм сохраняется).
GOLD_QUERIES: list[tuple[str, str]] = [
    ("Какой лимит итераций tool-loop у агента (TOOL_LOOP_CAP)?",
     "studio/backend/agent.py"),
    ("Атомарная запись JSON: tmp-файл и os.replace, threading.Lock",
     "studio/backend/memory.py"),
    ("Реестр MCP-серверов: дефолты, миграция старых мультитул-серверов",
     "studio/backend/mcp.py"),
    ("Инварианты: forbidden-паттерны, pre-guard и post-guard, приоритет",
     "openspec/changes/day14-invariants/specs/dialogue-invariants/spec.md"),
    ("PDF-движок: TTF-шрифт, ToUnicode CMap, детерминированные байты",
     "openspec/changes/day19-mcp-pipeline/specs/mcp-pipeline/spec.md"),
    ("Профиль пользователя: интервью, стоп-слова/табу, инъекция в промпт",
     "openspec/changes/day12-user-profile/specs/user-profile/spec.md"),
    ("Дайджест: GitHub Actions cron раз в 6 часов, сбор и коммит",
     "docs/superpowers/plans/2026-09-23-day18-mcp-digest.md"),
    ("План-ревью: человеческий гейт одобрения, approve и reject",
     "openspec/changes/day13-task-state-machine/specs/task-state-machine/spec.md"),
]


class KnowledgeBase:
    """База знаний: корпус → чанки → эмбеддинги → index.db (SQLite) →
    поиск.

    kb_dir = <repo>/data/kb (создаётся при отсутствии, включая uploads/);
    repo_root — корень репозитория (по умолчанию — два уровня вверх от
    studio/backend/kb.py); в корпус не используется (корпус — только
    загрузки kb_dir/uploads, см. corpus_files)."""

    DEFAULT_SETTINGS = {"agent_loop": True, "rag": True,
                        "reranker": "off", "rag_recall": 50,
                        "rag_top_k": 3, "min_score": 0.0,
                        "strategy": "structural", "embedder": "hash"}

    def __init__(self, kb_dir: str, repo_root: str | None = None):
        if repo_root is None:
            base = os.path.dirname(os.path.abspath(__file__))
            repo_root = os.path.dirname(os.path.dirname(base))
        self.kb_dir = kb_dir
        self.repo_root = repo_root
        os.makedirs(os.path.join(kb_dir, "uploads"), exist_ok=True)
        self._store = _IndexStore(kb_dir)
        self._p_settings = os.path.join(kb_dir, "settings.json")
        # Прогресс текущей/последней сборки (для GET /api/kb/build-status):
        # phase: corpus|chunking|embedding|metrics|save|idle|done
        self._progress = {"running": False, "phase": "idle",
                          "done": 0, "total": 0}

    def _set_progress(self, phase: str, done: int, total: int) -> None:
        self._progress = {"running": True, "phase": phase,
                          "done": int(done), "total": int(total)}

    def progress(self) -> dict:
        """Текущий прогресс сборки (копия); вне сборки — running: False."""
        return dict(self._progress)

    # ---------- корпус ----------

    def _read_file(self, path: str) -> str | None:
        """Чтение с авто-определением кодировки: utf-8 (строгий) →
        cp1251 (строгий; Windows-ANSI-файлы пользователя) →
        utf-8/errors="replace" (мусор — U+FFFD, но сборка не роняется).
        История: cp1251-файл молча превращался в U+FFFD-кашу и стал
        невидим для кириллических запросов. Байты читаются один раз;
        переводы строк нормализуются в \\n (как раньше в текстовом
        режиме) — поведение для валидного utf-8 не изменилось."""
        try:
            with open(path, "rb") as f:
                raw = f.read()
        except OSError:
            return None  # недоступный файл — пропустить, сборку не ронять

        def decode(enc: str) -> str:
            return (raw.decode(enc).replace("\r\n", "\n")
                    .replace("\r", "\n"))

        for enc in ("utf-8", "cp1251"):
            try:
                return decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", errors="replace")

    def corpus_files(self) -> list[CorpusDoc]:
        """Корпус — ТОЛЬКО загрузки пользователя: kb_dir/uploads/*
        (source "upload", только UPLOAD_EXTS). Файлы попадают в корпус
        исключительно через UI «+ Добавить файл» (POST /api/kb/upload);
        документы репозитория (README/RELEASE/docs/superpowers/openspec)
        и код бэкенда (studio/backend/*.py) в авто-корпус больше НЕ
        входят (исправление после релиза дня 21): что не добавлено
        кнопкой — не индексируется. repo_root в корпусе не используется
        (параметр оставлен в конструкторе для обратной совместимости)."""
        docs: list[CorpusDoc] = []

        def add(rel: str, text: str | None, source: str) -> None:
            if text is None:
                return
            docs.append(CorpusDoc(rel, text, source))

        # загрузки: kb_dir/uploads/* (только UPLOAD_EXTS)
        up = os.path.join(self.kb_dir, "uploads")
        if os.path.isdir(up):
            for fn in sorted(os.listdir(up)):
                ext = os.path.splitext(fn)[1].lower()
                if ext not in UPLOAD_EXTS:
                    continue
                full = os.path.join(up, fn)
                # Путь в индексе — фиксированный «uploads/<имя>»: kb_dir
                # не входит в доки-структуру репозитория (в проде это
                # data/kb, в тестах — tmp-каталог), relpath от корня репо
                # даёт нестабильный путь (data/kb/… или ../kb/…)
                rel = "uploads/" + fn
                add(rel, self._read_file(full), "upload")

        return docs

    # ---------- индекс ----------

    def load_index(self) -> dict | None:
        """index.db (SQLite, через _IndexStore) или None
        (отсутствует/битый). Форма — как у старого JSON-документа."""
        v = self._store.load()
        return v if isinstance(v, dict) else None

    def _migrate_legacy(self) -> None:
        """Авто-миграция legacy-индекса: index.json есть, index.db нет —
        перенос в SQLite одной транзакцией, затем удаление json-файла.
        Идемпотентна: если index.db уже есть — ничего не делает (db —
        канон). Битый json — как отсутствующий (семантика read_json
        default: файл удаляется, дальше full-сборка)."""
        legacy = os.path.join(self.kb_dir, "index.json")
        if not os.path.isfile(legacy) or self._store.exists():
            return
        doc = read_json(legacy, None)
        if isinstance(doc, dict):
            self._store.replace(doc)
        os.remove(legacy)

    def _metrics(self, chunks: list[Chunk], vecs: list[list[float]],
                 embedder) -> dict:
        """hit@3 / precision@3 / mrr по GOLD_QUERIES: expected_file среди
        top-3 чанков по косинусу; mrr — по позиции 1-го хита."""
        chars = [len(c.text) for c in chunks]
        m = {"chunks": len(chunks),
             "avg_chars": round(sum(chars) / len(chars), 1) if chars else 0,
             "max_chars": max(chars) if chars else 0,
             "hit_at_3": 0.0, "precision_at_3": 0.0, "mrr": 0.0}
        # Gold-фильтр: считаются только запросы, чей expected_file есть
        # среди чанков. Корпус — только загрузки (uploads-only), а
        # gold-файлы GOLD_QUERIES — доки репозитория, поэтому пригодных
        # запросов обычно 0 → нулевые метрики (path выше).
        files = {c.file for c in chunks}
        queries = [t for t in GOLD_QUERIES if t[1] in files]
        if not chunks or not queries:
            return m
        hits = 0
        pres = 0.0
        mrr = 0.0
        for q, expected in queries:
            qv = embedder.embed([q])[0]
            scored = sorted(
                ((cosine(qv, v), i) for i, v in enumerate(vecs)),
                key=lambda t: -t[0])
            top3 = [chunks[i].file for _s, i in scored[:3]]
            pos = next((p for p, f in enumerate(top3, 1)
                        if f == expected), None)
            if pos is not None:
                hits += 1
                mrr += 1.0 / pos
            pres += sum(1 for f in top3 if f == expected) / 3.0
        m["hit_at_3"] = round(hits / len(queries), 4)
        m["precision_at_3"] = round(pres / len(queries), 4)
        m["mrr"] = round(mrr / len(queries), 4)
        return m

    def _chunks_out(self, chunks: list[Chunk],
                    vecs: list[list[float]], strategy: str) -> list[dict]:
        """Сериализация чанков индекса. chunk_id = {file_stem}-
        {strategy}-{i:04d}, i — индекс в пределах файла. "terms" —
        частоты токенов для лексической ноги гибридного поиска (BM25)."""
        counters: dict[str, int] = {}
        out = []
        for c, v in zip(chunks, vecs):
            stem = os.path.splitext(os.path.basename(c.file))[0]
            i = counters.get(c.file, 0)
            counters[c.file] = i + 1
            out.append({
                "chunk_id": f"{stem}-{strategy}-{i:04d}",
                "source": c.source, "file": c.file, "section": c.section,
                "chars": len(c.text), "text": c.text,
                "vector": [round(x, 6) for x in v],
                "terms": dict(sorted(_term_freq(c.text).items())),
            })
        return out

    def build(self, strategy: str, embedder, mode: str = "auto") -> dict:
        """Собрать индекс.

        mode:
        - "full" — полная пересборка: корпус → чанки ОБЕИХ стратегий →
          эмбеддинги обоих наборов → метрики по GOLD_QUERIES → персист в
          index.db (ТОЛЬКО активная стратегия, одна транзакция);
        - "incremental" — только НОВЫЕ файлы (есть в корпусе, нет в
          индексе): их чанки активной стратегии → эмбеддинги → INSERT в
          существующий index.db (старые чанки не переписываются).
          Сравнительный отчёт — от последней полной сборки. Допустимо
          ТОЛЬКО при совпадении strategy+embedder с индексом (иначе —
          full);
        - "auto" (дефолт) — incremental при условиях выше, иначе full.

        Перед сборкой — авто-миграция legacy index.json в index.db
        (если db ещё нет). Ответ: {stats, comparison, strategy, mode,
        added} — added — число добавленных файлов (full — весь корпус).
        """
        if strategy not in ("fixed", "structural"):
            raise ValueError(
                f"Неизвестная стратегия: {strategy!r} (fixed|structural)")
        if mode not in ("auto", "full", "incremental"):
            raise ValueError(
                f"Неизвестный mode: {mode!r} (auto|full|incremental)")
        if self._progress.get("running"):
            raise KBError("Сборка индекса уже идёт")

        self._migrate_legacy()
        existing = self.load_index()
        use_incremental = (
            mode in ("auto", "incremental") and existing is not None
            and existing.get("strategy") == strategy
            and existing.get("embedder") == embedder.name)

        t0 = time.monotonic()
        try:
            if use_incremental:
                return self._build_incremental(existing, strategy,
                                                embedder, t0)
            return self._build_full(strategy, embedder, t0)
        finally:
            self._progress = {"running": False, "phase": "done",
                              "done": self._progress.get("total", 0),
                              "total": self._progress.get("total", 0)}

    def _build_full(self, strategy: str, embedder, t0: float) -> dict:
        self._set_progress("corpus", 0, 1)
        docs = self.corpus_files()
        self._set_progress("chunking", 0, 1)
        fixed = []
        structural = []
        fc = FixedChunker()
        sc = StructuredChunker()
        for d in docs:
            fixed.extend(fc.chunk(d))
            structural.extend(sc.chunk(d))

        total = len(fixed) + len(structural)

        def _cb(offset):
            def cb(done: int, _total: int) -> None:
                self._set_progress("embedding", offset + done, total)
            return cb

        self._set_progress("embedding", 0, total)
        fixed_vecs = embedder.embed([c.text for c in fixed],
                                    on_progress=_cb(0))
        struct_vecs = embedder.embed([c.text for c in structural],
                                     on_progress=_cb(len(fixed)))
        self._set_progress("metrics", 0, 1)
        comparison = {"fixed": self._metrics(fixed, fixed_vecs, embedder),
                      "structural": self._metrics(structural, struct_vecs,
                                                  embedder)}
        if strategy == "structural":
            active, active_vecs = structural, struct_vecs
        else:
            active, active_vecs = fixed, fixed_vecs

        stats = {"docs": len(docs),
                 "files": len({c.file for c in active}),
                 "chunks": len(active),
                 "total_chars": sum(len(c.text) for c in active),
                 "build_ms": int((time.monotonic() - t0) * 1000),
                 "corpus_words": sum(len(d.text.split()) for d in docs)}

        self._set_progress("save", 0, 1)
        chunks_out = self._chunks_out(active, active_vecs, strategy)
        index = {"version": 1, "strategy": strategy,
                 "embedder": embedder.name, "model": embedder.model,
                 "dim": embedder.dim,
                 "built_at": datetime.now(timezone.utc).isoformat(),
                 "stats": stats, "comparison": comparison,
                 "chunks": chunks_out}
        self._store.replace(index)
        return {"stats": stats, "comparison": comparison,
                "strategy": strategy, "mode": "full",
                "added": len(docs)}

    def _build_incremental(self, existing: dict, strategy: str,
                           embedder, t0: float) -> dict:
        """Только новые файлы: INSERT в существующий index.db (та же
        стратегия и эмбеддер) — старые строки НЕ переписываются; meta
        (stats/built_at) обновляется."""
        self._set_progress("corpus", 0, 1)
        docs = self.corpus_files()
        idx_files = {c.get("file") for c in existing.get("chunks", [])}
        new_docs = [d for d in docs if d.path not in idx_files]

        added = 0
        if new_docs:
            chunker = (StructuredChunker() if strategy == "structural"
                       else FixedChunker())
            new_chunks: list[Chunk] = []
            for d in new_docs:
                new_chunks.extend(chunker.chunk(d))
            self._set_progress("embedding", 0, len(new_chunks))
            new_vecs = embedder.embed(
                [c.text for c in new_chunks],
                on_progress=lambda done, tot: self._set_progress(
                    "embedding", done, tot))
            self._store.insert_chunks(
                self._chunks_out(new_chunks, new_vecs, strategy))
            added = len(new_docs)

        # Лексическая нога гибридного поиска: "terms" дополняем ТОЛЬКО
        # у чанков, где их нет (офлайн, дёшево, векторы не трогаем) —
        # апгрейд старых индексов, собранных до появления "terms".
        self._store.upgrade_terms()

        summary = self._store.chunk_summary()
        stats = {"docs": len(docs),
                 "files": summary["files"],
                 "chunks": summary["chunks"],
                 "total_chars": summary["total_chars"],
                 "build_ms": int((time.monotonic() - t0) * 1000),
                 "corpus_words": sum(len(d.text.split()) for d in docs)}
        self._set_progress("save", 0, 1)
        self._store.update_meta(
            {"built_at": datetime.now(timezone.utc).isoformat(),
             "stats": stats})
        return {"stats": stats,
                "comparison": existing.get("comparison") or {},
                "strategy": strategy, "mode": "incremental",
                "added": added}

    # ---------- удаление ----------

    def _recompute_stats(self, old_stats: dict) -> dict:
        """stats индекса после изменения состава файлов (удаление):
        docs/corpus_words — по текущему корпусу, files/chunks/total_chars —
        по оставшимся чанкам index.db, build_ms — от предыдущей
        сборки."""
        docs = self.corpus_files()
        summary = self._store.chunk_summary()
        return {"docs": len(docs),
                "files": summary["files"],
                "chunks": summary["chunks"],
                "total_chars": summary["total_chars"],
                "build_ms": (old_stats or {}).get("build_ms", 0),
                "corpus_words": sum(len(d.text.split()) for d in docs)}

    def delete_upload(self, name: str) -> dict:
        """Удалить загруженный файл и его чанки из индекса.

        ValueError — имя не basename (traversal); FileNotFoundError — файла
        нет в uploads. Индекс без файла — просто удаление файла.
        Ответ: {ok, file, chunks_removed}."""
        safe = os.path.basename(name)
        if safe != name or not safe:
            raise ValueError("Некорректное имя файла")
        path = os.path.join(self.kb_dir, "uploads", safe)
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Файл не найден: {safe}")
        os.remove(path)
        removed = 0
        idx = self.load_index()
        if idx is not None:
            key = "uploads/" + safe
            removed = self._store.delete_file(key)
            if removed:
                self._store.update_meta(
                    {"stats": self._recompute_stats(idx.get("stats"))})
        return {"ok": True, "file": safe, "chunks_removed": removed}

    def wipe(self) -> dict:
        """Полная очист базы: удалить ВСЕ загрузки + index.db.
        settings.json сохраняется (тумблеры/стратегия — как были).
        Ответ: {ok, uploads_removed}."""
        up = os.path.join(self.kb_dir, "uploads")
        n = 0
        if os.path.isdir(up):
            for fn in os.listdir(up):
                fp = os.path.join(up, fn)
                if os.path.isfile(fp):
                    os.remove(fp)
                    n += 1
        self._store.remove()
        # legacy index.json (если миграция ещё не происходила) — тоже
        legacy = os.path.join(self.kb_dir, "index.json")
        if os.path.isfile(legacy):
            os.remove(legacy)
        self._progress = {"running": False, "phase": "idle",
                          "done": 0, "total": 0}
        return {"ok": True, "uploads_removed": n}

    # ---------- поиск ----------

    def _embedder_from_index(self, idx: dict):
        """Эмбеддер по имени из индекса: hash — офлайн, api — из env."""
        name = idx.get("embedder")
        if name == "hash":
            return HashEmbedder()
        if name == "api":
            key = os.environ.get("GPUSTACK_KEY_EMBED", "")
            if not key:
                raise KBError(
                    "Ключ эмбеддингов не настроен (GPUSTACK_KEY_EMBED)")
            base = os.environ.get("GPUSTACK_BASE_URL",
                                  "https://gpustack.data.lmru.tech/v1")
            return APIEmbedder(base, key)
        raise KBError(f"Неизвестный эмбеддер индекса: {name!r}")

    def search(self, query: str, k: int = 5) -> list[dict]:
        """Top-k чанков: гибридный поиск — векторная нога (косинус по
        индексному эмбеддеру) + лексическая (BM25 по "terms"), фузия
        Reciprocal Rank. Индекс обязателен.

        Старые индексы (чанки без "terms") — только вектор; векторная
        нога недоступна (нет ключа api-эмбеддера) и "terms" есть —
        только лексика (fallback, лог [KB]); ни одна нога не работает —
        KBError. [{"chunk_id","source","file","section","score","text"}]
        (score — RRF-сумма, ранги безразмерны)."""
        idx = self.load_index()
        if idx is None:
            raise KBError("Индекс не построен")
        chunks = idx.get("chunks", [])
        k = max(0, k)
        if not chunks or k == 0:
            return []

        has_terms = any(c.get("terms") for c in chunks)
        vec_scores: dict[int, float] = {}
        try:
            qv = self._embedder_from_index(idx).embed([query])[0]
            vec_scores = {i: cosine(qv, c.get("vector") or [])
                          for i, c in enumerate(chunks)}
        except KBError as e:
            if not has_terms:
                raise
            print(f"[KB] векторный поиск недоступен ({e}) — "
                  f"лексический (BM25) fallback")
        vec_rank = (sorted(vec_scores, key=lambda i: (-vec_scores[i], i))
                    if vec_scores else [])
        bm = _bm25_scores(query, chunks) if has_terms else {}
        bm_rank = [i for i, _s in sorted(bm.items(),
                                         key=lambda t: (-t[1], t[0]))]
        fused = _rrf([l for l in (vec_rank, bm_rank) if l])
        if not fused:
            return []
        top = sorted(fused,
                     key=lambda i: (-fused[i], -vec_scores.get(i, 0.0), i)
                     )[:k]
        return [{"chunk_id": chunks[i]["chunk_id"],
                 "source": chunks[i]["source"], "file": chunks[i]["file"],
                 "section": chunks[i]["section"],
                 "score": round(fused[i], 6), "text": chunks[i]["text"]}
                for i in top]

    def search_rag(self, query: str, recall: int, top_k: int,
                   reranker_mode: str = "off",
                   reranker: "APIReranker | None" = None,
                   min_score: float = 0.0) -> dict:
        """Двухэтапный поиск RAG: этап 1 — гибридный top-`recall`
        (`search`), этап 2 — реранкер (reranker_mode "api", модель
        qwen3-reranker-4b) и top-`top_k`.

        Возврат: {"results": [...], "recall_total": N, "reranked": bool}.
        Результат этапа 1 — как у `search`; после реранка у каждого —
        "rerank_score" (0..1), "stage1_rank" (1-based ранг гибрида) и
        "reranked": True; порядок — по rerank_score desc (stable: при
        равенстве — порядок этапа 1).

        `min_score` (0..1, день 23): порог релевантности, применяется
        ПОСЛЕ этапа 1 (+реранка, если был) и ДО top-k среза.
        min_score <= 0 — поведение без изменений (поля не добавляются).
        min_score > 0: при реранке — чанки с rerank_score >= min_score;
        без реранкера — относительный нормализованный порог
        score >= min_score * best (best = max(score); best == 0 —
        пустой результат, деления нет). В ответ аддитивно добавляются
        "filtered": True и "dropped": int (сколько отброшено порогом).

        Индекс не построен — KBError (как у `search`). Реранкер недоступен
        (нет ключа GPUSTACK_KEY_RERANK) или API-сбой — деградация на
        результат этапа 1 (лог [KB], реранк не ломает поиск).
        `reranker` — явный экземпляр (тесты/e2e с fake); None — строится
        из env при mode "api"."""
        stage1 = self.search(query, k=recall)
        out = {"recall_total": len(stage1), "reranked": False}
        if not stage1:
            out["results"] = []
            return out
        if reranker_mode == "api":
            if reranker is None:
                key = os.environ.get("GPUSTACK_KEY_RERANK", "")
                if not key:
                    print("[KB] Reranker: ключ не настроен "
                          "(GPUSTACK_KEY_RERANK) — гибридный поиск")
                else:
                    reranker = APIReranker(
                        os.environ.get("GPUSTACK_BASE_URL",
                                       "https://gpustack.data.lmru.tech/v1"),
                        key)
            if reranker is not None:
                try:
                    scores = reranker.rerank(
                        query, [r["text"] for r in stage1])
                except KBError as e:
                    print(f"[KB] Reranker сбой ({e}) — гибридный поиск")
                else:
                    if len(scores) != len(stage1):
                        raise KBError(
                            f"Reranker: {len(scores)} scores на "
                            f"{len(stage1)} чанков")
                    for i, r in enumerate(stage1):
                        r["rerank_score"] = round(scores[i], 6)
                        r["stage1_rank"] = i + 1
                        r["reranked"] = True
                    stage1.sort(key=lambda r: -r["rerank_score"])
                    out["reranked"] = True
        if min_score > 0:
            if out["reranked"]:
                kept = [r for r in stage1 if r["rerank_score"] >= min_score]
            else:
                # RRF-шкала (0.003..0.033) не сопоставима с порогом 0..1 —
                # относительная нормализация: порог от лучшего результата.
                best = max((r["score"] for r in stage1), default=0)
                kept = ([r for r in stage1
                         if r["score"] >= min_score * best]
                        if best > 0 else [])
            out["filtered"] = True
            out["dropped"] = len(stage1) - len(kept)
            stage1 = kept
        out["results"] = stage1[:max(0, top_k)]
        return out

    # ---------- настройки ----------

    def settings(self) -> dict:
        """settings.json; отсутствующий файл → дефолты (без записи)."""
        v = read_json(self._p_settings, None)
        if not isinstance(v, dict):
            return dict(self.DEFAULT_SETTINGS)
        s = dict(self.DEFAULT_SETTINGS)
        for k in self.DEFAULT_SETTINGS:
            if k in v:
                s[k] = v[k]
        return s

    def update_settings(self, patch: dict) -> dict:
        """Частичное обновление с валидацией (RU ValueError). Лишние ключи
        игнорируются. Атомарная запись; возвращает обновлённые настройки."""
        s = self.settings()
        for k, v in (patch or {}).items():
            if k not in self.DEFAULT_SETTINGS:
                continue  # лишние ключи игнорируются
            if k in ("agent_loop", "rag"):
                if not isinstance(v, bool):
                    raise ValueError(f"{k} должен быть bool (true/false)")
            elif k == "reranker":
                if v not in ("off", "api"):
                    raise ValueError("reranker: off|api")
            elif k == "rag_recall":
                if not isinstance(v, int) or isinstance(v, bool) \
                        or not (1 <= v <= 200):
                    raise ValueError("rag_recall — целое число от 1 до 200")
            elif k == "rag_top_k":
                if not isinstance(v, int) or isinstance(v, bool) \
                        or not (1 <= v <= 10):
                    raise ValueError("rag_top_k — целое число от 1 до 10")
            elif k == "min_score":
                # bool — ПЕРВЫЙ: isinstance(True, int) == True
                if isinstance(v, bool) \
                        or not isinstance(v, (int, float)) \
                        or not (0.0 <= v <= 1.0):
                    raise ValueError("min_score должен быть числом от 0 до 1")
                v = float(v)  # храним float (0 → 0.0, 1 → 1.0)
            elif k == "strategy":
                if v not in ("fixed", "structural"):
                    raise ValueError(
                        "strategy: fixed|structural")
            elif k == "embedder":
                if v not in ("hash", "api"):
                    raise ValueError("embedder: hash|api")
            s[k] = v
        atomic_write_json(self._p_settings, s)
        return s
