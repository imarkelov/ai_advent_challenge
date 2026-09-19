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
    prof_dlg_id: str | None = None
    dlg3_id: str | None = None
    dlg4_id: str | None = None
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

        # 7b. Тумблеры слоёв: wm off → отражается в /api/memory → restore on
        code, _, _ = http("POST", "/api/memory/toggles",
                          {"layer": "wm", "enabled": False})
        code2, body, _ = http("GET", "/api/memory")
        tog = json.loads(body).get("toggles", {})
        code3, _, _ = http("POST", "/api/memory/toggles",
                           {"layer": "wm", "enabled": True})
        if (code == 200 and code2 == 200 and code3 == 200
                and tog.get("wm") is False and tog.get("st") is True
                and tog.get("lt") is True):
            record("API: memory toggles (wm off/on)", "PASS")
        else:
            record("API: memory toggles (wm off/on)", "FAIL",
                   f"off={code} mem={code2} on={code3} toggles={tog}")
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
        _cleanup([dlg_id, dlg2_id, prof_dlg_id, dlg3_id, dlg4_id], orig_model)
        stop_server(proc)


if __name__ == "__main__":
    sys.exit(main())
