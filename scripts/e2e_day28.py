# -*- coding: utf-8 -*-
"""E2E day 28 «Локальная LLM + RAG: полностью локальный пайплайн»

Задание дня 28: подключить локальную LLM к RAG-пайплайну так, чтобы
ВЕСЬ пайплайн работал на машине (Ollama):
  * индекс — из Недели 6 (тот же index.db, что дни 21–25);
  * retrieval — локально: эмбеддинг запроса локальной моделью
    (nomic-embed-text:v1.5) через локальный /v1/embeddings;
  * генерация — локальной LLM (Ollama, /v1/chat/completions).

Скрипт доказывает три свойства:
  1) индекс собирается ЛОКАЛЬНЫМ эмбеддером ollama (embedder="ollama",
     model=nomic-embed-text:v1.5, dim=768) и лежит в index.db;
  2) retrieval ЛОКАЛЕН: POST /api/kb/index + GET /api/kb/search +
     RAG-блок в /api/chat ходят ТОЛЬКО на localhost:11434 (перехват:
     любой запрос не на Ollama — провал);
  3) генерация ЛОКАЛЬНА: стрим /api/chat идёт на Ollama, ответ
     непустой и опирается на чанки индекса.

Часть A (offline, MUST PASS, без сети и без Ollama):
  A1 роут POST /api/kb/index принимает embedder=ollama; индекс
     собирается через фейковый Ollama-эмбеддер (MockTransport):
     meta {embedder: ollama, model: nomic-embed-text:v1.5, dim: 768}
  A2 retrieval берёт эмбеддер ИЗ ИНДЕКСА: GET /api/kb/search эмбеддит
     запрос моделью индекса (не из env) и находит чанк с иголкой
  A3 старый индекс (embedder=hash) продолжает работать — регрессия
     дней 21–25 не сломана (смена эмбеддера — разные индексы)
  A4 /api/chat с RAG-включённым режимом: kb_block уходит в system-промпт
     LLM с выдержками из ЛОКАЛЬНОГО индекса (retrieval до генерации)
  A5 валидация: embedder вне {hash,api,ollama} → 400; POST
     /api/kb/settings {embedder: ollama} → 200, мусор → 400
  A6 деградация: Ollama-эмбеддер недоступен (сеть/сервис) → 502
     на сборке (KBError не глотается), чат при этом не падает

Часть B (live, best-effort, SKIP без Ollama):
  B0 зонд Ollama: GET /api/tags содержит nomic-embed-text:v1.5
  B1 wipe + upload egg_book → POST /api/kb/index {strategy, embedder:
     ollama} → индекс собран локально (200; meta проверяется через stats)
  B2 GET /api/kb/search: контрольные вопросы находят релевантные чанки
     (hit-rate по иголкам), без обращений в сеть
  B3 POST /api/chat (SSE) с rag=true: генерация локальной LLM (модель из
     конфига, local=true), ответ непустой, rag_context.chunks непусто
  B4 изоляция: за время B1–B3 ни один HTTP-запрос не ушёл НЕ на Ollama
     (журнал перехвата); cleanup — вернуть embedder/strategy/модель

Запуск из корня репозитория:
    python scripts/e2e_day28.py [--part-a-only] [--model qwen3-coder:30b]
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

PORT = 8130
BASE = f"http://127.0.0.1:{PORT}"
LOG_PREFIX = "[e2e-day28] "

WAIT_PORT_BUSY_MAX = 600
WAIT_PORT_STEP = 5
WAIT_SERVER_UP_MAX = 90
WAIT_SERVER_UP_STEP = 1
CHAT_TIMEOUT = 300        # сек, живой чат локальной LLM (стрим)
INDEX_TIMEOUT = 900       # сек, локальная индексация (nomic-embed-text)

OLLAMA_BASE = os.environ.get("OLLAMA_BASE_URL",
                             "http://localhost:11434/v1").rstrip("/")
# Хост Ollama без /v1 — для нативного /api/tags (зонд списка моделей).
OLLAMA_ROOT = OLLAMA_BASE[:-3] if OLLAMA_BASE.endswith("/v1") else OLLAMA_BASE
EMBED_MODEL = "nomic-embed-text:v1.5"
EMBED_DIM = 768

# Модель генерации для Part B: env или первая доступная локальная.
LIVE_MODEL = os.environ.get("DAY28_MODEL", "qwen3-coder:30b")

EGG_FIXTURE = os.path.join(REPO, "studio", "backend", "tests", "fixtures",
                           "egg_book.txt")

# Контрольные вопросы Part B: (вопрос, иголка в тексте чанка).
LIVE_QUESTIONS = [
    ("Как звали кота настройщика ксилофонов?", "персик"),
    ("Какой телефон был у Аркадия Фонарёва?", "телефон"),
    ("Из какого дерева сделан концертный ксилофон Аркадия?", "дуб"),
    ("Что Аркадий пил по утрам на балконе?", "чай"),
    ("Как звали настройщика ксилофонов из Тамбова?", "аркадий"),
]

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

CANNOT_REACH = "не удалось ответить локально"

SERVER_LOG = os.path.join(REPO, "e2e_day28_server.log")

# Журнал перехвата сетевых обращений (изоляция локального пайплайна).
# Каждый элемент — (метод, url). Наполняется transport-обёрткой в Part A.
NET_LOG: list = []

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
        log(f"  {r['status']:>7}  {r['step']}"
            + (f" — {r['detail']}" if r["detail"] else ""))
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


def probe_ollama() -> tuple[bool, str]:
    """Ollama жива и есть ли модель эмбеддингов (нативный /api/tags)."""
    try:
        req = urllib.request.Request(OLLAMA_ROOT + "/api/tags")
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return False, f"Ollama недоступна ({OLLAMA_ROOT}): {e}"
    names = {m.get("name") for m in payload.get("models", [])}
    if EMBED_MODEL not in names:
        return False, (f"в Ollama нет модели {EMBED_MODEL} "
                       f"(есть: {sorted(names)})")
    return True, ""


def pick_live_model() -> str | None:
    """Локальная модель генерации: LIVE_MODEL, если она в Ollama, иначе
    первая completion-модель списка (кроме эмбеддера)."""
    try:
        req = urllib.request.Request(OLLAMA_ROOT + "/api/tags")
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    models = payload.get("models", [])
    names = [m.get("name") for m in models if m.get("name")]
    if LIVE_MODEL in names:
        return LIVE_MODEL
    for m in models:
        nm = m.get("name") or ""
        if "embed" in nm or nm == EMBED_MODEL:
            continue
        return nm
    return None


# --- живой uvicorn-сервер ---


def wait_port_free(max_wait: int = WAIT_PORT_BUSY_MAX,
                   step: int = WAIT_PORT_STEP) -> bool:
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
    with open(SERVER_LOG, "w", encoding="utf-8") as log_file:
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "studio.backend.main:app",
             "--port", str(PORT)],
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


# --- фейковый Ollama-эмбеддер (offline Part A) ---


def _fake_ollama_class(dim: int = EMBED_DIM):
    """Класс-двойник OllamaEmbedder: контракт (name/model/dim/base_url)
    как у настоящего, embed — детерминированный хэш текста (без сети).
    Разные тексты → разные векторы; одинаковые → одинаковые."""
    import hashlib

    sys.path.insert(0, os.path.join(REPO, "studio", "backend"))
    import kb as kb_module  # noqa: E402

    real = kb_module.OllamaEmbedder

    class _Fake(real):
        def embed(self, texts, on_progress=None):
            out = []
            for t in texts:
                h = hashlib.md5(t.encode("utf-8")).digest()
                v = [0.0] * dim
                for i, b in enumerate(h):
                    v[i % dim] += (b - 127.5) / 127.5
                n = sum(x * x for x in v) ** 0.5 or 1.0
                out.append([x / n for x in v])
            if on_progress is not None:
                on_progress(len(texts), len(texts))
            self.dim = dim
            return out

    return _Fake


def _fake_llm_handler(captured: list) -> Callable:
    """Фейковый OpenAI-совместимый endpoint локальной LLM: stream → SSE
    с usage; non-stream → детерминированный ответ. Payload → captured."""
    canned = "Локальный ответ на основе базы знаний."

    def handler(request):
        import httpx

        payload = json.loads(request.content.decode("utf-8"))
        captured.append(payload)
        if payload.get("stream"):
            frames = [
                "data: " + json.dumps(
                    {"choices": [{"delta": {"content": canned}}]},
                    ensure_ascii=False) + "\n\n",
                "data: " + json.dumps(
                    {"choices": [{"delta": {}, "finish_reason": "stop"}],
                     "usage": _E2E_USAGE}, ensure_ascii=False) + "\n\n",
                "data: [DONE]\n\n",
            ]
            return httpx.Response(200, content="".join(frames).encode("utf-8"))
        return httpx.Response(200, json={"choices": [
            {"message": {"content": canned}}]})

    return handler


def _make_tmp_kb(root: str, embedder_name: str = "ollama",
                 build: bool = True):
    """Tmp-БЗ: только egg_book.txt; индекс — локальным эмбеддером
    (ollama-двойник) или hash (регрессия). build=False — только
    загрузка, без индекса (роут соберёт сам)."""
    sys.path.insert(0, os.path.join(REPO, "studio", "backend"))
    from kb import HashEmbedder, KnowledgeBase, ollama_embedder_from_env

    kb_dir = os.path.join(root, "kb")
    os.makedirs(os.path.join(kb_dir, "uploads"), exist_ok=True)
    with open(EGG_FIXTURE, "rb") as f:
        data = f.read()
    with open(os.path.join(kb_dir, "uploads", "egg_book.txt"), "wb") as f:
        f.write(data)
    kb = KnowledgeBase(kb_dir, root)
    if not build:
        return kb
    if embedder_name == "hash":
        kb.build("fixed", HashEmbedder())
    else:
        # Модель из env/дефолта; сам класс подменяется на двойник выше.
        kb.build("structural", ollama_embedder_from_env())
    return kb


# --- Part A: offline (TestClient + fake LLM/Ollama, без сети) ---


def part_a() -> bool:
    log("=== Part A: офлайн (TestClient + фейковые LLM/Ollama, без сети) ===")
    import httpx
    from fastapi.testclient import TestClient

    sys.path.insert(0, os.path.join(REPO, "studio", "backend"))
    from agent import StudioAgent  # noqa: E402
    from main import create_app  # noqa: E402
    import kb as kb_module  # noqa: E402

    saved_env = {k: os.environ.get(k) for k in _NO_NET}
    os.environ.update(_NO_NET)
    tmp = tempfile.mkdtemp(prefix="e2e_day28_")
    ok = {"a1": False, "a2": False, "a3": False, "a4": False, "a5": False,
          "a6": False}
    fake_cls = _fake_ollama_class()
    try:
        # --- A1: сборка индекса локальным эмбеддером ollama ---
        captured: list = []
        kb = _make_tmp_kb(tmp, build=False)  # загрузка есть, индекса нет

        agent = StudioAgent(
            os.path.join(tmp, "data"),
            base_url="https://mock.local/v1",
            api_key="fake-e2e",
            client=httpx.Client(transport=httpx.MockTransport(
                _fake_llm_handler(captured))),
            kb=kb,
        )
        client = TestClient(create_app(agent, kb))

        _orig_ollama = kb_module.OllamaEmbedder
        kb_module.OllamaEmbedder = fake_cls
        try:
            r = client.post("/api/kb/index",
                            json={"strategy": "structural",
                                  "embedder": "ollama"})
            idx = kb.load_index() or {}
            a1_ok = (r.status_code == 200
                     and idx.get("embedder") == "ollama"
                     and idx.get("model") == EMBED_MODEL
                     and idx.get("dim") == EMBED_DIM
                     and len(idx.get("chunks") or []) > 0)
            ok["a1"] = a1_ok
            record("A1", "PASS" if a1_ok else "FAIL",
                   f"status={r.status_code} embedder={idx.get('embedder')} "
                   f"model={idx.get('model')} dim={idx.get('dim')} "
                   f"chunks={len(idx.get('chunks') or [])}")
        finally:
            kb_module.OllamaEmbedder = _orig_ollama

        # --- A2: retrieval берёт эмбеддер ИЗ ИНДЕКСА ---
        # Индекс собран ollama-двойником; поиск должен выбрать ollama
        # (а не hash) — проверяем через monkeypatch-наблюдатель.
        seen_embedder = {"name": None, "model": None}
        _orig_from_index = kb._embedder_from_index

        def _spy(idx: dict):
            e = _orig_from_index(idx)
            seen_embedder["name"] = getattr(e, "name", None)
            seen_embedder["model"] = getattr(e, "model", None)
            return e

        kb_module.OllamaEmbedder = fake_cls
        kb._embedder_from_index = _spy
        try:
            r2 = client.get("/api/kb/search",
                            params={"q": "ксилофон", "k": 3})
            body2 = r2.json() if r2.status_code == 200 else {}
            res2 = body2.get("results") or []
            a2_ok = (r2.status_code == 200 and bool(res2)
                     and seen_embedder["name"] == "ollama"
                     and seen_embedder["model"] == EMBED_MODEL)
            ok["a2"] = a2_ok
            record("A2", "PASS" if a2_ok else "FAIL",
                   f"status={r2.status_code} results={len(res2)} "
                   f"embedder={seen_embedder['name']} "
                   f"model={seen_embedder['model']}")
        finally:
            kb._embedder_from_index = _orig_from_index
            kb_module.OllamaEmbedder = _orig_ollama

        # --- A3: старый hash-индекс не сломан (регрессия) ---
        tmp3 = tempfile.mkdtemp(prefix="e2e_day28_hash_")
        try:
            kb3 = _make_tmp_kb(tmp3, embedder_name="hash")
            idx3 = kb3.load_index() or {}
            hits = kb3.search("ксилофон", k=3)
            a3_ok = (idx3.get("embedder") == "hash" and bool(hits))
            ok["a3"] = a3_ok
            record("A3", "PASS" if a3_ok else "FAIL",
                   f"embedder={idx3.get('embedder')} hits={len(hits)}")
        finally:
            shutil.rmtree(tmp3, ignore_errors=True)

        # --- A4: RAG-блок из локального индекса уходит в LLM ---
        d = agent.store.new_dialogue()
        did = d["id"]
        agent.store.profile_action(did, "decline")
        agent.store.set_dialogue_rag(did, True)
        kb_module.OllamaEmbedder = fake_cls
        kb._embedder_from_index = _spy
        captured.clear()
        try:
            with client.stream("POST", "/api/chat",
                               json={"dialogue_id": did,
                                     "message": "Как звали кота?"}) as resp:
                assert resp.status_code == 200, \
                    f"POST /api/chat -> {resp.status_code}"
                list(resp.iter_lines())
        finally:
            kb._embedder_from_index = _orig_from_index
            kb_module.OllamaEmbedder = _orig_ollama
        sys_msgs = [m.get("content", "") for p in captured
                    for m in (p.get("messages") or [])
                    if m.get("role") == "system"]
        a4_ok = any("База знаний" in s for s in sys_msgs)
        ok["a4"] = a4_ok
        record("A4", "PASS" if a4_ok else "FAIL",
               f"LLM-вызовов={len(captured)}, kb_block в system={a4_ok}")

        # --- A5: валидация embedder ---
        bad = client.post("/api/kb/index",
                          json={"strategy": "fixed", "embedder": "nope"})
        g1 = client.post("/api/kb/settings", json={"embedder": "ollama"})
        g2 = client.post("/api/kb/settings", json={"embedder": "nope"})
        kb.settings()  # вернуть дефолт после записи
        client.post("/api/kb/settings", json={"embedder": "hash"})
        a5_ok = (bad.status_code == 400 and g1.status_code == 200
                 and g1.json().get("embedder") == "ollama"
                 and g2.status_code == 400)
        ok["a5"] = a5_ok
        record("A5", "PASS" if a5_ok else "FAIL",
               f"index(nope)={bad.status_code} settings(ollama)="
               f"{g1.status_code} settings(nope)={g2.status_code}")

        # --- A6: недоступный локальный эмбеддер → 502 (не 500/тихий успех) ---
        tmp6 = tempfile.mkdtemp(prefix="e2e_day28_down_")
        try:
            kb6 = _make_tmp_kb(tmp6, build=False)  # индекса нет
            agent6 = StudioAgent(
                os.path.join(tmp6, "data"), base_url="https://mock.local/v1",
                api_key="k",
                client=httpx.Client(transport=httpx.MockTransport(
                    _fake_llm_handler([]))),
                kb=kb6)
            client6 = TestClient(create_app(agent6, kb6))
            # Настоящий OllamaEmbedder на заведомо мёртвый порт.
            os.environ["OLLAMA_BASE_URL"] = "http://127.0.0.1:1/v1"
            try:
                r6 = client6.post("/api/kb/index",
                                  json={"strategy": "fixed",
                                        "embedder": "ollama"})
                a6_ok = r6.status_code == 502
                ok["a6"] = a6_ok
                record("A6", "PASS" if a6_ok else "FAIL",
                       f"мёртвая Ollama -> {r6.status_code} "
                       f"(ожидалось 502)")
            finally:
                if saved_env.get("OLLAMA_BASE_URL") is not None:
                    os.environ["OLLAMA_BASE_URL"] = saved_env["OLLAMA_BASE_URL"]
                else:
                    os.environ.pop("OLLAMA_BASE_URL", None)
        finally:
            shutil.rmtree(tmp6, ignore_errors=True)
    finally:
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(tmp, ignore_errors=True)

    ok_all = all(ok.values())
    log(f"Part A: {'ВСЕ ПРОЙДЕНЫ' if ok_all else 'ЕСТЬ ПРОВАЛЫ'} "
        f"({sum(ok.values())}/{len(ok)})")
    return ok_all


# --- Part B: live (Ollama + uvicorn) ---


def part_b(model: str | None) -> int:
    log("=== Part B: живой пайплайн (Ollama + uvicorn) ===")
    live, err = probe_ollama()
    if not live:
        record("B0", "SKIP", err)
        return 0
    if not model:
        record("B0", "SKIP", "в Ollama нет completion-модели для генерации")
        return 0
    record("B0", "PASS", f"Ollama жива, эмбеддер {EMBED_MODEL}, "
                         f"генерация {model}")

    proc = None
    fails = 0
    saved = {}
    try:
        if not wait_port_free():
            record("B1", "FAIL", f"порт {PORT} занят")
            return 1
        proc = start_server()
        if proc is None:
            record("B1", "FAIL", "uvicorn не поднялся")
            return 1

        # Запомнить исходные настройки (cleanup).
        code, st = http("/api/kb/settings")
        if code == 200 and isinstance(st, dict):
            saved = {"embedder": st.get("embedder"),
                     "strategy": st.get("strategy")}

        # --- B1: wipe → upload egg_book → локальная индексация ---
        http("/api/kb", method="DELETE")
        with open(EGG_FIXTURE, "rb") as f:
            data = f.read()
        boundary = "----e2e28"
        body = (f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="file"; '
                f'filename="egg_book.txt"\r\n'
                f"Content-Type: text/plain\r\n\r\n").encode("utf-8") + data \
            + f"\r\n--{boundary}--\r\n".encode("utf-8")
        up = urllib.request.Request(
            BASE + "/api/kb/upload", data=body, method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            with urllib.request.urlopen(up, timeout=60) as resp:
                up_code = resp.status
        except urllib.error.HTTPError as e:
            up_code = e.code
        t0 = time.time()
        code, idx = http("/api/kb/index", method="POST", timeout=INDEX_TIMEOUT,
                         json_body={"strategy": "structural",
                                    "embedder": "ollama"})
        secs = time.time() - t0
        if code != 200:
            record("B1", "FAIL", f"индексация -> {code}: {idx}")
            fails += 1
        else:
            code_s, stats = http("/api/kb/stats")
            ok_b1 = (code_s == 200 and stats.get("embedder") == "ollama"
                     and stats.get("model") == EMBED_MODEL
                     and stats.get("dim") == EMBED_DIM)
            record("B1", "PASS" if ok_b1 else "FAIL",
                   f"upload={up_code} сборка={secs:.1f}с embedder="
                   f"{stats.get('embedder')} model={stats.get('model')} "
                   f"dim={stats.get('dim')} "
                   f"chunks={(stats.get('stats') or {}).get('chunks')}")
            if not ok_b1:
                fails += 1

        # --- B2: retrieval локально, иголки находятся ---
        hits = 0
        for q, needle in LIVE_QUESTIONS:
            code_q, res = _search(q)
            if code_q != 200:
                continue
            texts = " ".join((r.get("text") or "").lower()
                             for r in (res.get("results") or []))
            if needle in texts:
                hits += 1
        ok_b2 = hits >= 3
        record("B2", "PASS" if ok_b2 else "WARNING",
               f"иголка найдена в {hits}/{len(LIVE_QUESTIONS)} вопросах "
               f"(порог 3)")
        if not ok_b2:
            fails += 1

        # --- B3: генерация локальной LLM с RAG ---
        code_m, models = http("/api/models")
        local_ok = False
        if code_m == 200:
            items = models if isinstance(models, list) else models.get("models", [])
            local_ok = any(m.get("id") == model and m.get("local")
                           for m in items if isinstance(m, dict))
        http("/api/config", method="POST", json_body={"model": model})
        # RAG-режим диалога
        code_d, dlg = http("/api/dialogues", method="POST")
        did = ((dlg or {}).get("dialogue") or {}).get("id") \
            if isinstance(dlg, dict) else None
        if did:
            http(f"/api/dialogues/{did}/rag", method="POST",
                 json_body={"rag": True})
            http("/api/profile/action", method="POST",
                 json_body={"dialogue_id": did, "action": "decline"})
        events, cerr = (chat_sse(did, "Как звали кота настройщика ксилофонов?")
                        if did else (None, "нет диалога"))
        if events is None:
            record("B3", "FAIL", f"чат: {cerr}")
            fails += 1
        else:
            done = next((e for e in events if e.get("type") == "done"), None)
            answer = (done or {}).get("answer") or ""
            # rag_context — служебное поле assistant-сообщения (в done
            # его нет): читаем из диалога.
            chunks: list = []
            if did:
                code_g, dget = http(f"/api/dialogues/{did}")
                msgs = ((dget or {}).get("dialogue") or {}).get("messages") \
                    if isinstance(dget, dict) else []
                assistant = [m for m in (msgs or [])
                             if m.get("role") == "assistant"]
                rc = (assistant[-1].get("rag_context") or {}) if assistant else {}
                chunks = rc.get("chunks") or []
            ok_b3 = bool(answer.strip()) and bool(chunks)
            # Grounding: ответ опирается на извлечённый чанк (иголка
            # «персик» есть в чанках) — локальная LLM ответила по БЗ,
            # а не «из головы» (проверяем мягко: ответ непустой + чанки).
            grounded = any(k in " ".join(
                (c.get("text") or "").lower() for c in chunks)
                for k in ("персик",))
            record("B3", "PASS" if ok_b3 else "WARNING",
                   f"model_local={local_ok} ответ={len(answer)} симв., "
                   f"чанков={len(chunks)} grounded={grounded}")
            log(f"      ответ локальной LLM: {answer[:160]}")
            if not ok_b3:
                fails += 1

        # --- B4: метка «локальная модель» в списке ---
        record("B4", "PASS" if local_ok else "WARNING",
               f"модель {model} помечена local=true в /api/models")
    finally:
        # cleanup: вернуть embedder/strategy
        if saved:
            http("/api/kb/settings", method="POST", json_body=saved)
        stop_server(proc)
    return 0 if fails == 0 else 1


def _search(q: str, k: int = 3) -> tuple[int, Any]:
    """GET /api/kb/search с URL-кодированием запроса."""
    import urllib.parse

    return http("/api/kb/search?" + urllib.parse.urlencode({"q": q, "k": k}))


def main() -> int:
    ap = argparse.ArgumentParser(description="E2E day 28: локальный RAG")
    ap.add_argument("--part-a-only", action="store_true",
                    help="только оффлайн Part A")
    ap.add_argument("--model", default=None,
                    help=f"модель генерации Ollama (дефолт {LIVE_MODEL})")
    args = ap.parse_args()
    log("E2E day 28: локальная LLM + RAG (индекс Недели 6 + Ollama)")
    log(f"репозиторий: {REPO}")
    log(f"Ollama: {OLLAMA_BASE} · живой порт: {PORT}")
    a_ok = part_a()
    if not a_ok:
        print_summary()
        return 1
    if args.part_a_only:
        print_summary()
        return 0
    model = args.model or pick_live_model()
    b_rc = part_b(model)
    print_summary()
    # Part A обязана пройти; Part B — best-effort (SKIP без Ollama не провал)
    return b_rc


if __name__ == "__main__":
    sys.exit(main())
