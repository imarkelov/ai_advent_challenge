# -*- coding: utf-8 -*-
"""E2E day 25 «Мини-чат с RAG + памятью задачи (production-like)»

Offline Part A (обязана проходить без сети/GPUStack, MUST PASS;
  TestClient + fake LLM + tmp-БЗ только egg_book, net cut):
   A1 сценарий 1 (12 сообщений, 1 диалог): на каждом шаге POST /api/chat
      (RAG-вкл): done; done.task_state == скриптованное состояние
      после хода; сохранённое assistant-сообщение: rag_context
      (chunks непусто + у каждого чанка source/file/section/chunk_id
      и text непусто) OR rag_context.dont_know == true
   A2 сценарий 2 (10 сообщений, другой диалог, другие goal/уточнения/
      ограничения): то же, что A1, + изоляция: task_state диалога A1
      после сценария 2 НЕ изменился (per-диалог память)
   A3 инъекция: после хода с непустой памятью задачи в captured
      LLM-payload system содержит блок «Память задачи (день 25)» +
      goal (модель видит цель в каждом последующем запросе)
   A4 REST task-state: POST запись целого состояния -> 200 + persist;
      POST 400 (goal не строка / clarifications не список / поле
      отсутствует); GET 404 (неизвестный диалог)
   A5 персистентность: пересоздание agent/TestClient (имитация
      перезапуска сервера) — task_state обоих диалогов на месте
Live Part B (:8109, best-effort, SKIP без GPUStack):
    B1 wipe -> upload egg-фикстуры -> индексация (hash — без сети)
    B2 10-сообщенный сценарий в живом чате: 10/10 done (MUST);
       rag_context (chunks с source OR dont_know) на каждом — MUST;
       task_state после сценария: goal непуст и содержит >=1 ключевое
       слово из уточнений/ограничений сценария (best-effort WARNING)
Live Part C (:8109, flag --part-c-only; проверка задания дня 25):
    C0 wipe -> upload 2 фикстур (pitch_deck_brief.txt +
       backend_review_guide.txt) -> hash-индекс (без сети)
    C1 сценарий 1 (12 сообщений, живой LLM): done на каждом (MUST);
       источники (chunks OR dont_know) на каждом ответе (MUST);
       цель не теряется: task_state.goal, став непустым, остаётся
       непустым до конца сценария (MUST); goal-recall — ответ на
       «Перескажи цель…» содержит ключевое слово (WARNING)
    C2 сценарий 2 (10 сообщений, другой диалог): то же, что C1;
       изоляция: task_state диалога C1 после сценария 2 не изменился
       (MUST); goal-recall (WARNING)
    Отчёт: .omo/evidence/day25-long-scenarios/report.json
       (per-сообщение: ответ, источники, goal/уточнения/ограничения)

Запуск из корня репозитория:
    python scripts/e2e_day25.py [--part-a-only | --part-c-only]
Код возврата: 0 — успех (выбранная часть без FAIL), 1 — провал.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from typing import Any, Callable

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from dotenv import load_dotenv  # noqa: E402

# --- константы ---

PORT = 8109
BASE = f"http://127.0.0.1:{PORT}"
LOG_PREFIX = "[e2e-day25] "

WAIT_PORT_BUSY_MAX = 600
WAIT_PORT_STEP = 5
WAIT_SERVER_UP_MAX = 90
WAIT_SERVER_UP_STEP = 1
CHAT_TIMEOUT = 180
INDEX_TIMEOUT = 600

CANNED = "e2e-deterministic answer."
TS_BLOCK_MARK = "Память задачи (день 25)"

EGG_FIXTURE = os.path.join(REPO, "studio", "backend", "tests", "fixtures", "egg_book.txt")

_NO_NET = {
    "HTTP_PROXY": "http://127.0.0.1:1",
    "HTTPS_PROXY": "http://127.0.0.1:1",
    "http_proxy": "http://127.0.0.1:1",
    "https_proxy": "http://127.0.0.1:1",
    "NO_PROXY": "",
    "no_proxy": "",
}

_E2E_USAGE = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
              "completion_tokens_details": {"reasoning_tokens": 2}}

SERVER_LOG = os.path.join(REPO, "e2e_day25_server.log")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

RESULTS = []


def log(msg: str) -> None:
    print(LOG_PREFIX + msg, flush=True)


def record(step: str, status: str, detail: str = "") -> None:
    RESULTS.append({"step": step, "status": status, "detail": detail})
    line = f"{step}: {status}"
    if detail:
        line += f" — {detail}"
    log(line)


def print_summary() -> None:
    log("")
    log("=== Summary ===")
    for r in RESULTS:
        log(f"  {r['status']:>7}  {r['step']}" + (f" — {r['detail']}" if r["detail"] else ""))
    ok = sum(1 for r in RESULTS if r["status"] == "PASS")
    fails = sum(1 for r in RESULTS if r["status"] == "FAIL")
    skips = sum(1 for r in RESULTS if r["status"] == "SKIP")
    warns = sum(1 for r in RESULTS if r["status"] == "WARNING")
    log(f"  Итого: PASS={ok} FAIL={fails} SKIP={skips} WARNING={warns}")


# --- http-помощники для живого сервера ---


def port_open(port: int, timeout: float = 1.0) -> bool:
    import socket

    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


load_dotenv(os.path.join(REPO, ".env"))


def http(path: str, *, method: str = "GET", json_body: Any = None,
         timeout: int = 60) -> tuple[int, Any]:
    url = BASE + path
    hdrs: dict = {}
    data = None
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            code = resp.status
    except urllib.error.HTTPError as e:
        body = e.read()
        code = e.code
    except Exception as e:
        return -1, f"connection error: {e}"
    try:
        parsed = json.loads(body.decode("utf-8")) if body else None
    except Exception:
        parsed = body.decode("utf-8", errors="replace")
    return code, parsed


def probe_gpustack() -> tuple[bool, str]:
    base = os.environ.get("GPUSTACK_BASE_URL", "")
    key = os.environ.get("GPUSTACK_API_KEY", "")
    if not base or not key:
        return False, "нет GPUSTACK_BASE_URL/GPUSTACK_API_KEY в окружении"
    req = urllib.request.Request(base.rstrip("/") + "/models",
                                 headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
        return True, ""
    except urllib.error.HTTPError:
        return True, ""
    except Exception as e:
        return False, f"GPUStack недоступен: {e}"


# --- живой uvicorn-сервер ---


def wait_port_free(max_wait: int = WAIT_PORT_BUSY_MAX, step: int = WAIT_PORT_STEP) -> bool:
    waited = 0
    while port_open(PORT):
        if waited >= max_wait:
            return False
        log(f"порт {PORT} занят, ждём {step} с… ({waited}/{max_wait})")
        time.sleep(step)
        waited += step
    return True


def _kill_proc(proc: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, timeout=15)
        else:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
    except Exception:
        pass


def _dump_server_log(limit: int = 3000) -> None:
    try:
        with open(SERVER_LOG, "r", encoding="utf-8", errors="replace") as f:
            tail = f.read()[-limit:]
        for line in tail.splitlines():
            log("  | " + line)
    except Exception:
        pass


def start_server(env_extra: dict | None = None) -> subprocess.Popen | None:
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    log(f"запускаем uvicorn на порту {PORT} (лог: {SERVER_LOG})")
    proc = None
    with open(SERVER_LOG, "w", encoding="utf-8") as log_file:
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "studio.backend.main:app", "--port", str(PORT)],
            cwd=REPO, env=env, stdout=log_file, stderr=subprocess.STDOUT,
        )
        waited = 0
        while waited < WAIT_SERVER_UP_MAX:
            if proc.poll() is not None:
                log("uvicorn завершился раньше времени, хвост server.log:")
                _dump_server_log()
                return None
            if port_open(PORT):
                return proc
            time.sleep(WAIT_SERVER_UP_STEP)
            waited += WAIT_SERVER_UP_STEP
    log(f"сервер не поднялся за {WAIT_SERVER_UP_MAX} с, хвост server.log:")
    _dump_server_log()
    _kill_proc(proc)
    return None


def stop_server(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    deadline = time.time() + 15
    while time.time() < deadline:
        if proc.poll() is not None:
            break
        try:
            proc.terminate()
        except Exception:
            pass
        time.sleep(0.5)
    if proc.poll() is None:
        _kill_proc(proc)


# --- фейк LLM (httpx.MockTransport) ---


def _sse(frames: list[str]) -> str:
    return "".join(frames)


def _delta_chunk(text: str) -> str:
    return "data: " + json.dumps({"choices": [{"delta": {"content": text}}]},
                                 ensure_ascii=False) + "\n\n"


def _stop_chunk() -> str:
    return "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}],
                                  "usage": _E2E_USAGE}, ensure_ascii=False) + "\n\n"


def _llm_handler(captured: list, ts_queue: list) -> Callable:
    """Фейковый OpenAI-совместимый endpoint (GPUStack).

    stream -> SSE с CANNED-ответом. non-stream (обновление памяти задачи,
    день 25) -> JSON из ts_queue (по одному состоянию на вызов; пустой
    список -> {} = экстракция «не изменила»)."""

    def handler(request):
        import httpx

        payload = json.loads(request.content.decode("utf-8"))
        captured.append(payload)
        if payload.get("stream"):
            frames = [_delta_chunk(CANNED), _stop_chunk(), "[DONE]"]
            return httpx.Response(200, content=_sse(frames).encode("utf-8"))
        if ts_queue:
            state = ts_queue.pop(0)
        else:
            state = {}
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(state,
                                                                     ensure_ascii=False)}}]})

    return handler


# --- tmp-БЗ (Part A) ---


def _make_tmp_kb(root: str):
    """Tmp-БЗ: только egg_book.txt -> fixed-чанкинг, HashEmbedder (без сети)."""
    from kb import HashEmbedder, KnowledgeBase

    kb_dir = os.path.join(root, "kb")
    os.makedirs(os.path.join(kb_dir, "uploads"), exist_ok=True)
    with open(EGG_FIXTURE, "rb") as f:
        data = f.read()
    with open(os.path.join(kb_dir, "uploads", "egg_book.txt"), "wb") as f:
        f.write(data)
    kb = KnowledgeBase(kb_dir, root)
    kb.build("fixed", HashEmbedder())
    return kb


# --- SSE-парсинг для живого /api/chat ---


def parse_sse_events(body: str) -> list:
    events: list = []
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("data: "):
            continue
        raw = line[len("data: "):].strip()
        if not raw or raw == "[DONE]":
            continue
        try:
            events.append(json.loads(raw))
        except Exception:
            pass
    return events


def chat_sse(dialogue_id: str, message: str,
             timeout: int = CHAT_TIMEOUT) -> tuple[list | None, str | None]:
    """POST /api/chat (SSE) -> (список событий | None, ошибка | None)."""
    data = json.dumps({"dialogue_id": dialogue_id, "message": message},
                      ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(BASE + "/api/chat", data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}"
    except Exception as e:
        return None, f"connection error: {e}"
    return parse_sse_events(body), None


def last_asst(store, did: str) -> dict:
    msgs = (store.get_dialogue(did) or {}).get("messages") or []
    return next((m for m in reversed(msgs)
                 if m.get("role") == "assistant"), {})


def check_sources(rag_context) -> str:
    """Источники у RAG-ответа: chunks непусто и у каждого чанка
    source/file/section/chunk_id/text OR dont_know. '' — ок, строка — причина."""
    if not isinstance(rag_context, dict):
        return "нет rag_context в сообщении"
    if rag_context.get("dont_know"):
        return ""
    chunks = rag_context.get("chunks") or []
    if not chunks:
        return "chunks пуст (не dont_know)"
    for i, c in enumerate(chunks):
        for f in ("source", "file", "section", "chunk_id", "text"):
            if not c.get(f):
                return f"чанк #{i}: нет поля {f}"
    return ""


# --- сценарии (по 10–15 сообщений, задание дня 25) ---

# Сценарий 1: подготовка питч-дека, 12 сообщений. Уточнения и
# ограничения накапливаются по ходу; память задачи — пер-диалог.
SCENARIO_1 = {
    "title": "e25-a1-pitch",
    "goal": "готовый питч-дек для стартапа про AI-агентов",
    "clarifications": ["12 слайдов", "аудитория — венчурные инвесторы"],
    "constraints": ["только данные из базы знаний", "без воды"],
    "messages": [
        "Подготовь мне питч-дек для стартапа про AI-агентов",
        "Уточнение: слайдов нужно ровно 12, больше не надо",
        "Аудитория — венчурные инвесторы, сделай акцент на рынке",
        "Какие источники ты использовал по размеру рынка?",
        "Добавь ограничение: все цифры — только из базы знаний",
        "Теперь расскажи про конкурентов, с опорой на выдержки",
        "Ограничение: никакого маркетингового сленга и воды",
        "Какую структуру слайдов ты предложил?",
        "Что по плану на слайд 10 — финансовая модель?",
        "Перескажи цель: что мы в итоге готовим и для кого",
        "Какие ещё факты из базы можно добавить на слайд 6?",
        "Итог: список слайдов с источниками по каждому",
    ],
}

# Сценарий 2: тех-ревизия, 10 сообщений, другой диалог — своя память задачи.
SCENARIO_2 = {
    "title": "e25-a2-techreview",
    "goal": "план тех-ревизии бэкенда",
    "clarifications": ["фокус — производительность запросов"],
    "constraints": ["без миграции БД"],
    "messages": [
        "Составь план тех-ревизии бэкенда",
        "Уточнение: главный фокус — производительность запросов",
        "Ограничение: миграцию базы данных делать нельзя",
        "Какие риски ты видишь по текущей архитектуре?",
        "Какие источники указываешь по профилированию?",
        "Сколько этапов в плане ревизии?",
        "Что будет на первом этапе ревизии?",
        "Повтори: какая у нас цель ревизии и главное ограничение",
        "Добавь в план замер времени ответа до/после",
        "Итоговый план ревизии одним списком с источниками",
    ],
}

# Live-сценарий Part B: тот же, что SCENARIO_1, первые 10 сообщений.
SCENARIO_LIVE = {
    "title": "e25-b-live",
    "keywords": ["12", "инвестор", "база знаний", "воды", "сленг"],
    "messages": SCENARIO_1["messages"][:10],
}

# --- Part C: live 2 длинных сценария (проверка задания дня 25) ---

PITCH_FIXTURE = os.path.join(REPO, "studio", "backend", "tests",
                             "fixtures", "pitch_deck_brief.txt")
REVIEW_FIXTURE = os.path.join(REPO, "studio", "backend", "tests",
                              "fixtures", "backend_review_guide.txt")
REPORT_DIR = os.path.join(REPO, ".omo", "evidence", "day25-long-scenarios")

# goal-recall (best-effort WARNING): на «повтори/перескажи цель»-сообщении
# живой ответ ассистента должен упоминать цель сценария.
SCENARIO_1_RECALL_N = 10   # «Перескажи цель: что мы в итоге готовим и для кого»
SCENARIO_1_RECALL_KW = ["питч", "инвестор"]
SCENARIO_2_RECALL_N = 8    # «Повтори: какая у нас цель ревизии и главное ограничение»
SCENARIO_2_RECALL_KW = ["производительност", "миграци"]


# --- Part A: offline (TestClient + fake LLM, без uvicorn и сети) ---


def part_a() -> bool:
    log("=== Part A: офлайн (TestClient + fake LLM + tmp-БЗ, без сети) ===")
    import httpx
    from fastapi.testclient import TestClient

    sys.path.insert(0, os.path.join(REPO, "studio", "backend"))
    from agent import StudioAgent  # noqa: E402
    from main import create_app  # noqa: E402

    saved_env = {k: os.environ.get(k) for k in _NO_NET}
    os.environ.update(_NO_NET)
    tmp = tempfile.mkdtemp(prefix="e2e_day25_")
    try:
        kb = _make_tmp_kb(tmp)
        captured: list = []
        ts_queue: list = []  # скриптованные task_state для non-stream-вызовов

        def build_app():
            agent = StudioAgent(
                os.path.join(tmp, "data"),
                base_url="https://mock.local/v1",
                api_key="fake-e2e",
                client=httpx.Client(transport=httpx.MockTransport(
                    _llm_handler(captured, ts_queue))),
                kb=kb,
            )
            return agent, TestClient(create_app(agent, kb))

        agent, client = build_app()
        store = agent.store

        def setup_dialogue(st, title: str) -> str:
            d = st.new_dialogue()
            did = d["id"]
            st.rename_dialogue(did, title)   # до первого сообщения — без авто-тайтла
            st.profile_action(did, "decline")
            st.set_dialogue_rag(did, True)
            return did

        def chat(cl, did: str, question: str) -> dict:
            """POST /api/chat (SSE через TestClient) -> done-событие."""
            with cl.stream("POST", "/api/chat",
                           json={"dialogue_id": did, "message": question}) \
                    as resp:
                assert resp.status_code == 200, \
                    f"POST /api/chat -> {resp.status_code}"
                lines = list(resp.iter_lines())
            done: dict = {}
            for line in lines:
                line = (line or "").strip()
                if not line.startswith("data: "):
                    continue
                raw = line[len("data: "):].strip()
                if not raw or raw == "[DONE]":
                    continue
                try:
                    ev = json.loads(raw)
                except Exception:
                    continue
                if isinstance(ev, dict) and ev.get("type") == "done":
                    done = ev
            return done

        def run_scenario(cl, sc: dict, label: str) -> str:
            """Прогнать сценарий: на каждом user-сообщении — done,
            done.task_state == скриптованное состояние, источники у
            assistant-сообщения (chunks с source OR dont_know)."""
            did = setup_dialogue(store, sc["title"])
            ts_queue.extend([{"goal": sc["goal"],
                              "clarifications": sc["clarifications"],
                              "constraints": sc["constraints"]}
                             for _ in range(len(sc["messages"]))])
            all_ok = True
            for i, msg in enumerate(sc["messages"], start=1):
                try:
                    done = chat(cl, did, msg)
                except AssertionError as e:
                    record(f"{label} msg#{i}", "FAIL", str(e))
                    all_ok = False
                    break
                if not done:
                    record(f"{label} msg#{i}", "FAIL", "нет done-события")
                    all_ok = False
                    break
                # источники (задание: «всегда выводит источники»)
                m = last_asst(store, did)
                src_err = check_sources(m.get("rag_context"))
                if src_err:
                    record(f"{label} msg#{i} sources", "FAIL", src_err)
                    all_ok = False
                # память задачи в done (аддитивное поле дня 25)
                ts = done.get("task_state")
                if not isinstance(ts, dict) \
                        or ts.get("goal") != sc["goal"] \
                        or ts.get("clarifications") != sc["clarifications"] \
                        or ts.get("constraints") != sc["constraints"]:
                    record(f"{label} msg#{i} task_state", "FAIL",
                           f"done.task_state != скрипту: {ts!r}")
                    all_ok = False
            # persist в dialogues.json (авторитетное чтение)
            ts = store.get_task_state(did)
            if ts.get("goal") != sc["goal"] \
                    or ts.get("clarifications") != sc["clarifications"] \
                    or ts.get("constraints") != sc["constraints"]:
                record(f"{label} persist", "FAIL",
                       f"task_state в store != скрипту: {ts!r}")
                all_ok = False
            if all_ok:
                record(f"{label} {len(sc['messages'])} сообщений",
                       "PASS", f"done x{len(sc['messages'])}, "
                               f"источники x{len(sc['messages'])}, "
                               f"task_state persist")
            return did

        # ---------- A1: сценарий 1 (12 сообщений) ----------
        did1 = run_scenario(client, SCENARIO_1, "A1 сценарий-1")

        # ---------- A2: сценарий 2 (10 сообщений, другой диалог) ----------
        ts1_snapshot = store.get_task_state(did1)
        did2 = run_scenario(client, SCENARIO_2, "A2 сценарий-2")
        # изоляция per-диалог: память диалога 1 не изменилась
        if store.get_task_state(did1) == ts1_snapshot:
            record("A2 изоляция per-диалог", "PASS")
        else:
            record("A2 изоляция per-диалог", "FAIL",
                   "task_state диалога 1 изменился после сценария 2")

        # ---------- A3: инъекция блока памяти задачи в LLM-payload ----------
        try:
            with_ts = [p for p in captured
                       if isinstance(p, dict) and p.get("stream")
                       and isinstance(p.get("messages"), list)
                       and p["messages"]
                       and TS_BLOCK_MARK in (p["messages"][0].get("content") or "")
                       and SCENARIO_1["goal"] in (p["messages"][0].get("content") or "")]
            if with_ts:
                record("A3 блок памяти задачи в system-payload", "PASS",
                       f"{len(with_ts)} payload(s) с блоком и goal")
            else:
                record("A3 блок памяти задачи в system-payload", "FAIL",
                       "ни один stream-payload не содержит блок + goal")
        except Exception as e:
            record("A3 блок памяти задачи в system-payload", "FAIL", repr(e))

        # ---------- A4: REST task-state ----------
        r2 = None
        try:
            r = client.post(f"/api/dialogues/{did2}/task-state",
                            json={"goal": "REST goal",
                                  "clarifications": ["а", "б"],
                                  "constraints": ["в"]})
            ok = r.status_code == 200 \
                and r.json().get("task_state", {}).get("goal") == "REST goal"
            if ok:
                r2 = client.get(f"/api/dialogues/{did2}/task-state")
                ok = r2.status_code == 200 \
                    and r2.json().get("task_state", {}).get("clarifications") == ["а", "б"]
            record("A4 POST/GET task-state 200", "PASS" if ok else "FAIL",
                   "" if ok else f"post={r.status_code} "
                                 f"get={r2.status_code if r2 else '-'}")
        except Exception as e:
            record("A4 POST/GET task-state 200", "FAIL", repr(e))
        try:
            r_bad = client.post(f"/api/dialogues/{did2}/task-state",
                                json={"goal": 42,
                                      "clarifications": ["x"],
                                      "constraints": []})
            r_bad2 = client.post(f"/api/dialogues/{did2}/task-state",
                                 json={"goal": "g",
                                       "clarifications": "не-список",
                                       "constraints": []})
            r_bad3 = client.post(f"/api/dialogues/{did2}/task-state",
                                 json={"clarifications": [], "constraints": []})
            r404 = client.get("/api/dialogues/unknown-ts-404/task-state")
            ok = r_bad.status_code == 400 and r_bad2.status_code == 400 \
                and r_bad3.status_code == 400 and r404.status_code == 404
            record("A4 POST 400 / GET 404", "PASS" if ok else "FAIL",
                   "" if ok else f"{r_bad.status_code}/{r_bad2.status_code}/"
                                 f"{r_bad3.status_code}/{r404.status_code}")
        except Exception as e:
            record("A4 POST 400 / GET 404", "FAIL", repr(e))

        # ---------- A5: персистентность после «перезапуска» ----------
        try:
            _agent2, client2 = build_app()   # новый экземпляр — те же файлы
            ts1 = store.get_task_state(did1)
            ts2 = store.get_task_state(did2)
            r1 = client2.get(f"/api/dialogues/{did1}/task-state")
            r2 = client2.get(f"/api/dialogues/{did2}/task-state")
            ok = r1.status_code == 200 and r2.status_code == 200 \
                and r1.json().get("task_state") == ts1 \
                and r2.json().get("task_state") == ts2 \
                and ts1.get("goal") == SCENARIO_1["goal"] \
                and ts2.get("goal") == "REST goal"
            record("A5 персистентность после перезапуска",
                   "PASS" if ok else "FAIL",
                   "" if ok else f"ts1={ts1!r} ts2={ts2!r}")
        except Exception as e:
            record("A5 персистентность после перезапуска", "FAIL", repr(e))

        return not any(r["status"] == "FAIL" for r in RESULTS)
    finally:
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(tmp, ignore_errors=True)


# --- Part B: live (best-effort) ---


def http_upload(path: str, filename: str, data: bytes,
                timeout: int = 120) -> tuple[int, Any]:
    """Multipart-upload (поле file) — urllib-аналог multipart-роута."""
    boundary = "----e25boundary"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: text/plain\r\n\r\n"
    ).encode("utf-8") + data + f"\r\n--{boundary}--\r\n".encode("utf-8")
    req = urllib.request.Request(
        BASE + path, data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            code = resp.status
    except urllib.error.HTTPError as e:
        raw = e.read()
        code = e.code
    except Exception as e:
        return -1, f"connection error: {e}"
    try:
        parsed = json.loads(raw.decode("utf-8")) if raw else None
    except Exception:
        parsed = raw.decode("utf-8", errors="replace")
    return code, parsed


def part_b() -> int:
    log("=== Part B: живой прогон :8109 (реальный GPustack LLM; без него -> SKIP) ===")
    if not wait_port_free():
        record("B: порт", "SKIP",
               f"порт {PORT} занят дольше {WAIT_PORT_BUSY_MAX} с (чужой сервер)")
        return 0
    ok, why = probe_gpustack()
    if not ok:
        record("B: GPustack", "SKIP", why)
        return 0
    proc = None
    orig_model = None
    try:
        proc = start_server()
        if proc is None:
            record("B: сервер", "FAIL", "uvicorn не поднялся")
            return 1

        # модель: первая доступная с GPustack, прежнюю сохраняем
        code, models = http("/api/models", timeout=30)
        live_model = None
        if code == 200 and isinstance(models, dict):
            mlist = models.get("models") or []
            if mlist:
                live_model = mlist[0].get("id") or mlist[0].get("name")
        code, cfg = http("/api/config", timeout=30)
        if code == 200 and isinstance(cfg, dict):
            orig_model = cfg.get("model")
        if live_model and live_model != orig_model:
            code, _ = http("/api/config", method="POST",
                           json_body={"model": live_model}, timeout=30)
            if code != 200:
                record("B: модель", "FAIL", f"не удалось установить модель {live_model}")
                return 1
        record("B: модель", "PASS", f"model={live_model or orig_model}")

        # B1: свежий корпус (только egg-фикстура) + hash-индекс (без сети)
        code, _ = http("/api/kb", method="DELETE", timeout=120)
        if code != 200:
            record("B1 корпус", "SKIP", f"DELETE /api/kb -> {code}")
            return 0
        with open(EGG_FIXTURE, "rb") as f:
            data = f.read()
        code, r = http_upload("/api/kb/upload", "egg_book.txt", data, timeout=120)
        if code != 200:
            record("B1 корпус", "SKIP", f"upload egg-фикстуры -> {code}: {r}")
            return 0
        code, r = http("/api/kb/index", method="POST",
                       json_body={"strategy": "fixed", "embedder": "hash"},
                       timeout=INDEX_TIMEOUT)
        if code != 200:
            record("B1 корпус", "SKIP",
                   f"hash-индексация не прошла -> {code}: {r}")
            return 0
        code, st = http("/api/kb/stats", timeout=30)
        chunks = ((st.get("stats") or {}).get("chunks")
                  if isinstance(st, dict) else None)
        record("B1 корпус", "PASS" if (chunks or 0) > 0 else "FAIL",
               f"embedder=hash, chunks={chunks}")
        if (chunks or 0) <= 0:
            return 1

        # B2: 10-сообщенный сценарий (SCENARIO_1[:10]) в живом чате
        code, d = http("/api/dialogues", method="POST", timeout=30)
        did = None
        if isinstance(d, dict):
            did = (d.get("dialogue") or {}).get("id") or d.get("id")
        if code != 201 or not did:
            record("B2 диалог", "FAIL", f"POST /api/dialogues -> {code}")
            return 1
        http(f"/api/dialogues/{did}/rename", method="POST",
             json_body={"title": SCENARIO_LIVE["title"]}, timeout=30)
        code, _ = http(f"/api/profile/action", method="POST",
                       json_body={"dialogue_id": did, "action": "decline"},
                       timeout=30)
        code, _ = http(f"/api/dialogues/{did}/rag", method="POST",
                       json_body={"rag": True}, timeout=30)
        if code != 200:
            record("B2 диалог", "FAIL", f"POST /rag -> {code}")
            return 1

        done_cnt = 0
        b2_fail = False
        for i, msg in enumerate(SCENARIO_LIVE["messages"], start=1):
            evs, err = chat_sse(did, msg)
            if err is not None or evs is None:
                record(f"B2 msg#{i}", "FAIL", err or "SSE error")
                b2_fail = True
                continue
            done = next((e for e in reversed(evs) if e.get("type") == "done"), None)
            if done is None:
                record(f"B2 msg#{i}", "FAIL", "нет done-события")
                b2_fail = True
                continue
            done_cnt += 1
        if b2_fail or done_cnt != len(SCENARIO_LIVE["messages"]):
            record("B2 10-сообщенный сценарий", "FAIL",
                   f"done={done_cnt}/{len(SCENARIO_LIVE['messages'])}")
            return 1
        record("B2 10-сообщенный сценарий", "PASS",
               f"done={done_cnt}/{len(SCENARIO_LIVE['messages'])}")

        # B3: источники на каждом assistant-сообщении (chunks OR dont_know)
        code, dlg = http(f"/api/dialogues/{did}", timeout=30)
        msgs = []
        if isinstance(dlg, dict):
            rec = dlg.get("dialogue") if "dialogue" in dlg else dlg
            if isinstance(rec, dict):
                msgs = rec.get("messages") or []
        asst = [m for m in msgs if m.get("role") == "assistant"]
        src_ok = 0
        for m in asst:
            if not check_sources(m.get("rag_context")):
                src_ok += 1
        if src_ok == len(asst) and len(asst) >= len(SCENARIO_LIVE["messages"]):
            record("B3 источники на каждом ответе", "PASS",
                   f"sources={src_ok}/{len(asst)} (chunks OR dont_know)")
        else:
            record("B3 источники на каждом ответе", "FAIL",
                   f"sources={src_ok}/{len(asst)}")
            return 1

        # B4: память задачи — goal непуст и содержит >=1 ключевого слова
        # (best-effort: живая модель, не FAIL)
        code, ts = http(f"/api/dialogues/{did}/task-state", timeout=30)
        goal = ""
        if code == 200 and isinstance(ts, dict):
            goal = ((ts.get("task_state") or {}).get("goal") or "")
        low = goal.lower()
        hit = [kw for kw in SCENARIO_LIVE["keywords"] if kw.lower() in low]
        if code != 200 or not goal.strip():
            record("B4 task_state.goal (live)", "WARNING",
                   f"goal пуст или endpoint недоступен: code={code} "
                   f"goal={goal!r} (extrakция best-effort)")
        elif hit:
            record("B4 task_state.goal (live)", "PASS",
                   f"goal={goal!r}, ключевых слов: {hit}")
        else:
            record("B4 task_state.goal (live)", "WARNING",
                   f"goal={goal!r}, ни одного ключевого слова из {SCENARIO_LIVE['keywords']}")
        return 0
    except Exception as e:
        record("B: непредвиденное исключение", "FAIL", repr(e))
        return 1
    finally:
        if proc is not None:
            if orig_model:
                http("/api/config", method="POST",
                     json_body={"model": orig_model}, timeout=30)
            stop_server(proc)


# --- Part C: live 2 длинных сценария (проверка задания дня 25) ---


def _new_dialogue(title: str) -> str | None:
    """POST /api/dialogues + rename + decline-профиля + RAG-вкл."""
    code, d = http("/api/dialogues", method="POST", timeout=30)
    did = None
    if isinstance(d, dict):
        did = (d.get("dialogue") or {}).get("id") or d.get("id")
    if code != 201 or not did:
        return None
    http(f"/api/dialogues/{did}/rename", method="POST",
         json_body={"title": title}, timeout=30)
    http("/api/profile/action", method="POST",
         json_body={"dialogue_id": did, "action": "decline"}, timeout=30)
    code, _ = http(f"/api/dialogues/{did}/rag", method="POST",
                   json_body={"rag": True}, timeout=30)
    return did if code == 200 else None


def _get_dialogue_messages(did: str) -> list:
    code, dlg = http(f"/api/dialogues/{did}", timeout=30)
    if isinstance(dlg, dict):
        rec = dlg.get("dialogue") if "dialogue" in dlg else dlg
        if isinstance(rec, dict):
            return rec.get("messages") or []
    return []


def _run_live_scenario(did: str, sc: dict, label: str,
                       scenarios: dict) -> tuple[int, int]:
    """Один live-сценарий: на каждом сообщении done (MUST) + источники
    (MUST) + goal из done.task_state (отчёт). Возврат (done_cnt, src_ok)."""
    done_cnt = 0
    sources_ok = 0
    entry = {"dialogue_id": did, "messages": []}
    for i, msg in enumerate(sc["messages"], start=1):
        evs, err = chat_sse(did, msg)
        if err is not None or evs is None:
            record(f"{label} msg#{i}", "FAIL", err or "SSE error")
            continue
        done = next((e for e in reversed(evs) if e.get("type") == "done"), None)
        if done is None:
            record(f"{label} msg#{i}", "FAIL", "нет done-события")
            continue
        done_cnt += 1
        # источники на каждом ответе (chunks с полями OR dont_know)
        msgs = _get_dialogue_messages(did)
        asst = next((m for m in reversed(msgs)
                     if m.get("role") == "assistant"), {})
        src_err = check_sources(asst.get("rag_context"))
        if src_err:
            record(f"{label} msg#{i} sources", "FAIL", src_err)
        else:
            sources_ok += 1
        # память задачи — done.task_state (обновлено после хода)
        ts = done.get("task_state") or {}
        goal = (ts.get("goal") or "").strip()
        entry["messages"].append({
            "n": i, "user": msg,
            "answer_head": (done.get("answer") or "")[:200],
            "sources_ok": not src_err,
            "goal": goal,
            "clarifications": ts.get("clarifications"),
            "constraints": ts.get("constraints"),
        })
        log(f"  [{label}] msg#{i}: done, sources={'ok' if not src_err else 'FAIL'}, "
            f"goal={goal[:60]!r}")
    scenarios[sc["title"]] = entry
    return done_cnt, sources_ok


def _check_goal_held(label: str, entry: dict) -> None:
    """Цель не теряется: goal, став непустым, не пустеет до конца сценария.
    Goal так и не заполнился — WARNING (экстракция best-effort)."""
    goals = [m["goal"] for m in entry["messages"]]
    if not goals:
        record(f"{label} цель", "WARNING", "нет ни одного done-ответа")
        return
    first_i = next((i for i, g in enumerate(goals) if g), None)
    if first_i is None:
        record(f"{label} цель", "WARNING",
               "goal так и не заполнился (экстракция best-effort)")
        return
    lost = [i + 1 for i, g in enumerate(goals) if i > first_i and not g]
    if lost:
        record(f"{label} цель", "FAIL",
               f"goal потерялся на msg {lost} (непуст с msg {first_i + 1})")
    else:
        record(f"{label} цель", "PASS",
               f"goal непуст с msg {first_i + 1} до msg {len(goals)} "
               f"(финал: {goals[-1][:70]!r})")


def _check_goal_recall(label: str, entry: dict, msg_n: int,
                       keywords: list) -> None:
    """Best-effort (WARNING): ответ на «повтори цель»-сообщение упоминает цель."""
    m = next((x for x in entry["messages"] if x["n"] == msg_n), None)
    if m is None:
        return
    low = m["answer_head"].lower()
    hit = [k for k in keywords if k in low]
    if hit:
        record(f"{label} goal-recall (live)", "PASS",
               f"ответ msg#{msg_n} упоминает цель: {hit}")
    else:
        record(f"{label} goal-recall (live)", "WARNING",
               f"ответ msg#{msg_n} не упоминает ключевые слова {keywords}")


def part_c() -> int:
    log("=== Part C: live 2 длинных сценария (12+10 сообщений; проверка задания дня 25) ===")
    start = len(RESULTS)
    if not wait_port_free():
        record("C: порт", "SKIP",
               f"порт {PORT} занят дольше {WAIT_PORT_BUSY_MAX} с (чужой сервер)")
        return 0
    ok, why = probe_gpustack()
    if not ok:
        record("C: GPustack", "SKIP", why)
        return 0
    proc = None
    orig_model = None
    report: dict = {"model": None, "scenarios": {}}
    try:
        proc = start_server()
        if proc is None:
            record("C: сервер", "FAIL", "uvicorn не поднялся")
            return 1

        # модель: первая доступная с GPustack (паттерн Part B)
        code, models = http("/api/models", timeout=30)
        live_model = None
        if code == 200 and isinstance(models, dict):
            mlist = models.get("models") or []
            if mlist:
                live_model = mlist[0].get("id") or mlist[0].get("name")
        code, cfg = http("/api/config", timeout=30)
        if code == 200 and isinstance(cfg, dict):
            orig_model = cfg.get("model")
        if live_model and live_model != orig_model:
            code, _ = http("/api/config", method="POST",
                           json_body={"model": live_model}, timeout=30)
            if code != 200:
                record("C: модель", "FAIL", f"не удалось установить модель {live_model}")
                return 1
        report["model"] = live_model or orig_model
        record("C: модель", "PASS", f"model={live_model or orig_model}")

        # C0: свежий корпус (2 фикстуры) + hash-индекс (без сети)
        code, _ = http("/api/kb", method="DELETE", timeout=120)
        if code != 200:
            record("C0 корпус", "SKIP", f"DELETE /api/kb -> {code}")
            return 0
        for path, name in ((PITCH_FIXTURE, "pitch_deck_brief.txt"),
                           (REVIEW_FIXTURE, "backend_review_guide.txt")):
            with open(path, "rb") as f:
                data = f.read()
            code, r = http_upload("/api/kb/upload", name, data, timeout=120)
            if code != 200:
                record("C0 корпус", "SKIP", f"upload {name} -> {code}: {r}")
                return 0
        code, r = http("/api/kb/index", method="POST",
                       json_body={"strategy": "fixed", "embedder": "hash"},
                       timeout=INDEX_TIMEOUT)
        if code != 200:
            record("C0 корпус", "SKIP", f"hash-индексация не прошла -> {code}: {r}")
            return 0
        code, st = http("/api/kb/stats", timeout=30)
        chunks = ((st.get("stats") or {}).get("chunks")
                  if isinstance(st, dict) else None)
        record("C0 корпус", "PASS" if (chunks or 0) > 0 else "FAIL",
               f"embedder=hash, chunks={chunks} (2 документа)")
        if (chunks or 0) <= 0:
            return 1

        # C1: сценарий 1 (12 сообщений)
        did1 = _new_dialogue(SCENARIO_1["title"])
        if not did1:
            record("C1 диалог", "FAIL", "POST /api/dialogues не создал диалог")
            return 1
        d1, s1 = _run_live_scenario(did1, SCENARIO_1, "C1", report["scenarios"])
        n1 = len(SCENARIO_1["messages"])
        if d1 == n1 and s1 == n1:
            record(f"C1 сценарий-1 ({n1} сообщений)", "PASS",
                   f"done={d1}/{n1}, источники={s1}/{n1}")
        else:
            record(f"C1 сценарий-1 ({n1} сообщений)", "FAIL",
                   f"done={d1}/{n1}, источники={s1}/{n1}")
        e1 = report["scenarios"][SCENARIO_1["title"]]
        _check_goal_held("C1", e1)
        _check_goal_recall("C1", e1, SCENARIO_1_RECALL_N, SCENARIO_1_RECALL_KW)

        # снапшот task_state диалога C1 (для изоляции)
        _, ts1_before = http(f"/api/dialogues/{did1}/task-state", timeout=30)
        ts1_before = (ts1_before or {}).get("task_state") \
            if isinstance(ts1_before, dict) else None

        # C2: сценарий 2 (10 сообщений, другой диалог)
        did2 = _new_dialogue(SCENARIO_2["title"])
        if not did2:
            record("C2 диалог", "FAIL", "POST /api/dialogues не создал диалог")
            return 1
        d2, s2 = _run_live_scenario(did2, SCENARIO_2, "C2", report["scenarios"])
        n2 = len(SCENARIO_2["messages"])
        if d2 == n2 and s2 == n2:
            record(f"C2 сценарий-2 ({n2} сообщений)", "PASS",
                   f"done={d2}/{n2}, источники={s2}/{n2}")
        else:
            record(f"C2 сценарий-2 ({n2} сообщений)", "FAIL",
                   f"done={d2}/{n2}, источники={s2}/{n2}")
        e2 = report["scenarios"][SCENARIO_2["title"]]
        _check_goal_held("C2", e2)
        _check_goal_recall("C2", e2, SCENARIO_2_RECALL_N, SCENARIO_2_RECALL_KW)

        # изоляция per-диалог: память C1 не изменилась во время C2
        _, ts1_after = http(f"/api/dialogues/{did1}/task-state", timeout=30)
        ts1_after = (ts1_after or {}).get("task_state") \
            if isinstance(ts1_after, dict) else None
        if ts1_before == ts1_after:
            record("C2 изоляция per-диалог", "PASS",
                   "task_state диалога C1 не изменился после сценария 2")
        else:
            record("C2 изоляция per-диалог", "FAIL",
                   f"task_state C1 изменился: {ts1_before!r} -> {ts1_after!r}")

        # отчёт
        os.makedirs(REPORT_DIR, exist_ok=True)
        report_path = os.path.join(REPORT_DIR, "report.json")
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=1)
        log(f"отчёт: {report_path}")
        return 1 if any(r["status"] == "FAIL" for r in RESULTS[start:]) else 0
    except Exception as e:
        record("C: непредвиденное исключение", "FAIL", repr(e))
        return 1
    finally:
        if proc is not None:
            if orig_model:
                http("/api/config", method="POST",
                     json_body={"model": orig_model}, timeout=30)
            stop_server(proc)


def main() -> int:
    ap = argparse.ArgumentParser(description="E2E day 25")
    ap.add_argument("--part-a-only", action="store_true",
                    help="только оффлайн Part A")
    ap.add_argument("--part-c-only", action="store_true",
                    help="только live Part C (2 длинных сценария; проверка задания)")
    args = ap.parse_args()
    log("E2E day 25: мини-чат с RAG + памятью задачи (production-like)")
    log(f"репозиторий: {REPO}")
    log(f"живой порт: {PORT} (Part B/C best-effort)")
    if args.part_c_only:
        c_rc = part_c()
        print_summary()
        return c_rc
    a_ok = part_a()
    if not a_ok:
        print_summary()
        return 1
    if args.part_a_only:
        print_summary()
        return 0
    b_rc = part_b()
    print_summary()
    return b_rc


if __name__ == "__main__":
    sys.exit(main())
