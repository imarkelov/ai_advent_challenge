"""MCP-сервер "Pipeline Tools" (день 19) — stdio, newline JSON-RPC 2.0.

Пятый stdio MCP-сервер AI Studio. Даёт модели 3 компонуемых инструмента
(модель сама выстраивает цепочку search -> summarize -> saveToFile):
   search     — поиск по сохранённым дайджестам (data/digests/*.json)
   summarize  — детерминированная extractive-суммаризация текста
   saveToFile — сохранение в data/pipeline: md|txt|json|pdf
                (PDF — собственный stdlib-райтер pdf_writer, кириллица)

Только stdlib. Env читаются в момент вызова (тесты переопределяют):
   PIPELINE_SEARCH_DIR — каталог дайджестов (дефолт <repo>/data/digests)
   PIPELINE_OUT_DIR    — каталог вывода (дефолт <repo>/data/pipeline)
   PIPELINE_FONT_PATH  — .ttf для PDF (дефолт — системный, см. pdf_writer)

Запуск: python studio/mcp_servers/pipeline_tools.py
"""
import json
import os
import re
import sys

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)
import pdf_writer  # noqa: E402

# <repo> = на два уровня выше каталога сервера (studio/mcp_servers -> repo)
REPO_ROOT = os.path.dirname(os.path.dirname(SERVER_DIR))

STOPWORDS = frozenset((
    "и в на не что а как но это то по для с из к у о от за при бы уже "
    "так есть был была было были он она оно они мы вы я их его ее ее "
    "the a an and or but of to in on is are was were it that".split()))
MIN_WORD = 2
MATCH_CAP = 20
_ENTRY_FIELDS = ("title", "summary", "snippet", "city")


def _search_dir():
    return (os.environ.get("PIPELINE_SEARCH_DIR")
            or os.path.join(REPO_ROOT, "data", "digests"))


def _out_dir():
    d = (os.environ.get("PIPELINE_OUT_DIR")
         or os.path.join(REPO_ROOT, "data", "pipeline"))
    os.makedirs(d, exist_ok=True)
    return d


def _font_path():
    return os.environ.get("PIPELINE_FONT_PATH") or None


def _ok_result(payload: dict) -> dict:
    """Успешный результат tools/call: текст = pretty-JSON."""
    return {"content": [{"type": "text",
                         "text": json.dumps(payload, ensure_ascii=False,
                                            indent=2)}],
            "isError": False}


def _error_result(message: str) -> dict:
    """Ошибка tools/call: текст = JSON {"error": ...}, isError: True."""
    return _ok_result({"error": message}) | {"isError": True}


# ---------- search ----------

def _digests_from(data):
    """JSON файла дайджестов (схема collector.py) -> список дайджестов.
    Один dict (last-digest.json) или список (history.json)."""
    if isinstance(data, dict):
        if "generated_at" in data or "news" in data:
            return [data]
        return []
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)
                and ("generated_at" in d or "news" in d)]
    return []


def _flatten_entries(digest: dict) -> list:
    """Дайджест -> записи {title, summary, snippet, city, generated_at}:
    одна по сводке (summary+city) и по одной на новость (title+url)."""
    gen = str(digest.get("generated_at") or "")
    weather = digest.get("weather")
    city = str(weather.get("city") or "") if isinstance(weather, dict) else ""
    entries = [{"title": str(digest.get("id") or "Дайджест"),
                "summary": str(digest.get("summary") or ""),
                "snippet": "", "city": city, "generated_at": gen}]
    news = digest.get("news")
    if isinstance(news, dict):
        for _src, items in news.items():
            if not isinstance(items, list):
                continue
            for it in items:
                if not isinstance(it, dict):
                    continue
                entries.append({
                    "title": str(it.get("title") or ""), "summary": "",
                    "snippet": str(it.get("url") or ""), "city": city,
                    "generated_at": gen})
    return entries


def _matches_text(matches: list) -> str:
    """Готовый текст совпадений: по одной строке на запись — непустые
    части [title, summary, snippet] склеены ' — '; непустой city
    дописан как ' [city]'; пустые строки пропускаются. Предназначен,
    чтобы модель скопировала его как есть в summarize.text /
    saveToFile.content."""
    lines = []
    for m in matches:
        parts = [m[k] for k in ("title", "summary", "snippet") if m.get(k)]
        line = " — ".join(parts)
        if m.get("city"):
            line += " [" + m["city"] + "]"
        if line.strip():
            lines.append(line.strip())
    return "\n".join(lines)


