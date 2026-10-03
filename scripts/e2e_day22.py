"""E2E день 22 «Первый RAG-запрос: POST /api/rag/compare» — гибрид:
детерминированное ядро + live.

Part A (MUST PASS, офлайн, без uvicorn и без сети):
    1. sys.path → studio/backend; tmp-БЗ (КОРПУС = ТОЛЬКО коммит-фикстура
       egg_book.txt, data/kb НЕ используется) + structural + HashEmbedder;
       in-process TestClient(create_app(agent, kb)), agent — StudioAgent
       с httpx.MockTransport fake-LLM, который ЗАХВАТЫВАЕТ каждый
       non-stream payload (порядок рук: plain → rag).
       `from main import create_app` — ЛЕНИВО, внутри функции: main.py:33
       load_dotenv'ит .env в МОДУЛЬНОМ уровне — модульный импорт притянул
       бы реальные GPUSTACK-ключи в os.environ (паттерн test_rag_compare).
    2. Ассерты (каждый — record):
       A1 (C1)  endpoint shape: POST /api/rag/compare → 200, все 5 полей
           (answer_plain, answer_rag, kb_block, chunks, rag_context),
           ответы non-empty, chunks non-empty (форма GET /api/kb/search,
           file = «uploads/<имя>»);
       A2 (C2)  kb_block: в system-промпте rag-руки (captured[1]) ЕСТЬ
           «База знаний», в plain-руке (captured[0]) НЕТ; user-сообщение
           == вопрос; ровно 2 non-stream вызова;
       A3 (C3)  400 пустой/не-строка вопрос (LLM не вызывается); 404
           «Индекс не построен» без индекса (загрузка есть, build нет);
       A4 (C4)  settings rag=false → оба ответа всё равно приходят
           (флаг игнорируется compare-эндпоинтом; kb_block на rag-руке
           остаётся);
       A5 (C5)  fixture-схема control_questions.json: 10 вопросов,
           expect_facts/expected_sources non-empty, id 1..10
           (encoding utf-8-sig — файл может иметь BOM);
       A6 (C6)  оба захваченных payload: temperature == 0,
           max_tokens == 1024, model == config model;
       A7 (C7)  голый system-промпт: маркеров «Профиль»/«Память»/
           «Инварианты» нет ни на одной руке (сравнение — не чат).
       Сеть отрезана на всё Part A proxy-переменными _NO_NET (127.0.0.1:1)
       — ни одна точка не дойдёт до реального API; env восстанавливается
       до Part B.
    3. finally: восстановление env, удаление tmp.

Part B — live (uvicorn :8106, реальный GPustack LLM, best-effort):
    1. Порт 8106: занят — ждать до 10 минут (чужой сервер НЕ убивается),
       всё ещё занят — SKIP (exit 0). Запуск `python -m uvicorn
       studio.backend.main:app --port 8106` detached из корня репозитория
       (готовность — GET /api/dialogues); timeout старта — собственный
       Popen-процесс убивается (иначе orphan держит порт).
    2. probe_gpustack() — недостижим → SKIP (exit 0).
    3. B1: corpus rebuild: GET /api/models (LLM недоступен → SKIP) →
       DELETE /api/kb (wipe) → upload egg_book.txt (фикстура) + 1-2 книги
       из data/kb/uploads (байты читаются ДО wipe, иначе wipe их удалит)
       → POST /api/kb/index {structural, api} (сбой/нет ключа → hash +
       метка) → GET /api/kb/build-status (running:False, phase:done,
       total>0). Индекс пишется в реальный data/kb — артефакт дня.
    4. B2: POST /api/kb/settings {embedder: api, reranker: api} — по
       наличию ключей GPUSTACK_KEY_EMBED / GPUSTACK_KEY_RERANK в env;
       без ключа — off/hash + метка.
    5. B3: один live compare-запрос «Какой телефон был у героя?» → 200,
       оба ответа non-empty, chunks non-empty; ответ с «Ошибка…» —
       SKIP (best-effort, паттерн B5 дня 21), не FAIL.
    6. B4: easter-egg-факт «IPhone 17Promax» (casefold) в answer_rag —
       best-effort: нет факта = WARNING в detail (PASS), НЕ FAIL
       (semantics модели не гарантируем).
    7. cleanup (ВСЕГДА в finally): восстановление модели конфига,
       stop_server (taskkill /PID /T /F, проверка порта).

Выход: PASS/FAIL на stdout; exit 0 для PASS и SKIP, 1 для FAIL.
Part A MUST PASS всегда (офлайн, детерминированное). Part B — PASS/SKIP.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
HOST = "127.0.0.1"
PORT = 8106  # 8100 — e2e_studio.py, 8101 — day17, 8102 — day18,
             # 8103 — day19, 8104 — day20, 8105 — day21
BASE = f"http://{HOST}:{PORT}"
WAIT_PORT_BUSY_MAX = 600   # 10 минут ожидания освобождения порта
WAIT_PORT_BUSY_STEP = 10
WAIT_SERVER_UP_MAX = 90
COMPARE_TIMEOUT = 825      # день 23: 5 non-stream LLM-вызовов (4 армы + rewrite)
INDEX_TIMEOUT = 900        # live-сборка индекса (корпус до нескольких МБ)
EGG_FIXTURE = os.path.join(REPO, "studio", "backend", "tests", "fixtures",
                           "egg_book.txt")
CONTROL_FIXTURE = os.path.join(REPO, "studio", "backend", "tests", "fixtures",
                               "control_questions.json")
COMPARE_QUESTION = "Какой телефон был у героя?"
EGG_FACT = "IPhone 17Promax"
# Part A «без сети» (паттерн e2e_day20/21 _NO_NET): мёртвый прокси — ни
# одна точка Part A (TestClient, fake LLM, HashEmbedder) не дойдёт до
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
    print(f"[e2e-day22] {msg}", flush=True)


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


def _local_open(req, timeout):
    """Локальный сервер (127.0.0.1:8106) — БЕЗ прокси: shell-прокси
    (в т.ч. мёртвый _NO_NET в net-cut-ране) не должен рвать readiness-
    чек и API-вызовы к своему же серверу. Внешний probe_gpustack() —
    наоборот, остаётся proxy-aware (в net-cut — SKIP)."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return opener.open(req, timeout=timeout)


