"""E2E день 19 «MCP Tool Composition Pipeline (search → summarize → saveToFile)»
— гибрид: детерминированное ядро + live.

Пайплайн:
  Part A — детерминированное ядро (в-процессе, без uvicorn, БЕЗ сети):
    1. sys.path → studio/backend; StudioAgent + MCPRegistry (реальный
       launcher) + MemoryStore (tmp-каталог, tempfile).
    2. MCP: reg.add("Pipeline E2E", stdio, [python, pipeline_tools.py],
       env PIPELINE_SEARCH_DIR/PIPELINE_OUT_DIR → tmp) → connect
       → status connected, tools_count == 3 (реальный subprocess
       pipeline_tools.py: initialize + tools/list).
    3. Seed: 3 дайджест-JSON (схема collector.py, кириллица, различные
       generated_at, один с «погода» в summary) в tmp search-каталог.
    4. Direct call_tool sanity: search("погода") → count >= 1;
       summarize (длинный кириллический текст) → points непустые;
       saveToFile("t.md", "hello", "md") → файл на диске.
    5. Fake-LLM (httpx.MockTransport, /chat/completions) — скриптит
       цепочку 3 тулов по числу role "tool" сообщений в запросе
       (stage 0 → search, 1 → summarize, 2 → saveToFile, 3 →
       текстовый ответ): SSE-чанки delta.tool_calls (index, id call_19),
       finish_reason "tool_calls" + usage + [DONE]; stage 1 строит
       text summarize ИЗ результата search (содержит title первого
       match), stage 2 строит content saveToFile ИЗ "summary"
       результата summarize; stage 3 — контент-чанки (кириллица) +
       finish_reason "stop" + usage + [DONE]; non-stream
       (авто-название) → JSON content "E2E-название".
    6. store.new_dialogue() + profile decline; ask_stream(...).
    7. ASSERT (каждый — record): done (без error); ровно 3
       tool-сообщения в порядке search→summarize→saveToFile; text
       аргумента summarize содержит matches[0].title из результата
       search; content аргумента saveToFile содержит "summary" из
       результата summarize; файл <tmp>/out/pipeline_report.pdf
       существует, байты начинаются b"%PDF-1.4" и содержат
       b"/ToUnicode"; финальный ответ непустой.
    8. finally: reg.close_all() + удаление tmp-каталога.

  Part B — live (uvicorn :8103, реальный LLM, best-effort):
    1. Порт 8103: занят — ждать до 10 минут (чужой сервер НЕ убивается),
       всё ещё занят — SKIP (exit 0).
    2. Запуск `python -m uvicorn studio.backend.main:app --port 8103`
       detached из корня репозитория (готовность — GET /api/dialogues).
    3. probe_gpustack() — недостижим → SKIP (exit 0), сервер всё равно
       убивается в finally.
    4. GET /api/models → доступные модели; нет ни одной → FAIL (только
       когда GPustack достижим). Модель чата — первая доступная
       (детерминизм), оригинал конфига восстанавливается в cleanup.
    5. POST /api/mcp/servers (Pipeline: [python, pipeline_tools.py],
       БЕЗ env-оверрайда — реальные data/digests) → 201; POST connect
       (timeout 120) → connected, tools_count 3.
    6. POST /api/dialogues → 201; POST /api/profile/action decline.
    7. POST /api/chat «В дайджестах найди сводки про погоду,
       суммаризируй главное и сохрани результат в PDF-файл
       pipeline_report.pdf» (timeout 330, IncompleteRead — 1 retry).
    8. GET /api/dialogues/{id}: НЕТ role "tool" сообщений ИЛИ нет
       data/pipeline/pipeline_report.pdf с заголовком %PDF-1.4
       (проверка до 15 с после завершения SSE) → SKIP «модель не
       скомпоновала пайплайн (best-effort)», НЕ FAIL. Иначе — PASS.
    9. cleanup (ВСЕГДА в finally): DELETE своего диалога и MCP-сервера,
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
PORT = 8103  # 8100 — e2e_studio.py, 8101 — e2e_day17.py, 8102 — e2e_day18.py
BASE = f"http://{HOST}:{PORT}"
WAIT_PORT_BUSY_MAX = 600   # 10 минут ожидания освобождения порта
WAIT_PORT_BUSY_STEP = 10
WAIT_SERVER_UP_MAX = 90
CHAT_TIMEOUT = 330
PIPELINE_SERVER = os.path.join(REPO, "studio", "mcp_servers",
                               "pipeline_tools.py")
PDF_NAME = "pipeline_report.pdf"
CHAT_MSG = ("В дайджестах найди записи про Самара, суммаризируй главное "
            "и сохрани результат в PDF-файл pipeline_report.pdf")

# Windows-консоль может быть cp1251 — выводим UTF-8, чтобы «→» не падало.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

RESULTS = []  # (step, status, detail)


def log(msg):
    print(f"[e2e-day19] {msg}", flush=True)


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


def _fake_llm_handler(request) -> "object":
    """Fake-LLM, скриптит композицию search → summarize → saveToFile
    по числу role "tool" сообщений в запросе (stage):
      0 → tool_call search {"query": "погода"};
      1 → парсит JSON search из tool-сообщения, tool_call summarize
          {"text": "<title+summary каждого match — содержит title
          первого match, т.е. подстроку результата search>"};
      2 → парсит JSON summarize, tool_call saveToFile
          {"filename": "pipeline_report.pdf", "content": "<summary
          из результата summarize>", "format": "pdf"};
      3+ → content (кириллица) + finish_reason "stop" + usage + [DONE].
    Non-stream (авто-название) → JSON content "E2E-название"."""
    import httpx
    payload = json.loads(request.content)
    if "stream" not in payload:
        return httpx.Response(200, json={"choices": [
            {"message": {"content": "E2E-название"}}]})
    msgs = payload.get("messages") or []
    stage = sum(1 for m in msgs if m.get("role") == "tool")
    if stage == 0:
        return _tool_response(_tc("search", {"query": "погода"},
                                  "call_19_0"))
    if stage == 1:
        try:
            found = json.loads(_last_tool_text(msgs))
        except ValueError:
            found = {}
        matches = found.get("matches") or []
        text = "\n".join(
            f"{m.get('city', '')}: {m.get('title', '')} — "
            f"{m.get('summary', '')}"
            for m in matches if isinstance(m, dict))
        return _tool_response(_tc("summarize",
                                  {"text": text, "max_points": 5},
                                  "call_19_1"))
    if stage == 2:
        try:
            summ = json.loads(_last_tool_text(msgs))
        except ValueError:
            summ = {}
        content = summ.get("summary") or "\n".join(
            str(p) for p in (summ.get("points") or []))
        return _tool_response(_tc("saveToFile",
                                  {"filename": PDF_NAME, "content": content,
                                   "format": "pdf"},
                                  "call_19_2"))
    return httpx.Response(200, content=_sse([
        _delta_chunk("Готово: сводки по погоде из дайджестов "
                     "суммаризированы и сохранены в "
                     f"{PDF_NAME} (PDF)."),
        _stop_chunk(),
        "[DONE]",
    ]).encode("utf-8"))


def _seed_digests(search_dir: str) -> list:
    """3 дайджест-JSON по схеме collector.py (кириллица, различные
    generated_at; первый и второй — «погода» в summary)."""
    digests = [
        {"id": "digest-20260924-0600",
         "generated_at": "2026-09-24T06:00:00Z",
         "weather": {"city": "Самара", "temp_c": 18.5,
                     "description": "Небольшой дождь", "wind_ms": 3.0},
         "news": {},
         "summary": "Погода Самара: +18.5°C, небольшой дождь; "
                    "погодный фронт смещается на восток."},
        {"id": "digest-20260924-1200",
         "generated_at": "2026-09-24T12:00:00Z",
         "weather": {"city": "Самара", "temp_c": 21.0,
                     "description": "Переменная облачность",
                     "wind_ms": 2.0},
         "news": {},
         "summary": "Погода Самара: +21.0°C, переменная облачность, "
                    "к вечеру осадков не ожидается."},
        {"id": "digest-20260924-1800",
         "generated_at": "2026-09-24T18:00:00Z",
         "weather": {"city": "Казань", "temp_c": 16.2,
                     "description": "Снегопад", "wind_ms": 5.5},
         "news": {},
         "summary": "Новости науки: релиз нового фреймворка; "
                    "итоги осенней конференции."},
    ]
    for d in digests:
        with open(os.path.join(search_dir, d["id"] + ".json"), "w",
                  encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    return digests


def part_a() -> bool:
    """Детерминированное ядро: всегда выполняется, должно PASSнуть."""
    log("=== Part A: детерминированное ядро "
        "(fake LLM + реальный MCP-процесс pipeline_tools.py) ===")
    import httpx
    sys.path.insert(0, os.path.join(REPO, "studio", "backend"))
    from agent import StudioAgent
    from mcp import MCPRegistry
    from memory import MemoryStore

    idx = len(RESULTS)  # A-записи этого прогона
    tmp = tempfile.mkdtemp(prefix="e2e_day19_")
    search_dir = os.path.join(tmp, "digests")
    out_dir = os.path.join(tmp, "out")
    os.makedirs(search_dir, exist_ok=True)
    os.makedirs(out_dir, exist_ok=True)
    reg = None
    try:
        # A1: MCP connect — connected, 3 tools (реальный subprocess)
        store = MemoryStore(tmp)
        reg = MCPRegistry(store, timeout=120, env=dict(os.environ))
        font = r"C:\Windows\Fonts\arial.ttf" if os.name == "nt" else ""
        srv_env = {"PIPELINE_SEARCH_DIR": search_dir,
                   "PIPELINE_OUT_DIR": out_dir}
        if font and os.path.exists(font):
            srv_env["PIPELINE_FONT_PATH"] = font
        rec = reg.add("Pipeline E2E", "stdio",
                      command=[sys.executable, PIPELINE_SERVER],
                      url="", env=srv_env, enabled=True)
        sid = rec["id"]
        view = reg.connect(sid)
        ok = (view.get("status") == "connected"
              and view.get("tools_count") == 3)
        record("A: MCP connect (connected, 3 tools)",
               "PASS" if ok else "FAIL",
               f"status={view.get('status')} "
               f"tools_count={view.get('tools_count')} "
               f"error={view.get('error')}")

        # A2: seed 3 дайджеста (схема collector.py, кириллица)
        _seed_digests(search_dir)
        ok = len(os.listdir(search_dir)) == 3
        record("A: seed 3 digest JSON (collector schema, cyrillic)",
               "PASS" if ok else "FAIL", f"files={os.listdir(search_dir)}")

        # A3: direct call_tool sanity
        r = reg.call_tool(sid, "search", {"query": "погода"})
        d = json.loads(r["content"][0]["text"])
        ok = (r.get("isError") is not True
              and int(d.get("count", 0)) >= 1
              and len(d.get("matches") or []) >= 1)
        record("A: call_tool search «погода» (count >= 1)",
               "PASS" if ok else "FAIL", f"count={d.get('count')}")

        long_text = ("Сводка за день: в Самаре переменная облачность, "
                     "днём до +21, к вечеру небольшой дождь. В Казани "
                     "снегопад и порывистый ветер. Транспорт работает "
                     "с задержками, аэропорт открыт. На следующей неделе "
                     "ожидается похолодание до нуля и гололёд на "
                     "покрытиях, рекомендуется воздержаться от "
                     "дальних поездок.")
        r = reg.call_tool(sid, "summarize", {"text": long_text,
                                             "max_points": 5})
        d = json.loads(r["content"][0]["text"])
        ok = (r.get("isError") is not True
              and len(d.get("points") or []) >= 1
              and int(d.get("input_chars", 0)) > 0)
        record("A: call_tool summarize (points non-empty)",
               "PASS" if ok else "FAIL",
               f"points={len(d.get('points') or [])}")

        r = reg.call_tool(sid, "saveToFile",
                          {"filename": "t.md", "content": "hello",
                           "format": "md"})
        d = json.loads(r["content"][0]["text"])
        ok = (r.get("isError") is not True
              and os.path.exists(os.path.join(out_dir, "t.md"))
              and int(d.get("size", 0)) > 0)
        record("A: call_tool saveToFile t.md (файл на диске)",
               "PASS" if ok else "FAIL",
               f"path={d.get('path')} size={d.get('size')}")

        # A4: CHAIN PROOF — fake-LLM скриптит search → summarize
        # → saveToFile, реальный tool-loop агента, реальный
        # subprocess MCP-сервер.
        agent = StudioAgent(tmp, base_url="https://mock.local/v1",
                            api_key="k",
                            client=httpx.Client(
                                transport=httpx.MockTransport(
                                    _fake_llm_handler)),
                            mcp=reg)
        d = store.new_dialogue()
        store.profile_action(d["id"], "decline")
        events = list(agent.ask_stream(
            d["id"], "Найди сводки про погоду, суммаризируй главное "
                     "и сохрани в PDF"))

        types = [e.get("type") for e in events]
        last = events[-1] if events else {}
        done_ok = (last.get("type") == "done" and "error" not in types)
        record("A: ask_stream (done, без error)",
               "PASS" if done_ok else "FAIL", f"types={types}")

        msgs = agent.store.get_messages(d["id"])
        tool_msgs = [m for m in msgs if m.get("role") == "tool"]
        order_ok = ([m.get("name") for m in tool_msgs]
                    == ["search", "summarize", "saveToFile"])
        record("A: ровно 3 tool-msg в порядке "
               "search→summarize→saveToFile",
               "PASS" if order_ok else "FAIL",
               f"names={[m.get('name') for m in tool_msgs]}")

        # Аргументы tool-вызовов из assistant-сообщений с tool_calls
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
        # Целостность 1: text summarize ⊇ matches[0].title из search
        search_out = json.loads(str(tool_msgs[0].get("content", "")))
        first_title = ((search_out.get("matches") or [{}])[0]
                       .get("title", ""))
        chain1_ok = bool(first_title) and (
            first_title in str(asst_args.get("summarize", {}).get(
                "text", "")))
        record("A: chain — summarize.text содержит title из "
               "результата search",
               "PASS" if chain1_ok else "FAIL",
               f"title={first_title[:40]!r}")
        # Целостность 2: content saveToFile ⊇ "summary" из summarize
        summ_out = json.loads(str(tool_msgs[1].get("content", "")))
        summary_text = str(summ_out.get("summary", ""))
        chain2_ok = bool(summary_text) and (
            summary_text in str(asst_args.get("saveToFile", {})
                                .get("content", "")))
        record("A: chain — saveToFile.content содержит summary "
               "из результата summarize",
               "PASS" if chain2_ok else "FAIL",
               f"summary[:40]={summary_text[:40]!r}")

        # PDF на диске: %PDF-1.4 + /ToUnicode (кириллица)
        pdf_path = os.path.join(out_dir, PDF_NAME)
        pdf_bytes = b""
        if os.path.exists(pdf_path):
            with open(pdf_path, "rb") as f:
                pdf_bytes = f.read()
        pdf_ok = (pdf_bytes.startswith(b"%PDF-1.4")
                  and b"/ToUnicode" in pdf_bytes)
        record(f"A: {PDF_NAME} (%PDF-1.4, /ToUnicode)",
               "PASS" if pdf_ok else "FAIL",
               f"size={len(pdf_bytes)} header={pdf_bytes[:8]!r}")

        answer = last.get("answer", "")
        ans_ok = bool(answer.strip())
        record("A: финальный ответ непустой",
               "PASS" if ans_ok else "FAIL", f"answer[:80]={answer[:80]!r}")

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
    """Live: uvicorn :8103 + реальный LLM (best-effort: SKIP, не FAIL,
    когда модель не скомпоновала пайплайн)."""
    log("=== Part B: live (uvicorn :8103, реальный LLM, best-effort) ===")
    proc = None
    dlg_id = None
    mcp_sid = None
    orig_model = None
    try:
        busy = wait_port_free()
        if busy:
            record("B: port 8103", "SKIP", busy)
            return 0

        proc = start_server()
        if proc is None:
            record("B: uvicorn up (:8103)", "FAIL",
                   "server did not come up on port 8103")
            return 1
        record(f"B: uvicorn up (port {PORT})", "PASS")

        skip_reason = probe_gpustack()
        if skip_reason:
            record("B: pipeline live (реальный LLM)", "SKIP",
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

        # MCP: Pipeline (реальный python-subprocess; БЕЗ env-оверрайда —
        # реальные data/digests и data/pipeline).
        code, body, _ = http("POST", "/api/mcp/servers",
                             {"name": "Pipeline E2E", "type": "stdio",
                              "command": [sys.executable,
                                          PIPELINE_SERVER]},
                             timeout=30)
        srv = json.loads(body).get("server", {}) if code == 201 else {}
        if code != 201 or not srv.get("id"):
            record("B: POST /api/mcp/servers (Pipeline)", "FAIL",
                   f"code={code}")
            return 1
        mcp_sid = srv["id"]
        record("B: POST /api/mcp/servers (201)", "PASS", mcp_sid)

        code, body, _ = http("POST", f"/api/mcp/servers/{mcp_sid}/connect",
                             timeout=120)
        view = json.loads(body).get("server", {}) if code == 200 else {}
        if (code != 200 or view.get("status") != "connected"
                or view.get("tools_count") != 3):
            record("B: MCP connect (connected, 3 tools)", "FAIL",
                   f"code={code} status={view.get('status')} "
                   f"tools_count={view.get('tools_count')} "
                   f"error={view.get('error')}")
            return 1
        record("B: MCP connect (connected, 3 tools)", "PASS")

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
        # (3 MCP-вызова в цепочке) — request_long с retry.
        code, raw, full = request_long(
            "POST", "/api/chat",
            {"dialogue_id": dlg_id, "message": CHAT_MSG})
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

        # PDF-артефакт: модель могла завершить запись чуть позже done
        # (атомарный replace) — даём до 15 с.
        pdf_path = os.path.join(REPO, "data", "pipeline", PDF_NAME)
        deadline = time.time() + 15
        while time.time() < deadline and not os.path.exists(pdf_path):
            time.sleep(1)
        pdf_ok = False
        if os.path.exists(pdf_path):
            with open(pdf_path, "rb") as f:
                pdf_ok = f.read(1024).startswith(b"%PDF-1.4")

        if not tool_msgs:
            record("B: pipeline live (role tool в диалоге)", "SKIP",
                   "модель не вызвала инструмент (best-effort)")
            return 0
        if not pdf_ok:
            record("B: pipeline live (PDF-артефакт)", "SKIP",
                   f"{PDF_NAME} с заголовком %PDF-1.4 не найден в "
                   f"data/pipeline (best-effort)")
            return 0
        record(f"B: pipeline live ({len(tool_msgs)} tool-msg, "
               f"{PDF_NAME} на диске)", "PASS",
               f"answer[:80]={answer[:80]!r}")
        return 0
    finally:
        try:
            _cleanup(dlg_id, mcp_sid, orig_model)
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
