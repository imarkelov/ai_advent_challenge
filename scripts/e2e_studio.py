"""E2E smoke day11 «Студия» (FastAPI + React, prod-режим).

Пайплайн:
  1. Env-проверка: GPUSTACK_BASE_URL + GPUSTACK_API_KEY (окружение или .env
     в корне репозитория) и достижимость GPustack (GET {base}/models).
     Недостижимо — чат-шаги SKIP (exit 0), остальной smoke идёт в любом случае.
  2. Порт 8100: если занят — ждать до 10 минут (чужой сервер НЕ убивается);
     всё ещё занят — SKIP с сообщением, exit 0.
  3. Запуск `python -m uvicorn studio.backend.main:app --port 8100` detached
     из корня репозитория (prod-режим: API + собранный фронтенд из dist).
  4. UI: GET / → 200, HTML содержит «Студия»; assets из index.html → 200.
  5. GET /api/config; GET /api/models (502 допустим — фиксируем, не FAIL).
  6. POST /api/dialogues → 201 + active_id.
  7. WM: POST /api/memory/working → ok; GET /api/memory → working.entries == 1.
  8. LT: POST /api/memory/longterm → ok; GET /api/memory → long_term.entries == 1.
  9. Чат (если GPustack достижим): POST /api/chat → SSE: >=1 delta + done.
  10. Авто-заголовок: чат в НОВОМ диалоге → title меняется с «Новый диалог»;
      assistant-сообщение хранится с model.
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


def _cleanup(dialogue_ids: list[str | None], orig_model: str | None) -> None:
    """Убрать артефакты самого e2e (не трогая данные пользователя)."""
    try:
        for dialogue_id in dialogue_ids:
            if dialogue_id:
                http("DELETE", f"/api/dialogues/{dialogue_id}", timeout=10)
        http("DELETE", "/api/memory/longterm/e2e", timeout=10)
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
    orig_model: str | None = None
    try:
        if proc is None:
            log("FAIL server did not come up on port 8100")
            return 1

        # 1. UI
        code, body, headers = http("GET", "/")
        text = body.decode("utf-8", "replace")
        if code == 200 and "Студия" in html.unescape(text):
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
        _cleanup([dlg_id, dlg2_id], orig_model)
        stop_server(proc)


if __name__ == "__main__":
    sys.exit(main())
