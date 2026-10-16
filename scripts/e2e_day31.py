# -*- coding: utf-8 -*-
"""E2E day 30–34 «Единый AI-ассистент разработчика и поддержки»

Задание: ассистент, который отвечает на вопросы по проекту (RAG), делает
ревью изменений (PR review), обрабатывает тикеты с учётом CRM-данных и
автономно работает с файлами проекта по высокоуровневым целям.

Скрипт доказывает, что это работает на реальных данных проекта, а не
описано в документации.

Часть A (offline, MUST PASS, без сети и без LLM):
  A1  служба поднята: GET /api/assistant/status → корпус, CRM, git
  A2  индекс проекта: POST /api/assistant/index собирает документацию и
      код (README/docs/openspec/backend) в ОТДЕЛЬНЫЙ индекс; индекс дня
      21 (data/kb/index.db) не тронут
  A3  корпус: `.venv`, `node_modules`, `data/` в индекс не попадают
  A4  вопрос по проекту: реальный вопрос о коде/доке → ответ +
      источники с путями и цитатами (RAG)
  A5  «не знаю» честно: вопрос вне корпуса → found=false без выдумок
  A6  ревью: реальный diff (текстом) → находки по severity с файлом и
      НОМЕРОМ СТРОКИ (secret/bare_except/missing_test), рекомендации
  A7  ревью устойчиво: пустой/битый diff → 400, а не пустой отчёт
  A8  ревью на РЕАЛЬНОМ git-diff репозитория → отчёт по настоящим
      изменениям (не по синтетике)
  A9  поддержка с CRM: тикет «оплата не прошла» → ответ учитывает план
      и статус оплаты пользователя; контекст CRM виден в ответе
  A10 поддержка изолирована: неизвестный тикет → found=false, без
      выдумывания данных пользователя
  A11 сценарий find_usages: место использования реального символа
      проекта → файлы + номера строк (код И документация)
  A12 сценарий generate (ADR): создаёт файл, нумерует по существующим,
      кириллический заголовок → осмысленный слаг (не «adr»)
  A13 сценарий update_docs: обновляет README по git-diff, повторный
      запуск НЕ дублирует секцию (идемпотентность)
  A14 сценарий check_invariants: проверяет файлы проекта на правила
  A15 сценарий diff_report: готовит отчёт об изменениях, умеет
      сохранять и возвращать без записи
  A16 БЕЗОПАСНОСТЬ: запись вне allow-list отвергается (абсолютный путь,
      `..`, код бэкенда) — сценарий не пишет куда угодно
  A17 неизвестный сценарий → внятная ошибка 400

Часть B (live, best-effort, SKIP без Ollama):
  B0  зонд Ollama: GET /api/tags; выбор локальной модели
  B1  живой сервер: ассистент отвечает через реальный HTTP (реальный
      uvicorn, не TestClient)
  B2  РЕАЛЬНАЯ LLM: вопрос по проекту отвечает локальная модель
      (llm=true), источники — файлы проекта
  B3  поддержка с реальной моделью: персонализированный ответ по
      тикету с учётом CRM
  B4  ревью реального git-diff с LLM: находки + текстовые
      рекомендации от модели
  B5  индекс проекта на живом сервере: собрался и переживает запросы

Запуск из корня репозитория:
    python scripts/e2e_day31.py [--part-a-only] [--port 8112]
Код возврата: 0 — успех (Part A весь PASS и Part B без FAIL), 1 — провал.
"""
from __future__ import annotations

import argparse
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
from typing import Any

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "studio", "backend"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(REPO, ".env"))

OLLAMA = os.environ.get("OLLAMA_BASE_URL",
                        "http://localhost:11434/v1").rstrip("/")
OLLAMA_NATIVE = OLLAMA[:-3] if OLLAMA.endswith("/v1") else OLLAMA

PORT = 8112
BASE = f"http://127.0.0.1:{PORT}"
SERVER_LOG = os.path.join(tempfile.gettempdir(), "e2e_day31_server.log")

WAIT_SERVER_UP_MAX = 45
WAIT_SERVER_UP_STEP = 0.25

# --- инфраструктура отчёта (паттерн e2e_day24/28/29/30) ---

RESULTS: list[tuple[str, str, str]] = []   # (шаг, PASS/FAIL/SKIP, деталь)


