"""E2E smoke day11 «Студия» (FastAPI + React, prod-режим).

Пайплайн:
  1. Env-проверка: GPUSTACK_BASE_URL + GPUSTACK_API_KEY (окружение или .env
     в корне репозитория) и достижимость GPustack (GET {base}/models).
     Недостижимо — чат-шаги SKIP (exit 0), остальной smoke идёт в любом случае.
  2. Порт 8100: если занят — ждать до 10 минут (чужой сервер НЕ убивается);
     всё ещё занят — SKIP с сообщением, exit 0.
  3. Запуск `python -m uvicorn studio.backend.main:app --port 8100` detached
     из корня репозитория (prod-режим: API + собранный фронтенд из dist).
   4. UI: GET / → 200, HTML содержит «AI Studio» (бренд, день 14); assets из
      index.html → 200.
  5. GET /api/config; GET /api/models (502 допустим — фиксируем, не FAIL).
  6. POST /api/dialogues → 201 + active_id.
  7. WM: POST /api/memory/working → ok; GET /api/memory → working.entries == 1.
   8. LT: POST /api/memory/longterm → ok; GET /api/memory → long_term.entries == 1.
    8b. Тумблеры слоёв: POST /api/memory/toggles wm off → /api/memory видит
        wm=false (ст/lt не тронуты) → restore on.
    8c. Профиль (день 12, свой диалог): pending по умолчанию → POST /api/profile
        (active) → GET /api/rules (profile_block + active) → decline.
        Эндпоинты детерминированы — без зависимости от GPustack.
    9. Чат (если GPustack достижим): POST /api/chat → SSE: >=1 delta + done.
       (день 12: чат-диалоги сначала decline-профильт — pending-диалог не
       уходит в LLM; день-11 поведение проверяется на не-pending диалоге)
    10. Авто-заголовок: чат в НОВОМ диалоге (после decline профиля) →
        title меняется с «Новый диалог»; assistant-сообщение хранится с model.
    10c. Персонализация (день 12, live): два диалога с разными active-
         профилями, один вопрос → разные ответы (best-effort) + профиль-
          блоки в телах запросов журнала; запрос с табу-словом → system-
          напоминание гарда (D8) в теле LLM-запроса.
      8d. Задача (день 13b): пошаговый пайплайн — start (task_id, plan из
           4 записей, user-маркер) → run (SSE до конца: agent_spawned /
           step_updated / step_delta / stage_done / task_done, структура
           событий, имя/число work-шагов не фиксированы) → состояние done
           (plan + work_steps completed) → маркеры сообщений
           (task_id/task_stage/task_step, финальный assistant без
           task_stage) → 400-гарды на done (pause/instruction/run) →
           новая задача (D9: новый task_id, 2 user-якоря) → гард чата
           на активной + reset. Live best-effort (SKIP, не FAIL): пауза
           на границе work-шага → task_paused + context_snapshot →
           instruction → resume → run → task_done; чат-режим после done
           (обычный чат, новых task_id-маркеров нет). SKIP, если
           GPustack недоступен.
     7e. MCP (день 16): дефолты в реестре (Firecrawl/Git) → 400-
          валидация → mock stdio-сервер (python -c, без сети): POST (201) →
          connect (200, status connected, 2 tools) → /api/mcp/tools содержит
          mock_echo/mock_ping → connect неизвестного id (404) → DELETE (200 +
          повтор 404). Live: connect Firecrawl — best-effort, SKIP (не FAIL),
          если npx недоступен.
    11. GET /api/tokens → {last, session, context_limit}.
  11. GET /api/requests → список; после успешного чата запись с model.
  12. finally: ВСЕГДА убить свой uvicorn, убедиться, что порт 8100 закрыт.

Выход: PASS/FAIL на stdout; exit 0 для PASS и SKIP, 1 для FAIL.
"""
import html
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
HOST = "127.0.0.1"
PORT = 8100
BASE = f"http://{HOST}:{PORT}"
WAIT_PORT_BUSY_MAX = 600   # 10 минут ожидания освобождения порта
WAIT_PORT_BUSY_STEP = 10
WAIT_SERVER_UP_MAX = 90
CHAT_TIMEOUT = 330

# MCP (день 16): детерминированный mock stdio-сервер для e2e-подключения
# (JSON-RPC по stdin/stdout, 2 инструмента; сеть и npx не нужны)
MOCK_MCP_SERVER = r'''
import json, sys
def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        m = json.loads(line)
    except ValueError:
        continue
    if "id" not in m:
        continue
    if m["method"] == "initialize":
        r = {"protocolVersion": "2024-11-05", "capabilities": {},
             "serverInfo": {"name": "mock", "version": "1"}}
    elif m["method"] == "tools/list":
        r = {"tools": [
            {"name": "mock_echo", "description": "E2E echo tool",
             "inputSchema": {"type": "object"}},
            {"name": "mock_ping", "description": "E2E ping",
             "inputSchema": {"type": "object"}},
        ]}
    else:
        r = {}
    send({"jsonrpc": "2.0", "id": m["id"], "result": r})
'''

# Windows-консоль может быть cp1251 — выводим UTF-8, чтобы «→» не падало.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

RESULTS = []  # (step, status, detail)


def log(msg):
    print(f"[e2e-studio] {msg}", flush=True)


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
    """None — достижим, иначе строка причины SKIP чата."""
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
    log("starting uvicorn (prod mode: API + static dist) on port 8100")
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


def _cleanup(dialogue_ids: list[str | None], orig_model: str | None,
             inv_ids: list[str | None] | None = None) -> None:
    """Убрать артефакты самого e2e (не трогая данные пользователя)."""
    try:
        # День 13: сброс задач ДО удаления диалогов (reset 404 на
        # несуществующем диалоге).
        for dialogue_id in dialogue_ids:
            if dialogue_id:
                http("POST", "/api/task/reset",
                     {"dialogue_id": dialogue_id}, timeout=10)
        for dialogue_id in dialogue_ids:
            if dialogue_id:
                http("DELETE", f"/api/dialogues/{dialogue_id}", timeout=10)
        http("DELETE", "/api/memory/longterm/e2e", timeout=10)
        # День 14: инварианты — глобальные, чистим свои по id.
        for inv_id in (inv_ids or []):
            if inv_id:
                http("DELETE", f"/api/invariants/{inv_id}", timeout=10)
        if orig_model:
            http("POST", "/api/config", {"model": orig_model}, timeout=10)
    except Exception:
        pass