def _call_search(arguments: dict) -> dict:
    q = (arguments or {}).get("query")
    if not isinstance(q, str) or not q.strip():
        return _error_result("search: аргумент 'query' (строка) обязателен")
    q = q.strip()
    d = _search_dir()
    if not os.path.isdir(d):
        return _error_result("search: каталог дайджестов не найден: " + d)
    ql = q.lower()
    matches = []
    for fn in sorted(os.listdir(d)):
        if not fn.lower().endswith(".json"):
            continue
        try:
            with open(os.path.join(d, fn), encoding="utf-8") as f:
                data = json.load(f)
        except (ValueError, OSError):
            continue
        for dg in _digests_from(data):
            for e in _flatten_entries(dg):
                hay = " ".join(str(e[k] or "") for k in _ENTRY_FIELDS).lower()
                if ql in hay:
                    m = {k: e[k] for k in _ENTRY_FIELDS}
                    m["generated_at"] = e["generated_at"]
                    m["file"] = fn
                    matches.append(m)
    if not matches:
        return _error_result("no matches for '%s'" % q)
    matches.sort(key=lambda m: (m["generated_at"], m["title"], m["file"]),
                 reverse=True)
    return _ok_result({"query": q, "count": len(matches),
                       "matches": matches[:MATCH_CAP],
                       "text": _matches_text(matches[:MATCH_CAP])})


# ---------- summarize ----------

_SENT_RE = re.compile(r"(?<=[.!?])\s+")
_WORD_RE = re.compile(r"[0-9a-zа-яё]+")


def _split_sentences(text: str) -> list:
    out = []
    for part in _SENT_RE.split(text.strip()):
        p = part.strip()
        if p:
            out.append(p)
    return out


def _extractive_summary(text: str, max_points: int) -> list:
    """Детерминированная extractive-выжимка: топ-N предложений по частоте
    слов (стоп-слова исключены); при равенстве очков — исходный порядок."""
    sentences = _split_sentences(text)
    if not sentences:
        return []
    freq = {}
    for s in sentences:
        for w in _WORD_RE.findall(s.lower()):
            if len(w) >= MIN_WORD and w not in STOPWORDS:
                freq[w] = freq.get(w, 0) + 1
    scored = []
    for idx, s in enumerate(sentences):
        score = sum(freq.get(w, 0) for w in _WORD_RE.findall(s.lower())
                    if w in freq)
        scored.append((-score, idx, s))
    scored.sort()
    return [s for _neg, _i, s in scored[:max_points]]


def _call_summarize(arguments: dict) -> dict:
    text = (arguments or {}).get("text")
    if not isinstance(text, str) or not text.strip():
        return _error_result("summarize: аргумент 'text' (непустая строка) "
                             "обязателен")
    mp = (arguments or {}).get("max_points")
    try:
        mp = int(mp) if mp is not None else 8
    except (TypeError, ValueError):
        mp = 8
    mp = max(1, min(mp, 20))
    points = _extractive_summary(text, mp)
    if not points:
        return _error_result("summarize: в тексте не найдено "
                             "ни одного предложения")
    summary = "\n".join("• " + p for p in points)
    return _ok_result({"summary": summary, "points": points,
                       "input_chars": len(text),
                       "output_chars": len(summary)})


# ---------- saveToFile ----------

