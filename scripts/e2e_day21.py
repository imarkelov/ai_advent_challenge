"""E2E день 21 «База знаний: документный RAG (chunking + embeddings + индекс)»
— гибрид: детерминированное ядро + live.

Part A (MUST PASS, офлайн, без uvicorn и без сети):
   1. sys.path → studio/backend (+ tests/ для make_fake_launcher);
      KnowledgeBase + чанкеры + HashEmbedder (tmp-каталог БЗ + загрузки-
      фикстуры в uploads; доки «мини-репозитория» в корпус НЕ входят —
      корпус uploads-only, только «+ Добавить файл»).
   2. Ассерты (каждый — record):
      A1  corpus_files: ТОЛЬКО загрузка (uploads/music.md +
          uploads/weather.md, source "upload"); доки репозитория
          (README/docs/superpowers) в корпусе НЕТ;
      A2  FixedChunker: размеры/перекрытие на синтетическом тексте
          (1000/200 → чанки 1000/1000/хвост, хвост N = голова N+1);
      A3  StructuredChunker: md — секции по заголовкам, без заголовков —
          "whole file", не-md — весь файл одним чанком;
       A4  build("structural", HashEmbedder): index.db (SQLite) на
           диске, chunk_id *structural-*, comparison {fixed,
           structural} с hit_at_3/precision_at_3/mrr, stats.chunks > 0,
           dim 256;
      A4b  авто-миграция legacy index.json → index.db (json существует,
           db нет → первая сборка: json потреблён и удалён, db создан,
           поиск работает);
       A5  build("fixed", ...): индекс пересобран (стратегия flipped,
          старых chunk_id нет);
      A6  search: запрос с «ксилофон» → uploads/music.md первым;
     A7  settings: дефолты + update валидация (rag_top_k=11 → ValueError);
      A8  TestClient(create_app(agent MockTransport, kb tmp)):
          /api/kb/stats 404 без индекса → POST /api/kb/index (structural,
          hash) 200 → stats exists:true; settings rag_top_k=11 → 400;
          upload .txt → 200 + файл на диске, .exe → 400;
      A8b инкрементальная сборка + build-status: pending_files после
          upload, повторный index (mode auto) → incremental, added=1,
          чанки растут, pending пуст; GET /api/kb/build-status —
          running:False, phase:done, total>0;
       A8c удаление файла + очистка базы: DELETE /api/kb/uploads/{name}
           (файл с диска + чанки из индекса + stats, повторно → 404),
           DELETE /api/kb (uploads + index.db, settings сохраняются);
      A8d гибридный поиск (вектор + BM25, RRF): в «книгу» спрятан
          редкий токен, вопрос в другой форме («телефона» vs «телефон»)
          → BM25 substring-матч, чанк-пасхалка top-1;
     A9  agent_loop=false + подключённый fake MCP (make_fake_launcher):
         tools в LLM-пейлоаде НЕТ (1 LLM-вызов); agent_loop=true →
         tools ЕСТЬ (*__mock_echo);
     A10 RAG on + hash-индекс: system-промпт содержит «База знаний» и
         «ксилофон»; rag off → блока нет.
     Сеть отрезана на всё Part A proxy-переменными _NO_NET (127.0.0.1:1) —
     ни одна точка не может дойти до реального API; env восстанавливается
     до Part B.
  3. finally: восстановление env, close_all, удаление tmp.

Part B — live (uvicorn :8105, реальный GPustack LLM + реальные эмбеддинги,
best-effort):
  1. Порт 8105: занят — ждать до 10 минут (чужой сервер НЕ убивается),
     всё ещё занят — SKIP (exit 0).
  2. Запуск `python -m uvicorn studio.backend.main:app --port 8105`
     detached из корня репозитория (готовность — GET /api/dialogues).
  3. probe_gpustack() — недостижим → SKIP (exit 0).
   4. B1: GET /api/models — LLM недоступен/моделей нет → SKIP остального.
   5. B1b: DELETE /api/kb (wipe) — чистое состояние: старые stale-чанки
      (доки репозитория из индекса до фикса) не попадают в ассерты;
      B1c: POST /api/kb/upload — загрузка «книги» с пасхалкой
      «IPhone 17Promax» (единственный источник корпуса: uploads-only).
   6. B2: POST /api/kb/index {structural, api} (timeout 900 — корпус =
      загруженная книга; может занять пару минут) → 200; 502/сеть —
      FAIL (инфраструктура). Индекс пишется в реальный data/kb — это
      артефакт дня, оставляется.
   7. B3: GET /api/kb/stats: exists, dim==4096 (qwen3-vl-embedding-8b),
      comparison, files; печатает сводку (chunks, dim, build_ms,
      corpus_words + метрики обеих стратегий).
   8. B4: GET /api/kb/search?q="модель телефона" → 200, results не
      пусты, top — загруженная книга (file + section).
   9. B5: RAG live: MCP connect (task_create, чтобы B6 не было vacuous),
      settings {rag: true, agent_loop: true}, диалог + decline,
      POST /api/chat (SSE) «Какая модель телефона была у героя?» →
      done; затем ДЕТЕРМИНИРОВАННЫЙ чек журнала (/api/requests + /{id}):
      system-промпт последнего запроса содержит блок «База знаний»
      (обязательное условие PASS; бессмысленный ответ модели — НЕ FAIL).
   10. B6: POST /api/kb/settings {agent_loop: false} → чат → в теле
      последнего LLM-запроса (журнал) КЛЮЧА tools НЕТ; восстановление
      {agent_loop: true}.
   11. cleanup (ВСЕГДА в finally): DELETE диалога, восстановление модели
       конфига, stop_server (taskkill /PID /T /F, проверка порта).
       data/kb (live-индекс) НЕ удаляем — артефакт дня.

Выход: PASS/FAIL на stdout; exit 0 для PASS и SKIP, 1 для FAIL.
Part A MUST PASS всегда (офлайн, детерминированное). Part B — PASS/SKIP.
"""
import http.client as _http_client
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
HOST = "127.0.0.1"
PORT = 8105  # 8100 — e2e_studio.py, 8101 — day17, 8102 — day18,
             # 8103 — day19, 8104 — day20
BASE = f"http://{HOST}:{PORT}"
WAIT_PORT_BUSY_MAX = 600   # 10 минут ожидания освобождения порта
WAIT_PORT_BUSY_STEP = 10
WAIT_SERVER_UP_MAX = 90
CHAT_TIMEOUT = 330
INDEX_TIMEOUT = 900        # live-сборка индекса по всему корпусу
# RAG-вопрос — на факт из загруженной «книги» (корпус uploads-only: доки
# репозитория в индекс не попадают, отвечать модели нечем было бы).
RAG_QUERY = ("Какая модель телефона была у героя? Ответь коротко.")
RAG_EGG_QUERY = "какая модель телефона была у героя"
# Part A «без сети» (паттерн e2e_day20 _NO_NET): мёртвый прокси — ни одна
# точка Part A (TestClient, fake MCP-тред, HashEmbedder) не дойдёт до
# реального API. Применяется на время Part A, восстанавливается до Part B
# (иначе uvicorn-процесс унаследовал бы мёртвый прокси).
_NO_NET = {"HTTP_PROXY": "http://127.0.0.1:1",
           "HTTPS_PROXY": "http://127.0.0.1:1",
           "http_proxy": "http://127.0.0.1:1",
           "https_proxy": "http://127.0.0.1:1",
           "NO_PROXY": "", "no_proxy": ""}