def http(method: str, path: str, body=None, timeout=30) -> tuple:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with _local_open(req, timeout) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def http_multipart(path: str, filename: str, content: bytes,
                   content_type: str = "text/plain",
                   timeout: int = 120) -> tuple:
    """POST multipart (поле "file") — загрузка в /api/kb/upload."""
    import uuid
    boundary = "----e2eday22" + uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; '
        f'filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8") + content + f"\r\n--{boundary}--\r\n".encode("utf-8")
    req = urllib.request.Request(BASE + path, data=body, method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    try:
        with _local_open(req, timeout) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


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
        return None  # API ответил (любой код, включая 401) — сервис на месте
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


def _kill_proc(proc) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, timeout=15)
        else:
            proc.terminate()
            proc.wait(timeout=15)
    except Exception as e:
        log(f"warning: kill issue: {e}")


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
    # Timeout старта: СВОЙ процесс убиваем — orphan держал бы порт
    # (урок T6 bug-2), чужой сервер так не трогаем.
    log(f"warning: uvicorn did not come up in {WAIT_SERVER_UP_MAX}s — "
        "killing our own process")
    _kill_proc(proc)
    return None


def _try_dialogues():
    try:
        return http("GET", "/api/dialogues", timeout=3)
    except Exception:
        return 0, b"", {}


def stop_server(proc) -> None:
    if proc is None:
        return
    log(f"stopping uvicorn (port {PORT})")
    _kill_proc(proc)
    deadline = time.time() + 15
    while time.time() < deadline and port_open():
        time.sleep(0.5)
    if port_open():
        log(f"warning: port {PORT} still open after stop")