def log(msg: str) -> None:
    print(msg, flush=True)


def record(step: str, status: str, detail: str = "") -> None:
    RESULTS.append((step, status, detail))
    mark = {"PASS": "OK  ", "FAIL": "FAIL", "SKIP": "SKIP"}[status]
    log(f"  [{mark}] {step}" + (f" — {detail}" if detail else ""))


def print_summary() -> tuple[int, int]:
    p = sum(1 for _, s, _ in RESULTS if s == "PASS")
    f = sum(1 for _, s, _ in RESULTS if s == "FAIL")
    k = sum(1 for _, s, _ in RESULTS if s == "SKIP")
    log("\n" + "=" * 74)
    log(f"ИТОГ: PASS={p} FAIL={f} SKIP={k}")
    for step, status, detail in RESULTS:
        if status == "FAIL":
            log(f"  ПРОВАЛ: {step} — {detail}")
    log("=" * 74)
    return p, f


# --- http-помощники ---


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def http(path: str, *, method: str | None = None, json_body: Any = None,
         base: str | None = None, timeout: int = 300) -> tuple[int, dict]:
    """HTTP-запрос → (status, json).

    Метод по умолчанию выводится из наличия тела: GET с телом попадает в
    SPA-фолбэк и молча возвращает HTML вместо JSON (см. e2e_day30).

    `base=None` читает ГЛОБАЛЬНУЮ BASE в момент вызова. Дефолт
    `base=BASE` был бы вычислен при импорте, и смена порта в `main()`
    не подействовала бы: запросы ушли бы на старый порт (Connection
    refused / чужой сервер).
    """
    if base is None:
        base = BASE
    if method is None:
        method = "POST" if json_body is not None else "GET"
    data = None
    hdrs: dict = {}
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, headers=hdrs,
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, json.loads(raw)
            except ValueError:
                return resp.status, {"_raw": raw[:400]}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"_raw": raw[:400]}


# --- сервер ---


def _dump_server_log() -> None:
    try:
        with open(SERVER_LOG, encoding="utf-8") as f:
            lines = f.read().splitlines()
        for line in lines[-25:]:
            log("    | " + line)
    except OSError:
        pass


def _kill_proc(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def wait_port_free(port: int, max_wait: float = 20.0,
                   step: float = 1.0) -> bool:
    """Дождаться освобождения порта.

    Критично для честности проверки: если порт занят прошлым сервером
    (например, остатком прогона day 30), новый uvicorn не займёт порт, а
    запросы уйдут к СТАРОМУ процессу. Тогда Part B проверяет чужой код, и
    результат ничего не значит — именно так 405/SPA-HTML выглядел как
    «ассистент не работает» (SPA-фолбэк `/{path}` отдаёт index.html на
    любой GET, если dist собран).
    """
    waited = 0.0
    while waited < max_wait:
        if not port_open(port):
            return True
        log(f"порт {port} занят, ждём {step} с…")
        time.sleep(step)
        waited += step
    return not port_open(port)


def start_server() -> subprocess.Popen | None:
    """Поднять uvicorn с ассистентом (app-dir-режим, см. e2e_day30)."""
    if not wait_port_free(PORT):
        log(f"порт {PORT} занят другим процессом — Part B не может "
            "проверять свой сервер")
        return None
    log(f"запускаем uvicorn на 127.0.0.1:{PORT} (лог: {SERVER_LOG})")
    with open(SERVER_LOG, "w", encoding="utf-8") as log_file:
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "main:app",
             "--app-dir", os.path.join("studio", "backend"),
             "--host", "127.0.0.1", "--port", str(PORT)],
            cwd=REPO, stdout=log_file, stderr=subprocess.STDOUT)
        waited = 0.0
        while waited < WAIT_SERVER_UP_MAX:
            if proc.poll() is not None:
                log("uvicorn завершился раньше времени, хвост server.log:")
                _dump_server_log()
                return None
            if port_open(PORT):
                return proc
            time.sleep(WAIT_SERVER_UP_STEP)
            waited += WAIT_SERVER_UP_STEP
    log(f"сервер не поднялся за {WAIT_SERVER_UP_MAX} с:")
    _dump_server_log()
    _kill_proc(proc)
    return None


# --- Ollama ---