# Windows-консоль может быть cp1251 — выводим UTF-8, чтобы «→» не падало.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

RESULTS = []  # (step, status, detail)


def log(msg):
    print(f"[e2e-day21] {msg}", flush=True)


def record(step, status, detail=""):
    RESULTS.append((step, status, detail))
    log(f"{status:5s} {step}" + (f" — {detail}" if detail else ""))


def print_summary() -> None:
    c = {"PASS": 0, "FAIL": 0, "SKIP": 0}
    for _, s, _ in RESULTS:
        c[s] = c.get(s, 0) + 1
    log(f"Итог: {c['PASS']} PASS, {c['FAIL']} FAIL, {c['SKIP']} SKIP")


def port_open() -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1)
    try:
        return s.connect_ex((HOST, PORT)) == 0
    finally:
        s.close()


def load_dotenv() -> None:
    """Тот же формат, что в backend/main.py (реальные env имеют приоритет)."""
    path = os.path.join(REPO, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def http(method: str, path: str, body=None, timeout=30) -> tuple:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def http_multipart(path: str, filename: str, content: bytes,
                   content_type: str = "text/plain",
                   timeout: int = 120) -> tuple:
    """POST multipart (поле "file") — загрузка в /api/kb/upload."""
    import uuid
    boundary = "----e2eday21" + uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; '
        f'filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8") + content + f"\r\n--{boundary}--\r\n".encode("utf-8")
    req = urllib.request.Request(BASE + path, data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def request_long(method: str, path: str, body=None,
                 timeout: int = CHAT_TIMEOUT) -> tuple:
    """Чат SSE: до 2 попыток; IncompleteRead (обрыв стрима) — retry,
    затем (0, partial_body, False). partial_body разбираем parse_sse:
    done-кадр мог дойти до обрыва."""
    data = json.dumps(body).encode() if body is not None else None
    partial = b""
    for attempt in range(2):
        req = urllib.request.Request(BASE + path, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read(), True
        except urllib.error.HTTPError as e:
            return e.code, e.read(), True
        except (_http_client.IncompleteRead,
                urllib.error.URLError) as e:
            partial = getattr(e, "partial", b"") or b""
            if attempt == 0:
                log(f"warning: {path} read failed "
                    f"({e.__class__.__name__}), retry")
            else:
                log(f"warning: {path} read failed twice ({e!r})")
    return 0, partial, False


def probe_gpustack():
    """None — достижим, иначе строка причины SKIP live-части."""
    base = os.environ.get("GPUSTACK_BASE_URL", "").strip().rstrip("/")
    if not base:
        return "GPUSTACK_BASE_URL not set (environment or .env)"
    if not os.environ.get("GPUSTACK_API_KEY", "").strip():
        return "GPUSTACK_API_KEY not set (environment or .env)"
    key = os.environ.get("GPUSTACK_API_KEY", "").strip()
    req = urllib.request.Request(
        f"{base}/models", headers={"Authorization": f"Bearer {key}"}, method="GET"
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
        return None
    except urllib.error.HTTPError:
        return None  # API ответил (любой код) — сеть и сервис на месте
    except Exception as e:
        return f"GPustack unreachable at {base}: {e}"


def wait_port_free():
    if not port_open():
        return None
    log(f"port {PORT} busy — waiting up to 10 min (not killing foreign server)")
    deadline = time.time() + WAIT_PORT_BUSY_MAX
    while time.time() < deadline:
        time.sleep(WAIT_PORT_BUSY_STEP)
        if not port_open():
            return None
    return f"port {PORT} still busy after {WAIT_PORT_BUSY_MAX}s"


def start_server():
    log(f"starting uvicorn (prod mode) on port {PORT}")
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "studio.backend.main:app",
         "--host", HOST, "--port", str(PORT)],
        cwd=REPO, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=flags,
    )
    deadline = time.time() + WAIT_SERVER_UP_MAX
    while time.time() < deadline:
        if proc.poll() is not None:
            return None
        code, _, _ = _try_dialogues()
        if code in (200,):
            return proc
        time.sleep(1)
    return None


def _try_dialogues():
    try:
        return http("GET", "/api/dialogues", timeout=3)
    except Exception:
        return 0, b"", {}


def _cleanup(dlg_id, orig_model) -> None:
    """Убрать артефакты самого e2e (не трогая данные пользователя).
    data/kb (live-индекс) — артефакт дня 21, НЕ удаляется."""
    try:
        if dlg_id:
            http("DELETE", f"/api/dialogues/{dlg_id}", timeout=10)
        if orig_model:
            http("POST", "/api/config", {"model": orig_model}, timeout=10)
    except Exception:
        pass


def stop_server(proc) -> None:
    if proc is None:
        return
    log(f"stopping uvicorn (port {PORT})")
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, timeout=15)
        else:
            proc.terminate()
            proc.wait(timeout=15)
    except Exception as e:
        log(f"warning: kill issue: {e}")
    deadline = time.time() + 15
    while time.time() < deadline and port_open():
        time.sleep(0.5)
    if port_open():
        log(f"warning: port {PORT} still open after stop")


def parse_sse(raw: bytes) -> tuple:
    """Разбирает SSE в (deltas, dones, errors)."""
    deltas, dones, errors = [], [], []
    for line in raw.decode("utf-8", "replace").splitlines():
        if not line.startswith("data: "):
            continue
        try:
            ev = json.loads(line[6:])
        except json.JSONDecodeError:
            continue
        t = ev.get("type")
        if t == "delta":
            deltas.append(ev.get("text", ""))
        elif t == "done":
            dones.append(ev)
        elif t == "error":
            errors.append(ev.get("message", "?"))
    return deltas, dones, errors


def _last_journal_request():
    """Полная запись журнала последнего LLM-запроса (с телом) или None."""
    code, body, _ = http("GET", "/api/requests", timeout=30)
    if code != 200:
        return None
    entries = json.loads(body).get("requests", [])
    if not entries:
        return None
    rid = entries[-1].get("id")
    code, body, _ = http("GET", f"/api/requests/{rid}", timeout=30)
    if code != 200:
        return None
    return json.loads(body)


# ---------- Part A: fake-LLM (SSE-чанки в OpenAI-совместимом формате) ----------

_E2E_USAGE = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
              "completion_tokens_details": {"reasoning_tokens": 2}}


