"""E2E день 20 «Orchestration MCP: 10 single-tool серверов, кросс-серверный флоу»
— гибрид: детерминированное ядро + live.

Part A (MUST PASS, офлайн):
  1. sys.path → studio/backend; StudioAgent + MCPRegistry + MemoryStore
     (tmp-каталог, tempfile).
  2. MCP: 10× reg.add (task_create/weather/news/digest_make/digest_read/
     habr_news/digest_search/digest_summarize/file_save/task_get — по 1
     тулу) + connect → status connected, tools_count == 1 у каждого
     (10 реальных subprocess-ов: initialize + tools/list).
  3. Seed: дайджест «Самара» в tmp search-каталог (оффлайн для search;
     network-зависимые тулы — get_habr_news/get_weather/get_news/
     make_digest — могут деградировать в isError, флоу не рвётся).
  4. Fake-LLM (httpx.MockTransport, /chat/completions) — скриптит
     10-шаговый флоу по числу role "tool" сообщений (stage 0..9):
     create_task → get_weather → get_news → make_digest →
     get_latest_digest → get_habr_news → search → summarize →
     saveToFile → get_task_details; данные цепочки берутся из
     предыдущих tool-сообщений (передача между серверами).
     Non-stream (авто-название) → JSON content "E2E-название".
  5. ASSERT (каждый — record): done (без error); ровно 10 tool-
     сообщений {server}__{tool} в порядке FLOW; маршрутизация
     (spy на reg.call_tool) == FLOW; передача данных между серверами
     (summarize.text=search.text, saveToFile.content=summarize.summary,
     task_get.task_id=create_task.id, digest_read.id=digest_make.id);
     day20_report.pdf (%PDF-1.4 + /ToUnicode) в tmp out; созданная
     задача в tmp tasks.json; дайджест make на диске (last-digest.json),
     read.id == make.id; кап — зацикленный fake-LLM → SSE error
     «Tool-loop: превышен лимит итераций (15)».
  6. finally: reg.close_all() + удаление tmp-каталога.

Part B — live (uvicorn :8104, реальный LLM, best-effort):
  1. Порт 8104: занят — ждать до 10 минут (чужой сервер НЕ убивается),
     всё ещё занят — SKIP (exit 0).
  2. Запуск `python -m uvicorn studio.backend.main:app --port 8104`
     detached из корня репозитория (готовность — GET /api/dialogues).
  3. probe_gpustack() — недостижим → SKIP (exit 0), сервер убивается
     в finally.
  4. GET /api/models → доступные модели; нет ни одной → FAIL (только
     когда GPustack достижим). Модель чата — первая доступная
     (детерминизм), оригинал конфига восстанавливается в cleanup.
  5. MCP: 10 серверов — дефолты реестра (Task 7), POST НЕ нужен:
     GET /api/mcp/servers → найти по именам FLOW → POST connect ×10
     (timeout 120) → connected, tools_count 1 у каждого. FAIL только
     на инфраструктурном сбое (сервер не поднялся / connect error).
  6. POST /api/dialogues → 201; POST /api/profile/action decline.
  7. POST /api/chat CHAT_MSG (timeout 330, request_long с retry).
  8. Best-effort: ≥ 5 tool-сообщений И ≥ 3 разных сервера (префиксы
     name.split("__")[0]) И done → PASS, иначе SKIP «модель не довела
     10-шаговую цепочку (best-effort)», НЕ FAIL.
  9. cleanup (ВСЕГДА в finally): DELETE своего диалога; 10 дефолтных
     серверов НЕ удаляем (они из реестра, не артефакт e2e);
     восстановление модели конфига, stop_server (taskkill /PID /T /F
     на Windows, проверка закрытия порта).

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

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
HOST = "127.0.0.1"
PORT = 8104  # 8100 — e2e_studio.py, 8101 — day17, 8102 — day18, 8103 — day19
BASE = f"http://{HOST}:{PORT}"
WAIT_PORT_BUSY_MAX = 600   # 10 минут ожидания освобождения порта
WAIT_PORT_BUSY_STEP = 10
WAIT_SERVER_UP_MAX = 90
CHAT_TIMEOUT = 330
FLOW = [("task_create", "create_task"), ("weather", "get_weather"),
        ("news", "get_news"), ("digest_make", "make_digest"),
        ("digest_read", "get_latest_digest"), ("habr_news", "get_habr_news"),
        ("digest_search", "search"), ("digest_summarize", "summarize"),
        ("file_save", "saveToFile"), ("task_get", "get_task_details")]
PDF_NAME = "day20_report.pdf"
CHAT_MSG = ("Создай задачу, узнай погоду в Самаре, собери дайджест, "
            "найди в дайджестах про Самара, суммаризируй и сохрани в PDF")
# Part A «без сети» (краткий §4): сети на машине разработки быть может,
# поэтому MCP-подпроцессы получают мёртвый прокси — urllib в collector.py /
# habr_news.py падает мгновенно (ECONNREFUSED), _mcp_base превращает это в
# isError-JSON («Habr недоступен» и т.п.), флоу детерминирован. Без этого
# реальный контент Habr (U+2011) ронял subprocess на cp1251-stdout
# (UnicodeEncodeError → «MCP-сервер закрыл соединение»).
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
    print(f"[e2e-day20] {msg}", flush=True)


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


def _cleanup(dlg_id, mcp_sids, orig_model) -> None:
    """Убрать артефакты самого e2e (не трогая данные пользователя).
    День 20: mcp_sids всегда [] — 10 дефолтных серверов НЕ удаляем."""
    try:
        if dlg_id:
            http("DELETE", f"/api/dialogues/{dlg_id}", timeout=10)
        for sid in mcp_sids or []:
            http("DELETE", f"/api/mcp/servers/{sid}", timeout=30)
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


def _tool_response(tc: dict) -> "object":
    """SSE-ответ с одним tool_call: чанк delta.tool_calls (index) +
    finish_reason "tool_calls" + usage + [DONE] (формат e2e_day17)."""
    import httpx
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


def _tc(name: str, arguments: dict, call_id: str) -> dict:
    return {"index": 0, "id": call_id, "type": "function",
            "function": {"name": name,
                         "arguments": json.dumps(arguments,
                                                 ensure_ascii=False)}}


def _last_tool_text(msgs: list) -> str:
    tool_msgs = [m for m in msgs if m.get("role") == "tool"]
    return str(tool_msgs[-1].get("content", "")) if tool_msgs else ""


def pick(tools_list, suffix):
    for t in tools_list or []:
        n = t.get("function", {}).get("name", "")
        if n == suffix or n.endswith("__" + suffix):
            return n
    raise AssertionError("tool %r not in payload tools" % suffix)


def _fake_llm_day20(request):
    """Скриптит флоу §6: stage N = N tool-сообщений в запросе.
    Данные цепочки берутся из предыдущих tool-сообщений (передача)."""
    import httpx
    payload = json.loads(request.content)
    if "stream" not in payload:
        return httpx.Response(200, json={"choices": [
            {"message": {"content": "E2E-название"}}]})
    msgs = payload.get("messages") or []
    tools = payload.get("tools") or []
    stage = sum(1 for m in msgs if m.get("role") == "tool")
    t = lambda k: json.loads(_last_tool_text(msgs)) if k == "last" else None
    if stage == 0:
        return _tool_response(_tc(pick(tools, "create_task"),
                                  {"title": "E2E day20: 10-серверный флоу",
                                   "description": "авто"},
                                  "call_20_0"))
    if stage == 1:
        return _tool_response(_tc(pick(tools, "get_weather"),
                                  {"city": "Самара"}, "call_20_1"))
    if stage == 2:
        return _tool_response(_tc(pick(tools, "get_news"), {}, "call_20_2"))
    if stage == 3:
        return _tool_response(_tc(pick(tools, "make_digest"),
                                  {"city": "Самара"}, "call_20_3"))
    if stage == 4:
        return _tool_response(_tc(pick(tools, "get_latest_digest"), {},
                                  "call_20_4"))
    if stage == 5:
        return _tool_response(_tc(pick(tools, "get_habr_news"),
                                  {"topics": ["testing", "ai"], "limit": 3},
                                  "call_20_5"))
    if stage == 6:
        return _tool_response(_tc(pick(tools, "search"),
                                  {"query": "Самара"}, "call_20_6"))
    if stage == 7:
        found = {}
        try:
            found = json.loads(_last_tool_text(msgs))
        except ValueError:
            pass
        return _tool_response(_tc(pick(tools, "summarize"),
                                  {"text": found.get("text") or "",
                                   "max_points": 3},
                                  "call_20_7"))
    if stage == 8:
        summ = {}
        try:
            summ = json.loads(_last_tool_text(msgs))
        except ValueError:
            pass
        return _tool_response(_tc(pick(tools, "saveToFile"),
                                  {"filename": PDF_NAME,
                                   "content": summ.get("summary") or "",
                                   "format": "pdf"},
                                  "call_20_8"))
    if stage == 9:
        first = [m for m in msgs if m.get("role") == "tool"][0]
        created = {}
        try:
            created = json.loads(str(first.get("content", "")))
        except ValueError:
            pass
        return _tool_response(_tc(pick(tools, "get_task_details"),
                                  {"task_id": created.get("id") or ""},
                                  "call_20_9"))
    return httpx.Response(200, content=_sse([
        _delta_chunk("Готово: 10-серверный флоу выполнен, отчёт "
                     f"{PDF_NAME} сохранён."),
        _stop_chunk(),
        "[DONE]",
    ]).encode("utf-8"))


def _loop_llm_day20(request):
    """Зацикленный LLM: всегда get_weather (для теста капа)."""
    import httpx
    payload = json.loads(request.content)
    if "stream" not in payload:
        return httpx.Response(200, json={"choices": [
            {"message": {"content": "E2E-название"}}]})
    tools = payload.get("tools") or []
    stage = sum(1 for m in (payload.get("messages") or [])
                if m.get("role") == "tool")
    return _tool_response(_tc(pick(tools, "get_weather"),
                              {"city": "Самара"},
                              "call_loop_%d" % stage))


def part_a() -> bool:
    log("=== Part A: 10 серверов, 10-шаговый флоу (fake LLM, без сети) ===")
    import httpx
    sys.path.insert(0, os.path.join(REPO, "studio", "backend"))
    from agent import StudioAgent
    from mcp import MCPRegistry
    from memory import MemoryStore

    idx = len(RESULTS)
    tmp = tempfile.mkdtemp(prefix="e2e_day20_")
    digests = os.path.join(tmp, "digests")
    out_dir = os.path.join(tmp, "out")
    tasks_file = os.path.join(tmp, "tasks.json")
    for d in (digests, out_dir):
        os.makedirs(d, exist_ok=True)
    reg = None
    try:
        store = MemoryStore(tmp)
        reg = MCPRegistry(store, timeout=120, env=dict(os.environ))
        font = r"C:\Windows\Fonts\arial.ttf" if os.name == "nt" else ""
        envs = {
            "digest_make": {"DIGEST_DATA_DIR": digests},
            "digest_read": {"DIGEST_DATA_DIR": digests},
            "digest_search": {"PIPELINE_SEARCH_DIR": digests},
            "task_create": {"TASKS_FILE": tasks_file},
            "task_get": {"TASKS_FILE": tasks_file},
            "file_save": {"PIPELINE_OUT_DIR": out_dir},
        }
        if font and os.path.exists(font):
            envs["file_save"]["PIPELINE_FONT_PATH"] = font
        # Part A без сети: мёртвый прокси для всех 10 подпроцессов
        # (см. комментарий у _NO_NET).
        for _ev in envs.values():
            _ev.update(_NO_NET)

        # A1: 10× connect, у каждого tools_count == 1
        sids = {}
        all_ok = True
        for server, _tool in FLOW:
            srv = os.path.join(REPO, "studio", "mcp_servers", server + ".py")
            rec = reg.add(server, "stdio", command=[sys.executable, srv],
                          url="",
                          env={**envs.get(server, {}), **_NO_NET},
                          enabled=True)
            sids[server] = rec["id"]
            view = reg.connect(rec["id"])
            ok = (view.get("status") == "connected"
                  and view.get("tools_count") == 1)
            all_ok &= ok
            record(f"A: connect {server} (connected, 1 tool)",
                   "PASS" if ok else "FAIL",
                   f"status={view.get('status')} "
                   f"tools_count={view.get('tools_count')} "
                   f"error={view.get('error')}")
        if not all_ok:
            return False

        # A2: seed — детерминированный дайджест с «Самара» (search оффлайн)
        seed = {"id": "digest-20260925-0100",
                "generated_at": "2026-09-25T01:00:00Z",
                "weather": {"city": "Самара", "temp_c": 19.0,
                            "description": "Ясно", "wind_ms": 2.0},
                "news": {},
                "summary": "Погода Самара: +19.0°C, ясно, слабый ветер."}
        with open(os.path.join(digests, seed["id"] + ".json"), "w",
                  encoding="utf-8") as f:
            json.dump(seed, f, ensure_ascii=False, indent=2)
        record("A: seed digest «Самара» (оффлайн для search)", "PASS")

        # A3: fake-LLM флоу (§6) + spy на call_tool (маршрутизация)
        routed = []  # (server, real_tool, args)
        orig_call = reg.call_tool

        def spy(sid, name, args):
            server = next(s for s, i in sids.items() if i == sid)
            routed.append((server, name, args))
            return orig_call(sid, name, args)
        reg.call_tool = spy

        agent = StudioAgent(tmp, base_url="https://mock.local/v1",
                            api_key="k",
                            client=httpx.Client(
                                transport=httpx.MockTransport(
                                    _fake_llm_day20)),
                            mcp=reg)
        d = store.new_dialogue()
        store.profile_action(d["id"], "decline")
        events = list(agent.ask_stream(d["id"], CHAT_MSG))
        types = [e.get("type") for e in events]
        last = events[-1] if events else {}
        record("A: ask_stream (done, без error)",
               "PASS" if (last.get("type") == "done"
                          and "error" not in types) else "FAIL",
               f"types={types}")

        # A4: порядок — 10 tool-сообщений, имена {server}__{tool}
        msgs = agent.store.get_messages(d["id"])
        tool_msgs = [m for m in msgs if m.get("role") == "tool"]
        expected = [f"{s}__{t}" for s, t in FLOW]
        got = [m.get("name") for m in tool_msgs]
        record("A: порядок — 10 tool-msg {server}__{tool}",
               "PASS" if got == expected else "FAIL", f"got={got}")

        # A5: маршрутизация — (server, real_tool) spy == FLOW, args коррект
        route_ok = [(s, t) for s, t, _ in routed] == FLOW
        record("A: маршрутизация — 10 серверов в порядке §6",
               "PASS" if route_ok else "FAIL",
               f"routed={[(s, t) for s, t, _ in routed]}")

        # A6: передача данных между серверами (цепочка)
        asst_args = {}
        for m in msgs:
            if m.get("role") == "assistant" and m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    fn = tc.get("function", {})
                    try:
                        asst_args[fn.get("name")] = json.loads(
                            fn.get("arguments", "{}"))
                    except ValueError:
                        asst_args[fn.get("name")] = {}
        t = {e[0]: json.loads(str(m.get("content", "")))
             for e, m in zip(routed, tool_msgs)}
        handoff = (
            asst_args.get("digest_summarize__summarize", {}).get("text")
            == (t.get("digest_search") or {}).get("text")
            and asst_args.get("file_save__saveToFile", {}).get("content")
            == (t.get("digest_summarize") or {}).get("summary")
            and asst_args.get("task_get__get_task_details", {})
            .get("task_id") == (t.get("task_create") or {}).get("id")
            and (t.get("digest_read") or {}).get("digest", {}).get("id")
            == (t.get("digest_make") or {}).get("id"))
        record("A: chain — summarize.text=search.text, "
               "saveToFile.content=summarize.summary, "
               "task_get.task_id=create.id, read.id=make.id",
               "PASS" if handoff else "FAIL",
               f"search_text_len={len((t.get('digest_search') or {}).get('text', ''))}"
               f" make_id={(t.get('digest_make') or {}).get('id')!r}")

        # A7: артефакты
        pdf_path = os.path.join(out_dir, PDF_NAME)
        pdf_bytes = b""
        if os.path.exists(pdf_path):
            with open(pdf_path, "rb") as f:
                pdf_bytes = f.read()
        record(f"A: {PDF_NAME} (%PDF-1.4, /ToUnicode)",
               "PASS" if (pdf_bytes.startswith(b"%PDF-1.4")
                          and b"/ToUnicode" in pdf_bytes) else "FAIL",
               f"size={len(pdf_bytes)}")
        tasks_data = {}
        if os.path.exists(tasks_file):
            with open(tasks_file, encoding="utf-8") as f:
                tasks_data = json.load(f)
        created_id = (t.get("task_create") or {}).get("id")
        record("A: tasks.json — созданная задача на диске",
               "PASS" if created_id
               and created_id in tasks_data.get("tasks", {}) else "FAIL",
               f"created_id={created_id!r} "
               f"task_get_status={(t.get('task_get') or {}).get('status')!r}")
        last_digest = os.path.join(digests, "last-digest.json")
        ld_id = None
        if os.path.exists(last_digest):
            with open(last_digest, encoding="utf-8") as f:
                ld_id = json.load(f).get("id")
        record("A: дайджест make на диске, read.id == make.id",
               "PASS" if ld_id
               and ld_id == (t.get("digest_make") or {}).get("id")
               and (t.get("digest_read") or {}).get("digest", {}).get("id")
               == (t.get("digest_make") or {}).get("id") else "FAIL",
               f"last_digest_id={ld_id!r} "
               f"make_id={(t.get('digest_make') or {}).get('id')!r}")

        # A8: кап — зацикленный fake-LLM → error «превышен лимит (15)»
        agent2 = StudioAgent(tmp, base_url="https://mock.local/v1",
                             api_key="k",
                             client=httpx.Client(
                                 transport=httpx.MockTransport(
                                     _loop_llm_day20)),
                             mcp=reg)
        d2 = store.new_dialogue()
        store.profile_action(d2["id"], "decline")
        ev2 = list(agent2.ask_stream(d2["id"], "зацикли"))
        err = ev2[-1] if ev2 else {}
        record("A: кап — зацикленный LLM → SSE error «(15)»",
               "PASS" if (err.get("type") == "error"
                          and "(15)" in str(err.get("message", "")))
               else "FAIL", f"last={err!r}")

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
    """Live: uvicorn :8104 + реальный LLM (best-effort: SKIP, не FAIL,
    когда модель не довела 10-шаговую цепочку)."""
    log("=== Part B: live (uvicorn :8104, реальный LLM, best-effort) ===")
    proc = None
    dlg_id = None
    orig_model = None
    try:
        busy = wait_port_free()
        if busy:
            record("B: port 8104", "SKIP", busy)
            return 0

        proc = start_server()
        if proc is None:
            record("B: uvicorn up (:8104)", "FAIL",
                   "server did not come up on port 8104")
            return 1
        record(f"B: uvicorn up (port {PORT})", "PASS")

        skip_reason = probe_gpustack()
        if skip_reason:
            record("B: 10-серверный флоу live (реальный LLM)", "SKIP",
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

        # MCP: 10 одиночных серверов — дефолты реестра (Task 7), НЕ
        # POST-им новые: ищем по именам FLOW и подключаем.
        wanted = {s for s, _ in FLOW}
        code, body, _ = http("GET", "/api/mcp/servers", timeout=30)
        servers = (json.loads(body).get("servers", []) if code == 200 else [])
        by_name = {s.get("name"): s for s in servers
                   if s.get("name") in wanted}
        missing = sorted(wanted - set(by_name))
        if missing:
            record("B: GET /api/mcp/servers ×10", "FAIL",
                   f"нет серверов: {missing}")
            return 1
        record("B: GET /api/mcp/servers ×10 (дефолты реестра)", "PASS",
               ",".join(sorted(by_name)))
        sids = [by_name[s]["id"] for s, _ in FLOW]

        counts = []
        for sid in sids:
            code, body, _ = http("POST", f"/api/mcp/servers/{sid}/connect",
                                 timeout=120)
            view = json.loads(body).get("server", {}) if code == 200 else {}
            counts.append((view.get("status"), view.get("tools_count"),
                           view.get("error")))
        if not all(s == "connected" and c == 1 for s, c, _ in counts):
            record("B: MCP connect ×10 (connected, 1 tool each)", "FAIL",
                   f"counts={counts}")
            return 1
        record("B: MCP connect ×10 (connected, 1 tool each)", "PASS")

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

        # Чат: реальный LLM + tool-loop (10 шагов — стрим длинный,
        # request_long с retry).
        code, raw, full = request_long(
            "POST", "/api/chat",
            {"dialogue_id": dlg_id, "message": CHAT_MSG})
        deltas, dones, errors = parse_sse(raw)
        if code != 200 or not dones:
            detail = (errors[0] if errors
                      else f"code={code} dones={len(dones)} "
                           f"full={full} partial={len(raw)}B")
            record("B: chat SSE (done)", "FAIL", detail)
            return 1
        record(f"B: chat SSE ({len(deltas)} deltas, done)", "PASS",
               "" if full else "(done дошёл до обрыва стрима)")
        if errors:
            record("B: 10-серверный флоу live", "SKIP",
                   f"LLM/tool-loop ошибка: {errors[0]} (best-effort)")
            return 0

        code, body, _ = http("GET", f"/api/dialogues/{dlg_id}")
        dlg_full = json.loads(body).get("dialogue", {}) if code == 200 else {}
        tool_msgs = [m for m in dlg_full.get("messages", [])
                     if m.get("role") == "tool"]
        prefixes = {str(m.get("name", "")).split("__")[0] for m in tool_msgs}
        answer = dones[-1].get("answer", "") if dones else ""

        if len(tool_msgs) >= 5 and len(prefixes) >= 3:
            record(f"B: 10-серверный флоу live "
                   f"({len(tool_msgs)} tool-msg, {len(prefixes)} серверов)",
                   "PASS", f"answer[:80]={answer[:80]!r}")
        else:
            record("B: 10-серверный флоу live", "SKIP",
                   "модель не довела 10-шаговую цепочку (best-effort)")
        return 0
    finally:
        try:
            _cleanup(dlg_id, [], orig_model)
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
