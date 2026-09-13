"""E2E smoke day9 (Task 12): реальный сервер + реальный GPustack.

Пайплайн:
  1. Env-проверка: GPUSTACK_BASE_URL + GPUSTACK_API_KEY (из окружения или
     .env рядом с main.py) и достижимость GPustack API (GET {base}/models).
     Недостижимо / env отсутствует — SKIP с сообщением, exit 0.
  2. Порт 8000: если занят (параллельный task держит сервер) — ждать 10 c,
     до 10 минут; всё ещё занят — SKIP с сообщением, exit 0.
     Чужой сервер НЕ убивается.
  3. Запуск `python main.py` detached в корне репозитория.
  4. POST /agent/config: window_size=3, summary_gap=1,
     compression_enabled=true, reasoning=false (быстрый smoke).
  5. 12 × POST /agent/ask (body {"message": ...}).
  6. Проверка dialogues.json в корне репозитория: у активного диалога
     len(messages) <= 8 И summary непустая (сжатие реально сработало).
  7. finally: ВСЕГДА убить свой сервер (и все python-процессы с main.py
     в командной строке), убедиться, что порт 8000 закрыт.

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
PORT = 8000
ASKS = 12
WAIT_PORT_BUSY_MAX = 600  # 10 минут ожидания освобождения порта
WAIT_PORT_BUSY_STEP = 10
WAIT_SERVER_UP_MAX = 90   # запуск main.py (load_dotenv + bind)
ASK_TIMEOUT = 330         # сервер ждёт LLM до TIMEOUT=300 c


def log(msg):
    print(f"[e2e-day9] {msg}", flush=True)


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
    """Достигим ли GPustack. Вернёт None — достижим, иначе строку причины SKIP."""
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


def wait_server_up(proc: subprocess.Popen, log_path: str) -> None:
    deadline = time.time() + WAIT_SERVER_UP_MAX
    while time.time() < deadline:
        if port_open():
            return
        if proc.poll() is not None:
            tail = ""
            try:
                with open(log_path, encoding="utf-8") as f:
                    tail = f.read()[-2000:]
            except OSError:
                pass
            raise RuntimeError(f"server exited early (code {proc.returncode}):\n{tail}")
        time.sleep(0.5)
    raise RuntimeError("server did not open port 8000 in time")


def post(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        f"http://{HOST}:{PORT}{path}",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=ASK_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


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


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    load_dotenv()

    reason = probe_gpustack()
    if reason:
        log(f"SKIP: {reason}")
        return 0
    reason = wait_port_free()
    if reason:
        log(f"SKIP: {reason} — unit tests remain the gate")
        return 0

    log_path = os.path.join(
        os.environ.get("TEMP", r"C:\Users\migor\AppData\Local\Temp\opencode"),
        "task-12-e2e-server.log",
    )
    proc = None
    try:
        proc = start_server(log_path)
        log(f"server started (pid {proc.pid}), waiting for port {PORT}...")
        wait_server_up(proc, log_path)

        cfg = post("/agent/config", {
            "window_size": 3,
            "summary_gap": 1,
            "compression_enabled": True,
            "reasoning": False,  # smoke: быстро, thinking не нужен
        })
        if cfg.get("ok") is not True:
            raise RuntimeError(f"config not applied: {cfg}")
        log("config applied: window_size=3, summary_gap=1, compression on")

        for i in range(1, ASKS + 1):
            res = post("/agent/ask", {
                "message": f"E2E smoke {i}/{ASKS}: назови факт о сжатии контекста.",
            })
            usage = res.get("usage") or {}
            log(f"ask {i}/{ASKS} ok (prompt={usage.get('prompt_tokens')}, "
                f"completion={usage.get('completion_tokens')})")

        with open(os.path.join(REPO, "dialogues.json"), encoding="utf-8") as f:
            data = json.load(f)
        active = next(
            (d for d in data["dialogues"] if d["id"] == data.get("active_id")), None
        )
        if active is None:
            raise RuntimeError("active dialogue not found in dialogues.json")
        msgs = len(active["messages"])
        summary = active.get("summary")
        log(f"active dialogue: {msgs} messages, summary={len(summary) if isinstance(summary, str) else summary!r} chars")
        if msgs > 8:
            raise RuntimeError(f"compression failed: active dialogue has {msgs} messages (> 8)")
        if not isinstance(summary, str) or not summary.strip():
            raise RuntimeError("compression failed: active dialogue summary is empty")
        log("PASS: 12 asks, window=3/gap=1 -> messages <= 8, summary non-empty")
        return 0
    except Exception as e:
        log(f"FAIL: {e}")
        return 1
    finally:
        if proc is not None:
            kill_server(proc)


if __name__ == "__main__":
    sys.exit(main())