def _sse(chunks: list) -> str:
    """SSE-тело из чанков (dict или строка "[DONE]") — кадры data: {json}."""
    parts = []
    for c in chunks:
        if c == "[DONE]":
            parts.append("data: [DONE]\n\n")
        else:
            parts.append("data: " + json.dumps(c, ensure_ascii=False) + "\n\n")
    return "".join(parts)


def _delta_chunk(text: str) -> dict:
    return {"id": "c1", "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {"content": text},
                         "finish_reason": None}]}


def _stop_chunk() -> dict:
    return {"id": "c1", "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": _E2E_USAGE}


def _make_llm_handler(captured: dict):
    """Fake-LLM: non-stream (авто-название) → JSON; stream → захват
    payload (payload/system в captured) + 2 дельты + usage + [DONE]."""
    def handler(request):
        import httpx
        payload = json.loads(request.content)
        if "stream" not in payload:
            return httpx.Response(200, json={"choices": [
                {"message": {"content": "E2E-название"}}]})
        captured["payload"] = payload
        msgs = payload.get("messages") or []
        captured["system"] = msgs[0].get("content", "") if msgs else ""
        return httpx.Response(200, content=_sse([
            _delta_chunk("Прив"), _delta_chunk("ет"),
            _stop_chunk(), "[DONE]",
        ]).encode("utf-8"))
    return handler


def _mini_repo(root: str) -> str:
    """Мини-репозиторий: README + 2 md-дока. Доки репозитория в корпус
    НЕ входят (корпус uploads-only, «+ Добавить файл») — остаются как
    «отвлекающий» репо, тот же паттерн, что в tests/test_kb_api.py."""
    os.makedirs(os.path.join(root, "docs", "superpowers"), exist_ok=True)
    with open(os.path.join(root, "README.md"), "w", encoding="utf-8") as f:
        f.write("Проект Студии. Корпус знаний.\n")
    with open(os.path.join(root, "docs", "superpowers", "music.md"), "w",
              encoding="utf-8") as f:
        f.write("# Инструменты\n\nКсилофон — ударный музыкальный "
                "инструмент.\n\n## Ксилофон\nЗвук ксилофона возникает "
                "при ударе по пластинам.\n")
    with open(os.path.join(root, "docs", "superpowers", "weather.md"), "w",
              encoding="utf-8") as f:
        f.write("# Погода\n\nЗимой бывает холодно.\n")
    return root


def _seed_uploads(kb_dir: str) -> None:
    """Загрузки-фикстуры в kb_dir/uploads — ЕДИНСТВЕННЫЙ источник
    корпуса (uploads-only): music.md (факт про ксилофон) + weather.md."""
    up = os.path.join(kb_dir, "uploads")
    os.makedirs(up, exist_ok=True)
    with open(os.path.join(up, "music.md"), "w", encoding="utf-8") as f:
        f.write("# Инструменты\n\nКсилофон — ударный музыкальный "
                "инструмент.\n\n## Ксилофон\nЗвук ксилофона возникает "
                "при ударе по пластинам.\n")
    with open(os.path.join(up, "weather.md"), "w", encoding="utf-8") as f:
        f.write("# Погода\n\nЗимой бывает холодно.\n")


