"""E2E smoke day10 (Task 13): реальный сервер + стратегии контекста.

Пайплайн:
  1. Env-проверка: GPUSTACK_BASE_URL + GPUSTACK_API_KEY (из окружения или
     .env рядом с main.py) и достижимость GPustack API (GET {base}/models).
     Недостижимо / env отсутствует — ask-зависимые проверки SKIP'ятся с
     сообщением, но НЕ-LLM поверхность (стратегии, state, UI) проверяется
     полностью; exit 0.
  2. Порт 8000: main.py хардкодит PORT=8000. Если занят (параллельный task
     держит сервер) — ждать 10 c, до 10 минут; всё ещё занят — SKIP с
     сообщением, exit 0. Чужой сервер НЕ убивается.
  3. Запуск `python main.py` detached в корне репозитория, ожидание
     GET /agent/config.
  4. Поток стратегий (работает без LLM):
       POST /agent/strategy/switch {"strategy":"sliding_window"} -> 200
       GET  /agent/strategy -> strategy == "sliding_window"
       [REAL] 3 x POST /agent/ask -> GET /agent/last-request:
              messages == 1 system + 4 (окно) + 1 user = 6
       POST /agent/strategy/switch {"strategy":"sticky_facts"} -> 200
       POST /agent/strategy/switch {"strategy":"branching"}    -> 200
       POST /agent/strategy/checkpoint                         -> 200,
              state.checkpoint — int
       POST /agent/strategy/branch {"branch":"B"}              -> 200,
              state.active == "B"
       GET  /agent/strategy/state -> последовательно (int checkpoint,
              ветки A/B, active "B")
       GET  / -> 200 и содержит "cfg-strategy" (UI дня 10 на месте)
  5. finally: ВСЕГДА убить свой сервер, убедиться, что порт 8000 закрыт.

Выход: PASS/FAIL на stdout; exit 0 для PASS и SKIP, 1 для FAIL.
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
HOST = "127.0.0.1"
PORT = 8000  # main.py: PORT = 8000 (hardcoded)
ASKS = 3  # real-режим: достаточно для окна N=4 (1 system + 4 + 1 user)
WAIT_PORT_BUSY_MAX = 600  # 10 минут ожидания освобождения порта
WAIT_PORT_BUSY_STEP = 10
WAIT_SERVER_UP_MAX = 90   # запуск main.py (load_dotenv + bind)
ROUTE_TIMEOUT = 30
ASK_TIMEOUT = 330         # сервер ждёт LLM до TIMEOUT=300 c


def log(msg):
    print(f"[e2e-day10] {msg}", flush=True)


def port_open() -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1)
    try:
        return s.connect_ex((HOST, PORT)) == 0
    finally:
        s.close()


def load_dotenv() -> None:
    """Тот же формат, что в main.py (реальные env имеют приоритет)."""
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


def probe_gpustack() -> str | None:
    """Достигим ли GPustack. Вернёт None — достижим, иначе строку причины."""
    base = os.environ.get("GPUSTACK_BASE_URL", "").strip().rstrip("/")
    if not base:
        return "GPUSTACK_BASE_URL not set (environment or .env)"
    if not os.environ.get("GPUSTACK_API_KEY", "").strip():
        return "GPUSTACK_API_KEY not set (environment or .env)"
    key = os.environ.get("GPUSTACK_API_KEY", "").strip()
    req = urllib.request.Request(
        f"{base}/models",
        headers={"Authorization": f"Bearer {key}"},
        method="GET",
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
    """Дождаться, пока порт 8000 освободится. None — свободен, иначе причина SKIP."""
    if not port_open():
        return None
    log(f"port {PORT} busy — waiting up to {WAIT_PORT_BUSY_MAX // 60} min "
        "(parallel task's server is NOT killed)")
    deadline = time.time() + WAIT_PORT_BUSY_MAX
    while time.time() < deadline:
        time.sleep(WAIT_PORT_BUSY_STEP)
        if not port_open():
            log("port 8000 is free now")
            return None
    return f"port {PORT} still busy after {WAIT_PORT_BUSY_MAX}s (another task's server)"


def start_server(log_path: str) -> subprocess.Popen:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    out = open(log_path, "w", encoding="utf-8")
    return subprocess.Popen(
        [sys.executable, "main.py"],
        cwd=REPO,
        stdout=out,
        stderr=subprocess.STDOUT,
        creationflags=flags,
    )


def get(path: str, timeout: int = ROUTE_TIMEOUT) -> bytes:
    with urllib.request.urlopen(
        f"http://{HOST}:{PORT}{path}", timeout=timeout
    ) as resp:
        return resp.read()


def get_json(path: str, timeout: int = ROUTE_TIMEOUT):
    return json.loads(get(path, timeout).decode("utf-8"))


def post(path: str, body: dict | None = None, timeout: int = ROUTE_TIMEOUT) -> dict:
    if body is None:  # e.g. /agent/strategy/checkpoint — без body
        req = urllib.request.Request(
            f"http://{HOST}:{PORT}{path}", data=b"", method="POST"
        )
    else:
        req = urllib.request.Request(
            f"http://{HOST}:{PORT}{path}",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def wait_server_up(proc: subprocess.Popen, log_path: str) -> None:
    """Ждать, пока GET /agent/config ответит (сервер реально принимает)."""
    deadline = time.time() + WAIT_SERVER_UP_MAX
    while time.time() < deadline:
        if port_open():
            try:
                get_json("/agent/config")
                return
            except Exception:
                pass  # порт открыт, handler ещё не готов
        if proc.poll() is not None:
            tail = ""
            try:
                with open(log_path, encoding="utf-8") as f:
                    tail = f.read()[-2000:]
            except OSError:
                pass
            raise RuntimeError(f"server exited early (code {proc.returncode}):\n{tail}")
        time.sleep(0.5)
    raise RuntimeError("server did not answer /agent/config in time")


def kill_server(proc: subprocess.Popen) -> None:
    """Убить свой сервер (taskkill /T по pid). Если порт всё ещё занят —
    добор: python-процессы с main.py в cmdline, 2-3 раунда. В конце —
    проверка, что порт 8000 закрыт."""
    if proc.poll() is None:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
        )
        time.sleep(1.5)
    if not port_open():
        log("server killed (own process tree), port 8000 closed")
        return
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
        "Where-Object { $_.CommandLine -match 'main\\.py' } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    for round_no in range(3):
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True)
        time.sleep(1.5)
        if not port_open():
            log(f"server killed (round {round_no + 1}), port 8000 closed")
            return
    if port_open():
        raise RuntimeError(f"port {PORT} still open after kill attempts")
    log("server killed, port 8000 closed")


def run_strategy_flow(real_mode: bool) -> None:
    """Поток проверок (п. 4 docstring). Ошибки — RuntimeError."""
    info = post("/agent/strategy/switch", {"strategy": "sliding_window"})
    if info.get("strategy") != "sliding_window":
        raise RuntimeError(f"switch sliding_window: {info}")
    info = get_json("/agent/strategy")
    if info.get("strategy") != "sliding_window":
        raise RuntimeError(f"GET /agent/strategy: {info}")
    if not isinstance(info.get("strategy_state"), dict) or not isinstance(info.get("facts"), dict):
        raise RuntimeError(f"GET /agent/strategy shape: {info}")
    log("switch sliding_window -> 200, GET /agent/strategy ok (state, facts)")

    if real_mode:
        for i in range(1, ASKS + 1):
            res = post("/agent/ask", {
                "message": f"E2E day10 {i}/{ASKS}: назови факт о стратегии контекста.",
            }, timeout=ASK_TIMEOUT)
            if "reply" not in res and "error" in res:
                raise RuntimeError(f"ask {i}/{ASKS} failed: {res}")
            log(f"ask {i}/{ASKS} ok")
        last = get_json("/agent/last-request")
        payload = last.get("request") or {}
        msgs = len(payload.get("messages", []))
        if msgs == 0:
            raise RuntimeError(f"last-request empty: {last}")
        expected = 1 + 4 + 1  # system + окно N=4 + текущий user
        if msgs != expected:
            raise RuntimeError(
                f"sliding window bound failed: last-request has {msgs} messages, "
                f"expected {expected} (1 system + 4 + 1 user)"
            )
        log(f"sliding window bound ok: last-request {msgs} messages (1+4+1)")
    else:
        log("SKIP: ask-зависимые проверки (sliding window bound) — GPustack недоступен")

    for name in ("sticky_facts", "branching"):
        info = post("/agent/strategy/switch", {"strategy": name})
        if info.get("strategy") != name:
            raise RuntimeError(f"switch {name}: {info}")
        log(f"switch {name} -> 200")

    state = post("/agent/strategy/checkpoint").get("state")
    if not isinstance(state, dict) or not isinstance(state.get("checkpoint"), int):
        raise RuntimeError(f"checkpoint: {state}")
    log(f"checkpoint -> 200, checkpoint={state['checkpoint']} (int)")

    state = post("/agent/strategy/branch", {"branch": "B"}).get("state")
    if not isinstance(state, dict) or state.get("active") != "B":
        raise RuntimeError(f"branch B: {state}")
    log("branch B -> 200, active=B")

    state = get_json("/agent/strategy/state")
    if not isinstance(state.get("checkpoint"), int):
        raise RuntimeError(f"GET /agent/strategy/state checkpoint: {state}")
    if state.get("active") != "B":
        raise RuntimeError(f"GET /agent/strategy/state active: {state}")
    branches = state.get("branches")
    if not isinstance(branches, dict) or set(branches) != {"A", "B"}:
        raise RuntimeError(f"GET /agent/strategy/state branches: {state}")
    log("GET /agent/strategy/state consistent (checkpoint int, A/B, active=B)")

    page = get("/").decode("utf-8")
    if "cfg-strategy" not in page:
        raise RuntimeError("GET / does not contain 'cfg-strategy' (UI day10 missing)")
    log("GET / -> 200, contains 'cfg-strategy'")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    load_dotenv()

    gp_reason = probe_gpustack()
    real_mode = gp_reason is None
    if not real_mode:
        log(f"NOTE: {gp_reason} — mock-mode (non-LLM surface only)")

    reason = wait_port_free()
    if reason:
        log(f"SKIP: {reason} — unit tests remain the gate")
        return 0

    log_path = os.path.join(
        os.environ.get("TEMP", r"C:\Users\migor\AppData\Local\Temp\opencode"),
        "task-13-e2e-server.log",
    )
    proc = None
    try:
        proc = start_server(log_path)
        log(f"server started (pid {proc.pid}), waiting for /agent/config...")
        wait_server_up(proc, log_path)

        run_strategy_flow(real_mode)

        mode = "real" if real_mode else "mock (non-LLM surface)"
        log(f"PASS: strategy flow ok ({mode}), "
            f"{'3 asks + window bound' if real_mode else 'asks skipped'}")
        return 0
    except Exception as e:
        log(f"FAIL: {e}")
        return 1
    finally:
        if proc is not None:
            kill_server(proc)


if __name__ == "__main__":
    sys.exit(main())
