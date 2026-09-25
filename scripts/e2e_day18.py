"""E2E день 18 «Планировщик и фоновые задачи (digest 24/7)» — гибрид:
детерминированное ядро + live.

Пайплайн:
  Part A — детерминированное ядро (в-процессе, без uvicorn, БЕЗ сети):
    1. sys.path → studio/backend + studio; MCPRegistry (реальный launcher)
       + MemoryStore (tmp-каталог); env DIGEST_DATA_DIR → tmp/digests.
    2. MCP: 4× reg.add (weather/news/digest_make/digest_read — одиночные
       серверы, по 1 тулу) + connect → status connected, tools_count == 1
       у каждого (реальные subprocess-ы: initialize + tools/list;
       env DIGEST_DATA_DIR — только у digest_make и digest_read).
    3. reg.call_tool make_digest {} → isError False, id+generated_at,
       last-digest.json на диске в tmp/digests.
    4. reg.call_tool get_latest_digest {} → source "local", id совпадает.
    5. in-process: collector.collect_digest (инжект-фетчеры, FIXED_NOW)
       → id "digest-20260923-1200", generated_at "2026-09-23T12:00:00Z".
    6. in-process: collector.save_digest x2 → history.json: 2 записи.
    7. CLI subprocess: `python studio/collector.py --out tmp/cli_out`
       → exit 0, last-digest.json создан.
    8. finally: reg.close_all() + удаление tmp-каталога.

  Part B — live (uvicorn :8102, реальный LLM, best-effort):
    1. Порт 8102: занят — ждать до 10 минут (чужой сервер НЕ убивается),
       всё ещё занят — SKIP (exit 0).
    2. Запуск `python -m uvicorn studio.backend.main:app --port 8102`
       detached из корня репозитория (готовность — GET /api/dialogues).
    3. probe_gpustack() — недостижим → SKIP (exit 0), сервер всё равно
       убивается в finally.
    4. GET /api/models → доступные модели; нет ни одной → FAIL (только
       когда GPustack достижим). Модель чата — первая доступная
       (детерминизм), оригинал конфига восстанавливается в cleanup.
    5. POST /api/mcp/servers (Digest Read: [python, digest_read.py])
       → 201; POST connect (timeout 120) → connected, tools_count 1.
    6. POST /api/dialogues → 201; POST /api/profile/action decline.
    7. POST /api/chat «Покажи последнюю сводку (дайджест)» (timeout 330)
       → SSE done.
    8. GET /api/dialogues/{id}: сообщений role "tool" НЕТ → SKIP «модель
       не вызвала инструмент (best-effort)», НЕ FAIL. Есть — PASS, если
       (одна из tool-сообщений) содержит "generated_at" или "source" И
       done.answer непустой; несовпадение сценария — SKIP.
    9. cleanup (ВСЕГДА в finally): DELETE своего диалога и MCP-сервера,
       восстановление модели конфига, stop_server.

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
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
HOST = "127.0.0.1"
PORT = 8102  # 8100 — e2e_studio.py, 8101 — e2e_day17.py
BASE = f"http://{HOST}:{PORT}"
WAIT_PORT_BUSY_MAX = 600   # 10 минут ожидания освобождения порта
WAIT_PORT_BUSY_STEP = 10
WAIT_SERVER_UP_MAX = 90
CHAT_TIMEOUT = 330
MCP_DIR = os.path.join(REPO, "studio", "mcp_servers")
WEATHER_PY = os.path.join(MCP_DIR, "weather.py")
NEWS_PY = os.path.join(MCP_DIR, "news.py")
DIGEST_MAKE_PY = os.path.join(MCP_DIR, "digest_make.py")
DIGEST_READ_PY = os.path.join(MCP_DIR, "digest_read.py")

# Windows-консоль может быть cp1251 — выводим UTF-8, чтобы «→» не падало.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

RESULTS = []  # (step, status, detail)


def log(msg):
    print(f"[e2e-day18] {msg}", flush=True)


def record(step, status, detail=""):
    RESULTS.append((step, status, detail))
    log(f"{status:5s} {step}" + (f" — {detail}" if detail else ""))


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


def http(method: str, path: str, body=None, timeout=30) -> tuple[int, bytes, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def request_long(method: str, path: str, body=None,
                 timeout: int = CHAT_TIMEOUT) -> tuple[int, bytes, bool]:
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


def probe_gpustack() -> str | None:
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


def wait_port_free() -> str | None:
    if not port_open():
        return None
    log(f"port {PORT} busy — waiting up to 10 min (not killing foreign server)")
    deadline = time.time() + WAIT_PORT_BUSY_MAX
    while time.time() < deadline:
        time.sleep(WAIT_PORT_BUSY_STEP)
        if not port_open():
            return None
    return f"port {PORT} still busy after {WAIT_PORT_BUSY_MAX}s"


def start_server() -> subprocess.Popen | None:
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


def _cleanup(dlg_id: str | None, mcp_sid: str | None,
             orig_model: str | None) -> None:
    """Убрать артефакты самого e2e (не трогая данные пользователя)."""
    try:
        if dlg_id:
            http("DELETE", f"/api/dialogues/{dlg_id}", timeout=10)
        if mcp_sid:
            http("DELETE", f"/api/mcp/servers/{mcp_sid}", timeout=30)
        if orig_model:
            http("POST", "/api/config", {"model": orig_model}, timeout=10)
    except Exception:
        pass


def stop_server(proc: subprocess.Popen | None) -> None:
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


def parse_sse(raw: bytes) -> tuple[list, list, list]:
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


def part_a() -> bool:
    """Детерминированное ядро: всегда выполняется, должно PASSнуть."""
    log("=== Part A: детерминированное ядро (collector + реальный MCP-процесс) ===")
    sys.path.insert(0, os.path.join(REPO, "studio", "backend"))
    sys.path.insert(0, os.path.join(REPO, "studio"))
    import collector  # noqa: E402
    from mcp import MCPRegistry  # noqa: E402
    from memory import MemoryStore  # noqa: E402

    idx = len(RESULTS)  # A-записи этого прогона
    tmp = tempfile.mkdtemp(prefix="e2e_day18_")
    data_dir = os.path.join(tmp, "digests")
    os.makedirs(data_dir, exist_ok=True)
    reg = None
    try:
        # A1: MCP connect ×4 — connected, 1 tool each (реальные subprocess-ы)
        store = MemoryStore(os.path.join(tmp, "data"))
        reg = MCPRegistry(store, timeout=120)
        adds = [
            ("Weather", WEATHER_PY, None),
            ("News", NEWS_PY, None),
            ("Digest Make", DIGEST_MAKE_PY, {"DIGEST_DATA_DIR": data_dir}),
            ("Digest Read", DIGEST_READ_PY, {"DIGEST_DATA_DIR": data_dir}),
        ]
        sids = {}
        views = {}
        for name, path, senv in adds:
            rec = reg.add(name, "stdio", command=[sys.executable, path],
                          env=senv)
            sids[name] = rec["id"]
            views[name] = reg.connect(rec["id"])
        ok = all(v.get("status") == "connected" and v.get("tools_count") == 1
                 for v in views.values())
        record("A: MCP connect ×4 (connected, 1 tool each)",
               "PASS" if ok else "FAIL",
               " ".join(f"{n}={views[n].get('tools_count')}"
                        f"/{views[n].get('status')}" for n, _, _ in adds))

        # A2: make_digest — ok, id+generated_at, last-digest.json на диске
        r = reg.call_tool(sids["Digest Make"], "make_digest", {})
        d = json.loads(r["content"][0]["text"])
        ok = (r.get("isError") is not True and "id" in d
              and "generated_at" in d
              and os.path.exists(os.path.join(data_dir,
                                              "last-digest.json")))
        record("A: make_digest (id+generated_at, last-digest.json)",
               "PASS" if ok else "FAIL", f"digest={str(d)[:80]}")

        # A3: get_latest_digest — source=local, id совпадает
        g = reg.call_tool(sids["Digest Read"], "get_latest_digest", {})
        lg = json.loads(g["content"][0]["text"])
        ok = (lg.get("source") == "local"
              and lg.get("digest", {}).get("id") == d.get("id"))
        record("A: get_latest_digest (source=local, id совпадает)",
               "PASS" if ok else "FAIL", f"lg={str(lg)[:80]}")

        # A4: in-process collect_digest (инжект-фетчеры) — офлайн-схема
        fake_w = lambda city: {"city": "Самара", "temp_c": 20.0,
                               "feels_like_c": 18.0,
                               "description": "Ясно", "wind_ms": 2.0}
        fake_n = lambda src: [{"title": "t", "url": "https://x/" + src}]
        dig = collector.collect_digest(
            now=datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc),
            fetch_weather=fake_w, fetch_news=fake_n)
        ok = (dig["id"] == "digest-20260923-1200"
              and dig["generated_at"] == "2026-09-23T12:00:00Z")
        record("A: collect_digest (deterministic id/generated_at)",
               "PASS" if ok else "FAIL", f"id={dig['id']}")

        # A5: save_digest x2 — history.json: 2 записи
        # (каталог сбрасываем: A2-subprocess уже положил туда один дайджест)
        for f in ("last-digest.json", "history.json"):
            pth = os.path.join(data_dir, f)
            if os.path.exists(pth):
                os.remove(pth)
        collector.save_digest(dig, data_dir)
        collector.save_digest(dig, data_dir)
        hist = json.load(open(os.path.join(data_dir, "history.json"),
                              encoding="utf-8"))
        ok = len(hist) == 2
        record("A: save_digest x2 (history=2)",
               "PASS" if ok else "FAIL", f"len={len(hist)}")

        # A6: CLI (subprocess) — exit 0, last-digest.json
        cli_out = os.path.join(tmp, "cli_out")
        p = subprocess.run(
            [sys.executable, os.path.join(REPO, "studio", "collector.py"),
             "--out", cli_out],
            capture_output=True, timeout=150, cwd=REPO)
        ok = (p.returncode == 0
              and os.path.exists(os.path.join(cli_out,
                                              "last-digest.json")))
        record("A: collector CLI (exit 0, last-digest.json)",
               "PASS" if ok else "FAIL",
               f"rc={p.returncode} err={p.stderr[:80]!r}")

        failed = any(s != "PASS" for _, s, _ in RESULTS[idx:])
    except Exception as e:
        record("A: unexpected exception", "FAIL", repr(e))
        failed = True
    finally:
        if reg is not None:
            try:
                reg.close_all()
            except Exception:
                pass
        shutil.rmtree(tmp, ignore_errors=True)
    return not failed


def part_b() -> int:
    """Live: uvicorn :8102 + реальный LLM (best-effort: SKIP, не FAIL,
    когда поведение модели не совпало со сценарием)."""
    log("=== Part B: live (uvicorn :8102, реальный LLM, best-effort) ===")
    proc = None
    dlg_id = None
    mcp_sid = None
    orig_model = None
    try:
        busy = wait_port_free()
        if busy:
            record("B: port 8102", "SKIP", busy)
            return 0

        proc = start_server()
        if proc is None:
            record("B: uvicorn up (:8102)", "FAIL",
                   "server did not come up on port 8102")
            return 1
        record(f"B: uvicorn up (port {PORT})", "PASS")

        skip_reason = probe_gpustack()
        if skip_reason:
            record("B: digest live (реальный LLM)", "SKIP",
                   f"GPustack недоступен: {skip_reason}")
            return 0

        code, body, _ = http("GET", "/api/models", timeout=120)
        if code != 200:
            record("B: GET /api/models", "FAIL", f"code={code}")
            return 1
        avail = json.loads(body).get("models", [])
        if not avail:
            record("B: GET /api/models", "FAIL", "нет доступных моделей")
            return 1
        record(f"B: модели ({len(avail)}, чат: {avail[0]['id']})", "PASS")

        code, body, _ = http("GET", "/api/config")
        orig_model = json.loads(body).get("model") if code == 200 else None
        http("POST", "/api/config", {"model": avail[0]["id"]}, timeout=30)

        # MCP: Digest Read (реальный python-subprocess)
        code, body, _ = http("POST", "/api/mcp/servers",
                             {"name": "Digest Read", "type": "stdio",
                              "command": [sys.executable, DIGEST_READ_PY]},
                             timeout=30)
        srv = json.loads(body).get("server", {}) if code == 201 else {}
        if code != 201 or not srv.get("id"):
            record("B: POST /api/mcp/servers (Digest Read)", "FAIL",
                   f"code={code}")
            return 1
        mcp_sid = srv["id"]
        record("B: POST /api/mcp/servers (201)", "PASS", mcp_sid)

        code, body, _ = http("POST", f"/api/mcp/servers/{mcp_sid}/connect",
                             timeout=120)
        view = json.loads(body).get("server", {}) if code == 200 else {}
        if (code != 200 or view.get("status") != "connected"
                or view.get("tools_count") != 1):
            record("B: MCP connect (connected, 1 tool)", "FAIL",
                   f"code={code} status={view.get('status')} "
                   f"tools_count={view.get('tools_count')} "
                   f"error={view.get('error')}")
            return 1
        record("B: MCP connect (connected, 1 tool)", "PASS")

        # Диалог + отказ от профиля (pending-гейт дня 12)
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

        # Чат: реальный LLM + tool-loop (best-effort). Стрим длинный
        # (tool-loop + сетевые MCP-вызовы) — request_long с retry.
        code, raw, full = request_long(
            "POST", "/api/chat",
            {"dialogue_id": dlg_id,
             "message": "Покажи последнюю сводку (дайджест)"})
        deltas, dones, errors = parse_sse(raw)
        if code != 200 or not dones or errors:
            detail = (errors[0] if errors
                      else f"code={code} dones={len(dones)} "
                           f"full={full} partial={len(raw)}B")
            record("B: chat SSE (done)", "FAIL", detail)
            return 1
        record(f"B: chat SSE ({len(deltas)} deltas, done)", "PASS",
               "" if full else "(done дошёл до обрыва стрима)")

        code, body, _ = http("GET", f"/api/dialogues/{dlg_id}")
        dlg_full = json.loads(body).get("dialogue", {}) if code == 200 else {}
        tool_msgs = [m for m in dlg_full.get("messages", [])
                     if m.get("role") == "tool"]
        answer = dones[-1].get("answer", "") if dones else ""
        if not tool_msgs:
            record("B: digest live (role tool в диалоге)", "SKIP",
                   "модель не вызвала инструмент (best-effort)")
            return 0
        tool_ok = any(("generated_at" in str(m.get("content", ""))
                       or "source" in str(m.get("content", "")))
                      for m in tool_msgs)
        ans_ok = bool(answer.strip())
        if tool_ok and ans_ok:
            record(f"B: digest live ({len(tool_msgs)} tool-msg, done.answer)",
                   "PASS", f"answer[:80]={answer[:80]!r}")
            return 0
        record("B: digest live (роль tool, совпадение сценария)", "SKIP",
               f"tool_ok={tool_ok} ans_ok={ans_ok} (поведение модели, "
               f"best-effort); answer[:80]={answer[:80]!r}")
        return 0
    finally:
        try:
            _cleanup(dlg_id, mcp_sid, orig_model)
        finally:
            stop_server(proc)


def main() -> int:
    load_dotenv()
    if not part_a():
        return 1
    return part_b()


if __name__ == "__main__":
    sys.exit(main())