def part_a() -> bool:
    log("=== Part A: БЗ-ядро (чанки, hash-эмбеддинги, роуты, RAG, "
        "toggles; без сети) ===")
    import httpx
    sys.path.insert(0, os.path.join(REPO, "studio", "backend"))
    sys.path.insert(0, os.path.join(REPO, "studio", "backend", "tests"))
    from agent import StudioAgent
    from kb import (CorpusDoc, FixedChunker, HashEmbedder, KnowledgeBase,
                    StructuredChunker, atomic_write_json)
    from main import create_app
    from mcp import MCPRegistry
    from memory import MemoryStore
    from fastapi.testclient import TestClient
    import test_mcp

    # Сеть на время Part A отрезана (комментарий у _NO_NET).
    saved_env = {k: os.environ.get(k) for k in _NO_NET}
    os.environ.update(_NO_NET)
    tmp = tempfile.mkdtemp(prefix="e2e_day21_")
    reg9 = None
    failed = False
    try:
        repo = _mini_repo(os.path.join(tmp, "repo"))
        kb = KnowledgeBase(os.path.join(tmp, "kb"), repo)

        # A1: корпус = ТОЛЬКО загрузки (uploads-only, «+ Добавить файл»);
        # доки репозитория (README/docs/superpowers) в корпус не входят
        _seed_uploads(kb.kb_dir)
        docs = kb.corpus_files()
        by_path = {d.path: d for d in docs}
        a1 = (len(docs) == 2
              and set(by_path) == {"uploads/music.md",
                                   "uploads/weather.md"}
              and all(d.source == "upload" for d in docs)
              and "README.md" not in by_path
              and "docs/superpowers/music.md" not in by_path
              and os.path.exists(os.path.join(repo, "README.md")))
        record("A1: corpus_files — только загрузки (uploads/*, "
               "source upload; доки репозитория не входят)",
               "PASS" if a1 else "FAIL", f"paths={sorted(by_path)}")

        # A2: FixedChunker — размеры и перекрытие
        fc = FixedChunker(chunk_chars=1000, overlap=200)
        synth = ("абвгде" * 426) + "абвг"  # ровно 2560 символов
        fchunks = fc.chunk(CorpusDoc("synth.txt", synth, "upload"))
        a2 = (len(fchunks) == 3
              and [len(c.text) for c in fchunks] == [1000, 1000, 960]
              and fchunks[1].text[:200] == fchunks[0].text[-200:]
              and fchunks[2].text[:200] == fchunks[1].text[-200:]
              and all(c.section == "whole file" for c in fchunks)
              and [c.chunk_index for c in fchunks] == [0, 1, 2])
        record("A2: FixedChunker (1000/200 → 1000/1000/960, "
               "хвост N = голова N+1)",
               "PASS" if a2 else "FAIL",
               f"n={len(fchunks)} sizes={[len(c.text) for c in fchunks]}")

        # A3: StructuredChunker — md по заголовкам, не-md — whole file
        sc = StructuredChunker()
        mchunks = sc.chunk(by_path["uploads/music.md"])
        plain_md = sc.chunk(CorpusDoc("notes.md", "текст без заголовков",
                                      "docs"))
        code_doc = sc.chunk(CorpusDoc("studio/backend/agent.py",
                                      "def f(): pass", "code"))
        a3 = ([c.section for c in mchunks] == ["Инструменты", "Ксилофон"]
              and len(plain_md) == 1 and plain_md[0].section == "whole file"
              and len(code_doc) == 1 and code_doc[0].section == "whole file")
        record("A3: StructuredChunker (md → секции по заголовкам; "
               "без заголовков / не-md → whole file)",
               "PASS" if a3 else "FAIL",
               f"md_sections={[c.section for c in mchunks]}")

        # A4: build structural + hash → index.db (SQLite)
        res4 = kb.build("structural", HashEmbedder())
        idx4 = kb.load_index()
        comp4 = res4.get("comparison", {})
        a4 = (idx4 is not None
              and os.path.exists(os.path.join(kb.kb_dir, "index.db"))
              and idx4.get("strategy") == "structural"
              and idx4.get("embedder") == "hash"
              and idx4.get("dim") == 256
              and idx4.get("chunks")
              and all("-structural-" in c["chunk_id"]
                      for c in idx4["chunks"])
              and set(comp4) >= {"fixed", "structural"}
              and all({"hit_at_3", "precision_at_3", "mrr"} <= set(comp4[s])
                      for s in ("fixed", "structural"))
              and res4.get("stats", {}).get("chunks", 0) > 0)
        record("A4: build(structural, hash) — index.db (SQLite), "
               "chunk_id *structural-*, comparison (hit@3/prec@3/mrr), "
               "chunks>0",
               "PASS" if a4 else "FAIL",
               f"chunks={res4.get('stats', {}).get('chunks')} "
               f"dim={idx4.get('dim') if idx4 else None}")

        # A4b: авто-миграция legacy index.json → index.db
        # db удалён, индекс возвращён как legacy-json → первая сборка:
        # json потреблён одной транзакцией и удалён, db создан, поиск
        # после миграции работает
        kb_mig = KnowledgeBase(os.path.join(tmp, "kb_mig"), repo)
        _seed_uploads(kb_mig.kb_dir)
        kb_mig.build("structural", HashEmbedder())
        mig_doc = kb_mig.load_index()
        kb_mig._store.remove()
        mig_json = os.path.join(kb_mig.kb_dir, "index.json")
        atomic_write_json(mig_json, mig_doc)
        assert mig_doc is not None  # guard: fixture-сборка дала индекс
        kb_mig.build("structural", HashEmbedder())
        mig_r = kb_mig.search("что про ксилофон", k=3)
        a4b = (mig_doc is not None
               and not os.path.exists(mig_json)
               and os.path.exists(
                   os.path.join(kb_mig.kb_dir, "index.db"))
               and kb_mig.load_index() is not None
               and bool(mig_r)
               and mig_r[0]["file"] == "uploads/music.md")
        record("A4b: авто-миграция index.json → index.db (json удалён, "
               "db создан, поиск работает)",
               "PASS" if a4b else "FAIL",
               f"json_gone={not os.path.exists(mig_json)} "
               f"top={mig_r[0]['file'] if mig_r else None}")

        # A5: build fixed → индекс пересобран, старых chunk_id нет
        kb.build("fixed", HashEmbedder())
        idx5 = kb.load_index()
        a5 = (idx5 is not None and idx5.get("strategy") == "fixed"
              and idx5.get("chunks")
              and all("-fixed-" in c["chunk_id"] for c in idx5["chunks"])
              and all("-structural-" not in c["chunk_id"]
                      for c in idx5["chunks"]))
        record("A5: build(fixed) — стратегия flipped, "
               "структурных chunk_id нет",
               "PASS" if a5 else "FAIL",
               f"strategy={idx5.get('strategy') if idx5 else None} "
               f"chunks={len(idx5.get('chunks', [])) if idx5 else 0}")

        # A6: search — «ксилофон» → загрузка music.md первой
        r6 = kb.search("что про ксилофон", k=3)
        a6 = (bool(r6)
              and r6[0]["file"] == "uploads/music.md"
              and "ксилофон" in r6[0]["text"].lower())
        record("A6: search «что про ксилофон» → uploads/music.md первым",
               "PASS" if a6 else "FAIL",
               f"top={r6[0]['file'] if r6 else None} "
               f"score={r6[0]['score'] if r6 else None}")

        # A7: settings — дефолты + валидация
        kb_s = KnowledgeBase(os.path.join(tmp, "kb_settings"), repo)
        a7 = (kb_s.settings() == KnowledgeBase.DEFAULT_SETTINGS
              and kb_s.update_settings({"rag_top_k": 5})["rag_top_k"] == 5)
        if a7:
            try:
                kb_s.update_settings({"rag_top_k": 11})
                a7 = False
            except ValueError:
                pass
        record("A7: settings (дефолты; rag_top_k=5 ок, "
               "rag_top_k=11 → ValueError)", "PASS" if a7 else "FAIL")

        # A8: роуты /api/kb/* через TestClient (agent — MockTransport)
        kb_api = KnowledgeBase(os.path.join(tmp, "kb_api"), repo)
        # корпус kb_api — тоже только загрузки (иначе первая сборка
        # будет по пустому корпусу, chunks == 0)
        _seed_uploads(kb_api.kb_dir)
        agent8 = StudioAgent(os.path.join(tmp, "data8"),
                             base_url="https://mock.local/v1", api_key="k",
                             client=httpx.Client(transport=httpx.MockTransport(
                                 _make_llm_handler({}))),
                             kb=kb_api)
        client = TestClient(create_app(agent8, kb_api))
        r_no = client.get("/api/kb/stats")
        r_build = client.post("/api/kb/index",
                              json={"strategy": "structural",
                                    "embedder": "hash"})
        r_stats = client.get("/api/kb/stats")
        r_bad = client.post("/api/kb/settings", json={"rag_top_k": 11})
        up_ok = client.post("/api/kb/upload",
                            files={"file": ("note.txt", b"hello kb",
                                             "text/plain")})
        up_bad = client.post("/api/kb/upload",
                             files={"file": ("evil.exe", b"MZ",
                                             "application/octet-stream")})
        up_path = os.path.join(kb_api.kb_dir, "uploads", "note.txt")
        a8 = (r_no.status_code == 404
              and r_build.status_code == 200
              and r_build.json().get("stats", {}).get("chunks", 0) > 0
              and r_stats.status_code == 200
              and r_stats.json().get("exists") is True
              and r_bad.status_code == 400
              and up_ok.status_code == 200
              and up_ok.json().get("ok") is True
              and os.path.exists(up_path)
              and up_bad.status_code == 400
              and not os.path.exists(
                  os.path.join(kb_api.kb_dir, "uploads", "evil.exe")))
        record("A8: /api/kb/* (stats 404→200, index 200, settings "
               "11→400, upload .txt 200 / .exe 400)",
               "PASS" if a8 else "FAIL",
               f"stats={r_no.status_code}/{r_stats.status_code} "
               f"index={r_build.status_code} settings={r_bad.status_code} "
               f"upload={up_ok.status_code}/{up_bad.status_code}")

        # A8b: инкрементальная сборка + build-status (день 21 UX)
        # Загруженный в A8 note.txt ещё не в индексе → pending_files;
        # повторный index (mode auto, strategy+embedder совпадают) —
        # ТОЛЬКО новый файл (incremental, added=1, чанки растут);
        # build-status вне сборки — running:False, phase:done
        st_pen = client.get("/api/kb/stats")
        r_inc = client.post("/api/kb/index",
                            json={"strategy": "structural",
                                  "embedder": "hash"})
        st_after = client.get("/api/kb/stats")
        st_build = client.get("/api/kb/build-status")
        a8b = (st_pen.status_code == 200
               and st_pen.json().get("pending_files") == ["uploads/note.txt"]
               and r_inc.status_code == 200
               and r_inc.json().get("mode") == "incremental"
               and r_inc.json().get("added") == 1
               and r_inc.json().get("stats", {}).get("chunks", 0)
               > r_build.json().get("stats", {}).get("chunks", 0)
               and st_after.json().get("pending_files") == []
               and "uploads/note.txt" in st_after.json().get("files", [])
               and st_build.status_code == 200
               and st_build.json() == {"running": False, "phase": "done",
                                       "done": st_build.json()["total"],
                                       "total": st_build.json()["total"]}
               and st_build.json()["total"] > 0)
        record("A8b: incremental (только новый файл, added=1) + "
               "build-status (running:False, phase:done)",
               "PASS" if a8b else "FAIL",
               f"pending={st_pen.json().get('pending_files')} "
               f"mode={r_inc.json().get('mode') if r_inc.status_code == 200 else r_inc.status_code} "
               f"added={r_inc.json().get('added') if r_inc.status_code == 200 else '-'} "
               f"status={st_build.json()}")

        # A8c: удаление файла + полная очист базы (день 21 UX)
        # DELETE /api/kb/uploads/{name} — файл с диска + чанки из индекса;
        # 404 на отсутствующий; DELETE /api/kb — uploads + index.db,
        # settings сохраняются
        s_before = client.get("/api/kb/stats")
        r_del = client.delete("/api/kb/uploads/note.txt")
        s_del = client.get("/api/kb/stats")
        r_del404 = client.delete("/api/kb/uploads/note.txt")
        client.post("/api/kb/settings", json={"rag_top_k": 8})
        r_wipe = client.delete("/api/kb")
        s_wipe = client.get("/api/kb/stats")
        s_set = client.get("/api/kb/settings")
        a8c = (r_del.status_code == 200
               and r_del.json().get("file") == "note.txt"
               and r_del.json().get("chunks_removed", 0) > 0
               and not os.path.exists(
                   os.path.join(kb_api.kb_dir, "uploads", "note.txt"))
               and "uploads/note.txt" not in s_del.json().get("files", [])
               and s_del.json().get("stats", {}).get("chunks", 99999)
               < s_before.json().get("stats", {}).get("chunks", 0)
               and r_del404.status_code == 404
               and r_wipe.status_code == 200
               and r_wipe.json().get("ok") is True
                and s_wipe.status_code == 404
                and not os.path.exists(
                    os.path.join(kb_api.kb_dir, "index.db"))
               and s_set.status_code == 200
               and s_set.json().get("rag_top_k") == 8)
        record("A8c: delete upload (файл+чанки+stats, 404 повторно) + "
               "wipe (uploads+index, settings живы)",
               "PASS" if a8c else "FAIL",
               f"del={r_del.status_code}/{r_del.json() if r_del.status_code == 200 else '-'} "
               f"del404={r_del404.status_code} wipe={r_wipe.status_code} "
               f"stats={s_wipe.status_code} settings_rag_top_k={s_set.json().get('rag_top_k')}")

        # A8d: гибридный поиск (вектор + BM25, RRF) — «скрытая строка»
        # В «книгу» спрятан редкий токен; вопрос в другой форме
        # («телефона» — род. падеж, в тексте «телефон» — им.) — BM25
        # substring-матч поднимает чанк-пасхалку в top-1 (чистый
        # вектор её не ранжирует — сигнал 6% чанка)
        egg_book = ("Жил-был герой, и был он не злой. " * 25
                    + "У героя был телефон Zubravichka-9000, на который "
                      "он делал много фото. "
                    + "Герой шёл по улице медленно. " * 25)
        r_upb = client.post("/api/kb/upload",
                            files={"file": ("book.txt",
                                            egg_book.encode("utf-8"),
                                            "text/plain")})
        r_b2 = client.post("/api/kb/index",
                           json={"strategy": "structural",
                                 "embedder": "hash"})
        r_se = client.get("/api/kb/search",
                          params={"q": "какая модель телефона была у героя",
                                  "k": 5})
        top_txt = (r_se.json()["results"][0]["text"]
                   if r_se.status_code == 200
                   and r_se.json().get("results") else "")
        a8d = (r_upb.status_code == 200
               and r_b2.status_code == 200
               and r_b2.json().get("mode") == "full"
               and r_se.status_code == 200
               and "Zubravichka-9000" in top_txt)
        record("A8d: hybrid search — скрытая строка «телефон …» найдена "
               "вопросом «…телефона…» (BM25+вектор, RRF, top-1)",
               "PASS" if a8d else "FAIL",
               f"book={r_upb.status_code} build={r_b2.status_code}"
               f"/{r_b2.json().get('mode') if r_b2.status_code == 200 else '-'} "
               f"search={r_se.status_code} "
                f"top1_egg={'Zubravichka-9000' in top_txt}")

        # A8e: двухэтапный поиск (гибридный recall + реранкер) — fake
        # APIReranker без сети: top-1 — чанк-пасхалка (score 0.99),
        # ответ {results, recall_total, reranked}, у результатов
        # rerank_score/stage1_rank, сортировка по rerank_score desc
        import kb as kb_module

        class _FakeRR:
            """Офлайн-фейк реранкера: чанк с пасхалкой — 0.99, остальные —
            0.0i (детерминированная инверсия порядка этапа 1)."""

            def __init__(self, base_url, api_key, client=None):
                pass

            def rerank(self, query, texts):
                return [0.99 if "Zubravichka" in t else round(0.01 + i * 0.001, 6)
                        for i, t in enumerate(texts)]

        _orig_rr = kb_module.APIReranker
        _orig_key = os.environ.get("GPUSTACK_KEY_RERANK")
        os.environ["GPUSTACK_KEY_RERANK"] = "fake-e2e"
        kb_module.APIReranker = _FakeRR
        try:
            r_rr_cfg = client.post("/api/kb/settings",
                                   json={"reranker": "api", "rag_recall": 10})
            r_rr = client.get("/api/kb/search",
                              params={"q": "какая модель телефона была у героя",
                                      "k": 5})
        finally:
            kb_module.APIReranker = _orig_rr
            if _orig_key is None:
                os.environ.pop("GPUSTACK_KEY_RERANK", None)
            else:
                os.environ["GPUSTACK_KEY_RERANK"] = _orig_key
            client.post("/api/kb/settings", json={"reranker": "off"})
        b_rr = r_rr.json() if r_rr.status_code == 200 else {}
        res_rr = b_rr.get("results") or []
        sc_rr = [x.get("rerank_score") for x in res_rr]
        top_rr = res_rr[0] if res_rr else {}
        a8e = (r_rr_cfg.status_code == 200
               and r_rr.status_code == 200
               and b_rr.get("reranked") is True
               and b_rr.get("recall_total", 0) >= 1
               and "Zubravichka-9000" in (top_rr.get("text") or "")
               and top_rr.get("rerank_score") == 0.99
               and top_rr.get("reranked") is True
               and "stage1_rank" in top_rr
               and sc_rr == sorted(sc_rr, reverse=True))
        record("A8e: 2-этап (гибридный recall + fake cross-encoder): "
               "reranked=True, top-1 — чанк-пасхалка (0.99, stage1_rank)",
               "PASS" if a8e else "FAIL",
               f"cfg={r_rr_cfg.status_code} search={r_rr.status_code} "
               f"reranked={b_rr.get('reranked')} "
               f"recall_total={b_rr.get('recall_total')} n={len(res_rr)} "
               f"top1_score={top_rr.get('rerank_score')}")

        # A9: тумблер agent_loop — tools в пейлоаде / нет (fake MCP)
        reg9 = MCPRegistry(MemoryStore(os.path.join(tmp, "mcp9")),
                           launcher=test_mcp.make_fake_launcher())
        sid9 = reg9.servers()[0]["id"]
        v9 = reg9.connect(sid9)
        kb9 = KnowledgeBase(os.path.join(tmp, "kb9"), repo)
        cap = {}
        agent9 = StudioAgent(os.path.join(tmp, "data9"),
                             base_url="https://mock.local/v1", api_key="k",
                             client=httpx.Client(transport=httpx.MockTransport(
                                 _make_llm_handler(cap))),
                             mcp=reg9, kb=kb9)
        mcp_ok = (v9.get("status") == "connected"
                  and v9.get("tools_count") == 2)
        kb9.update_settings({"agent_loop": False})
        d9a = agent9.store.new_dialogue()
        agent9.store.profile_action(d9a["id"], "decline")
        ev9a = list(agent9.ask_stream(d9a["id"], "привет"))
        p9a = cap.get("payload") or {}
        off_ok = (mcp_ok
                  and ev9a and ev9a[-1].get("type") == "done"
                  and "tools" not in p9a
                  and len(agent9.requests_list()) == 1)
        kb9.update_settings({"agent_loop": True})
        cap.clear()
        d9b = agent9.store.new_dialogue()
        agent9.store.profile_action(d9b["id"], "decline")
        ev9b = list(agent9.ask_stream(d9b["id"], "привет"))
        p9b = cap.get("payload") or {}
        names9 = [t.get("function", {}).get("name", "")
                  for t in (p9b.get("tools") or [])]
        on_ok = (ev9b and ev9b[-1].get("type") == "done"
                 and "tools" in p9b
                 and any(n.endswith("__mock_echo") for n in names9))
        record("A9: agent_loop (false → tools нет, 1 вызов; "
               "true → tools *__mock_echo)",
               "PASS" if (off_ok and on_ok) else "FAIL",
               f"mcp={v9.get('status')}/{v9.get('tools_count')} "
               f"off_tools={'tools' in p9a} on_tools={names9}")

        # A10: RAG-блок в system-промпте (on/off)
        kb.update_settings({"rag": True})
        kb.build("structural", HashEmbedder())
        cap10 = {}
        agent10 = StudioAgent(os.path.join(tmp, "data10"),
                              base_url="https://mock.local/v1", api_key="k",
                              client=httpx.Client(transport=httpx.MockTransport(
                                  _make_llm_handler(cap10))),
                              kb=kb)
        d10a = agent10.store.new_dialogue()
        agent10.store.profile_action(d10a["id"], "decline")
        ev10a = list(agent10.ask_stream(d10a["id"], "что про ксилофон?"))
        sys10a = cap10.get("system", "")
        rag_on_ok = (ev10a and ev10a[-1].get("type") == "done"
                     and "База знаний" in sys10a
                     and "ксилофон" in sys10a.lower()
                     and "music.md" in sys10a)
        kb.update_settings({"rag": False})
        cap10.clear()
        d10b = agent10.store.new_dialogue()
        agent10.store.profile_action(d10b["id"], "decline")
        ev10b = list(agent10.ask_stream(d10b["id"], "что про ксилофон?"))
        sys10b = cap10.get("system", "")
        rag_off_ok = (ev10b and ev10b[-1].get("type") == "done"
                      and "База знаний" not in sys10b)
        record("A10: RAG (on → «База знаний» + ксилофон в system; "
               "off → блока нет)",
               "PASS" if (rag_on_ok and rag_off_ok) else "FAIL",
               f"on: блок={'База знаний' in sys10a} "
               f"слово={'ксилофон' in sys10a.lower()} "
               f"off: блок={'База знаний' in sys10b}")
        failed = not (a1 and a2 and a3 and a4 and a4b and a5 and a6 and a7
                      and a8 and off_ok and on_ok
                      and rag_on_ok and rag_off_ok)
    except Exception as e:
        record("A: unexpected exception", "FAIL", repr(e))
        failed = True
    finally:
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        if reg9 is not None:
            try:
                reg9.close_all()
            except Exception:
                pass
        shutil.rmtree(tmp, ignore_errors=True)
    return not failed