def _call_save_to_file(arguments: dict) -> dict:
    a = arguments or {}
    filename = a.get("filename")
    content = a.get("content")
    fmt = a.get("format") or "md"
    if not isinstance(filename, str) or not filename.strip():
        return _error_result("saveToFile: 'filename' — непустая строка")
    if not isinstance(content, str):
        return _error_result("saveToFile: 'content' — строка")
    if fmt not in ("md", "txt", "json", "pdf"):
        return _error_result("saveToFile: 'format' — md|txt|json|pdf")
    name = os.path.basename(filename.strip())  # path traversal -> имя
    if not name or set(name) <= {"."}:
        return _error_result("saveToFile: недопустимое имя файла: "
                             + repr(filename))
    name = re.sub(r"[^\w.\- ]", "_", name)
    if fmt == "pdf" and not name.lower().endswith(".pdf"):
        name += ".pdf"
    out_dir = _out_dir()
    target = os.path.join(out_dir, name)
    try:
        if fmt == "json":
            blob = json.dumps(content, ensure_ascii=False,
                              indent=2).encode("utf-8")
        elif fmt == "pdf":
            blob = pdf_writer.text_to_pdf(content, font_path=_font_path())
        else:
            blob = content.encode("utf-8")
    except pdf_writer.PdfError as e:
        return _error_result("saveToFile: PDF: " + str(e))
    tmp = os.path.join(out_dir, "." + name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(blob)
    os.replace(tmp, target)  # атомарно
    return _ok_result({"path": os.path.abspath(target), "size": len(blob),
                       "format": fmt})


TOOLS = [
    {"name": "search",
      "description": ("Поиск по сохранённым дайджестам (data/digests/*.json, "
                      "схема collector.py): case-insensitive substring по "
                      "title+summary+snippet+city. До 20 совпадений, свежие "
                      "сначала (generated_at desc). Результат также содержит "
                      "готовое поле text (многострочное: по записи строка "
                      "«title — summary — snippet [city]») — скопируй его "
                      "как есть в summarize.text / saveToFile.content."),
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string",
                   "description": "Поисковый запрос (substring)"}},
         "required": ["query"]}},
    {"name": "summarize",
     "description": ("Детерминированная extractive-суммаризация: топ-N "
                     "предложений по частоте слов (RU+EN стоп-слова). "
                     "Возвращает summary (строки '• точка'), points, "
                     "input_chars, output_chars."),
     "inputSchema": {"type": "object", "properties": {
         "text": {"type": "string",
                  "description": "Текст для суммаризации"},
         "max_points": {"type": "integer",
                        "description": "Число точек (дефолт 8, макс 20)"}},
         "required": ["text"]}},
    {"name": "saveToFile",
     "description": ("Сохранить content атомарно в каталоге вывода "
                     "(data/pipeline): md|txt|json|pdf. PDF — собственный "
                     "stdlib-райтер (A4, кириллица, шрифт вшит). "
                     "Возвращает path, size, format."),
     "inputSchema": {"type": "object", "properties": {
         "filename": {"type": "string",
                      "description": ("Имя файла (только basename; "
                                       "символы вне [\\w.\\- ] -> _)")},
         "content": {"type": "string",
                     "description": "Содержимое файла"},
         "format": {"type": "string",
                    "enum": ["md", "txt", "json", "pdf"],
                    "description": "Формат (дефолт md; pdf -> +.pdf)"}},
         "required": ["filename", "content"]}},
]

CALL_HANDLERS = {
    "search": _call_search,
    "summarize": _call_summarize,
    "saveToFile": _call_save_to_file,
}


def _call_tool(params: dict) -> dict:
    name = (params or {}).get("name")
    handler = CALL_HANDLERS.get(name)
    if handler is None:
        return _error_result("Инструмент «%s» не найден" % name)
    try:
        return handler((params or {}).get("arguments") or {})
    except Exception as e:
        return _error_result("Внутренняя ошибка: " + str(e))


def main() -> None:
    """Цикл: по одной JSON-строке из stdin, ответ — строка в stdout.
    Notification (без id) не получает ответа."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if "id" not in m:  # notification — без ответа
            continue
        if m.get("method") == "initialize":
            result = {"protocolVersion": "2024-11-05", "capabilities": {},
                      "serverInfo": {"name": "pipeline-tools",
                                     "version": "1.0"}}
        elif m.get("method") == "tools/list":
            result = {"tools": TOOLS}
        elif m.get("method") == "tools/call":
            result = _call_tool(m.get("params"))
        else:
            sys.stdout.write(json.dumps(
                {"jsonrpc": "2.0", "id": m["id"],
                 "error": {"code": -32601, "message": "метод не найден"}},
                ensure_ascii=False) + "\n")
            sys.stdout.flush()
            continue
        sys.stdout.write(json.dumps(
            {"jsonrpc": "2.0", "id": m["id"], "result": result},
            ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
