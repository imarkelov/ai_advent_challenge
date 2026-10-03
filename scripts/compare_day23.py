# -*- coding: utf-8 -*-
r"""День 23: сравнение 4 рук RAG — plain / rag / rag+filter / rag+rewrite —
на 10 контрольных вопросах + 2 «отвлекающих» (темы, заведомо отсутствующие
в корпусе), детерминированный факт-чек + LLM-judge (4 руки в одном вызове),
отчёт ВСЕГДА пишется.

Сценарий (полностью автономный, паттерн compare_day22.py):
  1. Инфраструктура (HARD):
     - порт 8108: busy → ожидание до 10 мин → SKIP (exit 0);
     - uvicorn subprocess (127.0.0.1:8108, -X utf8, PYTHONUTF8=1,
       логи → %TEMP%\opencode\compare_day23_server.log);
     - probe GPustack (GET {base}/models, timeout 10s): HTTP-ответ
       (даже 401) = UP; connection error = down → SKIP (exit 0);
     - корпус: DELETE /api/kb → книги fetch_books.py (import модуля;
       существующие валидные файлы ≥50KB переиспользуются, недостающие
       добираются через resolve_book → seed) + egg_book.txt (fixture)
       через multipart POST /api/kb/upload;
     - POST /api/kb/index {strategy:"structural", embedder:"api"}
       (долгий синхронный вызов — фоновый тред) + poll
       /api/kb/build-status (1.5s, timeout 30 мин);
     - POST /api/kb/settings {embedder:"api", reranker:"api",
       rag_recall:50, rag_top_k:3, min_score:0.0} — ЯВНО: дефолт
       min_score = 0.0 НЕ меняется, фильтр включается body-override'ом
       сравнения (см. ниже).
  2. Ран (HARD: завершённость): 10 вопросов control_questions.json
     (COPY, файл не модифицируется) + 2 дистрактора (борщ, Python
     3.13 — expect_facts=[], expected_sources=[] — скип-логика по
     источникам безопасна: missing=[] → не скип; sources_ok=None).
     Один POST /api/rag/compare на вопрос с body {"question",
     "min_score": 0.6} — min_score-override активирует ФИЛЬТР-руку на
     весь ран (настройки KB остаются 0.0; plain/rag руки фильтр
     не применяют — семантика agent.rag_compare). 4 руки в одном
     ответе: answer_plain / answer_rag / answer_rag_filter /
     answer_rag_rewrite (+ chunks*, rewritten_query, rewrite_applied).
     Ошибка одного вопроса → error в записи, продолжаем.
  3. Оценка (SOFT — записывается, никогда не фейлит):
     - факт-чек: нормализованное (lower, collapse spaces, ё→е)
       подстроковое совпадение каждого expect_fact в КАЖДОЙ из 4 рук;
     - sources_ok: set(chunks[rag-руки].file) ∩ expected_sources ≠ ∅
       (префикс "uploads/" снимается); для дистракторов — None;
     - LLM-judge: один вызов на вопрос (4 ответа A/B/C/D +
       expect_facts, T=0, thinking off, strict JSON
       {"scores": {plain,rag,rag_filter,rag_rewrite: 1-10},
       "verdict", "reason"}; verdict: plain_wins|rag_wins|
       rag_filter_wins|rag_rewrite_wins|tie|inconclusive);
       дистракторам — заметка «честное признание отсутствия
       информации — хороший ответ, галлюцинация — низкий балл»;
       сбой judge → judge: null.
  4. Отчёт (try/finally — ВСЕГДА, даже при крахе):
     .omo/evidence/day23-compare/compare.json + report.md
     (таблица 12×4 руки + judge, win-rate по рукам, rewrite-инфо,
     settings-эхо с пометкой о body-override 0.6, ЧЕСТНЫЙ итог —
     фильтр/rewrite могут НЕ дать выигрыша, пишем как есть).
  5. Exit 0 — PASS или SKIP; exit 1 — инфраструктурный FAIL (сервер не
     поднялся, индекс не собрался, отчёт не записан).

Только stdlib (urllib — паттерн compare_day22.py).
"""
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
HOST = "127.0.0.1"
PORT = 8108  # 8100 — e2e_studio.py, 8101-8105 — e2e дней 17-21,
             # 8106 — compare_day22, 8107 — e2e_day23