# ---------- Part A: fake-LLM (non-stream — захват payload; stream — SSE) ----------

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


def _llm_handler(captured: list):
    """Fake-LLM: non-stream (обе руки compare) — 200 с детерминированным
    ответом, payload сохраняется в captured (порядок = порядок вызовов:
    plain → rag). Stream-ответы (остальные эндпоинты) — пара дельт +
    usage, как в test_kb_api."""
    def handler(request):
        import httpx
        payload = json.loads(request.content)
        if "stream" in payload:
            return httpx.Response(200, content=_sse([
                _delta_chunk("Ок"), _stop_chunk(), "[DONE]",
            ]).encode("utf-8"))
        captured.append(payload)
        return httpx.Response(200, json={"choices": [
            {"message": {"content": "Детерминированный ответ e2e."}}]})
    return handler


def _make_tmp_kb(root: str, build: bool):
    """Tmp-БЗ: корпус = ТОЛЬКО egg_book.txt (uploads-only, день 21).
    build=True — structural + HashEmbedder."""
    from kb import HashEmbedder, KnowledgeBase
    kb = KnowledgeBase(os.path.join(root, "kb"), root)
    up = os.path.join(kb.kb_dir, "uploads")
    os.makedirs(up, exist_ok=True)
    with open(EGG_FIXTURE, encoding="utf-8") as f:
        text = f.read()
    with open(os.path.join(up, "egg_book.txt"), "w", encoding="utf-8") as g:
        g.write(text)
    if build:
        kb.build("structural", HashEmbedder())
    return kb