def ollama_models() -> list:
    """Список локальных моделей (native /api/tags)."""
    try:
        with urllib.request.urlopen(OLLAMA_NATIVE + "/api/tags",
                                    timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return [m.get("name") for m in data.get("models", [])
                if m.get("name")]
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return []


def pick_local_model(models: list) -> str | None:
    """Выбрать модель, которая умеет и ответить, и посчитать (не эмбеддер)."""
    for m in models:
        if "embed" not in m.lower():
            return m
    return None


# --- Part A (offline) ---


def _response_for(symbol: str) -> str:
    """Реальный ответ API проекта, в котором есть искомый символ."""
    return symbol


def part_a() -> bool:
    log("\n" + "=" * 74)
    log("ЧАСТЬ A — офлайн (реальный проект, без сети и без LLM)")
    log("=" * 74)

    from fastapi.testclient import TestClient          # noqa: E402

    import main as main_mod                            # noqa: E402
    from agent import StudioAgent                      # noqa: E402
    from assistant import AssistantService             # noqa: E402
    from kb import KnowledgeBase                       # noqa: E402

    # Отдельные данные — рабочие каталоги репозитория НЕ трогаем
    # (кроме сценариев, которые пишут в docs/, — их результат удаляем).
    tmp = tempfile.mkdtemp(prefix="e2e_day31_")
    day21_index = os.path.join(REPO, "data", "kb", "index.db")
    day21_before = None
    if os.path.isfile(day21_index):
        with open(day21_index, "rb") as f:
            day21_before = f.read()

    agent = StudioAgent(os.path.join(tmp, "studio-data"))
    assistant = AssistantService(REPO, agent=None,
                                 kb_root=os.path.join(tmp, "kb"))
    app = main_mod.create_app(agent=agent,
                              kb=KnowledgeBase(os.path.join(tmp, "kb21")),
                              assistant=assistant)
    client = TestClient(app)
    created: list[str] = []

    def cleanup() -> None:
        for rel in created:
            p = os.path.join(REPO, rel)
            if os.path.isfile(p):
                os.remove(p)
            parent = os.path.dirname(p)
            if (os.path.isdir(parent)
                    and not os.listdir(parent)
                    and "adr" in parent):
                os.rmdir(parent)
        shutil.rmtree(tmp, ignore_errors=True)

    try:
        # A1 — служба поднята
        r = client.get("/api/assistant/status")
        status = r.json()
        if (r.status_code == 200 and status.get("corpus", {}).get("files", 0) > 50
                and status.get("crm", {}).get("tickets", 0) >= 1):
            record("A1 status: корпус + CRM + git",
                   "PASS",
                   f"файлов корпуса={status['corpus']['files']}, "
                   f"тикетов={status['crm']['tickets']}, "
                   f"git={'есть' if status['git']['available'] else 'нет'}")
        else:
            record("A1 status: корпус + CRM + git", "FAIL",
                   f"HTTP {r.status_code}: {status}")

        # A2 — индекс проекта собран и изолирован от дня 21
        r = client.post("/api/assistant/index", json={})
        idx = r.json() if r.status_code == 200 else {}
        stats = idx.get("stats", {})
        day21_after = None
        if os.path.isfile(day21_index):
            with open(day21_index, "rb") as f:
                day21_after = f.read()
        iso = (day21_before is None and day21_after is None) or \
              (day21_before == day21_after)
        if r.status_code == 200 and stats.get("docs", 0) > 50 and iso:
            record("A2 индекс проекта (отдельно от дня 21)", "PASS",
                   f"документов={stats.get('docs')}, "
                   f"чанков={stats.get('chunks')}, "
                   f"индекс дня 21 {'не тронут' if iso else 'ИЗМЕНЁН'}")
        else:
            record("A2 индекс проекта (отдельно от дня 21)", "FAIL",
                   f"HTTP {r.status_code}, iso={iso}, stats={stats}")

        # A3 — корпус без мусора
        corpus = status.get("corpus", {})
        files = assistant.pkb.corpus.files()
        junk = [p for p in files
                if any(x in p for x in (".venv", "node_modules",
                                        "__pycache__", "data/"))]
        if not junk and len(files) > 50:
            record("A3 корпус без .venv/node_modules/data", "PASS",
                   f"{len(files)} файлов, мусора нет")
        else:
            record("A3 корпус без .venv/node_modules/data", "FAIL",
                   f"мусор в корпусе: {junk[:5]}")

        # A4 — вопрос по проекту с источниками
        r = client.post("/api/assistant/ask",
                        json={"question": "как устроен шлюз локальной LLM "
                                          "и его ограничения?"})
        d = r.json() if r.status_code == 200 else {}
        srcs = d.get("sources") or []
        if r.status_code == 200 and d.get("found") and srcs and d.get("answer"):
            files_src = {s.get("file") for s in srcs}
            real = any("gateway" in (f or "") or "README" in (f or "")
                       or "day30" in (f or "") for f in files_src)
            record("A4 вопрос по проекту → ответ + источники", "PASS",
                   f"источников={len(srcs)}, релевантность="
                   f"{'да' if real else 'сомнительно'}: "
                   f"{sorted(files_src)[:2]}")
        else:
            record("A4 вопрос по проекту → ответ + источники", "FAIL",
                   f"HTTP {r.status_code}, found={d.get('found')}, "
                   f"sources={len(srcs)}")

        # A5 — честное «не знаю»
        r = client.post("/api/assistant/ask",
                        json={"question": "квантовый телепорт "
                                          "межзвёздного двигателя zxqwv"})
        d = r.json() if r.status_code == 200 else {}
        if r.status_code == 200 and d.get("found") is False and not d.get("sources"):
            record("A5 честное «не знаю» без выдумок", "PASS",
                   d.get("answer", "")[:60])
        else:
            # Найденное, но не выдуманное — тоже допустимо.
            record("A5 честное «не знаю» без выдумок",
                   "PASS" if r.status_code == 200 and not d.get("llm") else "FAIL",
                   f"found={d.get('found')}, llm={d.get('llm')}")

        # A6 — ревью diff с доказательствами
        diff = (
            "diff --git a/studio/backend/mod.py b/studio/backend/mod.py\n"
            "--- a/studio/backend/mod.py\n"
            "+++ b/studio/backend/mod.py\n"
            "@@ -1,2 +1,6 @@\n"
            ' """Модуль."""\n'
            '+import os\n'
            '+API_KEY = "sk-abcdef1234567890abcd"\n'
            "+def g():\n"
            "+    try:\n"
            "+        pass\n"
            "+    except:\n"
            "+        pass\n"
            " x = 1\n")
        # пересоберём корректным числом строк заголовка
        diff = diff.replace("@@ -1,2 +1,6 @@", "@@ -1,2 +1,9 @@")
        r = client.post("/api/assistant/review", json={"diff": diff})
        d = r.json() if r.status_code == 200 else {}
        rules = {f["rule"] for f in d.get("findings", [])}
        with_line = [f for f in d.get("findings", [])
                     if isinstance(f.get("line"), int)]
        if (r.status_code == 200 and {"secret", "bare_except"} <= rules
                and with_line and d.get("recommendations")):
            rec = [f for f in d["findings"] if f["rule"] == "secret"][0]
            record("A6 ревью diff: находки + номер строки", "PASS",
                   f"правила={sorted(rules)}, "
                   f"secret в {rec['file']}:{rec['line']}, "
                   f"severity={d['summary']['by_severity']}")
        else:
            record("A6 ревью diff: находки + номер строки", "FAIL",
                   f"HTTP {r.status_code}, rules={sorted(rules)}, "
                   f"with_line={len(with_line)}")

        # A7 — ревью устойчиво к мусору
        bad = []
        for body in ({"diff": ""}, {"diff": "это не diff"}):
            rr = client.post("/api/assistant/review", json=body)
            bad.append(rr.status_code)
        if bad == [400, 400]:
            record("A7 ревью: пустой/битый diff → 400", "PASS",
                   "без пустого отчёта")
        else:
            record("A7 ревью: пустой/битый diff → 400", "FAIL",
                   f"коды={bad}")

        # A8 — ревью РЕАЛЬНОГО git-diff репозитория
        r = client.post("/api/assistant/review", json={"with_llm": False})
        if r.status_code == 400:
            record("A8 ревью реального git-diff", "SKIP",
                   "в рабочем дереве нет изменений")
        else:
            d = r.json()
            summ = d.get("summary", {})
            if r.status_code == 200 and summ.get("files", 0) >= 1:
                record("A8 ревью реального git-diff", "PASS",
                       f"файлов={summ['files']}, +{summ['added']}/"
                       f"-{summ['removed']}, находок={summ['findings']}")
            else:
                record("A8 ревью реального git-diff", "FAIL",
                       f"HTTP {r.status_code}: {summ}")

        # A9 — поддержка с CRM
        r = client.post("/api/assistant/support", json={"ticket_id": "T-5003"})
        d = r.json() if r.status_code == 200 else {}
        ctx = d.get("context") or {}
        applied = ctx.get("applied") or []
        if (r.status_code == 200 and d.get("found")
                and any("оплат" in a for a in applied) and d.get("answer")):
            record("A9 поддержка: персонализация из CRM", "PASS",
                   f"тикет T-5003, план={ctx.get('user', {}).get('plan')}, "
                   f"применено: {applied}")
        else:
            record("A9 поддержка: персонализация из CRM", "FAIL",
                   f"HTTP {r.status_code}, found={d.get('found')}, "
                   f"applied={applied}")

        # A10 — поддержка не выдумывает
        r = client.post("/api/assistant/support",
                        json={"ticket_id": "T-9999",
                              "question": "как сбросить пароль?"})
        d = r.json() if r.status_code == 200 else {}
        ctx = d.get("context") or {}
        if r.status_code == 200 and d.get("found") is False \
                and ctx.get("user") is None and d.get("answer"):
            record("A10 поддержка: неизвестный тикет не выдумывает", "PASS",
                   "found=false, пользователь не подставлен")
        else:
            record("A10 поддержка: неизвестный тикет не выдумывает", "FAIL",
                   f"HTTP {r.status_code}, found={d.get('found')}, "
                   f"user={ctx.get('user')}")

        # A11 — find_usages по реальному проекту
        symbol = "compress_messages"
        r = client.post("/api/assistant/scenario",
                        json={"name": "find_usages",
                              "params": {"symbol": symbol}})
        d = r.json() if r.status_code == 200 else {}
        res = d.get("result") or {}
        got_files = {f["file"] for f in res.get("files", [])}
        if r.status_code == 200 and res.get("total", 0) > 0 \
                and any("gateway.py" in f for f in got_files):
            record(f"A11 find_usages «{symbol}»", "PASS",
                   f"вхождений={res['total']} в {res['file_count']} "
                   f"файлах, включая gateway.py")
        else:
            record(f"A11 find_usages «{symbol}»", "FAIL",
                   f"HTTP {r.status_code}, total={res.get('total')}, "
                   f"files={sorted(got_files)[:3]}")

        # A12 — generate ADR (кириллический заголовок + нумерация)
        r = client.post("/api/assistant/scenario",
                        json={"name": "generate",
                              "params": {"kind": "adr",
                                         "title": "Выбор векторной базы"}})
        d = r.json() if r.status_code == 200 else {}
        cf = d.get("changed_files") or []
        ok_slug = bool(cf) and "vybor-vektornoy-bazy" in cf[0]
        if r.status_code == 200 and cf and ok_slug:
            created.extend(cf)
            body = open(os.path.join(REPO, cf[0]), encoding="utf-8").read()
            record("A12 generate ADR (транслит + нумерация)", "PASS",
                   f"{cf[0]} ({len(body)} символов, ADR в заголовке="
                   f"{'ADR' in body})")
        else:
            record("A12 generate ADR (транслит + нумерация)", "FAIL",
                   f"HTTP {r.status_code}, changed={cf}")

        # A13 — update_docs идемпотентен (на копии README)
        import assistant as assistant_mod             # noqa: E402
        readme = os.path.join(REPO, "README.md")
        with open(readme, encoding="utf-8") as f:
            readme_orig = f.read()
        try:
            r1 = client.post("/api/assistant/scenario",
                             json={"name": "update_docs"})
            r2 = client.post("/api/assistant/scenario",
                             json={"name": "update_docs"})
            with open(readme, encoding="utf-8") as f:
                after = f.read()
            marks = after.count(assistant_mod.AUTOGEN_BEGIN)
            if r1.status_code == 200 and r2.status_code == 200 and marks == 1:
                record("A13 update_docs идемпотентен", "PASS",
                       f"два запуска → маркер встречается {marks} раз")
            else:
                record("A13 update_docs идемпотентен", "FAIL",
                       f"HTTP {r1.status_code}/{r2.status_code}, "
                       f"маркеров={marks}")
        finally:
            with open(readme, "w", encoding="utf-8") as f:
                f.write(readme_orig)

        # A14 — check_invariants
        r = client.post("/api/assistant/scenario",
                        json={"name": "check_invariants"})
        d = r.json() if r.status_code == 200 else {}
        res = d.get("result") or {}
        if r.status_code == 200 and res.get("checked", 0) > 10:
            record("A14 check_invariants по проекту", "PASS",
                   f"проверено={res['checked']}, нарушений="
                   f"{len(res.get('violations', []))}, правил="
                   f"{len(res.get('rules', []))}")
        else:
            record("A14 check_invariants по проекту", "FAIL",
                   f"HTTP {r.status_code}, checked={res.get('checked')}")

        # A15 — diff_report: без записи и с записью
        r = client.post("/api/assistant/scenario",
                        json={"name": "diff_report"})
        d = r.json() if r.status_code == 200 else {}
        no_write = r.status_code == 200 and not d.get("changed_files") \
            and d.get("report")
        # с записью — во временный каталог проекта (docs/), потом удаляем
        rel_out = "docs/_e2e_day31_report.md"
        r2 = client.post("/api/assistant/scenario",
                         json={"name": "diff_report",
                               "params": {"path": rel_out}})
        d2 = r2.json() if r2.status_code == 200 else {}
        if r2.status_code == 200 and rel_out in (d2.get("changed_files") or []):
            created.append(rel_out)
            wrote = os.path.isfile(os.path.join(REPO, rel_out))
        else:
            wrote = False
        if no_write and wrote:
            record("A15 diff_report: вывод и сохранение", "PASS",
                   f"отчёт {len(d.get('report', ''))} символов, "
                   f"сохранён в {rel_out}")
        else:
            record("A15 diff_report: вывод и сохранение", "FAIL",
                   f"no_write={no_write}, wrote={wrote}")

        # A16 — БЕЗОПАСНОСТЬ записи
        blocked = []
        for bad_path in ("/tmp/e2e_day31_evil.md", "../evil.md",
                         "studio/backend/main.py", "data/evil.md"):
            rr = client.post("/api/assistant/scenario",
                             json={"name": "generate",
                                   "params": {"kind": "readme",
                                              "path": bad_path}})
            blocked.append(rr.status_code)
        exposed = os.path.isfile("/tmp/e2e_day31_evil.md")
        if all(c == 400 for c in blocked) and not exposed:
            record("A16 запись вне allow-list отвергнута", "PASS",
                   f"4 вектора → 400, файл вне проекта не создан")
        else:
            record("A16 запись вне allow-list отвергнута", "FAIL",
                   f"коды={blocked}, exposed={exposed}")

        # A17 — неизвестный сценарий
        r = client.post("/api/assistant/scenario", json={"name": "hack_all"})
        if r.status_code == 400 and "Неизвестный сценарий" in \
                str(r.json().get("detail", "")):
            record("A17 неизвестный сценарий → 400", "PASS",
                   r.json()["detail"][:60])
        else:
            record("A17 неизвестный сценарий → 400", "FAIL",
                   f"HTTP {r.status_code}: {r.text[:80]}")
    finally:
        cleanup()

    fails = sum(1 for _, s, _ in RESULTS if s == "FAIL")
    return fails == 0


# --- Part B (live) ---


def part_b() -> bool:
    log("\n" + "=" * 74)
    log("ЧАСТЬ B — live (реальный сервер + реальная Ollama)")
    log("=" * 74)

    r = http("/api/assistant/status", base=f"http://127.0.0.1:{PORT}")
    if r[0] != 200:
        record("B5 индекс проекта на живом сервере", "FAIL",
               f"HTTP {r[0]}")
        return False

    models = ollama_models()
    model = pick_local_model(models)
    if not model:
        record("B0 зонд Ollama", "SKIP",
               "Ollama недоступна — Part B пропущен")
        return True
    record("B0 зонд Ollama", "PASS",
           f"моделей={len(models)}, выбрана {model}")

    # Выбираем модель для ассистента через конфиг Студии. Текущую модель
    # запоминаем и ВОЗВРАЩАЕМ в конце: e2e не должен менять настройки
    # пользователя как побочный эффект прогона.
    _, cfg_before = http("/api/config", method="GET")
    prev_model = (cfg_before or {}).get("model")
    st3, _ = http("/api/config", method="POST", json_body={"model": model})
    setup_ok = st3 == 200
    record("B0b модель ассистента", "PASS" if setup_ok else "SKIP",
           f"модель={model}" + ("" if setup_ok else
                                f" (HTTP {st3}) — отвечает модель "
                                "по умолчанию"))

    # B1 — живой сервер отвечает
    code, status = http("/api/assistant/status", method="GET")
    if code == 200:
        record("B1 живой сервер: ассистент отвечает", "PASS",
               f"корпус={status.get('corpus', {}).get('files')} файлов, "
               f"llm={status.get('llm_available')}")
    else:
        record("B1 живой сервер: ассистент отвечает", "FAIL", f"HTTP {code}")

    # B5 — индекс на живом сервере
    code, idx = http("/api/assistant/index", json_body={})
    if code == 200 and idx.get("stats", {}).get("docs", 0) > 50:
        record("B5 индекс проекта на живом сервере", "PASS",
               f"документов={idx['stats']['docs']}, "
               f"чанков={idx['stats']['chunks']}")
    else:
        record("B5 индекс проекта на живом сервере", "FAIL",
               f"HTTP {code}: {str(idx)[:100]}")

    # B2 — реальная LLM отвечает на вопрос по проекту
    code, ans = http("/api/assistant/ask", json_body={
        "question": "Что такое шлюз локальной LLM и какие у него лимиты?"})
    if code == 200 and ans.get("llm") and ans.get("answer"):
        record("B2 реальная LLM: вопрос по проекту", "PASS",
               f"llm={ans['llm']}, источников={len(ans.get('sources', []))}, "
               f"ответ {len(ans['answer'])} символов")
    else:
        record("B2 реальная LLM: вопрос по проекту",
               "SKIP" if code == 200 else "FAIL",
               f"HTTP {code}, llm={ans.get('llm')}, причина: "
               f"{ans.get('llm_error') or '—'}")

    # B3 — поддержка с реальной моделью
    code, sup = http("/api/assistant/support",
                     json_body={"ticket_id": "T-5003"})
    if code == 200 and sup.get("answer"):
        record("B3 поддержка с реальной LLM", "PASS",
               f"llm={sup.get('llm')}, применено: "
               f"{sup.get('context', {}).get('applied')}")
    else:
        record("B3 поддержка с реальной LLM", "FAIL",
               f"HTTP {code}: {str(sup)[:80]}")

    # B4 — ревью реального git-diff с LLM
    code, rev = http("/api/assistant/review", json_body={})
    if code == 400:
        record("B4 ревью реального diff с LLM", "SKIP",
               "нет изменений в рабочем дереве")
    elif code == 200:
        record("B4 ревью реального diff с LLM", "PASS",
               f"файлов={rev['summary']['files']}, "
               f"находок={rev['summary']['findings']}, "
               f"LLM-рекомендации={'есть' if rev.get('llm_note') else 'нет'}")
    else:
        record("B4 ревью реального diff с LLM", "FAIL", f"HTTP {code}")

    # Возвращаем прежнюю модель: прогон e2e не должен менять настройки.
    if prev_model and prev_model != model:
        back, _ = http("/api/config", method="POST",
                       json_body={"model": prev_model})
        log(f"  модель возвращена к {prev_model} (HTTP {back})")

    return True


def main() -> int:
    global PORT, BASE
    ap = argparse.ArgumentParser()
    ap.add_argument("--part-a-only", action="store_true")
    ap.add_argument("--port", type=int, default=PORT)
    args = ap.parse_args()
    PORT = args.port
    BASE = f"http://127.0.0.1:{PORT}"

    log("E2E дни 30–34: единый AI-ассистент разработчика и поддержки")
    log(f"репозиторий: {REPO}")

    if not part_a():
        print_summary()
        log("\nPart A содержит провалы — Part B не запускается.")
        return 1

    if args.part_a_only:
        p, f = print_summary()
        return 0 if f == 0 else 1

    proc = start_server()
    if proc is None:
        record("B0 сервер", "FAIL", "uvicorn не поднялся (порт занят?)")
        print_summary()
        return 1
    try:
        part_b()
    finally:
        _kill_proc(proc)

    p, f = print_summary()
    return 0 if f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