BASE = f"http://{HOST}:{PORT}"
EVIDENCE_DIR = os.path.join(REPO, ".omo", "evidence", "day23-compare")
SERVER_LOG = os.path.join(tempfile.gettempdir(), "opencode",
                          "compare_day23_server.log")
QUESTIONS_FILE = os.path.join(
    REPO, "studio", "backend", "tests", "fixtures", "control_questions.json")
EGG_BOOK = os.path.join(
    REPO, "studio", "backend", "tests", "fixtures", "egg_book.txt")
UPLOADS_DIR = os.path.join(REPO, "data", "kb", "uploads")

WAIT_PORT_BUSY_MAX = 600   # 10 минут ожидания освобождения порта
WAIT_PORT_BUSY_STEP = 10
WAIT_SERVER_UP_MAX = 90
PROBE_TIMEOUT = 10
INDEX_TIMEOUT = 1800       # 30 минут на сборку индекса (api-эмбеддинги)
INDEX_POLL_STEP = 1.5
COMPARE_TIMEOUT = 600      # на вопрос: 5 LLM-вызовов (plain, rag,
                           # filter-retrieval, rewrite, rag+rewrite)
JUDGE_TIMEOUT = 120
REUSE_MIN_SIZE = 50 * 1024  # файл книги ≥50KB — переиспользуем, не качаем
MIN_SCORE_OVERRIDE = 0.6    # body-override сравнения: активирует
                            # фильтр-руку (настройки KB остаются 0.0)
MODES = ("plain", "rag", "rag_filter", "rag_rewrite")
ANSWER_KEYS = {"plain": "answer_plain", "rag": "answer_rag",
               "rag_filter": "answer_rag_filter",
               "rag_rewrite": "answer_rag_rewrite"}
CHUNK_KEYS = {"rag": "chunks", "rag_filter": "chunks_rag_filter",
              "rag_rewrite": "chunks_rag_rewrite"}
JUDGE_VERDICTS = ("plain_wins", "rag_wins", "rag_filter_wins",
                  "rag_rewrite_wins", "tie", "inconclusive")

# 2 «отвлекающих» вопроса: темы ЗАВЕДОМО отсутствуют в книжном корпусе
# (pushkin/chekhov/tolstoy/gogol/egg_book). expect_facts=[] /
# expected_sources=[] — скип-логика по источникам безопасна
# (missing=[]) → вопрос выполняется; sources_ok=None, факт-чек «—».
DISTRACTORS = [
    {
        "id": 11,
        "distractor": True,
        "question": ("Какой рецепт борща с грибами рекомендуют "
                     "современные повара и сколько варить свёклу?"),
        "expect_facts": [],
        "expected_sources": [],
    },
    {
        "id": 12,
        "distractor": True,
        "question": ("Какие новые ключевые слова появились в Python 3.13 "
                     "и как их использовать в асинхронном коде?"),
        "expect_facts": [],
        "expected_sources": [],
    },
]

# 10 контрольных вопросов (committed fixture) + 2 отвлекающих = 12
with open(QUESTIONS_FILE, encoding="utf-8") as _qf:
    QUESTIONS = json.load(_qf) + DISTRACTORS

# Windows-консоль cp1251: печатать UTF-8, errors="replace" — print
# никогда не роняет скрипт (паттерн дня 18 / compare_day22).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def log(msg: str) -> None:
    print(f"[compare-day23] {msg}", flush=True)


# ---------- env / http ----------

def load_dotenv() -> None:
    """Тот же формат, что в backend/main.py (реальные env — приоритет)."""
    path = os.path.join(REPO, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(),
                                  value.strip().strip('"').strip("'"))