def part_a() -> bool:
    log("=== Part A: RAG compare (офлайн: TestClient + fake LLM + "
        "HashEmbedder, без сети) ===")
    import httpx
    sys.path.insert(0, os.path.join(REPO, "studio", "backend"))
    from agent import StudioAgent
    from fastapi.testclient import TestClient

    # Сеть на время Part A отрезана (комментарий у _NO_NET).
    saved_env = {k: os.environ.get(k) for k in _NO_NET}
    os.environ.update(_NO_NET)
    tmp = tempfile.mkdtemp(prefix="e2e_day22_")
    failed = False
    try:
        # ЛЕНИВЫЙ импорт: main.py:33 load_dotenv'ит .env в модульном
        # уровне — при модульном импорте притянул бы реальные ключи
        # (паттерн test_rag_compare._client).
        from main import create_app

        kb = _make_tmp_kb(os.path.join(tmp, "kb_main"), build=True)
        captured = []
        agent = StudioAgent(os.path.join(tmp, "data"),
                            base_url="https://mock.local/v1", api_key="k",
                            client=httpx.Client(transport=httpx.MockTransport(
                                _llm_handler(captured))),
                            kb=kb)
        client = TestClient(create_app(agent, kb))

        def compare(question):
            return client.post("/api/rag/compare", json={"question": question})

        # A1 (C1): endpoint shape — 200, все 5 полей, non-empty
        r1 = compare(COMPARE_QUESTION)
        b1 = r1.json() if r1.status_code == 200 else {}
        c0 = (b1.get("chunks") or [{}])[0]
        a1 = (r1.status_code == 200
              and all(isinstance(b1.get(k), str) and b1[k].strip()
                      for k in ("answer_plain", "answer_rag", "kb_block"))
              and isinstance(b1.get("chunks"), list)
              and len(b1["chunks"]) >= 1
              and all(key in c0 for key in
                      ("chunk_id", "source", "file", "section", "score",
                       "text"))
              and c0.get("file") == "uploads/egg_book.txt"
              and isinstance(b1.get("rag_context"), dict))
        record("A1: shape — POST /api/rag/compare → 200, все 5 полей "
               "(answer_plain/answer_rag/kb_block/chunks/rag_context), "
               "chunks non-empty (uploads/egg_book.txt)",
               "PASS" if a1 else "FAIL",
               f"code={r1.status_code} "
               f"plain={len(b1.get('answer_plain', ''))}B "
               f"rag={len(b1.get('answer_rag', ''))}B "
               f"chunks={len(b1.get('chunks', []))} "
               f"top={c0.get('file')}")

        # A2 (C2): kb_block — в rag-руке ЕСТЬ «База знаний», в plain НЕТ
        plain_sys = ((captured[0].get("messages") or [{}])[0].get("content", "")
                     if len(captured) > 0 else "")
        rag_sys = ((captured[1].get("messages") or [{}])[0].get("content", "")
                   if len(captured) > 1 else "")
        plain_user = ((captured[0].get("messages") or [{}])[1].get("content", "")
                      if len(captured) > 0
                      and len(captured[0].get("messages", [])) > 1 else "")
        a2 = (len(captured) == 5  # день 23: plain, rag, filter, rewrite,
              # rewrite-arm (контракт захвата)
              and "База знаний" not in plain_sys
              and "База знаний" in rag_sys
              and plain_user == COMPARE_QUESTION)
        record("A2: kb_block — «База знаний» в system rag-руки, НЕТ в "
               "plain-руке (fake-LLM захват, порядок plain → rag → …)",
               "PASS" if a2 else "FAIL",
               f"calls={len(captured)} "
               f"plain_block={'База знаний' in plain_sys} "
               f"rag_block={'База знаний' in rag_sys}")

        # A3 (C3): 400 пустой/не-строка вопрос + 404 без индекса
        r_e = compare("")
        r_w = compare("   ")
        r_n = client.post("/api/rag/compare", json={"question": 123})
        r_x = client.post("/api/rag/compare", json={})
        detail_ok = (r_e.status_code == 400 and r_w.status_code == 400
                     and r_n.status_code == 400 and r_x.status_code == 400
                     and all(isinstance(r.json().get("detail"), str)
                             and r.json()["detail"]
                             for r in (r_e, r_w, r_n, r_x)))
        no_calls = len(captured) == 5  # LLM на 400 не вызывался
        # 404 — отдельная tmp-БЗ: загрузка есть, индекс НЕ собран
        kb404 = _make_tmp_kb(os.path.join(tmp, "kb_noidx"), build=False)
        agent404 = StudioAgent(os.path.join(tmp, "data404"),
                               base_url="https://mock.local/v1", api_key="k",
                               client=httpx.Client(
                                   transport=httpx.MockTransport(
                                       _llm_handler([]))),
                               kb=kb404)
        client404 = TestClient(create_app(agent404, kb404))
        r_404 = client404.post("/api/rag/compare",
                               json={"question": COMPARE_QUESTION})
        a3 = (detail_ok and no_calls
              and r_404.status_code == 404
              and "Индекс не построен" in r_404.json().get("detail", ""))
        record("A3: 400 пустой/не-строка вопрос (LLM не вызван) + "
               "404 «Индекс не построен» без индекса",
               "PASS" if a3 else "FAIL",
               f"400={r_e.status_code}/{r_w.status_code}/{r_n.status_code}/"
               f"{r_x.status_code} 404={r_404.status_code} "
               f"calls_before={len(captured)}")

        # A4 (C4): settings rag=false → оба ответа всё равно (игнор флага)
        kb.update_settings({"rag": False})
        cap4 = []
        agent4 = StudioAgent(os.path.join(tmp, "data4"),
                             base_url="https://mock.local/v1", api_key="k",
                             client=httpx.Client(
                                 transport=httpx.MockTransport(
                                     _llm_handler(cap4))),
                             kb=kb)
        client4 = TestClient(create_app(agent4, kb))
        r4 = client4.post("/api/rag/compare",
                          json={"question": COMPARE_QUESTION})
        b4 = r4.json() if r4.status_code == 200 else {}
        rag4_sys = ((cap4[1].get("messages") or [{}])[0].get("content", "")
                    if len(cap4) > 1 else "")
        a4 = (r4.status_code == 200
              and isinstance(b4.get("answer_plain"), str)
              and b4["answer_plain"].strip()
              and isinstance(b4.get("answer_rag"), str)
              and b4["answer_rag"].strip()
              and "База знаний" in rag4_sys)
        kb.update_settings({"rag": True})
        record("A4: settings rag=false → оба ответа приходят (флаг "
               "игнорируется compare-эндпоинтом, kb_block на месте)",
               "PASS" if a4 else "FAIL",
               f"code={r4.status_code} "
               f"plain={len(b4.get('answer_plain', ''))}B "
               f"rag={len(b4.get('answer_rag', ''))}B "
               f"rag_block={'База знаний' in rag4_sys}")

        # A5 (C5): fixture-схема control_questions.json
        with open(CONTROL_FIXTURE, encoding="utf-8-sig") as f:
            q5 = json.load(f)
        ids5 = [q.get("id") for q in q5]
        a5 = (isinstance(q5, list) and len(q5) == 10
              and ids5 == list(range(1, 11))
              and all(isinstance(q.get("question"), str)
                      and q["question"].strip() for q in q5)
              and all(isinstance(q.get("expect_facts"), list)
                      and q["expect_facts"]
                      and all(isinstance(x, str) for x in q["expect_facts"])
                      for q in q5)
              and all(isinstance(q.get("expected_sources"), list)
                      and q["expected_sources"]
                      and all(isinstance(x, str)
                              for x in q["expected_sources"])
                      for q in q5))
        record("A5: control_questions.json — 10 вопросов, id 1..10, "
               "expect_facts/expected_sources non-empty (utf-8-sig)",
               "PASS" if a5 else "FAIL",
               f"n={len(q5) if isinstance(q5, list) else '-'} "
               f"ids={ids5 if isinstance(q5, list) else '-'}")

        # A6 (C6): T=0 + max_tokens=1024 в payload-ах 4-х рук
        # (plain/rag/filter/rewrite-arm — позиции 0,1,2,4); rewrite-вызов
        # (позиция 3, день 23 задача 3) — T=0, max_tokens=200
        model6 = agent.get_config()["model"]
        arm_calls6 = [captured[i] for i in (0, 1, 2, 4)]
        rewrite6 = captured[3]
        a6 = (len(captured) == 5
              and all(p.get("temperature") == 0
                      and p.get("max_tokens") == 1024
                      and p.get("model") == model6 for p in arm_calls6)
              and rewrite6.get("temperature") == 0
              and rewrite6.get("max_tokens") == 200
              and rewrite6.get("model") == model6)
        record("A6: payload-ы 4-х рук — temperature == 0, max_tokens == "
               "1024, model == config (+ rewrite-вызов: max_tokens=200)",
               "PASS" if a6 else "FAIL",
               f"t={[p.get('temperature') for p in captured]} "
               f"mt={[p.get('max_tokens') for p in captured]} "
               f"model={model6}")

        # A7 (C7): голый system-промпт — без маркеров чата
        markers = ("Профиль", "Память", "Инварианты")
        sysprompts = [p["messages"][0]["content"] for p in captured[:2]]
        a7 = (len(captured) == 5  # день 23: 4 армы + rewrite-вызов
              and all(m not in s for s in sysprompts for m in markers))
        record("A7: bare system prompt — маркеров «Профиль»/«Память»/"
               "«Инварианты» нет ни на одной руке",
               "PASS" if a7 else "FAIL",
               f"hits={[m for s in sysprompts for m in markers if m in s]}")

        failed = not (a1 and a2 and a3 and a4 and a5 and a6 and a7)
    except Exception as e:
        record("A: unexpected exception", "FAIL", repr(e))
        failed = True
    finally:
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(tmp, ignore_errors=True)
    return not failed

