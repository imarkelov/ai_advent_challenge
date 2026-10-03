# -*- coding: utf-8 -*-
"""E2E day 23 «min_score порог релевантности + rewrite (перефраз)»

Offline part A (обязана проходить без сети/GPUStack):
  A1 settings: GET/POST min_score, валидация 400 (1.5 / -0.1 / "0.5" / True)
  A2 fake reranker (patch kb.APIReranker): абсолютный min_score по rerank_score
     + аддитивные поля "filtered"/"dropped" (только при min_score > 0)
  A3 reranker off: относительный min_score (score >= min_score * best),
     порядок сохранён (kept chunk_ids — префикс базы)
  A4 /api/rag/compare с min_score=0.6: 13 ключей ответа, chunks_rag_filter —
     только прошедшие порог, rag-плечо без фильтра, 5 вызовов LLM,
     скриптованный rewrite
  A5 падение rewrite (500 на max_tokens==200): фолбэк — chunks_rag_rewrite
     == chunks, rewrite_applied=False, rewritten_query == question, ответ 200
  A6 compare с min_score=0.999 на главном клиенте: chunks_rag_filter == [],
     answer_rag_filter непустой (LLM отвечает и на пустом контексте)
  A7 day22-совместимость: все 5 старых ключей compare + 6 полей чанка
Live part B (best-effort; без GPUStack -> SKIP):
  B1 пересборка корпуса (egg + extras), B2 настройки (embedder/reranker по
  ключам), B3 живой compare с min_score=0.5 (4 плеча, 13 ключей),
  B4 пасхалка IPhone 17Promax в answer_rag/answer_rag_rewrite (WARNING,
  не FAIL).

Запуск из корня репозитория:
    python scripts/e2e_day23.py
Код возврата: 0 — успех (Part A весь PASS и Part B без FAIL), 1 — провал.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Callable

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from dotenv import load_dotenv  # noqa: E402

# --- константы ---

PORT = 8107
BASE = f"http://127.0.0.1:{PORT}"
BOUNDARY = "----e2eday23" + uuid.uuid4().hex[:8]
LOG_PREFIX = "[e2e-day23] "

WAIT_PORT_BUSY_MAX = 600   # сек, ожидание освобождения порта (чужой сервер не убиваем)
WAIT_PORT_BUSY_STEP = 5
WAIT_SERVER_UP_MAX = 90
WAIT_SERVER_UP_STEP = 1
COMPARE_TIMEOUT = 825      # сек, compare с 5 вызовами LLM (до 15 мин)
INDEX_TIMEOUT = 900        # сек, api-индексация свежего корпуса (до 15 мин)

EGG_FIXTURE = os.path.join(REPO, "studio", "backend", "tests", "fixtures", "egg_book.txt")

COMPARE_QUESTION = "What phone did the hero have?"
EGG_FACT = "IPhone 17Promax"
REWRITE_SCRIPTED = "What did cat Persik do on the case and what tea did the hero drink in the mornings?"
SCORES = (0.99, 0.5, 0.1)  # позиционные скоры фейкового реранкера

EXPECTED_KEYS = {
    "answer_plain", "answer_rag", "kb_block", "chunks", "rag_context",
    "answer_rag_filter", "answer_rag_rewrite",
    "chunks_rag_filter", "chunks_rag_rewrite",
    "rag_context_rag_filter", "rag_context_rag_rewrite",
    "rewritten_query", "rewrite_applied",
}

_NO_NET = {
    "HTTP_PROXY": "http://127.0.0.1:1",
    "HTTPS_PROXY": "http://127.0.0.1:1",
    "http_proxy": "http://127.0.0.1:1",
    "https_proxy": "http://127.0.0.1:1",
    "NO_PROXY": "",
    "no_proxy": "",
}

SERVER_LOG = os.path.join(REPO, "e2e_day23_server.log")

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
         raw_body: bytes | None = None, headers: dict | None = None,
         timeout: int = 60) -> tuple[int, Any]:
    url = BASE + path
    hdrs = dict(headers or {})
    data = None
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    elif raw_body is not None:
        data = raw_body
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


def http_multipart(path: str, field: str, filename: str, content: bytes,
                   timeout: int = 120) -> tuple[int, Any]:
    parts = [
        f"--{BOUNDARY}\r\n".encode(),
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'.encode(),
        b"Content-Type: text/plain\r\n\r\n",
        content,
        f"\r\n--{BOUNDARY}--\r\n".encode(),
    ]
    body = b"".join(parts)
    return http(path, method="POST", raw_body=body,
                headers={"Content-Type": f"multipart/form-data; boundary={BOUNDARY}"},
                timeout=timeout)


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


# --- живой uvicorn-сервер ---


def wait_port_free(max_wait: int = WAIT_PORT_BUSY_MAX, step: int = WAIT_PORT_BUSY_STEP) -> bool:
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


# --- фейки: LLM (MockTransport) и реранкер (patch kb.APIReranker) ---

_E2E_USAGE = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
              "completion_tokens_details": {"reasoning_tokens": 2}}


def _sse(frames: list[str]) -> str:
    return "".join(frames)


def _delta_chunk(text: str) -> str:
    return "data: " + json.dumps({"choices": [{"delta": {"content": text}}]},
                                 ensure_ascii=False) + "\n\n"


def _stop_chunk() -> str:
    return "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}],
                                  "usage": _E2E_USAGE}, ensure_ascii=False) + "\n\n"


def _llm_handler(captured: list, *, rewrite_text: str = REWRITE_SCRIPTED,
                 fail_rewrite: bool = False) -> Callable:
    """Фейковый OpenAI-совместимый endpoint (GPUStack).

    - stream -> SSE с usage;
    - каждый не-stream payload -> captured (порядок: plain, rag, filter,
      rewrite, rag_rewrite);
    - max_tokens == 200 -> вызов rewrite: скриптованный перефраз или 500
      (fail_rewrite), ПЕРЕД 500 payload уже в captured;
    - иначе -> детерминированный ответ.
    """

    def handler(request):
        import httpx

        payload = json.loads(request.content.decode("utf-8"))
        if payload.get("stream"):
            frames = [_delta_chunk("Ок"), _stop_chunk(), "[DONE]"]
            return httpx.Response(200, content=_sse(frames).encode("utf-8"))
        captured.append(payload)
        if fail_rewrite and payload.get("max_tokens") == 200:
            return httpx.Response(500, json={"error": "rewrite failed (fake)"})
        content = (rewrite_text if payload.get("max_tokens") == 200
                   else "Детерминированный ответ e2e.")
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    return handler


_RR_QUERIES: list = []  # spy: все запросы, ушедшие в фейковый реранкер


class _FakeRR:
    """Фейковый кросс-энкодер: детерминированные позиционные скоры SCORES.

    Требование kb.search_rag: len(scores) == len(texts), иначе KBError.
    """

    def __init__(self, base_url: str, api_key: str, client: Any = None):
        self.base_url = base_url
        self.api_key = api_key

    def rerank(self, query: str, texts: list) -> list:
        _RR_QUERIES.append(query)
        return [SCORES[i] if i < len(SCORES) else 0.0 for i in range(len(texts))]


def _make_tmp_kb_fixed(root: str):
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


# --- Part A: offline ---


def part_a() -> bool:
    log("=== Part A: офлайн (TestClient + fake LLM + fake реранкер, без сети) ===")
    import httpx
    from fastapi.testclient import TestClient

    sys.path.insert(0, os.path.join(REPO, "studio", "backend"))
    from agent import StudioAgent  # noqa: E402  (lazy: после _NO_NET)

    saved_env = {k: os.environ.get(k) for k in _NO_NET}
    os.environ.update(_NO_NET)
    tmp = tempfile.mkdtemp(prefix="e2e_day23_")
    failed = False
    ok_a1 = ok_a2 = ok_a3 = ok_a4 = ok_a5 = ok_a6 = ok_a7 = False
    try:
        from main import create_app  # noqa: E402  (lazy: читает .env при импорте)
        import kb as kb_module

        kb = _make_tmp_kb_fixed(tmp)

        def make_agent(data_name: str, captured: list, *, fail_rewrite: bool = False):
            return StudioAgent(
                os.path.join(tmp, data_name),
                base_url="https://mock.local/v1",
                api_key="fake-e2e",
                client=httpx.Client(transport=httpx.MockTransport(
                    _llm_handler(captured, fail_rewrite=fail_rewrite))),
                kb=kb,
            )

        captured: list = []
        agent = make_agent("data", captured)
        client = TestClient(create_app(agent, kb))
        cap4: list = []
        agent4 = make_agent("data4", cap4)
        client4 = TestClient(create_app(agent4, kb))
        cap5: list = []
        agent5 = make_agent("data5", cap5, fail_rewrite=True)
        client5 = TestClient(create_app(agent5, kb))

        def search(cl, **params):
            r = cl.get("/api/kb/search", params={"q": COMPARE_QUESTION, "k": 10, **params})
            return r.status_code, (r.json() if r.status_code == 200 else {})

        def patch(cl, body: dict):
            return cl.post("/api/kb/settings", json=body)

        # ---------- A1: settings + валидация min_score ----------
        ok_a1 = False
        try:
            r0 = client.get("/api/kb/settings")
            s0 = r0.json() if r0.status_code == 200 else {}
            base_ok = r0.status_code == 200 and s0.get("min_score") == 0.0
            r1 = patch(client, {"min_score": 0.5})
            echo_ok = r1.status_code == 200 and r1.json().get("min_score") == 0.5
            bad_ok = True
            for bad in (1.5, -0.1, "0.5", True):
                rb = patch(client, {"min_score": bad})
                try:
                    d = rb.json().get("detail", "")
                except Exception:
                    d = ""
                if rb.status_code != 400 or not d:
                    bad_ok = False
                    break
            r2 = patch(client, {"min_score": 0.0})
            restore_ok = r2.status_code == 200 and r2.json().get("min_score") == 0.0
            ok_a1 = base_ok and echo_ok and bad_ok and restore_ok
            record("A1 settings min_score + валидация 400",
                   "PASS" if ok_a1 else "FAIL",
                   f"base={s0.get('min_score')} echo=0.5 bad_ok={bad_ok}")
        except Exception as e:
            record("A1 settings min_score + валидация 400", "FAIL", f"exception: {e!r}")

        # ---------- patch фейкового реранкера живёт через A2-A6 ----------
        orig_rr = kb_module.APIReranker
        orig_key = os.environ.get("GPUSTACK_KEY_RERANK")
        os.environ["GPUSTACK_KEY_RERANK"] = "fake-e2e"
        kb_module.APIReranker = _FakeRR
        n = 0
        b4: dict = {}
        try:
            # ---------- A2: абсолютный фильтр (fake reranker) ----------
            ok_a2 = False
            try:
                patch(client, {"reranker": "api", "min_score": 0.0})
                code, b = search(client)
                results = b.get("results", [])
                n = len(results)
                exp = [SCORES[i] if i < len(SCORES) else 0.0 for i in range(n)]
                base_ok = (code == 200 and n >= 2
                           and b.get("recall_total") == n
                           and b.get("reranked") is True
                           and "filtered" not in b
                           and [c.get("rerank_score") for c in results] == exp)
                patch(client, {"min_score": 0.6})
                code, bf = search(client)
                res6 = bf.get("results", [])
                filt_ok = (code == 200 and len(res6) == 1
                           and res6[0].get("rerank_score") == 0.99
                           and bf.get("filtered") is True
                           and bf.get("dropped") == n - 1)
                patch(client, {"min_score": 0.999})
                code, bz = search(client)
                empty_ok = (code == 200 and bz.get("results") == []
                             and bz.get("filtered") is True
                             and bz.get("dropped") == n)
                patch(client, {"min_score": 0.0})
                code, b0 = search(client)
                same_ok = (code == 200 and b0 == b)  # 0.0 → без изменений
                ok_a2 = base_ok and filt_ok and empty_ok and same_ok
                record("A2 search: абсолютный min_score (fake reranker)",
                       "PASS" if ok_a2 else "FAIL",
                       f"n={n} base={base_ok} 0.6->1+dropped={n - 1}:{filt_ok} "
                       f"0.999->[]+dropped={n}:{empty_ok} 0.0==base:{same_ok}")
            except Exception as e:
                record("A2 search: абсолютный min_score (fake reranker)", "FAIL",
                       f"exception: {e!r}")

            # ---------- A3: относительный фильтр (reranker off) ----------
            ok_a3 = False
            try:
                patch(client, {"reranker": "off", "min_score": 0.0})
                code, b3 = search(client)
                res3 = b3.get("results", [])
                n3 = len(res3)
                base3_ok = (code == 200 and n3 >= 2
                            and b3.get("reranked") is False
                            and "filtered" not in b3
                            and all("rerank_score" not in c for c in res3))
                patch(client, {"min_score": 0.9999})
                code, bre = search(client)
                res_re = bre.get("results", [])
                kept_ids = [c.get("chunk_id") for c in res_re]
                base_ids = [c.get("chunk_id") for c in res3]
                rel_ok = (code == 200
                          and bre.get("filtered") is True
                          and bre.get("dropped") == n3 - len(res_re)
                          and kept_ids == base_ids[: len(res_re)]
                          and (len(res_re) < n3 or bre.get("dropped") == 0))
                patch(client, {"reranker": "api", "min_score": 0.0})
                ok_a3 = base3_ok and rel_ok
                record("A3 search: относительный min_score (reranker off)",
                       "PASS" if ok_a3 else "FAIL",
                       f"n={n3} base={base3_ok} kept={len(res_re)} "
                       f"dropped={bre.get('dropped')} prefix_ok={rel_ok}")
            except Exception as e:
                record("A3 search: относительный min_score (reranker off)", "FAIL",
                       f"exception: {e!r}")

            # ---------- A4: compare с min_score=0.6 (свежий агент) ----------
            ok_a4 = False
            try:
                r4 = client4.post("/api/rag/compare",
                                  json={"question": COMPARE_QUESTION, "min_score": 0.6})
                b4 = r4.json() if r4.status_code == 200 else {}
                crf = b4.get("chunks_rag_filter") or []
                keys_ok = EXPECTED_KEYS <= set(b4)
                # retrieval rewrite-плеча ушёл на скриптованный перефраз
                spy_ok = _RR_QUERIES[-3:] == [COMPARE_QUESTION, COMPARE_QUESTION,
                                              REWRITE_SCRIPTED]
                ok_a4 = (r4.status_code == 200
                         and keys_ok
                         and b4.get("rewritten_query") == REWRITE_SCRIPTED
                         and b4.get("rewrite_applied") is True
                         and len(crf) == 1
                         and crf[0].get("rerank_score") == 0.99
                         and len(b4.get("chunks") or []) == n  # rag-плечо без фильтра
                         and spy_ok
                         and len(cap4) == 5
                         and all(p.get("model") == agent4.get_config()["model"] for p in cap4)
                         and isinstance(b4.get("rag_context_rag_filter"), dict)
                         and isinstance(b4.get("rag_context_rag_rewrite"), dict))
                record("A4 compare min_score=0.6: filter-плечо + скриптованный rewrite",
                       "PASS" if ok_a4 else "FAIL",
                       f"keys_ok={keys_ok} filter_chunks={len(crf)} "
                       f"rag_chunks={len(b4.get('chunks') or [])} calls={len(cap4)} "
                       f"rw_applied={b4.get('rewrite_applied')} spy={spy_ok}")
            except Exception as e:
                record("A4 compare min_score=0.6: filter-плечо + скриптованный rewrite",
                       "FAIL", f"exception: {e!r}")

            # ---------- A5: падение rewrite -> фолбэк ----------
            ok_a5 = False
            try:
                r5 = client5.post("/api/rag/compare", json={"question": COMPARE_QUESTION})
                b5 = r5.json() if r5.status_code == 200 else {}
                arw = b5.get("answer_rag_rewrite") or ""
                ok_a5 = (r5.status_code == 200
                         and arw.strip() != "" and not arw.startswith("Ошибка")
                         and b5.get("rewrite_applied") is False
                         and b5.get("rewritten_query") == COMPARE_QUESTION
                         and len(cap5) == 5
                         and cap5[3].get("max_tokens") == 200
                         and b5.get("chunks_rag_rewrite") == b5.get("chunks"))
                record("A5 compare: падение rewrite -> фолбэк на rag-плечо",
                       "PASS" if ok_a5 else "FAIL",
                       f"applied={b5.get('rewrite_applied')} "
                       f"rw_eq_q={b5.get('rewritten_query') == COMPARE_QUESTION} "
                       f"chunks_eq={b5.get('chunks_rag_rewrite') == b5.get('chunks')} "
                       f"calls={len(cap5)}")
            except Exception as e:
                record("A5 compare: падение rewrite -> фолбэк на rag-плечо", "FAIL",
                       f"exception: {e!r}")

            # ---------- A6: compare с min_score=0.999 на главном клиенте ----------
            ok_a6 = False
            try:
                before = len(captured)
                r6 = client.post("/api/rag/compare",
                                 json={"question": COMPARE_QUESTION, "min_score": 0.999})
                b6 = r6.json() if r6.status_code == 200 else {}
                arf = b6.get("answer_rag_filter") or ""
                ok_a6 = (before == 0
                         and r6.status_code == 200
                         and b6.get("chunks_rag_filter") == []
                         and arf.strip() != "" and not arf.startswith("Ошибка")
                         and b6.get("rewritten_query") == REWRITE_SCRIPTED
                         and b6.get("rewrite_applied") is True
                         and len(captured) == before + 5)
                record("A6 compare min_score=0.999: пустое filter-плечо не роняет ответ",
                       "PASS" if ok_a6 else "FAIL",
                       f"before={before} "
                       f"filter_chunks={len(b6.get('chunks_rag_filter') or [])} "
                       f"applied={b6.get('rewrite_applied')} calls={len(captured)}")
            except Exception as e:
                record("A6 compare min_score=0.999: пустое filter-плечо не роняет ответ",
                       "FAIL", f"exception: {e!r}")

            # ---------- A7: day22-совместимость (на b4) ----------
            ok_a7 = False
            try:
                c40 = (b4.get("chunks") or [{}])[0]
                old5 = ("answer_plain", "answer_rag", "kb_block", "chunks", "rag_context")
                chunk6 = ("chunk_id", "source", "file", "section", "score", "text")
                rc = b4.get("rag_context") or {}
                det = "Детерминированный ответ e2e."  # эталонный fake-payload
                ok_a7 = (all(k in b4 for k in old5)
                         and all(k in c40 for k in chunk6)
                         and b4.get("answer_plain") == det
                         and b4.get("answer_rag") == det
                         and isinstance(b4.get("kb_block"), str)
                         and b4["kb_block"].startswith("\n\nБаза знаний")
                         and isinstance(rc, dict)
                         and set(rc) == {"recall_total", "reranked", "chunks"})
                record("A7 day22-совместимость: старые ключи compare/чанков",
                       "PASS" if ok_a7 else "FAIL",
                       f"old_keys_ok={all(k in b4 for k in old5)} "
                       f"chunk6_ok={all(k in c40 for k in chunk6)} "
                       f"payload_ok={b4.get('answer_plain') == det}")
            except Exception as e:
                record("A7 day22-совместимость: старые ключи compare/чанков", "FAIL",
                       f"exception: {e!r}")
        finally:
            kb_module.APIReranker = orig_rr
            if orig_key is None:
                os.environ.pop("GPUSTACK_KEY_RERANK", None)
            else:
                os.environ["GPUSTACK_KEY_RERANK"] = orig_key

        failed = not (ok_a1 and ok_a2 and ok_a3 and ok_a4 and ok_a5 and ok_a6 and ok_a7)
    except Exception as e:
        record("A: непредвиденное исключение", "FAIL", repr(e))
        failed = True
    finally:
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(tmp, ignore_errors=True)
    a_pass = sum(1 for v in (ok_a1, ok_a2, ok_a3, ok_a4, ok_a5, ok_a6, ok_a7) if v)
    log(f"Part A: {a_pass}/7 PASS")
    return not failed


# --- Part B: live (best-effort) ---


def part_b() -> int:
    log("=== Part B: живой прогон (реальный GPUStack; без GPUStack -> SKIP) ===")
    if not wait_port_free():
        record("B: порт", "SKIP", f"порт {PORT} занят дольше {WAIT_PORT_BUSY_MAX} с (чужой сервер)")
        return 0
    ok, why = probe_gpustack()
    if not ok:
        record("B: GPUStack", "SKIP", why)
        return 0
    orig_model = None
    proc = None
    try:
        proc = start_server()
        if proc is None:
            record("B: сервер", "FAIL", "uvicorn не поднялся")
            return 1

        # модель: берём первую доступную с GPUStack, сохраняем прежнюю
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

        # B1: свежий корпус (egg + extras), пересборка
        uploads = os.path.join(REPO, "data", "kb", "uploads")
        extras: list = []
        try:
            for fn in os.listdir(uploads):
                if fn in ("egg_book.txt", "control_book.txt"):
                    continue
                p = os.path.join(uploads, fn)
                if os.path.isfile(p):
                    with open(p, "rb") as f:
                        extras.append((fn, f.read()))
            extras = extras[:2]
        except OSError:
            pass
        code, _ = http("/api/kb", method="DELETE", timeout=120)
        if code != 200:
            record("B1 корпус", "FAIL", f"DELETE /api/kb -> {code}")
            return 1
        with open(EGG_FIXTURE, "rb") as f:
            egg_bytes = f.read()
        code, r = http_multipart("/api/kb/upload", "file", "egg_book.txt", egg_bytes)
        if code != 200:
            record("B1 корпус", "FAIL", f"загрузка egg -> {code}: {r}")
            return 1
        for fn, content in extras:
            http_multipart("/api/kb/upload", "file", fn, content)
        code, r = http("/api/kb/index", method="POST",
                       json_body={"strategy": "structural", "embedder": "api"},
                       timeout=INDEX_TIMEOUT)
        b1_embedder = "api"
        if code != 200:
            code, r = http("/api/kb/index", method="POST",
                           json_body={"strategy": "structural", "embedder": "hash"},
                           timeout=300)
            b1_embedder = "hash"
            if code != 200:
                record("B1 корпус", "FAIL", f"hash-индексация не прошла -> {code}: {r}")
                return 1
        # Индекс собран: /api/kb/stats -> 200 + stats.chunks > 0
        # (404, если index отсутствует); /api/kb/build-status ->
        # kb.progress(): {running, phase, done, total} (running: False)
        code, st = http("/api/kb/stats", timeout=30)
        chunks = ((st.get("stats") or {}).get("chunks")
                  if isinstance(st, dict) else None)
        stats_ok = (code == 200 and isinstance(st, dict)
                    and isinstance(st.get("stats"), dict)
                    and (chunks or 0) > 0)
        code_p, prog = http("/api/kb/build-status", timeout=30)
        running_ok = (code_p == 200 and isinstance(prog, dict)
                      and prog.get("running") is False)
        b1_ok = stats_ok and running_ok
        record("B1 корпус", "PASS" if b1_ok else "FAIL",
               f"egg + {len(extras)} extras, индексация {b1_embedder}, "
               f"chunks={chunks} running={prog.get('running') if isinstance(prog, dict) else '?'}")
        if not b1_ok:
            return 1

        # B2: настройки под живой прогон (без min_score — он в теле B3)
        embedder = "api" if os.environ.get("GPUSTACK_KEY_EMBED") else "hash"
        reranker = "api" if os.environ.get("GPUSTACK_KEY_RERANK") else "off"
        code, s = http("/api/kb/settings", method="POST",
                       json_body={"embedder": embedder, "reranker": reranker}, timeout=30)
        b2_ok = (code == 200 and isinstance(s, dict)
                 and s.get("embedder") == embedder and s.get("reranker") == reranker)
        record("B2 настройки", "PASS" if b2_ok else "FAIL",
               f"embedder={embedder} reranker={reranker}")
        if not b2_ok:
            return 1

        # B3: живой compare с min_score=0.5
        code, b3 = http("/api/rag/compare", method="POST",
                        json_body={"question": COMPARE_QUESTION, "min_score": 0.5},
                        timeout=COMPARE_TIMEOUT)
        if code != 200 or not isinstance(b3, dict):
            record("B3 compare min_score=0.5", "FAIL", f"code={code}")
            return 1
        arms = [b3.get("answer_plain") or "", b3.get("answer_rag") or "",
                b3.get("answer_rag_filter") or "", b3.get("answer_rag_rewrite") or ""]
        if any("Ошибка" in a for a in arms):
            record("B3 compare min_score=0.5", "SKIP",
                   "LLM вернул ошибку в одном из плеч")
            return 0
        keys_ok = EXPECTED_KEYS <= set(b3)
        shape_ok = (keys_ok
                    and all(a.strip() for a in arms)
                    and len(b3.get("chunks") or []) > 0
                    and isinstance(b3.get("rewritten_query"), str)
                    and b3.get("rewritten_query") != ""
                    and isinstance(b3.get("rewrite_applied"), bool))
        record("B3 compare min_score=0.5", "PASS" if shape_ok else "FAIL",
               f"keys_ok={keys_ok} filter_chunks={len(b3.get('chunks_rag_filter') or [])} "
               f"applied={b3.get('rewrite_applied')} rw={b3.get('rewritten_query')}")
        if not shape_ok:
            return 1

        # B4: пасхалка (best-effort)
        hay = ((b3.get("answer_rag") or "") + " "
               + (b3.get("answer_rag_rewrite") or "")).casefold()
        if EGG_FACT.casefold() in hay:
            record("B4 пасхалка", "PASS", f"«{EGG_FACT}» найдена в ответе")
        else:
            record("B4 пасхалка", "WARNING",
                   f"«{EGG_FACT}» не найдена в ответе (best-effort, не провал)")
        return 0
    except Exception as e:
        record("B: непредвиденное исключение", "FAIL", repr(e))
        return 1
    finally:
        if orig_model:
            try:
                http("/api/config", method="POST",
                     json_body={"model": orig_model}, timeout=30)
            except Exception:
                pass
        stop_server(proc)


def main() -> int:
    log("E2E day 23: min_score порог релевантности + rewrite (перефраз)")
    log(f"репозиторий: {REPO}")
    log(f"живой порт: {PORT} (Part B best-effort)")
    a_ok = part_a()
    if not a_ok:
        print_summary()
        return 1
    b_rc = part_b()
    print_summary()
    return b_rc


if __name__ == "__main__":
    sys.exit(main())
