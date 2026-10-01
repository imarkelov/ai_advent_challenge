# -*- coding: utf-8 -*-
r"""День 22 (RAG): скрипт сравнения «RAG vs без RAG» — 10 контрольных
вопросов, детерминированный факт-чек + LLM-judge, отчёт ВСЕГДА пишется.

Сценарий (полностью автономный):
  1. Инфраструктура (HARD):
     - порт 8106: busy → ожидание до 10 мин → SKIP (exit 0);
     - uvicorn subprocess (127.0.0.1:8106, -X utf8, PYTHONUTF8=1,
       логи → %TEMP%\opencode\compare_day22_server.log);
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
       rag_recall:50, rag_top_k:3} — ЯВЛЮ, не «как есть».
  2. Ран (HARD: завершённость): control_questions.json (10 вопросов);
     вопрос с отсутствующим expected_source → skipped: "source
     unavailable" (не фейлит); POST /api/rag/compare на вопрос;
     ошибка одного вопроса → error в записи, продолжаем.
  3. Оценка (SOFT — записывается, никогда не фейлит):
     - факт-чек: нормализованное (lower, collapse spaces, ё→е)
       подстроковое совпадение каждого expect_fact в answer_rag /
       answer_plain;
     - sources_ok: set(chunks[].file) ∩ expected_sources ≠ ∅
       (chunk file несёт префикс "uploads/" — снимаем до сравнения);
     - LLM-judge (паттерн benchmark.py): один вызов на вопрос (оба
       ответа + expect_facts, T=0, thinking off, strict JSON
       {"score":1-10,"verdict","reason"}; verdict: rag_wins|
       plain_wins|tie|inconclusive); сбой judge → judge: null.
  4. Отчёт (try/finally — ВСЕГДА, даже при крахе):
     .omo/evidence/day22-rag-compare/compare.json + report.md
     (таблица 10 строк, settings-эхо, ЧЕСТНЫЙ итог-абзац — RAG может
     проиграть, пишем как есть).
  5. Exit 0 — PASS или SKIP; exit 1 — инфраструктурный FAIL (сервер не
     поднялся, индекс не собрался, отчёт не записан).

Только stdlib (urllib — паттерн e2e_day21.py).
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
PORT = 8106  # 8100 — e2e_studio.py, 8101-8105 — e2e дней 17-21
BASE = f"http://{HOST}:{PORT}"
EVIDENCE_DIR = os.path.join(REPO, ".omo", "evidence", "day22-rag-compare")
SERVER_LOG = os.path.join(tempfile.gettempdir(), "opencode",
                          "compare_day22_server.log")
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
COMPARE_TIMEOUT = 300      # на вопрос: 2 LLM-вызова + retrieval
JUDGE_TIMEOUT = 120
REUSE_MIN_SIZE = 50 * 1024  # файл книги ≥50KB — переиспользуем, не качаем
JUDGE_VERDICTS = ("rag_wins", "plain_wins", "tie", "inconclusive")

# Windows-консоль cp1251: печатать UTF-8, errors="replace" — print
# никогда не роняет скрипт (паттерн дня 18 / e2e_day21).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def log(msg: str) -> None:
    print(f"[compare-day22] {msg}", flush=True)


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


# Локальный сервер (127.0.0.1) — ТОЛЬКО напрямую, без прокси: в net-cut
# тесте (мёртвый прокси-окружение) HTTP_PROXY/HTTPS_PROXY ведут даже
# localhost-запросы в тупик — polling сервера сломался бы.
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
    boundary = "----cmpday22" + uuid.uuid4().hex
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
    """Копия валидных (≥REUSE_MIN_SIZE) книг из текущих uploads — чтобы
    после wipe не перескачивать то, что уже есть (сеть = риск/время)."""
    if not os.path.isdir(UPLOADS_DIR):
        return
    for name in os.listdir(UPLOADS_DIR):
        path = os.path.join(UPLOADS_DIR, name)
        if os.path.isfile(path) and os.path.getsize(path) >= REUSE_MIN_SIZE:
            shutil.copy2(path, os.path.join(staging, name))
            log(f"корпус: переиспользую {name} "
                f"({os.path.getsize(path)} B) — без скачивания")


def prepare_corpus() -> list:
    """5 книг каталога fetch_books в data/kb/uploads: staging-копия →
    fetch_books.resolve_book (urls → fallback → seed). Возвращает
    список (name, status)."""
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import fetch_books
    os.makedirs(UPLOADS_DIR, exist_ok=True)
    staging = os.path.join(tempfile.gettempdir(), "opencode",
                           "compare_day22_staging")
    shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(staging, exist_ok=True)
    snapshot_books(staging)
    out = []
    for entry in fetch_books.CATALOG:
        name = entry["filename"]
        dest = os.path.join(UPLOADS_DIR, name)
        snap = os.path.join(staging, name)
        if os.path.exists(snap):
            shutil.copy2(snap, dest)
            status = "reused"
        else:
            res = fetch_books.resolve_book(entry, "")
            if res["text"] is not None:
                fetch_books.atomic_write(dest, res["text"].encode("utf-8"))
                status = res["status"]
            else:
                status = "failed"
        out.append((name, status))
        log(f"корпус: {name:28s} {status}")
    shutil.rmtree(staging, ignore_errors=True)
    return out


def upload_corpus(corpus: list) -> None:
    """Multipart-upload всех книг + egg_book.txt (fixture). 200 или raise."""
    files = [name for name, status in corpus if status != "failed"]
    files.append("egg_book.txt")
    for name in files:
        path = (EGG_BOOK if name == "egg_book.txt"
                else os.path.join(UPLOADS_DIR, name))
        if not os.path.exists(path):
            raise RuntimeError(f"файл корпуса не найден: {name}")
        with open(path, "rb") as f:
            content = f.read()
        code, body, _ = http_multipart("/api/kb/upload", name, content)
        if code != 200:
            raise RuntimeError(
                f"upload {name}: HTTP {code} "
                f"{body.decode('utf-8', 'replace')[:160]}")
        log(f"upload: {name} ({len(content)} B) OK")


def build_index() -> None:
    """POST /api/kb/index (structural, api) в фоновом тред (синхронный
    тяжёлый вызов) + poll /api/kb/build-status (1.5s, до 30 мин).
    Успех — GET /api/kb/stats отдаёт 200 (exists). Иначе raise."""
    state = {}

    def _do() -> None:
        try:
            code, body, _ = http("POST", "/api/kb/index",
                                 {"strategy": "structural",
                                  "embedder": "api"},
                                 timeout=INDEX_TIMEOUT)
            state["code"] = code
            state["body"] = body
        except Exception as e:  # noqa: BLE001 — фиксируем, решение позже
            state["exc"] = repr(e)

    t = threading.Thread(target=_do, daemon=True)
    t.start()
    deadline = time.time() + INDEX_TIMEOUT
    log(f"сборка индекса (structural, api-эмбеддинги) — poll до "
        f"{INDEX_TIMEOUT // 60} мин…")
    t0 = time.time()
    while True:
        time.sleep(INDEX_POLL_STEP)
        if not t.is_alive():
            if state.get("code") == 200:
                code2, _, _ = http("GET", "/api/kb/stats", timeout=30)
                if code2 == 200:
                    log(f"индекс собран за {time.time() - t0:.0f}s")
                    return
            detail = (state.get("body") or b"").decode(
                "utf-8", "replace")[:200]
            raise RuntimeError(
                f"POST /api/kb/index: code={state.get('code')} "
                f"exc={state.get('exc', '-')} {detail}")
        code, body, _ = http("GET", "/api/kb/build-status", timeout=15)
        st = json.loads(body) if code == 200 else {}
        if not st.get("running"):
            code2, _, _ = http("GET", "/api/kb/stats", timeout=30)
            if code2 == 200:
                log(f"индекс собран за {time.time() - t0:.0f}s")
                return
        if time.time() > deadline:
            raise RuntimeError(
                f"индекс не собрался за {INDEX_TIMEOUT // 60} мин")


# ---------- оценка ----------

def norm(s) -> str:
    """Нормализация для факт-чека (BM25-паттерн T2): lower, ё→е,
    collapse spaces."""
    if not isinstance(s, str):
        return ""
    return re.sub(r"\s+", " ", s.lower().replace("ё", "е")).strip()


def check_facts(expect: list, answer) -> dict:
    a = norm(answer)
    return {f: norm(f) in a for f in expect}


def chunk_files(chunks: list) -> set:
    """Файлы чанков БЕЗ префикса 'uploads/' (chunk file =
    'uploads/<name>', expected_sources — голые имена)."""
    out = set()
    for c in chunks or []:
        f = c.get("file") or ""
        if f.startswith("uploads/"):
            f = f[len("uploads/"):]
        if f:
            out.add(f)
    return out


def judge_question(model: str, question: str, expect: list,
                   answer_plain, answer_rag):
    """Один judge-вызов на вопрос (паттерн benchmark.py): T=0, thinking
    off, strict JSON. Сбой (сеть/API/парс) → None, warning."""
    base = os.environ.get("GPUSTACK_BASE_URL", "").strip().rstrip("/")
    key = os.environ.get("GPUSTACK_API_KEY", "").strip()
    if not base or not key or not model:
        log("judge: нет base/key/model → null")
        return None
    facts = "\n".join(f"- {f}" for f in expect)
    user = (
        "На вопрос ассистента отвечали дважды: БЕЗ RAG (Ответ A) и С RAG — "
        "с выдержками из базы знаний в system-промпте (Ответ B).\n\n"
        f"Вопрос: {question}\n\n"
        f"Ожидаемые факты (хороший ответ должен их содержать):\n{facts}\n\n"
        f"Ответ A (без RAG):\n{answer_plain}\n\n"
        f"Ответ B (с RAG):\n{answer_rag}\n\n"
        "Оцени Ответ B (с RAG): score — целое число 1-10, насколько Ответ B "
        "покрывает ожидаемые факты (10 — все факты точно присутствуют, "
        "1 — ни одного).\n"
        "Определи, какой ответ лучше для вопроса: verdict — одно из: "
        '"rag_wins" (B лучше), "plain_wins" (A лучше), "tie" (одинаково '
        'хороши или одинаково плохи), "inconclusive" (оценить невозможно, '
        "например в ответе ошибка).\n"
        'Ответ — СТРОГО один JSON-объект: {"score": 1-10, "verdict": "...", '
        '"reason": "..."}; reason — краткое обоснование на русском.'
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system",
             "content": "Ты — независимый LLM-судья сравнения RAG. "
                        "Отвечаешь СТРОГО одним JSON-объектом."},
            {"role": "user", "content": user},
        ],
        "temperature": 0,
        "max_tokens": 300,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=JUDGE_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        content = json.loads(raw)["choices"][0]["message"].get("content")
        m = re.search(r"\{.*\}", content or "", re.S)
        if not m:
            raise ValueError(f"JSON не найден в ответе judge: "
                             f"{(content or '')[:160]!r}")
        data = json.loads(m.group(0))
        score, verdict, reason = data["score"], data["verdict"], data["reason"]
        if isinstance(score, float) and score.is_integer():
            score = int(score)
        if not isinstance(score, int) or not (1 <= score <= 10):
            raise ValueError(f"score вне диапазона 1-10: {score!r}")
        if not isinstance(verdict, str) or not isinstance(reason, str):
            raise ValueError("verdict/reason должны быть строками")
        out = {"score": score, "verdict": verdict, "reason": reason}
        if verdict not in JUDGE_VERDICTS:
            out["raw_verdict"] = verdict
            out["verdict"] = "inconclusive"
        return out
    except Exception as e:  # noqa: BLE001 — judge сбой = null, не падение
        log(f"judge: не удался (вопрос): {e.__class__.__name__}: {e}")
        return None


# ---------- отчёт ----------

def _facts_cell(facts, side: str) -> str:
    if not facts:
        return "—"
    vals = [v[side] for v in facts.values()]
    got = sum(1 for v in vals if v)
    if not vals:
        return "—"
    mark = "✓" if got == len(vals) else "✗"
    return f"{mark} ({got}/{len(vals)})"


def _pct(hits: int, total: int) -> str:
    return f"{100 * hits / total:.0f}%" if total else "н/д"


def build_conclusion(status: str, summary: dict) -> str:
    if status == "skip":
        return ("Ран пропущен (SKIP): "
                + (summary.get("skip_reason") or "см. summary")
                + ". Сравнение не проводилось — инфраструктура или "
                  "GPustack недоступны, корпус не строился.")
    comp = summary.get("completed", 0)
    if comp == 0:
        return ("Ни один вопрос не был выполнен (ошибки/скипы) — "
                "сравнивать нечего. См. поля error/skipped в записях "
                "compare.json.")
    rv = summary.get("judge_verdicts", {})
    rw = rv.get("rag_wins", 0)
    pw = rv.get("plain_wins", 0)
    tie = rv.get("tie", 0)
    inc = rv.get("inconclusive", 0)
    fr = summary.get("facts_rag_pass", 0)
    fp = summary.get("facts_plain_pass", 0)
    fh_r = summary.get("facts_rag_hits", 0)
    fh_p = summary.get("facts_plain_hits", 0)
    sr = summary.get("facts_rag_total", 0)
    sp = summary.get("facts_plain_total", 0)
    parts = [
        f"Выполнено {comp}/{summary.get('total', 0)} вопросов "
        f"(ошибок: {summary.get('errors', 0)}, пропущено: "
        f"{summary.get('skipped', 0)}).",
        f"Факт-чек (детерминированный): RAG-ответ полностью покрывает "
        f"ожидаемые факты в {fr}/{comp} вопросов (совпадений {fh_r} из "
        f"{sr}, {_pct(fh_r, sr)}); plain-ответ — в {fp}/{comp} "
        f"(совпадений {fh_p} из {sp}, {_pct(fh_p, sp)}).",
        f"Источники (chunks ∩ expected_sources): "
        f"{summary.get('sources_ok_count', 0)}/{comp}.",
    ]
    if rw + pw + tie + inc:
        parts.append(
            f"LLM-judge: rag_wins={rw}, plain_wins={pw}, tie={tie}, "
            f"inconclusive={inc}.")
        if rw > pw:
            parts.append(
                "По вердиктам судьи RAG-ответ чаще лучше plain — база "
                "знаний дала выигрыш на этом наборе вопросов.")
        elif pw > rw:
            parts.append(
                "Честный результат: plain-ответ чаще лучше RAG — на "
                "этом наборе вопросов база знаний НЕ дала выигрыша "
                "(возможные причины: retrieval не вытащил нужный чанк в "
                "top-k, фокус-окно выдержки, поведение модели).")
        else:
            parts.append(
                "По вердиктам судьи равновесие — различие между RAG и "
                "plain на этом наборе вопросов не выражено.")
    if summary.get("errors", 0):
        parts.append(
            f"В {summary.get('errors', 0)} записях есть error (сбой "
            "отдельного вопроса) — они учтены в отчёте, но не влияют на "
            "остальные.")
    return " ".join(parts)


def _summarize(questions: list) -> dict:
    completed = sum(1 for q in questions
                    if q["error"] is None and q["skipped"] is None)
    errors = sum(1 for q in questions if q["error"] is not None)
    skipped = sum(1 for q in questions if q["skipped"] is not None)
    frp = fpp = frh = fph = frt = fpt = 0
    soc = 0
    verdicts = {}
    for q in questions:
        f = q.get("facts")
        if f:
            frp += 1 if all(v["rag"] for v in f.values()) else 0
            fpp += 1 if all(v["plain"] for v in f.values()) else 0
            frh += sum(1 for v in f.values() if v["rag"])
            fph += sum(1 for v in f.values() if v["plain"])
            frt += len(f)
            fpt += len(f)
        if q.get("sources_ok") is True:
            soc += 1
        j = q.get("judge")
        if j:
            verdicts[j["verdict"]] = verdicts.get(j["verdict"], 0) + 1
    return {
        "total": len(questions),
        "completed": completed,
        "errors": errors,
        "skipped": skipped,
        "facts_rag_pass": frp,
        "facts_plain_pass": fpp,
        "facts_rag_hits": frh,
        "facts_plain_hits": fph,
        "facts_rag_total": frt,
        "facts_plain_total": fpt,
        "facts_total": frt,
        "sources_ok_count": soc,
        "judge_verdicts": verdicts,
    }


def write_report(status: str, skip_reason, fail_reason,
                 settings_echo, questions: list) -> bool:
    """compare.json + report.md. True — записано, False — нет."""
    summary = _summarize(questions)
    summary["skip_reason"] = skip_reason
    summary["fail_reason"] = fail_reason
    compare = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "skip_reason": skip_reason,
        "fail_reason": fail_reason,
        "settings_echo": settings_echo,
        "questions": questions,
        "summary": summary,
    }
    try:
        os.makedirs(EVIDENCE_DIR, exist_ok=True)
        with open(os.path.join(EVIDENCE_DIR, "compare.json"), "w",
                  encoding="utf-8") as f:
            json.dump(compare, f, ensure_ascii=False, indent=2)

        lines = ["# День 22: сравнение RAG vs без RAG (10 контрольных вопросов)",
                 "",
                 f"- Статус: **{status.upper()}**"
                 + (f" — {skip_reason}" if skip_reason else "")
                 + (f" — {fail_reason}" if fail_reason else ""),
                 f"- Дата (UTC): {compare['generated_at']}",
                 "",
                 "## Настройки (settings_echo)",
                 ""]
        if settings_echo:
            lines += [
                "| Параметр | Значение |",
                "| --- | --- |",
                f"| model | {settings_echo.get('model')} |",
                f"| embedder | {settings_echo.get('embedder')} |",
                f"| reranker | {settings_echo.get('reranker')} |",
                f"| rag_recall | {settings_echo.get('rag_recall')} |",
                f"| rag_top_k | {settings_echo.get('rag_top_k')} |",
                f"| temperature | {settings_echo.get('temperature')} |",
                f"| max_tokens | {settings_echo.get('max_tokens')} |",
                f"| corpus_files | "
                f"{', '.join(settings_echo.get('corpus_files', []))} |",
                "",
            ]
        else:
            lines += ["Настройки не читались (ран не дошёл до сервера "
                      "или был пропущен).", ""]
        lines += ["## Вопросы", "",
                  "| # | Вопрос | Факты RAG | Факты plain | Sources | Judge |",
                  "| --- | --- | --- | --- | --- | --- |"]
        for q in questions:
            qtext = q["question"].replace("|", "\\|")
            if len(qtext) > 60:
                qtext = qtext[:57] + "…"
            if q["skipped"]:
                row = (f"| {q['id']} | {qtext} | — | — | — | — "
                       f"(skipped: {q['skipped']}) |")
            elif q["error"]:
                err = q["error"][:40].replace("|", "\\|")
                row = (f"| {q['id']} | {qtext} | — | — | — | — "
                       f"(error: {err}) |")
            else:
                j = q.get("judge")
                jcell = f"{j['score']} {j['verdict']}" if j else "—"
                s = "✓" if q.get("sources_ok") else "✗"
                row = (f"| {q['id']} | {qtext} | "
                       f"{_facts_cell(q.get('facts'), 'rag')} | "
                       f"{_facts_cell(q.get('facts'), 'plain')} | {s} | "
                       f"{jcell} |")
            lines.append(row)
        verdicts = summary["judge_verdicts"]
        lines += ["", "## Итог", "",
                  f"- Выполнено: {summary['completed']}/{summary['total']}; "
                  f"ошибок: {summary['errors']}; "
                  f"пропущено: {summary['skipped']}",
                  f"- Факт-чек (все факты вопроса найдены): RAG — "
                  f"{summary['facts_rag_pass']}/{summary['completed']}; "
                  f"plain — {summary['facts_plain_pass']}/{summary['completed']}",
                  f"- Совпадений фактов: RAG {summary['facts_rag_hits']}/"
                  f"{summary['facts_rag_total']}; plain "
                  f"{summary['facts_plain_hits']}/{summary['facts_plain_total']}",
                  f"- Sources OK (chunks ∩ expected_sources): "
                  f"{summary['sources_ok_count']}",
                  "- Judge: " + (", ".join(f"{k}={v}" for k, v in
                                            sorted(verdicts.items()))
                                 or "—"),
                  "",
                  "## Заключение",
                  "",
                  build_conclusion(status, summary),
                  ""]
        with open(os.path.join(EVIDENCE_DIR, "report.md"), "w",
                  encoding="utf-8") as f:
            f.write("\n".join(lines))
        log(f"отчёт записан: {EVIDENCE_DIR}/compare.json + report.md")
        return True
    except Exception as e:  # noqa: BLE001 — «отчёт не записан» = FAIL
        log(f"CRITICAL: отчёт не записан: {e!r}")
        return False


# ---------- ран ----------

def new_record(q: dict) -> dict:
    return {
        "id": q["id"],
        "question": q["question"],
        "expect_facts": q["expect_facts"],
        "expected_sources": q["expected_sources"],
        "skipped": None,
        "error": None,
        "answer_rag": None,
        "answer_plain": None,
        "chunks": [],
        "facts": None,
        "sources_ok": None,
        "judge": None,
    }


def run_questions(questions: list, records: list, model: str) -> None:
    code, body, _ = http("GET", "/api/kb/uploads", timeout=30)
    if code != 200:
        raise RuntimeError(f"GET /api/kb/uploads: HTTP {code}")
    available = {u["name"] for u in json.loads(body).get("uploads", [])}
    log(f"доступные источники ({len(available)}): {sorted(available)}")
    for q, rec in zip(questions, records):
        missing = [s for s in q["expected_sources"] if s not in available]
        if missing:
            rec["skipped"] = "source unavailable: " + ", ".join(missing)
            log(f"Q{q['id']}: SKIP — source unavailable: {missing}")
            continue
        log(f"Q{q['id']}: POST /api/rag/compare …")
        t0 = time.time()
        try:
            code, body, _ = http("POST", "/api/rag/compare",
                                 {"question": q["question"]},
                                 timeout=COMPARE_TIMEOUT)
        except Exception as e:  # noqa: BLE001 — изоляция: continue
            rec["error"] = f"{e.__class__.__name__}: {e}"
            log(f"Q{q['id']}: ERROR — {rec['error']}")
            continue
        if code != 200:
            rec["error"] = (f"HTTP {code}: "
                            f"{body.decode('utf-8', 'replace')[:200]}")
            log(f"Q{q['id']}: ERROR — {rec['error']}")
            continue
        data = json.loads(body)
        rec["answer_rag"] = data.get("answer_rag")
        rec["answer_plain"] = data.get("answer_plain")
        rec["chunks"] = data.get("chunks") or []
        # SOFT-оценка (никогда не фейлит ран)
        rec["facts"] = {
            f: {"rag": check_facts([f], rec["answer_rag"])[f],
                "plain": check_facts([f], rec["answer_plain"])[f]}
            for f in q["expect_facts"]
        }
        rec["sources_ok"] = bool(
            chunk_files(rec["chunks"]) & set(q["expected_sources"]))
        rec["judge"] = judge_question(
            model, q["question"], q["expect_facts"],
            rec["answer_plain"], rec["answer_rag"])
        log(f"Q{q['id']}: done за {time.time() - t0:.0f}s "
            f"(sources_ok={rec['sources_ok']}, "
            f"judge={rec['judge']['verdict'] if rec['judge'] else '—'})")


def main() -> int:
    load_dotenv()
    try:
        with open(QUESTIONS_FILE, encoding="utf-8-sig") as f:
            questions = json.load(f)
    except Exception as e:
        log(f"CRITICAL: не удалось загрузить control_questions.json: {e!r}")
        write_report("fail", None, f"control_questions.json: {e!r}", None, [])
        return 1
    if not isinstance(questions, list) or not questions:
        log("CRITICAL: control_questions.json — пустой или не список")
        write_report("fail", None, "control_questions.json: empty", None, [])
        return 1
    records = [new_record(q) for q in questions]
    proc = None
    status, skip_reason, fail_reason = "pass", None, None
    settings_echo = None
    rc = 0
    try:
        busy = wait_port_free()
        if busy:
            status, skip_reason = "skip", busy
            for r in records:
                r["skipped"] = f"run skipped: {busy}"
            log(f"SKIP: {busy}")
        else:
            proc = start_server()
            if proc is None:
                status, fail_reason = "fail", \
                    "uvicorn не поднялся на порту 8106"
                for r in records:
                    r["error"] = f"run failed before question: {fail_reason}"
                log(f"FAIL: {fail_reason}")
                rc = 1
            else:
                log("uvicorn up (порт 8106)")
                reason = probe_gpustack()
                if reason:
                    status, skip_reason = "skip", reason
                    for r in records:
                        r["skipped"] = f"run skipped: {reason}"
                    log(f"SKIP: {reason}")
                else:
                    log("GPustack up")
                    # корпус: wipe → книги (fetch_books) → upload →
                    # индекс → settings → 10 вопросов
                    code, body, _ = http("DELETE", "/api/kb", timeout=60)
                    if code != 200:
                        raise RuntimeError(
                            f"DELETE /api/kb: HTTP {code} "
                            f"{body.decode('utf-8', 'replace')[:160]}")
                    log("wipe: DELETE /api/kb OK")
                    corpus = prepare_corpus()
                    upload_corpus(corpus)

                    build_index()

                    code, body, _ = http("POST", "/api/kb/settings",
                                         {"embedder": "api",
                                          "reranker": "api",
                                          "rag_recall": 50,
                                          "rag_top_k": 3},
                                         timeout=30)
                    if code != 200:
                        raise RuntimeError(
                            f"POST /api/kb/settings: HTTP {code} "
                            f"{body.decode('utf-8', 'replace')[:160]}")
                    log("settings: embedder=api, reranker=api, "
                        "rag_recall=50, rag_top_k=3")

                    code, body, _ = http("GET", "/api/config", timeout=30)
                    cfg = json.loads(body) if code == 200 else {}
                    code, body, _ = http("GET", "/api/kb/settings",
                                         timeout=30)
                    kbset = json.loads(body) if code == 200 else {}
                    code, body, _ = http("GET", "/api/kb/stats", timeout=30)
                    stats = json.loads(body) if code == 200 else {}
                    settings_echo = {
                        "model": cfg.get("model"),
                        "embedder": kbset.get("embedder"),
                        "reranker": kbset.get("reranker"),
                        "rag_recall": kbset.get("rag_recall"),
                        "rag_top_k": kbset.get("rag_top_k"),
                        "temperature": 0,   # pin rag_compare (T4)
                        "max_tokens": 1024,  # pin rag_compare (T4)
                        "corpus_files": stats.get("files", []),
                    }
                    log(f"settings_echo: model={settings_echo['model']} "
                        f"embedder={settings_echo['embedder']} "
                        f"reranker={settings_echo['reranker']} "
                        f"corpus_files="
                        f"{len(settings_echo['corpus_files'])}")

                    run_questions(questions, records,
                                  settings_echo.get("model") or "")
                    rc = 0
    except Exception as e:  # noqa: BLE001 — инфраструктурный сбой
        status, fail_reason = "fail", f"{e.__class__.__name__}: {e}"
        for r in records:
            if r["error"] is None and r["skipped"] is None:
                r["error"] = f"run failed before question: {fail_reason}"
        log(f"FAIL: {fail_reason}")
        rc = 1
    finally:
        stop_server(proc)
        if not write_report(status, skip_reason, fail_reason,
                            settings_echo, records):
            # «отчёт не записан» = FAIL даже если ран прошёл
            if status != "fail":
                status, fail_reason = "fail", "отчёт не записан"
                log(f"FAIL: {fail_reason}")
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