def part_b() -> int:
    """Live: uvicorn :8106 + реальный LLM (best-effort: SKIP, не FAIL,
    когда модель/окружение не совпали; FAIL — только инфраструктурные
    сбои и сломанные гарантии дня 22)."""
    log("=== Part B: live (uvicorn :8106, реальный LLM + RAG compare) ===")
    proc = None
    orig_model = None
    try:
        busy = wait_port_free()
        if busy:
            record("B: port 8106", "SKIP", busy)
            return 0

        proc = start_server()
        if proc is None:
            record("B: uvicorn up (:8106)", "FAIL",
                   "server did not come up on port 8106")
            return 1
        record(f"B: uvicorn up (port {PORT})", "PASS")

        skip_reason = probe_gpustack()
        if skip_reason:
            record("B: live RAG compare", "SKIP",
                   f"GPustack недоступен: {skip_reason}")
            return 0

        # LLM доступен, фиксируем модель (восстановим в cleanup)
        code, body, _ = http("GET", "/api/models", timeout=120)
        avail = json.loads(body).get("models", []) if code == 200 else []
        if code != 200 or not avail:
            record("B: GET /api/models (LLM доступен)", "SKIP",
                   f"code={code} models={len(avail)} — LLM недоступен, "
                   "live-часть пропущена")
            return 0
        code, body, _ = http("GET", "/api/config")
        orig_model = json.loads(body).get("model") if code == 200 else None
        http("POST", "/api/config", {"model": avail[0]["id"]}, timeout=30)

        # B1: corpus rebuild — wipe → upload (egg + 1-2 книги, байты ДО
        # wipe: сам wipe удаляет файлы) → index (api; сбой → hash + метка)
        # → build-status
        up_dir = os.path.join(REPO, "data", "kb", "uploads")
        extras = []  # (name, bytes) — читаем ДО wipe
        if os.path.isdir(up_dir):
            for name in sorted(os.listdir(up_dir)):
                if name == "egg_book.txt" or len(extras) >= 2:
                    continue
                p = os.path.join(up_dir, name)
                if not os.path.isfile(p):
                    continue
                with open(p, "rb") as f:
                    extras.append((name, f.read()))
        code, body, _ = http("DELETE", "/api/kb", timeout=60)
        b1_wipe = code == 200
        with open(EGG_FIXTURE, "rb") as f:
            egg_bytes = f.read()
        code, body, _ = http_multipart("/api/kb/upload", "egg_book.txt",
                                       egg_bytes)
        b1_upload = code == 200
        b1_extras = []
        for name, data in extras:
            code, body, _ = http_multipart("/api/kb/upload", name, data)
            b1_extras.append(f"{name}:{code}")
            if code != 200:
                b1_upload = False
        extra_names = "+".join(n for n, _ in extras)
        log(f"строим live-индекс (корпус = egg{'+ ' + extra_names if extra_names else ''} — может занять пару минут)...")
        code, body, _ = http("POST", "/api/kb/index",
                             {"strategy": "structural", "embedder": "api"},
                             timeout=INDEX_TIMEOUT)
        b1_embedder = "api"
        if code != 200:
            # api-эмбеддер недоступен (нет ключа / API упал) → hash + метка
            log(f"warning: api-индекс не удался (code={code}), "
                "fallback hash")
            b1_embedder = "hash"
            code, body, _ = http("POST", "/api/kb/index",
                                 {"strategy": "structural", "embedder": "hash"},
                                 timeout=INDEX_TIMEOUT)
        b1_index = code == 200
        b1_chunks = (json.loads(body).get("stats", {}).get("chunks", 0)
                     if b1_index else 0)
        code, body, _ = http("GET", "/api/kb/build-status", timeout=30)
        bs = json.loads(body) if code == 200 else {}
        b1_status = (code == 200 and bs.get("running") is False
                     and bs.get("phase") == "done" and bs.get("total", 0) > 0)
        b1 = b1_wipe and b1_upload and b1_index and b1_status
        record("B1: corpus rebuild (wipe → upload egg"
               + (f" + {extra_names}" if extra_names else "")
               + " → index → build-status)",
               "PASS" if b1 else "FAIL",
               f"wipe={b1_wipe} upload={b1_extras or 'egg-only'} "
               f"embedder={b1_embedder} chunks={b1_chunks} "
               f"status={bs if code == 200 else code}")
        if not b1:
            return 1

        # B2: settings — embedder/reranker api по наличию ключей,
        # иначе off/hash + метка
        embed_key = bool(os.environ.get("GPUSTACK_KEY_EMBED", "").strip())
        rerank_key = bool(os.environ.get("GPUSTACK_KEY_RERANK", "").strip())
        patch = {"embedder": "api" if embed_key else "hash",
                 "reranker": "api" if rerank_key else "off"}
        code, body, _ = http("POST", "/api/kb/settings", patch, timeout=30)
        b2 = (code == 200
              and json.loads(body).get("embedder") == patch["embedder"]
              and json.loads(body).get("reranker") == patch["reranker"])
        note2 = (f"embedder={patch['embedder']} reranker={patch['reranker']}"
                 if (embed_key or rerank_key) else
                 "ключей GPUSTACK_KEY_EMBED/RERANK нет — off/hash + метка")
        record("B2: settings (embedder/reranker api при ключах, "
               "иначе off + метка)", "PASS" if b2 else "FAIL", note2)
        if not b2:
            return 1

        # B3: один live compare-запрос — 200, оба ответа non-empty,
        # chunks non-empty; «Ошибка…» в ответе — SKIP (best-effort,
        # паттерн B5 дня 21)
        code, body, _ = http("POST", "/api/rag/compare",
                             {"question": COMPARE_QUESTION},
                             timeout=COMPARE_TIMEOUT)
        if code != 200:
            record("B3: live compare (200)", "FAIL",
                   f"code={code} {body.decode('utf-8', 'replace')[:120]}")
            return 1
        c3 = json.loads(body)
        ap = c3.get("answer_plain") or ""
        ar = c3.get("answer_rag") or ""
        ch3 = c3.get("chunks") or []
        if not ap.strip() or not ar.strip():
            record("B3: live compare (оба ответа non-empty)", "FAIL",
                   f"plain={len(ap)}B rag={len(ar)}B")
            return 1
        err3 = ("Ошибка" in ap) or ("Ошибка" in ar)
        if err3:
            record("B3: live compare (LLM arms)", "SKIP",
                   f"LLM-ошибка в руке (best-effort): "
                   f"plain={ap[:80]!r} rag={ar[:80]!r}")
            return 0
        b3 = bool(ch3) and all(
            key in ch3[0] for key in
            ("chunk_id", "source", "file", "section", "score", "text"))
        record("B3: live compare «Какой телефон был у героя?» — 200, "
               "оба ответа non-empty, chunks non-empty",
               "PASS" if b3 else "FAIL",
               f"plain={len(ap)}B rag={len(ar)}B chunks={len(ch3)} "
               f"top={ch3[0].get('file') if ch3 else None}")
        if not b3:
            return 1

        # B4: easter-egg-факт в answer_rag — best-effort: нет факта =
        # WARNING (PASS), НЕ FAIL (semantics модели не гарантируем)
        found = EGG_FACT.casefold() in ar.casefold()
        record("B4: easter-egg «IPhone 17Promax» (casefold) в answer_rag "
               "(best-effort)",
               "PASS",
               "факт в ответе" if found else
               "WARNING: факт не найден в answer_rag "
               f"(best-effort, semantics модели не гарантируем): "
               f"{ar[:100]!r}")
        return 0
    finally:
        try:
            if orig_model:
                http("POST", "/api/config", {"model": orig_model}, timeout=10)
        except Exception:
            pass
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