def stop_server(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    log("stopping uvicorn")
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
        log("warning: port 8100 still open after stop")


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


def parse_task_sse(raw: bytes) -> list:
    """Задачный SSE: ВСЕ кадры как dict ({"type": ...} + поля события)."""
    events = []
    for line in raw.decode("utf-8", "replace").splitlines():
        if not line.startswith("data: "):
            continue
        try:
            ev = json.loads(line[6:])
        except json.JSONDecodeError:
            continue
        if isinstance(ev, dict) and ev.get("type"):
            events.append(ev)
    return events


def main() -> int:
    load_dotenv()
    if not os.path.isdir(os.path.join(REPO, "studio", "frontend", "dist")):
        log("SKIP studio/frontend/dist не собран (npm run build) — не наш FAIL")
        return 0

    busy = wait_port_free()
    if busy:
        log(f"SKIP {busy}")
        return 0

    proc = start_server()
    dlg_id: str | None = None
    dlg2_id: str | None = None
    prof_dlg_id: str | None = None
    dlg3_id: str | None = None
    dlg4_id: str | None = None
    task_dlg_id: str | None = None
    inv_id: str | None = None
    orig_model: str | None = None
    try:
        if proc is None:
            log("FAIL server did not come up on port 8100")
            return 1

        # 1. UI
        code, body, headers = http("GET", "/")
        text = body.decode("utf-8", "replace")
        if code == 200 and "AI Studio" in html.unescape(text):
            record("UI: GET / → студия", "PASS")
        else:
            record("UI: GET / → студия", "FAIL", f"code={code}")
            return 1

        # 2. статические assets из index.html
        asset = None
        for m in re.finditer(r'(?:src|href)="(/assets/[^"]+)"', text):
            asset = m.group(1)
            break
        if asset:
            code, _, h = http("GET", asset)
            if code == 200:
                record(f"UI: asset {asset[:40]}…", "PASS")
            else:
                record("UI: asset", "FAIL", f"{asset} → {code}")
                return 1
        else:
            record("UI: asset", "FAIL", "no /assets/* reference in index.html")
            return 1

        # 3. конфиг
        code, body, _ = http("GET", "/api/config")
        cfg = json.loads(body)
        if code == 200 and cfg.get("model"):
            record(f"API: config (model={cfg['model']})", "PASS")
        else:
            record("API: config", "FAIL", f"code={code}")
            return 1

        # 4. модели (502 допустим). list_models фильтрует доступные (зонд
        # max_tokens=1): в списке только то, чем ключ реально может работать.
        code, body, _ = http("GET", "/api/models", timeout=60)
        avail = []
        if code == 200:
            avail = json.loads(body).get("models", [])
            record(f"API: models available ({len(avail)}: {', '.join(m['id'] for m in avail)})", "PASS")
        elif code == 502:
            record("API: models", "PASS", "502 — GPustack недоступен, допустимо")
        else:
            record("API: models", "FAIL", f"code={code}")
            return 1

        # 5. диалог
        code, body, _ = http("POST", "/api/dialogues")
        dlg = json.loads(body).get("dialogue", {})
        if code == 201 and dlg.get("id"):
            dlg_id = dlg["id"]
            record(f"API: dialogue created ({dlg['id'][:8]})", "PASS")
        else:
            record("API: dialogue", "FAIL", f"code={code}")
            return 1

        # 5b. переименование диалога
        code, body, _ = http("POST", f"/api/dialogues/{dlg_id}/rename",
                             {"title": "e2e-диалог"})
        code2, body2, _ = http("GET", "/api/dialogues")
        titles = [d.get("title") for d in json.loads(body2).get("dialogues", [])]
        if code == 200 and "e2e-диалог" in titles:
            record("API: dialogue rename", "PASS")
        else:
            record("API: dialogue rename", "FAIL", f"code={code} titles={titles}")
            return 1

        # 6. WM (проверка конкретного ключа — не зависит от чужих данных)
        code, _, _ = http("POST", "/api/memory/working",
                          {"key": "e2e", "value": "проверка"})
        code2, body, _ = http("GET", "/api/memory")
        wm_items = json.loads(body).get("working", {}).get("items", {})
        if code == 200 and code2 == 200 and wm_items.get("e2e") == "проверка":
            record("API: WM set + memory", "PASS")
        else:
            record("API: WM set + memory", "FAIL",
                   f"set={code} mem={code2} items={wm_items}")
            return 1

        # 7. LT (то же самое, глобальный слой)
        code, _, _ = http("POST", "/api/memory/longterm",
                          {"key": "e2e", "value": "глобальная"})
        code2, body, _ = http("GET", "/api/memory")
        lt_items = json.loads(body).get("long_term", {}).get("items", {})
        if code == 200 and code2 == 200 and lt_items.get("e2e") == "глобальная":
            record("API: LT set + memory", "PASS")
        else:
            record("API: LT set + memory", "FAIL",
                   f"set={code} mem={code2} items={lt_items}")
            return 1

        # 7b. Тумблеры слоёв: wm off → отражается в /api/memory → restore on.
        #     Idempotent: baseline — текущие toggles (toggles.json persistится,
        #     st/lt могут быть выключены пользователем); проверяем, что wm
        #     переключился, а st/lt не тронулись.
        _, body, _ = http("GET", "/api/memory")
        tog_orig = json.loads(body).get("toggles", {})
        code, _, _ = http("POST", "/api/memory/toggles",
                          {"layer": "wm", "enabled": False})
        code2, body, _ = http("GET", "/api/memory")
        tog = json.loads(body).get("toggles", {})
        code3, _, _ = http("POST", "/api/memory/toggles",
                           {"layer": "wm", "enabled": True})
        if (code == 200 and code2 == 200 and code3 == 200
                and tog.get("wm") is False
                and tog.get("st") == tog_orig.get("st")
                and tog.get("lt") == tog_orig.get("lt")):
            record("API: memory toggles (wm off/on)", "PASS")
        else:
            record("API: memory toggles (wm off/on)", "FAIL",
                   f"off={code} mem={code2} on={code3} "
                   f"toggles={tog} orig={tog_orig}")
            return 1

        # 7c. профиль (день 12): pending → set → block в rules → decline.
        #     Детерминированные эндпоинты — без GPustack. Свой свежий диалог
        #     (не трогает диалоги других шагов); удаляется в _cleanup.
        code, body, _ = http("POST", "/api/dialogues")
        prof_dlg = json.loads(body).get("dialogue", {})
        prof_dlg_id = prof_dlg.get("id")
        if code == 201 and prof_dlg_id:
            code, body, _ = http("GET", f"/api/dialogues/{prof_dlg_id}")
            prof = json.loads(body).get("dialogue", {}).get("profile", {})
            if code == 200 and prof.get("status") == "pending":
                record("API: профиль pending по умолчанию", "PASS")
            else:
                record("API: профиль pending по умолчанию", "FAIL",
                       f"code={code} status={prof.get('status')!r}")
                return 1

            code, body, _ = http("POST", "/api/profile",
                                 {"dialogue_id": prof_dlg_id, "name": "Иван",
                                  "role": "backend", "tone": "кратко",
                                  "taboos": ""})
            prof = json.loads(body).get("profile", {})
            if (code == 200 and prof.get("status") == "active"
                    and prof.get("name") == "Иван"):
                record("API: профиль set (Иван, active)", "PASS")
            else:
                record("API: профиль set (Иван, active)", "FAIL",
                       f"code={code} profile={prof}")
                return 1

            code, body, _ = http("GET", f"/api/rules?dialogue_id={prof_dlg_id}")
            rules = json.loads(body)
            if (code == 200 and "Иван" in rules.get("profile_block", "")
                    and rules.get("profile_status") == "active"):
                record("API: профиль в rules (block + active)", "PASS")
            else:
                record("API: профиль в rules (block + active)", "FAIL",
                       f"code={code} block={rules.get('profile_block')!r} "
                       f"status={rules.get('profile_status')!r}")
                return 1

            code, body, _ = http("POST", "/api/profile/action",
                                 {"dialogue_id": prof_dlg_id, "action": "decline"})
            prof = json.loads(body).get("profile", {})
            if code == 200 and prof.get("status") == "declined":
                record("API: профиль decline (declined)", "PASS")
            else:
                record("API: профиль decline (declined)", "FAIL",
                       f"code={code} status={prof.get('status')!r}")
                return 1
        else:
            record("API: профиль dialogue", "FAIL", f"code={code}")
            return 1

        # 7d. Инварианты (день 14): детерминированные эндпоинты CRUD +
        #     блок в /api/rules + статистика в /api/memory — без GPustack.
        #     Глобальный слой; свой ключ «e2e-inv» всегда чистится после.
        code, body, _ = http("GET", "/api/invariants")
        inv_before = json.loads(body).get("invariants", []) if code == 200 else None
        if code == 200:
            record(f"API: invariants GET ({len(inv_before)} originals)", "PASS")
        else:
            record("API: invariants GET", "FAIL", f"code={code}")
            return 1

        code, body, _ = http("POST", "/api/invariants",
                             {"title": "e2e-inv", "description": "Kotlin",
                              "forbidden": ["python"], "is_active": True})
        inv = json.loads(body).get("invariant", {}) if code == 201 else {}
        created_id = inv.get("id")
        if code == 201 and created_id and inv.get("title") == "e2e-inv":
            inv_id = created_id
            record(f"API: invariants POST ({created_id})", "PASS")
        else:
            record("API: invariants POST", "FAIL",
                   f"code={code} body={body[:120]!r}")
            return 1

        code, body, _ = http("GET", "/api/invariants")
        inv_list = json.loads(body).get("invariants", []) if code == 200 else []
        in_rules = any(inv.get("id") == inv_id for inv in inv_list
                       if inv.get("title") == "e2e-inv")
        if code == 200 and in_rules:
            record("API: invariants GET содержит добавленный", "PASS")
        else:
            record("API: invariants GET содержит добавленный", "FAIL",
                   f"code={code} found={in_rules}")
            return 1

        # /api/rules → invariants_block с title/description
        code, body, _ = http("GET", "/api/rules")
        rules = json.loads(body)
        iblock = rules.get("invariants_block", "")
        if code == 200 and "e2e-inv" in iblock and "Kotlin" in iblock:
            record("API: invariants в /api/rules (block)", "PASS")
        else:
            record("API: invariants в /api/rules (block)", "FAIL",
                   f"code={code} block={iblock!r}")
            return 1

        # /api/memory → invariants в layer_stats
        code, body, _ = http("GET", "/api/memory")
        inv_stats = json.loads(body).get("invariants", {})
        if code == 200 and inv_stats.get("entries", 0) >= 1:
            record(f"API: invariants в /api/memory (entries={inv_stats.get('entries')})",
                   "PASS")
        else:
            record("API: invariants в /api/memory", "FAIL",
                   f"code={code} stats={inv_stats}")
            return 1

        # Валидация 400: пустое описание
        code, _, _ = http("POST", "/api/invariants",
                          {"title": "e2e-inv", "description": ""})
        if code == 400:
            record("API: invariants POST (пустое значение → 400)", "PASS")
        else:
            record("API: invariants POST (пустое значение → 400)", "FAIL",
                   f"code={code}")
            return 1

        # Удаление существующего → 200; несуществующего → 404
        code, _, _ = http("DELETE", f"/api/invariants/{inv_id}")
        code2, body, _ = http("GET", "/api/invariants")
        inv_after = json.loads(body).get("invariants", []) if code2 == 200 else []
        gone = not any(inv.get("id") == inv_id for inv in inv_after)
        code3, _, _ = http("DELETE", f"/api/invariants/{inv_id}")
        if code == 200 and gone and code3 == 404:
            record("API: invariants DELETE (200) + повтор (404)", "PASS")
            inv_id = None
        else:
            record("API: invariants DELETE (200) + повтор (404)", "FAIL",
                   f"del={code} gone={gone} del2={code3}")
            return 1

        # 7e. MCP (день 16): реестр + connect на локальном mock stdio-сервере
        # (детерминировано, без сети); live Firecrawl — best-effort SKIP.
        code, body, _ = http("GET", "/api/mcp/servers")
        mcp_servers = json.loads(body).get("servers", []) if code == 200 else []
        if code == 200 and {"Firecrawl", "Git"} <= {s["name"] for s in mcp_servers}:
            record(f"MCP: дефолты в реестре ({len(mcp_servers)} серверов)", "PASS")
        else:
            record("MCP: дефолты в реестре", "FAIL", f"code={code}")

        code, _, _ = http("POST", "/api/mcp/servers",
                          {"type": "stdio", "command": ["npx"]}, timeout=10)
        record("MCP: POST без name → 400", "PASS" if code == 400 else "FAIL", f"code={code}")
        code, _, _ = http("POST", "/api/mcp/servers",
                          {"name": "X", "type": "tcp", "command": ["npx"]}, timeout=10)
        record("MCP: POST с неизвестным type → 400", "PASS" if code == 400 else "FAIL", f"code={code}")

        code, body, _ = http("POST", "/api/mcp/servers", {
            "name": "E2E Mock", "type": "stdio",
            "command": ["python", "-c", MOCK_MCP_SERVER],
        }, timeout=10)
        mcp_mock = json.loads(body).get("server", {}) if code == 201 else {}
        record("MCP: POST mock-сервер (201)", "PASS" if code == 201 else "FAIL", f"code={code}")

        if mcp_mock:
            code, body, _ = http("POST", f"/api/mcp/servers/{mcp_mock['id']}/connect", timeout=90)
            view = json.loads(body).get("server", {}) if code == 200 else {}
            ok = code == 200 and view.get("status") == "connected" \
                and view.get("tools_count") == 2
            if ok:
                code2, body, _ = http("GET", "/api/mcp/tools")
                tools = json.loads(body).get("tools", []) if code2 == 200 else []
                names = {t["name"] for t in tools if t.get("server") == mcp_mock["id"]}
                ok = {"mock_echo", "mock_ping"} <= names
            record("MCP: mock connect (connected, 2 tools в /api/mcp/tools)",
                   "PASS" if ok else "FAIL", f"code={code}, view={view}")
            code3, _, _ = http("POST", "/api/mcp/servers/mcp_nope/connect", timeout=10)
            record("MCP: connect неизвестного id → 404", "PASS" if code3 == 404 else "FAIL", f"code={code3}")
            code4, _, _ = http("DELETE", f"/api/mcp/servers/{mcp_mock['id']}", timeout=10)
            code5, _, _ = http("DELETE", f"/api/mcp/servers/{mcp_mock['id']}", timeout=10)
            record("MCP: DELETE (200) + повтор (404)",
                   "PASS" if code4 == 200 and code5 == 404 else "FAIL",
                   f"code={code4}/{code5}")
        else:
            record("MCP: mock connect (connected, 2 tools в /api/mcp/tools)",
                   "FAIL", "mock-сервер не создан (нет 201)")

        # Live: connect Firecrawl — best-effort (npx может быть недоступен —
        # SKIP, не FAIL); детерминированное ядро — mock-блок выше
        fc = next((s for s in mcp_servers if s["name"] == "Firecrawl"), None)
        if fc:
            code, body, _ = http("POST", f"/api/mcp/servers/{fc['id']}/connect", timeout=180)
            view = json.loads(body).get("server", {}) if code == 200 else {}
            if code == 200 and view.get("status") == "connected":
                record(f"MCP: live Firecrawl (connected, {view.get('tools_count')} tools)", "PASS")
            else:
                record("MCP: live Firecrawl", "SKIP",
                       f"npx недоступен ({view.get('error') or code})")
        else:
            record("MCP: live Firecrawl", "SKIP", "Firecrawl нет в реестре")

        # 8. чат (SSE) — SKIP, если GPustack недоступен
        skip_reason = probe_gpustack()
        if skip_reason:
            record("API: chat SSE", "SKIP", skip_reason)
        elif not avail:
            record("API: chat SSE", "FAIL",
                   "нет ни одной доступной модели (у ключа нет доступа)")
            return 1
        else:
            # Детерминизм: модель чата — первая доступная. Конфиг пользователя
            # может содержать модель, доступ к которой у ключа отсутствует.
            # Оригинал восстанавливается в _cleanup.
            code0, body0, _ = http("GET", "/api/config")
            orig_model = json.loads(body0).get("model")
            http("POST", "/api/config", {"model": avail[0]["id"]}, timeout=30)
            # День 12: новый диалог = pending-профиль → первый запрос не
            # уходит в LLM (служебный ход приглашения). Отказ от профиля,
            # чтобы проверить по-прежнему день-11 поведение (LLM-стрим).
            http("POST", "/api/profile/action",
                 {"dialogue_id": dlg["id"], "action": "decline"})
            code, raw, _ = http("POST", "/api/chat",
                                {"dialogue_id": dlg["id"], "message": "Скажи: OK"},
                                timeout=CHAT_TIMEOUT)
            deltas, dones, errors = parse_sse(raw)
            if code == 200 and dones and not errors:
                record(f"API: chat SSE ({len(deltas)} deltas, done)", "PASS")
            elif errors:
                record("API: chat SSE", "FAIL", f"error event: {errors[0]}")
                return 1
            else:
                record("API: chat SSE", "FAIL",
                       f"code={code} deltas={len(deltas)} dones={len(dones)}")
                return 1

            # 8b. авто-заголовок: первое сообщение в НОВОМ диалоге (с title
            #     «Новый диалог») → LLM называет диалог; assistant-сообщение
            #     хранится с model. Чат в первом диалоге не проверяет
            #     авто-заголовок — его title уже переименован (чек 5b).
            code, body, _ = http("POST", "/api/dialogues")
            dlg2 = json.loads(body).get("dialogue", {})
            dlg2_id = dlg2.get("id")
            # Тот же pending-гейт дня 12 — сначала отказ от профиля.
            http("POST", "/api/profile/action",
                 {"dialogue_id": dlg2_id, "action": "decline"})
            code, raw, _ = http("POST", "/api/chat",
                                {"dialogue_id": dlg2_id,
                                 "message": "Скажи: ОК"},
                                timeout=CHAT_TIMEOUT)
            deltas2, dones2, errors2 = parse_sse(raw)
            code, body, _ = http("GET", "/api/dialogues")
            dlg2_after = next(
                (d for d in json.loads(body).get("dialogues", [])
                 if d.get("id") == dlg2_id), {})
            code, body, _ = http("GET", f"/api/dialogues/{dlg2_id}")
            msgs = json.loads(body).get("dialogue", {}).get("messages", [])
            assistants = [m for m in msgs if m.get("role") == "assistant"]
            got_model = assistants[-1].get("model") if assistants else None
            if (code == 200 and dones2 and not errors2
                    and dlg2_after.get("title") not in (None, "", "Новый диалог")
                    and got_model == avail[0]["id"]):
                record(f"API: chat auto-title + model «{dlg2_after['title']}»",
                       "PASS")
            else:
                record("API: chat auto-title + model", "FAIL",
                       f"chat={code} dones={len(dones2)} "
                       f"title={dlg2_after.get('title')!r} model={got_model!r}")
                return 1

            # 8c. Персонализация (день 12, live): разные active-профили →
            #     разные ответы + гард табу-слов. Детерминированное ядро:
            #     профиль-блоки и гард-напоминание в ТЕЛАХ запросов журнала
            #     (GET /api/requests/{id}); различие live-ответов — best-
            #     effort (fix-ап в detail, не FAIL — мелкие модели).
            code, body, _ = http("POST", "/api/dialogues")
            dlg3_id = json.loads(body).get("dialogue", {}).get("id")
            code, body, _ = http("POST", "/api/dialogues")
            dlg4_id = json.loads(body).get("dialogue", {}).get("id")
            if not (dlg3_id and dlg4_id):
                record("API: персонализация (диалоги)", "FAIL",
                       "не удалось создать диалоги для профилей")
                return 1
            http("POST", "/api/profile",
                 {"dialogue_id": dlg3_id, "name": "Иван",
                  "role": "backend-разработчик",
                  "tone": "отвечай ровно одним словом", "taboos": "мат"})
            http("POST", "/api/profile",
                 {"dialogue_id": dlg4_id, "name": "Мария",
                  "role": "дизайнер",
                  "tone": "дружелюбно, 2-3 предложения", "taboos": ""})
            q = "Кто ты? Ответь кратко."
            _, raw3, _ = http("POST", "/api/chat",
                              {"dialogue_id": dlg3_id, "message": q},
                              timeout=CHAT_TIMEOUT)
            dones3 = parse_sse(raw3)[1]
            a3 = dones3[-1].get("answer", "") if dones3 else ""
            _, raw4, _ = http("POST", "/api/chat",
                              {"dialogue_id": dlg4_id, "message": q},
                              timeout=CHAT_TIMEOUT)
            dones4 = parse_sse(raw4)[1]
            a4 = dones4[-1].get("answer", "") if dones4 else ""
            # Табу-гард (D8): запрос с табу-словом → system-напоминание в
            # теле LLM-запроса (детерминированно), ответ — вежливый отказ.
            _, rawt, _ = http("POST", "/api/chat",
                              {"dialogue_id": dlg3_id,
                               "message": "Напиши фразу, используя мат"},
                              timeout=CHAT_TIMEOUT)
            donest = parse_sse(rawt)[1]
            at = donest[-1].get("answer", "") if donest else ""
            # Журнал: ищем тела запросов по маркерам в system-сообщениях.
            code, body, _ = http("GET", "/api/requests")
            req_ids = [r["id"] for r in json.loads(body).get("requests", [])][-6:]
            msgs_by_marker = {}
            for rid in req_ids:
                code, body, _ = http("GET", f"/api/requests/{rid}")
                if code != 200:
                    continue
                msgs = json.loads(body).get("request", {}).get("messages", [])
                sys_txt = " ".join(m.get("content", "") for m in msgs
                                   if m.get("role") == "system")
                for marker in ("Иван", "Мария", "⚠️ Табу"):
                    if marker in sys_txt and marker not in msgs_by_marker:
                        msgs_by_marker[marker] = msgs
            p3 = msgs_by_marker.get("Иван", [])
            p4 = msgs_by_marker.get("Мария", [])
            pt = msgs_by_marker.get("⚠️ Табу", [])
            ok3 = any("Иван" in m.get("content", "")
                      and "отвечай ровно одним словом" in m.get("content", "")
                      for m in p3 if m.get("role") == "system")
            ok4 = any("Мария" in m.get("content", "") for m in p4
                      if m.get("role") == "system")
            okt = any("⚠️ Табу" in m.get("content", "") and "мат" in m.get("content", "")
                      for m in pt if m.get("role") == "system")
            if (dones3 and dones4 and donest and ok3 and ok4 and okt):
                detail = (f"Иван(1 слово): {a3[:60]!r} | "
                          f"Мария: {a4[:60]!r} | "
                          f"гард: {at[:60]!r}")
                if a3.strip() == a4.strip():
                    detail += " [warn: ответы совпали — best-effort]"
                record("API: персонализация (профили → ответы + гард табу)",
                       "PASS", detail)
            else:
                record("API: персонализация (профили → ответы + гард табу)",
                       "FAIL",
                       f"dones={len(dones3)}/{len(dones4)}/{len(donest)} "
                       f"block3={ok3} block4={ok4} guard={okt} "
                       f"a3={a3[:40]!r} a4={a4[:40]!r}")
                return 1

            # 8c.2 Инварианты live (день 14): добавлен инвариант → запрос,
            #     содержащий его ключ и противоречащее значение → server-side
            #     гард добавляет system-напоминание в тело LLM-запроса
            #     (детерминированно, до LLM). Само напоминание проверяется в
            #     журнале — как у табу-гарда (8c) и конфликта памяти (день 11).
            code, body, _ = http("POST", "/api/invariants",
                                 {"title": "e2e-inv", "description": "Kotlin",
                                  "forbidden": ["python"], "is_active": True})
            live_inv = json.loads(body).get("invariant", {}) if code == 201 else {}
            live_inv_id = live_inv.get("id")
            if live_inv_id:
                inv_id = live_inv_id
            _, rawinv, _ = http("POST", "/api/chat",
                                {"dialogue_id": dlg3_id,
                                 "message":
                                     "e2e-inv — напиши на Python"},
                                timeout=CHAT_TIMEOUT)
            dones_inv = parse_sse(rawinv)[1]
            # журнал: найти последний запрос, в system-сообщениях которого
            # есть напоминание об инварианте
            code, body, _ = http("GET", "/api/requests")
            inv_req_ids = [r["id"] for r in json.loads(body)
                           .get("requests", [])][-2:]
            inv_reminder = False
            for rid in inv_req_ids:
                code, body, _ = http("GET", f"/api/requests/{rid}")
                if code != 200:
                    continue
                msgs = json.loads(body).get("request", {}).get("messages", [])
                sys_txt = " ".join(m.get("content", "") for m in msgs
                                   if m.get("role") == "system")
                if "инвариант" in sys_txt and "e2e-inv" in sys_txt:
                    inv_reminder = True
                    break
            if dones_inv and inv_reminder:
                record("API: инварианты live (гард → system-напоминание)",
                       "PASS")
            else:
                record("API: инварианты live (гард → system-напоминание)",
                       "FAIL",
                       f"dones={len(dones_inv)} reminder={inv_reminder}")
                return 1
            code, _, _ = http("DELETE", f"/api/invariants/{live_inv_id}")
            inv_id = None

        # 8d. Задача (день 13b): пошаговый пайплайн — SKIP, если GPustack
        #     недоступен (задача ходит в LLM, как и чат).
        if skip_reason:
            record("TASK: core (planning→done, маркеры, гарды)", "SKIP",
                   skip_reason)
            record("TASK: live пауза на границе work-шага", "SKIP",
                   skip_reason)
            record("TASK: live чат-режим после задачи", "SKIP",
                   skip_reason)
        else:
            # 8d.1 свой свежий диалог (первое сообщение — user-якорь
            #     задачи; в _cleanup диалог удаляется вместе с остальными)
            code, body, _ = http("POST", "/api/dialogues")
            task_dlg = json.loads(body).get("dialogue", {})
            did = task_dlg.get("id")
            task_dlg_id = did
            if code != 201 or not did:
                record("TASK: core (planning→done, маркеры, гарды)", "FAIL",
                       f"dialogue code={code}")
                return 1

            # 8d.2 старт: schema (task_id, stage=planning, plan из 4)
            code, raw, _ = http("POST", "/api/task/start",
                                {"dialogue_id": did,
                                 "description":
                                     "Составь план запуска тестовой "
                                     "кампании"})
            t1 = json.loads(raw)["task"] if code == 200 else {}
            t1_id = t1.get("task_id", "")
            start_ok = (code == 200 and t1_id.startswith("t_")
                        and t1.get("stage") == "planning"
                        and len(t1.get("plan", [])) == 4
                        and t1.get("active") is True)

            # 8d.3 user-маркер: первое сообщение диалога — {role: user,
            #     task_id}
            code, body, _ = http("GET", f"/api/dialogues/{did}")
            msgs1 = json.loads(body).get("dialogue", {}).get("messages", [])
            anchor_ok = (bool(msgs1) and msgs1[0].get("role") == "user"
                         and msgs1[0].get("task_id") == t1_id)

            # 8d.4 run: SSE читать до конца (реальный LLM); структура
            #     событий: агент спавнится, ≥1 work-шаг (in_progress →
            #     delta → completed), стадии по pipeline, task_done.
            #     Число/имена work-шагов — от планировщика (1–5) — не
            #     проверяем, только порядок и наличие. LLM-вызов work-
            #     шага может упасть («Пустой ответ модели» — флеш-
            #     модели): задача → failed, повторный run — auto-
            #     resume (200, семантика «Повтор») с первой
            #     невыполненной стадии. До 4 попыток.
            evs = []
            attempts = 0
            stage_now = None
            try:
                for attempt in range(1, 5):
                    attempts = attempt
                    code, raw, _ = http("POST", "/api/task/run",
                                        {"dialogue_id": did},
                                        timeout=900)
                    evs.extend(parse_task_sse(raw))
                    c_st, body_st, _ = http("GET",
                                            f"/api/task?dialogue_id={did}")
                    stage_now = json.loads(body_st)["task"].get("stage")
                    if stage_now != "failed":
                        break
            except Exception as e:
                record("TASK: core (planning→done, маркеры, гарды)", "FAIL",
                       f"run: timeout/ошибка сети на попытке "
                       f"{attempts}: {e}")
                return 1
            types = [e["type"] for e in evs]

            def _idx(stage, etype="agent_spawned"):
                for i, e in enumerate(evs):
                    if e["type"] == etype and e.get("stage") == stage:
                        return i
                return -1

            i_spawn_planning = _idx("planning")
            sd_planning = next((e for e in evs
                                if e["type"] == "stage_done"
                                and e.get("stage") == "planning"), None)
            i_spawn_exec = _idx("execution")
            steps_ip = [e for e in evs if e["type"] == "step_updated"
                        and e.get("status") == "in_progress"]
            steps_delta = [e for e in evs if e["type"] == "step_delta"]
            steps_done = [e for e in evs if e["type"] == "step_updated"
                          and e.get("status") == "completed"]
            i_sd_exec = _idx("execution", "stage_done")
            i_spawn_val = _idx("validation")
            i_sd_val = _idx("validation", "stage_done")
            i_spawn_done = _idx("done")
            i_sd_done = _idx("done", "stage_done")
            t_done = [e for e in evs if e["type"] == "task_done"]
            ip_idx = {s.get("index") for s in steps_ip}
            run_ok = (code == 200
                      and i_spawn_planning >= 0
                      and sd_planning is not None
                      and 1 <= len(sd_planning.get("plan", [])) <= 5
                      and i_spawn_exec > i_spawn_planning
                      and len(steps_ip) >= 1 and len(steps_delta) >= 1
                      and len(steps_done) >= 1
                      and all(e.get("index") in ip_idx
                              for e in steps_delta)
                      and i_sd_exec > i_spawn_exec
                      and i_spawn_val > i_sd_exec
                      and i_sd_val > i_spawn_val
                      and i_spawn_done > i_sd_val
                      and i_sd_done > i_spawn_done
                      and len(t_done) >= 1
                      and (t_done[-1].get("answer") or "").strip()
                      and ("task_failed" not in types
                           or "task_resumed" in types)
                      and "error" not in types)

            # 8d.5 состояние после run: stage=done, plan все completed,
            #     work_steps completed с outputs
            code, body, _ = http("GET", f"/api/task?dialogue_id={did}")
            t_after = json.loads(body)["task"]
            state_ok = (t_after.get("stage") == "done"
                        and t_after.get("task_id") == t1_id
                        and len(t_after.get("plan", [])) == 4
                        and all(e.get("status") == "completed"
                                for e in t_after.get("plan", []))
                        and len(t_after.get("work_steps", [])) >= 1
                        and all(w.get("status") == "completed"
                                and (w.get("output") or "").strip()
                                for w in t_after.get("work_steps", [])))

            # 8d.6 маркеры сообщений: assistant с task_stage
            #     (planning / execution+task_step / validation) +
            #     финальный assistant БЕЗ task_stage с тем же task_id
            code, body, _ = http("GET", f"/api/dialogues/{did}")
            msgs2 = json.loads(body).get("dialogue", {}).get("messages", [])
            a_plan = [m for m in msgs2 if m.get("role") == "assistant"
                      and m.get("task_stage") == "planning"
                      and m.get("task_id") == t1_id]
            a_exec = [m for m in msgs2 if m.get("role") == "assistant"
                      and m.get("task_stage") == "execution"
                      and m.get("task_step") and m.get("task_id") == t1_id]
            a_val = [m for m in msgs2 if m.get("role") == "assistant"
                     and m.get("task_stage") == "validation"
                     and m.get("task_id") == t1_id]
            a_final = [m for m in msgs2 if m.get("role") == "assistant"
                       and m.get("task_id") == t1_id
                       and "task_stage" not in m]
            markers_ok = (len(a_plan) >= 1 and len(a_exec) >= 1
                          and len(a_val) >= 1 and len(a_final) >= 1)

            # 8d.7 400-гарды на завершённой задаче: pause / instruction
            #     (вне паузы) / run — все 400
            c_p0, _, _ = http("POST", "/api/task/pause",
                              {"dialogue_id": did})
            c_i0, _, _ = http("POST", "/api/task/instruction",
                             {"dialogue_id": did, "text": "вне паузы"})
            c_r0, raw_r0, _ = http("POST", "/api/task/run",
                                   {"dialogue_id": did}, timeout=30)
            run_r0_txt = raw_r0.decode("utf-8", "replace")
            done_guards_ok = (c_p0 == 400 and c_i0 == 400 and c_r0 == 400
                              and "Задача завершена" in run_r0_txt)

            # 8d.8 новая задача после done (D9): новый task_id, второй
            #     user-якорь в диалоге
            code, raw, _ = http("POST", "/api/task/start",
                                {"dialogue_id": did,
                                 "description":
                                     "Подготовь список метрик кампании"})
            t2 = json.loads(raw)["task"] if code == 200 else {}
            code, body, _ = http("GET", f"/api/dialogues/{did}")
            msgs3 = json.loads(body).get("dialogue", {}).get("messages", [])
            anchors = [m for m in msgs3 if m.get("role") == "user"
                       and m.get("task_id")]
            new_task_ok = (code == 200 and t2.get("task_id", "")
                           .startswith("t_")
                           and t2.get("task_id") != t1_id
                           and t2.get("stage") == "planning"
                           and len(anchors) == 2)

            # 8d.9 гард чата: активная незавершённая задача (новая,
            #     stage=planning) → SSE error, сообщение не сохраняется
            code, body, _ = http("GET", f"/api/dialogues/{did}")
            n_before = len(json.loads(body).get("dialogue", {})
                           .get("messages", []))
            code, raw, _ = http("POST", "/api/chat",
                                {"dialogue_id": did, "message": "привет"},
                                timeout=60)
            code, body, _ = http("GET", f"/api/dialogues/{did}")
            n_after = len(json.loads(body).get("dialogue", {})
                          .get("messages", []))
            chat_guard_ok = ("Задача выполняется" in raw.decode(
                "utf-8", "replace") and n_before == n_after)

            # 8d.10 reset → неактивная задача
            code, raw, _ = http("POST", "/api/task/reset",
                                {"dialogue_id": did})
            t5 = json.loads(raw)["task"] if code == 200 else {}
            reset_ok = (code == 200 and t5.get("active") is False
                        and t5.get("stage") is None
                        and t5.get("task_id") is None)

            core_ok = (start_ok and anchor_ok and run_ok and state_ok
                       and markers_ok and done_guards_ok and new_task_ok
                       and chat_guard_ok and reset_ok)
            record("TASK: core (planning→done, маркеры, гарды)",
                   "PASS" if core_ok else "FAIL",
                   f"start={start_ok} anchor={anchor_ok} run={run_ok} "
                   f"state={state_ok} markers={markers_ok} "
                   f"done_guards={done_guards_ok} "
                   f"new_task={new_task_ok} chat_guard={chat_guard_ok} "
                   f"reset={reset_ok} runs={attempts} events={len(evs)}")
            if not core_ok:
                return 1

            # 8d-live.1 пауза на границе work-шага (best-effort: живые
            #     агенты, race паузы с быстрой моделью — SKIP, не FAIL):
            #     start → run в потоке → polling до stage=execution →
            #     pause → task_paused + context_snapshot → instruction →
            #     resume → run → task_done.
            code, raw, _ = http("POST", "/api/task/start",
                                {"dialogue_id": did,
                                 "description":
                                     "Сравни два тарифа подписки"})
            l_status, l_detail = "PASS", ""
            if code != 200:
                l_status, l_detail = "SKIP", f"start code={code}"
            else:
                box = {}
                def run_task_live():
                    c, r, _ = http("POST", "/api/task/run",
                                   {"dialogue_id": did}, timeout=900)
                    box["code"], box["raw"] = c, r
                th = threading.Thread(target=run_task_live, daemon=True)
                th.start()
                stage_now = None
                deadline = time.time() + 300
                while time.time() < deadline and th.is_alive():
                    time.sleep(0.5)
                    c, r, _ = http("GET", f"/api/task?dialogue_id={did}")
                    stage_now = json.loads(r)["task"].get("stage")
                    if stage_now in ("execution", "paused",
                                     "failed", "done"):
                        break
                c_p, raw_p, _ = http("POST", "/api/task/pause",
                                     {"dialogue_id": did})
                th.join(timeout=180)
                c_g, r_g, _ = http("GET", f"/api/task?dialogue_id={did}")
                st_g = json.loads(r_g)["task"]
                raw1 = box.get("raw", b"") or b""
                paused_ev = b'"task_paused"' in raw1
                if c_p == 200 and st_g.get("stage") == "paused" \
                        and paused_ev:
                    snap = st_g.get("context_snapshot") or {}
                    snap_ok = bool(snap.get("description")) \
                        and isinstance(snap.get("work_steps"), list) \
                        and len(snap["work_steps"]) >= 1
                    c_i, raw_i, _ = http("POST", "/api/task/instruction",
                                         {"dialogue_id": did,
                                          "text": "Сделай акцент "
                                                  "на метриках"})
                    ti = json.loads(raw_i)["task"] if c_i == 200 else {}
                    c_r, raw_r, _ = http("POST", "/api/task/resume",
                                         {"dialogue_id": did})
                    # повторные run = auto-resume (как в core)
                    run_err = None
                    ev2 = []
                    c2 = 0
                    for _att in range(1, 4):
                        try:
                            c2, r2, _ = http("POST", "/api/task/run",
                                             {"dialogue_id": did},
                                             timeout=900)
                        except Exception as e:
                            c2, r2, run_err = 0, b"", str(e)
                            break
                        ev2 = parse_task_sse(r2)
                        c_ch, body_ch, _ = http("GET",
                                                f"/api/task?dialogue_id={did}")
                        if json.loads(body_ch)["task"].get("stage") \
                                != "failed":
                            break
                    d2 = [e for e in ev2
                          if e["type"] == "task_done"]
                    c_g2, r_g2, _ = http("GET",
                                         f"/api/task?dialogue_id={did}")
                    st_g2 = json.loads(r_g2)["task"]
                    ok2 = (snap_ok and c_i == 200
                           and ti.get("expected_action") == "human_input"
                           and c_r == 200 and c2 == 200
                           and d2 and (d2[-1].get("answer") or "").strip()
                           and st_g2.get("stage") == "done"
                           and "task_failed" not in
                           [e["type"] for e in ev2])
                    l_detail = (f"pause={c_p} paused_event=True "
                                f"snapshot={snap_ok} instr={c_i} "
                                f"resume={c_r} run={c2} "
                                f"done={'yes' if d2 else 'no'} "
                                f"stage={st_g2.get('stage')}")
                    if run_err:
                        l_detail += f" run_err={run_err[:60]}"
                    if not ok2:
                        l_status = "SKIP"
                        l_detail += " (best-effort: сбой live-ветки)"
                else:
                    l_status = "SKIP"
                    l_detail = (f"pause={c_p} stage={st_g.get('stage')} "
                                f"paused_event={paused_ev} "
                                f"(задача уехала раньше паузы или LLM-сбой "
                                f"— best-effort)")
                # чистый финал диалога до live-чата: задача не done —
                # reset (done — как есть, чат гард на done не срабатывает)
                c_fin, raw_fin, _ = http("GET",
                                         f"/api/task?dialogue_id={did}")
                st_fin = json.loads(raw_fin)["task"]
                if st_fin.get("stage") != "done":
                    http("POST", "/api/task/reset",
                         {"dialogue_id": did})
                record("TASK: live пауза на границе work-шага", l_status,
                       l_detail)

            # 8d-live.2 чат-режим после задачи (best-effort): обычный
            #     чат → done, новых task_id-маркеров нет. Profile-gate
            #     дня 12 не давать мешать — сначала decline (свой
            #     свежий диалог был pending).
            code, body, _ = http("GET", f"/api/dialogues/{did}")
            n_marks_before = len([m for m in json.loads(body)
                                  .get("dialogue", {}).get("messages", [])
                                  if m.get("task_id")])
            http("POST", "/api/profile/action",
                 {"dialogue_id": did, "action": "decline"})
            l2_status, l2_detail = "PASS", ""
            try:
                code, raw, _ = http("POST", "/api/chat",
                                    {"dialogue_id": did,
                                     "message": "Скажи: OK"},
                                    timeout=CHAT_TIMEOUT)
                deltas, dones, errors = parse_sse(raw)
                code, body, _ = http("GET", f"/api/dialogues/{did}")
                msgs_now = json.loads(body).get("dialogue", {}) \
                    .get("messages", [])
                n_marks_after = len([m for m in msgs_now
                                     if m.get("task_id")])
                last_user = [m for m in msgs_now
                             if m.get("role") == "user"][-1]
                ok2 = (code == 200 and dones and not errors
                       and n_marks_after == n_marks_before
                       and "task_id" not in last_user)
                l2_detail = (f"code={code} dones={len(dones)} "
                             f"errors={len(errors)} "
                             f"marks={n_marks_before}→{n_marks_after}")
                if not ok2:
                    l2_status = "SKIP"
                    l2_detail += " (best-effort: чат-ветка)"
            except Exception as e:
                l2_status = "SKIP"
                l2_detail = f"timeout/ошибка сети: {e} (best-effort)"
            record("TASK: live чат-режим после задачи", l2_status,
                   l2_detail)

        # 9. токены
        code, body, _ = http("GET", "/api/tokens")
        tok = json.loads(body)
        if code == 200 and "session" in tok and "context_limit" in tok:
            record(f"API: tokens (limit={tok['context_limit']})", "PASS")
        else:
            record("API: tokens", "FAIL", f"code={code} body={body[:120]!r}")
            return 1

        # 10. журнал запросов
        code, body, _ = http("GET", "/api/requests")
        reqs = json.loads(body).get("requests", [])
        if code == 200 and isinstance(reqs, list):
            detail = f"{len(reqs)} записей"
            if not skip_reason and reqs:
                detail += f", last model={reqs[-1].get('model')}"
            record(f"API: requests ({detail})", "PASS")
        else:
            record("API: requests", "FAIL", f"code={code}")
            return 1

        fails = [r for r in RESULTS if r[1] == "FAIL"]
        skips = [r for r in RESULTS if r[1] == "SKIP"]
        log(f"RESULT: {len(RESULTS) - len(fails) - len(skips)} PASS, "
            f"{len(skips)} SKIP, {len(fails)} FAIL")
        return 1 if fails else 0
    finally:
        _cleanup([dlg_id, dlg2_id, prof_dlg_id, dlg3_id, dlg4_id,
                  task_dlg_id], orig_model, inv_ids=[inv_id])
        stop_server(proc)


if __name__ == "__main__":
    sys.exit(main())