# Локальный сервер (127.0.0.1) — ТОЛЬКО напрямую, без прокси (паттерн
# compare_day22.py: мёртвый прокси не должен ломать polling сервера).
# Внешние вызовы (probe GPustack, judge) — обычный urlopen, прокси уважает.
_NOPROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http(method: str, path: str, body=None, timeout: int = 30) -> tuple:
    """-> (status, raw_bytes, headers). HTTPError -> (code, body, hdrs)."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with _NOPROXY.open(req, timeout=timeout) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def http_multipart(path: str, filename: str, content: bytes,
                   timeout: int = 120) -> tuple:
    """POST multipart (поле "file") — загрузка в /api/kb/upload."""
    boundary = "----cmpday23" + uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; '
        f'filename="{filename}"\r\n'
        f"Content-Type: text/plain\r\n\r\n"
    ).encode("utf-8") + content + f"\r\n--{boundary}--\r\n".encode("utf-8")
    req = urllib.request.Request(BASE + path, data=body, method="POST")
    req.add_header("Content-Type",
                   f"multipart/form-data; boundary={boundary}")
    try:
        with _NOPROXY.open(req, timeout=timeout) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


# ---------- порт / сервер ----------

def port_open() -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1)
    try:
        return s.connect_ex((HOST, PORT)) == 0
    finally:
        s.close()


def wait_port_free():
    """None — порт свободен; иначе строка причины SKIP."""
    if not port_open():
        return None
    log(f"порт {PORT} занят — жду до 10 мин (чужой сервер НЕ убиваю)")
    deadline = time.time() + WAIT_PORT_BUSY_MAX
    while time.time() < deadline:
        time.sleep(WAIT_PORT_BUSY_STEP)
        if not port_open():
            return None
    return f"порт {PORT} занят дольше {WAIT_PORT_BUSY_MAX}s"


def start_server():
    log(f"запускаю uvicorn (127.0.0.1:{PORT})…")
    os.makedirs(os.path.dirname(SERVER_LOG), exist_ok=True)
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    logf = open(SERVER_LOG, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-X", "utf8", "-m", "uvicorn",
         "studio.backend.main:app", "--host", HOST, "--port", str(PORT)],
        cwd=REPO, stdout=logf, stderr=subprocess.STDOUT, env=env,
        creationflags=flags,
    )
    deadline = time.time() + WAIT_SERVER_UP_MAX
    while time.time() < deadline:
        if proc.poll() is not None:
            logf.close()
            return None  # процесс умер сам (порт занят / ImportError)
        try:
            code, _, _ = http("GET", "/api/dialogues", timeout=3)
            if code == 200:
                return proc
        except Exception:
            pass
        time.sleep(1)
    # Таймаут: убиваем СВОЙ же процесс (не чужой!) — иначе orphan будет
    # держать порт, и stop_server(None) его не найдёт.
    log("uvicorn не ответил за 90s — убиваю свой процесс")
    stop_server(proc)
    return None


def stop_server(proc) -> None:
    if proc is None:
        return
    log(f"останавливаю uvicorn (порт {PORT})")
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, timeout=15)
        else:
            proc.terminate()
            proc.wait(timeout=15)
    except Exception as e:
        log(f"warning: kill: {e}")
    deadline = time.time() + 15
    while time.time() < deadline and port_open():
        time.sleep(0.5)
    if port_open():
        log(f"warning: порт {PORT} всё ещё открыт после stop")


# ---------- GPustack probe ----------

def probe_gpustack():
    """None — доступен, иначе строка причины SKIP."""
    base = os.environ.get("GPUSTACK_BASE_URL", "").strip().rstrip("/")
    if not base:
        return "GPUSTACK_BASE_URL not set (environment or .env)"
    if not os.environ.get("GPUSTACK_API_KEY", "").strip():
        return "GPUSTACK_API_KEY not set (environment or .env)"
    key = os.environ.get("GPUSTACK_API_KEY", "").strip()
    req = urllib.request.Request(
        f"{base}/models",
        headers={"Authorization": f"Bearer {key}"}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT) as resp:
            resp.read()
        return None
    except urllib.error.HTTPError:
        return None  # API ответил (любой код, даже 401) — сервис на месте
    except Exception as e:
        return f"GPustack недоступен ({base}): {e.__class__.__name__}"


# ---------- корпус ----------

def snapshot_books(staging: str) -> None:
    """Снапшот скачанных (>= REUSE_MIN_SIZE) книг из uploads — чтобы
    после wipe их не перекачивать заново (сеть = время)."""
    if not os.path.isdir(UPLOADS_DIR):
        return
    for name in os.listdir(UPLOADS_DIR):
        path = os.path.join(UPLOADS_DIR, name)
        if os.path.isfile(path) and os.path.getsize(path) >= REUSE_MIN_SIZE:
            shutil.copy2(path, os.path.join(staging, name))
            log(f"corpus: переиспользуем {name} "
                f"({os.path.getsize(path)} B)")


def prepare_corpus() -> list:
    """5 книг каталога fetch_books -> data/kb/uploads (staging-копия →
    resolve_book: urls → fallback → seed) + egg_book.txt. Возвращает
    список имён для upload. Raise, если ни одна книга не получена."""
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import fetch_books
    os.makedirs(UPLOADS_DIR, exist_ok=True)
    staging = os.path.join(tempfile.gettempdir(), "opencode",
                           "compare_day23_staging")
    shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(staging, exist_ok=True)
    snapshot_books(staging)
    names = []
    for entry in fetch_books.CATALOG:
        name = entry["filename"]
        dest = os.path.join(UPLOADS_DIR, name)
        snap = os.path.join(staging, name)
        if os.path.exists(snap):
            shutil.copy2(snap, dest)
            names.append(name)
            log(f"corpus: {name:28s} reused")
            continue
        res = fetch_books.resolve_book(entry, "")
        if res.get("text"):
            fetch_books.atomic_write(dest, res["text"].encode("utf-8"))
            names.append(name)
            log(f"corpus: {name:28s} {res.get('status', 'ok')}")
        else:
            log(f"warning: {name}: не удалось получить")
    # staging не удаляем — кэш переиспользования для следующих прогонов
    names.append("egg_book.txt")
    if len(names) <= 1:
        raise RuntimeError("corpus пуст: ни одна книга не скачалась")
    return names


def upload_corpus(files: list) -> None:
    """Multipart-upload каждого файла. Raise при != 200."""
    for name in files:
        path = (EGG_BOOK if name == "egg_book.txt"
                else os.path.join(UPLOADS_DIR, name))
        with open(path, "rb") as f:
            content = f.read()
        code, raw, _ = http_multipart(
            "/api/kb/upload", name, content)
        if code != 200:
            raise RuntimeError(f"upload {name}: HTTP {code}: "
                               f"{raw.decode('utf-8', 'replace')[:200]}")
        log(f"uploaded {name} ({len(content)} B)")


def build_index() -> dict:
    """POST /api/kb/index (фоновый поток, синхронный на сервере), poll
    /api/kb/build-status {running, phase, done, total}. Успех — GET
    /api/kb/stats отдаёт 200 (зеркало day22). Raise по таймауту/ошибке."""
    t0 = time.time()
    threading.Thread(target=_index_worker, daemon=True).start()
    while time.time() - t0 < INDEX_TIMEOUT:
        time.sleep(INDEX_POLL_STEP)
        code, raw, _ = http("GET", "/api/kb/build-status", timeout=15)
        if code == 200:
            st = json.loads(raw.decode("utf-8", "replace"))
            if st.get("running"):
                log(f"индексация: phase={st.get('phase')} "
                    f"{st.get('done')}/{st.get('total')}")
        try:
            return get_stats()
        except RuntimeError:
            continue
    raise RuntimeError(f"index build timeout ({INDEX_TIMEOUT}s)")


def _index_worker():
    try:
        code, raw, _ = http(
            "POST", "/api/kb/index",
            body={"strategy": "structural", "embedder": "api"},
            timeout=INDEX_TIMEOUT)
        if code != 200:
            log(f"warning: index POST: HTTP {code}: "
                f"{raw.decode('utf-8', 'replace')[:200]}")
    except Exception as e:  # фоновый поток: ошибка видна в poll-статусе
        log(f"warning: index POST: {e}")


def get_stats() -> dict:
    code, raw, _ = http("GET", "/api/kb/stats", timeout=30)
    if code != 200:
        raise RuntimeError(f"GET /api/kb/stats: HTTP {code}")
    return json.loads(raw.decode("utf-8", "replace"))


# ---------- оценка фактов ----------

def norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower().replace("ё", "е")).strip()


def check_facts(answer: str, facts: list) -> list:
    """Каждый факт: (подстрока) OR (>= 2 значимых слова)."""
    a = norm(answer)
    hits = []
    for fact in facts:
        fn = norm(fact)
        if fn in a:
            hits.append(fact)
            continue
        words = [w for w in fn.split()
                 if len(w) >= 4 and not w.isdigit()]
        if len(words) >= 2 and all(w in a for w in words):
            hits.append(fact)
        else:
            hits.append(False)
    return hits


def chunk_files(chunks) -> set:
    out = set()
    for c in chunks or []:
        src = c.get("file") or c.get("source") or ""
        if src:
            out.add(src.replace("\\", "/").split("uploads/")[-1])
    return out


# ---------- judge (LLM) ----------

def judge_question(model: str, question: str, expect: dict,
                   answers: dict, distractor: bool):
    """Один LLM-вызов: сравнение 4 ответов A-D. -> dict или None."""
    if distractor or not expect.get("facts"):
        facts_block = ("Эталонных фактов нет. Если правильный ответ — "
                       "«в источниках этого нет», высокая оценка.")
    else:
        facts_block = "Эталонные факты (ожидается в правильном ответе):\n" + \
            "\n".join(f"- {f}" for f in expect["facts"])
    system = (
        "Ты строгий оценщик ответов на вопросы по электронной библиотеке. "
        "Сравни ЧЕТЫРЕ ответа на один вопрос и верни СТРОГО JSON без "
        "markdown и пояснений.\n"
        f"{facts_block}\n\n"
        "Правила оценки: 1-10 за каждый ответ; полнота покрытия фактов, "
        "отсутствие выдумок, точность. Ответы могут быть частичными.\n"
        'Формат: {"scores": {"plain": N, "rag": N, "rag_filter": N, '
        '"rag_rewrite": N}, "verdict": "<plain_wins|rag_wins|'
        'rag_filter_wins|rag_rewrite_wins|tie|inconclusive>", '
        '"reason": "<1-2 предложения>"}\n'
        "verdict — лучший ответ по сумме; tie при равенстве; "
        "inconclusive если все ответы плохие/нерелевантные."
    )
    user = (
        f"Вопрос: {question}\n\n"
        f"A (без RAG): {answers.get('plain') or ''}\n\n"
        f"B (RAG): {answers.get('rag') or ''}\n\n"
        f"C (RAG+фильтр): {answers.get('rag_filter') or ''}\n\n"
        f"D (RAG+rewrite): {answers.get('rag_rewrite') or ''}\n\n"
        "Верни только JSON."
    )
    base = os.environ.get("GPUSTACK_BASE_URL", "").strip().rstrip("/")
    # GPUSTACK_API_KEY не имеет доступа к chat-модели (403 Forbidden) -
    # judge ходит модельным ключом (как backend), с fallback.
    key = (os.environ.get("GPUSTACK_KEY_DEEPSEEK", "").strip()
           or os.environ.get("GPUSTACK_API_KEY", "").strip())
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": 0,
        "max_tokens": 500,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(payload).encode("utf-8"), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(req, timeout=JUDGE_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        text = data["choices"][0]["message"]["content"]
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            return None
        out = json.loads(m.group(0))
        scores = out.get("scores") or {}
        clean = {}
        for k in MODES:
            v = scores.get(k)
            if isinstance(v, (int, float)) and 1 <= v <= 10:
                clean[k] = round(float(v), 1)
        out["scores"] = clean or scores  # частичный валидный -> оставляем
        if out.get("verdict") not in JUDGE_VERDICTS:
            out["raw_verdict"] = out.get("verdict")
            out["verdict"] = "inconclusive"
        return out
    except Exception:
        return None


# ---------- прогон ----------

def new_record(q: dict) -> dict:
    return {
        "id": q["id"], "question": q["question"],
        "expect_facts": q.get("expect_facts", []),
        "expected_sources": q.get("expected_sources", []),
        "distractor": bool(q.get("distractor")),
        "skipped": None,
        "answer_plain": None, "answer_rag": None,
        "answer_rag_filter": None, "answer_rag_rewrite": None,
        "chunks": None, "chunks_rag_filter": None,
        "chunks_rag_rewrite": None,
        "rewritten_query": None, "rewrite_applied": None,
        "facts": None, "sources_ok": None, "judge": None,
    }


def run_questions(model: str, questions: list) -> list:
    records = []
    for q in questions:
        rec = new_record(q)
        records.append(rec)
        if rec["distractor"]:
            log(f"Q{q['id']:02d} (отвлекающий) {q['question'][:50]}…")
        else:
            log(f"Q{q['id']:02d} {q['question'][:50]}…")
        code, raw, _ = http("POST", "/api/rag/compare",
                            body={"question": q["question"],
                                  "min_score": MIN_SCORE_OVERRIDE},
                            timeout=COMPARE_TIMEOUT)
        if code != 200:
            rec["skipped"] = f"HTTP {code}: {raw.decode('utf-8','replace')[:150]}"
            log(f"  SKIP: {rec['skipped']}")
            continue
        data = json.loads(raw.decode("utf-8", "replace"))
        for mode in MODES:
            rec[ANSWER_KEYS[mode]] = data.get(ANSWER_KEYS[mode])
        for rmode, key in CHUNK_KEYS.items():
            rec[key] = data.get(key)
        rec["rewritten_query"] = data.get("rewritten_query")
        rec["rewrite_applied"] = data.get("rewrite_applied")
        # факты по каждому режиму
        facts = {}
        for fact in q.get("expect_facts", []):
            facts[fact] = {mode: bool(check_facts(rec[ANSWER_KEYS[mode]]
                                                  or "", [fact])[0])
                           for mode in MODES}
        rec["facts"] = facts or None
        # источники: по rag-чанкам
        if q.get("expected_sources"):
            rec["sources_ok"] = bool(
                chunk_files(data.get("chunks"))
                & set(q["expected_sources"]))
        else:
            rec["sources_ok"] = None
        t = judge_question(model, q["question"], q,
                           {m: rec[ANSWER_KEYS[m]] for m in MODES},
                           rec["distractor"])
        rec["judge"] = t
        log(f"  done, judge={'ok' if t else 'fail'}")
    return records


# ---------- отчёт ----------

def _excerpt(text, n: int = 70) -> str:
    t = re.sub(r"\s+", " ", (text or "").replace("|", "/")).strip()
    return (t[:n] + "…") if len(t) > n else t


def _pct(num: int, den: int) -> str:
    return f"{100.0 * num / den:.0f}%" if den else "—"


def _mode_cell(rec: dict, mode: str) -> str:
    """'✓ k/n · excerpt' или '— · excerpt'; None -> '✗'."""
    ans = rec.get(ANSWER_KEYS[mode])
    ex = _excerpt(ans)
    if not ans or ans.startswith("Ошибка"):
        return f"✗ · {ex}" if ex else "✗"
    facts = rec.get("facts")
    if rec["distractor"] or not facts:
        return f"— · {ex}"
    k = sum(1 for f in facts.values() if f.get(mode))
    n = len(facts)
    return f"{'✓' if k == n else '○'} {k}/{n} · {ex}"


def _summarize(records: list) -> dict:
    """Агрегаты по режимам: факты, judge-верdicts, avg scores."""
    out = {}
    for mode in MODES:
        f_ok = f_tot = 0
        scores = []
        for rec in records:
            facts = rec.get("facts")
            if rec["distractor"] or not facts:
                continue
            f_tot += len(facts)
            f_ok += sum(1 for f in facts.values() if f.get(mode))
            j = rec.get("judge") or {}
            s = (j.get("scores") or {}).get(mode)
            if isinstance(s, (int, float)):
                scores.append(float(s))
        verdicts = {}
        for rec in records:
            j = rec.get("judge")
            if j:
                verdicts[j.get("verdict", "inconclusive")] = \
                    verdicts.get(j.get("verdict", "inconclusive"), 0) + 1
        out[mode] = {
            "facts_ok": f_ok, "facts_total": f_tot,
            "facts_rate": round(f_ok / f_tot, 3) if f_tot else None,
            "avg_score": round(sum(scores) / len(scores), 2)
            if scores else None,
            "n_scored": len(scores),
        }
    out["verdicts"] = verdicts
    out["sources_ok"] = {
        "ok": sum(1 for r in records if r.get("sources_ok") is True),
        "total": sum(1 for r in records if r.get("sources_ok") is not None),
    }
    out["rewrite_applied"] = sum(
        1 for r in records if r.get("rewrite_applied") is True)
    out["judged"] = sum(1 for r in records if r.get("judge"))
    return out


def build_conclusion(records: list, summ: dict, stats: dict,
                     duration_s: float) -> list:
    lines = []
    judged = summ["judged"]
    v = summ["verdicts"]
    if judged == 0:
        lines.append("Judge не дал ни одного результата — вывод по "
                     "фактам/источникам (см. таблицу).")
        return lines
    lines.append(f"Judge-верdicts по {judged} вопросам: "
                 + ", ".join(f"{k}={n}" for k, n in sorted(v.items(),
                                                           key=lambda x: -x[1]))
                 + ".")
    # rag vs plain
    rag_better = v.get("rag_wins", 0) + v.get("rag_filter_wins", 0) \
        + v.get("rag_rewrite_wins", 0)
    plain_better = v.get("plain_wins", 0)
    if rag_better > plain_better:
        lines.append(f"RAG-режимы лучше plain: {rag_better} vs "
                     f"{plain_better} побед judge.")
    elif plain_better > rag_better:
        lines.append(f"Judge чаще выбирал plain ({plain_better} vs "
                     f"{rag_better}) — честный негативный результат.")
    else:
        lines.append("Judge не увидел преимущества RAG над plain.")
    # filter vs rag
    fr = summ["rag_filter"]
    rr = summ["rag"]
    if fr["avg_score"] is not None and rr["avg_score"] is not None:
        d = fr["avg_score"] - rr["avg_score"]
        sign = "+" if d >= 0 else ""
        lines.append(
            f"Фильтр (min_score={MIN_SCORE_OVERRIDE}) vs RAG: avg score "
            f"{fr['avg_score']} vs {rr['avg_score']} ({sign}{d:.2f}); "
            f"факты {_pct(fr['facts_ok'], fr['facts_total'])} vs "
            f"{_pct(rr['facts_ok'], rr['facts_total'])}.")
    # rewrite vs rag
    wr = summ["rag_rewrite"]
    if wr["avg_score"] is not None and rr["avg_score"] is not None:
        d = wr["avg_score"] - rr["avg_score"]
        sign = "+" if d >= 0 else ""
        lines.append(
            f"Rewrite vs RAG (применён в {summ['rewrite_applied']} из "
            f"{sum(1 for r in records if r.get('rewrite_applied') is not None)} "
            f"случаях): avg score {wr['avg_score']} vs {rr['avg_score']} "
            f"({sign}{d:.2f}); факты {_pct(wr['facts_ok'], wr['facts_total'])} "
            f"vs {_pct(rr['facts_ok'], rr['facts_total'])}.")
    if v.get("rag_filter_wins"):
        lines.append("Вывод: фильтр дал измеримый выигрыш "
                     f"({v['rag_filter_wins']} побед).")
    elif not v.get("rag_wins") and fr["avg_score"] and rr["avg_score"] \
            and abs(fr["avg_score"] - rr["avg_score"]) < 0.5:
        lines.append("Вывод: существенных различий между режимами нет.")
    return lines


def write_report(status: str, skip_reason: str = None,
                 fail_reason: str = None, settings_echo: dict = None,
                 records: list = None, stats: dict = None,
                 duration_s: float = 0.0) -> bool:
    """compare.json + report.md в EVIDENCE_DIR. -> успех записи."""
    ok = True
    os.makedirs(EVIDENCE_DIR, exist_ok=True)
    summ = _summarize(records or [])
    compare = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "status": status,
        "skip_reason": skip_reason,
        "fail_reason": fail_reason,
        "min_score_body": MIN_SCORE_OVERRIDE if status == "OK" else None,
        "duration_s": round(duration_s, 1),
        "index_stats": stats,
        "settings_echo": settings_echo,
        "summary": summ,
        "records": records or [],
    }
    try:
        with open(os.path.join(EVIDENCE_DIR, "compare.json"), "w",
                  encoding="utf-8") as f:
            json.dump(compare, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"CRITICAL: write compare.json: {e}")
        ok = False
    lines = [
        "# Day 23: сравнение 4 режимов RAG (plain / rag / rag+filter / "
        "rag+rewrite)",
        "",
        f"- **Статус**: {status}"
        + (f" — {skip_reason or fail_reason}" if status != "OK" else ""),
        f"- **Дата**: {compare['generated_at']}  |  длительность: "
        f"{duration_s:.0f}s",
    ]
    if status == "OK":
        lines += [
            f"- **Индекс**: {stats}",
            f"- **Settings echo**: `{json.dumps(settings_echo, ensure_ascii=False)}`",
            f"- **Важно**: во всех вызовах /api/rag/compare передавался "
            f"`min_score={MIN_SCORE_OVERRIDE}` в теле — переопределяет "
            f"настройку KB (по умолчанию 0.0) на время прогона. Настройка "
            f"KB в интерфейсе остаётся 0.0.",
            f"- **Judge**: {summ['judged']}/{len(records)} вопросов "
            f"оценены LLM.",
            "",
            "| # | Вопрос | plain | rag | rag+filter | rag+rewrite | Judge |",
            "|---|--------|-------|-----|------------|-------------|-------|",
        ]
        for rec in records:
            j = rec.get("judge") or {}
            verdict = j.get("verdict", "—")
            lines.append(
                f"| {rec['id']} | {_excerpt(rec['question'], 50)} "
                f"| {_mode_cell(rec, 'plain')} | {_mode_cell(rec, 'rag')} "
                f"| {_mode_cell(rec, 'rag_filter')} "
                f"| {_mode_cell(rec, 'rag_rewrite')} | {verdict} |")
        lines += ["", "## Win-rate / агрегаты", ""]
        for mode in MODES:
            s = summ[mode]
            lines.append(
                f"- **{mode}**: факты {s['facts_ok']}/{s['facts_total']} "
                f"({_pct(s['facts_ok'], s['facts_total'])}), avg score "
                f"{s['avg_score']} (n={s['n_scored']})")
        lines.append(
            f"- **sources_ok**: {summ['sources_ok']['ok']}/"
            f"{summ['sources_ok']['total']}")
        lines.append(
            f"- **rewrite_applied**: {summ['rewrite_applied']}")
        lines += ["", "## Rewrite (переписанные запросы)", ""]
        for rec in records:
            if rec.get("rewritten_query") and \
                    rec["rewritten_query"] != rec["question"]:
                lines.append(
                    f"- Q{rec['id']}: `{_excerpt(rec['rewritten_query'], 80)}`")
        if summ["rewrite_applied"] == 0:
            lines.append("- Ни один запрос не был переписан "
                         "(rewrite_applied=False везде).")
        lines += ["", "## Вывод (честный)", ""]
        lines += build_conclusion(records or [], summ, stats, duration_s)
    lines += ["", f"Compare JSON: `{os.path.join(EVIDENCE_DIR, 'compare.json')}`",
              f"Сервер-лог: `{SERVER_LOG}`", ""]
    try:
        with open(os.path.join(EVIDENCE_DIR, "report.md"), "w",
                  encoding="utf-8") as f:
            f.write("\n".join(lines))
    except Exception as e:
        log(f"CRITICAL: write report.md: {e}")
        ok = False
    return ok


def main() -> int:
    t0 = time.time()
    wait_port_free()
    load_dotenv()
    os.makedirs(EVIDENCE_DIR, exist_ok=True)
    proc = start_server()
    if proc is None:
        log("CRITICAL: сервер не поднялся")
        write_report("FAIL", fail_reason="сервер не поднялся")
        return 1
    try:
        skip = probe_gpustack()
        if skip:
            log(f"SKIP: {skip}")
            write_report("SKIP", skip_reason=skip)
            return 0
        code, raw, _ = http("DELETE", "/api/kb", timeout=30)
        log(f"сброс базы: {code}")
        upload_corpus(prepare_corpus())
        stats = build_index()
        settings = {"embedder": "api", "reranker": "api",
                    "rag_recall": 50, "rag_top_k": 3, "min_score": 0.0}
        code, raw, _ = http("POST", "/api/kb/settings", body=settings,
                            timeout=30)
        if code != 200:
            raise RuntimeError(f"settings: HTTP {code}: "
                               f"{raw.decode('utf-8', 'replace')[:200]}")
        kb_code, kb_raw, _ = http("GET", "/api/kb/settings", timeout=30)
        kbset = json.loads(kb_raw.decode("utf-8", "replace")) \
            if kb_code == 200 else {}
        cfg_code, cfg_raw, _ = http("GET", "/api/config", timeout=30)
        cfg = json.loads(cfg_raw.decode("utf-8", "replace")) \
            if cfg_code == 200 else {}
        model = cfg.get("chat_model") or cfg.get("model") or "default"
        echo = dict(settings)
        echo["min_score_body"] = MIN_SCORE_OVERRIDE
        echo["kbset"] = kbset
        echo["model"] = model
        records = run_questions(model, QUESTIONS)
        duration = time.time() - t0
        write_report("OK", settings_echo=echo, records=records,
                     stats=stats, duration_s=duration)
        log(f"готово за {duration:.0f}s -> {EVIDENCE_DIR}")
        return 0
    except Exception as e:
        log(f"CRITICAL: {e}")
        write_report("FAIL", fail_reason=f"{e.__class__.__name__}: {e}",
                     duration_s=time.time() - t0)
        return 1
    finally:
        stop_server(proc)


if __name__ == "__main__":
    sys.exit(main())
