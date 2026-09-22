"""E2E день 17 «MCP Tool-Loop (LLM-driven)» — гибрид: детерминированное ядро + live.

Пайплайн:
  Part A — детерминированное ядро (в-процессе, без uvicorn, БЕЗ сети):
    1. sys.path → studio/backend; StudioAgent + MCPRegistry (реальный
       launcher) + MemoryStore (tmp-каталог, tempfile).
    2. MCP: reg.add("E2E TM", stdio, [python, task_manager.py]) → connect
       → status connected, tools_count == 2 (реальный subprocess
       task_manager.py: initialize + tools/list).
    3. Fake-LLM (httpx.MockTransport, /chat/completions):
       * non-stream (авто-название) → JSON content "E2E-название";
       * stream БЕЗ role "tool" в messages → SSE: tool-чанк
         get_task_details {"task_id": "TASK-42"} (id call_17) +
         finish_reason "tool_calls" + usage + [DONE];
       * stream С role "tool" → SSE: content "Статус задачи: " + текст
         tool-сообщения (из тела запроса) + finish_reason "stop" +
         usage + [DONE].
    4. store.new_dialogue() + profile decline; ask_stream(«Каков статус
       задачи TASK-42?»).
    5. ASSERT (каждый — record): done (без error); answer содержит
       TASK-42 + in_progress; ровно 1 tool-сообщение (TASK-42,
       in_progress) и ровно 1 assistant с tool_calls (get_task_details,
       args == {"task_id": "TASK-42"}); журнал — 2 записи LLM, в первой
       body `tools` содержит get_task_details.
    6. finally: reg.close_all() + удаление tmp-каталога.

  Part B — live (uvicorn :8101, реальный LLM, best-effort):
    1. Порт 8101: занят — ждать до 10 минут (чужой сервер НЕ убивается),
       всё ещё занят — SKIP (exit 0).
    2. Запуск `python -m uvicorn studio.backend.main:app --port 8101`
       detached из корня репозитория (готовность — GET /api/dialogues).
    3. probe_gpustack() — недостижим → SKIP (exit 0), сервер всё равно
       убивается в finally.
    4. GET /api/models → доступные модели; нет ни одной → FAIL (только
       когда GPustack достижим). Модель чата — первая доступная
       (детерминизм), оригинал конфига восстанавливается в cleanup.
    5. POST /api/mcp/servers (Task Manager: [python, task_manager.py])
       → 201; POST connect (timeout 90) → connected, tools_count 2.
    6. POST /api/dialogues → 201; POST /api/profile/action decline.
    7. POST /api/chat «Каков статус задачи TASK-42?» (timeout 330) →
       SSE done.
    8. GET /api/dialogues/{id}: сообщений role "tool" НЕТ → SKIP «модель
       не вызвала инструмент (best-effort)», НЕ FAIL. Есть — PASS, если
       (одна из tool-сообщений) содержит "TASK-42" И done.answer содержит
       "in_progress" или "TASK-42"; несовпадение сценария — SKIP
       (поведение модели, не баг скрипта).
    9. cleanup (ВСЕГДА в finally): DELETE своего диалога и MCP-сервера,
       восстановление модели конфига, stop_server (taskkill /PID /T /F
       на Windows, проверка закрытия порта).

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
PORT = 8101  # 8100 занят e2e_studio.py
BASE = f"http://{HOST}:{PORT}"
WAIT_PORT_BUSY_MAX = 600   # 10 минут ожидания освобождения порта
WAIT_PORT_BUSY_STEP = 10
WAIT_SERVER_UP_MAX = 90
CHAT_TIMEOUT = 330
TASK_MANAGER = os.path.join(REPO, "studio", "mcp_servers", "task_manager.py")

# Windows-консоль может быть cp1251 — выводим UTF-8, чтобы «→» не падало.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

RESULTS = []  # (step, status, detail)


def log(msg):
    print(f"[e2e-day17] {msg}", flush=True)


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


def _fake_llm_handler(request) -> "object":
    """Fake-LLM: non-stream (авто-название) → JSON; stream без role "tool"
    → SSE с tool-call get_task_details(TASK-42); stream с role "tool" →
    SSE content «Статус задачи: <текст tool>» (+ usage + [DONE])."""
    import httpx
    payload = json.loads(request.content)
    if "stream" not in payload:
        return httpx.Response(200, json={"choices": [
            {"message": {"content": "E2E-название"}}]})
    msgs = payload.get("messages") or []
    tool_msgs = [m for m in msgs if m.get("role") == "tool"]
    if tool_msgs:
        tool_text = str(tool_msgs[-1].get("content", ""))
        return httpx.Response(200, content=_sse([
            _delta_chunk("Статус задачи: " + tool_text),
            _stop_chunk(),
            "[DONE]",
        ]).encode("utf-8"))
    tc = {"index": 0, "id": "call_17", "type": "function",
          "function": {"name": "get_task_details",
                       "arguments": "{\"task_id\": \"TASK-42\"}"}}
    return httpx.Response(200, content=_sse([
        {"id": "c1", "object": "chat.completion.chunk",
         "choices": [{"index": 0, "delta": {"tool_calls": [tc]},
                      "finish_reason": None}]},
        {"id": "c1", "object": "chat.completion.chunk",
         "choices": [{"index": 0, "delta": {},
                      "finish_reason": "tool_calls"}],
         "usage": _E2E_USAGE},
        "[DONE]",
    ]).encode("utf-8"))


def part_a() -> bool:
    """Детерминированное ядро: всегда выполняется, должно PASSнуть."""
    log("=== Part A: детерминированное ядро (fake LLM + реальный MCP-процесс) ===")
    import httpx
    sys.path.insert(0, os.path.join(REPO, "studio", "backend"))
    from agent import StudioAgent
    from mcp import MCPRegistry
    from memory import MemoryStore

    tmp = tempfile.mkdtemp(prefix="e2e_day17_")
    reg = None
    failed = False
    try:
        store = MemoryStore(tmp)
        reg = MCPRegistry(store, timeout=30)  # реальный (default) launcher
        rec = reg.add("E2E TM", "stdio", command=[sys.executable, TASK_MANAGER])
        sid = rec["id"]
        view = reg.connect(sid)
        ok = (view.get("status") == "connected"
              and view.get("tools_count") == 2)
        record("A: MCP connect (connected, 2 tools)",
               "PASS" if ok else "FAIL",
               f"status={view.get('status')} tools_count={view.get('tools_count')} "
               f"error={view.get('error')}")
        if not ok:
            return False

        agent = StudioAgent(tmp, base_url="https://mock.local/v1", api_key="k",
                            client=httpx.Client(
                                transport=httpx.MockTransport(_fake_llm_handler)),
                            mcp=reg)
        d = store.new_dialogue()
        store.profile_action(d["id"], "decline")
        events = list(agent.ask_stream(d["id"], "Каков статус задачи TASK-42?"))

        types = [e.get("type") for e in events]
        last = events[-1] if events else {}
        done_ok = (last.get("type") == "done"
                   and "error" not in types)
        record("A: ask_stream (done, без error)",
               "PASS" if done_ok else "FAIL", f"types={types}")

        answer = last.get("answer", "")
        ans_ok = "TASK-42" in answer and "in_progress" in answer
        record("A: answer содержит TASK-42 + in_progress",
               "PASS" if ans_ok else "FAIL", f"answer[:80]={answer[:80]!r}")

        msgs = agent.store.get_messages(d["id"])
        tool_msgs = [m for m in msgs if m.get("role") == "tool"]
        tm_ok = (len(tool_msgs) == 1
                 and "TASK-42" in str(tool_msgs[0].get("content", ""))
                 and "in_progress" in str(tool_msgs[0].get("content", "")))
        record("A: ровно 1 tool-сообщение (TASK-42, in_progress)",
               "PASS" if tm_ok else "FAIL", f"tool_msgs={len(tool_msgs)}")

        asst_tc = [m for m in msgs
                   if m.get("role") == "assistant" and m.get("tool_calls")]
        tc_ok = False
        if len(asst_tc) == 1:
            tc = asst_tc[0]["tool_calls"][0]
            tc_ok = (tc.get("function", {}).get("name") == "get_task_details"
                     and json.loads(tc.get("function", {}).get("arguments", "{}"))
                     == {"task_id": "TASK-42"})
        record("A: ровно 1 assistant с tool_calls (get_task_details, TASK-42)",
               "PASS" if tc_ok else "FAIL", f"asst_tc={len(asst_tc)}")

        rl = agent.requests_list()
        tools_ok = False
        if len(rl) == 2:
            full = agent.requests_get(rl[0]["id"]) or {}
            tools = (full.get("request") or {}).get("tools") or []
            names = [t.get("function", {}).get("name") for t in tools
                     if isinstance(t, dict)]
            tools_ok = "get_task_details" in names and "create_task" in names
        record("A: журнал (2 записи; tools с get_task_details в первой)",
               "PASS" if tools_ok else "FAIL", f"requests={len(rl)}")

        failed = (not ok) or (not done_ok) or (not ans_ok) or (not tm_ok) \
            or (not tc_ok) or (not tools_ok)
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
    """Live: uvicorn :8101 + реальный LLM (best-effort: SKIP, не FAIL,
    когда поведение модели не совпало со сценарием)."""
    log("=== Part B: live (uvicorn :8101, реальный LLM, best-effort) ===")
    proc = None
    dlg_id = None
    mcp_sid = None
    orig_model = None
    try:
        busy = wait_port_free()
        if busy:
            record("B: port 8101", "SKIP", busy)
            return 0

        proc = start_server()
        if proc is None:
            record("B: uvicorn up (:8101)", "FAIL",
                   "server did not come up on port 8101")
            return 1
        record(f"B: uvicorn up (port {PORT})", "PASS")

        skip_reason = probe_gpustack()
        if skip_reason:
            record("B: tool-loop live (реальный LLM)", "SKIP",
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

        # MCP: Task Manager (реальный python-subprocess)
        code, body, _ = http("POST", "/api/mcp/servers",
                             {"name": "E2E TM", "type": "stdio",
                              "command": [sys.executable, TASK_MANAGER]},
                             timeout=30)
        srv = json.loads(body).get("server", {}) if code == 201 else {}
        if code != 201 or not srv.get("id"):
            record("B: POST /api/mcp/servers (Task Manager)", "FAIL",
                   f"code={code}")
            return 1
        mcp_sid = srv["id"]
        record("B: POST /api/mcp/servers (201)", "PASS", mcp_sid)

        code, body, _ = http("POST", f"/api/mcp/servers/{mcp_sid}/connect",
                             timeout=90)
        view = json.loads(body).get("server", {}) if code == 200 else {}
        if (code != 200 or view.get("status") != "connected"
                or view.get("tools_count") != 2):
            record("B: MCP connect (connected, 2 tools)", "FAIL",
                   f"code={code} status={view.get('status')} "
                   f"tools_count={view.get('tools_count')} "
                   f"error={view.get('error')}")
            return 1
        record("B: MCP connect (connected, 2 tools)", "PASS")

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

        # Чат: реальный LLM + tool-loop (best-effort)
        code, raw, _ = http("POST", "/api/chat",
                            {"dialogue_id": dlg_id,
                             "message": "Каков статус задачи TASK-42?"},
                            timeout=CHAT_TIMEOUT)
        deltas, dones, errors = parse_sse(raw)
        if code != 200 or not dones or errors:
            detail = errors[0] if errors else f"code={code} dones={len(dones)}"
            record("B: chat SSE (done)", "FAIL", detail)
            return 1
        record(f"B: chat SSE ({len(deltas)} deltas, done)", "PASS")

        code, body, _ = http("GET", f"/api/dialogues/{dlg_id}")
        dlg_full = json.loads(body).get("dialogue", {}) if code == 200 else {}
        tool_msgs = [m for m in dlg_full.get("messages", [])
                     if m.get("role") == "tool"]
        answer = dones[-1].get("answer", "") if dones else ""
        if not tool_msgs:
            record("B: tool-loop live (role tool в диалоге)", "SKIP",
                   "модель не вызвала инструмент (best-effort)")
            return 0
        tool_ok = any("TASK-42" in str(m.get("content", ""))
                      for m in tool_msgs)
        ans_ok = ("in_progress" in answer) or ("TASK-42" in answer)
        if tool_ok and ans_ok:
            record(f"B: tool-loop live ({len(tool_msgs)} tool-msg, done.answer)",
                   "PASS", f"answer[:80]={answer[:80]!r}")
            return 0
        record("B: tool-loop live (роль tool, совпадение сценария)", "SKIP",
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