def part_b() -> int:
    """Live: uvicorn :8105 + реальный LLM + реальные эмбеддинги
    (best-effort: SKIP, не FAIL, когда модель/окружение не совпали;
    FAIL — только инфраструктурные сбои и сломанные гарантии дня 21)."""
    log("=== Part B: live (uvicorn :8105, реальный LLM + эмбеддинги) ===")
    proc = None
    dlg_id = None
    orig_model = None
    try:
        busy = wait_port_free()
        if busy:
            record("B: port 8105", "SKIP", busy)
            return 0

        proc = start_server()
        if proc is None:
            record("B: uvicorn up (:8105)", "FAIL",
                   "server did not come up on port 8105")
            return 1
        record(f"B: uvicorn up (port {PORT})", "PASS")

        skip_reason = probe_gpustack()
        if skip_reason:
            record("B: live БЗ (реальный LLM + эмбеддинги)", "SKIP",
                   f"GPustack недоступен: {skip_reason}")
            return 0

        # B1: LLM доступен
        code, body, _ = http("GET", "/api/models", timeout=120)
        avail = json.loads(body).get("models", []) if code == 200 else []
        if code != 200 or not avail:
            record("B1: GET /api/models (LLM доступен)", "SKIP",
                   f"code={code} models={len(avail)} — LLM недоступен, "
                   "live-часть пропущена")
            return 0
        record(f"B1: модели ({len(avail)}, чат: {avail[0]['id']})", "PASS")

        code, body, _ = http("GET", "/api/config")
        orig_model = json.loads(body).get("model") if code == 200 else None
        http("POST", "/api/config", {"model": avail[0]["id"]}, timeout=30)

        # B1b: чистое состояние — wipe: старые stale-чанки (доки
        # репозитория из индекса, собранного до фикса) не должны попасть
        # в ассерты; корпус — только загрузка (uploads-only)
        code, body, _ = http("DELETE", "/api/kb", timeout=60)
        if code != 200:
            record("B1b: wipe (DELETE /api/kb)", "FAIL",
                   f"code={code} "
                   f"{body.decode('utf-8', 'replace')[:120]}")
            return 1
        record("B1b: wipe (DELETE /api/kb, чистое состояние)", "PASS",
               body.decode("utf-8", "replace")[:80])

        # B1c: загрузка «книги» с пасхалкой (единственный источник
        # корпуса: uploads, «+ Добавить файл»)
        egg_para = ("Жил-был герой, и был он не злой. Герой шёл по улице "
                    "медленно, думая о прошлом, и вспоминал, как учился "
                    "играть на ксилофоне.\n")
        egg_book = (egg_para * 2500
                    + "У него был телефон IPhone 17Promax, на который он "
                      "снимал все свои приключения.\n"
                    + egg_para * 500)
        code, body, _ = http_multipart("/api/kb/upload", "book.txt",
                                       egg_book.encode("utf-8"))
        if code != 200:
            record("B1c: upload книги (book.txt)", "FAIL",
                   f"code={code} "
                   f"{body.decode('utf-8', 'replace')[:120]}")
            return 1
        record("B1c: upload книги (book.txt, пасхалка IPhone 17Promax)",
               "PASS",
               f"size={len(egg_book.encode('utf-8'))}B")

        # B2: live-индекс с реальными эмбеддингами
        log("строим live-индекс (api-эмбеддинги, корпус = загруженная "
            "книга — может занять пару минут)...")
        code, body, _ = http("POST", "/api/kb/index",
                             {"strategy": "structural", "embedder": "api"},
                             timeout=INDEX_TIMEOUT)
        if code != 200:
            snippet = body.decode("utf-8", "replace")[:200]
            record("B2: POST /api/kb/index (structural, api)", "FAIL",
                   f"code={code} {snippet} — инфраструктура")
            return 1
        b2 = json.loads(body)
        record("B2: индекс собран (api-эмбеддинги)", "PASS",
               f"chunks={b2.get('stats', {}).get('chunks')} "
               f"build_ms={b2.get('stats', {}).get('build_ms')}")

        # B3: stats — exists, dim 4096, comparison, files
        code, body, _ = http("GET", "/api/kb/stats", timeout=60)
        b3 = json.loads(body) if code == 200 else {}
        b3_ok = (code == 200
                 and b3.get("exists") is True
                 and b3.get("dim") == 4096
                 and b3.get("embedder") == "api"
                 and isinstance(b3.get("comparison"), dict)
                 and set(b3.get("comparison", {})) >= {"fixed", "structural"}
                 and b3.get("files"))
        st = b3.get("stats", {})
        for name in ("fixed", "structural"):
            m = (b3.get("comparison") or {}).get(name, {})
            log(f"  {name}: chunks={m.get('chunks')} "
                f"hit@3={m.get('hit_at_3')} prec@3={m.get('precision_at_3')} "
                f"mrr={m.get('mrr')}")
        record("B3: GET /api/kb/stats (exists, dim 4096, comparison, "
               "files)", "PASS" if b3_ok else "FAIL",
               f"chunks={st.get('chunks')} dim={b3.get('dim')} "
               f"build_ms={st.get('build_ms')} "
               f"corpus_words={st.get('corpus_words')} "
               f"files={len(b3.get('files', []))}")
        if not b3_ok:
            return 1

        # B4: search — факт из загруженной книги («модель телефона»);
        # корпус uploads-only, поэтому top — book.txt (не доки репозитория)
        q = urllib.parse.quote("модель телефона")
        code, body, _ = http("GET", f"/api/kb/search?q={q}&k=5", timeout=120)
        res4 = json.loads(body).get("results", []) if code == 200 else []
        b4 = (code == 200 and bool(res4)
              and res4[0].get("file") == "uploads/book.txt"
              and bool(res4[0].get("section")))
        record("B4: GET /api/kb/search?q=\"модель телефона\" (results, "
               "top: uploads/book.txt + section)", "PASS" if b4 else "FAIL",
               f"n={len(res4)} top={res4[0].get('file') if res4 else None}"
               f" {res4[0].get('section') if res4 else None!r} "
               f"score={res4[0].get('score') if res4 else None}")
        if not b4:
            return 1

        # MCP: подключаем дефолтный task_create — чтобы B6 (тумблер
        # agent_loop) не было vacuous: при agent_loop=true tools были бы.
        code, body, _ = http("GET", "/api/mcp/servers", timeout=30)
        servers = (json.loads(body).get("servers", []) if code == 200 else [])
        tc = next((s for s in servers if s.get("name") == "task_create"), None)
        if tc is None:
            record("B: MCP connect (task_create)", "FAIL",
                   "сервер task_create не найден в реестре")
            return 1
        code, body, _ = http("POST", f"/api/mcp/servers/{tc['id']}/connect",
                             timeout=90)
        view = json.loads(body).get("server", {}) if code == 200 else {}
        if code != 200 or view.get("status") != "connected":
            record("B: MCP connect (task_create)", "FAIL",
                   f"code={code} status={view.get('status')} "
                   f"error={view.get('error')}")
            return 1
        record("B: MCP connect (task_create, connected)", "PASS")

        # B5: RAG live — чат + детерминированный чек журнала
        http("POST", "/api/kb/settings",
             {"rag": True, "agent_loop": True}, timeout=30)
        code, body, _ = http("POST", "/api/dialogues")
        dlg = json.loads(body).get("dialogue", {}) if code == 201 else {}
        if code != 201 or not dlg.get("id"):
            record("B: POST /api/dialogues", "FAIL", f"code={code}")
            return 1
        dlg_id = dlg["id"]
        record("B: dialogue (201)", "PASS", dlg_id[:8])

        code, _, _ = http("POST", "/api/profile/action",
                          {"dialogue_id": dlg_id, "action": "decline"})
        if code != 200:
            record("B: профиль decline", "FAIL", f"code={code}")
            return 1
        record("B: профиль decline", "PASS")

        code, raw, full = request_long(
            "POST", "/api/chat",
            {"dialogue_id": dlg_id, "message": RAG_QUERY})
        deltas, dones, errors = parse_sse(raw)
        if errors:
            record("B5: RAG live (done)", "SKIP",
                   f"LLM/tool-loop ошибка: {errors[0]} (best-effort)")
            return 0
        if code != 200 or not dones:
            record("B5: RAG live (done)", "FAIL",
                   f"code={code} dones={len(dones)} full={full} "
                   f"partial={len(raw)}B")
            return 1
        answer = (dones[-1].get("answer", "") if dones else "")
        entry = _last_journal_request()
        body_req = (entry or {}).get("request") or {}
        sys_prompt = (((body_req.get("messages") or [{}])[0])
                      .get("content", ""))
        kb_in_journal = "База знаний" in sys_prompt
        record(f"B5: RAG live (done, {len(deltas)} deltas) + «База "
               f"знаний» в system-промпте (журнал)",
               "PASS" if kb_in_journal else "FAIL",
               f"kb_block={kb_in_journal} "
               f"answer[:80]={answer[:80]!r} (смысл ответа не проверяется)")
        if not kb_in_journal:
            return 1

        # B7: live reranker (qwen3-reranker-4b) — 2-этапный поиск +
        # rag_context у assistant-сообщения: settings reranker=api,
        # rag_recall=50; hard — механизм (reranked=true, поля, сортировка
        # по rerank_score) + stage-1 (гибрид) нашёл пасхалку
        # (stage1_rank<=5); informational — ранк пасхалки после
        # реранкера (cross-encoder не всегда поднимает иголку в длинном
        # чанке — live-факт дня 21: s1=2 → rr=10); чат —
        # message.rag_context.reranked=true. Без ключа / сбой
        # реранкер-API — SKIP (best-effort)
        code, stb, _ = http("GET", "/api/kb/stats")
        rr_key_cfg = (code == 200
                      and json.loads(stb).get("reranker_key_configured")
                      is True)
        if not rr_key_cfg:
            record("B7: live reranker (qwen3-reranker-4b)", "SKIP",
                   "GPUSTACK_KEY_RERANK не настроен на сервере")
        else:
            code, _, _ = http("POST", "/api/kb/settings",
                              {"reranker": "api", "rag_recall": 50},
                              timeout=30)
            if code != 200:
                record("B7: settings reranker=api", "FAIL", f"code={code}")
                return 1
            # k=50 — полный recall: чанк-пасхалка в выдаче даже если
            # cross-encoder сдвинул её ниже top-5
            code, seb, _ = http(
                "GET", "/api/kb/search?q="
                + urllib.parse.quote(RAG_EGG_QUERY) + "&k=50", timeout=120)
            se7 = json.loads(seb) if code == 200 else {}
            res7 = se7.get("results") or []
            sc7 = [x.get("rerank_score") for x in res7]
            b7_search = (code == 200
                         and se7.get("reranked") is True
                         and se7.get("recall_total", 0) >= 1
                         and 1 <= len(res7) <= 50
                         and all("rerank_score" in x and "stage1_rank" in x
                                 for x in res7)
                         and sc7 == sorted(sc7, reverse=True))
            egg7 = next((x for x in res7
                         if "IPhone 17Promax" in (x.get("text") or "")),
                        None)
            b7_egg_s1 = (egg7 is not None
                         and egg7.get("stage1_rank", 999) <= 5)
            egg7_rr_pos = (next((i for i, x in enumerate(res7, 1)
                                 if x is egg7), None)
                           if egg7 is not None else None)
            if code != 200 or se7.get("reranked") is not True:
                record("B7: live rerank search", "SKIP",
                       f"code={code} "
                       f"reranked={se7.get('reranked') if code == 200 else '-'}"
                       " (реранкер-API деградировал → гибридный поиск, "
                       "best-effort)")
                http("POST", "/api/kb/settings", {"reranker": "off"},
                     timeout=30)
            else:
                code, raw, full = request_long(
                    "POST", "/api/chat",
                    {"dialogue_id": dlg_id, "message": RAG_EGG_QUERY})
                deltas, dones, errors = parse_sse(raw)
                if errors:
                    record("B7: live rerank chat (done)", "SKIP",
                           f"LLM-ошибка: {errors[0]} (best-effort)")
                    http("POST", "/api/kb/settings", {"reranker": "off"},
                         timeout=30)
                    return 0
                if code != 200 or not dones:
                    record("B7: live rerank chat (done)", "FAIL",
                           f"code={code} dones={len(dones)} full={full} "
                           f"partial={len(raw)}B")
                    http("POST", "/api/kb/settings", {"reranker": "off"},
                         timeout=30)
                    return 1
                code, mbb, _ = http("GET", "/api/dialogues/" + dlg_id)
                mlist = (((json.loads(mbb).get("dialogue") or {})
                          .get("messages")) if code == 200 else []) or []
                asst7 = [m for m in mlist if m.get("role") == "assistant"]
                ctx7 = asst7[-1].get("rag_context") if asst7 else None
                b7_ctx = (isinstance(ctx7, dict)
                          and ctx7.get("reranked") is True
                          and (ctx7.get("chunks") or []))
                egg_in_ctx = any(
                    "IPhone 17Promax" in (c.get("text") or "")
                    for c in (ctx7 or {}).get("chunks", []))
                http("POST", "/api/kb/settings", {"reranker": "off"},
                     timeout=30)
                b7 = b7_search and b7_egg_s1 and b7_ctx
                record("B7: live reranker — 2-этапный поиск (механизм) + "
                       "stage-1 нашёл пасхалку (stage1_rank<=5) + "
                       "rag_context в сообщении; ранк после реранкера — "
                       "info",
                       "PASS" if b7 else "FAIL",
                       f"reranked={se7.get('reranked')} "
                       f"egg_s1_rank={egg7.get('stage1_rank') if egg7 else None}"
                       f" egg_rr_pos={egg7_rr_pos} "
                       f"recall_total={se7.get('recall_total')} "
                       f"ctx_reranked={bool(ctx7 and ctx7.get('reranked'))} "
                       f"ctx_chunks={len((ctx7 or {}).get('chunks', []))} "
                       f"egg_in_ctx={egg_in_ctx} (info)")
                if not b7:
                    return 1

        # B6: тумблер agent_loop — tools из тела запроса
        code, _, _ = http("POST", "/api/kb/settings",
                          {"agent_loop": False}, timeout=30)
        if code != 200:
            record("B6: settings agent_loop=false", "FAIL", f"code={code}")
            return 1
        code, raw, full = request_long(
            "POST", "/api/chat",
            {"dialogue_id": dlg_id,
             "message": "Привет. Ответь одним словом."})
        deltas, dones, errors = parse_sse(raw)
        if errors:
            record("B6: agent_loop off (done)", "SKIP",
                   f"LLM-ошибка: {errors[0]} (best-effort)")
            return 0
        if code != 200 or not dones:
            record("B6: agent_loop off (done)", "FAIL",
                   f"code={code} dones={len(dones)} full={full} "
                   f"partial={len(raw)}B")
            return 1
        entry = _last_journal_request()
        body_req = (entry or {}).get("request") or {}
        no_tools = "tools" not in body_req
        record("B6: agent_loop=false → в теле LLM-запроса "
               "КЛЮЧА tools НЕТ (MCP подключён)",
               "PASS" if no_tools else "FAIL",
               f"keys={sorted(body_req.keys()) if body_req else None}")
        http("POST", "/api/kb/settings", {"agent_loop": True}, timeout=30)
        return 0 if no_tools else 1
    finally:
        try:
            _cleanup(dlg_id, orig_model)
        finally:
            stop_server(proc)


def main() -> int:
    load_dotenv()
    if not part_a():
        print_summary()
        return 1
    rc = part_b()
    print_summary()
    return rc


if __name__ == "__main__":
    sys.exit(main())
