# -*- coding: utf-8 -*-
"""E2E day 24 «Цитаты, источники и анти-галлюцинации»

Offline Part A (обязана проходить без сети/GPUStack, MUST PASS;
  TestClient + fake LLM + tmp-БЗ только egg_book, net cut):
  A1 10 контрольных вопросов (control_questions.json) × POST /api/chat
     в одном диалоге (RAG-вкл, min_score=0): каждый -> 200 done;
     (chunks непусто -> у каждого чанка source/file/section/chunk_id
     и text непусто) OR rag_context.dont_know == true
  A2 dont-know: вопрос без совпадений при min_score=0 (векторная нога
     отключена) -> done: answer == DONT_KNOW_TEXT (байт-в-байт),
     usage None, request_id None; сообщение: content == DONT_KNOW_TEXT,
     rag_context {recall_total: 0, reranked: False, chunks: [],
     dont_know: True}; ноль LLM-вызовов
  A3 CITE_RULE в system captured LLM-payload: есть при непустом
     kb_block (A1) и нет без чанков (A6, БЗ без индекса)
  A4 verbatim: text каждого non-dont-know чанка A1 subset сохранённого
     текста чанка в БЗ и len <= 300
  A5 chunk_id: каждый chunk_id non-dont-know rag_context A1 существует
     в индексе tmp-БЗ
  A6 регрессия shape дней 21/23: rag_context non-dont-know ответа
     (recall_total/reranked/chunks) + POST/GET /api/kb/settings
     min_score 0.5 -> 0 (валидация 1.5 -> 400) + БЗ без индекса
     (KBError) -> обычный чат (CANNED, 1 вызов, без dont_know/CITE_RULE)
Live Part B (:8108, best-effort, SKIP без GPUStack/API-эмбеддера):
  B1 wipe → fetch_books.py → индексация (api; API-эмбеддер недоступен
     -> SKIP всей Part B)
  B2 10 контрольных вопросов в одном диалоге: 10/10 done с непустым
     ответом (MUST); sources/citations <10/10 → WARNING
  B3 dont-know live (GPUSTACK_KEY_RERANK + min_score=0.999)
  B4 cleanup: min_score=0.0 + restore reranker/model (в finally)

Запуск из корня репозитория:
    python scripts/e2e_day24.py [--part-a-only]
Код возврата: 0 — успех (Part A весь PASS и Part B без FAIL), 1 — провал.
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

PORT = 8108
BASE = f"http://127.0.0.1:{PORT}"
LOG_PREFIX = "[e2e-day24] "

WAIT_PORT_BUSY_MAX = 600   # сек, ожидание освобождения порта (чужой сервер не убиваем)
WAIT_PORT_STEP = 5
WAIT_SERVER_UP_MAX = 90
WAIT_SERVER_UP_STEP = 1
CHAT_TIMEOUT = 180         # сек, живой чат с RAG (LLM-стрим)
INDEX_TIMEOUT = 900        # сек, api-индексация свежего корпуса (до 15 мин)
FETCH_TIMEOUT = 900        # сек, fetch_books.py (скачивание 5 книг)

Q_NOMATCH = "Какова квантовая хромодинамика и бозон Хиггса?"
Q_RAG = "Какой телефон был у героя книги про настройщика ксилофонов Аркадия?"
CANNED = "e2e-deterministic answer."

EGG_FIXTURE = os.path.join(REPO, "studio", "backend", "tests", "fixtures", "egg_book.txt")
CONTROL_Q = os.path.join(REPO, "studio", "backend", "tests", "fixtures", "control_questions.json")

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

SERVER_LOG = os.path.join(REPO, "e2e_day24_server.log")

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
        # сервис жив, но ключ/модель может не отвечать — LLM сам об этом скажет
        return True, ""
    except Exception as e:
        return False, f"GPUStack недоступен: {e}"


def probe_embedder_api() -> tuple[bool, str]:
    """Доступность API-эмбеддера (qwen3-vl-embedding-8b): ключ
    GPUSTACK_KEY_EMBED + живой POST /embeddings. Part B без него
    SKIP целиком (деградация на hash-эмбеддер спекой не допускается)."""
    base = os.environ.get("GPUSTACK_BASE_URL", "")
    key = os.environ.get("GPUSTACK_KEY_EMBED", "")
    if not base or not key:
        return False, "нет GPUSTACK_KEY_EMBED"
    body = json.dumps({"model": "qwen3-vl-embedding-8b",
                       "input": ["e2e probe"]}).encode("utf-8")
    req = urllib.request.Request(base.rstrip("/") + "/embeddings", data=body,
                                 method="POST",
                                 headers={"Authorization": f"Bearer {key}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
        return True, ""
    except Exception as e:
        return False, f"embeddings-зонд не удался: {e}"


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


def _llm_handler(captured: list) -> Callable:
    """Фейковый OpenAI-совместимый endpoint (GPUStack).

    Каждый payload (и stream, и non-stream) -> captured;
    stream -> SSE с usage; non-stream -> детерминированный CANNED-ответ.
    """

    def handler(request):
        import httpx

        payload = json.loads(request.content.decode("utf-8"))
        captured.append(payload)
        if payload.get("stream"):
            frames = [_delta_chunk(CANNED), _stop_chunk(), "[DONE]"]
            return httpx.Response(200, content=_sse(frames).encode("utf-8"))
        return httpx.Response(200, json={"choices": [{"message": {"content": CANNED}}]})

    return handler


# --- tmp-БЗ (Part A) ---


def _make_tmp_kb(root: str):
    """Tmp-БЗ: только egg_book.txt (2044 симв.) -> fixed-чанкинг 1200/200
    -> ровно 2 детерминированных чанка, HashEmbedder (без сети)."""
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


# --- Part A: offline (TestClient + fake LLM, без uvicorn и сети) ---


def part_a() -> bool:
    log("=== Part A: офлайн (TestClient + fake LLM + tmp-БЗ, без сети) ===")
    import httpx
    from fastapi.testclient import TestClient

    sys.path.insert(0, os.path.join(REPO, "studio", "backend"))
    from agent import CITE_RULE, DONT_KNOW_TEXT, StudioAgent  # noqa: E402
    from main import create_app  # noqa: E402  (lazy: читает .env при импорте)
    import kb as kb_module  # noqa: E402

    saved_env = {k: os.environ.get(k) for k in _NO_NET}
    os.environ.update(_NO_NET)
    tmp = tempfile.mkdtemp(prefix="e2e_day24_")
    ok = {"a1": False, "a2": False, "a3": False, "a4": False,
          "a5": False, "a6": False}
    non_dk_chunks: list = []  # non-dont-know чанки A1 (A4/A5)
    a1_rc_first: dict = {}    # rag_context первого non-dont-know (A6 shape)
    sys_with = ""             # system с непустым kb_block (A3)
    sys_without = ""          # system без чанков (A3)
    try:
        kb = _make_tmp_kb(tmp)
        idx = kb.load_index() or {}
        stored = {c.get("chunk_id"): (c.get("text") or "")
                  for c in (idx.get("chunks") or [])}

        captured: list = []
        agent = StudioAgent(
            os.path.join(tmp, "data"),
            base_url="https://mock.local/v1",
            api_key="fake-e2e",
            client=httpx.Client(transport=httpx.MockTransport(
                _llm_handler(captured))),
            kb=kb,
        )
        store = agent.store
        client = TestClient(create_app(agent, kb))

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

        def last_asst(st, did: str) -> dict:
            """Последнее assistant-сообщение диалога: rag_context живёт
            в сохранённом сообщении (append_message), а не в done-событии."""
            msgs = (st.get_dialogue(did) or {}).get("messages") or []
            return next((m for m in reversed(msgs)
                         if m.get("role") == "assistant"), {})

        # ---------- A1: 10 контрольных вопросов × POST /api/chat ----------
        A1_LABEL = "A1 10 контрольных вопросов /api/chat (sources+citations OR dont_know)"
        try:
            with open(CONTROL_Q, "r", encoding="utf-8") as f:
                questions = sorted(json.load(f), key=lambda q: q.get("id") or 0)
            rset = client.post("/api/kb/settings", json={"min_score": 0})
            assert rset.status_code == 200, \
                f"settings min_score=0 -> {rset.status_code}"
            did1 = setup_dialogue(store, "e24-a1")
            a1_ok = len(questions) == 10
            n_chunks_q = 0
            n_dk_q = 0
            for q in questions:
                qid = q.get("id")
                d = chat(client, did1, q["question"])
                rc = last_asst(store, did1).get("rag_context") or {}
                chunks = rc.get("chunks") or []
                if chunks:
                    n_chunks_q += 1
                    good = bool(d) and all(
                        c.get("source") is not None
                        and isinstance(c.get("file"), str)
                        and c.get("section") is not None
                        and isinstance(c.get("chunk_id"), str)
                        and c["chunk_id"] != ""
                        and isinstance(c.get("text"), str)
                        and c["text"] != ""
                        for c in chunks)
                    non_dk_chunks.extend(chunks)
                    if not a1_rc_first:
                        a1_rc_first = rc
                else:
                    n_dk_q += 1
                    good = bool(d) and rc.get("dont_know") is True
                if not good:
                    a1_ok = False
            ok["a1"] = a1_ok
            if captured:
                sys_with = ((captured[0].get("messages") or [{}])[0]
                            .get("content") or "")
            record(A1_LABEL, "PASS" if a1_ok else "FAIL",
                   f"q={len(questions)} chunks_q={n_chunks_q} "
                   f"dontknow_q={n_dk_q} calls={len(captured)}")
        except Exception as e:
            record(A1_LABEL, "FAIL", f"exception: {e!r}")

        # ---------- A2: dont-know (0 чанков, ноль LLM-вызовов) ----------
        orig_embed = kb_module.KnowledgeBase._embedder_from_index

        def _raise(self, idx_doc):
            raise kb_module.KBError("e2e: vector leg off")

        kb_module.KnowledgeBase._embedder_from_index = _raise
        try:
            did2 = setup_dialogue(store, "e24-a2")
            n0 = len(captured)
            d2 = chat(client, did2, Q_NOMATCH)
            m2 = last_asst(store, did2)
            rc2 = m2.get("rag_context") or {}
            ok["a2"] = (
                d2 is not None
                and d2.get("answer") == DONT_KNOW_TEXT
                and d2.get("usage") is None
                and d2.get("request_id") is None
                and rc2.get("recall_total") == 0
                and rc2.get("reranked") is False
                and rc2.get("chunks") == []
                and rc2.get("dont_know") is True
                and m2.get("content") == DONT_KNOW_TEXT
                and len(captured) == n0
            )
            record("A2 dont-know: DONT_KNOW_TEXT, 0 LLM-вызовов",
                   "PASS" if ok["a2"] else "FAIL",
                   f"answer_eq={d2.get('answer') == DONT_KNOW_TEXT if d2 else False} "
                   f"msg_eq={m2.get('content') == DONT_KNOW_TEXT} "
                   f"rc={m2.get('rag_context')} calls={len(captured) - n0}")
        except Exception as e:
            record("A2 dont-know: DONT_KNOW_TEXT, 0 LLM-вызовов", "FAIL",
                   f"exception: {e!r}")
        finally:
            kb_module.KnowledgeBase._embedder_from_index = orig_embed

        # ---------- A4: verbatim: non-dont-know чанки A1 ⊂ БЗ, len <= 300 ----------
        try:
            ok["a4"] = (
                bool(non_dk_chunks)
                and all(
                    isinstance(c.get("text"), str)
                    and len(c["text"]) <= 300
                    and c["text"] in (stored.get(c.get("chunk_id")) or "")
                    for c in non_dk_chunks
                )
            )
            record("A4 verbatim: цитата ⊂ текста чанка из БЗ, len <= 300",
                   "PASS" if ok["a4"] else "FAIL",
                   f"chunks={len(non_dk_chunks)} "
                   f"max_len={max((len(c.get('text') or '') for c in non_dk_chunks), default=0)}")
        except Exception as e:
            record("A4 verbatim: цитата ⊂ текста чанка из БЗ, len <= 300", "FAIL",
                   f"exception: {e!r}")

        # ---------- A5: chunk_id каждого non-dont-know чанка A1 в индексе ----------
        try:
            ok["a5"] = (
                bool(non_dk_chunks)
                and all(isinstance(c.get("chunk_id"), str)
                        and c["chunk_id"] in stored
                        for c in non_dk_chunks)
            )
            record("A5 chunk_id: все в индексе tmp-БЗ",
                   "PASS" if ok["a5"] else "FAIL",
                   f"chunks={len(non_dk_chunks)} "
                   f"in_index={sum(1 for c in non_dk_chunks if c.get('chunk_id') in stored)}")
        except Exception as e:
            record("A5 chunk_id: все в индексе tmp-БЗ", "FAIL",
                   f"exception: {e!r}")

        # ---------- A6: shape регрессия + min_score + БЗ без индекса ----------
        A6_LABEL = "A6 shape регрессия + min_score settings + без индекса (обычный чат)"
        cap6: list = []
        sys6 = ""
        try:
            shape_ok = {"recall_total", "reranked", "chunks"} <= set(a1_rc_first)

            r1 = client.post("/api/kb/settings", json={"min_score": 0.5})
            g1 = client.get("/api/kb/settings")
            r2 = client.post("/api/kb/settings", json={"min_score": 0})
            g2 = client.get("/api/kb/settings")
            rbad = client.post("/api/kb/settings", json={"min_score": 1.5})
            set_ok = (
                r1.status_code == 200
                and r1.json().get("min_score") == 0.5
                and g1.status_code == 200 and g1.json().get("min_score") == 0.5
                and r2.status_code == 200
                and g2.status_code == 200 and g2.json().get("min_score") == 0.0
                and rbad.status_code == 400
            )

            os.makedirs(os.path.join(tmp, "kempty"), exist_ok=True)
            kb_empty = kb_module.KnowledgeBase(os.path.join(tmp, "kempty"), tmp)
            agent6 = StudioAgent(
                os.path.join(tmp, "data6"),
                base_url="https://mock.local/v1",
                api_key="fake-e2e",
                client=httpx.Client(transport=httpx.MockTransport(
                    _llm_handler(cap6))),
                kb=kb_empty,
            )
            client6 = TestClient(create_app(agent6, kb_empty))
            did6 = setup_dialogue(agent6.store, "e24-a6")
            d6 = chat(client6, did6, Q_RAG)
            m6 = last_asst(agent6.store, did6)
            if cap6:
                sys6 = ((cap6[0].get("messages") or [{}])[0].get("content") or "")
                sys_without = sys6
            no_index_ok = (
                bool(d6)
                and d6.get("answer") == CANNED
                and len(cap6) == 1
                and (m6.get("rag_context") or {}).get("dont_know") is not True
                and CITE_RULE not in sys6
            )
            ok["a6"] = shape_ok and set_ok and no_index_ok
            record(A6_LABEL, "PASS" if ok["a6"] else "FAIL",
                   f"shape={shape_ok} settings={set_ok} no_index={no_index_ok} "
                   f"calls6={len(cap6)}")
        except Exception as e:
            record(A6_LABEL, "FAIL", f"exception: {e!r}")

        # ---------- A3: CITE_RULE только при непустом kb_block ----------
        ok["a3"] = (bool(sys_with) and CITE_RULE in sys_with
                    and bool(sys_without) and CITE_RULE not in sys_without)
        record("A3 CITE_RULE: есть с непустым блоком, нет без чанков",
               "PASS" if ok["a3"] else "FAIL",
               f"with_block={bool(sys_with) and CITE_RULE in sys_with} "
               f"without_absent={bool(sys_without) and CITE_RULE not in sys_without}")
    except Exception as e:
        record("A: непредвиденное исключение", "FAIL", repr(e))
    finally:
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(tmp, ignore_errors=True)
    n = sum(1 for v in ok.values() if v)
    log(f"Part A: {n}/6 PASS")
    return all(ok.values())


# --- Part B: live (best-effort) ---


def part_b() -> int:
    log("=== Part B: живой прогон :8108 (реальный GPustack; без него -> SKIP) ===")
    sys.path.insert(0, os.path.join(REPO, "studio", "backend"))
    from agent import DONT_KNOW_TEXT  # noqa: E402

    if not wait_port_free():
        record("B: порт", "SKIP",
               f"порт {PORT} занят дольше {WAIT_PORT_BUSY_MAX} с (чужой сервер)")
        return 0
    ok, why = probe_gpustack()
    if not ok:
        record("B: GPustack", "SKIP", why)
        return 0
    ok, why = probe_embedder_api()
    if not ok:
        # Спека: API-эмбеддер недоступен — SKIP всей Part B (не FAIL)
        for step in ("B1 корпус", "B2 10 контрольных вопросов",
                     "B2 sources/citations", "B3 dont-know", "B4 cleanup"):
            record(step, "SKIP", f"Part B: API-эмбеддер недоступен: {why}")
        return 0
    orig_model = None
    pre_settings = None
    proc = None
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

        # B1: свежий корпус (fetch_books.py) + индексация
        code, _ = http("/api/kb", method="DELETE", timeout=120)
        if code != 200:
            record("B1 корпус", "SKIP", f"DELETE /api/kb -> {code}")
            return 0
        uploads = os.path.join(REPO, "data", "kb", "uploads")
        os.makedirs(uploads, exist_ok=True)
        fetch = subprocess.run(
            [sys.executable, os.path.join(REPO, "scripts", "fetch_books.py"),
             "--out", uploads],
            capture_output=True, timeout=FETCH_TIMEOUT)
        if fetch.returncode != 0:
            tail = (fetch.stderr or fetch.stdout or b"").decode("utf-8", "replace")[-300:]
            record("B1 корпус", "SKIP", f"fetch_books.py exit {fetch.returncode}: {tail}")
            return 0
        code, r = http("/api/kb/index", method="POST",
                       json_body={"strategy": "structural", "embedder": "api"},
                       timeout=INDEX_TIMEOUT)
        if code != 200:
            record("B1 корпус", "SKIP",
                   f"индексация с api-эмбеддером не прошла -> {code}: {r}")
            return 0
        code, st = http("/api/kb/stats", timeout=30)
        chunks = ((st.get("stats") or {}).get("chunks")
                  if isinstance(st, dict) else None)
        record("B1 корпус", "PASS" if (chunks or 0) > 0 else "FAIL",
               f"embedder=api, chunks={chunks}")
        if (chunks or 0) <= 0:
            return 1

        # B2: 10 контрольных вопросов в одном диалоге (RAG on)
        code, d = http("/api/dialogues", method="POST", timeout=30)
        did = None
        if isinstance(d, dict):
            did = (d.get("dialogue") or {}).get("id") or d.get("id")
        if code != 201 or not did:
            record("B2 диалог", "FAIL", f"POST /api/dialogues -> {code}")
            return 1
        http(f"/api/dialogues/{did}/rename", method="POST",
             json_body={"title": "e24-b2"}, timeout=30)
        code, _ = http(f"/api/dialogues/{did}/rag", method="POST",
                       json_body={"rag": True}, timeout=30)
        if code != 200:
            record("B2 диалог", "FAIL", f"POST /rag -> {code}")
            return 1
        with open(CONTROL_Q, "r", encoding="utf-8") as f:
            questions = sorted(json.load(f), key=lambda q: q.get("id") or 0)
        done_cnt = 0
        b2_ok = True
        for q in questions:
            qid = q.get("id")
            evs, err = chat_sse(did, q["question"])
            if err is not None or evs is None:
                record(f"B2 q{qid}", "FAIL", err or "SSE error")
                b2_ok = False
                continue
            done = next((e for e in reversed(evs) if e.get("type") == "done"), None)
            if done is None or not (done.get("answer") or "").strip():
                record(f"B2 q{qid}", "FAIL", "нет done с непустым ответом")
                b2_ok = False
                continue
            done_cnt += 1
        code, dlg = http(f"/api/dialogues/{did}", timeout=30)
        msgs = []
        if isinstance(dlg, dict):
            rec = dlg.get("dialogue") if "dialogue" in dlg else dlg
            if isinstance(rec, dict):
                msgs = rec.get("messages") or []
        asst = [m for m in msgs if m.get("role") == "assistant"]
        sources_n = sum(1 for m in asst if (m.get("rag_context") or {}).get("chunks"))
        cites_n = sum(
            1 for m in asst
            if (m.get("rag_context") or {}).get("chunks")
            and all((c.get("text") or "").strip()
                    for c in (m.get("rag_context") or {}).get("chunks") or [])
        )
        record("B2 10 контрольных вопросов",
               "PASS" if done_cnt == len(questions) else "FAIL",
               f"done={done_cnt}/{len(questions)}")
        if done_cnt != len(questions) or not b2_ok:
            return 1
        if sources_n < len(questions) or cites_n < len(questions):
            record("B2 sources/citations", "WARNING",
                   f"sources={sources_n}/{len(questions)} "
                   f"citations={cites_n}/{len(questions)} (retrieval best-effort)")
        else:
            record("B2 sources/citations", "PASS",
                   f"sources={sources_n}/{len(questions)} "
                   f"citations={cites_n}/{len(questions)}")

        # B3: dont-know live (min_score=0.999 + reranker)
        if not os.environ.get("GPUSTACK_KEY_RERANK", "").strip():
            record("B3 dont-know", "SKIP",
                   "нет GPUSTACK_KEY_RERANK: относительный min_score без "
                   "реранкера не может форсировать 0 чанков")
        else:
            code, s0 = http("/api/kb/settings", timeout=30)
            if code == 200 and isinstance(s0, dict):
                pre_settings = s0
            code, _ = http("/api/kb/settings", method="POST",
                           json_body={"reranker": "api", "min_score": 0.999},
                           timeout=30)
            if code != 200:
                record("B3 dont-know", "SKIP", f"settings -> {code}")
            else:
                evs, err = chat_sse(did, Q_NOMATCH)
                if err is not None or evs is None:
                    record("B3 dont-know", "SKIP", err or "SSE error")
                else:
                    done = next((e for e in reversed(evs) if e.get("type") == "done"), None)
                    if done is None:
                        record("B3 dont-know", "SKIP", "no done")
                    elif done.get("answer") == DONT_KNOW_TEXT:
                        record("B3 dont-know", "PASS", "answer == DONT_KNOW_TEXT")
                    elif (done.get("answer") or "").strip():
                        record("B3 dont-know", "WARNING",
                               "обычный ответ вместо dont-know")
                    else:
                        record("B3 dont-know", "SKIP", "пустой ответ")
        return 0
    except Exception as e:
        record("B: непредвиденное исключение", "FAIL", repr(e))
        return 1
    finally:
        # B4: cleanup (пока сервер жив)
        if proc is not None:
            try:
                body = {"min_score": 0.0}
                if pre_settings:
                    body["reranker"] = pre_settings.get("reranker", "off")
                code, r = http("/api/kb/settings", method="POST",
                               json_body=body, timeout=30)
                if orig_model:
                    http("/api/config", method="POST",
                         json_body={"model": orig_model}, timeout=30)
                record("B4 cleanup", "PASS" if code == 200 else "WARNING",
                       "min_score=0.0, reranker/model restored")
            except Exception as e:
                record("B4 cleanup", "WARNING", repr(e))
        stop_server(proc)


def main() -> int:
    ap = argparse.ArgumentParser(description="E2E day 24")
    ap.add_argument("--part-a-only", action="store_true",
                    help="только оффлайн Part A")
    args = ap.parse_args()
    log("E2E day 24: цитаты, источники, анти-галлюцинации")
    log(f"репозиторий: {REPO}")
    log(f"живой порт: {PORT} (Part B best-effort)")
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
